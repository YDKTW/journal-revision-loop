# -*- coding: utf-8 -*-
# Dify「程式碼執行」節點 13：解析「學術顧問」LLM 的輸出
# 輸入：llm_text (String) 學術顧問節點的輸出（節點失敗時為空）
# 輸出：advisor_text (String) 去掉檢索標籤後的建議本文；q1, q2 (String) 第二批檢索字串；all (String)
import json
import re


def _clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()[:120]


def main(llm_text: str = "") -> dict:
    t = re.sub(r"<think>.*?</think>", "", llm_text or "", flags=re.S).strip()
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    qs = []
    m = re.search(r"<queries>\s*(\{.*?\})\s*</queries>", t, re.S)
    if m:
        try:
            data = json.loads(m.group(1), strict=False)
            lst = data.get("queries") if isinstance(data, dict) else None
            if isinstance(lst, list):
                qs = [_clean(x) for x in lst if _clean(x)]
        except Exception:
            qs = []
    text = re.sub(r"<queries>.*?</queries>", "", t, flags=re.S).strip()
    qs = (qs + ["", ""])[:2]
    return {"advisor_text": text, "q1": qs[0], "q2": qs[1], "all": "\n".join(x for x in qs if x)}
