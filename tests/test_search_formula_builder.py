from __future__ import annotations

import json

from services import case_service
from services import search_formula_builder as sfb


def test_parse_fterm_full_code_with_additional_code():
    parsed = sfb.parse_fterm_code("4C083AC172")
    assert parsed == {
        "raw": "4C083AC172",
        "theme": "4C083",
        "query_code": "AC17.2",
    }


def test_parse_fterm_full_code_without_additional_code():
    parsed = sfb.parse_fterm_code("4C083AB13")
    assert parsed["theme"] == "4C083"
    assert parsed["query_code"] == "AB13."


def test_parse_cosmetics_base_fterm_uses_trailing_dot_to_include_additional_codes():
    parsed = sfb.parse_fterm_code("4C083AD05")
    assert parsed == {
        "raw": "4C083AD05",
        "theme": "4C083",
        "query_code": "AD05.",
    }


def test_parse_short_fterm_preserves_trailing_dot():
    parsed = sfb.parse_fterm_code("AD05.")
    assert parsed["theme"] == ""
    assert parsed["query_code"] == "AD05."


def test_parse_laminate_fterm_layer_suffix_is_preserved():
    parsed = sfb.parse_fterm_code("4F100AK01B")
    assert parsed == {
        "raw": "4F100AK01B",
        "theme": "4F100",
        "query_code": "AK01B",
    }


def test_numeric_suffix_is_not_treated_as_additional_code_for_other_themes():
    parsed = sfb.parse_fterm_code("4F100AK011")
    assert parsed["theme"] == "4F100"
    assert parsed["query_code"] == "AK011"


def test_fterm_formula_parts_groups_by_theme_and_uses_fc():
    parts, skipped, normalized = sfb.fterm_formula_parts([
        "4C083AC172",
        "4C083AB081",
        "4F100AK01B",
        "not-a-code",
    ])
    assert skipped == ["not-a-code"]
    assert normalized == ["4C083:AC17.2", "4C083:AB08.1", "4F100:AK01B"]
    assert "(4C083/FC * (AC17.2+AB08.1)/FT)" in parts
    assert "(4F100/FC * AK01B/FT)" in parts


def test_build_l0_main_fterm_uses_theme_code(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    (tmp_path / "cases").mkdir()
    case_service.create_minimal_case("2030-ft", title="x", field="cosmetics")
    case_dir = tmp_path / "cases" / "2030-ft"
    (case_dir / "hongan.json").write_text(
        json.dumps({"applicant": "株式会社テスト"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (case_dir / "keywords.json").write_text(
        json.dumps([
            {
                "group_id": 1,
                "label": "主成分",
                "segment_ids": ["1A"],
                "keywords": [],
                "search_codes": {
                    "fi": [{"code": "A61K 8/898"}],
                    "fterm": [{"code": "4C083AC172"}],
                },
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )

    result, code = sfb.build_l0("2030-ft", include_main_fterm=True)
    assert code == 200
    assert "4C083AC172/FT" not in result["formula"]
    assert "(4C083/FC * AC17.2/FT)" in result["formula"]
    assert result["components"]["main_fterm_codes"] == ["4C083:AC17.2"]
