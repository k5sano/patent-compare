"""Step 1 (本願) パネルに hongan.tables (PDF テキストパーサ由来) が表示されることを
回帰防止する smoke test。

意図: 「抽出は出来ているのに UI に出ない」問題を防ぐため、サマリ行に件数が出ること、
表示用モーダルが HTML に含まれることを Flask test client で確認する。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def case_with_hongan_tables(tmp_path, monkeypatch):
    """tables 入りの hongan.json を持つ案件を作って、web.py の PROJECT_ROOT を差し替える"""
    import services.case_service as case_service
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)

    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    case_id = "2030-honganview"
    case_service.create_minimal_case(case_id, title="本願表テスト", field="cosmetics")
    case_dir = cases_dir / case_id

    # tables 3 件入りの hongan.json
    hongan = {
        "file_name": "hongan",
        "file_type": "application",
        "patent_number": "JP2030-99999",
        "patent_title": "テスト発明",
        "format": "JP",
        "total_pages": 10,
        "claims": [{"number": 1, "text": "請求項1"}],
        "paragraphs": [{"id": "0001", "text": "本文", "section": "発明の詳細な説明"}],
        "tables": [
            {"id": "表1", "page": 3, "section": "実施例",
             "paragraph_id": "0035", "content": "成分A | 成分B\n10 | 20"},
            {"id": "表2", "page": 4, "section": "実施例",
             "paragraph_id": "0040", "content": "比較例の配合データ"},
            {"id": "表3", "page": 5, "section": "比較例",
             "paragraph_id": "0050", "content": "別表"},
        ],
    }
    with open(case_dir / "hongan.json", "w", encoding="utf-8") as f:
        json.dump(hongan, f, ensure_ascii=False)

    # web.py の PROJECT_ROOT も差し替え (load_json_file が読む先)
    import web
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    web.app.config["TESTING"] = True
    return case_id, web.app


def test_summary_includes_table_count(case_with_hongan_tables):
    case_id, app = case_with_hongan_tables
    client = app.test_client()
    resp = client.get(f"/case/{case_id}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # サマリ行に「表: 3件」が出る
    assert "表: 3件" in body, f"件数表示が見当たらない / body={body[:1000]}"


def test_modal_includes_each_table_content(case_with_hongan_tables):
    case_id, app = case_with_hongan_tables
    client = app.test_client()
    resp = client.get(f"/case/{case_id}")
    body = resp.get_data(as_text=True)
    # モーダル内に各表の content が埋め込まれる (Jinja で server-side render)
    assert "表1" in body
    assert "成分A" in body
    assert "比較例の配合データ" in body
    assert "別表" in body
    # モーダル開閉ボタンが存在
    assert 'onclick="showHonganPdfTables()"' in body
    assert 'id="hongan-pdf-tables-modal"' in body


def test_button_only_when_tables_nonempty(tmp_path, monkeypatch):
    """tables=[] の案件ではボタン/モーダルが出ない (Jinja の {% if %} で抑制)"""
    import services.case_service as case_service
    import web
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    (tmp_path / "cases").mkdir()
    case_id = "2030-no-tables"
    case_service.create_minimal_case(case_id, title="x", field="cosmetics")

    case_dir = tmp_path / "cases" / case_id
    hongan = {
        "patent_number": "X", "patent_title": "Y", "total_pages": 1,
        "claims": [], "paragraphs": [], "tables": [],
    }
    with open(case_dir / "hongan.json", "w", encoding="utf-8") as f:
        json.dump(hongan, f, ensure_ascii=False)

    web.app.config["TESTING"] = True
    client = web.app.test_client()
    resp = client.get(f"/case/{case_id}")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    # サマリ行は出る (表: 0件)
    assert "表: 0件" in body
    # モーダル/ボタンは出ない
    assert 'onclick="showHonganPdfTables()"' not in body
    assert 'id="hongan-pdf-tables-modal"' not in body


def test_legacy_hongan_without_tables_key(tmp_path, monkeypatch):
    """tables キー自体が無い古い案件でも 500 にならない (|length のフォールバック)"""
    import services.case_service as case_service
    import web
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    (tmp_path / "cases").mkdir()
    case_id = "2030-legacy"
    case_service.create_minimal_case(case_id, title="x", field="cosmetics")

    case_dir = tmp_path / "cases" / case_id
    hongan = {
        # tables キーなし (古い hongan.json)
        "patent_number": "X", "patent_title": "Y", "total_pages": 1,
        "claims": [], "paragraphs": [],
    }
    with open(case_dir / "hongan.json", "w", encoding="utf-8") as f:
        json.dump(hongan, f, ensure_ascii=False)

    web.app.config["TESTING"] = True
    client = web.app.test_client()
    resp = client.get(f"/case/{case_id}")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:500]
    body = resp.get_data(as_text=True)
    assert "表: 0件" in body
