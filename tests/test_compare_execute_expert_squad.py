#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json

import pytest

from services import case_service as cs
from services import comparison_service as cmp


@pytest.fixture
def expert_case(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "PROJECT_ROOT", tmp_path)
    case_dir = tmp_path / "cases" / "CASE1"
    (case_dir / "citations").mkdir(parents=True)
    (case_dir / "responses").mkdir()
    case_dir.joinpath("case.yaml").write_text(
        "case_id: CASE1\n"
        "field: cosmetics\n"
        "citations:\n"
        "  - id: JPTEST\n"
        "    role: 主引例\n"
        "    label: JPTEST\n",
        encoding="utf-8",
    )
    case_dir.joinpath("segments.json").write_text(json.dumps([
        {
            "claim_number": 1,
            "is_independent": True,
            "segments": [
                {"id": "1A", "text": "成分Aを含むこと"},
                {"id": "1B", "text": "成分Bを含むこと"},
                {"id": "1C", "text": "成分Cを含むこと"},
            ],
        }
    ], ensure_ascii=False), encoding="utf-8")
    case_dir.joinpath("citations", "JPTEST.json").write_text(json.dumps({
        "patent_number": "JPTEST",
        "paragraphs": [
            {"id": "0010", "text": "成分Aを含む。"},
            {"id": "0020", "text": "成分Bを含む。"},
            {"id": "0030", "text": "成分Cを含む。"},
        ],
        "claims": [],
        "tables": [],
    }, ensure_ascii=False), encoding="utf-8")
    return case_dir


def _judge_response():
    return {
        "document_id": "JPTEST",
        "comparisons": [
            {
                "requirement_id": rid,
                "judgment": "○",
                "cited_location": loc,
                "judgment_reason": f"{rid}が記載される",
            }
            for rid, loc in (("1A", "0010"), ("1B", "0020"), ("1C", "0030"))
        ],
        "overall_summary": "構成は近い",
        "category_suggestion": "X",
    }


def test_compare_execute_expert_squad_saves_response(
    expert_case, monkeypatch
):
    monkeypatch.setenv("COMPARE_MODE", "expert_squad")
    calls = {"extractor": 0, "judge": 0}

    def fake_call(prompt, **kwargs):
        if "該当箇所を抽出する専門家" in prompt:
            calls["extractor"] += 1
            rid = "1A"
            para = "0010"
            if "requirement_id: 1B" in prompt:
                rid, para = "1B", "0020"
            elif "requirement_id: 1C" in prompt:
                rid, para = "1C", "0030"
            return json.dumps({
                "requirement_id": rid,
                "citation_id": "JPTEST",
                "evidence_paragraphs": [
                    {"paragraph_id": para, "snippet": f"{rid} snippet", "score": 0.9, "reason": "一致"}
                ],
                "evidence_tables": [],
                "no_match_reason": None,
            }, ensure_ascii=False)
        calls["judge"] += 1
        assert "重点参酌" in prompt
        return json.dumps(_judge_response(), ensure_ascii=False)

    monkeypatch.setattr("modules.claude_client.call_claude", fake_call)

    result, status = cmp.compare_execute("CASE1", ["JPTEST"], model="sonnet")

    assert status == 200
    assert result["success"] is True
    assert result["saved_docs"] == ["JPTEST"]
    assert calls == {"extractor": 3, "judge": 1}
    assert result["expert_squad_meta"]["extractor"]["total"] == 3
    saved = json.loads(expert_case.joinpath("responses", "JPTEST.json").read_text(encoding="utf-8"))
    assert saved["document_id"] == "JPTEST"
    assert len(saved["comparisons"]) == 3


def test_compare_execute_expert_squad_continues_when_extractor_fails(
    expert_case, monkeypatch
):
    monkeypatch.setenv("COMPARE_MODE", "expert_squad")
    calls = {"extractor": 0, "judge": 0}

    def fake_call(prompt, **kwargs):
        if "該当箇所を抽出する専門家" in prompt:
            calls["extractor"] += 1
            if calls["extractor"] == 1:
                raise RuntimeError("extractor down")
            return json.dumps({
                "requirement_id": "1B" if "requirement_id: 1B" in prompt else "1C",
                "citation_id": "JPTEST",
                "evidence_paragraphs": [],
                "evidence_tables": [],
                "no_match_reason": "なし",
            }, ensure_ascii=False)
        calls["judge"] += 1
        return json.dumps(_judge_response(), ensure_ascii=False)

    monkeypatch.setattr("modules.claude_client.call_claude", fake_call)

    result, status = cmp.compare_execute("CASE1", ["JPTEST"], model="sonnet")

    assert status == 200
    assert result["success"] is True
    assert calls["judge"] == 1
    assert result["expert_squad_meta"]["extractor"]["errors"]


def test_compare_execute_default_path_unchanged(expert_case, monkeypatch):
    monkeypatch.delenv("COMPARE_MODE", raising=False)
    calls = []

    def fake_call(prompt, **kwargs):
        calls.append(prompt)
        assert "該当箇所を抽出する専門家" not in prompt
        return json.dumps(_judge_response(), ensure_ascii=False)

    monkeypatch.setattr("modules.claude_client.call_claude", fake_call)

    result, status = cmp.compare_execute("CASE1", ["JPTEST"], model="sonnet")

    assert status == 200
    assert result["success"] is True
    assert "expert_squad_meta" not in result
    assert len(calls) == 1
