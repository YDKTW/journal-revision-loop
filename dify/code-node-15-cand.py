# -*- coding: utf-8 -*-
# Dify「程式碼執行」節點 15：把兩批 Google Scholar（SerpApi）檢索結果整理成「編號候選文獻清單」
# 輸入：b1..b5 (String) 五個 HTTP 節點的 body（第一批三個、顧問批兩個；可空）
# 輸出：cand_list (String，給 LLM 看的編號清單)、cand_json (String，JSON 陣列，給後續程式用)
import json
import re


def _rows(body):
    try:
        d = json.loads(body or "")
    except Exception:
        return []
    if not isinstance(d, dict) or d.get("error"):
        return []
    return [r for r in (d.get("organic_results") or []) if isinstance(r, dict)]


def _norm(s):
    return re.sub(r"\W+", "", (s or "").lower())[:60]


def main(b1: str = "", b2: str = "", b3: str = "", b4: str = "", b5: str = "") -> dict:
    per = [[_r for _r in _rows(b)] for b in (b1, b2, b3, b4, b5)]
    seen, items = set(), []
    for k in range(max([len(x) for x in per] + [0])):
        for cur in per:
            if k >= len(cur):
                continue
            r = cur[k]
            title = re.sub(r"\s+", " ", str(r.get("title") or "")).strip()
            key = _norm(title)
            if not title or key in seen:
                continue
            seen.add(key)
            pub = str((r.get("publication_info") or {}).get("summary", ""))[:160]
            snippet = re.sub(r"\s+", " ", str(r.get("snippet") or "")).strip()[:200]
            items.append({"n": len(items) + 1, "title": title[:160], "pub": pub, "snippet": snippet,
                          "link": str(r.get("link") or "")[:300]})
            if len(items) >= 40:
                break
        if len(items) >= 40:
            break
    if not items:
        return {"cand_list": "（沒有可用的候選文獻：未設定 SERPAPI_KEY，或檢索無結果）", "cand_json": "[]"}
    lines = ["[%d] 《%s》｜%s｜摘要片段：%s" % (i["n"], i["title"], i["pub"] or "出處不明", i["snippet"] or "（無）") for i in items]
    return {"cand_list": "\n".join(lines), "cand_json": json.dumps(items, ensure_ascii=False)}
