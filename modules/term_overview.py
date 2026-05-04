#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""本願明細書から特定 term の扱い (定義 / 例示 / 実施例 / 効果 / 臨界的効果) を要約。

予備調査 (Step 2 SUB 2) で表記ゆれを展開する際、並行して
「本願ではこの term が具体的にどう書かれているか」を LLM に要約させ、
ユーザーが「単なる用語リスト」ではなく「本願文脈での意味」を踏まえて
キーワード採用判断できるようにする。

軽量化方針 (memory: feedback_chat_context_strategy):
- 本願全文 ではなく term を含む claim/paragraph のみ抽出して prompt に注入
- これで context は通常 数千字 以内に収まる
"""

from __future__ import annotations

import logging
import re
import unicodedata

from modules.claude_client import call_claude, ClaudeClientError

logger = logging.getLogger(__name__)


def _normalize_for_match(s: str) -> str:
    """検索一致判定用の正規化。

    PDF 抽出時に全角英数字 (Ｘ-２５-９１３８Ａ) や OCR 由来の語間空白
    (Ｘ - ２ ５ - ９ １ ３ ８ Ａ) が混じるため、NFKC で全角→半角化、
    全空白を除去、英大小同一視 で吸収する。

    LLM に渡す抜粋テキストはこの正規化を**しない** (原文を保つ)。
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", "", s)
    return s.lower()


def _extract_relevant(term: str, hongan: dict, char_budget: int = 20000) -> list[str]:
    """term を含む claim/paragraph を抽出。char_budget を超えたら以降省略。

    照合は NFKC + 空白除去 + 大小無視で行い、抜粋テキストは原文をそのまま返す。
    """
    excerpts: list[str] = []
    used = 0
    term_norm = _normalize_for_match(term)
    if not term_norm:
        return excerpts

    for c in hongan.get("claims") or []:
        text = c.get("text") or ""
        if term_norm in _normalize_for_match(text):
            entry = f"請求項{c.get('number')}: {text}"
            if used + len(entry) > char_budget:
                break
            excerpts.append(entry)
            used += len(entry)

    paragraphs = hongan.get("paragraphs") or []
    truncated_at = None
    for i, p in enumerate(paragraphs):
        text = p.get("text") or ""
        if term_norm in _normalize_for_match(text):
            section = p.get("section", "") or ""
            entry = f"【{p.get('id', '')}】({section}) {text}"
            if used + len(entry) > char_budget:
                truncated_at = i
                break
            excerpts.append(entry)
            used += len(entry)
    if truncated_at is not None:
        excerpts.append(f"... (以降省略、本願 {len(paragraphs)} 段落中 {truncated_at+1} 段落目以降)")
    return excerpts


def summarize_term_in_hongan(term: str, hongan: dict,
                             timeout: int = 120) -> dict:
    """本願での term の扱いを LLM で要約。

    Returns:
        {
            "term": str,
            "found": bool,        # 本願に該当 term があったか
            "matches": int,       # 該当 claim/paragraph 数
            "overview_md": str,   # Markdown 概況 (LLM 出力)
            "error": str | None,  # 失敗時のみ
        }
    """
    t = (term or "").strip()
    if not t:
        return {"term": t, "found": False, "matches": 0, "overview_md": "", "error": None}

    excerpts = _extract_relevant(t, hongan)
    if not excerpts:
        return {
            "term": t,
            "found": False,
            "matches": 0,
            "overview_md": f"本願明細書中に「{t}」の記載は見当たりませんでした。",
            "error": None,
        }

    excerpts_text = "\n\n".join(excerpts)
    title = (hongan.get("patent_title") or "").strip()
    pn = (hongan.get("patent_number") or "").strip()

    prompt = f"""本願明細書 ({pn} / 発明の名称: {title}) において、用語「{t}」がどのように扱われているかを、下記の観点で簡潔に要約してください。

# 本願抜粋 (term を含む請求項・段落のみ)
{excerpts_text}

# 出力フォーマット (Markdown)
- **定義 / 範囲**: 定義・上位概念・包含する物質群が明示されている場合のみ
- **例示**: 具体的な化合物 / 物質名 / 種類 (請求項・段落で列挙されているもの)
- **実施例**: 実施例で具体的に検討されている内容、配合量、組合せ条件
- **効果**: 作用効果、目的、本願がこの用語に紐づけている価値
- **臨界的効果**: 数値範囲、限定条件、優位性 (「○%以下では××せず、○%超では△△する」等)

# 制約
- 本願記載のみから抽出 (Web 検索や一般常識で補わない)
- 該当が無い項目は省略してよい (「定義は明示なし」など書かない)
- 前置き・結論まとめ・推測は不要
- 300〜600 字目安。Markdown のみ
"""

    try:
        raw = call_claude(prompt, timeout=timeout)
    except ClaudeClientError as e:
        logger.warning("term_overview: claude 呼び出し失敗 — %s", e)
        return {
            "term": t,
            "found": True,
            "matches": len(excerpts),
            "overview_md": "",
            "error": str(e),
        }
    except Exception as e:
        logger.warning("term_overview: 想定外のエラー — %s", e)
        return {
            "term": t,
            "found": True,
            "matches": len(excerpts),
            "overview_md": "",
            "error": str(e),
        }

    return {
        "term": t,
        "found": True,
        "matches": len(excerpts),
        "overview_md": (raw or "").strip(),
        "error": None,
    }
