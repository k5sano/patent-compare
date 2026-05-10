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
