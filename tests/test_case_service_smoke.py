"""case_service 分割前の黒箱スモークテスト。"""
from __future__ import annotations

import json

import yaml

from services import case_service


def test_load_case_meta_fallback_resolves_by_patent_number(copy_case_fixture):
    copy_case_fixture("smoke")

    meta = case_service.load_case_meta("特開2030-000001")

    assert meta["case_id"] == "smoke"
    assert meta["patent_number"] == "特開2030-000001"


def test_update_case_meta_sets_and_removes_optional_fields(copy_case_fixture):
    case_dir = copy_case_fixture("smoke")

    result, code = case_service.update_case_meta("smoke", {
        "patent_title": "更新後タイトル",
        "field": "laminate",
        "year": "",
        "priority_date": "2029-01-02",
    })

    assert code == 200
    assert result["success"] is True
    meta = yaml.safe_load((case_dir / "case.yaml").read_text(encoding="utf-8"))
    assert meta["patent_title"] == "更新後タイトル"
    assert meta["field"] == "laminate"
    assert meta["priority_date"] == "2029-01-02"
    assert "year" not in meta


def test_upload_citation_registers_normalized_doc_id(copy_case_fixture, monkeypatch, tmp_path):
    case_dir = copy_case_fixture("smoke")
    src = tmp_path / "download.pdf"
    src.write_bytes(b"%PDF-1.4\n")

    def fake_extract(path, kind):
        assert kind == "citation"
        assert path == str(src)
        return {
            "patent_number": "US 2016/0175445 A1",
            "patent_title": "Uploaded Citation",
            "claims": [{"number": 1, "text": "A composition."}],
            "paragraphs": [{"id": "0001", "text": "A composition is disclosed."}],
        }

    monkeypatch.setattr("modules.pdf_extractor.extract_patent_pdf", fake_extract)

    result, code = case_service.upload_citation("smoke", src, role="副引例", label="D2")

    assert code == 200
    assert result["success"] is True
    assert result["doc_id"] == "US20160175445"
    assert (case_dir / "citations" / "US20160175445.json").exists()
    assert (case_dir / "input" / "US20160175445.pdf").exists()
    meta = case_service.load_case_meta("smoke")
    assert {"id": "US20160175445", "role": "副引例", "label": "D2"} in meta["citations"]


def test_delete_citation_removes_registered_files_and_meta(copy_case_fixture):
    case_dir = copy_case_fixture("smoke")
    (case_dir / "prompts" / "JP2030000002A_prompt.txt").write_text("prompt", encoding="utf-8")

    result, code = case_service.delete_citation("smoke", "JP2030000002A")

    assert code == 200
    assert result["success"] is True
    assert not (case_dir / "citations" / "JP2030000002A.json").exists()
    assert not (case_dir / "responses" / "JP2030000002A.json").exists()
    assert not (case_dir / "prompts" / "JP2030000002A_prompt.txt").exists()
    assert case_service.load_case_meta("smoke")["citations"] == []


def test_delete_citation_removes_spaced_jplatpat_variants(copy_case_fixture):
    case_dir = copy_case_fixture("smoke")
    meta = case_service.load_case_meta("smoke")
    meta["citations"] = [{
        "id": "JPA1993042929-000000",
        "role": "主引例",
        "label": "JPA1993042929-000000",
    }]
    case_service.save_case_meta("smoke", meta)
    for sub in ("citations", "responses", "prompts", "input"):
        (case_dir / sub).mkdir(exist_ok=True)
    (case_dir / "citations" / "JPA1993042929-000000.json").write_text("{}", encoding="utf-8")
    (case_dir / "responses" / "JPA 1993042929-000000.json").write_text("{}", encoding="utf-8")
    (case_dir / "responses" / "JPA1993042929-000000.json").write_text("{}", encoding="utf-8")
    (case_dir / "prompts" / "JPA 1993042929-000000.txt").write_text("prompt", encoding="utf-8")
    (case_dir / "prompts" / "A_B_JPA1993042929-000000_prompt.txt").write_text("prompt", encoding="utf-8")
    (case_dir / "input" / "JPA1993042929-000000.pdf").write_bytes(b"%PDF-1.4")

    result, code = case_service.delete_citation("smoke", "JPA1993042929-000000")

    assert code == 200
    assert result["success"] is True
    assert not (case_dir / "citations" / "JPA1993042929-000000.json").exists()
    assert not (case_dir / "responses" / "JPA 1993042929-000000.json").exists()
    assert not (case_dir / "responses" / "JPA1993042929-000000.json").exists()
    assert not (case_dir / "prompts" / "JPA 1993042929-000000.txt").exists()
    assert not (case_dir / "prompts" / "A_B_JPA1993042929-000000_prompt.txt").exists()
    assert not (case_dir / "input" / "JPA1993042929-000000.pdf").exists()
    assert case_service.load_case_meta("smoke")["citations"] == []


def test_clear_all_citations_keeps_case_but_empties_work_files(copy_case_fixture):
    case_dir = copy_case_fixture("smoke")
    (case_dir / "prompts" / "JP2030000002A_prompt.txt").write_text("prompt", encoding="utf-8")

    result, code = case_service.clear_all_citations("smoke")

    assert code == 200
    assert result["success"] is True
    assert case_service.load_case_meta("smoke")["case_id"] == "smoke"
    assert case_service.load_case_meta("smoke")["citations"] == []
    for sub in ("citations", "responses", "prompts"):
        assert list((case_dir / sub).iterdir()) == []
