"""case_service / keyword_service のサービス層テスト"""

import json
from pathlib import Path

import pytest
import yaml

from services import case_service
from services.keyword_service import _fterm_short_code, add_fi, delete_fi, fi_candidates


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


class TestCreateCaseBibliography:
    def test_create_case_persists_jplatpat_bibliography(self, isolated_cases_dir, monkeypatch):
        import modules.claim_segmenter as claim_segmenter
        import modules.patent_downloader as patent_downloader
        import modules.pdf_extractor as pdf_extractor

        def fake_download(_pid, save_dir, **_kwargs):
            p = Path(save_dir) / "JP2024108988A.pdf"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"%PDF-1.3\n")
            return {"success": True, "path": str(p)}

        monkeypatch.setattr(patent_downloader, "download_patent_pdf_smart", fake_download)
        monkeypatch.setattr(pdf_extractor, "extract_patent_pdf", lambda *_a, **_k: {
            "patent_number": "特開2024-108988",
            "patent_title": "毛髪変形化粧料",
            "claims": [{"number": 1, "text": "Aを含む化粧料。", "is_independent": True}],
            "paragraphs": [],
        })
        monkeypatch.setattr(claim_segmenter, "segment_claims", lambda _claims: [
            {"claim_number": 1, "segments": [{"id": "1A", "text": "A"}]}
        ])
        monkeypatch.setattr(case_service, "_safe_fetch_bibliography", lambda _pid: {
            "fetched_at": "2026-05-06T00:00:00Z",
            "application_date": "2023-01-31",
            "priority_date": "2022-01-31",
            "applicants": ["株式会社イングラボ", "株式会社ＣＵＴＩＣＵＬＡ"],
            "applicant": "株式会社イングラボ",
            "inventors": ["中谷 靖章"],
            "ipc": ["A61K 8/898"],
            "fi": ["A61K 8/898"],
            "fterm": ["4C083AB082"],
            "theme_code": ["4C083"],
            "theme_codes": ["4C083"],
        })

        result = case_service.create_case("特開2024-108988")
        assert result["success"] is True

        case_dir = isolated_cases_dir / "2024-108988"
        with (case_dir / "case.yaml").open(encoding="utf-8") as f:
            meta = yaml.safe_load(f)
        assert meta["application_date"] == "2023-01-31"
        assert meta["applicants"] == ["株式会社イングラボ", "株式会社ＣＵＴＩＣＵＬＡ"]
        assert meta["inventors"] == ["中谷 靖章"]
        assert meta["priority_date"] == "2022-01-31"

        with (case_dir / "hongan.json").open(encoding="utf-8") as f:
            hongan = json.load(f)
        assert hongan["ipc"] == ["A61K 8/898"]
        assert hongan["fi"] == ["A61K 8/898"]
        assert hongan["fterm"] == ["4C083AB082"]
        assert hongan["theme_code"] == ["4C083"]

        with (case_dir / "search" / "classification.json").open(encoding="utf-8") as f:
            cls = json.load(f)
        assert cls["ipc"] == [{"code": "A61K 8/898"}]
        assert cls["fterm"][0]["code"] == "4C083AB082"


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


class TestFtermShortCode:
    """Fターム フルコード → サフィックス短縮コードへの抽出"""

    def test_cosmetics_full_code(self):
        assert _fterm_short_code("4C083AB13") == "AB13"

    def test_plastic_full_code(self):
        assert _fterm_short_code("4F100AK01B") == "AK01B"

    def test_three_digit_suffix(self):
        assert _fterm_short_code("4C083AB100") == "AB100"

    def test_already_short(self):
        assert _fterm_short_code("AB13") == "AB13"

    def test_empty(self):
        assert _fterm_short_code("") == ""

    def test_none_safe(self):
        assert _fterm_short_code(None) == ""

    def test_garbage(self):
        assert _fterm_short_code("not-a-code") == "not-a-code"


class TestFiCodes:
    def test_fi_candidates_include_hongan_and_classification_and_groups(self, isolated_cases_dir):
        case_service.create_minimal_case("2030-fi", title="T")
        case_dir = isolated_cases_dir / "2030-fi"
        (case_dir / "hongan.json").write_text(
            json.dumps({"fi": ["A61K 8/898"]}, ensure_ascii=False),
            encoding="utf-8",
        )
        (case_dir / "search").mkdir()
        (case_dir / "search" / "classification.json").write_text(
            json.dumps({"fi": [{"code": "A61Q 5/04", "label": "毛髪変形"}]}, ensure_ascii=False),
            encoding="utf-8",
        )
        (case_dir / "keywords.json").write_text(
            json.dumps([{
                "group_id": 1,
                "label": "A",
                "keywords": [],
                "search_codes": {"fi": [{"code": "A61K 8/898", "desc": ""}]},
            }], ensure_ascii=False),
            encoding="utf-8",
        )

        out, code = fi_candidates("2030-fi")

        assert code == 200
        codes = [x["code"] for x in out]
        assert codes == ["A61Q 5/04", "A61K 8/898"]
        assert out[0]["label"] == "毛髪変形"

    def test_add_and_delete_fi(self, isolated_cases_dir):
        case_service.create_minimal_case("2030-fi2", title="T")
        case_dir = isolated_cases_dir / "2030-fi2"
        (case_dir / "keywords.json").write_text(
            json.dumps([{"group_id": 1, "label": "A", "keywords": [], "search_codes": {}}], ensure_ascii=False),
            encoding="utf-8",
        )

        result, code = add_fi("2030-fi2", 1, "A61K 8/898", "毛髪")
        assert code == 200
        assert result["code"] == "A61K 8/898"

        with (case_dir / "keywords.json").open(encoding="utf-8") as f:
            groups = json.load(f)
        assert groups[0]["search_codes"]["fi"] == [{"code": "A61K 8/898", "desc": "毛髪"}]

        result, code = delete_fi("2030-fi2", 1, "A61K 8/898")
        assert code == 200
        with (case_dir / "keywords.json").open(encoding="utf-8") as f:
            groups = json.load(f)
        assert groups[0]["search_codes"]["fi"] == []


class TestFindCitationPdf:
    def test_source_pdf_hint_finds_mismatched_filename(self, tmp_path):
        """citations JSON の source_pdf で、文献IDと違う名前の input PDF を辿る"""
        case_dir = tmp_path / "c"
        input_dir = case_dir / "input"
        cit_dir = case_dir / "citations"
        input_dir.mkdir(parents=True)
        cit_dir.mkdir(parents=True)
        weird = input_dir / "browser_download.pdf"
        weird.write_bytes(b"%PDF-1.3\n")
        doc_id = "特開2024-999999"
        (cit_dir / f"{doc_id}.json").write_text(
            json.dumps(
                {"patent_number": doc_id, "source_pdf": "browser_download.pdf"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        found = case_service.find_citation_pdf(input_dir, doc_id)
        assert found == weird
