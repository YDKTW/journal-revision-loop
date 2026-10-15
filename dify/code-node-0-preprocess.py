# Dify「程式碼執行」節點 0：稿件前處理（用於上傳 PDF／Word 或貼上文字之後、進入循環之前）
# 輸入變數（三個都要設定；為了不依賴「合併稿件來源」節點的行為，直接讀取三個來源，依序取第一個非空者）：
#   pasted_text (String)     開始節點的 manuscript（貼上的稿件全文）
#   extracted_text (String)  文件提取器節點的 text（上傳 PDF／Word 擷取出的文字）
#   raw_text (String)        「合併稿件來源」節點的 output（備援）
# 輸出變數：
#   manuscript_ready (String)  清理並加上保護標記後的稿件，作為循環的初始稿件
#   report (String)            前處理報告（做了什麼、有何警示），結束節點會一併輸出
#   head_text (String)         稿件前 3500 字（供文獻檢索節點產生查詢）
#   kb_query (String)          供「知識檢索」節點使用的簡短查詢字串（標題＋關鍵詞＋摘要開頭，最多約 250 字元）
#
# 做的事：1) 清理格式  2) 修復 PDF 硬換行  3) 自動以 <!--LOCK--> 保護表格與研究倫理段落  4) 基本檢查
# 不做的事：不改寫任何內容文字；不判斷內容對錯。

import json
import re

MIN_CHARS = 500
TERMINAL = "。！？；：）」』”.!?;:)\"'"
HEAD_RE = re.compile(
    r"^(#{1,6}\s|[一二三四五六七八九十]+、|（[一二三四五六七八九十]+）|\([一二三四五六七八九十]+\)"
    r"|第[一二三四五六七八九十0-9]+[章節]|[0-9]+(\.[0-9]+)*[\.、\s]"
    r"|摘要|關鍵詞|關鍵字|參考文獻|Abstract|Keywords|References)"
)
TABLE_CAP_RE = re.compile(r"^(表|Table)\s*[0-9一二三四五六七八九十]+")
ETHICS_RE = re.compile(r"(研究倫理|倫理考量|倫理審查|Ethic)")


def _is_heading(line):
    t = line.strip()
    if not t:
        return False
    if HEAD_RE.match(t):
        return len(t) <= 40
    return len(t) <= 20 and t[-1] not in TERMINAL and "，" not in t and "," not in t


def _clean(text):
    t = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("\u00a0", " ").replace("\u200b", "")
    lines = [ln.rstrip() for ln in t.split("\n")]
    # 移除只含頁碼的行（如「12」「- 12 -」「第 3 頁」）
    page_re = re.compile(r"^\s*(-\s*)?(第\s*)?[0-9]{1,3}(\s*頁)?(\s*-)?\s*$")
    lines = [ln for ln in lines if not page_re.match(ln) or "\t" in ln]
    out, blank = [], 0
    for ln in lines:
        if ln.strip() == "":
            blank += 1
            if blank <= 1:
                out.append("")
        else:
            blank = 0
            out.append(ln)
    return "\n".join(out).strip()


def _unwrap(raw):
    """若貼進來的是 Dify 輸出的 JSON（含 final_manuscript），或被轉義成一行的字串（含 \\n），先還原成純文字。"""
    t = (raw or "").strip()
    note = ""
    for _ in range(3):
        if t[:1] in ("{", '"'):
            try:
                o = json.loads(t)
            except Exception:
                break
            if isinstance(o, dict) and isinstance(o.get("final_manuscript"), str):
                t, note = o["final_manuscript"], "偵測到貼上的是流程輸出的 JSON，已自動取出其中的 final_manuscript。"
            elif isinstance(o, str):
                t, note = o, note or "偵測到貼上的文字被轉義成 JSON 字串，已自動還原。"
            else:
                break
        else:
            break
    if t.count("\\n") >= 20 and t.count("\n") < 5:
        t = t.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
        note = note or "偵測到文字中的換行被寫成 \\n，已自動還原。"
    return t, note


def _looks_hard_wrapped(text):
    lines = [ln for ln in text.split("\n") if ln.strip() and "\t" not in ln and not ln.lstrip().startswith("|")]
    if len(lines) < 20:
        return False
    no_end = sum(1 for ln in lines if ln.strip()[-1] not in TERMINAL and not _is_heading(ln))
    lens = sorted(len(ln.strip()) for ln in lines)
    median = lens[len(lens) // 2]
    return no_end / float(len(lines)) > 0.35 and median < 70


def _rejoin(text):
    out, buf = [], ""
    for ln in text.split("\n"):
        s = ln.strip()
        if not s:
            if buf:
                out.append(buf)
                buf = ""
            out.append("")
            continue
        protected = "\t" in ln or s.startswith("|") or _is_heading(s) or TABLE_CAP_RE.match(s)
        if protected:
            if buf:
                out.append(buf)
                buf = ""
            out.append(s)
            continue
        if buf:
            # 中文直接相連；英文以空格相連
            sep = " " if (re.search(r"[A-Za-z0-9,]$", buf) and re.match(r"^[A-Za-z0-9(]", s)) else ""
            buf += sep + s
        else:
            buf = s
        if buf and buf[-1] in "。！？」』”.!?":
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return "\n".join(out)


def _lock_tables(text):
    lines = text.split("\n")
    n = len(lines)
    res, i, count = [], 0, 0
    in_lock = False
    while i < n:
        ln = lines[i]
        if "<!--LOCK-->" in ln:
            in_lock = True
        if "<!--/LOCK-->" in ln:
            in_lock = False
        is_tab = ("\t" in ln) or ln.strip().startswith("|")
        if is_tab and not in_lock:
            j = i
            while j < n and (("\t" in lines[j]) or lines[j].strip().startswith("|")):
                j += 1
            if j - i >= 2:
                # 往前帶入表格標題（表1、Table 1）
                cap = None
                k = len(res) - 1
                while k >= 0 and res[k] == "":
                    k -= 1
                if k >= 0 and TABLE_CAP_RE.match(res[k].strip()):
                    cap = res[k]
                    res = res[:k]
                res.append("<!--LOCK-->")
                if cap is not None:
                    res.append(cap)
                    res.append("")
                res.extend(lines[i:j])
                res.append("<!--/LOCK-->")
                count += 1
                i = j
                continue
        res.append(ln)
        i += 1
    return "\n".join(res), count



STRICT_HEAD_RE = re.compile(r"^(#{1,6}\s|[一二三四五六七八九十]+、|（[一二三四五六七八九十]+）|第[一二三四五六七八九十0-9]+[章節]|摘要|關鍵詞|關鍵字|參考文獻|Abstract|Keywords|References)")


def _row_like(s):
    """判斷一行是否像表格資料列：多個連續空白／Tab、數字比例高、很短，或只有分隔線。"""
    if re.fullmatch(r"[-=+| ]+", s):
        return True
    if "\t" in s or re.search(r"\S\s{2,}\S", s):
        return True
    digits = sum(ch.isdigit() for ch in s)
    if len(s) <= 12 or digits / float(max(len(s), 1)) >= 0.2:
        return True
    return False


def _lock_caption_tables(text):
    lines = text.split("\n")
    n = len(lines)
    res, i, count = [], 0, 0
    in_lock = False
    while i < n:
        ln = lines[i]
        if "<!--LOCK-->" in ln:
            in_lock = True
        if "<!--/LOCK-->" in ln:
            in_lock = False
        s = ln.strip()
        if (not in_lock) and TABLE_CAP_RE.match(s) and len(s) <= 60 and not s.endswith("。"):
            j, last, rows = i + 1, i, 0
            while j < n and j - i <= 40:
                t = lines[j].strip()
                if t == "":
                    j += 1
                    continue
                if re.fullmatch(r"[-=+| ]+", t):  # 分隔線（長度不限），視為表格的一部分
                    last = j
                    j += 1
                    continue
                if STRICT_HEAD_RE.match(t) or (len(t) > 60 and not re.search(r"\S\s{2,}\S", t)) or t.endswith("。") or not _row_like(t):
                    break
                last, rows = j, rows + 1
                j += 1
            if rows >= 2:
                res.append("<!--LOCK-->")
                res.extend(lines[i:last + 1])
                res.append("<!--/LOCK-->")
                count += 1
                i = last + 1
                continue
        res.append(ln)
        i += 1
    return "\n".join(res), count


def _lock_ethics(text):
    lines = text.split("\n")
    n = len(lines)
    res, i, count = [], 0, 0
    in_lock = False
    while i < n:
        ln = lines[i]
        if "<!--LOCK-->" in ln:
            in_lock = True
        if "<!--/LOCK-->" in ln:
            in_lock = False
        res.append(ln)
        if (not in_lock) and _is_heading(ln) and ETHICS_RE.search(ln) and len(ln.strip()) <= 30:
            j = i + 1
            while j < n and not _is_heading(lines[j]) and "<!--LOCK-->" not in lines[j]:
                j += 1
            body = [x for x in lines[i + 1:j]]
            if any(x.strip() for x in body):
                res.append("<!--LOCK-->")
                res.extend(body)
                # 區塊結尾若有空行，標記放在空行之前
                while res and res[-1] == "":
                    res.pop()
                    body_trailing = True
                res.append("<!--/LOCK-->")
                res.append("")
                count += 1
                i = j
                continue
        i += 1
    return "\n".join(res), count


def _lit_head(text, limit=3500):
    """供文獻檢索用的稿件開頭。計畫書的前幾頁多半是封面與主持人資料，用它產生查詢會偏離研究主題，
    因此偵測到計畫書封面特徵時，改從「研究動機／摘要／主題及目的」等研究內容起算。"""
    clean = re.sub(r"<!--/?LOCK-->\n?", "", text)
    first = clean[:3500]
    if re.search(r"計畫主持人|主持人部分|申請機構|計畫編號|基本資料|計畫類別", first):
        start = None
        m = re.search(r"計畫執行內容", clean)
        if m:
            start = m.start()
        if start is None:
            m = re.search(r"(?m)^[^\n]{0,10}(?:研究動機|計畫摘要|計畫成果摘要|主題及目的|研究目的)[^\n]{0,20}$", clean[800:])
            if m:
                start = 800 + m.start()
        if start is not None:
            return clean[start:start + limit]
    return clean[:limit]


def main(pasted_text: str = "", extracted_text: str = "", raw_text: str = "") -> dict:
    sources = [("貼上文字", pasted_text), ("上傳檔案擷取", extracted_text), ("合併節點", raw_text)]
    lens = [(name, len((v or "").strip()) if isinstance(v, str) else 0) for name, v in sources]
    raw, picked = "", ""
    for name, v in sources:
        if isinstance(v, str) and v.strip():
            raw, picked = v, name
            break
    raw, unwrap_note = _unwrap(raw)
    text = _clean(raw)
    notes = []
    if unwrap_note:
        notes.append("⚠" + unwrap_note + "（請確認這是你要審查的稿件）")
    if picked:
        notes.append("稿件來源：%s。" % picked)

    if len(text) < MIN_CHARS:
        detail = "；".join("%s %d 字元" % (n, k) for n, k in lens)
        raise ValueError(
            "取得的稿件文字只有 %d 字元（少於 %d）。各來源長度：%s。"
            "若「上傳檔案擷取」為 0，代表文件提取器沒有讀到內容（掃描型 PDF、檔案未成功上傳，或檔案受保護）；"
            "請改貼上文字，或先對 PDF 做文字辨識（OCR）。" % (len(text), MIN_CHARS, detail)
        )

    if _looks_hard_wrapped(text):
        text = _rejoin(text)
        notes.append("偵測到 PDF 式的硬換行，已將斷行的段落接回。")

    text, n_tab = _lock_tables(text)
    text, n_cap = _lock_caption_tables(text)
    n_tab += n_cap
    text, n_eth = _lock_ethics(text)
    locks = len(re.findall(r"<!--LOCK-->", text))

    if n_tab:
        notes.append("自動保護表格 %d 個（含標題）。" % n_tab)
    else:
        notes.append("未偵測到表格；若稿中有數據表，請自行確認是否需要以 <!--LOCK--> 保護。")
    if n_eth:
        notes.append("自動保護研究倫理段落 %d 處。" % n_eth)
    else:
        notes.append("未偵測到「研究倫理」段落；若有倫理聲明，建議手動以 <!--LOCK--> 保護。")

    if not re.search(r"參考文獻|References|Bibliography", text):
        notes.append("⚠未偵測到「參考文獻」章節，引註核對將無法進行。")
    if not re.search(r"摘要|Abstract", text):
        notes.append("⚠未偵測到「摘要」。")
    if re.search(r"\(cid:[0-9]+\)|\ufffd", text):
        notes.append("⚠文字含亂碼（如 cid 或 �），PDF 字型擷取可能失敗，建議改用 Word 檔。")

    # 產生知識庫檢索用的簡短查詢
    plain = [ln.strip() for ln in text.split("\n") if ln.strip() and "<!--" not in ln]
    title = plain[0] if plain else ""
    m_t = re.search(r"計畫名稱\s*[:：]?\s*([^\n]{4,80})", text)
    if m_t:
        title = (m_t.group(1) + " " + title).strip()
    kw = ""
    for ln in plain:
        if re.match(r"^(關鍵詞|關鍵字|Keywords?)", ln):
            kw = re.sub(r"^(關鍵詞|關鍵字|Keywords?)\s*[:：]?\s*", "", ln)
            break
    abs_txt = ""
    for idx, ln in enumerate(plain):
        if re.match(r"^(摘要|Abstract)\s*$", ln) and idx + 1 < len(plain):
            abs_txt = plain[idx + 1][:120]
            break
    kb_query = " ".join(x for x in (title, kw, abs_txt) if x)[:250]

    report = "前處理完成：%d 字元，鎖定區塊共 %d 個。" % (len(text), locks) + "".join("\n- " + x for x in notes)
    head = _lit_head(text)
    return {"manuscript_ready": text, "report": report, "kb_query": kb_query, "head_text": head}
