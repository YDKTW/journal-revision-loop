# Dify「程式碼執行」節點 10：解析「查詢字串產生」LLM 的輸出，得到三組 Google Scholar 查詢
# 輸入：llm_text (String) 查詢產生節點的輸出；fallback (String) 前處理節點的 kb_query（LLM 失敗時備用）
# 輸出：q1, q2, q3, all (String)
import json
import re


def _clean(s):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s[:120]


def main(llm_text: str = "", fallback: str = "") -> dict:
    qs = []
    t = re.sub(r"<think>.*?</think>", "", llm_text or "", flags=re.S)
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        try:
            data = json.loads(m.group(0), strict=False)
            lst = data.get("queries") if isinstance(data, dict) else None
            if isinstance(lst, list):
                qs = [_clean(x) for x in lst if _clean(x)]
        except Exception:
            qs = []
    if not qs and fallback:
        qs = [_clean(fallback)]
    qs = (qs + ["", "", ""])[:3]
    return {"q1": qs[0], "q2": qs[1], "q3": qs[2], "all": "\n".join(x for x in qs if x)}
