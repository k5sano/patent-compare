from __future__ import annotations

import json

from services import case_service
from services import opd_dossier_service as opd


def test_classifies_isr_with_attachment_note():
    item = opd._classify_opd_document("2024-05-20 International Search Report 英訳 添付書類")

    assert item["kind"] == "ISR"
    assert "添付書類" in item["note"]


def test_classifies_iper():
    item = opd._classify_opd_document("2024-05-20 International Preliminary Report on Patentability Chapter I")

    assert item["kind"] == "IPER"


def test_classifies_cn_office_action():
    item = opd._classify_opd_document("CN Notification of Office Action 2024-01-15")

    assert item["kind"] == "CN拒絶理由"


def test_classifies_us_rejections():
    non_final = opd._classify_opd_document("US Non-Final Rejection 2023-10-01")
    final = opd._classify_opd_document("US Final Rejection 2024-04-02")

    assert non_final["kind"] == "US Non Final Rejection"
    assert final["kind"] == "US Final Rejection"


def test_ignores_transmittal_and_container_rows():
    assert opd._classify_opd_document(
        "2022-12-03 Notification of transmittal of the international search report 原文"
    ) is None
    assert opd._classify_opd_document(
        "書類情報 別画面で表示 原文PDF一括 PDFダウンロード 提出日 書類名 2024-05-20 国際調査報告（International Search Report） 2024-05-20 添付書類"
    ) is None


def test_links_attached_document_to_previous_isr():
    docs = [
        {
            "label": "2024-05-20 国際調査報告（International Search Report） 受理書類 原文 英訳",
            "text": "2024-05-20 国際調査報告（International Search Report） 受理書類 原文 英訳",
            "kind": "ISR",
            "priority": 100,
            "target": True,
        },
        {
            "label": "2024-05-20 添付書類（Attached Document） 受理書類 原文 英訳",
            "text": "2024-05-20 添付書類（Attached Document） 受理書類 原文 英訳",
            "kind": "添付書類",
            "priority": 0,
            "target": False,
        },
    ]

    targets = opd._link_attached_documents(docs)

    assert targets[0]["date"] == "2024-05-20"
    assert targets[0]["preferred_source"] == "attachment_if_available"
    assert targets[0]["attachment_labels"] == ["2024-05-20 添付書類（Attached Document） 受理書類 原文 英訳"]
    assert docs[1]["attachment_for"] == "ISR"


def test_citation_candidates_ignore_page_text_family_and_use_citation_info():
    data = {
        "case_id": "2024-533284",
        "patent_number": "特開2024-533284",
        "page_text": "JP 5548456 B2 JP 2014510173 A はファミリー列挙であり直接引用ではない",
        "citation_info_texts": [
            "引用文献 WO 2020/112595 A1 page 38 example 1; US 2016/175445 A1 paragraphs [0004]-[0055]",
        ],
        "documents": [],
    }

    candidates = opd._extract_citation_candidates_from_index(data)

    assert [c["patent_id"] for c in candidates] == ["WO2020112595A1", "US2016175445A1"]


def test_citation_candidates_include_ocr_reports():
    data = {
        "case_id": "2024-533284",
        "patent_number": "特開2024-533284",
        "documents": [],
        "ocr_reports": [{
            "label": "本願PDF内ISR OCR",
            "citations": [
                {
                    "category": "X",
                    "doc_id": "WO2020112595A1",
                    "claims": "1-3",
                    "passages": "paragraph 38",
                },
            ],
        }],
    }

    candidates = opd._extract_citation_candidates_from_index(data)

    assert candidates[0]["patent_id"] == "WO2020112595A1"
    assert candidates[0]["source"] == "opd_dossier_ocr"
    assert candidates[0]["category"] == "X"


def test_citation_candidates_include_ocr_family_reports():
    data = {
        "case_id": "2024-533284",
        "patent_number": "特開2024-533284",
        "documents": [],
        "ocr_reports": [{
            "label": "本願PDF内ISR OCR",
            "citations": [{"category": "X", "doc_id": "US2016175445A1"}],
            "family_citations": [{
                "doc_id": "JP5980304B2",
                "label": "本ISRD2易読1",
                "raw_text": "US2016175445A1 のJPファミリー",
                "family_of": "US2016175445A1",
            }],
        }],
    }

    candidates = opd._extract_citation_candidates_from_index(data)

    assert [c["patent_id"] for c in candidates] == ["US2016175445A1", "JP5980304B2"]
    assert candidates[1]["label"] == "本ISRD2易読1"
    assert candidates[1]["source"] == "opd_dossier_ocr_family"


def test_save_and_load_opd_index(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(opd, "get_case_dir", case_service.get_case_dir)
    monkeypatch.setattr(opd, "load_case_meta", case_service.load_case_meta)
    (tmp_path / "cases").mkdir()
    case_service.create_minimal_case("2030-opd", title="x")

    payload = {"case_id": "2030-opd", "documents": [], "targets": [{"kind": "ISR"}]}
    path = opd.save_opd_index("2030-opd", payload)
    loaded, code = opd.load_opd_index("2030-opd")

    assert code == 200
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["targets"][0]["kind"] == "ISR"
    assert loaded["exists"] is True
    assert loaded["targets"][0]["kind"] == "ISR"


def test_rebuild_ocr_reports_returns_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(opd, "get_case_dir", case_service.get_case_dir)
    monkeypatch.setattr(opd, "load_case_meta", case_service.load_case_meta)
    monkeypatch.setattr(opd, "_hongan_patent_number", lambda case_id: "特開2030-123456")
    monkeypatch.setattr(opd, "_build_ocr_reports", lambda case_id: [{
        "label": "本願PDF内ISR OCR",
        "citations": [{"category": "Y", "doc_id": "US2016175445A1"}],
        "family_citations": [{"doc_id": "JP5980304B2", "label": "本ISRD2易読1"}],
        "raw_text": "Y US2016175445A1",
        "raw_text_length": 17,
    }])
    (tmp_path / "cases").mkdir()
    case_service.create_minimal_case("2030-opd", title="x")

    data, code = opd.rebuild_ocr_reports("2030-opd")

    assert code == 200
    assert data["ocr_reports"][0]["label"] == "本願PDF内ISR OCR"
    assert data["ocr_scope"] == "hongan_embedded_isr"
    assert data["citation_candidates"][0]["patent_id"] == "US2016175445A1"
    assert data["citation_candidates"][1]["patent_id"] == "JP5980304B2"
    cache = tmp_path / "cases" / "2030-opd" / "dossier" / "opd_ocr_reports.json"
    assert json.loads(cache.read_text(encoding="utf-8"))["reports"][0]["citations"][0]["category"] == "Y"


def test_extract_citation_candidates_marks_loaded_and_urls(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(opd, "get_case_dir", case_service.get_case_dir)
    monkeypatch.setattr(opd, "load_case_meta", case_service.load_case_meta)
    monkeypatch.setattr(opd, "_build_ocr_reports", lambda case_id: [{
        "label": "本願PDF内ISR OCR",
        "citations": [{"doc_id": "US2016175445A1"}],
        "family_citations": [{"doc_id": "JP5980304B2", "label": "本ISRD2易読1"}],
    }])
    (tmp_path / "cases").mkdir()
    case_service.create_minimal_case("2030-opd", title="x")
    case_dir = case_service.get_case_dir("2030-opd")
    (case_dir / "citations").mkdir(exist_ok=True)
    (case_dir / "citations" / "US2016175445A1.json").write_text(
        json.dumps({"patent_number": "US2016175445A1"}),
        encoding="utf-8",
    )

    data, code = opd.extract_citation_candidates("2030-opd")

    assert code == 200
    by_id = {c["patent_id"]: c for c in data["candidates"]}
    assert by_id["US2016175445A1"]["loaded"] is True
    assert by_id["JP5980304B2"]["loaded"] is False
    assert by_id["JP5980304B2"]["jplatpat_url"]
    assert by_id["JP5980304B2"]["google_patents_url"]


def test_rejection_documents_list_opd_targets_and_search_report_text(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(opd, "get_case_dir", case_service.get_case_dir)
    monkeypatch.setattr(opd, "load_case_meta", case_service.load_case_meta)
    monkeypatch.setattr(opd, "_load_or_build_ocr_reports", lambda case_id: [])
    (tmp_path / "cases").mkdir()
    case_service.create_minimal_case("2030-opd", title="x")
    case_dir = case_service.get_case_dir("2030-opd")
    dossier_dir = case_dir / "dossier"
    dossier_dir.mkdir()
    (dossier_dir / "opd_index.json").write_text(json.dumps({
        "case_id": "2030-opd",
        "documents": [
            {
                "kind": "US Final Rejection",
                "label": "2025-01-01 US Final Rejection 原文",
                "text": "2025-01-01 US Final Rejection 原文",
                "target": True,
            }
        ],
    }), encoding="utf-8")
    reports_dir = case_dir / "search_reports"
    reports_dir.mkdir()
    (reports_dir / "search_reports.json").write_text(json.dumps({
        "reports": [{
            "filename": "IPER.pdf",
            "form": "IPER",
            "box_v": "Novelty: No. Inventive step: No. D1 discloses claim 1.",
        }]
    }), encoding="utf-8")

    data, code = opd.get_rejection_documents("2030-opd")

    assert code == 200
    by_kind = {d["kind"]: d for d in data["documents"]}
    assert by_kind["US Final Rejection"]["status"] == "needs_pdf_ocr"
    assert "本願内ISR OCRでは処理されません" in by_kind["US Final Rejection"]["note"]
    assert by_kind["IPER"]["status"] == "ready"
    assert by_kind["IPER"]["has_text"] is True


def test_summarize_rejection_documents_caches_llm_result(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(opd, "get_case_dir", case_service.get_case_dir)
    monkeypatch.setattr(opd, "load_case_meta", case_service.load_case_meta)
    monkeypatch.setattr(opd, "_load_or_build_ocr_reports", lambda case_id: [])
    monkeypatch.setattr("modules.claude_client.call_claude", lambda prompt, timeout=300, model=None: "日本語要約")
    (tmp_path / "cases").mkdir()
    case_service.create_minimal_case("2030-opd", title="x")
    case_dir = case_service.get_case_dir("2030-opd")
    dossier_dir = case_dir / "dossier"
    dossier_dir.mkdir()
    (dossier_dir / "opd_index.json").write_text(json.dumps({"case_id": "2030-opd", "documents": []}), encoding="utf-8")
    reports_dir = case_dir / "search_reports"
    reports_dir.mkdir()
    (reports_dir / "search_reports.json").write_text(json.dumps({
        "reports": [{"filename": "IPER.pdf", "form": "IPER", "box_v": "Inventive step: No."}]
    }), encoding="utf-8")

    data, code = opd.summarize_rejection_documents("2030-opd", model="glm-opus")

    assert code == 200
    assert data["documents"][0]["ja_summary"] == "日本語要約"
    cache = json.loads((dossier_dir / "opd_rejection_summaries.json").read_text(encoding="utf-8"))
    assert next(iter(cache["items"].values()))["model"] == "glm-opus"


def test_ingest_opd_pdf_file_adds_ocr_report_to_rejections(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(opd, "get_case_dir", case_service.get_case_dir)
    monkeypatch.setattr(opd, "load_case_meta", case_service.load_case_meta)
    monkeypatch.setattr(opd, "_load_or_build_ocr_reports", lambda case_id: [])
    monkeypatch.setattr(opd, "_parse_opd_pdf_report", lambda case_id, pdf_path, meta: {
        "kind": "IPER",
        "label": meta["label"],
        "source": "opd_attached_pdf",
        "filename": pdf_path.name,
        "path": str(pdf_path),
        "box_v": "Inventive step: No. D1 discloses claim 1.",
        "raw_text": "Inventive step: No. D1 discloses claim 1.",
        "raw_text_length": 42,
        "citations": [],
    })
    (tmp_path / "cases").mkdir()
    case_service.create_minimal_case("2030-opd", title="x")
    case_dir = case_service.get_case_dir("2030-opd")
    dossier_dir = case_dir / "dossier"
    dossier_dir.mkdir()
    (dossier_dir / "opd_index.json").write_text(json.dumps({"case_id": "2030-opd", "documents": []}), encoding="utf-8")
    src = tmp_path / "iper.pdf"
    src.write_bytes(b"%PDF-1.4\n%%EOF")

    data, code = opd.ingest_opd_pdf_file("2030-opd", src, label="IPER imported")

    assert code == 200
    assert data["success"] is True
    assert data["documents"][0]["status"] == "ready"
    assert data["documents"][0]["label"] == "IPER imported"
    reports = json.loads((dossier_dir / "opd_pdf_reports.json").read_text(encoding="utf-8"))
    assert reports["reports"][0]["source"] == "opd_attached_pdf"
