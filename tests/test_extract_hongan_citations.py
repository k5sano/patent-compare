"""本願明細書から 【特許文献N】 を抽出する extract_hongan_citations のテスト。

ユーザー要望: 本願 PDF の「【特許文献2】特開2021-54031」のような記載を
自動抽出して「本願引用N」として登録できるようにする。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import case_service


@pytest.fixture
def case_with_hongan(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    (tmp_path / "cases").mkdir()
    case_id = "2030-extref"
    case_service.create_minimal_case(case_id, title="x", field="cosmetics")
    case_dir = tmp_path / "cases" / case_id

    hongan = {
        "patent_number": "JP2030-001",
        "patent_title": "テスト発明",
        "total_pages": 5,
        "claims": [],
        "tables": [],
        "paragraphs": [
            {"id": "0006", "page": 2, "section": "背景技術",
             "text": "従来から知られている。【特許文献１】特開2021-54031号には、A が記載されている。"},
            {"id": "0007", "page": 2, "section": "課題",
             "text": "【特許文献2】特開２０２０－１６９１２８号公報も参照。"},
            {"id": "0008", "page": 2, "section": "課題",
             "text": "【特許文献 3】WO2019/180364A1 にもある。【非特許文献1】学会誌 ABC 2020"},
            {"id": "0009", "page": 2, "section": "背景技術",
             "text": "【特許文献４】特許第6789012号 公報"},
        ],
    }
    with (case_dir / "hongan.json").open("w", encoding="utf-8") as f:
        json.dump(hongan, f, ensure_ascii=False)
    return case_id


class TestExtract:
    def test_basic_extraction(self, case_with_hongan):
        result, code = case_service.extract_hongan_citations(case_with_hongan)
        assert code == 200
        refs = result["refs"]
        # 4 件の【特許文献N】(非特許文献は除外)
        assert len(refs) == 4
        # ref_no 順
        nos = [r["ref_no"] for r in refs]
        assert nos == [1, 2, 3, 4]

    def test_full_width_digits_handled(self, case_with_hongan):
        """全角数字 (２０２０－１６９１２８) も正しく半角化される"""
        refs = case_service.extract_hongan_citations(case_with_hongan)[0]["refs"]
        ref2 = next(r for r in refs if r["ref_no"] == 2)
        assert ref2["patent_id"] == "特開2020-169128号"

    def test_full_width_marker_number(self, case_with_hongan):
        """マーカー側の番号が全角 (【特許文献１】) でも認識される"""
        refs = case_service.extract_hongan_citations(case_with_hongan)[0]["refs"]
        ref1 = next(r for r in refs if r["ref_no"] == 1)
        assert "特開2021-54031" in ref1["patent_id"]

    def test_label_is_hongan_inyou_n(self, case_with_hongan):
        refs = case_service.extract_hongan_citations(case_with_hongan)[0]["refs"]
        labels = [r["label"] for r in refs]
        assert labels == ["本願引用1", "本願引用2", "本願引用3", "本願引用4"]

    def test_wo_format(self, case_with_hongan):
        refs = case_service.extract_hongan_citations(case_with_hongan)[0]["refs"]
        ref3 = next(r for r in refs if r["ref_no"] == 3)
        assert "WO2019" in ref3["patent_id"]
        assert "180364" in ref3["patent_id"]

    def test_tokkyo_format(self, case_with_hongan):
        refs = case_service.extract_hongan_citations(case_with_hongan)[0]["refs"]
        ref4 = next(r for r in refs if r["ref_no"] == 4)
        assert "特許第6789012号" in ref4["patent_id"] or "特許6789012" in ref4["patent_id"]

    def test_embedded_isr_citations_are_added(self, case_with_hongan, tmp_path, monkeypatch):
        pdf_path = tmp_path / "hongan_with_isr.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n%test\n")

        monkeypatch.setattr(
            case_service,
            "_resolve_hongan_pdf_for_isr_scan",
            lambda case_id: pdf_path,
        )
        monkeypatch.setattr(
            case_service,
            "_parse_embedded_isr_search_report",
            lambda path: {
                "form": "ISR",
                "citations": [
                    {
                        "num": 1,
                        "category": "X",
                        "doc_label": "WO 2020/112595 A1 (OREAL)",
                        "doc_id": "WO2020112595A1",
                        "claims": "1-3",
                        "passages": "page 38; example 1",
                    },
                    {
                        "num": 2,
                        "category": "Y",
                        "doc_label": "US 2016/175445 A1 (LU)",
                        "doc_id": "US2016175445A1",
                        "claims": "14-19",
                        "passages": "paragraphs [0004]-[0055]",
                    },
                ],
            },
        )

        refs = case_service.extract_hongan_citations(case_with_hongan)[0]["refs"]

        assert [r["label"] for r in refs[-2:]] == ["ISR引用1", "ISR引用2"]
        assert [r["ref_no"] for r in refs[-2:]] == [5, 6]
        assert refs[-2]["source"] == "isr"
        assert refs[-2]["patent_id"] == "WO2020112595A1"

    def test_embedded_isr_jp_family_members_are_added_as_readable_refs(
        self, case_with_hongan, tmp_path, monkeypatch
    ):
        pdf_path = tmp_path / "hongan_with_isr.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n%test\n")

        monkeypatch.setattr(
            case_service,
            "_resolve_hongan_pdf_for_isr_scan",
            lambda case_id: pdf_path,
        )
        monkeypatch.setattr(
            case_service,
            "_parse_embedded_isr_search_report",
            lambda path: {
                "form": "ISR",
                "citations": [
                    {
                        "num": 1,
                        "category": "X",
                        "doc_label": "WO 2020/112595 A1 (OREAL)",
                        "doc_id": "WO2020112595A1",
                        "claims": "1-3",
                        "passages": "page 38; example 1",
                    },
                    {
                        "num": 2,
                        "category": "Y",
                        "doc_label": "US 2016/175445 A1 (LU)",
                        "doc_id": "US2016175445A1",
                        "claims": "14-19",
                        "passages": "paragraphs [0004]-[0055]",
                    },
                ],
                "raw_text": """
Information on patent family members
cited in search repo date member(s) date
WO 2020112595 Al 04-06-2020 US 2022047469 Al 17-02-2022
US 2016175445 Al 23-06-2016 CN 102639097 A 15-08-2012
JP 5980304 B2 31-08-2016
JP 2014510173 A 24-04-2014
10
JP 2030-000001 A 2030.1.1
""",
            },
        )

        refs = case_service.extract_hongan_citations(case_with_hongan)[0]["refs"]
        family_refs = [r for r in refs if r["source"] == "isr_family"]

        assert [r["label"] for r in family_refs] == ["本ISRD2易読1", "本ISRD2易読2"]
        assert [r["patent_id"] for r in family_refs] == ["JP5980304B2", "JP2014510173A"]
        assert all(r["family_of"] == "US2016175445A1" for r in family_refs)

    def test_opd_dossier_candidates_are_added(self, case_with_hongan, monkeypatch):
        from services import opd_dossier_service

        monkeypatch.setattr(case_service, "_extract_isr_citation_refs_from_hongan_pdf", lambda *_a, **_k: [])
        monkeypatch.setattr(
            opd_dossier_service,
            "extract_citation_candidates",
            lambda case_id: ({
                "candidates": [
                    {
                        "patent_id": "US2016175445A1",
                        "label": "ドシエ引用1",
                        "raw_text": "引用情報 US 2016/175445 A1",
                        "source_label": "引用情報1",
                    }
                ]
            }, 200),
        )

        refs = case_service.extract_hongan_citations(case_with_hongan)[0]["refs"]
        opd_refs = [r for r in refs if r["source"] == "opd_dossier"]

        assert len(opd_refs) == 1
        assert opd_refs[0]["label"] == "ドシエ引用1"
        assert opd_refs[0]["patent_id"] == "US2016175445A1"


class TestErrors:
    def test_missing_case(self, tmp_path, monkeypatch):
        monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
        (tmp_path / "cases").mkdir()
        result, code = case_service.extract_hongan_citations("non-existent")
        assert code == 404

    def test_missing_hongan(self, tmp_path, monkeypatch):
        monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
        (tmp_path / "cases").mkdir()
        case_service.create_minimal_case("2030-no-hongan", title="x", field="cosmetics")
        result, code = case_service.extract_hongan_citations("2030-no-hongan")
        assert code == 404
