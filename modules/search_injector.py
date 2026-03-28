#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
検索結果をプロンプトに注入するモジュール

Claude CLIを呼ぶ前にPython側で検索を実行し、
結果をプロンプト末尾に付加する。
"""

import re
import logging
from pathlib import Path

import requests
import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.resolve()


def _load_serpapi_key():
    """config.yamlからSerpAPIキーを読み込む"""
    config_path = PROJECT_ROOT / "config.yaml"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            return cfg.get("serpapi_key", "")
        except Exception:
            pass
    return ""


def inject_search_results(prompt_text, segments, keywords=None, field="cosmetics"):
    """プロンプトに検索結果を注入する

    Args:
        prompt_text: 元のプロンプト
        segments: 請求項分節データ (segments.json)
        keywords: キーワードグループ (keywords.json, optional)
        field: 技術分野

    Returns:
        検索結果を注入したプロンプト
    """
    api_key = _load_serpapi_key()
    if not api_key:
        return prompt_text

    search_terms = _extract_search_keywords(segments, keywords, field)
    if not search_terms:
        return prompt_text

    results_text = []

    # Google Patents検索（国際）
    global_results = _search_google_patents(api_key, search_terms)
    if global_results:
        results_text.append("## 事前検索結果: Google Patents\n" + global_results)

    # Google Patents検索（JP限定）
    jp_results = _search_google_patents_jp(api_key, search_terms)
    if jp_results:
        results_text.append("## 事前検索結果: 日本特許\n" + jp_results)

    if not results_text:
        return prompt_text

    injection = "\n\n".join(results_text)
    return prompt_text + f"\n\n---\n\n{injection}\n\n※上記は事前検索の参考情報です。これらを踏まえつつ、あなた自身の知識も合わせて候補を提案してください。"


def _extract_search_keywords(segments, keywords=None, field="cosmetics"):
    """分節とキーワードグループから検索用語を抽出"""
    terms = []

    # 1. キーワードグループがあればそこから取得（ユーザーが選定済み）
    if keywords and isinstance(keywords, list):
        for group in keywords:
            for kw in group.get("keywords", [])[:3]:
                term = kw.get("term", "") if isinstance(kw, dict) else str(kw)
                if term:
                    terms.append(term)

    # 2. 分節から名詞句を簡易抽出（キーワードが不足する場合）
    if len(terms) < 4:
        for claim in segments:
            if claim.get("claim_number") != 1:
                continue
            for seg in claim.get("segments", [])[:5]:
                words = re.findall(
                    r'[ァ-ヴー]{3,}|[一-龥]{2,}(?:剤|物|体|油|酸|液|層|膜)?',
                    seg.get("text", "")
                )
                terms.extend(words[:2])
            break

    # 重複除去して最大8語
    return list(dict.fromkeys(terms))[:8]


def _search_google_patents(api_key, search_terms):
    """SerpApi経由でGoogle Patents検索（国際）"""
    query = " ".join(search_terms[:5])
    logger.info("Google Patents検索: %s", query)

    try:
        resp = requests.get("https://serpapi.com/search", params={
            "engine": "google_patents",
            "q": query,
            "num": 8,
            "api_key": api_key,
        }, timeout=20)
        if resp.status_code != 200:
            logger.warning("Google Patents検索失敗: status=%d", resp.status_code)
            return ""

        data = resp.json()
        lines = [f"検索クエリ: {query}"]
        for r in data.get("organic_results", [])[:8]:
            patent_id = r.get("patent_id", "")
            title = r.get("title", "")
            assignee = r.get("assignee", "")
            priority_date = r.get("priority_date", "")
            snippet = r.get("snippet", "")[:200]
            pdf_url = r.get("pdf", "")
            lines.append(
                f"- {patent_id}: {title}\n"
                f"  出願人: {assignee}, 優先日: {priority_date}\n"
                f"  概要: {snippet}\n"
                f"  PDF: {pdf_url}"
            )
        return "\n".join(lines) if len(lines) > 1 else ""
    except Exception as e:
        logger.warning("Google Patents検索エラー: %s", e)
        return ""


def _search_google_patents_jp(api_key, search_terms):
    """SerpApi経由でGoogle Patents検索（日本特許限定）"""
    query = " ".join(search_terms[:4]) + " country:JP"
    logger.info("Google Patents JP検索: %s", query)

    try:
        resp = requests.get("https://serpapi.com/search", params={
            "engine": "google_patents",
            "q": query,
            "num": 5,
            "api_key": api_key,
        }, timeout=20)
        if resp.status_code != 200:
            return ""

        data = resp.json()
        lines = [f"検索クエリ: {query}"]
        for r in data.get("organic_results", [])[:5]:
            patent_id = r.get("patent_id", "")
            title = r.get("title", "")
            assignee = r.get("assignee", "")
            priority_date = r.get("priority_date", "")
            snippet = r.get("snippet", "")[:200]
            lines.append(
                f"- {patent_id}: {title}\n"
                f"  出願人: {assignee}, 優先日: {priority_date}\n"
                f"  概要: {snippet}"
            )
        return "\n".join(lines) if len(lines) > 1 else ""
    except Exception as e:
        logger.warning("Google Patents JP検索エラー: %s", e)
        return ""
