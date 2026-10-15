# -*- coding: utf-8 -*-
# 程式節點 12：合併對齊診斷（計畫書預審；其他論文類型直接回傳空字串）
#
# 輸入：llm_text（對齊診斷節點的輸出；節點被略過時為空）、paper_type
# 輸出：align_report（給審查者與修稿者的診斷文字；含標頭與使用規範）、align_status（供報告顯示）

import re

MAX_CHARS = 9000


def main(llm_text: str = "", paper_type: str = "") -> dict:
    if paper_type != "teaching_practice_proposal":
        return {"align_report": "", "align_status": "略過（非計畫書預審）"}
    t = (llm_text or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    if not t:
        return {"align_report": "【對齊診斷】本次未產生診斷結果；請審查者不要因此提意見。",
                "align_status": "未產生"}
    if len(t) > MAX_CHARS:
        t = t[:MAX_CHARS] + "\n（以下因長度限制省略）"
    head = ("【對齊診斷報告（輔助資料）】\n"
            "使用規範：\n"
            "1. 這份診斷由模型依初稿抽取，**可能誤判**；引用前必須對照稿件原文，不得直接當作事實。\n"
            "2. 它描述的是初稿（第 1 輪前）的狀態；稿件修訂後，缺口可能已補上，請以當前稿件為準。\n"
            "3. 審查者：可把「孤兒項」「論述深度」當作 B（文獻與理論）與 D（一致性矩陣）的檢查線索，嚴重度仍依審查標準判定。\n"
            "4. 修稿者：缺口只能標【待作者補充：…】，不得代為新增理論論述、文獻或研究設計內容。\n")
    return {"align_report": head + "\n" + t + "\n", "align_status": "已產生（%d 字）" % len(t)}
