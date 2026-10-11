# Dify「程式碼執行」節點 2：拆解 Claude 修稿輸出，並用程式強制檢查鎖定區塊
# 輸入變數：
#   reviser_raw (String)    Claude 修稿者 LLM 節點的輸出文字
#   old_manuscript (String) 本輪送審的稿件（current_manuscript）
#   fail_streak (Number)    連續「修稿輸出無法採用」的次數（循環變數）
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


def main(reviser_raw: str, old_manuscript: str, fail_streak: float = 0) -> dict:
    raw_len = len(reviser_raw or "")
    reviser_raw = re.sub(r"<think>.*?</think>", "", reviser_raw or "", flags=re.S)
    info = []  # 僅供提示，不影響是否繼續循環
    hard = []  # 硬失敗：本輪修訂作廢，沿用舊稿
    soft = []  # 軟警示：採用修訂稿，但停止循環交給人工
    marked = _grab(reviser_raw, "revised_manuscript")
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
    progress = len([d for d in (summary.get("dispositions") or [])
                    if isinstance(d, dict) and d.get("action") in ("adopted", "partial")])
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
        if action == "adopted":
            lines.append("%s｜採納" % d.get("id", ""))
        else:
            lines.append("%s｜%s｜%s" % (d.get("id", ""), action, d.get("reason", "")))

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
