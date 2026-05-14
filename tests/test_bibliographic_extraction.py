"""書誌事項抽出 (modules.pdf_extractor.detect_bibliographic_info) のテスト。

JPO 標準フォーマット:
    (21)出願番号　特願2022-141234
    (22)出願日　令和4年9月5日(2022.9.5)
    (43)公開日　令和6年3月19日(2024.3.19)
    (71)出願人　株式会社○○
    (72)発明者　山田　太郎
"""
from __future__ import annotations

import pytest

from modules.pdf_extractor import (
    detect_bibliographic_info,
    parse_claims_jp_bracket,
    _resolve_ocr_max_workers,
)


def _pages(text):
    return [{"page": 1, "text": text}]


class TestApplicationNumber:
    def test_basic(self):
        text = "(21)出願番号　特願2022-142104(P2022-142104)"
        info = detect_bibliographic_info(_pages(text))
        assert info.get("application_number") == "特願2022-142104"

    def test_with_newline_between_label_and_value(self):
        text = "(21)出願番号　\n特願2022-141234"
        info = detect_bibliographic_info(_pages(text))
        assert info.get("application_number") == "特願2022-141234"


class TestApplicationDate:
    def test_seireki_in_parens(self):
        text = "(22)出願日　\n令和4年9月7日(2022.9.7)"
        info = detect_bibliographic_info(_pages(text))
        assert info.get("application_date") == "2022-9-7"

    def test_only_wareki(self):
        """西暦が無く和暦のみの場合は和暦文字列をそのまま"""
        text = "(22)出願日　令和4年9月7日"
        info = detect_bibliographic_info(_pages(text))
        d = info.get("application_date", "")
        assert "令和4年9月7日" in d


class TestPublicationDate:
    def test_basic(self):
        text = "(43)公開日　令和6年3月19日(2024.3.19)"
        info = detect_bibliographic_info(_pages(text))
        assert info.get("publication_date") == "2024-3-19"

    def test_kohyo_pct(self):
        """特表 (PCT 翻訳公報) の公表日"""
        text = "(43)公表日　令和6年9月12日(2024.9.12)"
        info = detect_bibliographic_info(_pages(text))
        assert info.get("publication_date") == "2024-9-12"


class TestApplicant:
    def test_japanese_company(self):
        text = (
            "(71)出願人　\n"
            "591183935\n"
            "中央エアゾール化学株式会社\n"
            "埼玉県幸手市大字上吉羽２１００番地"
        )
        info = detect_bibliographic_info(_pages(text))
        assert info.get("applicant") == "中央エアゾール化学株式会社"

    def test_foreign_corp(self):
        text = (
            "(71)出願人　\n"
            "511234567\n"
            "BASF SE\n"
            "ドイツ連邦共和国"
        )
        info = detect_bibliographic_info(_pages(text))
        # BASF SE のような海外社名は会社接尾辞で拾えないが、識別番号直後の行を取る
        assert info.get("applicant") == "BASF SE"


class TestInventors:
    def test_multiple_inventors(self):
        text = (
            "(72)発明者　\n板橋  采女\n埼玉県\n"
            "(72)発明者　\n飯田  将一\n埼玉県\n"
            "(72)発明者　\n野村  祥吾\n埼玉県"
        )
        info = detect_bibliographic_info(_pages(text))
        assert info.get("inventors") == ["板橋  采女", "飯田  将一", "野村  祥吾"]

    def test_no_address_lines_picked_up(self):
        """発明者の住所行 (郵便番号など) が混じらない"""
        text = "(72)発明者　\n山田　太郎"
        info = detect_bibliographic_info(_pages(text))
        assert info.get("inventors") == ["山田　太郎"]


class TestEmpty:
    def test_no_pages(self):
        assert detect_bibliographic_info([]) == {}

    def test_no_bibliographic_markers(self):
        text = "本発明は、化粧料に関する。【背景技術】"
        assert detect_bibliographic_info(_pages(text)) == {}


def test_parse_japanese_wo_bracket_claims_from_ocr_text():
    text = """
    [請求 項 1] ポリ オレ フィン 系 樹脂 フィルム。
    [請求 項 2] 請求 項 1 に 記載 のポリオレフィン系樹脂フィルム。
    INTERNATIONAL SEARCH REPORT
    """

    claims = parse_claims_jp_bracket(text)

    assert [c["number"] for c in claims] == [1, 2]
    assert claims[0]["is_independent"] is True
    assert claims[1]["dependencies"] == [1]
    assert "INTERNATIONAL SEARCH REPORT" not in claims[1]["text"]


def test_resolve_ocr_max_workers_respects_env_cap(monkeypatch):
    monkeypatch.setenv("PATENT_COMPARE_OCR_MAX_WORKERS", "8")
    assert _resolve_ocr_max_workers(20, None) == 8
    assert _resolve_ocr_max_workers(20, 12) == 8
    assert _resolve_ocr_max_workers(3, None) == 3

    monkeypatch.setenv("PATENT_COMPARE_OCR_MAX_WORKERS", "bad")
    monkeypatch.setenv("OCR_MAX_WORKERS", "6")
    assert _resolve_ocr_max_workers(20, None) == 6
