#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json

from modules.llm_experts import run_expert
from services import case_service as cs


def _citation():
    return {
        "paragraphs": [
            {"id": "0010", "text": "成分Aを含む組成物。"},
            {"id": "0020", "text": "成分Bをさらに含む。"},
        ],
        "tables": [],
    }


def _segments():
    return [{
        "claim_number": 1,
        "is_independent": True,
        "segments": [
            {"id": "1A", "text": "成分Aを含むこと"},
            {"id": "1B", "text": "成分Bを含むこと"},
        ],
    }]


def _judge_response():
    return {
        "document_id": "JPTEST",
        "comparisons": [
            {
                "requirement_id": "1A",
                "judgment": "○",
                "cited_location": "0010",
                "judgment_reason": "成分Aが記載される",
            },
            {
                "requirement_id": "1B",
                "judgment": "○",
                "cited_location": "0020",
                "judgment_reason": "成分Bが記載される",
            },
        ],
        "overall_summary": "近い",
        "category_suggestion": "X",
    }


def test_evidence_extractor_prompt_and_result(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "PROJECT_ROOT", tmp_path)
    seen = {}

    def fake_call(prompt, **kwargs):
        seen["prompt"] = prompt
        seen["kwargs"] = kwargs
        return json.dumps({
            "requirement_id": "1A",
            "citation_id": "JPTEST",
            "evidence_paragraphs": [
                {"paragraph_id": "0010", "snippet": "成分Aを含む", "score": 0.9, "reason": "一致"}
            ],
            "evidence_tables": [],
            "no_match_reason": None,
        }, ensure_ascii=False)

    monkeypatch.setattr("modules.claude_client.call_claude", fake_call)

    result = run_expert(
        "evidence_extractor",
        inputs={
            "requirement_id": "1A",
            "requirement_text": "成分Aを含むこと",
            "keywords": ["成分A"],
            "citation_id": "JPTEST",
            "citation": _citation(),
        },
        case_id="CASE1",
    )

    assert result.success is True
    assert "判定はしない" in seen["prompt"]
    assert "成分A" in seen["prompt"]
    assert result.parsed["evidence_paragraphs"][0]["paragraph_id"] == "0010"
    assert seen["kwargs"]["model"] == "haiku"


def test_evidence_validator_detects_bad_paragraph_and_score(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "PROJECT_ROOT", tmp_path)

    def fake_call(prompt, **kwargs):
        return json.dumps({
            "requirement_id": "1A",
            "citation_id": "JPTEST",
            "evidence_paragraphs": [
                {"paragraph_id": "9999", "snippet": "x", "score": 1.5, "reason": "bad"}
            ],
            "evidence_tables": [],
            "no_match_reason": None,
        }, ensure_ascii=False)

    monkeypatch.setattr("modules.claude_client.call_claude", fake_call)

    result = run_expert(
        "evidence_extractor",
        inputs={
            "requirement_id": "1A",
            "requirement_text": "成分Aを含むこと",
            "citation_id": "JPTEST",
            "citation": _citation(),
        },
        case_id="CASE1",
        model_override="sonnet",
    )

    assert result.success is False
    assert any("paragraph_id" in e for e in result.errors)
    assert any("0〜1" in e for e in result.errors)
    assert result.model_used == "sonnet"


def test_claim_chart_judge_uses_existing_parser(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "PROJECT_ROOT", tmp_path)

    def fake_call(prompt, **kwargs):
        assert "重点参酌" in prompt
        assert "段落【0010】" in prompt
        return json.dumps(_judge_response(), ensure_ascii=False)

    monkeypatch.setattr("modules.claude_client.call_claude", fake_call)

    result = run_expert(
        "claim_chart_judge",
        inputs={
            "segments": _segments(),
            "citations": [{"patent_number": "JPTEST", **_citation()}],
            "keywords": [],
            "field": "cosmetics",
            "hongan": None,
            "evidence_by_req_cit": {
                "1A||JPTEST": {
                    "evidence_paragraphs": [
                        {"paragraph_id": "0010", "snippet": "成分Aを含む", "score": 0.9, "reason": "一致"}
                    ],
                    "evidence_tables": [],
                }
            },
            "all_segment_ids": ["1A", "1B"],
        },
        case_id="CASE1",
        model_override="opus",
    )

    assert result.success is True
    assert result.parsed["document_id"] == "JPTEST"
    assert result.model_used == "opus"


def test_run_expert_falls_back_after_call_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "PROJECT_ROOT", tmp_path)
    calls = []

    def fake_call(prompt, **kwargs):
        calls.append(kwargs["model"])
        if len(calls) == 1:
            raise RuntimeError("first failed")
        return json.dumps({
            "requirement_id": "1A",
            "citation_id": "JPTEST",
            "evidence_paragraphs": [
                {"paragraph_id": "0010", "snippet": "成分A", "score": 0.8, "reason": "一致"}
            ],
            "evidence_tables": [],
            "no_match_reason": None,
        }, ensure_ascii=False)

    monkeypatch.setattr("modules.claude_client.call_claude", fake_call)

    result = run_expert(
        "evidence_extractor",
        inputs={
            "requirement_id": "1A",
            "requirement_text": "成分Aを含むこと",
            "citation_id": "JPTEST",
            "citation": _citation(),
        },
        case_id="CASE1",
    )

    assert result.success is True
    assert calls == ["haiku", "glm-haiku"]
    assert result.model_used == "glm-haiku"


def test_run_expert_reports_cache_hit(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "PROJECT_ROOT", tmp_path)
    calls = []

    def fake_call(prompt, **kwargs):
        calls.append(kwargs["model"])
        return json.dumps({
            "requirement_id": "1A",
            "citation_id": "JPTEST",
            "evidence_paragraphs": [
                {"paragraph_id": "0010", "snippet": "成分A", "score": 0.8, "reason": "一致"}
            ],
            "evidence_tables": [],
            "no_match_reason": None,
        }, ensure_ascii=False)

    monkeypatch.setattr("modules.claude_client.call_claude", fake_call)
    inputs = {
        "requirement_id": "1A",
        "requirement_text": "成分Aを含むこと",
        "citation_id": "JPTEST",
        "citation": _citation(),
    }

    r1 = run_expert("evidence_extractor", inputs=inputs, case_id="CASE1")
    r2 = run_expert("evidence_extractor", inputs=inputs, case_id="CASE1")

    assert r1.cache_hit is False
    assert r2.cache_hit is True
    assert len(calls) == 1
