"""case_service の新規サービス (create_minimal_case / compute_segments) のテスト"""

import json
from pathlib import Path

import pytest
import yaml

from services import case_service


@pytest.fixture
def isolated_cases_dir(tmp_path, monkeypatch):
    """cases/ ディレクトリを tmp_path に差し替える"""
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    (tmp_path / "cases").mkdir()
    return tmp_path / "cases"


class TestCreateMinimalCase:
    def test_creates_case_directory_structure(self, isolated_cases_dir):
        result, code = case_service.create_minimal_case(
            "2030-test01", title="テスト発明", field="cosmetics")

        assert code == 200
        assert result["success"] is True

        case_dir = isolated_cases_dir / "2030-test01"
        for sub in ("input", "citations", "prompts", "responses", "output"):
            assert (case_dir / sub).is_dir(), f"{sub}/ が作成されていない"

        yaml_path = case_dir / "case.yaml"
        assert yaml_path.exists()
        with open(yaml_path, "r", encoding="utf-8") as f:
            meta = yaml.safe_load(f)
        assert meta["case_id"] == "2030-test01"
        assert meta["title"] == "テスト発明"
        assert meta["field"] == "cosmetics"
        assert meta["citations"] == []

    def test_rejects_existing_case(self, isolated_cases_dir):
        case_service.create_minimal_case("2030-test01")
        result, code = case_service.create_minimal_case("2030-test01")
        assert code == 409
        assert "error" in result

    def test_default_field(self, isolated_cases_dir):
        case_service.create_minimal_case("2030-test02", title="X")
        meta = case_service.load_case_meta("2030-test02")
        assert meta["field"] == "cosmetics"


class TestComputeSegments:
    def test_segments_from_hongan(self, isolated_cases_dir):
        case_service.create_minimal_case("2030-test03", title="T")
        case_dir = isolated_cases_dir / "2030-test03"

        hongan = {
            "claims": [
                {"number": 1, "text": "(A)と(B)を含有する化粧料。",
                 "is_independent": True},
                {"number": 2, "text": "請求項1に記載の化粧料であって、(C)を含有する化粧料。",
                 "is_independent": False, "dependencies": [1]},
            ],
            "paragraphs": [],
        }
        with open(case_dir / "hongan.json", "w", encoding="utf-8") as f:
            json.dump(hongan, f, ensure_ascii=False)

        result, code = case_service.compute_segments("2030-test03")
        assert code == 200
        assert result["success"] is True
        assert result["num_claims"] == 2
        assert result["num_segments"] >= 2

        with open(case_dir / "segments.json", "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert len(saved) == 2

    def test_missing_hongan_returns_error(self, isolated_cases_dir):
        case_service.create_minimal_case("2030-test04")
        result, code = case_service.compute_segments("2030-test04")
        assert code == 400
        assert "本願" in result["error"]

    def test_empty_claims_returns_error(self, isolated_cases_dir):
        case_service.create_minimal_case("2030-test05")
        case_dir = isolated_cases_dir / "2030-test05"
        with open(case_dir / "hongan.json", "w", encoding="utf-8") as f:
            json.dump({"claims": [], "paragraphs": []}, f)

        result, code = case_service.compute_segments("2030-test05")
        assert code == 400
        assert "請求項" in result["error"]
