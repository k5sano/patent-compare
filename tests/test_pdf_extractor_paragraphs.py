"""pdf_extractor paragraph metadata tests."""
from __future__ import annotations

from modules.pdf_extractor import parse_paragraphs_en


def test_parse_wo_without_paragraph_numbers_adds_page_line_metadata():
    page_text = (
        "EXAMPLES\n"
        "Composition A was prepared.\n"
        "The composition contained guanyl cysteine.\n"
        "The pH was adjusted.\n\n"
        "INDUSTRIAL APPLICABILITY\n"
        "The composition can be used for hair treatment.\n"
    )
    pages = [{"page": 1, "text": page_text}]

    paragraphs = parse_paragraphs_en(page_text, pages, fmt="WO")

    hit = next(p for p in paragraphs if "guanyl cysteine" in p["text"])
    assert hit["has_paragraph_number"] is False
    assert hit["page"] == 1
    assert hit["line_start"] == 1
    assert hit["line_end"] == 4
