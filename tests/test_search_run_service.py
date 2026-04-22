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
