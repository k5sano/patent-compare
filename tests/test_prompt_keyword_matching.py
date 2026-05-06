"""Step 5 prompt keyword matching should tolerate OCR/notation noise."""
from __future__ import annotations

from modules.prompt_generator import (
    _find_matching_claims,
    _find_matching_paragraphs,
    _find_matching_tables,
    generate_prompt_requirement_first,
)


def test_paragraph_match_tolerates_guanyl_cysteine_ocr_variants():
    units = [
        {"id": "0001", "text": "毛髪用組成物はグァニルシスティンを含む。"},
    ]

    hits = _find_matching_paragraphs(units, ["グアニルシステイン"], 5)

    assert [h["id"] for h in hits] == ["0001"]


def test_claim_match_tolerates_internal_whitespace():
    claims = [
        {"number": 2, "text": "Ｎ－グ アニル シ ステインを有効成分とする。"},
    ]

    hits = _find_matching_claims(claims, ["グアニルシステイン"])

    assert [h["number"] for h in hits] == [2]


def test_table_match_tolerates_fullwidth_alphanum_and_katakana_variants():
    tables = [
        {
            "caption": "表1",
            "rows": [["実施例1", "ＰＥＧ－４０", "グァニルシスティン"]],
        },
    ]

    hits = _find_matching_tables(tables, ["PEG-40", "グアニルシステイン"])

    assert len(hits) == 1


def test_requirement_first_prompt_includes_variant_hit():
    segments = [{
        "claim_number": 1,
        "is_independent": True,
        "dependencies": [],
        "segments": [{"id": "1A", "text": "グアニルシステイン及び"}],
    }]
    keywords = [{
        "group_id": 1,
        "label": "主還元剤",
        "segment_ids": ["1A"],
        "keywords": [{"term": "グアニルシステイン"}],
    }]
    citations = [{
        "patent_number": "JPTEST",
        "label": "JPTEST",
        "claims": [],
        "paragraphs": [
            {"id": "0010", "section": "実施例", "text": "実施例ではグァニルシスティンを1質量%配合した。"},
        ],
        "tables": [],
    }]

    prompt = generate_prompt_requirement_first(
        segments, citations, keywords, field="cosmetics", hongan=None,
    )

    assert "キーワードヒットなし" not in prompt
    assert "グァニルシスティンを1質量%配合" in prompt


def test_requirement_first_prompt_excerpt_centers_long_hit_text():
    segments = [{
        "claim_number": 1,
        "is_independent": True,
        "dependencies": [],
        "segments": [{"id": "1A", "text": "グアニルシステイン及び"}],
    }]
    keywords = [{
        "group_id": 1,
        "label": "主還元剤",
        "segment_ids": ["1A"],
        "keywords": [{"term": "グアニルシステイン"}],
    }]
    long_prefix = "背景技術。" * 200
    citations = [{
        "patent_number": "JPTEST",
        "label": "JPTEST",
        "claims": [],
        "paragraphs": [
            {
                "id": "_hittext",
                "section": "全文取得 (Step 4.5)",
                "text": long_prefix + "実施例ではＮ－グァニルシスティンを1質量%配合した。",
            },
        ],
        "tables": [],
    }]

    prompt = generate_prompt_requirement_first(
        segments, citations, keywords, field="cosmetics", hongan=None,
    )

    assert "Ｎ－グァニルシスティンを1質量%配合" in prompt
    assert "段落_hittext" not in prompt


def test_requirement_first_prompt_uses_page_lines_for_fake_wo_paragraphs():
    segments = [{
        "claim_number": 1,
        "is_independent": True,
        "dependencies": [],
        "segments": [{"id": "1A", "text": "グアニルシステイン及び"}],
    }]
    keywords = [{
        "group_id": 1,
        "label": "主還元剤",
        "segment_ids": ["1A"],
        "keywords": [{"term": "グアニルシステイン"}],
    }]
    citations = [{
        "patent_number": "WO2007108460",
        "label": "WO2007108460",
        "claims": [],
        "paragraphs": [
            {
                "id": "0001",
                "page": 7,
                "line_start": 18,
                "line_end": 22,
                "has_paragraph_number": False,
                "section": "実施例",
                "text": "再表の本文ではグアニルシステインを配合する。",
            },
        ],
        "tables": [],
    }]

    prompt = generate_prompt_requirement_first(
        segments, citations, keywords, field="cosmetics", hongan=None,
    )

    assert "P7G18-22(実施例)" in prompt
    assert "段落0001(実施例)" not in prompt
