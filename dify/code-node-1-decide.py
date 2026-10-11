# Dify「程式碼執行」節點 1：解析兩份審查 JSON，重算分數，判斷是否停止
# 輸入變數（皆需在節點的「輸入變數」中設定）：
#   gemini_raw (String)            審查A（方法與論證，節點①）的輸出文字；變數名稱沿用舊名，與實際使用的模型無關
#   gpt_raw (String)               審查B（文獻與格式，節點②）的輸出文字；變數名稱沿用舊名，與實際使用的模型無關
#   paper_type (String)            quantitative | qualitative | theoretical | teaching_practice | mixed | content_analysis | historical | institutional_comparison | cross_cultural | literature_linguistics
#   round_no (Number)              目前輪次（從 1 開始）
#   max_rounds (Number)            輪數上限，例如 4
#   required_pass_rounds (Number)  需連續通過的輪數，rubric 預設 2
#   prev_total (Number)            上一輪的綜合加權總分（第 1 輪填 0）
#   prev_major (Number)            上一輪未解決的 critical+major 數量（第 1 輪填 0）
#   pass_streak (Number)           目前連續通過輪數（初始 0）
#   stall_streak (Number)          目前連續停滯輪數（初始 0）
#   history (String)               累積的每輪紀錄（初始空字串）
# 輸出變數（型別需與下列一致）：
#   decision(String) reason(String) round_total(Number) major_count(Number)
#   pass_streak(Number) stall_streak(Number) prior_gemini(String) prior_gpt(String)
#   issues_for_reviser(String) history_next(String)

import difflib
import json
import re
import unicodedata

DIMS = ["A", "B", "C", "D", "E", "F", "G"]
DEFAULT_W = {"A": 15, "B": 15, "C": 20, "D": 20, "E": 15, "F": 10, "G": 5}
TYPE_W = {
    "quantitative": {"B": 10, "C": 25, "D": 25, "F": 5},
    "qualitative": {"C": 25, "D": 20, "E": 15},
    "theoretical": {"B": 20, "C": 10, "D": 20, "E": 20},
    "teaching_practice": {"C": 20, "D": 20, "F": 15},
    "mixed": {},
    "content_analysis": {"B": 10, "C": 25, "D": 25, "F": 5},
    "historical": {"A": 10, "B": 20, "C": 15, "D": 25, "E": 20, "F": 5},
    "institutional_comparison": {"C": 20, "D": 20, "E": 20, "F": 5},
    "cross_cultural": {"B": 20, "C": 20, "D": 20, "E": 15, "F": 5},
    "literature_linguistics": {"A": 15, "B": 20, "C": 10, "D": 25, "E": 25, "F": 5},
    # 教學實踐研究計畫書預審：F＝計畫主持人部分（官方 20%）；A–E 對應執行內容（官方 80%）；G＝硬性合規
    "teaching_practice_proposal": {"A": 15, "B": 10, "C": 20, "D": 20, "E": 10, "F": 20, "G": 5},
}
VERDICT_RANK = {"accept": 0, "minor": 1, "major": 2, "reject": 3}
# 主責審查者：審查A 負責 A、C、D、E、F；審查B 負責 B、G。綜合分數以主責者為主（75%），另一位僅供參考（25%），
# 以免非本行的審查者給分偏寬，墊高整體分數。
PRIMARY = {"A": "A", "C": "A", "D": "A", "E": "A", "F": "A", "B": "B", "G": "B"}
PRIMARY_SHARE = 0.75
# 計畫書預審的主責分工：審查A（教學設計與研究設計）A、C、D、E；審查B（文獻、主持人部分、格式合規）B、F、G
PRIMARY_PROPOSAL = {"A": "A", "C": "A", "D": "A", "E": "A", "B": "B", "F": "B", "G": "B"}


def _escape_inner_quotes(s):
    """修復字串值中夾帶的半形雙引號：只有後面緊接 , } ] : 的引號才視為字串結尾。"""
    out = []
    in_str = False
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if in_str:
            if c == "\\" and i + 1 < n:
                out.append(c + s[i + 1])
                i += 2
                continue
            if c == '"':
                j = i + 1
                while j < n and s[j] in " \t\r\n":
                    j += 1
                crossed_newline = "\n" in s[i + 1:j]
                if j >= n or s[j] in ",}]:" or (crossed_newline and s[j] == '"'):
                    in_str = False
                    out.append(c)
                else:
                    out.append('\\"')
                i += 1
                continue
            out.append(c)
        else:
            if c == '"':
                in_str = True
            elif c == "，":
                c = ","
            elif c == "：":
                c = ":"
            out.append(c)
        i += 1
    return "".join(out)


def _repair(s):
    """嘗試修復常見的 JSON 格式錯誤。"""
    s = _escape_inner_quotes(s)
    s = re.sub(r"\}(\s*)\{", r"},\1{", s)  # 物件之間缺逗號
    s = re.sub(r"\](\s*)\[", r"],\1[", s)
    # 上一行以 " } ] 數字 結尾、下一行是 "key": 時，補逗號
    s = re.sub(r'(["\}\]\d])(\s*\n\s*)("[^"\n]*"\s*:)', r"\1,\2\3", s)
    s = re.sub(r",(\s*[}\]])", r"\1", s)  # 多餘的結尾逗號
    return s


def _parse(raw):
    s = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S).strip()
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    # 先判斷是否被截斷：大括號未閉合，通常是最大輸出 token 不足（含模型內部推理所佔用的 token）
    if s.count("{") > s.count("}") or not s.endswith("}"):
        if s.count("{") > s.count("}"):
            return None, (
                "輸出疑似被截斷（大括號未閉合，輸出僅 %d 字元）。"
                "請調高該 LLM 節點的「最大 token」（建議 32000），"
                "並降低推理強度；輸出結尾：…%s" % (len(s), s[-60:].replace("\n", "⏎"))
            )
    m = re.search(r"\{.*\}", s, re.S)
    if m:
        s = m.group(0)
    try:
        return json.loads(s, strict=False), ""
    except Exception as e1:
        pos1 = getattr(e1, "pos", 0) or 0
        ctx1 = s[max(0, pos1 - 40):pos1 + 80].replace("\n", "⏎")
        fixed = _repair(s)
        try:
            return json.loads(fixed, strict=False), ""
        except Exception as e2:
            pos2 = getattr(e2, "pos", 0) or 0
            ctx2 = fixed[max(0, pos2 - 40):pos2 + 80].replace("\n", "⏎")
            head = s[:120].replace("\n", "⏎")
            return None, (
                "JSON 解析失敗。原始錯誤：%s，附近：…%s…｜自動修復後仍失敗：%s，附近：…%s…｜輸出開頭：%s｜總長度：%d"
                % (e1, ctx1, e2, ctx2, head, len(s))
            )


def _num(v):
    """把各種寫法的分數轉為 1–5 的數字；無法轉換回傳 None。"""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        x = float(v)
    elif isinstance(v, str):
        m = re.search(r"\d+(?:\.\d+)?", v)
        x = float(m.group(0)) if m else None
    else:
        return None
    return x if x is not None and 1 <= x <= 5 else None


def _prep(d):
    """檢查並正規化單一審查者的輸出。回傳 (物件或 None, 錯誤說明)。分數無效的面向放進 _bad，由 main 以另一位審查者代入。"""
    if not isinstance(d, dict):
        return None, "不是 JSON 物件"
    if d.get("verdict") not in VERDICT_RANK:
        return None, "verdict 無效（%r）" % (d.get("verdict"),)
    if d.get("issues") in (None, ""):
        d["issues"] = []
    if not isinstance(d.get("issues"), list):
        return None, "issues 不是陣列"
    sc = d.get("scores")
    if not isinstance(sc, dict):
        return None, "缺少 scores"
    clean, bad = {}, {}
    for k in DIMS:
        v = _num(sc.get(k))
        if v is None:
            bad[k] = repr(sc.get(k))[:24]
        else:
            clean[k] = v
    d["scores"], d["_bad"] = clean, bad
    return d, ""


def _validate(d):
    if not isinstance(d, dict):
        return "不是 JSON 物件"
    sc = d.get("scores")
    if not isinstance(sc, dict):
        return "缺少 scores"
    for k in DIMS:
        v = sc.get(k)
        if not isinstance(v, (int, float)) or v < 1 or v > 5:
            return "scores." + k + " 無效"
    if d.get("verdict") not in VERDICT_RANK:
        return "verdict 無效"
    if not isinstance(d.get("issues"), list):
        return "issues 不是陣列"
    return ""


def _weights(paper_type):
    w = dict(DEFAULT_W)
    w.update(TYPE_W.get(paper_type, {}))
    s = float(sum(w.values()))
    return {k: v / s for k, v in w.items()}  # 權重加總不是 100 時自動正規化


def _total(scores, w):
    return sum(scores[k] * w[k] for k in DIMS)


KNOWN_GAP_RE = re.compile(r"^\s*【已知待補(?:#(\d+))?】")
ORIG_SEV_RE = re.compile(r"原嚴重度[：:]\s*(critical|major|minor)")


def _open_issues(d, gaps_n=0):
    """未解決的意見。【已知待補#N】只有在「作者確實填了清單、且 N 落在清單範圍內」時才有效，有效者嚴重度最高為 minor；
    其餘（清單為空、沒有編號、編號超出範圍）一律視為無效標示：忽略它，並把嚴重度還原為標示中所寫的原嚴重度。"""
    out = []
    for i in d.get("issues", []):
        if not isinstance(i, dict) or i.get("status") == "resolved":
            continue
        j = dict(i)
        m = KNOWN_GAP_RE.match(str(j.get("problem", "")))
        if m:
            n = int(m.group(1)) if m.group(1) else 0
            if gaps_n > 0 and 1 <= n <= gaps_n:
                j["known_gap"] = True
                j["gap_ref"] = n
                if j.get("severity") in ("critical", "major"):
                    j["severity"] = "minor"
            else:
                j["tag_ignored"] = True
                om = ORIG_SEV_RE.search(str(j.get("problem", "")))
                if om and not j.get("screen_note"):
                    j["severity"] = om.group(1)
        out.append(j)
    return out


SCREEN_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
MARK_RE = re.compile(r"〔?【(?:待作者補充|待作者確認|建議補充)[^】]*】〕?")
ID_ROUND_RE = re.compile(r"-R(\d+)-")


def _norm(t):
    t = unicodedata.normalize("NFKC", SCREEN_COMMENT_RE.sub("", t or ""))
    return re.sub(r"[\W_]+", "", t)


def _found(ev, hays):
    """證據是否逐字出現在任一文本中（略過標點與空白；容許極小差異：最長連續相符片段 >= 85%）。"""
    e = _norm(ev)
    if len(e) < 6:
        return False
    for h in hays:
        if not h:
            continue
        if e in h:
            return True
        if difflib.SequenceMatcher(None, h, e, autojunk=False).find_longest_match(0, len(h), 0, len(e)).size >= 0.85 * len(e):
            return True
    return False


def _screen(d, ms, orig, prog, round_no, who):
    """計畫書模式：本輪新提出的 major／critical 意見必須附逐字原文證據，找不到者降為 minor；
    第 2 輪起新提出的 major（非 critical）必須是修稿造成的新問題（證據不在原稿中），否則降為 minor（凍結問題清單）。"""
    hays = [_norm(ms), _norm(MARK_RE.sub("", ms or "")), _norm(prog)]
    horig = [_norm(orig)]
    notes = []
    for i in d.get("issues", []):
        if not isinstance(i, dict) or i.get("status") == "resolved":
            continue
        sev = i.get("severity")
        if sev not in ("critical", "major"):
            continue
        m = ID_ROUND_RE.search(str(i.get("id", "")))
        if not m or int(m.group(1)) != round_no:
            continue  # 沿用上一輪的意見已在當時核對過
        ev = str(i.get("evidence") or "")
        why = ""
        if not ev.strip():
            why = "未附原文證據"
        elif not _found(ev, hays):
            why = "原文證據在稿件中找不到"
        elif round_no >= 2 and sev == "major":
            if i.get("revision_induced") is not True:
                why = "第%d輪起凍結問題清單，新增 major 非修稿造成" % round_no
            elif _found(ev, horig):
                why = "自稱修稿造成，但證據原稿本來就有"
        if why:
            i["orig_severity"] = sev
            i["severity"] = "minor"
            i["screen_note"] = why
            notes.append((who, i.get("id", "?"), why))
    return notes


def _count(issues, sev):
    return len([i for i in issues if i.get("severity") == sev])



AUDIT_LABEL = {
    "matched": "通過內部檢查", "year_mismatch": "年份不符", "missing_in_list": "清單缺漏",
    "uncited": "未被引用", "incomplete": "書目不完整", "placeholder_suspected": "疑似佔位文字",
    "needs_external_check": "需外部核實",
}


def _audit_summary(d):
    """把審查B 的 reference_audit 整理成一行文字；沒有該欄位則回傳空字串。"""
    items = d.get("reference_audit")
    if not isinstance(items, list) or not items:
        return ""
    counts, flagged = {}, []
    for it in items:
        if not isinstance(it, dict):
            continue
        st = it.get("status", "")
        counts[st] = counts.get(st, 0) + 1
        if st not in ("matched", ""):
            name = re.sub(r"【[^】]*】?", "", (it.get("citation_as_written") or it.get("list_entry") or it.get("ref_id") or ""))
            name = re.split(r"[。.]", name)[0].strip()[:20]
            flagged.append("%s（%s）" % (name, AUDIT_LABEL.get(st, st)))
    parts = ["共%d筆" % len(items)]
    for st in AUDIT_LABEL:
        if counts.get(st):
            parts.append("%s%d" % (AUDIT_LABEL[st], counts[st]))
    txt = "｜文獻稽核：" + "、".join(parts)
    if counts.get("matched"):
        txt += "（「通過內部檢查」僅代表稿內一致，不代表文獻真實存在）"
    if flagged:
        txt += "｜待處理：" + "；".join(flagged[:6]) + ("…" if len(flagged) > 6 else "")
    return txt


def _lit_entries(lit_context):
    """從文獻清單文字取出 {編號: 標題}。"""
    out = {}
    for m in re.finditer(r"^(\d{1,2})\. 《(.+?)》", lit_context or "", re.M):
        out[int(m.group(1))] = m.group(2)
    return out


def _lit_summary(d, who, entries):
    """整理審查者的 literature_use：核對編號與標題確實在清單內；回傳一行摘要（無資料則空字串）。"""
    items = d.get("literature_use")
    if not entries or not isinstance(items, list) or not items:
        return ""
    ok, bad, miss = 0, 0, []
    for it in items:
        if not isinstance(it, dict):
            bad += 1
            continue
        try:
            n = int(it.get("list_no"))
        except Exception:
            n = -1
        t = _norm(it.get("title") or "")
        ref = _norm(entries.get(n, ""))
        if not ref or not t or (t[:10] not in ref and ref[:10] not in t):
            bad += 1
            continue
        ok += 1
        if it.get("engagement") == "not_engaged" and it.get("relevance") == "direct":
            miss.append("#%d《%s》" % (n, entries[n][:24]))
    txt = "｜%s文獻清單使用：採計%d筆" % (who, ok)
    if miss:
        txt += "，稿件未處理：" + "、".join(miss[:5])
    if bad:
        txt += "；另%d筆編號或標題與清單不符，已忽略" % bad
    return txt


def main(gemini_raw: str, gpt_raw: str, paper_type: str, round_no: int,
         max_rounds: int, required_pass_rounds: int, prev_total: float,
         prev_major: int, pass_streak: int, stall_streak: int,
         history: str, known_gaps: str = "", manuscript: str = "", orig_manuscript: str = "",
         program_checks: str = "", lit_context: str = "") -> dict:
    round_no = int(round_no)
    max_rounds = int(max_rounds)
    required_pass_rounds = int(required_pass_rounds)
    pass_streak = int(pass_streak)
    stall_streak = int(stall_streak)
    prev_major = int(prev_major)
    prev_total = float(prev_total)

    g, eg = _parse(gemini_raw)
    p, ep = _parse(gpt_raw)
    if g is not None:
        g, eg = _prep(g)
    if p is not None:
        p, ep = _prep(p)

    # 容錯 1：某個面向分數無效時，以另一位審查者的分數代入（並在紀錄中註明）
    tol_warn = []
    for d, other, who, ow in ((g, p, "審查A", "審查B"), (p, g, "審查B", "審查A")):
        if d is None:
            continue
        for k, raw_v in list(d["_bad"].items()):
            if other is not None and k in other["scores"]:
                d["scores"][k] = other["scores"][k]
                del d["_bad"][k]
                tol_warn.append("%s未給面向%s有效分數（原值：%s），以%s之分數代入" % (who, k, raw_v, ow))
    for d, who in ((g, "審查A"), (p, "審查B")):
        if d is not None and d["_bad"]:
            d_err = "面向 %s 的分數無效（%s），且另一位審查者也無法代入" % ("、".join(d["_bad"]), "；".join("%s=%s" % kv for kv in d["_bad"].items()))
            if who == "審查A":
                g, eg = None, d_err
            else:
                p, ep = None, d_err

    # 容錯 2：只有一位審查者失效時，本輪僅採用另一位的意見繼續（本輪不得判定通過，也不判分歧）
    degraded = ""
    if (g is None or eg) and p is not None and not ep:
        degraded = "審查A（方法論證）輸出無效（%s），本輪僅採用審查B的意見，且本輪不得判定通過" % (eg or "未知")
        g = {"scores": dict(p["scores"]), "verdict": p["verdict"], "issues": [], "_bad": {}}
        eg = ""
    elif (p is None or ep) and g is not None and not eg:
        degraded = "審查B（文獻格式）輸出無效（%s），本輪僅採用審查A的意見，且本輪不得判定通過" % (ep or "未知")
        p = {"scores": dict(g["scores"]), "verdict": g["verdict"], "issues": [], "reference_audit": [], "_bad": {}}
        ep = ""

    errors = []
    if g is None or eg:
        errors.append("審查A（方法論證）輸出無效：" + (eg or "未知"))
    if p is None or ep:
        errors.append("審查B（文獻格式）輸出無效：" + (ep or "未知"))
    if errors:
        line = "第%d輪｜審查輸出格式錯誤，需人工處理" % round_no
        return {
            "decision": "stop_human",
            "reason": "；".join(errors),
            "round_total": prev_total,
            "major_count": prev_major,
            "pass_streak": pass_streak,
            "stall_streak": stall_streak,
            "prior_gemini": "[]",
            "prior_gpt": "[]",
            "issues_for_reviser": "{}",
            "history_next": (history + "\n" + line).strip(),
        }

    _ents = _lit_entries(lit_context)
    w = _weights(paper_type)
    PRIMARY_MAP = PRIMARY_PROPOSAL if paper_type == "teaching_practice_proposal" else PRIMARY
    tg, tp = _total(g["scores"], w), _total(p["scores"], w)
    gaps_list = [x for x in (known_gaps or "").split("\n") if x.strip()]
    screen_notes = []
    if paper_type == "teaching_practice_proposal":
        screen_notes += _screen(g, manuscript, orig_manuscript, program_checks, round_no, "審查A")
        screen_notes += _screen(p, manuscript, orig_manuscript, program_checks, round_no, "審查B")
    og, op = _open_issues(g, len(gaps_list)), _open_issues(p, len(gaps_list))
    blended = {}
    capped = []
    for k in DIMS:
        a_s, b_s = g["scores"][k], p["scores"][k]
        prim, other = (a_s, b_s) if PRIMARY_MAP[k] == "A" else (b_s, a_s)
        blended[k] = PRIMARY_SHARE * prim + (1 - PRIMARY_SHARE) * other
        # 面向分數上限：該面向有未解決的 major → 上限 3.5；有 critical → 上限 2.5（不論由哪位審查者提出）
        sevs = [i.get("severity") for i in og + op if i.get("dimension") == k]
        cap = 2.5 if "critical" in sevs else 3.5 if "major" in sevs else None
        if cap is not None and blended[k] > cap:
            blended[k] = cap
            capped.append(k)
    round_total = round(_total(blended, w), 2)

    crit = _count(og, "critical") + _count(op, "critical")
    major = _count(og, "major") + _count(op, "major")
    minor = _count(og, "minor") + _count(op, "minor")
    major_count = crit + major
    if paper_type == "teaching_practice_proposal":  # 計畫書：有未解決 critical 總分不超過 2.5，有 major 不超過 3.5（避免分數與結論脫節）
        _cap = 2.5 if crit else 3.5 if major else None
        if _cap is not None and round_total > _cap:
            round_total = _cap
            capped.append("總分")

    # 通過條件：兩位審查者 verdict 皆為 accept/minor，且無未解決的 critical/major
    pass_now = (
        not degraded
        and g["verdict"] in ("accept", "minor")
        and p["verdict"] in ("accept", "minor")
        and major_count == 0
    )
    new_pass = pass_streak + 1 if pass_now else 0

    # 分歧條件：任一面向相差 >= 2 分，或 verdict 相差 >= 2 級
    gaps = {k: abs(g["scores"][k] - p["scores"][k]) for k in DIMS}
    gap_dim = max(gaps, key=gaps.get)
    dim_gap = gaps[gap_dim]
    verdict_gap = abs(VERDICT_RANK[g["verdict"]] - VERDICT_RANK[p["verdict"]])
    ranks = sorted([VERDICT_RANK[g["verdict"]], VERDICT_RANK[p["verdict"]]])
    disagree = (not degraded) and ranks[1] == 3 and ranks[0] <= 1  # 一位 reject、另一位 accept/minor

    # 停滯條件：總分進步 < 0.1 且 critical/major 數量未減少（第 2 輪起才計算）
    if round_no >= 2 and (round_total - prev_total) < 0.1 and major_count >= prev_major:
        new_stall = stall_streak + 1
    else:
        new_stall = 0

    if disagree:
        decision = "stop_human"
        reason = "兩位審查者結論嚴重分歧：審查A %s、審查B %s（面向%s 審查A %.1f 分 vs 審查B %.1f 分），請人工裁決" % (
            g["verdict"], p["verdict"], gap_dim, g["scores"][gap_dim], p["scores"][gap_dim])
    elif new_pass >= required_pass_rounds:
        decision, reason = "stop_pass", "連續 %d 輪通過" % new_pass
    elif new_stall >= 2:
        decision, reason = "stop_stalled", "連續兩輪停滯"
    elif round_no >= max_rounds:
        decision, reason = "stop_limit", "已達輪數上限 %d" % max_rounds
    else:
        decision, reason = "continue", "尚未達停止條件"

    warn = ""
    for tw in tol_warn:
        warn += "｜⚠" + tw
    if degraded:
        warn += "｜⚠" + degraded
    n_known = len([i for i in og + op if i.get("known_gap")])
    n_ignored = len([i for i in og + op if i.get("tag_ignored")])
    if n_known:
        covered = sorted({i["gap_ref"] for i in og + op if i.get("gap_ref")})
        warn += "｜已知待補 %d 項有效（涵蓋清單第 %s 項；作者自行處理，不計入通過條件）" % (n_known, "、".join(str(x) for x in covered))
    if gaps_list:
        warn += "｜你填了 %d 項已知待補清單" % len(gaps_list)
        if n_known == 0:
            warn += "，但沒有任何意見被有效標示（⚠請確認清單用語是否與審查意見相近，否則相關問題仍會阻擋通過）"
    if n_ignored:
        warn += "｜⚠%d 條【已知待補】標示無效已忽略並還原嚴重度（清單為空、缺少編號或編號超出範圍）" % n_ignored
    if screen_notes:
        warn += "｜⚠%d 項 major／critical 意見經程式核對降為 minor（不計入結論，請人工查看）：%s" % (
            len(screen_notes), "；".join("%s %s（%s）" % n for n in screen_notes[:8]))
    if capped:
        warn += "｜面向分數受未解決 major／critical 限制：" + "、".join(capped)
    if dim_gap >= 2 and not disagree:
        warn += "｜提示：面向%s 兩位審查者相差 %.1f 分（綜合分數以主責審查者為主）" % (gap_dim, dim_gap)
    if all(g["scores"][k] >= 5 for k in DIMS):
        warn += "｜⚠審查A全部給滿分，疑似過於寬鬆"
    if all(p["scores"][k] >= 5 for k in DIMS):
        warn += "｜⚠審查B全部給滿分，疑似過於寬鬆"

    line = (
        "第%d輪｜審查A（方法論證）%.2f（%s）｜審查B（文獻格式）%.2f（%s）｜綜合 %.2f（主責加權）｜"
        "未解決 critical %d / major %d / minor %d｜判斷：%s（%s）"
    ) % (round_no, tg, g["verdict"], tp, p["verdict"], round_total,
         crit, major, minor, decision, reason) + warn + _audit_summary(p) + _audit_summary(g) + _lit_summary(p, "審查B", _ents) + _lit_summary(g, "審查A", _ents)

    sev_rank = {"critical": 0, "major": 1, "minor": 2}
    key = lambda i: sev_rank.get(i.get("severity"), 3)
    for_reviser = {
        "gemini": {"verdict": g["verdict"], "scores": g["scores"],
                   "issues": sorted(og, key=key)},
        "gpt": {"verdict": p["verdict"], "scores": p["scores"],
                "issues": sorted(op, key=key)},
    }

    return {
        "decision": decision,
        "reason": reason,
        "round_total": round_total,
        "major_count": major_count,
        "pass_streak": new_pass,
        "stall_streak": new_stall,
        "prior_gemini": json.dumps(og, ensure_ascii=False),
        "prior_gpt": json.dumps(op, ensure_ascii=False),
        "issues_for_reviser": json.dumps(for_reviser, ensure_ascii=False),
        "history_next": (history + "\n" + line).strip(),
    }
