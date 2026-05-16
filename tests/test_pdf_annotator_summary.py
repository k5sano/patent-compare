from __future__ import annotations

import fitz

from modules.pdf_annotator import annotate_citation_pdf, _toc_dest


def test_annotated_pdf_appends_summary_page_and_precise_bookmark(tmp_path):
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
        summary_text = annotated[-1].get_text()
        assert "注釈PDFサマリー" in summary_text
        assert "Y: 他文献との組合せ" in summary_text
        assert "本願請求項で埋まっていない構成" in summary_text
        assert "1B ×" in summary_text

        toc = annotated.get_toc(simple=False)
        para_entry = next(e for e in toc if "1A ○ 【0001】" in e[1])
        dest = para_entry[3]
        assert dest["page"] == 0  # source PDF stays at the original first page
        assert dest["to"].y > 200
        summary_entry = next(e for e in toc if "注釈サマリー:" in e[1])
        assert summary_entry[2] == 2
    finally:
        annotated.close()


def test_claim_reference_bookmark_keeps_claim_number_and_jumps_to_claim(tmp_path):
    src = tmp_path / "src_claim.pdf"
    out = tmp_path / "out_claim.pdf"

    doc = fitz.open()
    page1 = doc.new_page(width=595, height=842)
    page1.insert_text(
        fitz.Point(72, 120),
        "【請求項１】テスト請求項。",
        fontsize=12,
        fontname="japan",
    )
    page2 = doc.new_page(width=595, height=842)
    page2.insert_text(
        fitz.Point(72, 310),
        "【請求項７】発泡断熱紙容器用シートに関するテスト請求項。",
        fontsize=12,
        fontname="japan",
    )
    doc.save(src)
    doc.close()

    response = {
        "document_id": "JPCLAIM",
        "overall_summary": "summary",
        "category_suggestion": "Y",
        "comparisons": [
            {
                "requirement_id": "1A",
                "judgment": "○",
                "cited_location": "CL7",
                "judgment_reason": "請求項7に記載あり",
            },
        ],
    }
    citation = {
        "patent_number": "JPCLAIM",
        "paragraphs": [],
    }

    annotate_citation_pdf(src, out, response, citation)

    annotated = fitz.open(out)
    try:
        toc = annotated.get_toc(simple=False)
        claim_entry = next(e for e in toc if "1A ○ 請求項7" in e[1])
        assert "1A ○ 請求項 (p.1)" not in [e[1] for e in toc]
        assert claim_entry[2] == 2
        assert claim_entry[3]["page"] == 1
        assert claim_entry[3]["to"].y > 250
    finally:
        annotated.close()


def test_sub_claim_bookmark_prefers_requirement_id(tmp_path):
    src = tmp_path / "src_sub.pdf"
    out = tmp_path / "out_sub.pdf"

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(
        fitz.Point(72, 260),
        "【００７８】内層にLBKP70質量％とマシンブローク30質量％を配合した。",
        fontsize=12,
        fontname="japan",
    )
    doc.save(src)
    doc.close()

    response = {
        "document_id": "JPSUB",
        "overall_summary": "summary",
        "category_suggestion": "Y",
        "comparisons": [],
        "sub_claims": [
            {
                "claim_number": 5,
                "requirement_id": "5A",
                "judgment": "○",
                "cited_location": "78",
                "judgment_reason": "配合率が範囲内",
            }
        ],
    }
    citation = {
        "patent_number": "JPSUB",
        "paragraphs": [{"id": "0078", "page": 1, "section": "実施例", "text": "内層にLBKP70質量％"}],
    }

    annotate_citation_pdf(src, out, response, citation)

    annotated = fitz.open(out)
    try:
        titles = [e[1] for e in annotated.get_toc(simple=False)]
        assert "5A ○ 【0078】 (p.1)" in titles
        assert "請求項5 ○ 【0078】 (p.1)" not in titles
    finally:
        annotated.close()


def test_bookmarks_follow_segments_claim_row_order(tmp_path):
    src = tmp_path / "src_order.pdf"
    out = tmp_path / "out_order.pdf"

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for y, n in [(120, "0001"), (180, "0002"), (240, "0003"), (300, "0004")]:
        page.insert_text(
            fitz.Point(72, y),
            f"【{n}】段落{n}",
            fontsize=12,
            fontname="japan",
        )
    doc.save(src)
    doc.close()

    response = {
        "document_id": "JPORDER",
        "overall_summary": "summary",
        "category_suggestion": "Y",
        # 応答側はわざと順不同。TOC は segments 順に直す。
        "comparisons": [
            {"requirement_id": "9A", "judgment": "△", "cited_location": "4"},
            {"requirement_id": "1A", "judgment": "○", "cited_location": "1"},
            {"requirement_id": "2A", "judgment": "○", "cited_location": "2"},
        ],
        "sub_claims": [
            {"claim_number": 5, "requirement_id": "5A", "judgment": "×", "cited_location": ""},
        ],
    }
    citation = {
        "patent_number": "JPORDER",
        "paragraphs": [
            {"id": "0001", "page": 1, "section": "詳細", "text": "段落1"},
            {"id": "0002", "page": 1, "section": "詳細", "text": "段落2"},
            {"id": "0004", "page": 1, "section": "詳細", "text": "段落4"},
        ],
    }
    segments = [
        {"claim_number": 1, "is_independent": True, "segments": [{"id": "1A"}]},
        {"claim_number": 2, "is_independent": True, "segments": [{"id": "2A"}]},
        {"claim_number": 5, "is_independent": False, "segments": [{"id": "5A"}]},
        {"claim_number": 9, "is_independent": True, "segments": [{"id": "9A"}]},
    ]

    annotate_citation_pdf(src, out, response, citation, segments=segments)

    annotated = fitz.open(out)
    try:
        titles = [e[1] for e in annotated.get_toc(simple=False)]
        filtered = [
            t for t in titles
            if t.startswith(("1A ", "2A ", "5A ", "9A "))
        ]
        assert filtered == [
            "1A ○ 【0001】 (p.1)",
            "2A ○ 【0002】 (p.1)",
            "5A × (不明)",
            "9A △ 【0004】 (p.1)",
        ]
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
