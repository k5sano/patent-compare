"""Step 5 対比プロンプト生成時の citation + hit_text マージのテスト。

Step 4.5 で「全文取得」した結果は cases/<id>/search_runs/_hit_text/<pid>.json
にキャッシュされるが、citations/<id>.json には反映されない。Step 5 で
プロンプトを生成する際に hit_text の本文 (description / claims) を citation に
補完して、キーワードの silent miss を防ぐ。
"""
from __future__ import annotations

import json

import pytest

from services import case_service, comparison_service


@pytest.fixture
def case_with_citation_and_hit_text(tmp_path, monkeypatch):
    """citation (本文薄い) + hit_text (本文厚い) が両方ある状態を作る"""
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    case_id = "2030-merge"
    case_service.create_minimal_case(case_id, title="x", field="cosmetics")
    case_dir = cases_dir / case_id

    # segments
    with open(case_dir / "segments.json", "w", encoding="utf-8") as f:
        json.dump([{
            "claim_number": 1, "is_independent": True, "dependencies": [],
            "segments": [{"id": "1A", "text": "成分A"}],
        }], f, ensure_ascii=False)

    # 本願も用意 (空でいい)
    with open(case_dir / "hongan.json", "w", encoding="utf-8") as f:
        json.dump({"claims": [], "paragraphs": [], "tables": []},
                  f, ensure_ascii=False)

    # citation (本文に「グアニルシステイン」は出てこない、PDF から抽出失敗気味)
    citation = {
        "patent_number": "JP2014-001183",
        "claims": [{"number": 1, "text": "毛髪トリートメント用組成物"}],
        "paragraphs": [
            {"id": "0001", "page": 1, "section": "課題", "text": "毛髪保護を目的とする"}
        ],
        "tables": [],
    }
    with open(case_dir / "citations" / "JP2014-001183.json", "w", encoding="utf-8") as f:
        json.dump(citation, f, ensure_ascii=False)

    # hit_text (Step 4.5 全文取得): description に「グアニルシステイン」が出てくる
    hit_dir = case_dir / "search_runs" / "_hit_text"
    hit_dir.mkdir(parents=True)
    hit_text = {
        "patent_id": "JP2014-001183",
        "title": "毛髪トリートメント用組成物",
        "description": (
            "本発明の毛髪トリートメント用組成物は、必須成分としてグアニルシステインを"
            "0.5〜10質量%含有する。これにより、毛髪のダメージを補修できる。"
        ),
        "claims": [],  # citation 側にあるので補完不要
    }
    with open(hit_dir / "JP2014-001183.json", "w", encoding="utf-8") as f:
        json.dump(hit_text, f, ensure_ascii=False)

    # case.yaml の citations に登録
    meta = case_service.load_case_meta(case_id)
    meta["citations"] = [{"id": "JP2014-001183", "role": "主引例", "label": "JP2014-001183"}]
    case_service.save_case_meta(case_id, meta)

    return case_id, case_dir


class TestEnrichCitationWithHitText:
    def test_appends_description_to_paragraphs(self, case_with_citation_and_hit_text):
        case_id, _ = case_with_citation_and_hit_text
        citation_path = (
            case_service.get_case_dir(case_id)
            / "citations" / "JP2014-001183.json"
        )
        with open(citation_path, encoding="utf-8") as f:
            citation = json.load(f)
        enriched = comparison_service._enrich_citation_with_hit_text(
            case_id, "JP2014-001183", citation,
        )
        # 元の paragraph は維持
        assert any("毛髪保護" in (p.get("text") or "") for p in enriched["paragraphs"])
        # hit_text の description が追加されている
        assert any(
            "グアニルシステイン" in (p.get("text") or "")
            for p in enriched["paragraphs"]
        )

    def test_no_op_when_hit_text_already_in_citation(self, case_with_citation_and_hit_text):
        """citation 側に既に同じ description が入っていれば重複追加しない"""
        case_id, case_dir = case_with_citation_and_hit_text
        # citation の paragraphs に description 全文を含むテキストを足す
        citation_path = case_dir / "citations" / "JP2014-001183.json"
        with open(citation_path, encoding="utf-8") as f:
            citation = json.load(f)
        citation["paragraphs"].append({
            "id": "0010", "page": 2, "section": "実施例",
            "text": "本発明の毛髪トリートメント用組成物は、必須成分としてグアニルシステインを"
                    "0.5〜10質量%含有する。これにより、毛髪のダメージを補修できる。"
                    "実施例で詳述する。",
        })
        enriched = comparison_service._enrich_citation_with_hit_text(
            case_id, "JP2014-001183", citation,
        )
        # _hittext 段落が追加されない
        ids = [p.get("id") for p in enriched["paragraphs"]]
        assert "_hittext" not in ids

    def test_no_hit_text_returns_citation_as_is(self, tmp_path, monkeypatch):
        monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
        (tmp_path / "cases").mkdir()
        case_service.create_minimal_case("2030-no-hit", title="x", field="cosmetics")
        cit = {"claims": [{"number": 1, "text": "x"}], "paragraphs": []}
        result = comparison_service._enrich_citation_with_hit_text(
            "2030-no-hit", "anything", cit,
        )
        assert result == cit  # そのまま


class TestGeneratePromptUsesEnrichedCitation:
    def test_prompt_contains_hit_text_keyword(self, case_with_citation_and_hit_text):
        """Step 5 generate_prompt_single でプロンプトに hit_text のキーワードが含まれる"""
        case_id, _ = case_with_citation_and_hit_text
        result, code = comparison_service.generate_prompt_single(
            case_id, "JP2014-001183",
        )
        assert code == 200, f"プロンプト生成失敗: {result}"
        prompt = result["prompt"]
        # citation の本文に元はなかった「グアニルシステイン」が含まれる
        assert "グアニルシステイン" in prompt, \
            "hit_text マージ後はプロンプトにキーワードが含まれるべき"

    def test_multi_prompt_also_includes_hit_text(self, case_with_citation_and_hit_text):
        case_id, _ = case_with_citation_and_hit_text
        result, code = comparison_service.generate_prompt_multi(
            case_id, ["JP2014-001183"],
        )
        assert code == 200, f"プロンプト生成失敗: {result}"
        assert "グアニルシステイン" in result["prompt"]
