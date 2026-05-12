from __future__ import annotations

import json
import time

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


def test_downloadable_rejection_targets_require_safe_attachment():
    targets = [
        {
            "kind": "IPER",
            "label": "2024-05-20 特許性に関する国際予備報告 原文 英訳",
            "attachment_labels": [
                "2024-05-20 添付書類（Attached Document） 受理書類 原文 英訳",
                "2024-05-20 添付書類（Attached Document） 出願書類 受理書類 分類情報 原文 英訳",
            ],
        },
        {
            "kind": "IPER",
            "label": "2023-12-03 International Preliminary Report on Patentability Chapter I 原文",
            "attachment_labels": [],
        },
        {
            "kind": "ISR",
            "label": "2024-05-20 International Search Report",
            "attachment_labels": ["2024-05-20 添付書類（Attached Document） 受理書類 原文 英訳"],
        },
    ]

    downloadable, skipped = opd._downloadable_rejection_targets(targets)

    assert [t["label"] for t in downloadable] == ["2024-05-20 特許性に関する国際予備報告 原文 英訳"]
    assert len(skipped) == 1
    assert skipped[0]["skipped"] is True


def test_downloadable_rejection_targets_exclude_application_attachment():
    targets = [{
        "kind": "IPER",
        "label": "2024-05-20 特許性に関する国際予備報告 原文 英訳",
        "attachment_labels": [
            "2024-05-20 添付書類（Attached Document） 出願書類 受理書類 分類情報 原文 英訳",
        ],
    }]

    downloadable, skipped = opd._downloadable_rejection_targets(targets)

    assert downloadable == []
    assert skipped[0]["error"].startswith("OPD上で添付PDFの実体行")


def test_downloadable_rejection_targets_allow_wo_original_iper():
    targets = [{
        "kind": "IPER",
        "label": "2023-12-03 International Preliminary Report on Patentability Chapter I 発送書類 最終処分 分類情報 原文",
        "attachment_labels": [],
    }]

    downloadable, skipped = opd._downloadable_rejection_targets(targets)

    assert skipped == []
    assert downloadable[0]["preferred_source"] == "original_if_no_attachment"


def test_suppress_covered_skipped_downloads_hides_duplicate_failures():
    downloads = [
        {"kind": "IPER", "date": "2024-05-20", "label": "IPER attached", "success": True},
        {"kind": "IPER", "date": "2024-05-20", "label": "IPER cover", "success": False, "skipped": True},
        {"kind": "US Final Rejection", "date": "2024-06-01", "label": "US", "success": False, "skipped": True},
    ]

    filtered = opd._suppress_covered_skipped_downloads(downloads)

    assert [d["label"] for d in filtered] == ["IPER attached", "US"]


def test_scrape_documents_from_visible_text_recovers_opd_rows():
    docs = []
    seen = set()
    text = """
    書類情報を全て閉じる
    2024-05-20
    特許性に関する国際予備報告（第Ｉ章）（International Preliminary Report on Patentability (Chapter I of the Patent Cooperation Treaty)）
    受理書類
    原文 英訳
    2024-05-20
    添付書類（Attached Document）
    受理書類
    原文 英訳
    """

    opd._scrape_documents_from_text(text, docs, seen)

    assert any(d["kind"] == "IPER" and d["target"] for d in docs)
    assert any(d["kind"] == "添付書類" and not d["target"] for d in docs)


def test_iter_pdf_url_candidates_finds_docu_url_and_pdf_values():
    payload = {
        "DOCU_INFO_PART": {"DOCU_URL": "/app/pdf/opd-doc.pdf"},
        "items": [
            {"name": "cover", "url": "/not-pdf"},
            {"pdfUrl": "https://example.test/file.pdf"},
        ],
    }

    urls = opd._iter_pdf_url_candidates(payload)

    assert "/app/pdf/opd-doc.pdf" in urls
    assert "https://example.test/file.pdf" in urls


def test_is_pdf_bytes_accepts_magic_or_content_type():
    assert opd._is_pdf_bytes(b"%PDF-1.7\n", "")
    assert not opd._is_pdf_bytes(b"data", "application/pdf")
    assert not opd._is_pdf_bytes(b"<!doctype html>", "application/pdf")
    assert not opd._is_pdf_bytes(b"html", "text/html")


def test_focus_rejection_summary_text_prefers_box_v():
    text = "cover " * 1000 + "Box No.V__ Reasoned statement under Rule 43bis.1(a)(i)\n" + "important " * 1000

    focused = opd._focus_rejection_summary_text(text)

    assert "Box No.V__ Reasoned statement" in focused
    assert len(focused) < len(text)


def test_find_opd_download_recipe_matches_target(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(opd, "get_case_dir", case_service.get_case_dir)
    (tmp_path / "cases" / "2030-opd" / "dossier").mkdir(parents=True)
    body = {
        "DOC_ID": "Attached_Document_123_JP",
        "FILE_TYPE": "PDF",
        "INPUT": {"CNTRY_CD_PART": "JP", "NUM_PART": "2030123456", "KIND_CD_PART": "A"},
    }
    (tmp_path / "cases" / "2030-opd" / "dossier" / "opd_download_signals.json").write_text(json.dumps({
        "case_id": "2030-opd",
        "signals": [{
            "captured_at": "2030-01-01T00:00:00+00:00",
            "kind": "IPER",
            "label": "2030-01-02 International Preliminary Report 原文",
            "date": "2030-01-02",
            "entries": [{
                "url": "https://www.j-platpat.inpit.go.jp/app/opdgw/wsh0901",
                "post_data": json.dumps(body),
            }],
        }],
    }), encoding="utf-8")

    recipe = opd._find_opd_download_recipe("2030-opd", {
        "kind": "IPER",
        "label": "2030-01-02 International Preliminary Report 原文",
        "date": "2030-01-02",
    })

    assert recipe["body"]["DOC_ID"] == "Attached_Document_123_JP"
    assert recipe["endpoint"].endswith("/app/opdgw/wsh0901")


def test_click_opd_toolbar_button_falls_back_to_dom_click(monkeypatch):
    calls = {}

    class FakePage:
        frames = []
        main_frame = None

        def evaluate(self, script, arg):
            calls["script"] = script
            calls["arg"] = arg
            return True

    monkeypatch.setattr(opd, "_click_first_visible", lambda *_args, **_kwargs: False)

    assert opd._click_opd_toolbar_button(FakePage(), ["書類情報を全て開く"], []) is True
    assert "clickableAncestor" in calls["script"]
    assert calls["arg"] == {"labels": ["書類情報を全て開く", "書類情報をすべて開く"]}


def test_click_opd_toolbar_button_searches_child_frames(monkeypatch):
    calls = []

    class FakeFrame:
        def __init__(self, name, result):
            self.name = name
            self.result = result

        def evaluate(self, script, arg):
            calls.append((self.name, arg))
            return self.result

    class FakePage(FakeFrame):
        def __init__(self):
            super().__init__("page", False)
            self.main_frame = object()
            self.frames = [self.main_frame, FakeFrame("child", True)]

    monkeypatch.setattr(opd, "_click_first_visible", lambda *_args, **_kwargs: False)

    assert opd._click_opd_toolbar_button(FakePage(), ["書類情報を全て開く"], []) is True
    assert [name for name, _ in calls] == ["page", "child"]


def test_documents_look_expanded_when_target_and_attachment_are_present():
    docs = [
        {"kind": "IPER", "target": True, "text": "2030-01-02 International Preliminary Report 原文"},
        {"kind": "添付書類", "target": False, "text": "2030-01-02 添付書類（Attached Document） 原文"},
    ]

    assert opd._documents_look_expanded(docs) is True
    assert opd._documents_look_expanded([{"kind": "IPER", "target": True, "text": "IPER"}]) is False


def test_download_rejection_pdfs_uses_saved_recipe_without_row(monkeypatch):
    sess = opd.OpdDossierSession()
    sess._page = object()
    monkeypatch.setattr(sess, "_ensure_active_page", lambda: sess._page)
    monkeypatch.setattr(opd, "_dismiss_modals", lambda _page: None)
    monkeypatch.setattr(opd, "_click_expand_all_documents", lambda _page: False)
    monkeypatch.setattr(opd, "_try_direct_opd_recipe_download", lambda _page, _case_id, _target: (True, "saved.pdf"))

    def fail_find_row(*_args, **_kwargs):
        raise AssertionError("row lookup should not be needed when a saved recipe works")

    monkeypatch.setattr(opd, "_find_opd_attachment_row_for_target", fail_find_row)

    result = sess._op_download_rejection_pdfs("2030-opd", [{
        "kind": "IPER",
        "label": "2030-01-02 International Preliminary Report",
        "date": "2030-01-02",
    }])

    assert result["downloads"][0]["success"] is True
    assert result["downloads"][0]["path"] == "saved.pdf"
    assert result["downloads"][0]["resolved_by"] == "saved_wsh0901_recipe"


def test_select_opd_page_prefers_h0200_window():
    class FakeLocator:
        def __init__(self, text):
            self.text = text

        def inner_text(self, timeout=800):
            return self.text

    class FakePage:
        def __init__(self, url, body):
            self.url = url
            self.body = body
            self.front = False

        def title(self, timeout=500):
            return ""

        def locator(self, selector):
            return FakeLocator(self.body)

        def bring_to_front(self):
            self.front = True

        def wait_for_timeout(self, ms):
            pass

        def is_closed(self):
            return False

    sess = opd.OpdDossierSession()
    fixed = FakePage("https://www.j-platpat.inpit.go.jp/c1801/PU/JP-2030-123456/11/ja", "")
    opd_page = FakePage("https://www.j-platpat.inpit.go.jp/h0200", "書類情報を全て開く")
    sess._ctx = type("Ctx", (), {"pages": [fixed, opd_page]})()
    sess._page = fixed

    assert sess._select_opd_page() is opd_page
    assert sess._page is opd_page
    assert opd_page.front is True


def test_ensure_active_page_recreates_closed_page():
    class FakePage:
        def __init__(self, closed=False):
            self._closed = closed

        def is_closed(self):
            return self._closed

    class FakeContext:
        def __init__(self):
            self.created = []

        def new_page(self):
            page = FakePage(False)
            self.created.append(page)
            return page

    class FakeBrowser:
        def is_connected(self):
            return True

    sess = opd.OpdDossierSession()
    sess._browser = FakeBrowser()
    sess._ctx = FakeContext()
    sess._page = FakePage(True)

    page = sess._ensure_active_page()

    assert page is sess._ctx.created[0]
    assert sess._page is page


def test_rejection_documents_opd_pdf_covers_same_date_kind_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(opd, "get_case_dir", case_service.get_case_dir)
    case_dir = tmp_path / "cases" / "2030-opd"
    case_dir.mkdir(parents=True)
    (case_dir / "meta.json").write_text(json.dumps({"case_id": "2030-opd"}), encoding="utf-8")

    data = {
        "documents": [{
            "kind": "IPER",
            "target": True,
            "date": "2030-01-02",
            "text": "2030-01-02 International Preliminary Report on Patentability Chapter I 発送書類 原文",
        }],
        "opd_pdf_reports": [{
            "kind": "WOSA",
            "label": "2030-01-02 特許性に関する国際予備報告（第I章） Attached Document",
            "date": "2030-01-02",
            "raw_text": "Box No.V Reasoned statement text",
        }],
    }

    docs = opd._build_rejection_documents("2030-opd", data)

    assert len(docs) == 1
    assert docs[0]["source"] == "opd_attached_pdf"


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


def test_citation_candidates_include_opd_attached_pdf_reports():
    data = {
        "case_id": "2024-533284",
        "patent_number": "特開2024-533284",
        "documents": [],
        "opd_pdf_reports": [{
            "label": "IPER attached PDF",
            "kind": "WOSA",
            "citations": [
                {"category": "D1", "doc_id": "WO2020112595A1", "doc_label": "WO 2020/112595 A1"},
                {"category": "D2", "doc_id": "US2016175445A1", "doc_label": "US 2016/175445 A1"},
            ],
        }],
    }

    candidates = opd._extract_citation_candidates_from_index(data)

    assert [c["patent_id"] for c in candidates] == ["WO2020112595A1", "US2016175445A1"]
    assert candidates[0]["source"] == "opd_attached_pdf_ocr"
    assert candidates[0]["source_label"] == "IPER attached PDF"


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


def test_opd_timing_is_saved_and_loaded_with_index(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(opd, "get_case_dir", case_service.get_case_dir)
    monkeypatch.setattr(opd, "load_case_meta", case_service.load_case_meta)
    (tmp_path / "cases").mkdir()
    case_service.create_minimal_case("2030-opd", title="x")
    opd.save_opd_index("2030-opd", {"case_id": "2030-opd", "documents": [], "targets": []})

    timing = opd._new_opd_timing("2030-opd", "collect_opd_documents")
    started = time.perf_counter()
    opd._add_timing_step(timing, "fetch", started, documents=3)
    saved = opd._finish_opd_timing("2030-opd", timing, status="ok", document_count=3)

    loaded, code = opd.load_opd_index("2030-opd")

    assert code == 200
    assert saved["operation"] == "collect_opd_documents"
    assert saved["steps"]["fetch"] >= 0
    assert loaded["opd_timing"]["document_count"] == 3
    assert loaded["opd_timing"]["events"][0]["documents"] == 3


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
    assert data["opd_timing"]["operation"] == "rebuild_embedded_isr_ocr"
    assert "ocr" in data["opd_timing"]["steps"]
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


def test_rejection_documents_use_embedded_isr_ocr_text(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(opd, "get_case_dir", case_service.get_case_dir)
    monkeypatch.setattr(opd, "load_case_meta", case_service.load_case_meta)
    monkeypatch.setattr(opd, "_load_or_build_ocr_reports", lambda case_id: [{
        "kind": "ISR",
        "label": "本願PDF内ISR OCR",
        "source": "hongan_pdf_embedded_isr",
        "raw_text": "INTERNATIONAL SEARCH REPORT\nC. DOCUMENTS CONSIDERED TO BE RELEVANT\nWO 2020/112595 A1",
        "raw_text_length": 86,
        "citations": [{"doc_id": "WO2020112595A1"}],
    }])
    (tmp_path / "cases").mkdir()
    case_service.create_minimal_case("2030-opd", title="x")
    dossier_dir = case_service.get_case_dir("2030-opd") / "dossier"
    dossier_dir.mkdir()
    (dossier_dir / "opd_index.json").write_text(json.dumps({
        "case_id": "2030-opd",
        "documents": [],
    }), encoding="utf-8")

    data, code = opd.get_rejection_documents("2030-opd")

    assert code == 200
    isr = next(d for d in data["documents"] if d["kind"] == "ISR")
    assert isr["status"] == "ready"
    assert isr["has_text"] is True
    assert "引用抽出に使用した保存済みOCR本文" in isr["note"]


def test_rejection_documents_hide_opd_candidate_covered_by_attached_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(opd, "get_case_dir", case_service.get_case_dir)
    monkeypatch.setattr(opd, "load_case_meta", case_service.load_case_meta)
    monkeypatch.setattr(opd, "_load_or_build_ocr_reports", lambda case_id: [])
    (tmp_path / "cases").mkdir()
    case_service.create_minimal_case("2030-opd", title="x")
    case_dir = case_service.get_case_dir("2030-opd")
    dossier_dir = case_dir / "dossier"
    dossier_dir.mkdir()
    label = "2024-05-20 International Preliminary Report on Patentability 原文"
    (dossier_dir / "opd_index.json").write_text(json.dumps({
        "case_id": "2030-opd",
        "documents": [{
            "kind": "IPER",
            "label": label,
            "text": label,
            "target": True,
            "date": "2024-05-20",
        }],
    }), encoding="utf-8")
    (dossier_dir / "opd_pdf_reports.json").write_text(json.dumps({
        "case_id": "2030-opd",
        "reports": [{
            "kind": "WOSA",
            "label": label,
            "date": "2024-05-20",
            "raw_text": "Reasoned statement. Inventive step: No.",
            "raw_text_length": 40,
        }],
    }), encoding="utf-8")

    data, code = opd.get_rejection_documents("2030-opd")

    assert code == 200
    assert len(data["documents"]) == 1
    assert data["documents"][0]["kind"] == "WOSA"
    assert data["documents"][0]["status"] == "ready"


def test_rejection_documents_hide_empty_search_report_covered_by_opd_pdf(tmp_path, monkeypatch):
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
        "documents": [],
    }), encoding="utf-8")
    (dossier_dir / "opd_pdf_reports.json").write_text(json.dumps({
        "case_id": "2030-opd",
        "reports": [{
            "kind": "WOSA",
            "label": "2030-01-01 International Preliminary Report on Patentability Chapter I",
            "date": "2030-01-01",
            "raw_text": "reasoned statement",
            "raw_text_length": 18,
        }],
    }), encoding="utf-8")
    reports_dir = case_dir / "search_reports"
    reports_dir.mkdir()
    (reports_dir / "search_reports.json").write_text(json.dumps({
        "reports": [{"filename": "IPER.pdf", "form": "WOSA", "box_v": ""}],
    }), encoding="utf-8")

    data, code = opd.load_opd_index("2030-opd")

    assert code == 200
    assert len(data["rejection_documents"]) == 1
    assert data["rejection_documents"][0]["source"] == "opd_attached_pdf"


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


def test_rejection_documents_parse_saved_search_report_pdf_when_box_v_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(opd, "get_case_dir", case_service.get_case_dir)
    monkeypatch.setattr(opd, "load_case_meta", case_service.load_case_meta)
    monkeypatch.setattr(opd, "_load_or_build_ocr_reports", lambda case_id: [])
    monkeypatch.setattr("modules.search_report_parser.parse_search_report", lambda path: {
        "form": "WOSA",
        "box_v": "",
        "raw_text": "parsed IPER raw text",
    })
    (tmp_path / "cases").mkdir()
    case_service.create_minimal_case("2030-opd", title="x")
    case_dir = case_service.get_case_dir("2030-opd")
    dossier_dir = case_dir / "dossier"
    dossier_dir.mkdir()
    (dossier_dir / "opd_index.json").write_text(json.dumps({"case_id": "2030-opd", "documents": []}), encoding="utf-8")
    reports_dir = case_dir / "search_reports"
    reports_dir.mkdir()
    (reports_dir / "IPER.pdf").write_bytes(b"%PDF-1.7\n")
    (reports_dir / "search_reports.json").write_text(json.dumps({
        "reports": [{"filename": "IPER.pdf", "form": "WOSA", "box_v": ""}],
    }), encoding="utf-8")

    data, code = opd.load_opd_index("2030-opd")

    assert code == 200
    docs = data["rejection_documents"]
    assert docs[0]["source"] == "search_report"
    assert docs[0]["status"] == "ready"
    assert docs[0]["has_text"] is True
    assert docs[0]["text_preview"] == "parsed IPER raw text"


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
