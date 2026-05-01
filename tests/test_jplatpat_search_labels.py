"""J-PlatPat 詳細ページ遷移用の検索ラベル候補生成 (build_search_label_candidates) のテスト。

ユーザー報告: 特開2024-533284 (実は PCT 翻訳公報「特表」) で「詳細ページへの遷移に
失敗 (試行: ['特開2024-533284', '2024-533284'])」エラー。PDF パーサが「特開」とタグ
しても J-PlatPat 実体は「特表」のことがあるので、両方を候補に入れる必要がある。
"""
from __future__ import annotations

import pytest

from modules.jplatpat_client import build_search_label_candidates


class TestKokai:
    def test_simple_kokai_includes_kohyo_alternative(self):
        cands = build_search_label_candidates("特開2024-533284")
        assert "特開2024-533284" in cands
        # 特表 も試行候補に入る (実は PCT 翻訳公報のケース)
        assert "特表2024-533284" in cands
        # 番号だけのフォーム
        assert "2024-533284" in cands

    def test_kokai_with_space_variants(self):
        cands = build_search_label_candidates("特開2024-533284")
        # スペース付きフォーム (J-PlatPat 表示揺れ吸収)
        assert "特表 2024-533284" in cands
        assert "特開 2024-533284" in cands


class TestKohyo:
    def test_kohyo_input_also_tries_kokai(self):
        cands = build_search_label_candidates("特表2024-533284")
        assert "特表2024-533284" in cands
        # 逆方向: 特表入力でも特開候補を入れる
        assert "特開2024-533284" in cands


class TestJpAscii:
    def test_jpa_form_expands(self):
        cands = build_search_label_candidates("JP2024-123456A")
        assert any("特開2024-123456" in c for c in cands)
        assert any("特表2024-123456" in c for c in cands)

    def test_jpb_form_expands_to_tokkyo(self):
        cands = build_search_label_candidates("JP6789012B2")
        assert "特許第6789012号" in cands
        assert "特許6789012" in cands


class TestEdgeCases:
    def test_empty_input(self):
        assert build_search_label_candidates("") == []
        assert build_search_label_candidates(None) == []

    def test_dedup_preserves_order(self):
        cands = build_search_label_candidates("特開2024-533284")
        # 重複なし
        assert len(cands) == len(set(cands))
        # 元の入力が先頭
        assert cands[0] == "特開2024-533284"

    def test_no_year_pattern_returns_input_only(self):
        cands = build_search_label_candidates("ABCDEF")
        # 数字パターン無し → 入力そのものだけ (空白除去版含む)
        assert cands == ["ABCDEF"]
