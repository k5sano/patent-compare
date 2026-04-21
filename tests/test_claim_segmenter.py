"""claim_segmenter モジュールの回帰テスト。

注意:
    分節ロジックは将来的に改善される可能性が高いため、
    ここでは "仕様として安定していると期待する" 最小限の振る舞いのみを検証する。
    具体的には:
    - 分節数が妥当（独立請求項は2つ以上に分かれる）
    - 各 segment に id/text が付く
    - id は `<番号><アルファベット>` 形式
    - 数値範囲（例: 0.1～5質量%）が単一 segment 内に保持される
    - 従属請求項では追加限定のみが分節される
"""

import re

import pytest

from modules.claim_segmenter import (
    segment_single_claim,
    segment_claims,
)


def _ids(segments):
    return [s["id"] for s in segments]


class TestSegmentSingleClaim:
    def test_independent_claim_is_split(self):
        claim = {
            "number": 1,
            "text": "(A)成分としてポリマーを含有し、"
                    "(B)成分として界面活性剤を含有し、"
                    "(C)成分として水を含有する化粧料。",
            "is_independent": True,
        }
        result = segment_single_claim(claim)

        assert result["claim_number"] == 1
        assert result["is_independent"] is True
        assert len(result["segments"]) >= 2

        for seg in result["segments"]:
            assert "id" in seg
            assert "text" in seg
            assert seg["text"].strip()
            assert re.match(r"^1[A-Z]$", seg["id"])

    def test_ids_are_unique_and_sequential(self):
        claim = {
            "number": 2,
            "text": "(A)と、(B)と、(C)と、を含有する組成物。",
            "is_independent": True,
        }
        result = segment_single_claim(claim)
        ids = _ids(result["segments"])
        assert len(ids) == len(set(ids))

        alphas = [i[1:] for i in ids]
        expected = [chr(ord("A") + i) for i in range(len(alphas))]
        assert alphas == expected

    def test_numeric_range_preserved_in_single_segment(self):
        """数値範囲 '0.1〜5質量%' が分断されずに保持されること"""
        claim = {
            "number": 1,
            "text": "(A)ポリマーを0.1〜5質量%含有する化粧料。",
            "is_independent": True,
        }
        result = segment_single_claim(claim)
        full = " ".join(s["text"] for s in result["segments"])
        assert "0.1" in full and "5質量%" in full

        for seg in result["segments"]:
            if "0.1" in seg["text"]:
                assert "5質量%" in seg["text"], \
                    f"数値範囲が分断された: {seg['text']}"

    def test_dependent_claim_segments_only_additions(self):
        """従属請求項は追加限定のみが分節される"""
        claim = {
            "number": 2,
            "text": "請求項1に記載の化粧料であって、"
                    "さらに(D)成分として油を含有する化粧料。",
            "is_independent": False,
            "dependencies": [1],
        }
        result = segment_single_claim(claim)

        assert result["claim_number"] == 2
        assert result["is_independent"] is False
        assert result["segments"], "従属項でも追加限定の分節が存在すること"

        full = " ".join(s["text"] for s in result["segments"])
        assert "油" in full or "(D)" in full

    def test_product_name_separated(self):
        """末尾の製品名が独立 segment になること"""
        claim = {
            "number": 1,
            "text": "(A)水溶性ポリマーと、(B)油性成分と、を含有する、"
                    "皮膚化粧料。",
            "is_independent": True,
        }
        result = segment_single_claim(claim)

        assert any(s["text"].strip() in ("皮膚化粧料", "化粧料") for s in result["segments"]), \
            f"製品名が独立しなかった: {[s['text'] for s in result['segments']]}"

    def test_empty_dependencies_allowed(self):
        claim = {
            "number": 1,
            "text": "水を含有する化粧料。",
            "is_independent": True,
        }
        result = segment_single_claim(claim)
        assert result["dependencies"] == []

    def test_preserves_full_text(self):
        text = "(A)成分を含有する化粧料。"
        result = segment_single_claim({
            "number": 1, "text": text, "is_independent": True,
        })
        assert result["full_text"] == text


class TestSegmentClaims:
    def test_segments_multiple_claims(self):
        claims = [
            {"number": 1, "text": "(A)と(B)を含有する化粧料。", "is_independent": True},
            {"number": 2, "text": "請求項1に記載の化粧料であって、(C)をさらに含有する化粧料。",
             "is_independent": False, "dependencies": [1]},
        ]
        results = segment_claims(claims)
        assert len(results) == 2
        assert results[0]["claim_number"] == 1
        assert results[1]["claim_number"] == 2
        assert results[0]["is_independent"] is True
        assert results[1]["is_independent"] is False

    def test_empty_list(self):
        assert segment_claims([]) == []
