from __future__ import annotations

import fitz

from modules.pdf_annotator import annotate_citation_pdf, _toc_dest


def test_annotated_pdf_adds_summary_page_and_precise_bookmark(tmp_path):
    src = tmp_path / "src.pdf"
    out = tmp_path / "out.pdf"

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(
        fitz.Point(72, 250),
        "【０００１】段落テスト。グアニルシステインを含む。",
        fontsize=12,
        fontname="japan",
    )
    doc.save(src)
    doc.close()

    response = {
        "document_id": "JPTEST",
        "overall_summary": "この文献は毛髪変形化粧料に関する文献である。",
        "category_suggestion": "Y",
        "rejection_relevance": "主引例候補。",
        "comparisons": [
            {
                "requirement_id": "1A",
                "judgment": "○",
                "cited_location": "1",
                "judgment_reason": "段落に記載あり",
            },
            {
                "requirement_id": "1B",
                "judgment": "×",
                "cited_location": "",
                "judgment_reason": "シリコーン油が明記されない",
            },
        ],
    }
    citation = {
        "patent_number": "JPTEST",
        "patent_title": "毛髪変形化粧料",
        "applicant": "テスト株式会社",
        "paragraphs": [{"id": "0001", "page": 1, "section": "詳細", "text": "段落テスト"}],
    }

    result = annotate_citation_pdf(src, out, response, citation)
    assert result["bookmarks"] >= 4

    annotated = fitz.open(out)
    try:
        assert annotated.page_count == 2
        summary_text = annotated[0].get_text()
        assert "注釈PDFサマリー" in summary_text
        assert "Y: 他文献との組合せ" in summary_text
        assert "本願請求項で埋まっていない構成" in summary_text
        assert "1B ×" in summary_text

        toc = annotated.get_toc(simple=False)
        para_entry = next(e for e in toc if "1A ○ 【0001】" in e[1])
        dest = para_entry[3]
        assert dest["page"] == 1  # summary page is inserted before the source PDF
        assert dest["to"].y > 200
    finally:
        annotated.close()


def test_migrates_only_user_bookmarks_under_legacy_tab(tmp_path):
    src = tmp_path / "src.pdf"
    old = tmp_path / "old.pdf"
    out = tmp_path / "out.pdf"

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(
        fitz.Point(72, 250),
        "【０００１】段落テスト。グアニルシステインを含む。",
        fontsize=12,
        fontname="japan",
    )
    doc.save(src)
    doc.close()

    response = {
        "document_id": "JPTEST",
        "overall_summary": "summary",
        "category_suggestion": "A",
        "comparisons": [
            {
                "requirement_id": "1A",
                "judgment": "○",
                "cited_location": "1",
                "judgment_reason": "段落に記載あり",
            },
        ],
    }
    citation = {
        "patent_number": "JPTEST",
        "patent_title": "テスト",
        "paragraphs": [{"id": "0001", "page": 1, "section": "詳細", "text": "段落テスト"}],
    }

    annotate_citation_pdf(src, old, response, citation)
    old_doc = fitz.open(old)
    try:
        toc = old_doc.get_toc(simple=False)
        toc.append([1, "ユーザーメモ", 2, _toc_dest(2, 120)])
        old_doc.set_toc(toc)
        old_doc.saveIncr()
    finally:
        old_doc.close()

    result = annotate_citation_pdf(
        src,
        out,
        response,
        citation,
        migrate_bookmarks_from=old,
    )

    assert result["migrated_bookmarks"] == 1

    new_doc = fitz.open(out)
    try:
        titles = [entry[1] for entry in new_doc.get_toc(simple=False)]
        assert "旧版から移行" in titles
        assert "ユーザーメモ" in titles
        migrated_index = titles.index("旧版から移行")
        user_index = titles.index("ユーザーメモ")
        assert user_index > migrated_index
        assert titles.count("1A ○ 【0001】 (p.1)") == 1
    finally:
        new_doc.close()
