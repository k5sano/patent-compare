#!/usr/bin/env python
# -*- coding: utf-8 -*-

from modules.llm_cache import cached_call_claude
from services import case_service as cs


def test_cached_call_miss_then_hit(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "PROJECT_ROOT", tmp_path)

    def fake_call(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return "response-1"

    monkeypatch.setattr("modules.claude_client.call_claude", fake_call)

    r1, m1 = cached_call_claude("prompt", model="sonnet", effort="low", case_id="C1")
    r2, m2 = cached_call_claude("prompt", model="sonnet", effort="low", case_id="C1")

    assert r1 == r2 == "response-1"
    assert m1["cache_hit"] is False
    assert m2["cache_hit"] is True
    assert len(calls) == 1


def test_cached_call_model_and_template_version_make_distinct_keys(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "PROJECT_ROOT", tmp_path)

    def fake_call(prompt, **kwargs):
        calls.append(kwargs)
        return f"response-{len(calls)}"

    monkeypatch.setattr("modules.claude_client.call_claude", fake_call)

    r1, _ = cached_call_claude("prompt", model="sonnet", effort="low", case_id="C1")
    r2, _ = cached_call_claude("prompt", model="haiku", effort="low", case_id="C1")
    r3, _ = cached_call_claude(
        "prompt", model="sonnet", effort="low", case_id="C1",
        template_version="v2",
    )

    assert [r1, r2, r3] == ["response-1", "response-2", "response-3"]
    assert len(calls) == 3


def test_cached_call_scope_none_disables_cache(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "PROJECT_ROOT", tmp_path)

    def fake_call(prompt, **kwargs):
        calls.append(kwargs)
        return f"response-{len(calls)}"

    monkeypatch.setattr("modules.claude_client.call_claude", fake_call)

    r1, m1 = cached_call_claude("prompt", cache_scope="none", case_id="C1")
    r2, m2 = cached_call_claude("prompt", cache_scope="none", case_id="C1")

    assert [r1, r2] == ["response-1", "response-2"]
    assert m1["cache_scope"] == "none"
    assert m2["cache_hit"] is False
    assert len(calls) == 2


def test_cached_call_ignores_corrupt_cache_file(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "PROJECT_ROOT", tmp_path)

    def fake_call(prompt, **kwargs):
        calls.append(kwargs)
        return "fresh"

    monkeypatch.setattr("modules.claude_client.call_claude", fake_call)
    _r1, m1 = cached_call_claude("prompt", model="sonnet", case_id="C1")
    path = m1["cache_path"]
    with open(path, "w", encoding="utf-8") as f:
        f.write("{broken")

    r2, m2 = cached_call_claude("prompt", model="sonnet", case_id="C1")

    assert r2 == "fresh"
    assert m2["cache_hit"] is False
    assert len(calls) == 2
