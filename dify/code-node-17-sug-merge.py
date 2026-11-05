# -*- coding: utf-8 -*-
# Dify「程式碼執行」節點 17：把理論補強建議以紅字區塊插入稿件，並附紅字建議參考文獻
# 輸入：manuscript (String)、sug_json (String)、c1..c6 (String：Crossref 回應 body)
# 輸出：annotated_manuscript (String，標記版)、annotated_html (String，紅字 HTML)、suggestion_report (String：給人看的摘要)
# 書目原則：只使用檢索結果與 Crossref 回傳的欄位，不補寫任何欄位；Crossref 標題相似度 >= 0.85 才視為已核對。
import difflib
import html
import json
import re

CJK = re.compile(r"[㐀-鿿]")


def _n(s):
    return re.sub(r"[\W_]+", "", (s or "").lower())


def _crossref_match(body, title):
    try:
        d = json.loads(body or "")
        items = d["message"]["items"]
    except Exception:
        return None
    best, score = None, 0.0
    for it in items:
        tt = " ".join(it.get("title") or [])
        r = difflib.SequenceMatcher(None, _n(tt), _n(title)).ratio()
        if r > score:
            best, score = it, r
    return best if best is not None and score >= 0.85 else None


def _name(a):
    fam, giv = (a.get("family") or "").strip(), (a.get("given") or "").strip()
    if not fam and not giv:
        return ""
    if CJK.search(fam + giv):
        return fam + giv
    # Crossref 偶有「family 含縮寫」「全大寫」等資料：把 family 內的縮寫移到 given，全大寫改為首字大寫
    toks = fam.replace(",", " ").split()
    ini_t = [t for t in toks if re.fullmatch(r"[A-Za-z]\.?", t)]
    fam_t = [t for t in toks if t not in ini_t]
    if fam_t and ini_t:
        fam, giv = " ".join(fam_t), (" ".join(ini_t) + " " + giv).strip()
    elif len(toks) > 1:
        fam = " ".join(toks)
    if fam.isupper() and len(fam) > 1:
        fam = fam.capitalize()
    giv = giv.replace(",", " ")
    ini = " ".join(p[0].upper() + "." for p in re.split(r"[\s\-.]+", giv) if p)
    return (fam + (", " + ini if ini else "")).strip(", ")


def _case(s, kind):
    if s and s.isupper():
        if kind == "container":
            return re.sub(r" (Of|And|The|For|In|On|To|A|An)\b", lambda m: m.group(0).lower(), s.title())
        return s[0] + s[1:].lower()
    return s


def _fmt_crossref(it):
    raw = it.get("author") or []
    au = [x for x in (_name(a) for a in raw[:6]) if x]
    year = ""
    try:
        year = str(it["issued"]["date-parts"][0][0])
    except Exception:
        pass
    title = " ".join(it.get("title") or [])
    cont = " ".join(it.get("container-title") or [])
    vol, iss, pg, doi = it.get("volume"), it.get("issue"), it.get("page"), it.get("DOI")
    zh = bool(CJK.search(title) or (au and CJK.search(au[0])))
    if zh:
        s = ("、".join(au) or "（作者欄位缺）") + ("等" if len(raw) > 6 else "")
        s += "（%s）。%s。" % (year or "年份缺", title)
        if cont:
            s += cont + (("，" + str(vol) + ("(%s)" % iss if iss else "")) if vol else "") + (("，" + str(pg)) if pg else "") + "。"
    else:
        names = (", ".join(au[:-1]) + ", & " + au[-1] if len(au) > 1 else au[0]) if au else "（作者欄位缺）"
        if len(raw) > 6:
            names += ", et al."
        title = _case(title, "title")
        s = "%s (%s). %s." % (names, year or "年份缺", title.rstrip("."))
        if cont:
            s += " " + _case(cont, "container")
            if vol:
                s += ", " + str(vol) + ("(%s)" % iss if iss else "")
            if pg:
                s += ", " + str(pg)
            s += "."
    if doi:
        s += " https://doi.org/" + doi
    return s


def main(manuscript: str = "", sug_json: str = "", c1: str = "", c2: str = "", c3: str = "", c4: str = "", c5: str = "", c6: str = "") -> dict:
    try:
        data = json.loads(sug_json or "{}")
    except Exception:
        data = {}
    sugs = data.get("suggestions") or []
    cands = data.get("slot_cands") or []
    if not sugs:
        return {"annotated_manuscript": "", "annotated_html": "", "suggestion_report": "沒有可標註的理論補強建議。"}
    bodies = [c1, c2, c3, c4, c5, c6]
    refs, verified = [], 0
    msn = _n(manuscript)
    for i, c in enumerate(cands):
        it = _crossref_match(bodies[i], c.get("title", ""))
        if it is not None:
            doi = (it.get("DOI") or "").lower()
            if (doi and doi in manuscript.lower()) or (len(_n(c.get("title"))) >= 12 and _n(c.get("title")) in msn):
                refs.append("R%d　（此文獻稿中已列於參考文獻，毋須重複新增；上列建議可直接對照稿中既有條目）" % (i + 1))
                continue
            verified += 1
            refs.append("R%d　%s　（已以 Crossref 核對書目欄位；仍請對照原文）" % (i + 1, _fmt_crossref(it)))
        else:
            refs.append("R%d　《%s》｜%s｜%s　（僅依 Google Scholar 檢索結果，書目欄位不完整、未經 Crossref 核對，請作者逐項補全並對照原文）" %
                        (i + 1, (c.get("title", "") or "").strip("《》 "), c.get("pub") or "出處不明", c.get("link") or "無連結"))
    lines = manuscript.split("\n")
    inserts = {}
    for k, s in enumerate(sugs, 1):
        idx = next((j for j, ln in enumerate(lines) if s["anchor"] in ln), None)
        if idx is None:
            continue
        txt = "【理論補強建議 S%d｜待作者評估是否採用】可考慮引入「%s」" % (k, s["concept"])
        if s.get("link"):
            txt += "；與稿中發現的連結：" + s["link"]
        if s.get("reason"):
            txt += "；理由：" + s["reason"]
        if s.get("options"):
            txt += "；選項：" + s["options"]
        if s.get("slots"):
            txt += "；建議文獻：" + "、".join("R%d" % x for x in s["slots"])
        txt += "；" + (s.get("verify") or "涉及稿外知識，請作者查證") + "。"
        inserts.setdefault(idx, []).append(txt)
    out, red = [], []
    for j, ln in enumerate(lines):
        out.append(ln)
        red.append(False)
        for t in inserts.get(j, []):
            out.append("")
            red.append(False)
            out.append(t)
            red.append(True)
    tail = ["", "【建議參考文獻（紅字：由系統依檢索結果提出，非稿件原有；請作者逐筆核對後決定是否引用，不要直接複製）】"] + refs
    out += tail
    red += [False, True] + [True] * len(refs)
    ann = "\n".join(out)
    body = "".join(('<p style="color:#c00000">%s</p>' if r else "<p>%s</p>") % html.escape(l) if l.strip() else "" for l, r in zip(out, red))
    page = "<html><head><meta charset='utf-8'><title>標註版（理論補強建議）</title></head><body style='font-family:serif'>%s</body></html>" % body
    rep = "理論補強建議 %d 則（%d 則已插入稿件）；建議文獻 %d 筆，其中 %d 筆已以 Crossref 核對書目。" % (len(sugs), len(inserts), len(refs), verified)
    return {"annotated_manuscript": ann, "annotated_html": page, "suggestion_report": rep}
