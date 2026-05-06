"""table_extractor LLM routing tests."""
from __future__ import annotations

from pathlib import Path

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


def test_table_extraction_rejects_glm_image_model(tmp_path):
    img = tmp_path / "table.png"
    img.write_bytes(b"fake-image")

    result = extract_table_via_claude(img, model="glm-opus")

    assert result.is_table is False
    assert "画像入力" in result.error
