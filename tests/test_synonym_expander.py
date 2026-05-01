"""synonym_expander の単体テスト。

LLM 呼び出し (modules.claude_client.call_claude) を monkeypatch でモック化して、
- 応答パース (番号付け/マーカー除去/重複除外)
- 元の用語が常に先頭に含まれる
- LLM エラー時のフォールバック ([term] のみ返す)
を検証する。
"""
from __future__ import annotations

import pytest

from modules import synonym_expander
from modules.claude_client import ClaudeClientError


class TestParseLines:
    def test_strips_bullet_markers(self):
        raw = """- foo
* bar
1. baz
2) qux
・ quux"""
        out = synonym_expander._parse_lines(raw)
        assert out == ["foo", "bar", "baz", "qux", "quux"]

    def test_skips_preamble_lines(self):
        raw = """対象用語: サッカリン
表記揺れ:
サッカリン
サッカリンナトリウム"""
        out = synonym_expander._parse_lines(raw)
        assert out == ["サッカリン", "サッカリンナトリウム"]

    def test_dedup_preserves_order(self):
        raw = "A\nB\nA\nC\nB"
        assert synonym_expander._parse_lines(raw) == ["A", "B", "C"]

    def test_strips_quotes_and_backticks(self):
        raw = "`foo`\n\"bar\"\n'baz'"
        assert synonym_expander._parse_lines(raw) == ["foo", "bar", "baz"]

    def test_empty_input(self):
        assert synonym_expander._parse_lines("") == []
        assert synonym_expander._parse_lines("\n\n  \n") == []


class TestExpandSynonyms:
    def test_returns_parsed_candidates_with_original_first(self, monkeypatch):
        def fake_call_claude(prompt, timeout=90):
            assert "サッカリン" in prompt
            return "サッカリンナトリウム\nサッカリンNa\nsaccharin"
        monkeypatch.setattr(
            "modules.synonym_expander.call_claude", fake_call_claude
        )
        result = synonym_expander.expand_synonyms("サッカリン")
        assert result[0] == "サッカリン", "元の用語が先頭に来るべき"
        assert "サッカリンナトリウム" in result
        assert "サッカリンNa" in result
        assert "saccharin" in result

    def test_keeps_original_when_already_in_response(self, monkeypatch):
        def fake_call_claude(prompt, timeout=90):
            return "サッカリン\nサッカリンナトリウム"
        monkeypatch.setattr(
            "modules.synonym_expander.call_claude", fake_call_claude
        )
        result = synonym_expander.expand_synonyms("サッカリン")
        # 重複しない (元用語が一回だけ)
        assert result.count("サッカリン") == 1

    def test_empty_term_returns_empty(self):
        assert synonym_expander.expand_synonyms("") == []
        assert synonym_expander.expand_synonyms("   ") == []

    def test_falls_back_on_claude_error(self, monkeypatch):
        def fake_call_claude(prompt, timeout=90):
            raise ClaudeClientError("CLI not found")
        monkeypatch.setattr(
            "modules.synonym_expander.call_claude", fake_call_claude
        )
        result = synonym_expander.expand_synonyms("サッカリン")
        assert result == ["サッカリン"]

    def test_falls_back_on_unexpected_error(self, monkeypatch):
        def fake_call_claude(prompt, timeout=90):
            raise RuntimeError("boom")
        monkeypatch.setattr(
            "modules.synonym_expander.call_claude", fake_call_claude
        )
        result = synonym_expander.expand_synonyms("foo")
        assert result == ["foo"]

    def test_falls_back_on_empty_response(self, monkeypatch):
        def fake_call_claude(prompt, timeout=90):
            return ""
        monkeypatch.setattr(
            "modules.synonym_expander.call_claude", fake_call_claude
        )
        result = synonym_expander.expand_synonyms("foo")
        assert result == ["foo"]

    def test_uses_custom_prompt_hint(self, monkeypatch):
        captured = {}
        def fake_call_claude(prompt, timeout=90):
            captured["prompt"] = prompt
            return "alpha\nbeta"
        monkeypatch.setattr(
            "modules.synonym_expander.call_claude", fake_call_claude
        )
        synonym_expander.expand_synonyms("X", prompt_hint="化粧品成分の表記揺れ")
        assert "化粧品成分の表記揺れ" in captured["prompt"]
