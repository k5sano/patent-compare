"""LLM provider routing tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules import claude_client as cc


class _Resp:
    def __init__(self, payload, status_code=200, text=None):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        return self._payload


def test_model_aliases_resolve_to_expected_providers():
    assert cc.resolve_model("opus") == "claude-opus-4-6"
    assert cc.model_provider("opus") == "claude"
    assert cc.resolve_model("codex-sonnet") == "gpt-5.4"
    assert cc.model_provider("codex-sonnet") == "codex"
    assert cc.model_provider("openai-sonnet") == "codex"
    assert cc.resolve_model("glm-opus") == "glm-5.1"
    assert cc.model_provider("glm-opus") == "glm"
    assert cc.resolve_model("glm-sonnet") == "glm-5-turbo"
    assert cc.resolve_model("glm-haiku") == "glm-4.5-air"
    assert cc.resolve_model("openai:gpt-5.5") == "gpt-5.5"
    assert cc.model_provider("openai:gpt-5.5") == "codex"
    assert cc.model_provider("glm:glm-5") == "glm"


def test_call_claude_routes_codex_to_cli(monkeypatch):
    monkeypatch.setattr(cc, "is_codex_available", lambda: True)
    calls = []

    class _Completed:
        returncode = 0
        stdout = b"transcript noise"
        stderr = b""

    def fake_run(cmd, input=None, stdout=None, stderr=None, timeout=None, **kwargs):
        calls.append((cmd, input, timeout))
        out_path = Path(cmd[cmd.index("--output-last-message") + 1])
        out_path.write_text('{"ok": true}', encoding="utf-8")
        return _Completed()

    monkeypatch.setattr(cc.subprocess, "run", fake_run)

    out = cc.call_claude(
        "hello",
        model="codex-sonnet",
        effort="max",
        use_search=True,
        timeout=12,
    )

    assert out == "{\"ok\": true}"
    cmd, stdin_bytes, timeout = calls[0]
    assert cmd[:5] == ["codex", "--search", "exec", "--model", "gpt-5.4"]
    assert "--search" in cmd
    assert "--sandbox" in cmd
    assert cmd[cmd.index("--output-last-message") + 1] != "-"
    assert stdin_bytes == b"hello"
    assert timeout == 12


def test_call_claude_routes_glm_to_zai(monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "zai-test")
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, headers, json, timeout))
        return _Resp({"choices": [{"message": {"content": "{\"ok\": true}"}}]})

    monkeypatch.setattr(cc.requests, "post", fake_post)

    out = cc.call_claude("hello", model="glm-opus", effort="low", timeout=9)

    assert out == "{\"ok\": true}"
    url, headers, payload, timeout = calls[0]
    assert url == "https://api.z.ai/api/paas/v4/chat/completions"
    assert headers["Authorization"] == "Bearer zai-test"
    assert payload["model"] == "glm-5.1"
    assert "thinking" not in payload
    assert timeout == 9


def test_call_claude_codex_missing_cli_errors(monkeypatch):
    monkeypatch.setattr(cc, "is_codex_available", lambda: False)

    with pytest.raises(cc.ClaudeNotFoundError):
        cc.call_claude("hello", model="codex-sonnet")
