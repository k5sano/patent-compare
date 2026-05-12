from pathlib import Path

import fitz

from services import table_extractor
from services.table_extractor import ExtractedTable


def _make_vector_table_pdf(path: Path):
    doc = fitz.open()
    page = doc.new_page(width=420, height=300)
    page.insert_text((50, 30), "Table 1")
    x0, y0 = 50, 55
    cw, ch = 85, 24
    rows = [
        ["Component", "Example1", "Comp1"],
        ["A", "1.0", "0"],
        ["B", "2.0", "1.0"],
    ]
    for r in range(len(rows) + 1):
        page.draw_line((x0, y0 + r * ch), (x0 + len(rows[0]) * cw, y0 + r * ch))
    for c in range(len(rows[0]) + 1):
        page.draw_line((x0 + c * cw, y0), (x0 + c * cw, y0 + len(rows) * ch))
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            page.insert_text((x0 + c * cw + 5, y0 + r * ch + 16), text)
    doc.save(path)
    doc.close()


def _make_caption_only_pdf(path: Path):
    doc = fitz.open()
    page = doc.new_page(width=420, height=320)
    page.insert_text((50, 40), "Table 1")
    page.insert_text((50, 70), "Component    Example1    Comp1")
    page.insert_text((50, 92), "A            1.0         0")
    page.insert_text((50, 114), "B            2.0         1.0")
    doc.save(path)
    doc.close()


def test_extract_vector_tables_from_pdf(tmp_path):
    pdf = tmp_path / "vector.pdf"
    _make_vector_table_pdf(pdf)

    tables = table_extractor.extract_vector_tables(pdf)

    assert len(tables) == 1
    assert tables[0]["source"] == "vector"
    assert tables[0]["page_num"] == 1
    assert tables[0]["headers"] == ["Component", "Example1", "Comp1"]
    assert tables[0]["rows"][0]["cells"] == ["A", "1.0", "0"]


def test_extract_tables_from_pdf_uses_vector_without_llm(monkeypatch, tmp_path):
    pdf = tmp_path / "vector.pdf"
    out = tmp_path / "out"
    _make_vector_table_pdf(pdf)

    def fail_llm(*args, **kwargs):
        raise AssertionError("vector-covered page should not call LLM")

    monkeypatch.setattr(table_extractor, "extract_table_via_claude", fail_llm)
    summary = table_extractor.extract_tables_from_pdf(pdf, out)

    assert summary["vector_tables_count"] == 1
    assert summary["candidates_targeted"] == 0
    assert summary["n_table"] == 1
    assert summary["missing_table_references"] == []
    assert (out / "tables.json").exists()


def test_extract_tables_from_pdf_crops_caption_page(monkeypatch, tmp_path):
    pdf = tmp_path / "caption.pdf"
    out = tmp_path / "out"
    _make_caption_only_pdf(pdf)

    def fake_llm(image_path, *, model, caption_hint=None, effort="low", **kwargs):
        assert Path(image_path).exists()
        assert caption_hint == "Table 1"
        return ExtractedTable(
            is_table=True,
            image_path=str(image_path),
            title=caption_hint,
            headers=["Component", "Example1", "Comp1"],
            rows=[{"cells": ["A", "1.0", "0"]}],
            duration_ms=10,
            model=model,
        )

    monkeypatch.setattr(table_extractor, "extract_table_via_claude", fake_llm)
    summary = table_extractor.extract_tables_from_pdf(pdf, out)

    assert summary["vector_tables_count"] == 0
    assert summary["crop_candidates"] == 1
    assert summary["candidates_targeted"] == 1
    assert summary["n_table"] == 1
    assert summary["tables"][0]["source"] == "crop"
    assert summary["missing_table_references"] == []
