#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""services/search_run_service.py の単体テスト"""

import json
import shutil
from pathlib import Path
from unittest import mock

import pytest

from services import search_run_service as srs
from services import case_service as cs


@pytest.fixture
def tmp_cases(tmp_path, monkeypatch):
    """tmp_path/cases を case_service のベースとして使う"""
    cases = tmp_path / "cases"
    cases.mkdir()
    monkeypatch.setattr(cs, "PROJECT_ROOT", tmp_path)
    # get_case_dir は PROJECT_ROOT / "cases" / case_id を返す
    return cases


@pytest.fixture
def case_dir(tmp_cases):
    d = tmp_cases / "TEST-CASE"
    d.mkdir()
    (d / "case.yaml").write_text(
        "case_id: TEST-CASE\npatent_title: テスト発明\n", encoding="utf-8"
    )
    return d


def _make_hit(pid="特開2023-123456", screening="pending", score=None):
    return {
        "patent_id": pid,
        "title": f"タイトル {pid}",
        "applicant": "株式会社テスト",
        "publication_date": "2023-05-01",
        "ipc": ["A61K 8/06"],
        "fi": [],
        "fterm": [],
        "url": "",
        "abstract": None,
        "claim1": None,
        "ai_score": score,
        "ai_reason": None,
        "screening": screening,
        "note": "",
        "downloaded_as_citation": False,
        "row_text": "",
    }


def test_new_run_id_format():
    rid = srs.new_run_id("narrow")
    assert rid.endswith("-narrow")
    assert len(rid) > 15


def test_get_hit_text_resolves_saihyo_to_wo(case_dir):
    hit_dir = case_dir / "search_runs" / "_hit_text"
    hit_dir.mkdir(parents=True)
    (hit_dir / "再表2007_108460.json").write_text(
        json.dumps({
            "patent_id": "再表2007/108460",
            "description": "Ｎ－グアニルシステインを含む。",
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    hit = srs.get_hit_text("TEST-CASE", "WO2007108460")

    assert hit is not None
    assert "グアニルシステイン" in hit["description"]


def test_hit_bookmarks_are_named_and_deduped(case_dir):
    first = srs.save_hit_bookmark("TEST-CASE", "JP2030000001A", "要確認")
    second = srs.save_hit_bookmark("TEST-CASE", "JP2030000001A", "要確認")
    third = srs.save_hit_bookmark("TEST-CASE", "JP2030000002A", "要確認")

    bookmarks = srs.list_hit_bookmarks("TEST-CASE")

    assert first == second
    assert len(bookmarks) == 2
    assert third["patent_id"] == "JP2030000002A"
    assert {b["name"] for b in bookmarks} == {"要確認"}


def test_google_text_language_prefers_english_for_wo():
    assert srs._google_text_language_for("WO2023012009", "ja") == "en"
    assert srs._google_text_language_for("WO2023/012009", "ja") == "en"


def test_google_text_language_keeps_japanese_for_jp():
    assert srs._google_text_language_for("特開2024-123456", "ja") == "ja"
    assert srs._google_text_language_for("JP2024123456A", "ja") == "ja"


def test_thin_google_translation_cache_is_detected():
    assert srs._is_thin_google_translation({
        "source": "google",
        "url": "https://patents.google.com/patent/WO2023012009A1/ja",
        "abstract": "",
        "description": "translated from",
        "claims": ["1. A method"],
    })


def test_good_google_english_cache_is_not_thin():
    assert not srs._is_thin_google_translation({
        "source": "google",
        "url": "https://patents.google.com/patent/WO2023012009A1/en",
        "abstract": "A long enough abstract",
        "description": "x" * 5000,
        "claims": ["1. A method"],
    })


def test_create_and_load_run(case_dir):
    hits = [_make_hit("特開2023-123456"), _make_hit("特開2023-654321")]
    data = srs.create_run_from_hits(
        "TEST-CASE",
        formula="test formula",
        formula_level="narrow",
        source="jplatpat",
        hits=hits,
    )
    assert data["run_id"]
    assert data["hit_count"] == 2
    assert data["status"] == "done"

    reloaded = srs.load_run("TEST-CASE", data["run_id"])
    assert reloaded["formula"] == "test formula"
    assert len(reloaded["hits"]) == 2
    assert reloaded["hits"][0]["patent_id"] == "特開2023-123456"


def test_list_runs(case_dir):
    srs.create_run_from_hits("TEST-CASE", formula="a", formula_level="narrow",
                             hits=[_make_hit("特開2023-1")])
    srs.create_run_from_hits("TEST-CASE", formula="b", formula_level="wide",
                             hits=[_make_hit("特開2023-2"), _make_hit("特開2023-3")])
    runs = srs.list_runs("TEST-CASE")
    assert len(runs) == 2
    assert {r["formula_level"] for r in runs} == {"narrow", "wide"}
    wide_run = [r for r in runs if r["formula_level"] == "wide"][0]
    assert wide_run["hit_count"] == 2


def test_update_screening(case_dir):
    data = srs.create_run_from_hits(
        "TEST-CASE", formula="x", formula_level="narrow",
        hits=[_make_hit("特開2023-100"), _make_hit("特開2023-200")],
    )
    rid = data["run_id"]
    updated = srs.update_screening("TEST-CASE", rid, "特開2023-100", "star", note="主引例")
    assert updated is not None
    hit = [h for h in updated["hits"] if h["patent_id"] == "特開2023-100"][0]
    assert hit["screening"] == "star"
    assert hit["note"] == "主引例"


def test_update_screening_invalid_state(case_dir):
    data = srs.create_run_from_hits(
        "TEST-CASE", formula="x", formula_level="narrow",
        hits=[_make_hit("特開2023-100")],
    )
    with pytest.raises(ValueError):
        srs.update_screening("TEST-CASE", data["run_id"], "特開2023-100", "INVALID")


def test_bulk_update_screening(case_dir):
    data = srs.create_run_from_hits(
        "TEST-CASE", formula="x", formula_level="narrow",
        hits=[_make_hit("特開2023-100"), _make_hit("特開2023-200"), _make_hit("特開2023-300")],
    )
    rid = data["run_id"]
    updates = [
        {"patent_id": "特開2023-100", "screening": "star"},
        {"patent_id": "特開2023-200", "screening": "reject", "note": "関係なし"},
    ]
    updated = srs.bulk_update_screening("TEST-CASE", rid, updates)
    index = {h["patent_id"]: h for h in updated["hits"]}
    assert index["特開2023-100"]["screening"] == "star"
    assert index["特開2023-200"]["screening"] == "reject"
    assert index["特開2023-200"]["note"] == "関係なし"
    assert index["特開2023-300"]["screening"] == "pending"


def test_hold_patents_across_runs_marks_matching_hits(case_dir):
    d1 = srs.create_run_from_hits(
        "TEST-CASE", formula="x", formula_level="narrow",
        hits=[_make_hit("US 2016/175445 A1"), _make_hit("特開2023-100")],
    )
    d2 = srs.create_run_from_hits(
        "TEST-CASE", formula="y", formula_level="wide",
        hits=[_make_hit("US2016175445A1"), _make_hit("特開2023-200")],
    )
    result = srs.hold_patents_across_runs(
        "TEST-CASE", ["US2016175445A1", "WO2020/000001A1"], note="Step6から退避"
    )
    assert result["updated"] == 2
    assert result["not_found"] == ["WO2020/000001A1"]

    run1 = srs.load_run("TEST-CASE", d1["run_id"])
    run2 = srs.load_run("TEST-CASE", d2["run_id"])
    idx1 = {h["patent_id"]: h for h in run1["hits"]}
    idx2 = {h["patent_id"]: h for h in run2["hits"]}
    assert idx1["US 2016/175445 A1"]["screening"] == "hold"
    assert idx1["US 2016/175445 A1"]["note"] == "Step6から退避"
    assert idx1["特開2023-100"]["screening"] == "pending"
    assert idx2["US2016175445A1"]["screening"] == "hold"
    assert idx2["特開2023-200"]["screening"] == "pending"


def test_build_citation_card_hits_reuses_search_run_metadata(case_dir):
    srs.create_run_from_hits(
        "TEST-CASE", formula="x", formula_level="narrow",
        hits=[
            _make_hit("US 2016/175445 A1", screening="triangle", score=72),
            _make_hit("特開2023-100", screening="pending", score=20),
        ],
    )
    hits = srs.build_citation_card_hits(
        "TEST-CASE",
        [
            {"id": "US2016175445A1", "aliases": ["US 2016/175445 A1"]},
            {"id": "WO2020/000001A1", "aliases": []},
        ],
    )
    assert len(hits) == 2
    assert hits[0]["patent_id"] == "US 2016/175445 A1"
    assert hits[0]["screening"] == "triangle"
    assert hits[0]["ai_score"] == 72
    assert hits[0]["_citation_card_status"] == "matched"
    assert hits[0]["_source_run_id"]
    assert hits[1]["patent_id"] == "WO2020/000001A1"
    assert hits[1]["_citation_card_status"] == "fallback"


def test_merge_runs_dedups_by_patent_id(case_dir):
    d1 = srs.create_run_from_hits(
        "TEST-CASE", formula="a", formula_level="narrow",
        hits=[_make_hit("特開2023-111"), _make_hit("特開2023-222", screening="star")],
    )
    d2 = srs.create_run_from_hits(
        "TEST-CASE", formula="b", formula_level="wide",
        hits=[_make_hit("特開2023-222", screening="pending"), _make_hit("特開2023-333")],
    )
    merged = srs.merge_runs("TEST-CASE", [d1["run_id"], d2["run_id"]])
    pids = [h["patent_id"] for h in merged]
    assert len(merged) == 3
    # 重複は優先度高いスクリーニング状態 (star) に統一
    dup = [h for h in merged if h["patent_id"] == "特開2023-222"][0]
    assert dup["screening"] == "star"
    assert len(dup["found_in_runs"]) == 2


def test_get_starred_patent_ids(case_dir):
    d = srs.create_run_from_hits(
        "TEST-CASE", formula="a", formula_level="narrow",
        hits=[
            _make_hit("特開2023-A", screening="star"),
            _make_hit("特開2023-B", screening="triangle"),
            _make_hit("特開2023-C", screening="star"),
        ],
    )
    pids = srs.get_starred_patent_ids("TEST-CASE", [d["run_id"]])
    assert set(pids) == {"特開2023-A", "特開2023-C"}


def test_mark_downloaded_excludes_from_starred(case_dir):
    d = srs.create_run_from_hits(
        "TEST-CASE", formula="a", formula_level="narrow",
        hits=[_make_hit("特開2023-A", screening="star"),
              _make_hit("特開2023-B", screening="star")],
    )
    srs.mark_downloaded("TEST-CASE", d["run_id"], "特開2023-A", True)
    pids = srs.get_starred_patent_ids("TEST-CASE", [d["run_id"]])
    assert pids == ["特開2023-B"]


def test_delete_run(case_dir):
    d = srs.create_run_from_hits(
        "TEST-CASE", formula="a", formula_level="narrow",
        hits=[_make_hit()],
    )
    ok = srs.delete_run("TEST-CASE", d["run_id"])
    assert ok is True
    assert srs.load_run("TEST-CASE", d["run_id"]) is None


def test_get_formulas_from_keyword_dict(case_dir):
    # keyword_dictionary.json を置く
    search_dir = case_dir / "search"
    search_dir.mkdir()
    (search_dir / "keyword_dictionary.json").write_text(json.dumps({
        "search_formulas": {
            "narrow": {"formula_jplatpat": "a*b", "description": "狭"},
            "wide": {"formula_jplatpat": "a+b+c", "description": "広"},
        }
    }, ensure_ascii=False), encoding="utf-8")
    f = srs.get_formulas_from_keyword_dict("TEST-CASE")
    assert "narrow" in f
    assert f["narrow"]["formula_jplatpat"] == "a*b"


def test_get_formulas_missing_returns_empty(case_dir):
    assert srs.get_formulas_from_keyword_dict("TEST-CASE") == {}


def test_scoring_prompt_and_parse():
    hongan = {"title": "化粧料", "claim1": "成分Aと成分Bを含む化粧料"}
    hit = {"patent_id": "特開2023-1", "title": "類似発明",
           "applicant": "株式会社X", "abstract": "成分A、成分B、成分Cを含む",
           "claim1": "成分Aと成分Bと成分Cを含む"}
    prompt = srs._build_scoring_prompt(hongan, hit)
    assert "化粧料" in prompt
    assert "特開2023-1" in prompt

    score, reason = srs._parse_scoring_response('{"score": 85, "reason": "成分A+B が一致"}')
    assert score == 85
    assert "成分A" in reason


def test_scoring_parse_clamps_range():
    score, _ = srs._parse_scoring_response('{"score": 150, "reason": "x"}')
    assert score == 100
    score, _ = srs._parse_scoring_response('{"score": -5, "reason": "x"}')
    assert score == 0


def test_scoring_parse_handles_malformed():
    score, reason = srs._parse_scoring_response("garbage output")
    assert score is None
    assert reason == "garbage output"


# ===== parent_run_id と compute_run_diff のテスト =====

def test_parent_run_id_stored(case_dir):
    parent = srs.create_run_from_hits(
        "TEST-CASE", formula="a*b", formula_level="narrow",
        hits=[_make_hit("特開2023-1")],
    )
    child = srs.create_run_from_hits(
        "TEST-CASE", formula="a*b+c", formula_level="narrow",
        hits=[_make_hit("特開2023-1"), _make_hit("特開2023-2")],
        parent_run_id=parent["run_id"],
    )
    reloaded = srs.load_run("TEST-CASE", child["run_id"])
    assert reloaded["parent_run_id"] == parent["run_id"]
    # list_runs にも parent_run_id が含まれる
    runs = srs.list_runs("TEST-CASE")
    child_item = [r for r in runs if r["run_id"] == child["run_id"]][0]
    assert child_item["parent_run_id"] == parent["run_id"]


def test_parent_run_id_defaults_none(case_dir):
    d = srs.create_run_from_hits(
        "TEST-CASE", formula="a", formula_level="narrow",
        hits=[_make_hit()],
    )
    reloaded = srs.load_run("TEST-CASE", d["run_id"])
    assert reloaded.get("parent_run_id") is None


def test_compute_run_diff_basic(case_dir):
    base = srs.create_run_from_hits(
        "TEST-CASE", formula="a*b", formula_level="narrow",
        hits=[
            _make_hit("特開2023-1"),
            _make_hit("特開2023-2"),
            _make_hit("特開2023-3"),
        ],
    )
    new = srs.create_run_from_hits(
        "TEST-CASE", formula="a*b+c", formula_level="narrow",
        hits=[
            _make_hit("特開2023-2"),  # 共通
            _make_hit("特開2023-3"),  # 共通
            _make_hit("特開2023-4"),  # 新規
            _make_hit("特開2023-5"),  # 新規
        ],
        parent_run_id=base["run_id"],
    )
    diff = srs.compute_run_diff("TEST-CASE", new["run_id"], base["run_id"])
    assert diff is not None
    assert diff["summary"]["common"] == 2
    assert diff["summary"]["added"] == 2
    assert diff["summary"]["removed"] == 1
    new_ids = {h["patent_id"] for h in diff["only_new"]}
    removed_ids = {h["patent_id"] for h in diff["only_base"]}
    assert new_ids == {"特開2023-4", "特開2023-5"}
    assert removed_ids == {"特開2023-1"}


def test_compute_run_diff_missing_run(case_dir):
    d = srs.create_run_from_hits(
        "TEST-CASE", formula="a", formula_level="narrow",
        hits=[_make_hit()],
    )
    assert srs.compute_run_diff("TEST-CASE", d["run_id"], "nonexistent") is None
    assert srs.compute_run_diff("TEST-CASE", "nonexistent", d["run_id"]) is None


def test_compute_run_diff_preserves_screening(case_dir):
    """差分結果に screening と ai_score が含まれる"""
    base = srs.create_run_from_hits(
        "TEST-CASE", formula="a", formula_level="narrow",
        hits=[_make_hit("特開2023-1", screening="star", score=90)],
    )
    new = srs.create_run_from_hits(
        "TEST-CASE", formula="b", formula_level="narrow",
        hits=[_make_hit("特開2023-1", screening="triangle", score=70),
              _make_hit("特開2023-2")],
        parent_run_id=base["run_id"],
    )
    diff = srs.compute_run_diff("TEST-CASE", new["run_id"], base["run_id"])
    common = diff["common"][0]
    assert common["screening"] == "triangle"
    assert common["ai_score"] == 70


# ===== validate_formula のテスト =====

def test_validate_formula_ok():
    r = srs.validate_formula("(A+B)*C")
    assert r["ok"] is True
    assert r["errors"] == []
    assert r["parens_balance"] == 0


def test_validate_formula_unbalanced_open():
    r = srs.validate_formula("((A+B)*C")
    assert r["ok"] is False
    assert any("バランス" in e for e in r["errors"])
    assert r["parens_balance"] == 1


def test_validate_formula_unbalanced_close():
    r = srs.validate_formula("A+B)*C")
    assert r["ok"] is False
    assert any("閉じ括弧" in e or "バランス" in e for e in r["errors"])


def test_validate_formula_warns_zenkaku():
    r = srs.validate_formula("（A＋B）＊C")
    assert r["ok"] is True  # 括弧バランスはOK
    assert len(r["warnings"]) >= 1
    assert any("全角" in w for w in r["warnings"])


def test_validate_formula_consecutive_operators():
    r = srs.validate_formula("A*+B")
    assert r["ok"] is False
    assert any("連続" in e for e in r["errors"])


def test_validate_formula_empty():
    r = srs.validate_formula("")
    assert r["ok"] is True
    assert r["errors"] == []


def test_validate_formula_warns_missing_structural_tag():
    r = srs.validate_formula("(A+B)*C")
    assert r["ok"] is True
    assert any("構造タグ" in w for w in r["warnings"])


def test_validate_formula_no_warn_when_tag_present():
    r = srs.validate_formula("(A+B)/TX*(C+D)/TX")
    assert r["ok"] is True
    assert not any("構造タグ" in w for w in r["warnings"])


def test_validate_formula_composite_tag_error():
    """構造タグの '+' 連結 (/AB+CL 等) は J-PlatPat エラーなのでバリデーションで弾く"""
    r = srs.validate_formula("(A+B)/AB+CL")
    assert r["ok"] is False
    assert any("/AB+CL" in e for e in r["errors"])
    # 構造タグが存在する以上「構造タグが無い」警告は出ない
    assert not any("構造タグ" in w for w in r["warnings"])


def test_validate_formula_brackets_balanced():
    r = srs.validate_formula("[A/TX+B/TX]*[C/CL]")
    assert r["ok"] is True
    assert r["brackets_balance"] == 0


def test_validate_formula_brackets_unbalanced():
    r = srs.validate_formula("[A/TX+B/TX]*C/CL]")
    assert r["ok"] is False
    assert any("大括弧" in e for e in r["errors"])


def test_validate_formula_brackets_quadruple_nest_warn():
    r = srs.validate_formula("[[[[A/TX]]]]")
    assert r["ok"] is True
    assert any("三重" in w or "入れ子" in w for w in r["warnings"])


def test_validate_formula_not_operator_minus_ok():
    # NOT は半角ハイフン '-' が正
    r = srs.validate_formula("(A+B)/TX-(C+D)/TX")
    assert r["ok"] is True
    # NOT に関する警告は出ないこと
    assert not any("NOT" in w for w in r["warnings"])


def test_validate_formula_old_not_operator_warn():
    # 古い誤用: '/' を NOT として使用 → 警告
    r = srs.validate_formula("(A+B) / foo")
    assert any("NOT" in w and "-" in w for w in r["warnings"])


def test_validate_formula_proximity_search_ok():
    # 近傍検索は構造タグ警告が出ないこと
    r = srs.validate_formula("無電源,5C,発光/TX")
    assert r["ok"] is True


def test_validate_formula_warns_hyphen_in_keyword_japanese():
    # 日本語キーワード内の '-' は NOT と解釈されるので警告
    r = srs.validate_formula("(フィルム-電池+積層体)/TX")
    assert any("-" in w and ("ハイフン" in w or "NOT" in w) for w in r["warnings"])


def test_validate_formula_warns_hyphen_in_keyword_ascii():
    r = srs.validate_formula("(SUS-304+A3003)/CL")
    assert any("ハイフン" in w or "全角" in w for w in r["warnings"])


def test_validate_formula_no_warn_hyphen_between_brackets():
    # 正しい NOT ( ) - ( ) 形式
    r = srs.validate_formula("(A+B)/TX-(C+D)/TX")
    assert not any("ハイフン" in w for w in r["warnings"])


def test_validate_formula_warns_full_fterm_code():
    r = srs.validate_formula("4C083AC172/FT")
    assert any("4C083/FC*AC17.2/FT" in w for w in r["warnings"])


def test_validate_formula_warns_cosmetics_base_fterm_with_trailing_dot_hint():
    r = srs.validate_formula("4C083AD05/FT")
    assert any("4C083/FC*AD05./FT" in w for w in r["warnings"])


def test_validate_formula_warns_laminate_full_fterm_layer_suffix():
    r = srs.validate_formula("4F100AK01B/FT")
    assert any("4F100/FC*AK01B/FT" in w for w in r["warnings"])


# ===== get_keyword_snippets のテスト =====

def test_get_keyword_snippets_empty(case_dir):
    snip = srs.get_keyword_snippets("TEST-CASE")
    assert snip == {"groups": [], "ipc_codes": [], "fi_codes": [], "fterm_codes": [], "theme_codes": []}


def test_get_keyword_snippets_theme_codes_extracted(case_dir):
    """F-term コードからテーマコード (先頭5文字) を一意に抽出"""
    search_dir = case_dir / "search"
    search_dir.mkdir()
    (search_dir / "keyword_dictionary.json").write_text(json.dumps({
        "fterm_codes": ["4C083AB13", "4C083AC01", "4F100AA01", "4F100BB02"],
    }, ensure_ascii=False), encoding="utf-8")
    snip = srs.get_keyword_snippets("TEST-CASE")
    assert snip["theme_codes"] == ["4C083", "4F100"]


def test_get_keyword_snippets_theme_codes_extracted_with_additional_code(case_dir):
    search_dir = case_dir / "search"
    search_dir.mkdir()
    (search_dir / "keyword_dictionary.json").write_text(json.dumps({
        "fterm_codes": ["4C083AC172"],
    }, ensure_ascii=False), encoding="utf-8")
    snip = srs.get_keyword_snippets("TEST-CASE")
    assert snip["theme_codes"] == ["4C083"]


def test_get_keyword_snippets_merges_classification_json(case_dir):
    """classification.json に格納された fterm (構造化) もテーマコード抽出対象"""
    search_dir = case_dir / "search"
    search_dir.mkdir()
    (search_dir / "keyword_dictionary.json").write_text(json.dumps({
        "keyword_groups": [],
    }, ensure_ascii=False), encoding="utf-8")
    (search_dir / "classification.json").write_text(json.dumps({
        "ipc": [{"code": "A61K 8/00"}],
        "fi": [{"code": "A61K 8/02"}, {"code": "A61K 8/06"}],
        "fterm": [{"code": "4C083AB13"}, {"code": "4C083AC01"}, {"code": "5H050CA99"}],
    }, ensure_ascii=False), encoding="utf-8")
    snip = srs.get_keyword_snippets("TEST-CASE")
    assert "A61K 8/00" in snip["ipc_codes"]
    assert "A61K 8/02" in snip["fi_codes"]
    assert "4C083AB13" in snip["fterm_codes"]
    assert sorted(snip["theme_codes"]) == ["4C083", "5H050"]


def test_get_keyword_snippets_groups(case_dir):
    search_dir = case_dir / "search"
    search_dir.mkdir()
    (search_dir / "keyword_dictionary.json").write_text(json.dumps({
        "keyword_groups": [
            {
                "label": "化粧料",
                "terms": ["化粧料", "メイクアップ", "ファンデーション"],
                "classifications": {
                    "ipc": ["A61K 8/00"],
                    "fi": ["A61K 8/06"],
                    "fterm": ["4C083AD05"],
                },
            },
            {"label": "シリコーン", "synonyms": ["シリコーン", "silicone"]},
        ],
        "fi_codes": ["A61K 8/06", "A61Q 1/12"],
        "fterm_codes": ["4C083AA"],
    }, ensure_ascii=False), encoding="utf-8")
    snip = srs.get_keyword_snippets("TEST-CASE")
    assert len(snip["groups"]) == 2
    g0 = snip["groups"][0]
    assert g0["label"] == "化粧料"
    assert "化粧料" in g0["terms"]
    assert g0["jplatpat_group"] == "(化粧料+メイクアップ+ファンデーション)/TX"
    assert g0["jplatpat_group_raw"] == "(化粧料+メイクアップ+ファンデーション)"
    assert g0["terms_sanitized"] == ["化粧料", "メイクアップ", "ファンデーション"]
    assert g0["ipc_codes"] == ["A61K 8/00"]
    assert g0["fi_codes"] == ["A61K 8/06"]
    assert g0["fterm_codes"] == ["4C083AD05"]
    assert "A61K 8/00" in snip["ipc_codes"]


def test_get_keyword_snippets_sanitizes_hyphen(case_dir):
    search_dir = case_dir / "search"
    search_dir.mkdir()
    (search_dir / "keyword_dictionary.json").write_text(json.dumps({
        "keyword_groups": [
            {"label": "合金", "terms": ["SUS-304", "フィルム-電池"]},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    snip = srs.get_keyword_snippets("TEST-CASE")
    g = snip["groups"][0]
    # 表示は元のまま、挿入用は全角ハイフン化
    assert g["terms"] == ["SUS-304", "フィルム-電池"]
    assert g["terms_sanitized"] == ["SUS－304", "フィルム－電池"]
    assert g["jplatpat_group"] == "(SUS－304+フィルム－電池)/TX"


def test_get_keyword_snippets_fallback_keywords_preserves_group_codes(case_dir):
    (case_dir / "keywords.json").write_text(json.dumps([
        {
            "group_id": 1,
            "label": "成分A",
            "keywords": [{"term": "グアニルシステイン"}],
            "search_codes": {
                "ipc": [{"code": "A61K 8/00"}],
                "fi": [{"code": "A61K 8/898"}],
                "fterm": [{"code": "4C083AC172"}],
            },
        }
    ], ensure_ascii=False), encoding="utf-8")

    snip = srs.get_keyword_snippets("TEST-CASE")

    g = snip["groups"][0]
    assert g["label"] == "成分A"
    assert g["ipc_codes"] == ["A61K 8/00"]
    assert g["fi_codes"] == ["A61K 8/898"]
    assert g["fterm_codes"] == ["4C083AC172"]
    assert snip["ipc_codes"] == ["A61K 8/00"]
    assert snip["theme_codes"] == ["4C083"]


def test_merge_runs_preserves_found_in_runs_order(case_dir):
    """既存のマージロジックに対する影響がないこと確認"""
    d1 = srs.create_run_from_hits(
        "TEST-CASE", formula="a", formula_level="narrow",
        hits=[_make_hit("特開2023-X")],
    )
    d2 = srs.create_run_from_hits(
        "TEST-CASE", formula="b", formula_level="wide",
        hits=[_make_hit("特開2023-X")],
    )
    merged = srs.merge_runs("TEST-CASE", [d1["run_id"], d2["run_id"]])
    assert len(merged) == 1
    assert merged[0]["found_in_runs"] == [d1["run_id"], d2["run_id"]]


def test_ai_score_run_stream_yields_each_scored_hit(case_dir, monkeypatch):
    run = srs.create_run_from_hits(
        "TEST-CASE",
        formula="a",
        formula_level="narrow",
        hits=[_make_hit("JP1"), _make_hit("JP2")],
    )

    calls = []

    def fake_call(prompt, **kwargs):
        calls.append(prompt)
        return '{"score": 80, "reason": "近い"}'

    monkeypatch.setattr("modules.claude_client.call_claude", fake_call)

    events = list(srs.ai_score_run_stream("TEST-CASE", run["run_id"], model="sonnet"))

    assert events[0] == {"type": "start", "total": 2, "run_id": run["run_id"]}
    score_events = [e for e in events if e["type"] == "score"]
    assert [e["patent_id"] for e in score_events] == ["JP1", "JP2"]
    assert all(e["hit"]["ai_score"] == 80 for e in score_events)
    assert events[-1]["type"] == "done"
    assert events[-1]["scored"] == 2
    saved = srs.load_run("TEST-CASE", run["run_id"])
    assert [h["ai_score"] for h in saved["hits"]] == [80, 80]
    assert len(calls) == 2
