from __future__ import annotations

import json
from pathlib import Path

from services.evidence_index_service import build_evidence_index, search_evidence_index


def _write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def test_build_evidence_index_flattens_paragraphs_and_tables(isolated_project_root):
    case = isolated_project_root / "cases" / "C1"
    citation = {
        "paragraphs": [
            {"id": "0105", "text": "比較例7の説明。"},
            {"id": "0106", "text": "比較例8はブテン-エチレン共重合体を含む。"},
        ],
        "tables": [{
            "caption_label": "表2",
            "page_num": 43,
            "headers": ["項目", "比較例8"],
            "rows": [{"cells": ["ヒートシール層", "3μm"]}],
        }],
    }
    _write(case / "citations" / "D1.json", citation)

    index = build_evidence_index("C1", "D1")

    assert index["paragraph_count"] == 2
    assert index["table_count"] == 1
    assert (case / "indexes" / "evidence" / "D1.json").exists()
    assert any(c["chunk_id"] == "T:1" and "比較例8" in c["text"] for c in index["chunks"])


def test_search_evidence_index_returns_paragraph_context_and_table(isolated_project_root):
    case = isolated_project_root / "cases" / "C1"
    citation = {
        "paragraphs": [
            {"id": "0105", "text": "前段落。"},
            {"id": "0106", "text": "比較例8はブテン含有率19wt%のプロピレン-ブテン共重合体を含む。"},
            {"id": "0107", "text": "表1、表2に結果を示す。"},
        ],
        "tables": [{
            "caption_label": "表2",
            "page_num": 43,
            "headers": ["項目", "比較例8"],
            "rows": [
                {"cells": ["総厚み", "25μm"]},
                {"cells": ["ヒートシール層", "3μm"]},
            ],
        }],
    }
    _write(case / "citations" / "D1.json", citation)

    result = search_evidence_index(
        "C1",
        "D1",
        query_text="比較例8と表2。ヒートシール層3μmを確認",
        terms=["比較例8", "表2", "3μm"],
    )

    assert [p["para_no"] for p in result["paragraphs"]][:3] == ["0105", "0106", "0107"]
    assert result["tables"][0]["label"] == "表2"
    assert "ヒートシール層" in result["tables"][0]["text"]
