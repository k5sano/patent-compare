#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""modules/jplatpat_client.py の単体テスト

Playwright に依存しない部分 (データ正規化、行パース) のみ検証。
"""

import pytest
from unittest import mock

from modules.jplatpat_client import JplatpatHit, _parse_row


class _FakeLocator:
    def __init__(self, count=0, href=""):
        self._count = count
        self._href = href

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def get_attribute(self, _):
        return self._href


class _FakeRow:
    def __init__(self, text, anchor_href=""):
        self._text = text
        self._anchor_href = anchor_href

    def inner_text(self, timeout=2000):
        return self._text

    def locator(self, selector):
        if selector == "a":
            return _FakeLocator(count=1 if self._anchor_href else 0,
                                href=self._anchor_href)
        return _FakeLocator()


def test_dedup_key_jp():
    h = JplatpatHit(patent_id="特開2023-123456")
    assert h.dedup_key == "JP2023123456"


def test_dedup_key_us():
    h = JplatpatHit(patent_id="US20070292359A1")
    # 全数字を連結 (国コード+数字)。A1 の "1" も含まれる
    assert h.dedup_key == "US200702923591"


def test_dedup_key_wo():
    h = JplatpatHit(patent_id="WO2022/030405")
    assert h.dedup_key == "WO2022030405"


def test_dedup_key_empty():
    h = JplatpatHit(patent_id="")
    assert h.dedup_key == ""


def test_parse_row_jp_basic():
    text = "特開2023-123456\n2023/05/01\n株式会社テストコーポレーション\nテスト発明\nA61K 8/06"
    row = _FakeRow(text)
    hit = _parse_row(row)
    assert hit.patent_id == "特開2023-123456"
    assert hit.publication_date == "2023-05-01"
    assert "株式会社" in hit.applicant
    # タイトルとして 6-120 文字かつ文献番号でも日付でもない最も長い行が選ばれる
    assert hit.title in ("株式会社テストコーポレーション", "テスト発明")
    assert "A61K 8/06" in hit.ipc


def test_parse_row_with_url():
    text = "特開2024-999\n2024/01/15\n株式会社X\n発明タイトル"
    row = _FakeRow(text, anchor_href="/c1801/PU/JP-2024-000999/11/ja")
    hit = _parse_row(row)
    assert hit.patent_id == "特開2024-999"
    assert hit.url.startswith("https://www.j-platpat.inpit.go.jp")


def test_parse_row_fallback_empty():
    row = _FakeRow("")
    hit = _parse_row(row)
    assert hit.patent_id == ""
    assert hit.title == ""


def test_parse_row_patent_number():
    text = "特許6789012\n2020/08/20\n大学法人Y\n登録特許発明"
    row = _FakeRow(text)
    hit = _parse_row(row)
    assert hit.patent_id.startswith("特許")
    assert "大学" in hit.applicant


def test_run_jplatpat_search_without_playwright(monkeypatch):
    """playwright 未インストール時は空リストを返す。"""
    import modules.jplatpat_client as mod
    # sys.modules を汚染せず ImportError を誘発
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("playwright"):
            raise ImportError("fake")
        return real_import(name, *args, **kwargs)

    with mock.patch("builtins.__import__", side_effect=fake_import):
        hits = mod.run_jplatpat_search("test", max_results=5)
    assert hits == []


def test_to_dict():
    h = JplatpatHit(patent_id="特開2023-X", title="T", ipc=["A01B"])
    d = h.to_dict()
    assert d["patent_id"] == "特開2023-X"
    assert d["ipc"] == ["A01B"]
    assert "screening" not in d  # dataclass にないフィールドは含まない
