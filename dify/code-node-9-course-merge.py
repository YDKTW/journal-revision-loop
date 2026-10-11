# -*- coding: utf-8 -*-
# 程式節點 9：合併「貼上的授課計畫書」與「上傳檔擷取的授課計畫書」。貼上的文字優先。


def main(course_pasted: str = "", course_extracted: str = "") -> dict:
    return {"course_text": ((course_pasted or "").strip() or (course_extracted or "").strip())}
