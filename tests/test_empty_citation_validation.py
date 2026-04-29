"""空 citation (テキスト抽出失敗) を Step 5 プロンプト生成前に検出して
明示エラーで止める回帰防止テスト。

ユーザー報告: 引用 PDF を直接アップロードした際にスキャン画像で本文抽出が失敗すると、
citations/{id}.json は claims=[]/paragraphs=[]/tables=[] のまま保存され、
プロンプト生成側でも素通りして見出しだけがプロンプトに入る → Claude が判定不能になり
silent skip される。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from services import case_service, comparison_service


@pytest.fixture
def case_with_mixed_citations(tmp_path, monkeypatch):
    """空 citation と非空 citation が混在した案件を作る"""
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    case_id = "2030-empty-cit"
    case_service.create_minimal_case(case_id, title="x", field="cosmetics")
    case_dir = cases_dir / case_id

    # segments.json を最小構成で用意
    with open(case_dir / "segments.json", "w", encoding="utf-8") as f:
        json.dump([{
            "claim_number": 1,
            "is_independent": True,
            "dependencies": [],
            "segments": [{"id": "1A", "text": "成分A"}],
        }], f, ensure_ascii=False)

    # 空 citation
    empty = {
        "file_name": "scan",
        "file_type": "citation",
        "patent_number": "scan",
        "claims": [],
        "paragraphs": [],
        "tables": [],
        "_warning": "テキスト抽出できませんでした（スキャン画像PDFの可能性）",
    }
    with open(case_dir / "citations" / "scan.json", "w", encoding="utf-8") as f:
        json.dump(empty, f, ensure_ascii=False)

    # 非空 citation
    good = {
        "file_name": "good",
        "file_type": "citation",
        "patent_number": "JP2020-001",
        "claims": [{"number": 1, "text": "請求項1"}],
        "paragraphs": [{"id": "0001", "text": "本文", "section": "発明の詳細な説明"}],
        "tables": [],
    }
    with open(case_dir / "citations" / "JP2020-001.json", "w", encoding="utf-8") as f:
        json.dump(good, f, ensure_ascii=False)

    # case.yaml の citations を更新
    meta = case_service.load_case_meta(case_id)
    meta["citations"] = [
        {"id": "JP2020-001", "role": "主引例", "label": "JP2020-001"},
        {"id": "scan", "role": "副引例", "label": "scan"},
    ]
    case_service.save_case_meta(case_id, meta)

    return case_id, case_dir


class TestIsEmptyCitation:
    def test_truly_empty(self):
        assert comparison_service._is_empty_citation({
            "claims": [], "paragraphs": [], "tables": [],
        }) is True

    def test_missing_keys(self):
        assert comparison_service._is_empty_citation({}) is True

    def test_has_claims(self):
        assert comparison_service._is_empty_citation({
            "claims": [{"number": 1, "text": "..."}],
            "paragraphs": [], "tables": [],
        }) is False

    def test_has_paragraphs(self):
        assert comparison_service._is_empty_citation({
            "claims": [],
            "paragraphs": [{"id": "0001", "text": "..."}],
            "tables": [],
        }) is False

    def test_has_tables_only(self):
        # 表だけでも対比価値はある (実施例の配合表など)
        assert comparison_service._is_empty_citation({
            "claims": [], "paragraphs": [],
            "tables": [{"id": "T1", "content": "表"}],
        }) is False


class TestGeneratePromptValidation:
    def test_single_blocks_empty(self, case_with_mixed_citations):
        case_id, _ = case_with_mixed_citations
        result, code = comparison_service.generate_prompt_single(case_id, "scan")
        assert code == 400
        assert "scan" in result["error"]
        assert "テキスト" in result["error"]
        assert result["empty_citation_ids"] == ["scan"]

    def test_single_passes_good(self, case_with_mixed_citations):
        case_id, _ = case_with_mixed_citations
        result, code = comparison_service.generate_prompt_single(case_id, "JP2020-001")
        assert code == 200
        assert "prompt" in result
        assert result["char_count"] > 0

    def test_multi_blocks_when_any_empty(self, case_with_mixed_citations):
        """1 件でも空が混じれば全体を止める (silent skip 防止)"""
        case_id, _ = case_with_mixed_citations
        result, code = comparison_service.generate_prompt_multi(
            case_id, ["JP2020-001", "scan"]
        )
        assert code == 400
        assert result["empty_citation_ids"] == ["scan"]

    def test_multi_passes_when_all_good(self, case_with_mixed_citations):
        case_id, _ = case_with_mixed_citations
        result, code = comparison_service.generate_prompt_multi(
            case_id, ["JP2020-001"]
        )
        assert code == 200


class TestUploadResponseSurfacesWarning:
    """upload_citation のレスポンスに _warning が伝播することを確認する。

    実 PDF を読ませず、extract_patent_pdf を monkey patch してスキャン状況を再現。
    """

    def test_warning_in_response(self, tmp_path, monkeypatch):
        monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
        (tmp_path / "cases").mkdir()
        case_service.create_minimal_case("2030-warn", title="x", field="cosmetics")

        def fake_extract(pdf_path, doc_type):
            return {
                "file_name": "scan",
                "file_type": doc_type,
                "patent_number": "scan",
                "claims": [],
                "paragraphs": [],
                "tables": [],
            }
        monkeypatch.setattr(
            "modules.pdf_extractor.extract_patent_pdf", fake_extract
        )

        # ダミー PDF ファイルを置く (上書きコピーのソースとして必要)
        dummy = tmp_path / "scan.pdf"
        dummy.write_bytes(b"%PDF-1.4 dummy")

        result, code = case_service.upload_citation(
            "2030-warn", str(dummy), role="副引例", label=""
        )
        assert code == 200
        assert result["success"] is True
        assert "warning" in result, "抽出失敗時はレスポンスに warning が含まれるべき"
        assert "テキスト" in result["warning"]
