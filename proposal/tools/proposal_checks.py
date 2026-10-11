#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
教學實踐研究計畫書「程式檢查」：不靠模型，先把能機械判斷的事實算出來，
再交給審查者（模型）判斷嚴重度。輸出 Markdown 與 JSON。

檢查項目
  1. 頁數：總頁數（不含封面）≤ 25（一年期）／40（多年期）；計畫主持人部分 ≤ 5 頁
  2. 章節：官方指定章節是否齊全、編號是否重複或錯位
  3. 授課計畫書 × 計畫書 一致性：課程名稱變體、授課對象與年級、人數、課程屬性、學期、成績配分
  4. 歷年重複：與使用者提供的歷年計畫書逐字相同的連續片段（自我抄襲風險）
  5. 引註：正文引註 × 參考文獻清單（候選，需人工確認）
  6. 合規：AI 使用聲明、知情同意、倫理審查、去識別化、心理資訊關鍵字
  7. 用詞：同一概念的多種寫法、人名近似寫法、封面日期缺漏
  8. 研究問題與假設清單（供審查者建立一致性矩陣）

用法
  python proposal_checks.py --proposal 計畫書.pdf --course 授課計畫書.pdf \
      --history 114計畫書.pdf 113計畫書.pdf --names 葉定國 田光祐 -o report.md --json report.json

需要 pdftotext（poppler）；沒有時退回 pypdf。Word 檔需要 LibreOffice。

注意：檢查邏輯集中在 dify/code-node-7-proposal-checks.py（Dify 程式節點與本機共用同一份），這裡只負責讀 PDF／Word、算頁數與歷年比對。
"""
import argparse
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

_CORE_PATH = Path(__file__).resolve().parents[2] / "dify" / "code-node-7-proposal-checks.py"
_spec = importlib.util.spec_from_file_location("proposal_core", _CORE_PATH)
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)


def _to_pdf(path):
    """Word 檔先轉成 PDF（才有「頁」的資訊）；需要 LibreOffice。回傳 PDF 路徑。"""
    import tempfile
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        raise RuntimeError("讀取 Word 檔需要 LibreOffice（soffice）；請先另存成 PDF 再檢查。")
    out = tempfile.mkdtemp()
    subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", out, str(path)], check=True, capture_output=True, timeout=240)
    pdf = Path(out) / (Path(path).stem + ".pdf")
    if not pdf.exists():
        raise RuntimeError("Word 轉 PDF 失敗：" + str(path))
    return str(pdf)


def read_pdf(path):
    """回傳 (全文, 每頁文字清單)。支援 .pdf；.docx／.doc 會先轉成 PDF（頁數以 LibreOffice 排版為準，可能與 Word 略有差異）。"""
    path = str(path)
    if path.lower().endswith((".docx", ".doc")):
        path = _to_pdf(path)
    text = ""
    if shutil.which("pdftotext"):
        r = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True)
        text = r.stdout.decode("utf-8", errors="replace")
    else:
        try:
            from pypdf import PdfReader
            text = "\f".join((p.extract_text() or "") for p in PdfReader(path).pages)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError("無法讀取 PDF（需要 pdftotext 或 pypdf）：%s" % e)
    pages = text.split("\f")
    while pages and not pages[-1].strip():
        pages.pop()
    return text, pages


def run(proposal, course=None, history=None, names=None, years=1):
    ptxt, ppages = read_pdf(proposal)
    ctxt = read_pdf(course)[0] if course else ""
    hist = [(Path(h).name, read_pdf(h)[0]) for h in (history or [])]
    return core.run_checks(ptxt, ctxt, hist, (names or core.detect_names(ptxt)), ppages, years)


def main():
    ap = argparse.ArgumentParser(description="教學實踐研究計畫書程式檢查")
    ap.add_argument("--proposal", required=True, help="計畫書 PDF 或 Word")
    ap.add_argument("--course", help="授課計畫書 PDF 或 Word")
    ap.add_argument("--history", nargs="*", default=[], help="歷年計畫書 PDF（比對自我重複）")
    ap.add_argument("--names", nargs="*", default=[], help="計畫主持人與協同主持人姓名（檢查近似寫法）")
    ap.add_argument("--years", type=int, default=1, help="1＝一年期（25 頁），2＝多年期（40 頁）")
    ap.add_argument("-o", "--out", default="proposal_check.md")
    ap.add_argument("--json", help="另存 JSON")
    a = ap.parse_args()
    rep = run(a.proposal, a.course, a.history, a.names, a.years)
    md = "# 教學實踐研究計畫書：程式檢查結果\n\n> 這些是機械可判斷的事實；是否構成問題與嚴重度，由審查者依審查標準判斷。引註與一致性為候選清單，需人工確認。\n\n" + core.render(rep)
    Path(a.out).write_text(md, encoding="utf-8")
    if a.json:
        Path(a.json).write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=list), encoding="utf-8")
    print("已產生：", a.out)


if __name__ == "__main__":
    main()
