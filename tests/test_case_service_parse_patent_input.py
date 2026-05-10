"""case_service._parse_patent_input の書式テスト。"""
from __future__ import annotations

import pytest

from services.case_service import _parse_patent_input


@pytest.mark.parametrize("raw,expected", [
    ("特願2024-12345", {
        "kind": "application",
        "case_id": "wa-2024-012345",
        "application_number": "特願2024-012345",
        "patent_number": "",
        "jp_id": "",
    }),
    ("特開2024-533284号公報", {
        "kind": "publication",
        "case_id": "2024-533284",
        "patent_number": "特開2024-533284",
        "application_number": "",
        "jp_id": "JP2024533284A",
    }),
    ("特許第7250676号", {
        "kind": "publication",
        "case_id": "jp-7250676",
        "patent_number": "特許第7250676号",
        "application_number": "",
        "jp_id": "JP7250676B2",
    }),
    ("JP2024-073024A", {
        "kind": "publication",
        "case_id": "2024-073024",
        "patent_number": "特開2024-073024",
        "application_number": "",
        "jp_id": "JP2024073024A",
    }),
    ("２０２４－７３０２４", {
        "kind": "publication",
        "case_id": "2024-73024",
        "patent_number": "特開2024-073024",
        "application_number": "",
        "jp_id": "JP2024073024A",
    }),
])
def test_parse_known_jp_forms(raw, expected):
    assert _parse_patent_input(raw) == expected


def test_empty_input_returns_unknown_with_empty_ids():
    assert _parse_patent_input("  ") == {
        "kind": "unknown",
        "case_id": "",
        "patent_number": "",
        "application_number": "",
        "jp_id": "",
    }


def test_unknown_input_preserves_original_case_id_and_jp_id():
    result = _parse_patent_input("WO2020/112595A1")
    assert result["kind"] == "unknown"
    assert result["case_id"] == "WO2020/112595A1"
    assert result["jp_id"] == "WO2020/112595A1"

