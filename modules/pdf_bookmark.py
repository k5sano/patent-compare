# -*- coding: utf-8 -*-
"""
PDF にアウトライン（しおり／ブックマーク）を付与する。
PyMuPDF (fitz) を使用。
"""

import fitz


def apply_toc(doc, bookmarks):
    """開いている fitz.Document に TOC を適用する（in-place）。

    Args:
        doc: fitz.Document
        bookmarks: [{"title": "...", "page": 3}, ...]  page は 1-indexed

    Returns:
        適用された TOC エントリ数
    """
    page_count = doc.page_count
    toc = []
    for bm in bookmarks:
        page = int(bm.get("page", 1))
        if page < 1:
            page = 1
        if page > page_count:
            page = page_count
        title = str(bm.get("title", ""))[:200] or "(no title)"
        toc.append([1, title, page])
    doc.set_toc(toc)
    return len(toc)


def add_bookmarks(src_pdf: str, out_pdf: str, bookmarks):
    """ファイル間で完結するブックマーク付与"""
    doc = fitz.open(src_pdf)
    n = apply_toc(doc, bookmarks)
    doc.save(out_pdf, garbage=3, deflate=True)
    doc.close()
    return n
