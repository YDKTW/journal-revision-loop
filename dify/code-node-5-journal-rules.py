# Dify「程式碼執行」節點 5：決定要採用的期刊規範（上傳解析結果，或內建預設）
# 輸入變數：
#   parsed_rules (String)   期刊格式解析節點的輸出（未上傳格式檔時，該節點不會執行，值為空）
#   default_rules (String)  環境變數 DEFAULT_FORMAT_RULES（內建預設格式）
#   format_source (String)  開始表單的「期刊格式來源」（內建預設格式 / 上傳期刊格式檔）
# 輸出變數：
#   journal_rules (String)  採用的規範全文
#   journal_source (String) 規範來源說明（內建預設格式 / 上傳之期刊格式）
#   layout_spec (String)    版面規格 JSON（供 Word 輸出程式使用；稿約未載明的項目採內建預設）

import copy
import json
import re

DEFAULT_LAYOUT = {
    "page": {"size": "A4", "margin_top_cm": 2.54, "margin_bottom_cm": 2.54, "margin_left_cm": 3.17, "margin_right_cm": 3.17},
    "font_zh": "標楷體",
    "font_en": "Times New Roman",
    "body": {"size_pt": 12, "line_spacing": 1.5, "first_line_indent_chars": 2, "align": "justify"},
    "title": {"size_pt": 16, "bold": True, "align": "center"},
    "heading1": {"size_pt": 14, "bold": True, "align": "left"},
    "heading2": {"size_pt": 13, "bold": True, "align": "left"},
    "heading3": {"size_pt": 12, "bold": True, "align": "left"},
    "table_caption": {"position": "above", "size_pt": 11, "bold": True, "align": "center"},
    "table": {"size_pt": 10, "style": "grid"},
    "references": {"size_pt": 12, "line_spacing": 1.0, "hanging_indent_chars": 2},
    "page_number": {"enabled": True, "position": "center"},
}
LAYOUT_RE = re.compile(r"<layout_spec>(.*?)</layout_spec>", re.S)


def _merge(parsed):
    """以稿約解析出的非 null 值覆蓋預設；回傳 (規格, 使用預設值的項目清單)。"""
    out, defaulted = copy.deepcopy(DEFAULT_LAYOUT), []
    parsed = parsed if isinstance(parsed, dict) else {}
    for k, dv in DEFAULT_LAYOUT.items():
        pv = parsed.get(k)
        if isinstance(dv, dict):
            for kk in dv:
                v = pv.get(kk) if isinstance(pv, dict) else None
                if v is None:
                    defaulted.append("%s.%s" % (k, kk))
                else:
                    out[k][kk] = v
        else:
            if pv is None:
                defaulted.append(k)
            else:
                out[k] = pv
    return out, defaulted


def _split_layout(text):
    m = LAYOUT_RE.search(text or "")
    if not m:
        return text, {}
    body = (text[:m.start()] + text[m.end():]).strip()
    s = re.sub(r"^```[a-zA-Z]*\s*", "", m.group(1).strip())
    s = re.sub(r"\s*```$", "", s)
    try:
        return body, json.loads(s, strict=False)
    except Exception:
        return body, {}


def main(parsed_rules: str = "", default_rules: str = "", format_source: str = "") -> dict:
    parsed = (parsed_rules or "").strip()
    default = (default_rules or "").strip()
    wants_upload = (format_source or "").strip() == "上傳期刊格式檔"

    if wants_upload and len(parsed) >= 50 and "【警示】此文件似乎不是" not in parsed[:80]:
        rules_text, parsed_layout = _split_layout(parsed)
        spec, defaulted = _merge(parsed_layout)
        spec["unspecified"] = defaulted
        spec["source"] = "上傳之期刊格式（稿約未載明的項目採內建預設，見 unspecified）"
        return {"journal_rules": rules_text, "journal_source": "上傳之期刊格式",
                "layout_spec": json.dumps(spec, ensure_ascii=False)}

    if wants_upload:
        # 選了上傳，但沒有有效內容：明確停止，避免悄悄改用預設而讓使用者以為用的是自己的期刊格式
        raise ValueError(
            "已選擇「上傳期刊格式檔」，但沒有取得有效的期刊規範（檔案可能未成功上傳、為掃描檔，或內容不像稿約）。"
            "請確認檔案後重試，或將「期刊格式來源」改為「內建預設格式」。"
        )
    spec = copy.deepcopy(DEFAULT_LAYOUT)
    spec["unspecified"] = ["全部使用內建預設版面"]
    spec["source"] = "內建預設格式"
    return {"journal_rules": default, "journal_source": "內建預設格式",
            "layout_spec": json.dumps(spec, ensure_ascii=False)}
