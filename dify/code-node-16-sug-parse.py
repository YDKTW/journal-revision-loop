# -*- coding: utf-8 -*-
# Dify「程式碼執行」節點 16：解析並驗證「理論補強標註」LLM 的輸出
# 輸入：llm_text (String)、manuscript (String 最終稿)、cand_json (String)
# 輸出：sug_json (String：通過驗證的建議，含 slot 對應)、q1..q6 (String：Crossref 查詢字串)、status (String)
# 驗證：錨點句必須在稿中逐字出現且唯一、不在表格列或 LOCK 區塊內；文獻編號必須存在於候選清單；全部建議最多 8 則、不重複文獻最多 6 筆。
import json
import re

MAX_SUG, MAX_REFS = 8, 6
NONE_Q = "zzqq-none"
CJK = re.compile(r"[㐀-鿿]")


def _n(t):
    return re.sub(r"[\W_]+", "", (t or "").lower())


def _clean_q(title):
    # Crossref 查詢字串：含中日韓字元者不送（Crossref 涵蓋有限，且曾造成 HTTP 節點 Unknown error）；其餘移除會干擾參數解析的符號
    t = re.sub(r"\s+", " ", title or "").strip()
    if not t or len(CJK.findall(t)) / max(len(t), 1) > 0.15:
        return NONE_Q
    t = re.sub(r"[#&?%\\\"“”‘’<>{}|^`\[\]]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .,:;")
    return t[:150] or NONE_Q


def _lock_spans(t):
    return [(m.start(), m.end()) for m in re.finditer(r"<!--LOCK-->.*?<!--/LOCK-->", t, re.S)]


def main(llm_text: str = "", manuscript: str = "", cand_json: str = "") -> dict:
    empty = {"sug_json": "[]", "q1": NONE_Q, "q2": NONE_Q, "q3": NONE_Q, "q4": NONE_Q, "q5": NONE_Q, "q6": NONE_Q}
    t = re.sub(r"<think>.*?</think>", "", llm_text or "", flags=re.S).strip()
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return dict(empty, status="未產生建議")
    try:
        data = json.loads(m.group(0), strict=False)
    except Exception:
        return dict(empty, status="建議輸出格式錯誤（已略過）")
    sugs = data.get("suggestions") if isinstance(data, dict) else None
    if not isinstance(sugs, list):
        return dict(empty, status="沒有建議")
    try:
        cands = {int(c["n"]): c for c in json.loads(cand_json or "[]")}
    except Exception:
        cands = {}
    locks = _lock_spans(manuscript)
    msn = _n(manuscript)
    already = {n for n, c in cands.items() if len(_n(c.get("title"))) >= 12 and _n(c.get("title")) in msn}
    out, slots, dropped = [], [], 0
    for s in sugs:
        if len(out) >= MAX_SUG:
            break
        if not isinstance(s, dict):
            continue
        anchor = str(s.get("anchor") or "").strip()
        concept = str(s.get("concept") or "").strip()
        if not anchor or not concept or manuscript.count(anchor) != 1:
            dropped += 1
            continue
        pos = manuscript.index(anchor)
        if any(a <= pos < b for a, b in locks):
            dropped += 1
            continue
        ls = manuscript.rfind("\n", 0, pos) + 1
        if manuscript[ls:ls + 1] == "|" or manuscript[ls:ls + 4] == "<!--":
            dropped += 1
            continue
        refs = []
        for x in (s.get("refs") or [])[:3]:
            try:
                n = int(x)
            except Exception:
                continue
            if n not in cands or n in refs or n in already:
                continue
            if n not in slots:
                if len(slots) >= MAX_REFS:
                    continue
                slots.append(n)
            refs.append(n)
        out.append({"anchor": anchor, "concept": concept[:120],
                    "link": str(s.get("link") or "")[:300], "reason": str(s.get("reason") or "")[:400],
                    "options": str(s.get("options") or "")[:400], "verify": str(s.get("verify") or "")[:200],
                    "slots": [slots.index(n) + 1 for n in refs]})
    qs = []
    for n in slots:
        c = cands[n]
        qs.append(_clean_q(c.get("title", "")))
    qs += [NONE_Q] * (MAX_REFS - len(qs))
    skipped = sum(1 for q in qs[:len(slots)] if q == NONE_Q)
    note = ""
    if dropped:
        note += "；%d 則因錨點句無法定位而略過" % dropped
    if skipped:
        note += "；%d 筆為中文或不宜查詢，未送 Crossref" % skipped
    res = {"sug_json": json.dumps({"suggestions": out, "slot_cands": [cands[n] for n in slots]}, ensure_ascii=False),
           "status": "通過驗證 %d 則建議、%d 筆候選文獻%s" % (len(out), len(slots), note)}
    for i in range(MAX_REFS):
        res["q%d" % (i + 1)] = qs[i]
    return res
