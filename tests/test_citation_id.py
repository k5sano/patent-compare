"""citation_id モジュールのテスト"""

import pytest

from modules.citation_id import normalize_citation_id, dedupe_citations


class TestNormalizeCitationId:
    @pytest.mark.parametrize("raw,expected", [
        # 国際公報: 末尾 Kind Code を除去
        ("WO2019180364A1", "WO2019180364"),
        ("WO2019180364A2", "WO2019180364"),
        ("WO2019180364", "WO2019180364"),
        ("EP3719056A1", "EP3719056"),
        ("FR3088205A1", "FR3088205"),
        ("US20200123456A1", "US20200123456"),
        # JP: grant (B2) と公開 (A) の末尾除去
        ("JP6199984B2", "JP6199984"),
        ("JP2024-032096A", "JP2024-032096"),
        # 日本語表記はそのまま
        ("特開2024-032096", "特開2024-032096"),
        ("特許第6199984号", "特許第6199984号"),
        ("再公表WO2019/180364", "再公表WO2019180364"),
        # 表記ゆれ（スペース・スラッシュ）の正規化
        ("WO 2019/180364 A1", "WO2019180364"),
        (" EP 3719056 ", "EP3719056"),
        ("JP2024−032096A", "JP2024-032096"),  # 全角マイナス
        # 空/None 系
        ("", ""),
        (None, None),
    ])
    def test_normalize(self, raw, expected):
        assert normalize_citation_id(raw) == expected

    def test_idempotent(self):
        """正規化が冪等であること"""
        for raw in ["WO2019180364A1", "JP6199984B2", "EP3719056A1", "特開2024-032096"]:
            once = normalize_citation_id(raw)
            twice = normalize_citation_id(once)
            assert once == twice


class TestDedupeCitations:
    def test_removes_kind_code_duplicate(self):
        citations = [
            {"id": "WO2019180364", "role": "主引例候補", "label": "WO2019/180364A1"},
            {"id": "WO2019180364A1", "role": "主引例候補", "label": "WO2019/180364A1"},
        ]
        result = dedupe_citations(citations)
        assert len(result) == 1
        assert result[0]["id"] == "WO2019180364"

    def test_keeps_distinct_ids(self):
        citations = [
            {"id": "WO2019180364", "role": "主引例", "label": "A"},
            {"id": "WO2020109418", "role": "副引例", "label": "B"},
            {"id": "EP3719056A1", "role": "副引例", "label": "C"},
        ]
        result = dedupe_citations(citations)
        ids = [c["id"] for c in result]
        assert ids == ["WO2019180364", "WO2020109418", "EP3719056"]

    def test_merges_longer_role_label(self):
        """重複時はより長い role / label を残す"""
        citations = [
            {"id": "WO2019180364", "role": "主引例", "label": "short"},
            {"id": "WO2019180364A1", "role": "主引例候補（最有力）", "label": "WO2019/180364A1"},
        ]
        result = dedupe_citations(citations)
        assert len(result) == 1
        assert result[0]["role"] == "主引例候補（最有力）"
        assert result[0]["label"] == "WO2019/180364A1"

    def test_preserves_order_of_first_occurrence(self):
        citations = [
            {"id": "WO2022129469", "role": "主引例"},
            {"id": "WO2019180364A1", "role": "副引例"},
            {"id": "WO2019180364", "role": "副引例"},  # 2番目と同一扱い
            {"id": "WO2020109418", "role": "副引例"},
        ]
        result = dedupe_citations(citations)
        ids = [c["id"] for c in result]
        assert ids == ["WO2022129469", "WO2019180364", "WO2020109418"]

    def test_skips_empty_id(self):
        citations = [
            {"id": "", "role": "?"},
            {"id": "WO2019180364A1", "role": "主引例"},
        ]
        result = dedupe_citations(citations)
        assert len(result) == 1
        assert result[0]["id"] == "WO2019180364"

    def test_skips_non_dict_entries(self):
        citations = [
            "not-a-dict",
            None,
            {"id": "WO2019180364A1", "role": "主引例"},
        ]
        result = dedupe_citations(citations)
        assert len(result) == 1
        assert result[0]["id"] == "WO2019180364"

    def test_empty_list(self):
        assert dedupe_citations([]) == []
