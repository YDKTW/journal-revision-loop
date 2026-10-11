# Dify「程式碼執行」節點 11：合併三次 Google Scholar（SerpApi）檢索結果，整理成給審查者參考的文獻清單
# 輸入：b1, b2, b3 (String) 三個 HTTP 請求節點的 body；queries (String) 三組查詢（僅供註記）
# 輸出：lit_context (String) 給審查者；lit_report (String) 給人看的檢索摘要
# 原則：任何一步失敗都不中斷流程，只是不提供文獻背景。
import json
import re

HEADER = (
    "【文獻背景檢索結果（Google Scholar，經 SerpApi；僅供審查者參考）】\n"
    "使用規則：\n"
    "1. 這份清單是系統以關鍵字自動檢索的結果，不是稿件內容，也不代表稿件已引用；不得當作稿件的引註來核對。\n"
    "2. 只能用來評估稿件的「文獻定位」與「研究缺口」是否站得住腳，且此類意見嚴重度最高為 minor；不得據此提出 major 或 critical。\n"
    "3. 若建議作者補充文獻，只能從下列清單挑選，並寫出清單上的標題；清單以外的文獻不得憑記憶補充書目、年份或作者。\n"
    "4. 清單只有標題與摘要片段，沒有全文；不得評論超出片段的細節。檢索不到，不代表稿件的引註不實。\n"
    "5. 清單可能夾雜與稿件無關的來源（例如大陸簡體中文的一般教育研究）；與稿件主題無直接關係者請忽略，不得要求作者引用。\n"
)


def _parse(body):
    try:
        d = json.loads(body or "")
    except Exception:
        return [], "非 JSON 回應"
    if not isinstance(d, dict):
        return [], "格式不符"
    if d.get("error"):
        return [], str(d.get("error"))[:80]
    return d.get("organic_results") or [], ""


def main(b1: str = "", b2: str = "", b3: str = "", queries: str = "") -> dict:
    seen, errs = set(), []
    per = []  # 每組查詢各自的結果，之後輪流取用，避免第 3 組（英文）被前兩組擠掉
    for i, b in enumerate((b1, b2, b3), 1):
        cur = []
        if (b or "").strip():
            res, err = _parse(b)
            if err:
                errs.append("查詢%d：%s" % (i, err))
            for r in res:
                if not isinstance(r, dict):
                    continue
                title = re.sub(r"\s+", " ", str(r.get("title") or "")).strip()
                key = re.sub(r"\W+", "", title.lower())[:60]
                if not title or key in seen:
                    continue
                seen.add(key)
                pub = (r.get("publication_info") or {}).get("summary", "")
                cited = ((r.get("inline_links") or {}).get("cited_by") or {}).get("total")
                snippet = re.sub(r"\s+", " ", str(r.get("snippet") or "")).strip()[:220]
                cur.append((title[:150], str(pub)[:150], cited, snippet))
        per.append(cur)
    rows = []
    for k in range(max([len(x) for x in per] + [0])):
        for cur in per:
            if k < len(cur):
                rows.append(cur[k])
    rows = rows[:15]
    if not rows:
        why = "；".join(errs) if errs else "沒有取得任何檢索結果（可能未設定 SERPAPI_KEY，或查詢無結果）"
        return {"lit_context": "【文獻背景檢索結果】本次沒有可用的檢索結果，請審查者僅就稿件內部資訊審查，不要推測外部文獻。",
                "lit_report": "文獻檢索：未取得結果。" + why}
    lines = []
    for n, (t, p, c, s) in enumerate(rows, 1):
        line = "%d. 《%s》｜%s" % (n, t, p or "出處不明")
        if c:
            line += "｜被引 %s 次" % c
        if s:
            line += "\n   摘要片段：" + s
        lines.append(line)
    ctx = HEADER + "\n" + "\n".join(lines)
    rep = "文獻檢索：共 %d 筆（去重後）。查詢：%s%s" % (len(rows), queries.replace("\n", "；"), ("。部分失敗：" + "；".join(errs)) if errs else "")
    return {"lit_context": ctx, "lit_report": rep}
