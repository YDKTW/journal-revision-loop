# Dify「程式碼執行」節點 6：產生「原稿與最終稿的句子比對清單（累計所有輪次）」，供終審意見節點審計
# 輸入變數：prev_manuscript（比對基準：使用者上傳的原稿，即循環變數 orig_manuscript）、final_manuscript（最終稿件）、unresolved_a、unresolved_b（未解決意見的 JSON 字串）
# 輸出變數：changes_digest (String)、unresolved (String，兩位審查者未解決意見的精簡文字)
# 只比對文字與數字（標點、空白與版面調整不算修改）；每個修改處列出「原句 → 新句」，全新句子列為「新增」。

import difflib
import json
import re
import unicodedata

COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
LOCK_BLOCK_RE = re.compile(r"<!--LOCK-->.*?<!--/LOCK-->", re.S)
TODO_RE = re.compile(r"〔?【(?:待作者補充|待作者確認|建議補充)[^】]*】〕?")
NUM_RE = re.compile(r"-?\.?\d+(?:[.,]\d+)*")
SENT_RE = re.compile(r"[^。！？；!?;\n]*[。！？；!?;]?")
MAX_ITEMS = 60


def _sig(t):
    """內容簽章：只保留文字與數字；但保留數字中的小數點、千分位逗號與負號，避免「-.97」被悄悄改成「.97」。"""
    t = unicodedata.normalize("NFKC", t)
    t = re.sub(r"[-–−—](?=\.?\d)", "ⅿ", t)        # 數字前的負號或連字號
    t = re.sub(r"(?<=\d)[.,](?=\d)", "ⅾ", t)        # 數字之間的小數點或千分位逗號
    t = re.sub(r"(?<![\w])\.(?=\d)", "ⅾ", t)        # 省略前導 0 的小數（如 .31）
    return "".join(ch for ch in t if unicodedata.category(ch)[0] in "LN").lower()


def _split_line(ln):
    """切句。【待作者…】標記獨立成一段（回傳時略過，另行計數），避免與相鄰句子黏在一起被誤判為「新增」。"""
    ln = TODO_RE.sub("", ln)  # 先移除標記再切句，標記插在句中時原句不會被切成兩段而重複列出
    return [x.group(0) for x in SENT_RE.finditer(ln)]


def _sents(text):
    out = []
    for ln in COMMENT_RE.sub("", text or "").split("\n"):
        if "\t" in ln:
            continue
        for seg in _split_line(ln):
            s = seg.strip()
            if _sig(s):
                out.append(s)
    return out


def _number_changes(prev, fin):
    """比對正文（不含鎖定表格與【待作者…】標記）中的數字：原稿有、最終稿沒有；最終稿新增。"""
    def nums(t):
        t = TODO_RE.sub(" ", LOCK_BLOCK_RE.sub(" ", t or ""))
        t = COMMENT_RE.sub("", t)
        return [(m.group(0), t[max(0, m.start() - 10):m.end() + 10].replace("\n", " ")) for m in NUM_RE.finditer(t)]
    a, b = nums(prev), nums(fin)
    ca, cb = {}, {}
    for n, c in a:
        ca.setdefault(n, []).append(c)
    for n, c in b:
        cb.setdefault(n, []).append(c)
    removed, added = [], []
    for n, cs in ca.items():
        k = len(cs) - len(cb.get(n, []))
        if k > 0:
            removed.append((n, cs[0]))
    for n, cs in cb.items():
        k = len(cs) - len(ca.get(n, []))
        if k > 0:
            added.append((n, cs[0]))
    return removed, added


def _verdict(paper_type, ua, ub):
    """計畫書預審：由程式依未解決意見的嚴重度決定可送件度，避免終審模型每次判斷不一致。其他類型回傳空字串。"""
    if paper_type != "teaching_practice_proposal":
        return ""
    crit = major = minor = 0
    for raw in (ua, ub):
        try:
            lst = json.loads(raw or "[]")
        except Exception:
            lst = []
        for i in lst if isinstance(lst, list) else []:
            if isinstance(i, dict) and i.get("status") != "resolved":
                sv = i.get("severity")
                crit += sv == "critical"
                major += sv == "major"
                minor += sv == "minor"
    if crit:
        v = "需重新構思或補齊必要條件"
    elif major >= 2:
        v = "需明顯修改"
    elif major == 1 or minor:
        v = "小修後可送件"
    else:
        v = "可送件"
    demoted = 0
    for raw in (ua, ub):
        try:
            demoted += len([i for i in json.loads(raw or "[]") if isinstance(i, dict) and i.get("screen_note") and i.get("status") != "resolved"])
        except Exception:
            pass
    extra = "；另有 %d 項意見經程式核對（證據找不到或第2輪起凍結）降為 minor、未計入結論，請人工查看附錄B的系統註記" % demoted if demoted else ""
    return "【程式判定的可送件度（結論一欄必須照抄此判定，不得自行升降級；可補充原因）】\n%s（未解決 critical %d、major %d、minor %d；數量為兩位審查者加總，同一問題可能重複計算%s）" % (v, crit, major, minor, extra)


def main(prev_manuscript: str, final_manuscript: str, unresolved_a: str = "[]", unresolved_b: str = "[]", paper_type: str = "") -> dict:
    prev, fin = _sents(prev_manuscript), _sents(final_manuscript)
    prev_sigs = {_sig(s): s for s in prev}
    prev_pool = list(prev_sigs.items())
    items = []
    for s in fin:
        sg = _sig(s)
        if sg in prev_sigs:
            continue
        best, best_r = None, 0.0
        for ps, orig in prev_pool:
            if abs(len(ps) - len(sg)) > max(len(sg), len(ps)) * 0.6:
                continue
            sm = difflib.SequenceMatcher(None, ps, sg, autojunk=False)
            if sm.real_quick_ratio() < 0.5 or sm.quick_ratio() < 0.5:
                continue
            r = sm.ratio()
            if r > best_r:
                best, best_r = orig, r
        if best is not None and best_r >= 0.55:
            items.append("改寫：「%s」→「%s」" % (best[:160], s[:200]))
        else:
            items.append("新增：「%s」" % s[:200])
    cur = {_sig(s) for s in fin}
    removed = [s for s in prev if _sig(s) not in cur]
    # 被「改寫」的句子（有對應的新句）不算刪除；只列出完全找不到對應的原句
    new_sigs = [_sig(s) for s in fin]
    gone = []
    for s0 in removed:
        sg = _sig(s0)
        similar = any(difflib.SequenceMatcher(None, sg, ns, autojunk=False).quick_ratio() >= 0.7 and
                      difflib.SequenceMatcher(None, sg, ns, autojunk=False).ratio() >= 0.55 for ns in new_sigs
                      if abs(len(ns) - len(sg)) <= max(len(ns), len(sg)) * 0.6)
        if not similar:
            gone.append(s0)
    digest = ""
    if not items and not gone:
        digest = "（稿件與原稿沒有差異）"
    else:
        digest = "\n".join("%d. %s" % (i + 1, t) for i, t in enumerate(items[:MAX_ITEMS])) if items else "（沒有新增或改寫的句子）"
        if len(items) > MAX_ITEMS:
            digest += "\n…（另有 %d 處，略）" % (len(items) - MAX_ITEMS)
        if gone:
            digest += "\n\n【被刪除的原句（共 %d 句，在最終稿中找不到對應內容）】\n" % len(gone)
            digest += "\n".join("- 「%s」" % g[:140] for g in gone[:30])

    removed_n, added_n = _number_changes(prev_manuscript, final_manuscript)
    if removed_n or added_n:
        if digest.startswith("（稿件與原稿沒有差異）"):
            digest = ""
        digest += "\n\n【數字變動（正文；不含鎖定表格與待作者標記）——統計值、年份、人數被改動時，必須逐項核對是否有標示】\n"
        if removed_n:
            digest += "原稿有、最終稿沒有：" + "；".join("「%s」（…%s…）" % (n, c) for n, c in removed_n[:25]) + "\n"
        if added_n:
            digest += "最終稿新增：" + "；".join("「%s」（…%s…）" % (n, c) for n, c in added_n[:25]) + "\n"
        digest = digest.strip()
    un = []
    for label, raw in (("審查A", unresolved_a), ("審查B", unresolved_b)):
        try:
            for it in json.loads(raw or "[]"):
                if isinstance(it, dict):
                    un.append("[%s｜%s｜%s] %s" % (label, it.get("severity", ""), it.get("location", ""), (it.get("problem") or "")[:160]) + ("［系統註記：%s；原嚴重度 %s］" % (it.get("screen_note"), it.get("orig_severity", "")) if it.get("screen_note") else ""))
        except Exception:
            pass
    hint = _verdict(paper_type, unresolved_a, unresolved_b)
    mark_n = len(TODO_RE.findall(final_manuscript or "")) - len(TODO_RE.findall(prev_manuscript or ""))
    if digest.startswith("（稿件與原稿沒有差異）") and mark_n > 0:
        digest = "（正文句子沒有新增或改寫；修稿者只在正文加入了 %d 處【待作者補充／確認】標記，標記不列入句子比對）" % mark_n
        cue = ("【修訂狀況（程式比對）】修稿者已執行修訂，但只加入 %d 處待作者標記，沒有改動正文句子。"
               "這不是「未修稿」：終審不得寫「未產出任何修訂」「稿件與原稿完全一致」。"
               "第二節寫「修稿者在正文加入 %d 處待作者標記，將審查意見轉為作者待辦」；第三節寫「正文句子無差異，無需審計」。"
               "不得宣稱任何錯字、用語、數字、表格或段落「已修正」；對照表【系統核對】段落列出的意見視為尚未處理。") % (mark_n, mark_n)
        hint = (hint + "\n\n" + cue) if hint else cue
    elif digest.startswith("（稿件與原稿沒有差異）"):
        cue = "【修訂狀況（程式比對）】最終稿與原稿沒有任何差異，代表本次流程沒有任何修訂被採用。終審意見不得宣稱任何錯字、用語、數字、表格或段落「已修正」「已改為」「已加註」；所有審查意見都應視為尚未處理，並以「建議作者修改」的語氣撰寫。"
        hint = (hint + "\n\n" + cue) if hint else cue
    return {"changes_digest": digest, "unresolved": "\n".join(un) if un else "（無）", "verdict_hint": hint}
