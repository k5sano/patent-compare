from pathlib import Path

import fitz
import requests

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


def test_find_ocr_table_pages_prefers_hash_table_markers(monkeypatch):
    def fake_extract_text_from_pdf(_path, **_kwargs):
        return [
            {"page": 1, "text": "上記結果を 表 1 、 表 2 に示す。\n[0108] [#1] 実施例表"},
            {"page": 2, "text": "WO page\n[#2] 比較例表\n表 1 及び 表 2 の注記"},
        ]

    import modules.pdf_extractor as pdf_extractor
    monkeypatch.setattr(pdf_extractor, "extract_text_from_pdf", fake_extract_text_from_pdf)

    pages = table_extractor.find_ocr_table_pages("dummy.pdf")

    assert [(p["page_num"], p["caption_label"]) for p in pages] == [
        (1, "表1"),
        (2, "表2"),
    ]


def test_table_llm_workers_are_model_aware(monkeypatch):
    monkeypatch.delenv("PATENT_COMPARE_QWEN_VL_WORKERS", raising=False)
    monkeypatch.delenv("PATENT_COMPARE_TABLE_LLM_WORKERS", raising=False)

    assert table_extractor._table_llm_max_workers(5, "qwen2.5-vl") == 1
    assert table_extractor._table_llm_max_workers(5, "qwen2.5vl:7b") == 1
    assert table_extractor._table_llm_max_workers(5, "sonnet") == 4

    monkeypatch.setenv("PATENT_COMPARE_QWEN_VL_WORKERS", "2")
    monkeypatch.setenv("PATENT_COMPARE_TABLE_LLM_WORKERS", "6")
    assert table_extractor._table_llm_max_workers(5, "qwen2.5-vl") == 2
    assert table_extractor._table_llm_max_workers(5, "codex-opus") == 5


def test_extract_tables_from_image_records_retries_ssl_without_verify(monkeypatch, tmp_path):
    calls = []

    class FakeResp:
        content = b"fake-png"

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        calls.append(kwargs.get("verify"))
        if len(calls) == 1:
            raise requests.exceptions.SSLError("bad local CA")
        return FakeResp()

    def fake_llm(image_path, *, model, caption_hint=None, effort="low", **kwargs):
        assert Path(image_path).exists()
        assert Path(image_path).read_bytes() == b"fake-png"
        assert caption_hint == "表１"
        return ExtractedTable(
            is_table=True,
            image_path=str(image_path),
            title="表１",
            headers=["項目", "値"],
            rows=[{"cells": ["Sa", "1.0"]}],
            model=model,
        )

    monkeypatch.setattr(table_extractor.requests, "get", fake_get)
    monkeypatch.setattr(table_extractor, "extract_table_via_claude", fake_llm)

    summary = table_extractor.extract_tables_from_image_records(
        [{"src": "https://patentimages.storage.googleapis.com/x/table.png", "label": "表１"}],
        tmp_path / "out",
        "特開2024-144627",
        model="sonnet",
    )

    assert calls[-1] is False
    assert summary["n_error"] == 0
    assert summary["n_table"] == 1
