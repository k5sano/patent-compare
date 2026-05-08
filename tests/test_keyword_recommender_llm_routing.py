"""keyword_recommender LLM routing tests."""
from __future__ import annotations

from modules import keyword_recommender as kr


def test_recommend_regex_uses_shared_llm_by_default_even_with_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "invalid-api-key")
    monkeypatch.delenv("PATENT_COMPARE_USE_ANTHROPIC_API", raising=False)
    called = {"cli": False}

    def fake_cli(results, spec_text, field):
        called["cli"] = True
        results[0]["keywords"].append({
            "term": "追加語",
            "source": "llm",
            "type": "明細書関連語",
        })

    monkeypatch.setattr(kr, "_cli_enrich_keywords", fake_cli)

    out = kr.recommend_regex(
        [{"segments": [{"id": "1A", "text": "樹脂フィルム"}]}],
        {"paragraphs": [{"id": "0001", "text": "追加語を含む。"}]},
        "laminate",
    )

    assert called["cli"] is True
    assert any(k["term"] == "追加語" for k in out[0]["keywords"])


def test_anthropic_api_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    monkeypatch.delenv("PATENT_COMPARE_USE_ANTHROPIC_API", raising=False)
    assert kr._use_anthropic_api() is False

    monkeypatch.setenv("PATENT_COMPARE_USE_ANTHROPIC_API", "1")
    assert kr._use_anthropic_api() is True
