# Dify「程式碼執行」節點 3：格式整理防護（用於循環結束後的「格式整理」LLM 節點之後）
# 目的：格式整理只允許改標點、空白、Markdown 標記、引註與書目的排版；任何文字或數字被改動，就整份作廢。
# 輸入變數：
#   before (String)   格式整理前的稿件（循環的最終稿件 current_manuscript）
#   raw (String)      格式整理 LLM 節點的輸出文字（須含 <formatted_manuscript>…</formatted_manuscript>）
# 輸出變數：
#   ok (String: "true"/"false")   final_manuscript (String)   report (String)
#
# 檢查方法：把稿件轉成「內容簽章」——全形轉半形後，只保留文字與數字、轉小寫，其餘（標點、空白、符號）全部丟棄。
#   正文：整理前後的簽章必須完全相同（逐字）。
#   參考文獻：各條目的簽章必須相同，但允許條目順序改變（例如依字母排序）。
#   鎖定區塊（<!--LOCK-->…<!--/LOCK-->）：必須逐字相同。

import json
import re
import unicodedata

LOCK_RE = re.compile(r"<!--LOCK-->(.*?)<!--/LOCK-->", re.S)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
REF_HEAD_RE = re.compile(r"^[#\s*]*(參考文獻|References|Bibliography)\s*$", re.I | re.M)


def _sig(text):
    """內容簽章：只保留文字與數字；但保留數字中的小數點、千分位逗號與負號，避免「-.97」被悄悄改成「.97」。"""
    t = unicodedata.normalize("NFKC", COMMENT_RE.sub("", text or ""))
    t = re.sub(r"[-–−—](?=\.?\d)", "ⅿ", t)        # 數字前的負號或連字號
    t = re.sub(r"(?<=\d)[.,](?=\d)", "ⅾ", t)        # 數字之間的小數點或千分位逗號
    t = re.sub(r"(?<![\w])\.(?=\d)", "ⅾ", t)        # 省略前導 0 的小數（如 .31）
    return "".join(ch for ch in t if unicodedata.category(ch)[0] in "LN").lower()


def _split_refs(text):
    """回傳 (正文, 參考文獻條目清單)。找不到「參考文獻」標題時，條目為 None。"""
    ms = list(REF_HEAD_RE.finditer(text))
    if not ms:
        return text, None
    m = ms[-1]
    body, tail = text[:m.start()], text[m.end():]
    entries = [e.strip() for e in re.split(r"\n\s*\n|\n(?=[^\s])", tail) if e.strip()]
    return body, entries


def _first_diff(a, b, width=24):
    n = min(len(a), len(b))
    i = next((k for k in range(n) if a[k] != b[k]), n)
    return "第 %d 個有效字元附近：整理前「…%s」｜整理後「…%s」" % (
        i, a[max(0, i - 8):i + width], b[max(0, i - 8):i + width])


def _grab(raw, tag):
    m = re.search(r"<%s>(.*?)</%s>" % (tag, tag), raw or "", re.S)
    return m.group(1).strip() if m else ""


def _check(before, after):
    """回傳問題清單；空清單代表通過。"""
    problems = []
    if LOCK_RE.findall(before) != LOCK_RE.findall(after):
        problems.append("鎖定區塊被更動或遺失")
    if len(after) < 0.8 * len(before):
        problems.append("整理後長度僅為整理前的 %d%%，疑似被截斷" % (100 * len(after) // max(len(before), 1)))
    if len(after) > 1.2 * len(before):
        problems.append("整理後長度超過整理前的 120%，疑似新增了內容")
    b_body, b_refs = _split_refs(before)
    a_body, a_refs = _split_refs(after)
    sb, sa = _sig(b_body), _sig(a_body)
    if sb != sa:
        problems.append("正文文字或數字被更動（" + _first_diff(sb, sa) + "）")
    if (b_refs is None) != (a_refs is None):
        problems.append("「參考文獻」章節在整理前後不一致（遺失或新增）")
    elif b_refs is not None:
        rb = sorted(_sig(e) for e in b_refs if _sig(e))
        ra = sorted(_sig(e) for e in a_refs if _sig(e))
        if rb != ra:
            only_b = [x for x in rb if x not in ra][:1]
            only_a = [x for x in ra if x not in rb][:1]
            problems.append("參考文獻條目內容被更動或條數改變（整理前%d條／整理後%d條；差異例：整理前「%s」｜整理後「%s」）" % (
                len(rb), len(ra), (only_b[0][:30] if only_b else "—"), (only_a[0][:30] if only_a else "—")))
    return problems


def _candidates(raw):
    """從模型輸出中找出可能是「整理後稿件」的候選文字（依可信度排序）。回傳 [(來源說明, 文字)]。"""
    out = []
    t = _grab(raw, "formatted_manuscript")
    if t:
        out.append(("標準標籤", t))
    m = re.search(r"<formatted_manuscript>(.*)$", raw or "", re.S)  # 只有開頭標籤（可能被截斷或漏寫結尾標籤）
    if m and m.group(1).strip():
        body = re.sub(r"</?change_log>.*$", "", m.group(1), flags=re.S).strip()
        if body:
            out.append(("缺少結尾標籤", body))
    for fm in re.finditer(r"```[a-zA-Z]*\n(.*?)```", raw or "", re.S):
        out.append(("程式碼框內", fm.group(1).strip()))
    whole = (raw or "").strip()
    if whole:
        out.append(("整段輸出（無標籤）", whole))
        paras = re.split(r"\n\s*\n", whole)
        if len(paras) > 2:
            out.append(("去掉第一段說明", "\n\n".join(paras[1:]).strip()))
            out.append(("去掉最後一段說明", "\n\n".join(paras[:-1]).strip()))
            out.append(("去掉首尾說明", "\n\n".join(paras[1:-1]).strip()))
    return out


CJK = "\u4e00-\u9fff\u3400-\u4dbf"


def _basic_format(text):
    """純程式規則的基本整理（不呼叫模型）：中文語境的半形標點改全形、移除中文字之間的空格、清理多餘空行。
    表格、鎖定區塊與以英文為主的行（如英文書目）不處理；統計式（如 p = .31）不受影響。"""
    out, in_lock = [], False
    for ln in text.split("\n"):
        if "<!--LOCK-->" in ln:
            in_lock = True
            out.append(ln)
            continue
        if "<!--/LOCK-->" in ln:
            in_lock = False
            out.append(ln)
            continue
        if in_lock or "\t" in ln or ln.strip().startswith("|"):
            out.append(ln)
            continue
        s = ln.rstrip()
        n_cjk = len(re.findall("[" + CJK + "]", s))
        if n_cjk >= 0.3 * max(len(s.replace(" ", "")), 1):
            s = re.sub("(?<=[" + CJK + "]),(?=\\S)", "，", s)
            s = re.sub("(?<=[" + CJK + "]);", "；", s)
            s = re.sub("(?<=[" + CJK + "]):", "：", s)
            s = re.sub("(?<=[" + CJK + "])\\.(?=\\s|$)", "。", s)
            s = re.sub("(?<=[" + CJK + "])[ \\t]+(?=[" + CJK + "])", "", s)
            s = re.sub("\\(([^()\\n]*[" + CJK + "][^()\\n]*)\\)", r"（\1）", s)
        out.append(s)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))


def _lock_spans(text):
    return [(m.start(), m.end()) for m in LOCK_RE.finditer(text)]


def _punct_counts(t):
    """半形與全形視為同一類；只比較數量，所以「,」改「，」可以，但新增或刪除括號、逗號不行。"""
    t = t or ""
    return (len(re.findall(r"[，,]", t)), len(re.findall(r"[；;]", t)), len(re.findall(r"[：:]", t)), len(re.findall(r"[（(]", t)),
            len(re.findall(r"[）)]", t)), t.count("【") + t.count("】"), sum(t.count(c) for c in "「」『』"))


def _cjk_gap(t):
    """去掉中文與英文／數字之間的空白，用來辨識「只是加減間距」的修改。"""
    return re.sub(r"(?<=[\u4e00-\u9fff])[ \u3000]+(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])[ \u3000]+(?=[\u4e00-\u9fff])", "", t)


def _apply_edits(before, edits):
    """逐項套用「找→換」修改。每一項都必須：原文逐字出現且唯一、不在鎖定區塊、且只改標點或空白（內容簽章不變）。
    不合格的項目只跳過該項，不影響其他項目。回傳 (新稿件, 套用數, 跳過原因清單)。"""
    text, applied, skipped = before, 0, []
    for e in edits:
        if not isinstance(e, dict):
            continue
        if e.get("op") == "reorder_references":  # 依新順序重排參考文獻（只改順序，不改內容）
            order = e.get("order")
            ms = list(REF_HEAD_RE.finditer(text))
            _, entries = _split_refs(text)
            if (not ms or entries is None or not isinstance(order, list) or not all(isinstance(x, int) for x in order)
                    or sorted(order) != list(range(1, len(entries) + 1))):
                skipped.append("書目重排無效（順序必須包含每一條且不重複）")
                continue
            text = text[:ms[-1].end()] + "\n\n" + "\n\n".join(entries[k - 1] for k in order) + "\n"
            applied += 1
            continue
        f, r = e.get("find"), e.get("replace")
        if isinstance(f, str) and isinstance(r, str) and f and f == r:
            continue  # 找與換相同＝沒有變動，不列入跳過清單
        if not isinstance(f, str) or not isinstance(r, str) or not f:
            skipped.append("無效（缺少原文或替換文字）")
            continue
        if _cjk_gap(f) == _cjk_gap(r):
            skipped.append("僅調整中英文／數字間距，不列入格式修改")
            continue
        if len(f) > 400:
            skipped.append("原文片段過長")
            continue
        if text.count(f) != 1:
            skipped.append("找不到或不唯一：" + f[:14])
            continue
        i = text.index(f)
        if any(x < i + len(f) and i < y for x, y in _lock_spans(text)):
            skipped.append("位於鎖定區塊：" + f[:14])
            continue
        if _sig(f) != _sig(r):
            skipped.append("會改動文字或數字：「%s」→「%s」" % (f[:14], r[:14]))
            continue
        if _punct_counts(f) != _punct_counts(r):
            skipped.append("會增減逗號、冒號或括號：「%s」→「%s」" % (f[:14], r[:14]))
            continue
        text = text[:i] + r + text[i + len(f):]
        applied += 1
    return text, applied, skipped


def _parse_edits(raw):
    body = _grab(raw, "format_edits")
    if not body:
        m0 = re.search(r"<format_edits>(.*)$", raw or "", re.S)  # 輸出被截斷：沒有結尾標籤
        body = m0.group(1).strip() if m0 else ""
    if not body:
        return None
    body = re.sub(r"^```[a-zA-Z]*\s*", "", body.strip())
    body = re.sub(r"\s*```$", "", body)
    try:
        data = json.loads(body, strict=False)
    except Exception:
        m = re.search(r"\[.*\]", body, re.S)
        try:
            data = json.loads(m.group(0), strict=False) if m else None
        except Exception:
            data = None
        if data is None:  # JSON 不完整：只取已完整的 {"find","replace"} 項目，其餘丟棄
            data = []
            for mm in re.finditer(r'\{\s*"find"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"replace"\s*:\s*"((?:[^"\\]|\\.)*)"(?:\s*,\s*"reason"\s*:\s*"((?:[^"\\]|\\.)*)")?\s*\}', body, re.S):
                try:
                    data.append({"find": json.loads('"%s"' % mm.group(1)), "replace": json.loads('"%s"' % mm.group(2)), "reason": mm.group(3) or ""})
                except Exception:
                    pass
            if not data:
                data = None
    return data if isinstance(data, list) else None


def main(before: str, raw: str) -> dict:
    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S)
    log = _grab(raw, "change_log")

    # ---- 方式一（建議）：模型只回傳「找→換」修改清單 ----
    edits = _parse_edits(raw)
    if edits is not None:
        edited, applied, skipped = _apply_edits(before, edits)
        final = _basic_format(edited)
        problems = _check(before, final)
        if problems:  # 理論上不會發生；保險起見
            return {"ok": "false", "final_manuscript": before,
                    "report": "格式整理作廢，沿用整理前的稿件。套用修改後未通過內容檢查：" + "；".join(problems)}
        report = "格式整理通過防護檢查：模型提出 %d 項修改，套用 %d 項，跳過 %d 項；另以程式規則統一中文語境的標點與空白。內容檢查（文字、數字、鎖定區塊、參考文獻）皆與整理前相同。" % (
            len(edits), applied, len(skipped))
        if skipped:
            report += "\n跳過的項目：\n- " + "\n- ".join(skipped[:12])
        if log:
            report += "\n模型自述的變更紀錄（僅供參考；實際套用數量以上方統計為準，模型自述未必都已套用）：\n" + log[:1500]
        return {"ok": "true", "final_manuscript": final, "report": report}

    # ---- 方式二：模型輸出整篇稿件（舊格式），逐一嘗試擷取並檢查 ----
    cands = _candidates(raw)
    if not cands:
        basic = _basic_format(before)
        if not _check(before, basic) and basic != before:
            return {"ok": "true", "final_manuscript": basic,
                    "report": "格式整理：模型沒有輸出內容（輸出長度 0）；已改用程式規則做基本整理（中文語境的標點與空白）。請檢查「格式整理」節點的模型與最大 token 設定。"}
        return {"ok": "false", "final_manuscript": before,
                "report": "格式整理作廢：模型沒有輸出任何內容（輸出長度 0），沿用整理前的稿件。請檢查「格式整理」節點的模型與最大 token 設定。"}

    first_problems = None
    for src, cand in cands:
        problems = _check(before, cand)
        if first_problems is None:
            first_problems = (src, problems)
        if not problems:
            report = "格式整理通過防護檢查：正文文字與數字、參考文獻內容與鎖定區塊皆與整理前相同。"
            if src != "標準標籤":
                report += "（模型未依指定標籤輸出，已自「%s」擷取；內容檢查標準不變。）" % src
            if log:
                report += "\n變更紀錄：\n" + log[:1500]
            return {"ok": "true", "final_manuscript": cand, "report": report}

    src, problems = first_problems
    head = raw[:80].replace("\n", "⏎")
    why = "模型整理未通過防護（已嘗試 %d 種擷取方式；以「%s」為例，原因：%s｜模型輸出長度 %d 字元，開頭：%s）" % (
        len(cands), src, "；".join(problems), len(raw), head)
    basic = _basic_format(before)
    if not _check(before, basic):
        if basic == before:
            return {"ok": "false", "final_manuscript": before, "report": "格式整理：" + why + "；程式規則檢查後也沒有需要調整之處，沿用整理前的稿件。"}
        return {"ok": "true", "final_manuscript": basic,
                "report": "格式整理：" + why + "；已改用程式規則做基本整理（中文語境的標點與空白），內容檢查通過。其餘格式項目（如引註與書目排版）未處理，請自行核對。"}
    return {"ok": "false", "final_manuscript": before, "report": "格式整理作廢，沿用整理前的稿件。" + why}
