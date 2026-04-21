"""response_parser モジュールの回帰テスト。

対象: Claude の回答テキストから JSON を抽出して構成要件ごとの判定を
      バリデーションするロジック。
"""

import json
import pytest

from modules.response_parser import (
    parse_response,
    split_multi_response,
    merge_responses,
    generate_supplement_prompt,
)


REQUIRED_IDS = ["1A", "1B", "1C"]


def _make_response(doc_id="WO2019180364", judgments=None):
    """単一文献の有効な回答辞書を生成"""
    j = judgments or {"1A": "○", "1B": "○", "1C": "○"}
    return {
        "document_id": doc_id,
        "comparisons": [
            {
                "requirement_id": rid,
                "judgment": jv,
                "cited_location": "段落0010" if jv != "×" else "",
                "judgment_reason": "理由テキスト",
            }
            for rid, jv in j.items()
        ],
        "overall_summary": "概要",
        "category_suggestion": "X",
    }


class TestParseResponseSingleDoc:
    def test_valid_json_block(self):
        resp = _make_response()
        raw = "以下が判定結果です。\n```json\n" + json.dumps(resp, ensure_ascii=False) + "\n```"
        data, errors = parse_response(raw, REQUIRED_IDS)

        assert errors == []
        assert data["document_id"] == "WO2019180364"
        assert len(data["comparisons"]) == 3

    def test_raw_json_without_fences(self):
        resp = _make_response()
        raw = json.dumps(resp, ensure_ascii=False)
        data, errors = parse_response(raw, REQUIRED_IDS)
        assert errors == []
        assert data is not None

    def test_missing_requirement_is_flagged(self):
        resp = _make_response(judgments={"1A": "○", "1B": "○"})  # 1C 欠落
        raw = "```json\n" + json.dumps(resp, ensure_ascii=False) + "\n```"
        data, errors = parse_response(raw, REQUIRED_IDS)

        assert data is not None
        assert any("1C" in e for e in errors), errors

    def test_invalid_judgment_is_flagged(self):
        resp = _make_response(judgments={"1A": "○", "1B": "○", "1C": "YES"})
        raw = json.dumps(resp, ensure_ascii=False)
        _data, errors = parse_response(raw, REQUIRED_IDS)

        assert any("1C" in e and "YES" in e for e in errors), errors

    def test_empty_cited_location_for_match_is_flagged(self):
        resp = _make_response()
        resp["comparisons"][0]["cited_location"] = ""
        raw = json.dumps(resp, ensure_ascii=False)
        _data, errors = parse_response(raw, REQUIRED_IDS)

        assert any("1A" in e and "cited_location" in e for e in errors), errors

    def test_x_judgment_allows_empty_cited_location(self):
        resp = _make_response(judgments={"1A": "×", "1B": "○", "1C": "○"})
        resp["comparisons"][0]["cited_location"] = ""
        raw = json.dumps(resp, ensure_ascii=False)
        _data, errors = parse_response(raw, REQUIRED_IDS)

        assert not any("1A" in e and "cited_location" in e for e in errors), errors

    def test_no_json_returns_error(self):
        data, errors = parse_response("これはただのテキストです。", REQUIRED_IDS)
        assert data is None
        assert errors and any("JSON" in e for e in errors)


class TestParseResponseMultiDoc:
    def test_multi_json_blocks_merged_to_results(self):
        r1 = _make_response(doc_id="WO2019180364")
        r2 = _make_response(doc_id="WO2020109418")
        raw = (
            "文献1:\n```json\n" + json.dumps(r1, ensure_ascii=False) + "\n```\n"
            "文献2:\n```json\n" + json.dumps(r2, ensure_ascii=False) + "\n```"
        )
        data, errors = parse_response(raw, REQUIRED_IDS)

        assert errors == []
        assert "results" in data
        assert len(data["results"]) == 2
        doc_ids = {r["document_id"] for r in data["results"]}
        assert doc_ids == {"WO2019180364", "WO2020109418"}

    def test_single_json_block_returns_single_doc(self):
        """単一ブロック時は results 配列化せず単一文献形式で返る"""
        r = _make_response(doc_id="WO2019180364")
        raw = "```json\n" + json.dumps(r, ensure_ascii=False) + "\n```"
        data, errors = parse_response(raw, REQUIRED_IDS)
        assert errors == []
        assert data.get("document_id") == "WO2019180364"
        assert "results" not in data

    def test_three_json_blocks_merged(self):
        """3文献のブロックも全て results に統合される"""
        r1 = _make_response(doc_id="WO1")
        r2 = _make_response(doc_id="WO2")
        r3 = _make_response(doc_id="WO3")
        raw = "\n".join(
            f"```json\n{json.dumps(r, ensure_ascii=False)}\n```"
            for r in (r1, r2, r3)
        )
        data, errors = parse_response(raw, REQUIRED_IDS)
        assert errors == []
        assert len(data["results"]) == 3
        assert {r["document_id"] for r in data["results"]} == {"WO1", "WO2", "WO3"}

    def test_results_key_format(self):
        merged = {"results": [_make_response(doc_id="WO1"), _make_response(doc_id="WO2")]}
        raw = "```json\n" + json.dumps(merged, ensure_ascii=False) + "\n```"
        data, errors = parse_response(raw, REQUIRED_IDS)
        assert errors == []
        assert len(data["results"]) == 2

    def test_errors_prefixed_with_doc_id(self):
        r1 = _make_response(doc_id="WO1")
        r2 = _make_response(doc_id="WO2", judgments={"1A": "○", "1B": "○"})  # 1C欠落
        merged = {"results": [r1, r2]}
        raw = json.dumps(merged, ensure_ascii=False)
        _data, errors = parse_response(raw, REQUIRED_IDS)
        assert any("[WO2]" in e for e in errors), errors


class TestSplitMultiResponse:
    def test_splits_results(self):
        r1 = _make_response(doc_id="WO1")
        r2 = _make_response(doc_id="WO2")
        result = split_multi_response({"results": [r1, r2]})
        assert set(result.keys()) == {"WO1", "WO2"}
        assert result["WO1"]["document_id"] == "WO1"

    def test_single_response_passthrough(self):
        r = _make_response(doc_id="WO1")
        result = split_multi_response(r)
        assert set(result.keys()) == {"WO1"}

    def test_none_returns_empty(self):
        assert split_multi_response(None) == {}


class TestMergeResponses:
    def test_supplement_fills_missing_comparisons(self):
        existing = _make_response(judgments={"1A": "○", "1B": "○"})
        supplement = {
            "comparisons": [
                {"requirement_id": "1C", "judgment": "×",
                 "cited_location": "", "judgment_reason": "該当無し"}
            ]
        }
        merged = merge_responses(existing, supplement)
        ids = {c["requirement_id"] for c in merged["comparisons"]}
        assert ids == {"1A", "1B", "1C"}

    def test_supplement_overwrites_existing(self):
        existing = _make_response(judgments={"1A": "○", "1B": "○", "1C": "○"})
        supplement = {
            "comparisons": [
                {"requirement_id": "1A", "judgment": "×",
                 "cited_location": "", "judgment_reason": "修正"}
            ]
        }
        merged = merge_responses(existing, supplement)
        entry_1a = next(c for c in merged["comparisons"] if c["requirement_id"] == "1A")
        assert entry_1a["judgment"] == "×"

    def test_none_existing_returns_supplement(self):
        sup = _make_response()
        assert merge_responses(None, sup) is sup

    def test_none_supplement_returns_existing(self):
        ex = _make_response()
        assert merge_responses(ex, None) is ex


class TestGenerateSupplementPrompt:
    def test_includes_missing_ids(self):
        data = _make_response(judgments={"1A": "○", "1B": "○"})
        prompt = generate_supplement_prompt(data, ["1C の判定がありません"], REQUIRED_IDS)
        assert "1C" in prompt
