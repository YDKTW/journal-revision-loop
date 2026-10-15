# 從 rubric、審查者 prompt、SKILL.md 與兩支程式碼節點，產生可匯入 Dify 的工作流 DSL（.yml）
# 用法：python3 build_dify_dsl.py   →  產生 journal-review-loop.yml
# 修改任何 .md 或 .py 後重新執行即可，不必手動重建節點。
import re
import uuid
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "journal-review-loop.yml"


def uid():
    return str(uuid.uuid4())


def read(p):
    return Path(p).read_text(encoding="utf-8")


# ---------- 讀取素材 ----------
rubric = read(ROOT / "review-rubric.md")
skill = read(ROOT / "journal-manuscript-reviser" / "SKILL.md")
skill_body = re.sub(r"^---.*?---\s*", "", skill, count=1, flags=re.S)
skill_body += "\n\n**本次為自動化模式**，請依「自動化模式的輸出包裝」輸出 `<edits>`（只列要改的句子，不要輸出全文）、`<response_table>`、`<summary_json>`、`<plain_summary>` 四個標籤。\n"
code1 = read(Path(__file__).resolve().parent / "code-node-1-decide.py")
code2 = read(Path(__file__).resolve().parent / "code-node-2-parse-revision.py")
code0 = read(Path(__file__).resolve().parent / "code-node-0-preprocess.py")
code3 = read(Path(__file__).resolve().parent / "code-node-3-format-guard.py")
code4 = read(Path(__file__).resolve().parent / "code-node-4-report.py")
code5 = read(Path(__file__).resolve().parent / "code-node-5-journal-rules.py")
code6 = read(Path(__file__).resolve().parent / "code-node-6-changes-digest.py")
code7 = read(Path(__file__).resolve().parent / "code-node-7-proposal-checks.py")
code8 = read(Path(__file__).resolve().parent / "code-node-8-mode.py")
code9 = read(Path(__file__).resolve().parent / "code-node-9-course-merge.py")
code12 = read(Path(__file__).resolve().parent / "code-node-12-align-merge.py")
code10 = read(Path(__file__).resolve().parent / "code-node-10-lit-queries.py")
code11 = read(Path(__file__).resolve().parent / "code-node-11-lit-merge.py")
PROP = ROOT / "proposal"


def system_prompt(md_name):
    t = read(ROOT / "reviewers" / md_name)
    s = t.split("## System prompt", 1)[1].split("## User prompt 模板", 1)[0]
    s = s.strip().rstrip("-").strip()
    return s


# ---------- 節點 ID ----------
START, LOOP, LSTART = "1730000000001", "1730000000002", "1730000000003"
IF0, EXT, AGG, PRE = "1730000000201", "1730000000202", "1730000000203", "1730000000204"
IF2, EXT2, LLMJ, JR = "1730000000301", "1730000000302", "1730000000303", "1730000000304"
FMT, GUARD, REP = "1730000000305", "1730000000306", "1730000000307"
DIG, FINAL = "1730000000308", "1730000000309"
GEM, GPT, C1, ASA, IFE, REV, C2, ASB, END = [
    "17300000001%02d" % i for i in range(1, 10)
]
IFC, EXTC, CTX, PCHK, PCHK2 = ["17300000003%02d" % i for i in range(10, 15)]  # 計畫書模式新增節點
CTXA, IFP, EXTP = ["17300000003%02d" % i for i in range(15, 18)]  # 授課計畫書合併、前期計畫書
LITQ, LITP, LH1, LH2, LH3, LITM = ["17300000004%02d" % i for i in range(1, 7)]  # 文獻背景檢索（Google Scholar／SerpApi）
IFA, ALN, ALM = ["17300000005%02d" % i for i in range(1, 4)]  # 對齊診斷（計畫書預審）
LV = LOOP  # 循環變數的 selector 前綴

sys_gem = "{{#%s.sys_a#}}" % CTX
sys_gpt = "{{#%s.sys_b#}}" % CTX

USER_REVIEW = """請審查以下稿件。

【輪次】{{#%(L)s.round_no#}}
【論文類型】{{#%(S)s.paper_type#}}
【規範來源】{{#%(JR)s.journal_source#}}
【目標期刊與特定規範】
{{#%(JR)s.journal_rules#}}

{{#%(PC)s.extra_context#}}

{{#%(LIT)s.lit_context#}}

【作者已知待補事項（空白表示無）】
{{#%(S)s.known_gaps#}}

【上一輪你提出的意見清單（第 1 輪為空）】
{{#%(L)s.%(prior)s#}}

【作者對上一輪意見的回應摘要（第 1 輪為空）】
{{#%(L)s.response_summary#}}

【稿件全文】
<manuscript>
{{#%(L)s.current_manuscript#}}
</manuscript>

請依 system prompt 的規則，輸出符合 schema 的 JSON。"""

USER_REVISE = """【輪次】{{#%(L)s.round_no#}}
【論文類型】{{#%(S)s.paper_type#}}
【審查標準】
{{#%(CTX)s.rubric#}}

【規範來源】{{#%(JR)s.journal_source#}}
【目標期刊與特定規範】
{{#%(JR)s.journal_rules#}}

{{#%(PC)s.extra_context#}}

【作者已知待補事項（空白表示無）】
{{#%(S)s.known_gaps#}}

【兩位審查者的未解決意見（JSON）】
{{#%(C1)s.issues_for_reviser#}}

【當前稿件】
{{#%(L)s.current_manuscript#}}""" % {"L": LOOP, "S": START, "C1": C1, "JR": CTX, "CTX": CTX, "PC": PCHK}


# ---------- 節點建構 ----------
def pos(x, y):
    return {"x": x, "y": y}


def base_node(nid, data, x, y, inner=False, w=244, h=90):
    n = {
        "id": nid,
        "type": "custom",
        "data": data,
        "position": pos(x, y),
        "positionAbsolute": pos(x, y),
        "sourcePosition": "right",
        "targetPosition": "left",
        "selected": False,
        "width": w,
        "height": h,
    }
    if inner:
        n["parentId"] = LOOP
        n["extent"] = "parent"
        n["zIndex"] = 1002
        data["isInLoop"] = True
        data["loop_id"] = LOOP
    return n


def llm_node(nid, title, provider, model, system, user, x, y, max_tokens=None, inner=True, temperature=0.3):
    params = {"temperature": temperature}
    if max_tokens:
        params["max_tokens"] = max_tokens
    data = {
        "type": "llm",
        "title": title,
        "desc": "",
        "selected": False,
        "context": {"enabled": False, "variable_selector": []},
        "model": {"provider": provider, "name": model, "mode": "chat", "completion_params": params},
        "prompt_template": [
            {"id": uid(), "role": "system", "text": system},
            {"id": uid(), "role": "user", "text": user},
        ],
        "variables": [],
        "vision": {"enabled": False},
        # 暫時性錯誤（如 503 過載、連線逾時）自動重試，避免整個循環中斷
        "retry_config": {"retry_enabled": True, "max_retries": 3, "retry_interval": 3000},
    }
    return base_node(nid, data, x, y, inner=inner, h=98)


def code_node(nid, title, code, inputs, outputs, x, y, inner=True):
    data = {
        "type": "code",
        "title": title,
        "desc": "",
        "selected": False,
        "code": code,
        "code_language": "python3",
        "variables": [{"variable": k, "value_selector": v} for k, v in inputs],
        "outputs": {k: {"type": t, "children": None} for k, t in outputs},
    }
    return base_node(nid, data, x, y, inner=inner, h=54)


def assigner(nid, title, items, x, y):
    its = []
    for target, kind, val in items:
        if kind == "var":
            its.append({"variable_selector": [LOOP, target], "input_type": "variable", "operation": "over-write", "value": val})
        else:  # 數字遞增
            its.append({"variable_selector": [LOOP, target], "input_type": "constant", "operation": "+=", "value": val})
    data = {"type": "assigner", "title": title, "desc": "", "selected": False, "version": "2", "items": its}
    return base_node(nid, data, x, y, inner=True, h=54)


nodes = []

# 開始
start_vars = [
    {"variable": "manuscript", "label": "貼上稿件全文（含參考文獻）。也可改為上傳檔案", "type": "paragraph", "required": False, "max_length": 200000, "options": []},
    {"variable": "file", "label": "或上傳稿件檔案（PDF／Word）。★教學實踐研究計畫書預審：請把「計畫書本文」上傳或貼在這裡；若上方已貼上文字，則以貼上的文字為準", "type": "file", "required": False, "max_length": 1,
     "options": [], "allowed_file_types": ["document"], "allowed_file_extensions": [".pdf", ".docx", ".doc"],
     "allowed_file_upload_methods": ["local_file", "remote_url"]},
    {"variable": "paper_type", "label": "論文類型（teaching_practice_proposal=教學實踐研究計畫書預審、quantitative=量化、qualitative=質性、theoretical=理論／政策分析、teaching_practice=教學實踐、mixed=混合方法、content_analysis=內容分析、historical=歷史途徑、institutional_comparison=制度比較、cross_cultural=跨文化研究、literature_linguistics=文學與語言研究）",
     "type": "select", "required": True, "max_length": 64,
     "options": ["quantitative", "qualitative", "theoretical", "teaching_practice", "mixed", "content_analysis", "historical", "institutional_comparison", "cross_cultural", "literature_linguistics", "teaching_practice_proposal"]},
    {"variable": "format_source", "label": "期刊格式來源（選「上傳期刊格式檔」時，請在下一欄上傳稿約或格式說明）", "type": "select", "required": True, "max_length": 32,
     "options": ["內建預設格式", "上傳期刊格式檔"], "default": "內建預設格式"},
    {"variable": "journal_file", "label": "期刊格式檔（稿約／投稿須知，PDF 或 Word；僅在上一欄選「上傳期刊格式檔」時使用）", "type": "file", "required": False, "max_length": 1,
     "options": [], "allowed_file_types": ["document"], "allowed_file_extensions": [".pdf", ".docx", ".doc"],
     "allowed_file_upload_methods": ["local_file", "remote_url"]},
    {"variable": "known_gaps", "label": "作者已知待補事項（選填；每行一項。例如：質性分析程序細節、補充實證文獻。列在此處的缺口，審查者仍會提出，但嚴重度最高為 minor，不會阻擋通過）",
     "type": "paragraph", "required": False, "max_length": 3000, "options": []},
    {"variable": "course_plan", "label": "【計畫書預審・僅限「授課計畫書」，不是計畫書本文】貼上授課計畫書全文（選填；沒有可留空）", "type": "paragraph", "required": False, "max_length": 100000, "options": []},
    {"variable": "course_plan_file", "label": "【計畫書預審・僅限「授課計畫書」，不是計畫書本文】或上傳授課計畫書（PDF／Word；選填）", "type": "file", "required": False, "max_length": 1,
     "options": [], "allowed_file_types": ["document"], "allowed_file_extensions": [".pdf", ".docx", ".doc"],
     "allowed_file_upload_methods": ["local_file", "remote_url"]},
    {"variable": "prior_plan", "label": "【計畫書預審・選填】貼上「前期／相關計畫書」全文（例如本計畫所延續的上一年度計畫書；用來核對「延續與差異對照表」是否與原文相符。沒有可留空）", "type": "paragraph", "required": False, "max_length": 100000, "options": []},
    {"variable": "prior_plan_file", "label": "【計畫書預審・選填】或上傳前期／相關計畫書（PDF／Word）", "type": "file", "required": False, "max_length": 1,
     "options": [], "allowed_file_types": ["document"], "allowed_file_extensions": [".pdf", ".docx", ".doc"],
     "allowed_file_upload_methods": ["local_file", "remote_url"]},
    {"variable": "local_checks", "label": "【計畫書預審】本機檢查結果（選填；貼上 proposal_checks.py 的報告，內含精確頁數與歷年計畫書重複比對）", "type": "paragraph", "required": False, "max_length": 60000, "options": []},
    {"variable": "max_rounds", "label": "輪數上限", "type": "number", "required": True, "max_length": 48, "options": [], "default": 4},
    {"variable": "required_pass_rounds", "label": "需連續通過輪數", "type": "number", "required": True, "max_length": 48, "options": [], "default": 2},
]
nodes.append({
    "id": START, "type": "custom", "position": pos(40, 260), "positionAbsolute": pos(40, 260),
    "sourcePosition": "right", "targetPosition": "left", "selected": False, "width": 244, "height": 90,
    "data": {"type": "start", "title": "開始", "desc": "", "selected": False, "variables": start_vars},
})

# 循環變數
def lv(label, vtype, value, selector=False):
    return {"id": uid(), "label": label, "var_type": vtype,
            "value_type": "variable" if selector else "constant", "value": value}

loop_vars = [
    lv("current_manuscript", "string", [PRE, "manuscript_ready"], True),
    lv("prev_manuscript", "string", [PRE, "manuscript_ready"], True),
    lv("orig_manuscript", "string", [PRE, "manuscript_ready"], True),
    lv("round_no", "number", 1),
    lv("decision", "string", "continue"),
    lv("prev_total", "number", 0),
    lv("prev_major", "number", 0),
    lv("pass_streak", "number", 0),
    lv("stall_streak", "number", 0),
    lv("fail_streak", "number", 0),
    lv("history", "string", ""),
    lv("prior_gemini", "string", "[]"),
    lv("prior_gpt", "string", "[]"),
    lv("response_summary", "string", ""),
    lv("last_response_table", "string", ""),
    lv("last_marked", "string", ""),
    lv("last_flags", "string", ""),
    lv("last_reason", "string", ""),
]
nodes.append({
    "id": LOOP, "type": "custom", "position": pos(2300, 40), "positionAbsolute": pos(2300, 40),
    "sourcePosition": "right", "targetPosition": "left", "selected": False, "width": 2000, "height": 640,
    "zIndex": 1,
    "data": {
        "type": "loop", "title": "審查與修訂循環", "desc": "", "selected": False,
        "start_node_id": LSTART, "loop_count": 8, "logical_operator": "and",
        "error_handle_mode": "terminated", "width": 2000, "height": 640,
        "break_conditions": [{
            "id": uid(), "varType": "string", "variable_selector": [LOOP, "decision"],
            "comparison_operator": "is not", "value": "continue",
        }],
        "loop_variables": loop_vars,
    },
})
nodes.append({
    "id": LSTART, "type": "custom-loop-start", "parentId": LOOP, "draggable": False, "selectable": False,
    "zIndex": 1002, "position": pos(40, 280), "positionAbsolute": pos(380, 320),
    "sourcePosition": "right", "targetPosition": "left", "width": 44, "height": 48,
    "data": {"type": "loop-start", "title": "", "desc": "", "isInLoop": True, "loop_id": LOOP, "selected": False},
})

# ① ② 審查者（模型名稱為占位，匯入後請在節點內重新選擇）
nodes.append(llm_node(GEM, "① Gemini 審查（方法與論證）", "langgenius/gemini/google", "gemini-3.1-pro-preview",
                      sys_gem, USER_REVIEW % {"L": LOOP, "S": START, "prior": "prior_gemini", "JR": CTX, "PC": PCHK, "LIT": LITM}, 160, 60, max_tokens=32000))
nodes.append(llm_node(GPT, "② GPT 審查（文獻與格式）", "langgenius/openai/openai", "gpt-5",
                      sys_gpt, USER_REVIEW % {"L": LOOP, "S": START, "prior": "prior_gpt", "JR": CTX, "PC": PCHK, "LIT": LITM}, 160, 460, max_tokens=32000))

# ③ 程式碼：解析並判斷
nodes.append(code_node(C1, "③ 解析審查並判斷停止", code1, [
    ("gemini_raw", [GEM, "text"]), ("gpt_raw", [GPT, "text"]),
    ("paper_type", [START, "paper_type"]), ("round_no", [LOOP, "round_no"]),
    ("max_rounds", [START, "max_rounds"]), ("required_pass_rounds", [START, "required_pass_rounds"]),
    ("prev_total", [LOOP, "prev_total"]), ("prev_major", [LOOP, "prev_major"]),
    ("pass_streak", [LOOP, "pass_streak"]), ("stall_streak", [LOOP, "stall_streak"]),
    ("history", [LOOP, "history"]), ("known_gaps", [START, "known_gaps"]),
    ("manuscript", [LOOP, "current_manuscript"]), ("orig_manuscript", [LOOP, "orig_manuscript"]), ("program_checks", [PCHK, "extra_context"]), ("lit_context", [LITM, "lit_context"]),
], [("decision", "string"), ("reason", "string"), ("round_total", "number"), ("major_count", "number"),
    ("pass_streak", "number"), ("stall_streak", "number"), ("prior_gemini", "string"), ("prior_gpt", "string"),
    ("issues_for_reviser", "string"), ("history_next", "string")], 520, 260))

# ④ 賦值 A
nodes.append(assigner(ASA, "④ 更新判斷結果", [
    ("decision", "var", [C1, "decision"]), ("last_reason", "var", [C1, "reason"]),
    ("pass_streak", "var", [C1, "pass_streak"]), ("stall_streak", "var", [C1, "stall_streak"]),
    ("prev_total", "var", [C1, "round_total"]), ("prev_major", "var", [C1, "major_count"]),
    ("prior_gemini", "var", [C1, "prior_gemini"]), ("prior_gpt", "var", [C1, "prior_gpt"]),
    ("history", "var", [C1, "history_next"]),
], 840, 260))

# ⑤ 條件分支
nodes.append(base_node(IFE, {
    "type": "if-else", "title": "⑤ 是否繼續修稿", "desc": "", "selected": False,
    "cases": [{
        "id": "true", "case_id": "true", "logical_operator": "and",
        "conditions": [{"id": uid(), "varType": "string", "variable_selector": [LOOP, "decision"],
                        "comparison_operator": "is", "value": "continue"}],
    }],
}, 1130, 260, inner=True, h=126))

# ⑥ Claude 修稿
nodes.append(llm_node(REV, "⑥ Claude 修稿", "langgenius/anthropic/anthropic", "claude-sonnet-5",
                      "{{#%s.reviser_system#}}" % CTX, USER_REVISE, 1420, 200, max_tokens=16000))

# ⑦ 程式碼：解析修訂
nodes.append(code_node(C2, "⑦ 解析修訂並檢查鎖定區塊", code2, [
    ("reviser_raw", [REV, "text"]), ("old_manuscript", [LOOP, "current_manuscript"]), ("fail_streak", [LOOP, "fail_streak"]),
], [("ok", "string"), ("fail_streak_next", "number"), ("override_decision", "string"), ("override_reason", "string"), ("manuscript_clean", "string"),
    ("manuscript_marked", "string"), ("manuscript_prev", "string"), ("response_table", "string"), ("dispositions_summary", "string"),
    ("plain_summary", "string"), ("flags", "string")], 1710, 200))

# ⑧ 賦值 B
nodes.append(assigner(ASB, "⑧ 更新稿件與輪次", [
    ("prev_manuscript", "var", [C2, "manuscript_prev"]), ("current_manuscript", "var", [C2, "manuscript_clean"]), ("last_marked", "var", [C2, "manuscript_marked"]),
    ("last_response_table", "var", [C2, "response_table"]), ("response_summary", "var", [C2, "dispositions_summary"]),
    ("last_flags", "var", [C2, "flags"]), ("decision", "var", [C2, "override_decision"]), ("last_reason", "var", [C2, "override_reason"]),
    ("fail_streak", "var", [C2, "fail_streak_next"]),
    ("round_no", "const", 1),
], 1710, 400))

# 結束
end_outputs = [  # (輸出名稱, 來源節點, 來源變數)
    ("final_manuscript", GUARD, "final_manuscript"), ("format_report", GUARD, "report"),
    ("final_opinion", FINAL, "text"), ("report_json", REP, "report_json"), ("report_html", REP, "report_html"), ("change_summary", REP, "change_summary"),
    ("decision", LOOP, "decision"), ("last_reason", LOOP, "last_reason"), ("history", LOOP, "history"),
    ("last_flags", LOOP, "last_flags"), ("last_response_table", LOOP, "last_response_table"),
    ("unresolved_a", LOOP, "prior_gemini"), ("unresolved_b", LOOP, "prior_gpt"),
    ("preprocess_report", PRE, "report"), ("journal_source", CTX, "journal_source"), ("layout_spec", CTX, "layout_spec"), ("changes_digest", DIG, "changes_digest"),
    ("literature_report", LITM, "lit_report"), ("literature_context", LITM, "lit_context"),
    ("alignment_report", ALM, "align_report"), ("alignment_status", ALM, "align_status"),
]
nodes.append({
    "id": END, "type": "custom", "position": pos(6400, 260), "positionAbsolute": pos(6400, 260),
    "sourcePosition": "right", "targetPosition": "left", "selected": False, "width": 244, "height": 90,
    "data": {"type": "end", "title": "結束", "desc": "", "selected": False,
             "outputs": [{"variable": a, "value_selector": [n, v]} for a, n, v in end_outputs]},
})

# ---------- 連線 ----------

# ---------- 前段：貼上或上傳 → 提取 → 合併 → 前處理 ----------
nodes.append(base_node(IF0, {
    "type": "if-else", "title": "有貼上文字嗎？", "desc": "有貼上文字就直接使用；沒有則使用上傳的檔案", "selected": False,
    "cases": [{"id": "true", "case_id": "true", "logical_operator": "and",
               "conditions": [{"id": uid(), "varType": "string", "variable_selector": [START, "manuscript"],
                               "comparison_operator": "not empty", "value": ""}]}],
}, 340, 260, inner=False, h=126))
nodes.append(base_node(EXT, {
    "type": "document-extractor", "title": "文件提取器（PDF／Word）", "desc": "", "selected": False,
    "is_array_file": False, "variable_selector": [START, "file"],
}, 640, 400, inner=False))
nodes.append(base_node(AGG, {
    "type": "variable-aggregator", "title": "合併稿件來源", "desc": "", "selected": False,
    "output_type": "string", "variables": [[START, "manuscript"], [EXT, "text"]],
    "advanced_settings": {"group_enabled": False, "groups": []},
}, 940, 260, inner=False))
nodes.append(code_node(PRE, "稿件前處理（清理、保護表格與倫理段落）", code0,
                       [("pasted_text", [START, "manuscript"]), ("extracted_text", [EXT, "text"]), ("raw_text", [AGG, "output"])],
                       [("manuscript_ready", "string"), ("report", "string"), ("kb_query", "string"), ("head_text", "string")], 1040, 100, inner=False))


# ---------- 期刊規範：選擇上傳（解析）或內建預設 ----------
def fmt_system(md_name, marker_from, marker_to):
    t = read(ROOT / "reviewers" / md_name)
    return t.split(marker_from, 1)[1].split(marker_to, 1)[0].strip().rstrip("-").strip()

SYS_JPARSE = fmt_system("journal-format-parser.md", "## System prompt", "## User prompt 模板")
SYS_FMT = fmt_system("format-adjuster.md", "## System prompt", "## User prompt 模板")

nodes.append(base_node(IF2, {
    "type": "if-else", "title": "上傳了期刊格式檔？", "desc": "依開始表單的「期刊格式來源」決定", "selected": False,
    "cases": [{"id": "true", "case_id": "true", "logical_operator": "and",
               "conditions": [{"id": uid(), "varType": "string", "variable_selector": [START, "format_source"],
                               "comparison_operator": "is", "value": "上傳期刊格式檔"}]}],
}, 1340, 260, inner=False, h=126))
nodes.append(base_node(EXT2, {
    "type": "document-extractor", "title": "文件提取器（期刊格式檔）", "desc": "", "selected": False,
    "is_array_file": False, "variable_selector": [START, "journal_file"],
}, 1640, 420, inner=False))
nodes.append(llm_node(LLMJ, "期刊格式解析", "langgenius/gemini/google", "gemini-3-flash-preview",
                      SYS_JPARSE, "【稿約或格式文件的擷取文字】\n{{#%s.text#}}" % EXT2, 1940, 420,
                      max_tokens=16000, inner=False, temperature=0.1))
nodes.append(code_node(JR, "決定採用的期刊規範", code5,
                       [("parsed_rules", [LLMJ, "text"]), ("default_rules", ["env", "DEFAULT_FORMAT_RULES"]),
                        ("format_source", [START, "format_source"])],
                       [("journal_rules", "string"), ("journal_source", "string"), ("layout_spec", "string")], 2240, 260, inner=False))

# ---------- 循環之後：格式整理 → 防護 → 報告 ----------
FMT_USER = ("【規範來源】{{#%(JR)s.journal_source#}}\n【目標期刊與特定規範】\n{{#%(JR)s.journal_rules#}}\n\n"
            "【待整理的稿件】\n{{#%(L)s.current_manuscript#}}") % {"JR": CTX, "L": LOOP}
nodes.append(llm_node(FMT, "格式整理（只改版面，不改內容）", "langgenius/gemini/google", "gemini-3-flash-preview",
                      SYS_FMT, FMT_USER, 4900, 260, max_tokens=32000, inner=False, temperature=0.1))
nodes.append(code_node(GUARD, "格式整理防護（內容不得被更動）", code3,
                       [("before", [LOOP, "current_manuscript"]), ("raw", [FMT, "text"])],
                       [("ok", "string"), ("final_manuscript", "string"), ("report", "string")], 5200, 260, inner=False))
nodes.append(code_node(REP, "產生報告資料（最後一輪修訂標紅）", code4,
                       [("prev_manuscript", [LOOP, "prev_manuscript"]), ("final_manuscript", [GUARD, "final_manuscript"]),
                        ("history", [LOOP, "history"]), ("decision", [LOOP, "decision"]), ("last_reason", [LOOP, "last_reason"]),
                        ("last_flags", [LOOP, "last_flags"]), ("last_response_table", [LOOP, "last_response_table"]),
                        ("prior_a", [LOOP, "prior_gemini"]), ("prior_b", [LOOP, "prior_gpt"]),
                        ("preprocess_report", [PRE, "report"]), ("format_report", [GUARD, "report"]),
                        ("journal_source", [CTX, "journal_source"]), ("paper_type", [START, "paper_type"]),
                        ("final_opinion", [FINAL, "text"])],
                       [("report_json", "string"), ("report_html", "string"), ("change_summary", "string")], 6100, 260, inner=False))


# ---------- 終審意見 ----------
SYS_FINAL = fmt_system("final-review.md", "## System prompt", "## User prompt 模板")
FINAL_USER = ("{{#%(DIG)s.verdict_hint#}}\n\n【論文類型】{{#%(S)s.paper_type#}}\n【規範來源】{{#%(JR)s.journal_source#}}\n"
              "【結束原因】{{#%(L)s.decision#}}（{{#%(L)s.last_reason#}}）\n\n"
              "【逐輪審查紀錄】\n{{#%(L)s.history#}}\n\n【最後仍未解決的意見】\n{{#%(DIG)s.unresolved#}}\n\n"
              "【最後一輪修訂對照表】\n{{#%(L)s.last_response_table#}}\n\n【作者已知待補事項】\n{{#%(S)s.known_gaps#}}\n\n"
              "【原稿與最終稿的句子比對清單（累計所有輪次）】\n{{#%(DIG)s.changes_digest#}}\n\n"
              "【最終稿的程式檢查結果與授課計畫書（期刊稿件為空）】\n{{#%(PC2)s.extra_context#}}\n\n"
              "【整合調修後的稿件全文】\n{{#%(G)s.final_manuscript#}}") % {"S": START, "JR": CTX, "L": LOOP, "DIG": DIG, "G": GUARD, "PC2": PCHK2}
nodes.append(code_node(DIG, "修訂前後句子比對（供終審）", code6,
                       [("prev_manuscript", [LOOP, "orig_manuscript"]), ("final_manuscript", [GUARD, "final_manuscript"]),
                        ("unresolved_a", [LOOP, "prior_gemini"]), ("unresolved_b", [LOOP, "prior_gpt"]), ("paper_type", [START, "paper_type"])],
                       [("changes_digest", "string"), ("unresolved", "string"), ("verdict_hint", "string")], 5500, 260, inner=False))
nodes.append(llm_node(FINAL, "終審意見（Claude）", "langgenius/anthropic/anthropic", "claude-sonnet-5",
                      "{{#%s.final_system#}}" % CTX, FINAL_USER, 5800, 260, max_tokens=16000, inner=False, temperature=0.2))

# ---------- 計畫書模式：授課計畫書取得、模式選擇、程式檢查 ----------
nodes.append(base_node(IFC, {
    "type": "if-else", "title": "上傳了授課計畫書？", "desc": "僅計畫書預審使用；沒有上傳則略過提取", "selected": False,
    "cases": [{"id": "true", "case_id": "true", "logical_operator": "and",
               "conditions": [{"id": uid(), "varType": "file", "variable_selector": [START, "course_plan_file"],
                               "comparison_operator": "exists", "value": ""}]}],
}, 2540, 260, inner=False, h=126))
nodes.append(base_node(EXTC, {
    "type": "document-extractor", "title": "文件提取器（授課計畫書）", "desc": "", "selected": False,
    "is_array_file": False, "variable_selector": [START, "course_plan_file"],
}, 2840, 420, inner=False))
nodes.append(code_node(CTXA, "合併授課計畫書文字", code9, [("course_pasted", [START, "course_plan"]), ("course_extracted", [EXTC, "text"])],
                       [("course_text", "string")], 2840, 100, inner=False))
nodes.append(base_node(IFP, {
    "type": "if-else", "title": "上傳了前期計畫書？", "desc": "僅計畫書預審使用；沒有上傳則略過提取", "selected": False,
    "cases": [{"id": "true", "case_id": "true", "logical_operator": "and",
               "conditions": [{"id": uid(), "varType": "file", "variable_selector": [START, "prior_plan_file"],
                               "comparison_operator": "exists", "value": ""}]}],
}, 3140, 100, inner=False, h=126))
nodes.append(base_node(EXTP, {
    "type": "document-extractor", "title": "文件提取器（前期計畫書）", "desc": "", "selected": False,
    "is_array_file": False, "variable_selector": [START, "prior_plan_file"],
}, 3440, 420, inner=False))
nodes.append(code_node(CTX, "決定審查模式與取得授課計畫書", code8, [
    ("paper_type", [START, "paper_type"]), ("course_text_in", [CTXA, "course_text"]), ("prior_pasted", [START, "prior_plan"]), ("prior_extracted", [EXTP, "text"]),
    ("journal_rules", [JR, "journal_rules"]), ("journal_source", [JR, "journal_source"]), ("layout_spec", [JR, "layout_spec"]),
    ("format_source", [START, "format_source"]),
    ("rubric_j", ["env", "RUBRIC"]), ("rubric_p", ["env", "RUBRIC_PROPOSAL"]),
    ("sys_a_j", ["env", "SYS_A_JOURNAL"]), ("sys_a_p", ["env", "SYS_A_PROPOSAL"]),
    ("sys_b_j", ["env", "SYS_B_JOURNAL"]), ("sys_b_p", ["env", "SYS_B_PROPOSAL"]),
    ("skill_j", ["env", "REVISER_SKILL"]), ("skill_p", ["env", "REVISER_SKILL_PROPOSAL"]),
    ("final_j", ["env", "SYS_FINAL_JOURNAL"]), ("final_p", ["env", "SYS_FINAL_PROPOSAL"]),
    ("default_rules_p", ["env", "DEFAULT_FORMAT_PROPOSAL"]), ("layout_p", ["env", "LAYOUT_PROPOSAL"]),
], [("course_text", "string"), ("prior_text", "string"), ("rubric", "string"), ("sys_a", "string"), ("sys_b", "string"), ("reviser_system", "string"),
    ("final_system", "string"), ("journal_rules", "string"), ("journal_source", "string"), ("layout_spec", "string")], 3740, 260, inner=False))
nodes.append(code_node(PCHK, "程式檢查（計畫書預審；期刊稿件為空）", code7, [
    ("proposal_text", [LOOP, "current_manuscript"]), ("course_text", [CTX, "course_text"]), ("paper_type", [START, "paper_type"]),
    ("local_checks", [START, "local_checks"]), ("prior_text", [CTX, "prior_text"]), ("align_report", [ALM, "align_report"]),
], [("extra_context", "string")], 40, 120))
nodes.append(code_node(PCHK2, "程式檢查（最終稿，供終審）", code7, [
    ("proposal_text", [GUARD, "final_manuscript"]), ("course_text", [CTX, "course_text"]), ("paper_type", [START, "paper_type"]),
    ("local_checks", [START, "local_checks"]), ("prior_text", [CTX, "prior_text"]),
], [("extra_context", "string")], 5350, 260, inner=False))

# ---------- 文獻背景檢索（Google Scholar via SerpApi；失敗不中斷） ----------
SYS_LITQ = ("你是學術文獻檢索助理。依使用者提供的稿件開頭，產生 3 組 Google Scholar 檢索字串，用來找「與本研究最相關的既有研究」。\n"
            "基本規則：每組 2–6 個關鍵詞，以空格分隔，不要用句子，不要使用任何引號；不得捏造不存在的專有名詞。"
            "第 1 組：研究的核心概念組合（繁體中文），必須同時包含稿件最具辨識度的兩個核心概念；"
            "第 2 組：研究對象、教學場域或方法（繁體中文），例如對象加方法加成效；"
            "第 3 組：第 1 組核心概念的英文學術用語（英文），2–5 個詞。\n"
            "避免過度泛化：不要單獨使用「教學」「人工智慧」「評量」「成效」「學習」這類大範圍詞；"
            "AI 或某工具若只是輔助手段、不是研究主題，不要放進第 1、3 組，最多放進第 2 組一個詞。"
            "先判斷稿件真正的研究主題是什麼，再取詞；不要被稿件開頭的行政資料、課程名稱或工具名稱帶偏。\n"
            "主題用語對照（遇到下列主題時，用左欄的繁中詞與右欄的英文學術詞）：\n"
            "· 認知作戰／認知域作戰 → cognitive warfare, cognitive domain operations；資訊戰 → information warfare；影響力作戰 → influence operations；灰色地帶 → gray zone\n"
            "· 假訊息／不實訊息／資訊操弄 → disinformation, misinformation, information manipulation；事實查核 → fact-checking；心理接種 → inoculation, prebunking\n"
            "· 孫子兵法／兵學／戰略思想 → Sun Tzu, Art of War, strategic thought；詭道、用間 → deception, intelligence；古典戰略思想的當代轉譯 → classical strategic thought, contemporary application\n"
            "· 媒體識讀／資訊素養 → media literacy, information literacy；批判思考 → critical thinking；數位公民 → digital citizenship\n"
            "· 軍校／軍事教育 → military academy, cadets, professional military education；兵棋推演 → wargaming, war game in education；案例教學 → case-based learning\n"
            "· 兩岸關係／大陸問題／中共研究 → cross-strait relations, Taiwan Strait, China studies, CCP political system；中國大陸研究教學 → teaching China studies\n"
            "· 教學實踐研究／教學研究 → scholarship of teaching and learning (SoTL)；行動研究 → action research；學習遷移 → transfer of learning；鷹架 → scaffolding；認知彈性 → cognitive flexibility\n"
            "· 憲政民主／半總統制 → constitutional democracy, semi-presidentialism；地方自治 → local self-government；社區總體營造／社區發展 → community building, community development, Taiwan\n"
            "依論文類型調整：teaching_practice_proposal（教學實踐研究計畫書）→ 檢索重點是「研究主題的核心概念」與「教學場域加方法的既有研究」，"
            "第 1 組取研究動機與主題的核心概念，第 2 組取軍校或課程對象加教學方法加成效評估，第 3 組用英文核心概念；"
            "cross_cultural（跨文化研究）→ 第 1 組含所比較的文化／群體與研究主題，第 2 組含跨文化理論或測量等值、適應、溝通等概念，第 3 組用 cross-cultural／intercultural／measurement invariance 等英文術語；"
            "literature_linguistics（文學與語言研究）→ 第 1 組含作家／作品／語言現象與研究主題，第 2 組含所用理論或分析取徑（如敘事學、話語分析、語用學、語料庫、翻譯研究），第 3 組用對應英文術語；其他類型照一般規則。\n"
            "只輸出 JSON，格式：{\"queries\":[\"...\",\"...\",\"...\"]}，不要任何其他文字。")
nodes.append(llm_node(LITQ, "文獻檢索：產生查詢字串", "langgenius/gemini/google", "gemini-3-flash-preview", SYS_LITQ,
                      "【論文類型】{{#%s.paper_type#}}\n\n【稿件標題與關鍵詞】\n{{#%s.kb_query#}}\n\n【稿件開頭】\n{{#%s.head_text#}}" % (START, PRE, PRE),
                      3900, 560, max_tokens=2000, inner=False, temperature=0.1))
nodes.append(code_node(LITP, "文獻檢索：解析查詢", code10, [("llm_text", [LITQ, "text"]), ("fallback", [PRE, "kb_query"])],
                       [("q1", "string"), ("q2", "string"), ("q3", "string"), ("all", "string")], 4200, 560, inner=False))


def http_node(nid, title, qvar, y, lr="lang_zh-TW"):
    data = {
        "type": "http-request", "title": title, "desc": "SerpApi google_scholar；失敗不中斷流程", "selected": False,
        "method": "get", "url": "https://serpapi.com/search.json",
        "authorization": {"type": "no-auth", "config": None}, "headers": "",
        "params": "engine:google_scholar\nq:{{#%s.%s#}}\nhl:zh-TW\nlr:%s\nnum:8\napi_key:{{#env.SERPAPI_KEY#}}" % (LITP, qvar, lr),
        "body": {"type": "none", "data": []},
        "timeout": {"max_connect_timeout": 10, "max_read_timeout": 40, "max_write_timeout": 10},
        "retry_config": {"retry_enabled": True, "max_retries": 2, "retry_interval": 2000},
        "error_strategy": "default-value",
        "default_value": [{"key": "body", "type": "string", "value": ""}, {"key": "status_code", "type": "number", "value": 0},
                          {"key": "headers", "type": "object", "value": {}}],
    }
    return base_node(nid, data, 4500, y, inner=False)


nodes.append(http_node(LH1, "文獻檢索：Google Scholar 查詢1", "q1", 480))
nodes.append(http_node(LH2, "文獻檢索：Google Scholar 查詢2", "q2", 600))
nodes.append(http_node(LH3, "文獻檢索：Google Scholar 查詢3", "q3", 720, lr="lang_en"))
nodes.append(code_node(LITM, "文獻檢索：整理結果給審查者", code11,
                       [("b1", [LH1, "body"]), ("b2", [LH2, "body"]), ("b3", [LH3, "body"]),
                        ("queries", [LITP, "all"])],
                       [("lit_context", "string"), ("lit_report", "string")], 4800, 600, inner=False))


# ---------- 對齊診斷（計畫書預審；循環開始前執行一次；非計畫書直接略過） ----------
SYS_ALIGN = read(PROP / "reviewers" / "alignment-diagnosis.md").split("## System prompt", 1)[1].split("## User prompt 模板", 1)[0].strip().rstrip("-").strip()
ALIGN_USER = ("【論文類型】{{#%(S)s.paper_type#}}\n\n【作者已知待補事項（空白表示無；與此相關的缺口標註「作者已知」）】\n{{#%(S)s.known_gaps#}}\n\n"
              "【授課計畫書（可空；僅用來對照教學活動與週次，不是診斷對象）】\n{{#%(CTX)s.course_text#}}\n\n"
              "【文獻背景檢索結果】\n{{#%(LIT)s.lit_context#}}\n\n"
              "【計畫書全文】\n<proposal>\n{{#%(PRE)s.manuscript_ready#}}\n</proposal>") % {"S": START, "CTX": CTX, "LIT": LITM, "PRE": PRE}
nodes.append(base_node(IFA, {
    "type": "if-else", "title": "是計畫書預審嗎？", "desc": "是才執行對齊診斷；期刊稿件直接略過", "selected": False,
    "cases": [{"id": "true", "case_id": "true", "logical_operator": "and",
               "conditions": [{"id": uid(), "varType": "string", "variable_selector": [START, "paper_type"],
                               "comparison_operator": "is", "value": "teaching_practice_proposal"}]}],
}, 5100, 600, inner=False, h=126))
nodes.append(llm_node(ALN, "對齊診斷（理論—項次對應）", "langgenius/anthropic/anthropic", "claude-sonnet-5",
                      SYS_ALIGN, ALIGN_USER, 5400, 720, max_tokens=8000, inner=False, temperature=0.2))
nodes.append(code_node(ALM, "合併對齊診斷", code12, [("llm_text", [ALN, "text"]), ("paper_type", [START, "paper_type"])],
                       [("align_report", "string"), ("align_status", "string")], 5700, 600, inner=False))

type_of = {n["id"]: n["data"]["type"] for n in nodes}
edges = []


def edge(s, t, handle="source", inner=True):
    d = {"sourceType": type_of[s], "targetType": type_of[t], "isInLoop": inner}
    if inner:
        d["loop_id"] = LOOP
    e = {"id": "%s-%s-%s-target" % (s, handle, t), "source": s, "sourceHandle": handle, "target": t,
         "targetHandle": "target", "type": "custom", "data": d}
    if inner:
        e["zIndex"] = 1002
    else:
        e["zIndex"] = 0
    edges.append(e)


edge(START, IF0, inner=False)
edge(IF0, AGG, handle="true", inner=False)
edge(IF0, EXT, handle="false", inner=False)
edge(EXT, AGG, inner=False)
edge(AGG, PRE, inner=False)
edge(PRE, IF2, inner=False)
edge(IF2, EXT2, handle="true", inner=False)
edge(IF2, JR, handle="false", inner=False)
edge(EXT2, LLMJ, inner=False)
edge(LLMJ, JR, inner=False)
edge(JR, IFC, inner=False)
edge(IFC, EXTC, handle="true", inner=False)
edge(IFC, CTXA, handle="false", inner=False)
edge(EXTC, CTXA, inner=False)
edge(CTXA, IFP, inner=False)
edge(IFP, EXTP, handle="true", inner=False)
edge(IFP, CTX, handle="false", inner=False)
edge(EXTP, CTX, inner=False)
edge(CTX, LITQ, inner=False)
edge(LITQ, LITP, inner=False)
for _h in (LH1, LH2, LH3):
    edge(LITP, _h, inner=False)
    edge(_h, LITM, inner=False)
edge(LITM, IFA, inner=False)
edge(IFA, ALN, handle="true", inner=False)
edge(IFA, ALM, handle="false", inner=False)
edge(ALN, ALM, inner=False)
edge(ALM, LOOP, inner=False)
edge(LOOP, FMT, inner=False)
edge(FMT, GUARD, inner=False)
edge(GUARD, PCHK2, inner=False)
edge(PCHK2, DIG, inner=False)
edge(DIG, FINAL, inner=False)
edge(FINAL, REP, inner=False)
edge(REP, END, inner=False)
edge(LSTART, PCHK)
edge(PCHK, GEM)
edge(PCHK, GPT)
edge(GEM, C1)
edge(GPT, C1)
edge(C1, ASA)
edge(ASA, IFE)
edge(IFE, REV, handle="true")
edge(REV, C2)
edge(C2, ASB)

# ---------- 環境變數 ----------
def env(name, value, desc=""):
    return {"id": uid(), "name": name, "selector": ["env", name], "value": value, "value_type": "string", "description": desc}

DEFAULT_FORMAT = """【內建預設格式（未上傳期刊格式檔時使用；非任何特定期刊的規範）】
- 語言與標點：中文內文使用全形標點（，。；：、？！「」『』（））；英文與數字內部使用半形標點。
- 統計符號：t、p、d、N、F、α 等符號與等號、數值之間保留空格（如 p = .31、t = 6.42、N = 40）。
- 章節編號：以「一、」「（一）」「1.」「（1）」逐層編號，全文層級與寫法一致。
- 摘要與關鍵詞：中文摘要建議 300 字以內；關鍵詞 3 至 5 個。
- 正文引註：採作者─年份制，寫法為（作者，年份）或作者（年份）；全文引註的括號、逗號、頓號寫法一致。
- 參考文獻：依作者姓氏（或筆畫）排序；中文書籍為「作者（年份）。書名。出版地：出版者。」；中文期刊論文為「作者（年份）。篇名。期刊名，卷(期)，頁碼。」；英文書目依 APA 第七版精神。
- 圖表：表標題置於表上方、圖標題置於圖下方，編號連續，正文須引用。
- 匿名審查與 AI 使用揭露：預設未要求；投稿前請依目標期刊規定自行確認。
- 重要：此預設僅用於全文一致性檢查與整理，格式相關意見的嚴重度不得高於 minor。"""

_skill_p_raw = read(PROP / "skills" / "tp-proposal-reviser" / "SKILL.md")
_skill_p_body = re.sub(r"^---.*?---\s*", "", _skill_p_raw, count=1, flags=re.S)
skill_proposal = skill_body + "\n\n---\n\n# 計畫書模式補充規則（優先於上述通則；若有衝突，以本節為準）\n\n" + _skill_p_body


def _sys_from(path):
    t = read(path)
    return t.split("## System prompt", 1)[1].split("## User prompt 模板", 1)[0].strip().rstrip("-").strip()


DEFAULT_FORMAT_PROPOSAL = """【教育部教學實踐研究計畫「計畫內容」官方格式（未上傳其他格式檔時使用）】
- 體例：Word 編輯；中文標楷體、英文 Times New Roman、12 pt；字元間距標準；單行間距；邊界上下左右各 2 cm；頁碼 1、2、3 置於每頁下方中央。
- 頁數：一年期至多 25 頁、多年期至多 40 頁（含參考文獻與附件，不含封面），超出部分不予審查；計畫主持人部分至多 5 頁。
- 章節與順序：封面；一、計畫主持人部分；二、計畫執行內容部分：（一）教學實踐研究計畫動機（延續性計畫須於此列出與過去計畫的差異）、（二）研究主題與目的、（三）文獻探討、（四）教學設計與規劃、（五）研究設計與執行規劃、（六）預期完成工作項目與成果、（七）參考文獻、（八）附件。
- 課程設計與規劃須注重內容彼此之關聯性，避免直接複製授課計畫書；授課計畫書與計畫書所載課程須一致。
- 引註：作者─年份制；文獻引用（含自我引用）須遵守學術倫理，避免抄襲與自我抄襲。
- AI 使用揭露：使用 AI 輔助撰寫者，須說明用途、方式與範圍，並由計畫主持人負責內容正確性。
- 研究倫理：涉及心理資訊（焦慮、情緒、壓力等）、特殊身分研究對象，或實驗組／對照組設計者，應規劃研究倫理審查；其餘須有研究參與者招募與知情同意規劃；學生資料須去識別化。"""

env_vars = [
    env("RUBRIC", rubric, "審查標準全文（review-rubric.md）"),
    env("RUBRIC_PROPOSAL", read(PROP / "rubric-teaching-practice-proposal.md"), "教學實踐研究計畫書預審標準"),
    env("SYS_A_JOURNAL", system_prompt("reviewer-gemini.md"), "審查A系統提示（期刊；含 {{rubric}}、{{paper_type}} 占位符）"),
    env("SYS_B_JOURNAL", system_prompt("reviewer-gpt.md"), "審查B系統提示（期刊）"),
    env("SYS_A_PROPOSAL", _sys_from(PROP / "reviewers" / "proposal-reviewer-a.md"), "審查A系統提示（計畫書）"),
    env("SYS_B_PROPOSAL", _sys_from(PROP / "reviewers" / "proposal-reviewer-b.md"), "審查B系統提示（計畫書）"),
    env("REVISER_SKILL_PROPOSAL", skill_proposal, "修稿者 Skill（計畫書版：期刊通則＋計畫書補充規則）"),
    env("SYS_FINAL_JOURNAL", SYS_FINAL, "終審系統提示（期刊）"),
    env("SYS_FINAL_PROPOSAL", _sys_from(PROP / "reviewers" / "proposal-final-review.md"), "終審系統提示（計畫書）"),
    env("DEFAULT_FORMAT_PROPOSAL", DEFAULT_FORMAT_PROPOSAL, "計畫書官方格式摘要（未上傳其他格式檔時使用）"),
    env("LAYOUT_PROPOSAL", read(PROP / "layout-spec-moe-proposal.json"), "計畫書官方版面規格（Word 匯出用）"),
    env("REVISER_SKILL", skill_body, "修稿者 Skill 本文"),
    {"id": uid(), "name": "SERPAPI_KEY", "selector": ["env", "SERPAPI_KEY"], "value": "", "value_type": "secret", "description": "SerpApi 金鑰（Google Scholar 文獻檢索）。留空則略過文獻檢索，其他流程照常"},
    env("DEFAULT_FORMAT_RULES", DEFAULT_FORMAT, "內建預設格式（未上傳期刊格式檔時使用）；可依需要修改"),
]

dsl = {
    "app": {"name": "期刊稿件審查與修訂迴圈", "mode": "workflow", "icon": "📝", "icon_background": "#FFEAD5",
            "description": "Gemini＋GPT 審查，Claude 修稿，程式碼判斷停止條件；含教學實踐研究計畫書預審模式", "use_icon_as_answer_icon": False},
    "kind": "app",
    "version": "0.3.1",
    "workflow": {
        "conversation_variables": [],
        "environment_variables": env_vars,
        "features": {
            "file_upload": {"enabled": False}, "opening_statement": "", "retriever_resource": {"enabled": False},
            "sensitive_word_avoidance": {"enabled": False}, "speech_to_text": {"enabled": False},
            "suggested_questions": [], "suggested_questions_after_answer": {"enabled": False},
            "text_to_speech": {"enabled": False, "language": "", "voice": ""},
        },
        "graph": {"edges": edges, "nodes": nodes, "viewport": {"x": 0, "y": 0, "zoom": 0.5}},
    },
}


class Dumper(yaml.SafeDumper):
    pass


def _str(dumper, s):
    if "\n" in s:
        return dumper.represent_scalar("tag:yaml.org,2002:str", s, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", s)


Dumper.add_representer(str, _str)
OUT.write_text(yaml.dump(dsl, Dumper=Dumper, allow_unicode=True, sort_keys=False, width=10000), encoding="utf-8")
print("written", OUT, OUT.stat().st_size, "bytes")
