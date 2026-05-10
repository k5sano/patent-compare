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
    assert cmd[:7] == [
        "codex",
        "--disable",
        "plugins",
        "--search",
        "exec",
        "--model",
        "gpt-5.4",
    ]
    assert cmd[cmd.index("--disable") + 1] == "plugins"
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


def test_glm_retries_without_verify_on_ssl_error(monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "zai-test")
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs.get("verify"))
        if len(calls) == 1:
            raise cc.requests.exceptions.SSLError("certificate verify failed")
        return _Resp({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(cc.requests, "post", fake_post)

    out = cc.call_claude("hello", model="glm-opus", effort="low", timeout=9)

    assert out == "ok"
    assert calls[-1] is False


def test_glm_ssl_fallback_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "zai-test")
    monkeypatch.setenv("PATENT_COMPARE_INSECURE_SSL_FALLBACK", "0")

    def fake_post(url, **kwargs):
        raise cc.requests.exceptions.SSLError("certificate verify failed")

    monkeypatch.setattr(cc.requests, "post", fake_post)

    with pytest.raises(cc.ClaudeExecutionError) as ei:
        cc.call_claude("hello", model="glm-opus", effort="low", timeout=9)

    assert "certificate verify failed" in str(ei.value)


@pytest.mark.parametrize(
    ("status_code", "body", "exc_type", "needle"),
    [
        (401, '{"error":"unauthorized"}', cc.ClaudeNotFoundError, "GLM API キーが無効"),
        (429, '{"error":"rate limit"}', cc.ClaudeExecutionError, "GLM レート制限"),
        (404, '{"error":"model not found"}', cc.ClaudeExecutionError, "GLM モデル"),
    ],
)
def test_call_claude_glm_http_errors_are_classified(
    monkeypatch, status_code, body, exc_type, needle
):
    monkeypatch.setenv("ZAI_API_KEY", "zai-test")

    def fake_post(url, headers=None, json=None, timeout=None):
        return _Resp({}, status_code=status_code, text=body)

    monkeypatch.setattr(cc.requests, "post", fake_post)

    with pytest.raises(exc_type) as ei:
        cc.call_claude("hello", model="glm-sonnet", effort="low", timeout=9)

    assert needle in str(ei.value)


def test_call_claude_codex_missing_cli_errors(monkeypatch):
    monkeypatch.setattr(cc, "is_codex_available", lambda: False)

    with pytest.raises(cc.ClaudeNotFoundError):
        cc.call_claude("hello", model="codex-sonnet")
