#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""成分名/技術用語の表記揺れを LLM で展開する。

予備調査の最初のステップ。例:
    入力: "サッカリン"
    出力: ["サッカリン", "サッカリンナトリウム", "サッカリンNa", "サッカリン酸",
           "o-スルホベンズイミド", "Saccharin", "CAS:81-07-2"]

LLM 呼び出しは modules.claude_client.call_claude を使う (Claude Code CLI 経由 OAuth)。
keyword_recommender が使う Anthropic SDK 直接呼び出しと比べて、API キー不要で
案件ローカル開発時の障壁が低い。失敗時は元の用語のみを含むリストを返す
(呼び出し側がフォールバック表示できるようにする)。
"""

from __future__ import annotations

import logging
import re

from modules.claude_client import call_claude, ClaudeClientError

logger = logging.getLogger(__name__)


_DEFAULT_PROMPT_HINT = "技術用語の別名、略称、英訳を列挙してください"


def _build_prompt(term: str, prompt_hint: str) -> str:
    return (
        f"{prompt_hint}\n\n"
        f"対象用語: {term}\n\n"
        "表記揺れ候補を1行1個で列挙してください。説明・前置き・番号付け・記号は不要です。\n"
        "元の用語も含めてください。"
    )


def _parse_lines(response: str) -> list[str]:
    """LLM 応答を行ごとに分解して、装飾を取り除いた候補リストを返す。"""
    if not response:
        return []
    lines = []
    for raw in response.splitlines():
        s = raw.strip()
        if not s:
            continue
        # 番号付けやマーカーを除去 (例: "1. xxx", "- xxx", "* xxx", "・ xxx")
        s = re.sub(r"^\s*(?:[-*・]|\d+[.)、])\s*", "", s)
        # コードフェンスや quote マーカーを除去
        s = s.strip("`\"'")
        s = s.strip()
        if not s:
            continue
        # 「対象用語: xxx」「表記揺れ:」のような前置きはスキップ
        if re.match(r"^(対象用語|表記揺れ|候補|出力)\s*[:：]", s):
            continue
        lines.append(s)
    # 重複除去 (順序保持)
    seen: set[str] = set()
    unique: list[str] = []
    for s in lines:
        if s in seen:
            continue
        seen.add(s)
        unique.append(s)
    return unique


def expand_synonyms(term: str, prompt_hint: str | None = None,
                    timeout: int = 90) -> list[str]:
    """LLM (Claude Code CLI 経由) で表記揺れを列挙する。

    - 失敗時 (CLI 未インストール / タイムアウト / 例外) は `[term]` を返す
    - 成功時は元の用語が含まれていなければ先頭に追加して返す
    """
    t = (term or "").strip()
    if not t:
        return []

    hint = (prompt_hint or _DEFAULT_PROMPT_HINT).strip()
    prompt = _build_prompt(t, hint)

    try:
        raw = call_claude(prompt, timeout=timeout)
    except ClaudeClientError as e:
        logger.warning("synonym_expander: claude 呼び出し失敗 — %s", e)
        return [t]
    except Exception as e:
        logger.warning("synonym_expander: 想定外のエラー — %s", e)
        return [t]

    candidates = _parse_lines(raw)
    if not candidates:
        return [t]

    # 元の用語を必ず先頭に
    if t not in candidates:
        candidates.insert(0, t)
    return candidates
