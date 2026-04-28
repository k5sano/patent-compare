"""LLM の document_id と citation_id のあいまいマッチング (`_resolve_doc_id`) の単体テスト。

ユーザー報告の不具合 (US 特許や J-PlatPat 内部 ID 形式の引例で「回答済」が付かない) の
回帰防止。
"""
from __future__ import annotations

import pytest

from services.comparison_service import (
    _canonical_digits,
    _digit_groups,
    _normalize_doc_id,
    _resolve_doc_id,
)


class TestNormalize:
    @pytest.mark.parametrize("inp,expected", [
        ("US 2013/0040869", "US20130040869"),
        ("US-2013-0040869", "US20130040869"),
        ("US20130040869A1", "US20130040869A1"),
        ("特開2020-169128号", "特開2020169128号"),
        ("", ""),
    ])
    def test_basic(self, inp, expected):
        assert _normalize_doc_id(inp) == expected


class TestDigitGroups:
    @pytest.mark.parametrize("inp,expected", [
        ("JPB003596681-000000", ["3596681"]),
        ("JPA1993042929-000000", ["1993042929"]),
        ("特許第3596681号", ["3596681"]),
        ("US20130040869", ["20130040869"]),
        ("JP-2024-032096", ["2024", "32096"]),
        ("123", []),  # 4 桁未満は捨てる
    ])
    def test_extract(self, inp, expected):
        assert _digit_groups(inp) == expected


class TestCanonicalDigits:
    @pytest.mark.parametrize("inp,expected", [
        ("特開1993-042929", "1993042929"),
        ("JPA1993042929-000000", "1993042929000000"),
        ("特許第3596681号", "3596681"),
        ("JPB003596681-000000", "3596681000000"),
        ("US20130040869", "20130040869"),
        # B2 の 2 も数字なので末尾に付く (ただし吸着には影響しない)
        ("JP4696507B2", "46965072"),
    ])
    def test_canonical(self, inp, expected):
        assert _canonical_digits(inp) == expected


class TestResolveExact:
    def test_exact_match(self):
        known = ["JP5047668B2", "US20130040869"]
        assert _resolve_doc_id("JP5047668B2", known) == "JP5047668B2"
        assert _resolve_doc_id("US20130040869", known) == "US20130040869"

    def test_no_known_passthrough(self):
        # known が空なら触らない
        assert _resolve_doc_id("anything", []) == "anything"


class TestResolveNormalized:
    def test_us_with_separators(self):
        known = ["US20130040869"]
        assert _resolve_doc_id("US 2013/0040869", known) == "US20130040869"
        assert _resolve_doc_id("US-2013-0040869", known) == "US20130040869"

    def test_us_with_a1_suffix(self):
        known = ["US20130040869"]
        assert _resolve_doc_id("US20130040869A1", known) == "US20130040869"


class TestResolveDigitSignature:
    """J-PlatPat 内部 ID (JPA/JPB + 連番 + -000000) と LLM 出力の patent_number を吸着"""

    def test_jpb_japanese_patent_number(self):
        known = ["JP5047668B2", "JPB003596681-000000"]
        assert _resolve_doc_id("特許第3596681号", known) == "JPB003596681-000000"
        assert _resolve_doc_id("特許3596681", known) == "JPB003596681-000000"
        assert _resolve_doc_id("JP3596681B2", known) == "JPB003596681-000000"
        assert _resolve_doc_id("JP3596681", known) == "JPB003596681-000000"

    def test_jpa_publication_number(self):
        known = ["JPA1993042929-000000"]
        assert _resolve_doc_id("特開1993-042929", known) == "JPA1993042929-000000"
        assert _resolve_doc_id("JP1993-042929", known) == "JPA1993042929-000000"
        assert _resolve_doc_id("JP-1993-042929A", known) == "JPA1993042929-000000"

    def test_no_false_match_on_year_only(self):
        # known に 1993 系統が一切ない場合、年号 (4 桁) だけで誤マッチしない
        known = ["JP5047668B2", "US20130040869"]
        assert _resolve_doc_id("特開2020-12345", known) == "特開2020-12345"

    def test_unresolved_passes_through(self):
        known = ["JP5047668B2"]
        assert _resolve_doc_id("Some random text", known) == "Some random text"
