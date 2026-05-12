from __future__ import annotations

import fitz

from modules.pdf_bookmark import apply_toc
from services.case_service import _enrich_hongan_bookmarks_with_positions


def test_apply_toc_uses_y_coordinate_destination():
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    try:
        count = apply_toc(
            doc,
            [{"title": "段落位置", "page": 1, "y": 320}],
            preserve_existing=False,
        )

        assert count == 1
        toc = doc.get_toc(simple=False)
        dest = toc[0][3]
        assert dest["page"] == 0
        assert dest["to"].y > 250
    finally:
        doc.close()


def test_hongan_bookmark_positions_resolve_paragraph_marker():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(
        fitz.Point(72, 360),
        "【０００７】本願段落テスト。",
        fontsize=12,
        fontname="japan",
    )
    try:
        bookmarks = [{"title": "1A【0007】", "page": 1, "para_id": "0007"}]

        enriched = _enrich_hongan_bookmarks_with_positions(doc, bookmarks)

        assert enriched[0]["page"] == 1
        assert enriched[0]["y"] > 330
    finally:
        doc.close()
