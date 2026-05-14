"""table_extractor LLM routing tests."""
from __future__ import annotations

from pathlib import Path
import json
import subprocess

from services.table_extractor import extract_table_via_claude


def test_table_extraction_uses_codex_image_call(monkeypatch, tmp_path):
    img = tmp_path / "table.png"
    img.write_bytes(b"fake-image")

    def fake_call(prompt, image_path, timeout=600, model=None, effort="low"):
        assert "添付画像を読み取り" in prompt
        assert Path(image_path) == img.resolve()
        assert model == "codex-sonnet"
        return '{"is_table": true, "title": "表1", "headers": ["成分"], "rows": [{"cells": ["水"]}]}'

    monkeypatch.setattr("modules.claude_client.call_llm_with_image", fake_call)

    result = extract_table_via_claude(img, model="codex-sonnet")

    assert result.is_table is True
    assert result.title == "表1"
    assert result.headers == ["成分"]
    assert result.rows == [{"cells": ["水"]}]


def test_table_extraction_parses_pretty_codex_json(monkeypatch, tmp_path):
    img = tmp_path / "table.png"
    img.write_bytes(b"fake-image")

    def fake_call(prompt, image_path, timeout=600, model=None, effort="low"):
        return """{
  "is_table": true,
  "title": "表2",
  "headers": ["項目", "実施例1"],
  "rows": [
    {"cells": ["融解熱量比", "6%"]}
  ]
}"""

    monkeypatch.setattr("modules.claude_client.call_llm_with_image", fake_call)

    result = extract_table_via_claude(img, model="codex-sonnet")

    assert result.is_table is True
    assert result.title == "表2"
    assert result.headers == ["項目", "実施例1"]
    assert result.rows == [{"cells": ["融解熱量比", "6%"]}]


def test_table_extraction_uses_qwen_vl_local_image_call(monkeypatch, tmp_path):
    img = tmp_path / "table.png"
    img.write_bytes(b"fake-image")

    def fake_call(prompt, image_path, timeout=600, model=None, effort="low"):
        assert "添付画像を読み取り" in prompt
        assert Path(image_path) == img.resolve()
        assert model == "qwen2.5-vl"
        return '{"is_table": true, "title": "表1", "headers": ["成分"], "rows": [{"cells": ["樹脂A"]}]}'

    monkeypatch.setattr("modules.claude_client.call_llm_with_image", fake_call)

    result = extract_table_via_claude(img, model="qwen2.5-vl")

    assert result.is_table is True
    assert result.model == "qwen2.5-vl"
    assert result.title == "表1"
    assert result.headers == ["成分"]
    assert result.rows == [{"cells": ["樹脂A"]}]


def test_table_extraction_rejects_glm_image_model(tmp_path):
    img = tmp_path / "table.png"
    img.write_bytes(b"fake-image")

    result = extract_table_via_claude(img, model="glm-opus")

    assert result.is_table is False
    assert "画像入力" in result.error


def test_claude_table_extraction_ignores_anthropic_api_key(monkeypatch, tmp_path):
    img = tmp_path / "table.png"
    img.write_bytes(b"fake-image")
    seen = {}

    def fake_run(cmd, capture_output=False, timeout=None, check=False, env=None):
        seen["env"] = env
        body = {"is_table": True, "title": "表3", "headers": [], "rows": []}
        envelope = {
            "duration_ms": 1,
            "total_cost_usd": 0,
            "is_error": False,
            "result": "```json\n" + json.dumps(body, ensure_ascii=False) + "\n```",
        }
        return subprocess.CompletedProcess(cmd, 0, json.dumps(envelope).encode("utf-8"), b"")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "invalid")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr("subprocess.run", fake_run)

    result = extract_table_via_claude(img, model="sonnet")

    assert result.is_table is True
    assert result.title == "表3"
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert "CLAUDECODE" not in seen["env"]
