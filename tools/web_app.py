#!/usr/bin/env python3
"""
期刊稿件審查與修訂：本機網頁版（僅供自己使用，只綁定 127.0.0.1）

啟動
  pip install requests python-docx
  設定 DIFY_BASE_URL 與 DIFY_API_KEY（環境變數，或在本檔旁放一個 .env 檔，內容兩行 KEY=值）
  python web_app.py
  用瀏覽器開啟 http://127.0.0.1:8000

功能：上傳稿件（與選用的期刊格式檔）→ 執行 Dify 工作流 → 顯示進度 → 下載 Word 報告
      （最後一輪修訂以紅字標示，附終審意見）。
所有檔案都存在本機的 runs/ 資料夾。Dify 金鑰只在本程式中使用，不會出現在網頁上。
"""
import html
import json
import os
import sys
import threading
import time
import uuid
from email import message_from_bytes
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_review as rr  # noqa: E402

RUNS = HERE / "runs"
RUNS.mkdir(exist_ok=True)
PORT = int(os.environ.get("PORT", "8000"))
MAX_BODY = 60 * 1024 * 1024
TYPES = [
    ("teaching_practice", "教學實踐"), ("quantitative", "量化研究"), ("qualitative", "質性研究"),
    ("theoretical", "理論／政策分析"), ("mixed", "混合方法"), ("content_analysis", "內容分析"),
    ("historical", "歷史途徑"), ("institutional_comparison", "制度比較"),
    ("cross_cultural", "跨文化研究"), ("literature_linguistics", "文學與語言研究"),
]
JOBS, LOCK = {}, threading.Lock()


def load_env_file():
    f = HERE / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def parse_multipart(content_type, body):
    msg = message_from_bytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body, policy=HTTP)
    fields, files = {}, {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        fn = part.get_filename()
        data = part.get_payload(decode=True) or b""
        if fn:
            if data:
                files[name] = (fn, data)
        else:
            fields[name] = data.decode("utf-8", "replace")
    return fields, files


def job_log(job, text):
    with LOCK:
        job["log"].append(text)


def run_job(job, fields, files):
    d = RUNS / job["id"]
    d.mkdir(parents=True, exist_ok=True)
    try:
        inputs = {"paper_type": fields.get("paper_type", "teaching_practice"),
                  "max_rounds": int(fields.get("max_rounds", 2) or 2),
                  "required_pass_rounds": int(fields.get("pass_rounds", 1) or 1),
                  "format_source": "內建預設格式", "manuscript": fields.get("manuscript_text", ""),
                  "known_gaps": fields.get("known_gaps", "")}
        user = "web-" + job["id"][:8]
        if "manuscript_file" in files:
            fn, data = files["manuscript_file"]
            (d / fn).write_bytes(data)
            suffix = Path(fn).suffix.lower()
            if suffix in rr.TEXT_EXT:
                inputs["manuscript"] = data.decode("utf-8", "replace")
            elif suffix in rr.FILE_EXT:
                job_log(job, "上傳稿件…")
                inputs["file"] = {"type": "document", "transfer_method": "local_file",
                                  "upload_file_id": rr.upload(d / fn, user)}
            else:
                raise RuntimeError("不支援的稿件格式：" + suffix)
        elif not inputs["manuscript"].strip():
            raise RuntimeError("請貼上稿件文字，或選擇稿件檔案。")
        if "format_file" in files:
            fn, data = files["format_file"]
            (d / ("format_" + fn)).write_bytes(data)
            job_log(job, "上傳期刊格式檔…")
            inputs["format_source"] = "上傳期刊格式檔"
            inputs["journal_file"] = {"type": "document", "transfer_method": "local_file",
                                      "upload_file_id": rr.upload(d / ("format_" + fn), user)}
        job_log(job, "執行工作流（依稿件長度，可能需要數分鐘）…")
        status, outputs, err = rr.run_workflow(inputs, user, log=lambda t: job_log(job, t))
        (d / "dify_outputs.json").write_text(json.dumps(outputs, ensure_ascii=False, indent=2), encoding="utf-8")
        if status != "succeeded":
            raise RuntimeError("工作流未成功（%s）：%s" % (status, err))
        report = json.loads(outputs["report_json"])
        rr.export_docx(report, str(d / "審查報告.docx"))
        if outputs.get("report_html"):
            (d / "審查報告.html").write_text(outputs["report_html"], encoding="utf-8")
        try:  # 依期刊格式（或內建預設）輸出可下載的 Word／.doc／PDF
            import export_docx as ed
            ed.build_docx(outputs.get("final_manuscript", ""), outputs.get("layout_spec", ""), str(d / "修訂稿_依期刊格式.docx"))
            for fmt in ("doc", "pdf"):
                ed._soffice_convert(str(d / "修訂稿_依期刊格式.docx"), fmt)  # 沒有安裝 LibreOffice 時自動略過
        except Exception as e:  # noqa: BLE001
            job_log(job, "修訂稿 Word 輸出失敗（不影響報告）：%s" % e)
        import re as _re
        (d / "最終稿件.txt").write_text(_re.sub(r"(?m)^#{1,6}\s+", "", outputs.get("final_manuscript", "")), encoding="utf-8")
        with LOCK:
            job["summary"] = "結束原因：%s（%s）\n%s" % (outputs.get("decision"), outputs.get("last_reason"), outputs.get("change_summary", ""))
            job["status"] = "succeeded"
        job_log(job, "完成。")
    except BaseException as e:  # noqa: BLE001（SystemExit 也要接住）
        with LOCK:
            job["status"], job["error"] = "failed", str(e) or e.__class__.__name__
        job_log(job, "失敗：" + job["error"])


PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><title>期刊稿件審查與修訂</title>
<style>
body{font-family:-apple-system,"PingFang TC","Microsoft JhengHei",sans-serif;max-width:760px;margin:30px auto;padding:0 16px;color:#222}
h1{font-size:22px} label{display:block;margin:14px 0 4px;font-weight:600} .hint{color:#666;font-size:13px;font-weight:400}
input[type=number],select,textarea{width:100%%;padding:8px;box-sizing:border-box;font-size:15px}
textarea{height:110px} button{margin-top:18px;padding:10px 22px;font-size:16px;background:#1a56db;color:#fff;border:0;border-radius:6px;cursor:pointer}
button:disabled{background:#999} #log{background:#111;color:#ddd;padding:12px;border-radius:6px;white-space:pre-wrap;font-size:13px;min-height:60px;margin-top:20px;display:none}
.warn{background:#fff3cd;padding:10px;border-radius:6px} .ok a{display:inline-block;margin:6px 12px 0 0;padding:8px 14px;background:#0a7c3a;color:#fff;border-radius:6px;text-decoration:none}
.row{display:flex;gap:12px}.row>div{flex:1}
</style></head><body>
<h1>期刊稿件審查與修訂</h1>%(warn)s
<form id="f" enctype="multipart/form-data">
<label>稿件檔案 <span class="hint">（.docx、.pdf、.md、.txt）</span></label><input type="file" name="manuscript_file" accept=".docx,.doc,.pdf,.md,.txt">
<label>或貼上稿件文字 <span class="hint">（若已選檔案，以檔案為準）</span></label><textarea name="manuscript_text"></textarea>
<label>論文類型</label><select name="paper_type">%(types)s</select>
<div class="row"><div><label>輪數上限</label><input type="number" name="max_rounds" value="2" min="1" max="8"></div>
<div><label>需連續通過輪數</label><input type="number" name="pass_rounds" value="1" min="1" max="4"></div></div>
<label>作者已知待補事項 <span class="hint">（選填，每行一項；列在此處的缺口，審查者仍會提出，但嚴重度最高為 minor，不會阻擋通過）</span></label><textarea name="known_gaps" style="height:70px" placeholder="例如：質性分析的編碼程序細節\n補充實證文獻"></textarea>
<label>期刊格式檔 <span class="hint">（選填：稿約或投稿須知，.pdf／.docx；不選則使用內建預設格式）</span></label><input type="file" name="format_file" accept=".docx,.doc,.pdf">
<button id="go" type="submit">開始審查與修訂</button></form>
<div id="log"></div><div id="result" class="ok"></div>
<script>
const f=document.getElementById('f'),go=document.getElementById('go'),log=document.getElementById('log'),res=document.getElementById('result');
f.addEventListener('submit',async e=>{e.preventDefault();go.disabled=true;res.innerHTML='';log.style.display='block';log.textContent='送出中…';
 const r=await fetch('/start',{method:'POST',body:new FormData(f)});const j=await r.json();
 if(!r.ok){log.textContent=j.error||'送出失敗';go.disabled=false;return}
 const id=j.id;const t=setInterval(async()=>{const s=await (await fetch('/status?id='+id)).json();log.textContent=s.log.join('\\n');log.scrollTop=log.scrollHeight;
  if(s.status==='succeeded'){clearInterval(t);go.disabled=false;
   res.innerHTML='<p>'+(s.summary||'').replace(/\\n/g,'<br>')+'</p><a href="/download?id='+id+'&f=docx">下載審查報告 Word</a><a href=\"/download?id='+id+'&f=ms\">下載修訂稿 Word（依期刊格式）</a><a href=\"/download?id='+id+'&f=msdoc\">.doc</a><a href=\"/download?id='+id+'&f=mspdf\">PDF</a><a href="/download?id='+id+'&f=txt">下載最終稿件（純文字）</a><a href="/download?id='+id+'&f=html">下載 HTML 報告</a><a href="/download?id='+id+'&f=json">下載 Dify 原始輸出</a>';}
  else if(s.status==='failed'){clearInterval(t);go.disabled=false;res.innerHTML='<p style="color:#b00020">失敗：'+(s.error||'')+'</p>';}},2000);});
</script></body></html>"""

FILES = {"ms": "修訂稿_依期刊格式.docx", "msdoc": "修訂稿_依期刊格式.doc", "mspdf": "修訂稿_依期刊格式.pdf", "docx": "審查報告.docx", "html": "審查報告.html", "txt": "最終稿件.txt", "json": "dify_outputs.json"}
MIME = {"ms": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "msdoc": "application/msword", "mspdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "html": "text/html; charset=utf-8",
        "txt": "text/plain; charset=utf-8", "json": "application/json"}


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):  # 安靜
        pass

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            ok = bool(os.environ.get("DIFY_BASE_URL") and os.environ.get("DIFY_API_KEY"))
            warn = "" if ok else '<p class="warn">尚未設定 DIFY_BASE_URL 與 DIFY_API_KEY，無法執行。請參考 web_app.py 開頭的說明。</p>'
            types = "".join('<option value="%s">%s（%s）</option>' % (k, v, k) for k, v in TYPES)
            return self._send(200, PAGE % {"warn": warn, "types": types})
        if u.path == "/status":
            job = JOBS.get((q.get("id") or [""])[0])
            if not job:
                return self._send(404, json.dumps({"error": "找不到工作"}), "application/json")
            with LOCK:
                return self._send(200, json.dumps({k: job.get(k) for k in ("status", "log", "error", "summary")}, ensure_ascii=False), "application/json")
        if u.path == "/download":
            jid, kind = (q.get("id") or [""])[0], (q.get("f") or [""])[0]
            if jid not in JOBS or kind not in FILES:
                return self._send(404, "not found", "text/plain")
            f = RUNS / jid / FILES[kind]
            if not f.exists():
                return self._send(404, "檔案尚未產生", "text/plain; charset=utf-8")
            name = {"ms": "修訂稿_依期刊格式.docx", "msdoc": "修訂稿_依期刊格式.doc", "mspdf": "修訂稿_依期刊格式.pdf", "docx": "審查報告.docx", "html": "審查報告.html", "txt": "最終稿件.txt", "json": "dify_outputs.json"}[kind]
            from urllib.parse import quote
            return self._send(200, f.read_bytes(), MIME[kind], {"Content-Disposition": "attachment; filename*=UTF-8''" + quote(name)})
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        if urlparse(self.path).path != "/start":
            return self._send(404, "not found", "text/plain")
        if not (os.environ.get("DIFY_BASE_URL") and os.environ.get("DIFY_API_KEY")):
            return self._send(400, json.dumps({"error": "尚未設定 DIFY_BASE_URL 與 DIFY_API_KEY"}), "application/json")
        n = int(self.headers.get("Content-Length", "0"))
        if n <= 0 or n > MAX_BODY:
            return self._send(400, json.dumps({"error": "請求大小不符（上限 60 MB）"}), "application/json")
        ctype = self.headers.get("Content-Type", "")
        raw = self.rfile.read(n)
        if ctype.startswith("application/x-www-form-urlencoded"):
            fields = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
            files = {}
        else:
            fields, files = parse_multipart(ctype, raw)
        job = {"id": uuid.uuid4().hex[:12], "status": "running", "log": ["已收到，開始處理…"], "error": None, "summary": None}
        with LOCK:
            JOBS[job["id"]] = job
        threading.Thread(target=run_job, args=(job, fields, files), daemon=True).start()
        self._send(200, json.dumps({"id": job["id"]}), "application/json")


def main():
    load_env_file()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print("網頁版已啟動：http://127.0.0.1:%d （按 Ctrl+C 結束）" % PORT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
