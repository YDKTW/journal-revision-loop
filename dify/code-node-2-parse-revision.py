# Dify「程式碼執行」節點 2：拆解 Claude 修稿輸出，並用程式強制檢查鎖定區塊
# 輸入變數：
#   reviser_raw (String)    Claude 修稿者 LLM 節點的輸出文字
#   old_manuscript (String) 本輪送審的稿件（current_manuscript）
#   fail_streak (Number)    連續「修稿輸出無法採用」的次數（循環變數）
# 修稿輸出有兩種格式（向下相容）：
#   A. <revised_manuscript>…完整修訂稿…</revised_manuscript>（舊格式，整份稿件由模型重寫）
#   B. <edits>…修改清單…</edits>（新格式，模型只輸出要改的句子，由本程式套用到舊稿；速度快、成本低、不會被截斷）
#   兩者同時出現時以 A 為準。B 的格式見 journal-manuscript-reviser/SKILL.md「自動化模式的輸出包裝」。
# 處理原則：硬失敗（缺稿、鎖定區塊被改、截斷）→ 作廢本輪；軟警示（需人工補事實等）→ 採用修訂稿並停止循環
# 輸出變數：
#   ok(String: "true"/"false") override_decision(String) manuscript_clean(String)
#   manuscript_marked(String) manuscript_prev(String) response_table(String) dispositions_summary(String)
#   plain_summary(String) flags(String)

import json
import re

LOCK_RE = re.compile(r"<!--LOCK-->(.*?)<!--/LOCK-->", re.S)
TODO_STRIP_RE = re.compile(r"〔?【(?:待作者補充|待作者確認|建議補充)[^】]*】〕?")
NUM_RE = re.compile(r"-?\.?\d+(?:[.,]\d+)*")


def _num_diff(old, new):
    def nums(t):
        t = TODO_STRIP_RE.sub(" ", LOCK_RE.sub(" ", re.sub(r"<!--R\d+-[^>]*?-->", "", t or "")))
        d = {}
        for m in NUM_RE.finditer(t):
            d[m.group(0)] = d.get(m.group(0), 0) + 1
        return d
    a, b = nums(old), nums(new)
    rem = [k for k, v in a.items() if v > b.get(k, 0)]
    add = [k for k, v in b.items() if v > a.get(k, 0)]
    return rem, add
MARK_RE = re.compile(r"<!--R\d+-[^>]*?-->")


MARKER_ANY_RE = re.compile(r"〔?【(?:待作者補充|待作者確認|建議補充)[^】]*】〕?|<!--R\d+-[^>]*?-->")


def _restore_locks(old, new):
    """把新稿中被動過的鎖定區塊還原成舊稿內容；區塊內被塞入的標記移到區塊之後。
    回傳 (新稿, 是否有還原, 區塊數不符)。"""
    old_inner = LOCK_RE.findall(old)
    ms = list(LOCK_RE.finditer(new))
    if len(old_inner) != len(ms):
        return new, False, True
    out, pos, changed = [], 0, False
    for m, oi in zip(ms, old_inner):
        out.append(new[pos:m.start()])
        ni = m.group(1)
        if MARK_RE.sub("", ni) != oi:
            changed = True
            moved = [x for x in MARKER_ANY_RE.findall(ni) if x not in oi]
            out.append("<!--LOCK-->" + oi + "<!--/LOCK-->" + ("".join(moved) if moved else ""))
        else:
            out.append(m.group(0))
        pos = m.end()
    out.append(new[pos:])
    return "".join(out), changed, False


def _grab(raw, tag):
    m = re.search(r"<%s>(.*?)</%s>" % (tag, tag), raw or "", re.S)
    return m.group(1).strip() if m else ""


EDIT_RE = re.compile(r"<edit\b([^>]*)>(.*?)</edit>", re.S)
ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
MIN_FIND = 5  # 引用的原句至少 5 個字（去除空白後），避免太短而不唯一


def _has_block(raw, tag):
    return re.search(r"<%s>.*?</%s>" % (tag, tag), raw or "", re.S) is not None


def _squash_map(text):
    """去除所有空白後的字串，以及每個字元在原文中的位置。"""
    chars, idx = [], []
    for i, ch in enumerate(text):
        if not ch.isspace():
            chars.append(ch)
            idx.append(i)
    return "".join(chars), idx


def _locate(old, find):
    """在舊稿中找 find；回傳 (start, end, 錯誤訊息)。先精確比對，找不到再忽略空白與換行比對。必須唯一。"""
    n = old.count(find)
    if n == 1:
        i = old.index(find)
        return i, i + len(find), ""
    if n > 1:
        return None, None, "原句在稿中出現 %d 次，無法判斷改哪一處" % n
    sq_old, idx = _squash_map(old)
    sq_find, _ = _squash_map(find)
    if len(sq_find) < MIN_FIND:
        return None, None, "引用的原句太短"
    c = sq_old.count(sq_find)
    if c == 0:
        return None, None, "在稿中找不到這段原句"
    if c > 1:
        return None, None, "原句（忽略空白後）在稿中出現 %d 次，無法判斷改哪一處" % c
    j = sq_old.index(sq_find)
    return idx[j], idx[j + len(sq_find) - 1] + 1, ""


def _round_of(ids):
    m = re.search(r"R(\d+)", ids or "")
    return m.group(1) if m else "0"


def _apply_edits(old, edits_raw):
    """把修改清單套用到舊稿。回傳 (套用後含標記的稿件, 成功筆數, 失敗清單[(ids, 原因)], 成功的意見 id 集合)。
    所有位置都先在「舊稿」上定位，再由後往前一次替換，因此修改之間互不影響；重疊、碰到鎖定區塊、找不到、不唯一的修改一律跳過。"""
    lock_spans = [(m.start(), m.end()) for m in LOCK_RE.finditer(old)]
    plan, fails, done_ids = [], [], set()
    for m in EDIT_RE.finditer(edits_raw):
        attrs = dict(ATTR_RE.findall(m.group(1)))
        body = m.group(2)
        ids = (attrs.get("ids") or attrs.get("id") or "").strip()
        action = (attrs.get("action") or "replace").strip().lower()
        f = _grab(body, "find")
        r = _grab(body, "replace")
        if action not in ("replace", "insert_after", "insert_before", "delete"):
            fails.append((ids, "不認得的動作：%s" % action))
            continue
        if not f:
            fails.append((ids, "缺少 find（要修改的原句）"))
            continue
        if action != "delete" and not r:
            fails.append((ids, "缺少 replace（修改後的文字）"))
            continue
        a, b, err = _locate(old, f)
        if err:
            fails.append((ids, err))
            continue
        if any(a < le and b > ls for ls, le in lock_spans):
            fails.append((ids, "碰到受保護（LOCK）區塊"))
            continue
        plan.append((a, b, action, r, ids))
    plan.sort(key=lambda x: (x[0], x[1]))
    kept, last_end = [], -1
    for it in plan:
        if it[0] < last_end:
            fails.append((it[4], "與前一筆修改的位置重疊"))
            continue
        kept.append(it)
        last_end = it[1]
    out, pos = [], 0
    for a, b, action, r, ids in kept:
        out.append(old[pos:a])
        tag = "<!--R%s-%s-->" % (_round_of(ids), ids) if ids else ""
        orig = old[a:b]
        if action == "replace":
            out.append(r + tag)
        elif action == "insert_after":
            out.append(orig + r + tag)
        elif action == "insert_before":
            out.append(r + tag + orig)
        else:  # delete
            out.append(tag)
        pos = b
        for i in re.split(r"[,，、\s]+", ids):
            if i:
                done_ids.add(i)
    out.append(old[pos:])
    return "".join(out), len(kept), fails, done_ids


def main(reviser_raw: str, old_manuscript: str, fail_streak: float = 0) -> dict:
    raw_len = len(reviser_raw or "")
    reviser_raw = re.sub(r"<think>.*?</think>", "", reviser_raw or "", flags=re.S)
    info = []  # 僅供提示，不影響是否繼續循環
    hard = []  # 硬失敗：本輪修訂作廢，沿用舊稿
    soft = []  # 軟警示：採用修訂稿，但停止循環交給人工
    marked = _grab(reviser_raw, "revised_manuscript")
    edit_fails, edit_done, n_applied, n_edits_raw = [], set(), 0, 0
    edits_mode = False
    if not marked and _has_block(reviser_raw, "edits"):
        edits_mode = True
        edits_raw = _grab(reviser_raw, "edits")
        n_edits_raw = len(EDIT_RE.findall(edits_raw))
        marked, n_applied, edit_fails, edit_done = _apply_edits(old_manuscript, edits_raw)
        if n_edits_raw and not n_applied:
            hard.append("修改清單共 %d 筆，全部無法套用（%s）" % (n_edits_raw, "；".join(sorted({e for _, e in edit_fails})[:3])))
            marked = ""
        elif edit_fails:
            info.append("修改清單共 %d 筆，套用 %d 筆；以下 %d 筆無法套用，已略過：%s" % (
                n_edits_raw, n_applied, len(edit_fails),
                "；".join("%s（%s）" % (i or "無意見ID", e) for i, e in edit_fails[:6]) + ("…" if len(edit_fails) > 6 else "")))
        elif n_edits_raw:
            info.append("修改清單共 %d 筆，全部套用。" % n_applied)
        else:
            info.append("修改清單為空：修稿者本輪沒有修改稿件。")
    table = _grab(reviser_raw, "response_table")
    plain = _grab(reviser_raw, "plain_summary")
    summary_raw = _grab(reviser_raw, "summary_json")

    summary = {}
    if summary_raw:
        s = re.sub(r"^```[a-zA-Z]*\s*", "", summary_raw.strip())
        s = re.sub(r"\s*```$", "", s)
        try:
            summary = json.loads(s, strict=False)
        except Exception:
            soft.append("summary_json 解析失敗")
    else:
        info.append("缺少 summary_json（無法得知修稿者自述的處理狀況，已採用修訂稿）")

    if not marked:
        stripped = reviser_raw.strip()
        if not stripped:
            hard.append("修稿者沒有輸出任何內容（原始輸出長度 %d 字元；若遠大於 0，代表內容全在思考區塊內，常見原因是思考用光了輸出額度）" % raw_len)
        elif "<edits>" in stripped and not edits_mode:
            hard.append("修改清單被截斷（有開頭標籤、沒有結尾標籤；輸出長度 %d 字元；常見原因：思考用光了輸出額度）" % len(stripped))
        elif edits_mode:
            pass  # 已在上方記錄硬失敗原因
        elif "<revised_manuscript>" in stripped:
            hard.append("修訂稿輸出被截斷（有開頭標籤、沒有結尾標籤；輸出長度 %d 字元；常見原因：思考用光了輸出額度）" % len(stripped))
        else:
            hard.append("修稿者沒有依指定標籤輸出（輸出長度 %d 字元，開頭：%s）" % (len(stripped), stripped[:60].replace("\n", "⏎")))

    # 硬性檢查 1：鎖定區塊必須與舊稿逐字相同；若只是被動到，先自動還原（其餘修訂照常採用）
    if marked and LOCK_RE.findall(old_manuscript) != LOCK_RE.findall(MARK_RE.sub("", marked)):
        fixed, changed, count_bad = _restore_locks(old_manuscript, marked)
        if not count_bad and changed:
            marked = fixed
            info.append("鎖定區塊曾被修稿者更動（多半是在表格內插入標記），程式已自動還原為原稿內容，其餘修訂照常採用；插入的標記已移到區塊之後")
    clean = MARK_RE.sub("", marked) if marked else old_manuscript
    if marked and LOCK_RE.findall(old_manuscript) != LOCK_RE.findall(clean):
        hard.append("鎖定區塊遺失或數量不符，本輪修訂作廢")
    # 硬性檢查 2：長度驟減可能是輸出被截斷
    if marked and len(clean) < 0.7 * len(old_manuscript):
        hard.append("修訂稿長度不足舊稿 70%，疑似被截斷，本輪修訂作廢")

    # 軟警示：修稿者自述的狀況
    if summary.get("locked_regions_touched"):
        soft.append("修稿者回報動到鎖定區塊，請人工核對")
    failed_reason = {}
    if edits_mode and not hard:
        for _ids, _err in edit_fails:
            for _i in re.split(r"[,，、\s]+", _ids or ""):
                if _i:
                    failed_reason.setdefault(_i, _err)
    progress = len([d for d in (summary.get("dispositions") or [])
                    if isinstance(d, dict) and d.get("action") in ("adopted", "partial")
                    and not (edits_mode and d.get("id") in failed_reason and d.get("id") not in edit_done)])
    if edits_mode and not hard:
        claimed = [d.get("id", "") for d in (summary.get("dispositions") or [])
                   if isinstance(d, dict) and d.get("action") in ("adopted", "partial")]
        no_edit = [i for i in claimed if i and i not in edit_done and i not in failed_reason]
        if no_edit:
            info.append("以下意見自述已採納或部分採納，但修改清單中沒有引用該意見編號的修改（可能併入其他意見的修改，請核對）：%s" % "、".join(no_edit[:8]))
    if summary.get("recommendation") == "stop_human":
        if progress == 0:
            soft.append("修稿者建議交由人工處理（本輪沒有任何可處理的修訂，剩餘問題多需作者補充事實）")
        else:
            info.append("修稿者建議交由人工處理，但本輪已有 %d 項修訂，循環繼續，由下一輪審查檢驗" % progress)

    if marked and not hard:
        rem, add = _num_diff(old_manuscript, clean)
        if rem or add:
            info.append("⚠本輪改動了正文中的數字：移除 %s；新增 %s（請核對，數字原則上不得更動）" % ("、".join(rem[:8]) or "無", "、".join(add[:8]) or "無"))
    todo = len(re.findall(r"【待作者補充|【待作者確認|編輯註", clean)) if marked else 0
    if todo and not hard:
        info.append("提示：稿中約有 %d 處待作者補充或確認的標記（不影響循環，定稿前請處理）" % todo)

    if hard:
        clean = old_manuscript
        marked = old_manuscript

    lines = []
    for d in summary.get("dispositions", []) or []:
        action = d.get("action", "")
        did = d.get("id", "")
        if edits_mode and not hard and action in ("adopted", "partial") and did in failed_reason and did not in edit_done:
            action = "human"
            d = dict(d, action="human", reason="修稿者提出的修改無法套用到稿件（%s），本條視為尚未處理" % failed_reason[did])
        if action == "adopted":
            lines.append("%s｜採納" % d.get("id", ""))
        else:
            lines.append("%s｜%s｜%s" % (d.get("id", ""), action, d.get("reason", "")))

    if edits_mode and not hard:
        _unapplied = sorted({i for i, e in failed_reason.items() if i not in edit_done})
        if _unapplied and table:
            table = table.rstrip() + "\n\n---\n\n## 【系統核對】以下意見的修改未能套用到稿件，上表若寫「已加標記／已修改」，以本段為準（視為尚未處理，稿中沒有對應標記）\n" + \
                "\n".join("- %s：%s" % (i, failed_reason[i]) for i in _unapplied) + "\n"

    flags = hard + soft
    streak_next = int(fail_streak or 0) + 1 if hard else 0
    if hard and streak_next < 2:
        decision, reason = "continue", "修稿者輸出無法採用（" + "；".join(hard) + "），本輪沿用舊稿並重試一次"
    elif hard:
        decision, reason = "stop_human", "修稿者連續 %d 次輸出無法採用：" % streak_next + "；".join(hard)
    elif soft:
        decision, reason = "stop_human", "修稿者判斷剩餘問題需作者補充事實，或本輪修訂有警示：" + "；".join(flags)
    else:
        decision, reason = "continue", "尚未達停止條件"
    return {
        "ok": "true" if not flags else "false",
        "fail_streak_next": streak_next,
        "override_decision": decision,
        "override_reason": reason,
        "manuscript_clean": clean,
        "manuscript_marked": marked,
        "manuscript_prev": old_manuscript,
        "response_table": table,
        "dispositions_summary": "\n".join(lines),
        "plain_summary": plain,
        "flags": "；".join(flags + info),
    }
