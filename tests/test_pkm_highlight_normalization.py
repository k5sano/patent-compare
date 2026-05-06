"""pkm_highlight_python の OCR ゆれ吸収テスト。

化粧品/化学分野の用語は OCR で以下のゆれが頻出:
  - 小書きカタカナ化   : グアニル → グァニル
  - 拗音表記化         : システイン → システィン
  - 空白挿入           : グ アニル シ ステイン
  - 全半角混在         : ＰＨ7.0 → pH7.0

これらを吸収して同じキーワードとしてヒットすることを確認する。
"""
from __future__ import annotations

import pytest

from services.search_run_service import (
    _normalize_text_for_match,
    pkm_build_index,
    pkm_highlight_python,
)


class TestNormalize:
    def test_small_katakana_unified(self):
        norm, _ = _normalize_text_for_match("グァニル")
        assert norm == "グアニル"

    def test_digraph_unified(self):
        # 「テイ」と「ティ」が同じ正規化結果
        a, _ = _normalize_text_for_match("システイン")
        b, _ = _normalize_text_for_match("システィン")
        assert a == b

    def test_combined_ocr_variations_unified(self):
        # 「グアニルシステイン」「グァニルシスティン」「グ アニル シ ステイン」が同じに
        targets = [
            "グアニルシステイン",
            "グァニルシスティン",
            "グ アニル シ ステイン",
            "グァニル　システィン",  # 全角空白
            "グアニルシスティン",     # ァはそのまま、ティだけ
            "グァニルシステイン",     # ァだけ、テイはそのまま
        ]
        normalized = [_normalize_text_for_match(s)[0] for s in targets]
        assert all(n == normalized[0] for n in normalized), \
            f"全て同じ正規化結果になるべき: {normalized}"

    def test_fullwidth_alphanum_to_halfwidth(self):
        norm, _ = _normalize_text_for_match("ＰＨ７．０")
        # NFKC ではなく translate ベースなので、ピリオドは半角化されない
        # → 英数字は半角に統一される (lower case 化も含む)
        assert "p" in norm and "h" in norm and "7" in norm

    def test_idx_map_recovers_original_position(self):
        text = "グ アニルシステイン"  # 半角空白を 1 個挟んでいる
        norm, idx_map = _normalize_text_for_match(text)
        # norm 上の "グアニル" の位置から原文位置を逆引きできる
        pos = norm.find("グアニル")
        assert pos == 0
        # 0 文字目: グ → 原文 0
        # 1 文字目: ア → 原文 2 (空白を飛ばした位置)
        assert idx_map[0] == 0
        assert idx_map[1] == 2  # "グ "の次


class TestHighlightWithOcrNoise:
    def _idx(self):
        return pkm_build_index([{
            "group_id": 1,
            "keywords": [{"term": "グアニルシステイン"}],
        }])

    def test_exact_match(self):
        result = pkm_highlight_python(
            "本願ではグアニルシステインを必須成分とする。",
            self._idx(),
        )
        assert result["counts"].get(1) == 1
        assert "グアニルシステイン" in result["html"]
        assert "<mark" in result["html"]

    def test_small_katakana_variant(self):
        """グアニル → グァニル (ア → ァ) でも検出される"""
        result = pkm_highlight_python(
            "実施例ではグァニルシステインを 0.5% 配合した。",
            self._idx(),
        )
        assert result["counts"].get(1) == 1, \
            f"グァニル を含む文でカウントされるべき: {result}"

    def test_digraph_variant(self):
        """システイン → システィン (テイ → ティ) でも検出される"""
        result = pkm_highlight_python(
            "グアニルシスティンが含まれる組成物。",
            self._idx(),
        )
        assert result["counts"].get(1) == 1

    def test_combined_variants(self):
        """ァ + ティ の OCR 例がユーザ報告例 (グァニルシスティン)"""
        result = pkm_highlight_python(
            "毛髪用組成物として、グァニルシスティンを 1 質量%含む。",
            self._idx(),
        )
        assert result["counts"].get(1) == 1

    def test_with_internal_whitespace(self):
        """空白で寸断されてもキーワード全体としてマッチする"""
        result = pkm_highlight_python(
            "比較例3: グ アニル シ ステイン を含まない処方。",
            self._idx(),
        )
        assert result["counts"].get(1) == 1

    def test_fullwidth_digit_normalization(self):
        """全角英数字が混じった用語もマッチする"""
        idx = pkm_build_index([{
            "group_id": 1,
            "keywords": [{"term": "PEG-40"}],
        }])
        result = pkm_highlight_python("ＰＥＧ－４０ を含有する。", idx)
        assert result["counts"].get(1) == 1

    def test_no_match_for_unrelated_text(self):
        result = pkm_highlight_python(
            "本願は単純なシステインを使う。グアニル基ではない。",
            self._idx(),
        )
        assert result["counts"].get(1, 0) == 0, \
            "「グアニルシステイン」全体としては出現しないので 0 件"

    def test_overlap_avoidance(self):
        """重なる候補は採用されない (長い term 優先)"""
        idx = pkm_build_index([{
            "group_id": 1,
            "keywords": [
                {"term": "グアニルシステイン"},
                {"term": "グアニル"},   # 上の部分文字列
            ],
        }])
        result = pkm_highlight_python(
            "グアニルシステイン",
            idx,
        )
        # 長い term が優先されるので、結果は 1 ヒット (グアニルシステイン)
        assert result["counts"].get(1) == 1

    def test_mark_position_is_correct(self):
        """空白を含む原文でも <mark> は連続範囲を覆う (空白も含む)"""
        result = pkm_highlight_python(
            "ここにグ アニルシステインが",
            self._idx(),
        )
        assert result["counts"].get(1) == 1
        # ハイライト済 HTML には「グ アニルシステイン」(空白含む) が <mark> で囲まれる
        assert "グ アニルシステイン" in result["html"].replace("&nbsp;", " ").replace("\xa0", " ")
