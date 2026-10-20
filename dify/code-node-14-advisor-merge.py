# -*- coding: utf-8 -*-
# Dify「程式碼執行」節點 14：合併學術顧問建議與第二批文獻檢索
# 輸入：advisor_text (String)、lit2_context (String 第二批檢索清單，含使用規則標頭；可空)、lit2_report (String)
# 輸出：advisor_context（給審查者；含使用規範）、advisor_report（給作者；含建議本文與第二批文獻檢索摘要）、advisor_status
import re

MAX_CTX = 11000


def main(advisor_text: str = "", lit2_context: str = "", lit2_report: str = "") -> dict:
    t = (advisor_text or "").strip()
    if not t:
        return {"advisor_context": "", "advisor_report": "", "advisor_status": "未產生"}
    body = t if len(t) <= MAX_CTX else t[:MAX_CTX] + "\n（以下因長度限制省略）"
    head = ("【學術顧問建議（輔助資料，v1.4）】\n"
            "使用規範：\n"
            "1. 這份建議由模型依初稿產生，**可能誤判**；引用前必須對照稿件原文，不得直接當作事實。凡標「請作者查證」者，審查者不得斷言其正確或錯誤。\n"
            "2. 它描述的是初稿（第 1 輪前）的狀態；稿件修訂後，缺口可能已補上，請以當前稿件為準。\n"
            "3. 審查者：可把其中的選項寫成具體意見（嚴重度仍依審查標準判定，理論與方法的建議性意見通常為 minor 至 major），"
            "但必須自行判斷該建議對本稿是否成立；顧問未提及的問題，仍須依標準提出，不得因顧問沒說而省略。\n"
            "4. 理論與方法只可寫名稱與核心概念；作者、年份、書目只能取自文獻清單，不得憑記憶補充。\n"
            "5. 修稿者只能依審查者意見處理：理論、研究設計、文獻內容一律標【待作者補充：…】或【待作者確認：…】，不得代為新增。\n")
    ctx = head + "\n" + body + "\n"
    if (lit2_context or "").strip():
        ctx += "\n" + lit2_context.strip() + "\n"
    rep = "# 學術顧問建議（供作者決定；非審查意見，未自動套用於稿件）\n\n" + t + "\n"
    if (lit2_report or "").strip():
        rep += "\n---\n\n## 針對上述理論與方法方向的第二批文獻檢索\n\n" + lit2_report.strip() + "\n"
    return {"advisor_context": ctx, "advisor_report": rep, "advisor_status": "已產生（%d 字）" % len(t)}
