# Dify「程式碼執行」節點 4：產生最終報告資料（最後一輪修訂以紅色標示）
# 用途：循環與格式整理結束後執行一次。輸出供「本機匯出程式」產生 Word（.docx）使用，並附一份可直接開啟的 HTML。
# 輸入變數（String，除非另註）：
#   prev_manuscript   最後一次修稿之前的稿件（循環變數 prev_manuscript）
#   final_manuscript  最終稿件（格式整理防護節點的 final_manuscript）
#   history, decision, last_reason, last_flags, last_response_table
#   prior_a, prior_b  審查A、審查B 最後仍未解決的意見（JSON 字串，循環變數 prior_gemini、prior_gpt）
#   preprocess_report, format_report, journal_source, paper_type, final_opinion（終審意見節點的輸出）
# 輸出變數：report_json (String)  report_html (String)  change_summary (String)
#
# 紅字規則：以「只比對文字與數字」的方式與修訂前稿件逐句比較，標點、空白與版面調整不會被標紅；
#   修改過的句子只標出變動的字詞，全新的句子整句標紅。【待作者補充／確認／建議補充】標記以黃底標示。

import difflib
import html
import json
import re
import unicodedata

COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
TODO_RE = re.compile(r"〔?【(?:待作者補充|待作者確認|建議補充)[^】]*】〕?")
SENT_RE = re.compile(r"[^。！？；!?;]*[。！？；!?;]?")
HEAD_RE = re.compile(
    r"^(#{1,6}\s|[一二三四五六七八九十]+、|（[一二三四五六七八九十]+）|第[一二三四五六七八九十0-9]+[章節]|摘要|關鍵詞|關鍵字|參考文獻|Abstract|Keywords|References)"
)


def _alnum(ch):
    return unicodedata.category(unicodedata.normalize("NFKC", ch)[:1] or " ")[0] in "LN"


def _sig(text):
    """內容簽章：只保留文字與數字；但保留數字中的小數點、千分位逗號與負號，避免「-.97」被悄悄改成「.97」。"""
    t = unicodedata.normalize("NFKC", text)
    t = re.sub(r"[-–−—](?=\.?\d)", "ⅿ", t)        # 數字前的負號或連字號
    t = re.sub(r"(?<=\d)[.,](?=\d)", "ⅾ", t)        # 數字之間的小數點或千分位逗號
    t = re.sub(r"(?<![\w])\.(?=\d)", "ⅾ", t)        # 省略前導 0 的小數（如 .31）
    return "".join(ch for ch in t if unicodedata.category(ch)[0] in "LN").lower()


def _sentences(line):
    """切句。【待作者…】標記獨立成一段，避免與相鄰句子黏在一起而被誤判為「修改過」。"""
    out, last = [], 0
    for m in TODO_RE.finditer(line):
        out += [x.group(0) for x in SENT_RE.finditer(line[last:m.start()]) if x.group(0)]
        out.append(m.group(0))
        last = m.end()
    out += [x.group(0) for x in SENT_RE.finditer(line[last:]) if x.group(0)]
    return out


def _red_flags(cur, prev_sigs, prev_list):
    """回傳 cur 每個字元是否標紅的布林陣列。"""
    flags = [False] * len(cur)
    sig = _sig(cur)
    if not sig or sig in prev_sigs:
        return flags
    # 找最相似的舊句
    best, best_r = None, 0.0
    for ps in prev_list:
        if not ps or abs(len(ps) - len(sig)) > max(len(sig), len(ps)) * 0.6:
            continue
        sm = difflib.SequenceMatcher(None, ps, sig, autojunk=False)
        if sm.real_quick_ratio() < 0.5 or sm.quick_ratio() < 0.5:
            continue
        r = sm.ratio()
        if r > best_r:
            best, best_r = ps, r
    # 以「文字與數字」為單位的紅色標記
    red_sig = [True] * len(sig)
    if best is not None and best_r >= 0.55:
        red_sig = [False] * len(sig)
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, best, sig, autojunk=False).get_opcodes():
            if tag in ("insert", "replace"):
                for j in range(j1, j2):
                    red_sig[j] = True
    # 對應回原句字元；標點與空白依前後文字決定
    k, pos = 0, []
    for i, ch in enumerate(cur):
        if unicodedata.category(unicodedata.normalize("NFKC", ch)[:1] or " ")[0] in "LN" and k < len(sig):
            flags[i] = red_sig[k]
            pos.append(i)
            k += 1
    for a, b in zip(pos, pos[1:]):
        if flags[a] and flags[b]:
            for i in range(a + 1, b):
                flags[i] = True
    if pos and flags[pos[-1]]:
        for i in range(pos[-1] + 1, len(cur)):
            if cur[i] in "。！？；!?;，,、":
                flags[i] = True
    return flags


def _runs(line, prev_sigs, prev_list):
    """把一行文字切成 [{t, c}]；c 為 '' | 'red' | 'todo'。"""
    todo = [False] * len(line)
    for m in TODO_RE.finditer(line):
        for i in range(m.start(), m.end()):
            todo[i] = True
    red = [False] * len(line)
    pos = 0
    for sent in _sentences(line):
        f = _red_flags(sent, prev_sigs, prev_list)
        for i, v in enumerate(f):
            red[pos + i] = v
        pos += len(sent)
    cls = ["todo" if todo[i] else ("red" if red[i] else "") for i in range(len(line))]
    out, buf, cur = [], "", None
    for ch, c in zip(line, cls):
        if cur is None or c == cur:
            buf += ch
            cur = c
        else:
            out.append({"t": buf, "c": cur})
            buf, cur = ch, c
    if buf:
        out.append({"t": buf, "c": cur})
    return out


def _opinion_html(text):
    """終審意見（Markdown 風格純文字）→ 簡單 HTML。"""
    parts = []
    for ln in (text or "").split("\n"):
        t = ln.rstrip()
        if not t.strip():
            continue
        e = html.escape(t)
        if t.startswith("### "):
            parts.append("<h4>%s</h4>" % html.escape(t[4:].strip()))
        elif t.startswith("## "):
            parts.append("<h3>%s</h3>" % html.escape(t[3:].strip()))
        elif t.lstrip().startswith(("- ", "* ")):
            parts.append("<p style='margin:2px 0 2px 1.2em'>• %s</p>" % html.escape(t.lstrip()[2:]))
        else:
            parts.append("<p style='margin:4px 0'>%s</p>" % e)
    return "".join(parts)


def _parse_issues(txt):
    try:
        data = json.loads(txt or "[]")
    except Exception:
        return []
    return [{k: it.get(k, "") for k in ("id", "dimension", "severity", "location", "problem", "suggestion", "screen_note", "orig_severity")}
            for it in data if isinstance(it, dict)]


def _html_runs(runs):
    parts = []
    for r in runs:
        t = html.escape(r["t"])
        if r["c"] == "red":
            parts.append('<span style="color:#C00000">%s</span>' % t)
        elif r["c"] == "todo":
            parts.append('<span style="background:#FFF2CC">%s</span>' % t)
        else:
            parts.append(t)
    return "".join(parts)


def main(prev_manuscript: str, final_manuscript: str, history: str, decision: str, last_reason: str,
         last_flags: str, last_response_table: str, prior_a: str, prior_b: str, preprocess_report: str,
         format_report: str, journal_source: str, paper_type: str, final_opinion: str = "") -> dict:
    final_opinion = re.sub(r"<think>.*?</think>", "", final_opinion or "", flags=re.S).strip()
    final_opinion = re.sub(r"\*\*(.+?)\*\*", r"\1", final_opinion)  # 去除 Markdown 粗體記號
    prev = COMMENT_RE.sub("", prev_manuscript or "")
    fin = COMMENT_RE.sub("", final_manuscript or "")
    prev_list = [s for s in (_sig(x) for ln in prev.split("\n") for x in _sentences(ln)) if s]
    prev_sigs = set(prev_list)

    blocks, html_parts, n_red, n_total = [], [], 0, 0
    lines = [ln.rstrip() for ln in fin.split("\n")]
    i = 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        if "\t" in ln or ln.strip().startswith("|"):  # 表格列（Tab 分隔，或 Markdown 的 | 分隔）
            rows = []
            while i < len(lines) and ("\t" in lines[i] or lines[i].strip().startswith("|") or not lines[i].strip()):
                cur = lines[i].strip()
                if cur and not re.fullmatch(r"\|?[\s:|-]+\|?", cur):  # 略過 Markdown 的分隔列
                    if "\t" in cur:
                        rows.append([c.strip() for c in cur.split("\t")])
                    else:
                        rows.append([c.strip() for c in cur.strip("|").split("|")])
                i += 1
            blocks.append({"k": "table", "rows": rows})
            html_parts.append("<table border='1' cellspacing='0' cellpadding='4' style='border-collapse:collapse'>" +
                              "".join("<tr>" + "".join("<td>%s</td>" % html.escape(c) for c in r) + "</tr>" for r in rows) +
                              "</table>")
            continue
        hm = re.match(r"^(#{1,6})\s+(.*)$", ln.strip())
        text_ln = hm.group(2) if hm else ln
        runs = _runs(text_ln, prev_sigs, prev_list)
        n_total += len([s for s in _sentences(text_ln) if _sig(s)])
        n_red += len([1 for r in runs if r["c"] == "red"])
        t_strip = text_ln.strip()
        is_head = bool(hm) or (HEAD_RE.match(t_strip) and len(t_strip) <= 40
                               and not re.match(r"^(關鍵詞|關鍵字|Keywords?)\s*[:：]", t_strip))
        if not blocks and not is_head and len(t_strip) <= 80:
            is_head = True  # 全文第一行視為論文題目
        kind = "h" if is_head else "p"
        level = 0
        if is_head:
            if not blocks:
                level = 1  # 全文第一個標題＝論文題目
            elif hm:
                level = min(len(hm.group(1)), 3)
            elif re.match(r"^（[一二三四五六七八九十]+）", t_strip):
                level = 3
            else:
                level = 2
        blk = {"k": kind}
        if level:
            blk["l"] = level
        if len(runs) == 1 and runs[0]["c"] == "":
            blk["t"] = runs[0]["t"]
        else:
            blk["runs"] = runs
        blocks.append(blk)
        body = _html_runs(runs)
        html_parts.append("<h%d>%s</h%d>" % (level + 1, body, level + 1) if kind == "h" else "<p>%s</p>" % body)
        i += 1

    if _sig(prev) == _sig(fin):
        change_summary = "最後一輪沒有修訂內容（稿件文字與前一版相同），因此沒有紅色標示。"
    else:
        change_summary = "最後一輪修訂共標紅 %d 處（紅色：修改或新增之文字；黃底：待作者補充或確認的標記）。" % n_red

    unresolved = _parse_issues(prior_a) + _parse_issues(prior_b)
    if not final_opinion:
        cnt = {"critical": 0, "major": 0, "minor": 0}
        for u in unresolved:
            if u.get("severity") in cnt:
                cnt[u["severity"]] += 1
        n_todo = len(re.findall(r"【待作者(?:補充|確認)", fin))
        final_opinion = (
            "## 終審意見（模型沒有輸出內容）\n\n"
            "終審模型沒有產生任何文字，可能是模型的思考用盡了輸出額度，或呼叫失敗。以下是程式整理的摘要，**不是模型的判斷**：\n"
            "- 結束原因：%s（%s）\n- 最後仍未解決的意見：critical %d、major %d、minor %d（含作者已知待補者）\n"
            "- 稿中待作者補充或確認的標記：%d 處\n"
            "- 建議：參閱附錄 A 至 D；必要時調高「終審意見」節點的最大 token、關閉思考模式，或改用其他模型重做終審。"
        ) % (decision or "", last_reason or "", cnt["critical"], cnt["major"], cnt["minor"], n_todo)
    meta = {
        "paper_type": paper_type, "journal_source": journal_source, "decision": decision, "reason": last_reason,
        "change_summary": change_summary,
    }
    report = {
        "meta": meta,
        "final_opinion": final_opinion or "",
        "manuscript": blocks,
        "history": [x for x in (history or "").split("\n") if x.strip()],
        "unresolved": unresolved,
        "response_table": last_response_table or "",
        "flags": last_flags or "",
        "preprocess_report": preprocess_report or "",
        "format_report": format_report or "",
    }
    report_json = json.dumps(report, ensure_ascii=False, separators=(",", ":"))

    def _li(items):
        return "".join("<li>%s</li>" % html.escape(x) for x in items)

    html_doc = (
        "<html><head><meta charset='utf-8'><title>審查與修訂報告</title></head><body style='font-family:serif'>"
        "<h2>審查與修訂報告</h2><p>%s</p><p>論文類型：%s｜規範來源：%s｜結束原因：%s（%s）</p><hr>"
        "<h2>終審意見</h2>%s<hr>"
        "<h2>稿件（最終版）</h2>%s<hr><h2>附錄A 逐輪審查紀錄</h2><ul>%s</ul>"
        "<h2>附錄B 最後仍未解決的意見</h2><ul>%s</ul>"
        "<h2>附錄C 最後一輪修訂對照表</h2><pre style='white-space:pre-wrap'>%s</pre>"
        "<h2>附錄D 系統提示</h2><pre style='white-space:pre-wrap'>%s\n%s\n%s</pre></body></html>"
    ) % (
        html.escape(change_summary), html.escape(paper_type or ""), html.escape(journal_source or ""),
        html.escape(decision or ""), html.escape(last_reason or ""), _opinion_html(re.sub(r"^\s*##\s*終審意見\s*\n", "", final_opinion or "") or "（未產生終審意見）"), "".join(html_parts),
        _li(report["history"]),
        _li("[%s｜%s｜%s] %s" % (u["id"], u["severity"], u["location"], u["problem"] + ("［系統註記：%s；原嚴重度 %s］" % (u["screen_note"], u.get("orig_severity", "")) if u.get("screen_note") else "")) for u in unresolved),
        html.escape(last_response_table or ""), html.escape(preprocess_report or ""),
        html.escape(format_report or ""), html.escape(last_flags or ""),
    )
    if len(report_json) > 75000:
        report_json = json.dumps({**report, "manuscript": [{"k": "p", "t": "（稿件過長，請由 final_manuscript 欄位取得全文）"}]},
                                 ensure_ascii=False, separators=(",", ":"))
    if len(html_doc) > 75000:
        html_doc = "（報告過長，HTML 版略；請使用 report_json 匯出 Word。）"
    return {"report_json": report_json, "report_html": html_doc, "change_summary": change_summary}
