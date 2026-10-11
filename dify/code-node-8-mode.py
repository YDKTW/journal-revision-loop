# -*- coding: utf-8 -*-
# 程式節點 8：決定審查模式，並取得授課計畫書文字
#
# 論文類型為 teaching_practice_proposal（教學實踐研究計畫書預審）時，改用計畫書版的審查標準、審查者提示詞、
# 修稿規則、終審提示詞與官方體例；其餘類型原樣回傳期刊審查的設定（與先前版本完全相同）。
#
# 輸入：paper_type、course_text_in（授課計畫書文字）、prior_pasted／prior_extracted（前期計畫書）、
#       journal_rules / journal_source / layout_spec（上一個節點「決定採用的期刊規範」的輸出）、format_source、
#       以及環境變數中的各版本提示詞。
# 輸出：course_text、rubric、sys_a、sys_b、reviser_system、final_system、journal_rules、journal_source、layout_spec


def main(paper_type: str = "", course_text_in: str = "", prior_pasted: str = "", prior_extracted: str = "", journal_rules: str = "",
         journal_source: str = "", layout_spec: str = "", format_source: str = "",
         rubric_j: str = "", rubric_p: str = "", sys_a_j: str = "", sys_a_p: str = "", sys_b_j: str = "", sys_b_p: str = "",
         skill_j: str = "", skill_p: str = "", final_j: str = "", final_p: str = "",
         default_rules_p: str = "", layout_p: str = "") -> dict:
    proposal = (paper_type == "teaching_practice_proposal")
    rubric = rubric_p if proposal else rubric_j

    def fill(t):
        return (t or "").replace("{{rubric}}", rubric).replace("{{paper_type}}", paper_type or "")

    course = (course_text_in or "").strip() if proposal else ""
    prior = ((prior_pasted or "").strip() or (prior_extracted or "").strip()) if proposal else ""
    rules, source, layout = journal_rules, journal_source, layout_spec
    if proposal and (format_source or "") != "上傳期刊格式檔":
        rules = default_rules_p
        source = "內建預設格式（教育部教學實踐研究計畫徵件說明）"
        layout = layout_p
    return {
        "course_text": course,
        "prior_text": prior,
        "rubric": rubric,
        "sys_a": fill(sys_a_p if proposal else sys_a_j),
        "sys_b": fill(sys_b_p if proposal else sys_b_j),
        "reviser_system": skill_p if proposal else skill_j,
        "final_system": final_p if proposal else final_j,
        "journal_rules": rules,
        "journal_source": source,
        "layout_spec": layout,
    }
