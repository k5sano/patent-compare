#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
検索結果をプロンプトに注入するモジュール（v2: 複数戦略対応）

Claude CLIを呼ぶ前にPython側で検索を実行し、
結果をプロンプト末尾に付加する。

戦略:
1. Google Patents（国際）  — SerpAPI google_patents エンジン
2. Google Patents（JP限定）— 同上、country:JP フィルタ
3. Google Scholar          — SerpAPI google_scholar エンジン
"""

import re
import logging
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import List, Optional

import requests
import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# --- データクラス ---

@dataclass
class SearchHit:
    """検索1件分の正規化データ"""
    patent_id: str
    title: str
    assignee: str = ""
    priority_date: str = ""
    snippet: str = ""
    source: str = ""          # "google_patents" / "google_patents_jp" / "google_scholar"
    url: str = ""
    pdf_url: str = ""

    @property
    def dedup_key(self) -> str:
        """重複判定キー: 番号の数字部分のみ"""
        return re.sub(r'[\s\-/A-Za-z]', '', self.patent_id)


# --- 設定読み込み ---

def _load_serpapi_key() -> str:
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


# --- メインAPI ---

def inject_search_results(
    prompt_text: str,
    segments: list,
    keywords: Optional[list] = None,
    field: str = "cosmetics",
) -> str:
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
        logger.info("SerpAPIキー未設定 — 事前検索スキップ")
        return prompt_text

    search_terms = _extract_search_keywords(segments, keywords, field)
    if not search_terms:
        logger.info("検索キーワード抽出不可 — 事前検索スキップ")
        return prompt_text

    logger.info("事前検索キーワード: %s", search_terms)

    # --- 複数戦略で検索 ---
    all_hits: List[SearchHit] = []

    # 戦略1: Google Patents（国際）
    hits = _search_google_patents(api_key, search_terms)
    all_hits.extend(hits)

    # 戦略2: Google Patents（JP限定）
    hits_jp = _search_google_patents_jp(api_key, search_terms)
    all_hits.extend(hits_jp)

    # 戦略3: Google Scholar
    hits_scholar = _search_google_scholar(api_key, search_terms, field)
    all_hits.extend(hits_scholar)

    if not all_hits:
        logger.info("事前検索結果なし")
        return prompt_text

    # --- 重複除去 ---
    unique = _deduplicate(all_hits)
    logger.info("事前検索: %d件取得 → 重複除去後 %d件", len(all_hits), len(unique))

    # --- テキスト組み立て ---
    injection = _format_results(unique, search_terms)
    return (
        prompt_text
        + f"\n\n---\n\n{injection}\n\n"
        "※上記は事前検索の参考情報です。これらを踏まえつつ、"
        "あなた自身の知識も合わせて候補を提案してください。"
    )


# --- キーワード抽出 ---

def _extract_search_keywords(
    segments: list,
    keywords: Optional[list] = None,
    field: str = "cosmetics",
) -> List[str]:
    """分節とキーワードグループから検索用語を抽出"""
    terms: List[str] = []

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
                text = seg.get("text", "")
                # カタカナ3文字以上 or 漢字2文字以上
                words = re.findall(
                    r'[ァ-ヴー]{3,}|[一-龥]{2,}(?:剤|物|体|油|酸|液|層|膜)?',
                    text,
                )
                terms.extend(words[:2])
            break

    # 重複除去して最大8語
    return list(dict.fromkeys(terms))[:8]


# --- 検索戦略 ---

def _search_google_patents(api_key: str, search_terms: List[str]) -> List[SearchHit]:
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
            return []

        data = resp.json()
        hits = []
        for r in data.get("organic_results", [])[:8]:
            hits.append(SearchHit(
                patent_id=r.get("patent_id", ""),
                title=r.get("title", ""),
                assignee=r.get("assignee", ""),
                priority_date=r.get("priority_date", ""),
                snippet=(r.get("snippet", "") or "")[:200],
                source="google_patents",
                url=r.get("link", ""),
                pdf_url=r.get("pdf", ""),
            ))
        return hits
    except Exception as e:
        logger.warning("Google Patents検索エラー: %s", e)
        return []


def _search_google_patents_jp(api_key: str, search_terms: List[str]) -> List[SearchHit]:
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
            logger.warning("Google Patents JP検索失敗: status=%d", resp.status_code)
            return []

        data = resp.json()
        hits = []
        for r in data.get("organic_results", [])[:5]:
            hits.append(SearchHit(
                patent_id=r.get("patent_id", ""),
                title=r.get("title", ""),
                assignee=r.get("assignee", ""),
                priority_date=r.get("priority_date", ""),
                snippet=(r.get("snippet", "") or "")[:200],
                source="google_patents_jp",
                url=r.get("link", ""),
                pdf_url=r.get("pdf", ""),
            ))
        return hits
    except Exception as e:
        logger.warning("Google Patents JP検索エラー: %s", e)
        return []


def _search_google_scholar(
    api_key: str,
    search_terms: List[str],
    field: str = "cosmetics",
) -> List[SearchHit]:
    """SerpApi経由でGoogle Scholar検索（特許含む学術文献）"""
    # 分野名をクエリに付加
    field_label = {"cosmetics": "cosmetic", "laminate": "laminate film"}.get(field, field)
    query = f"patent {field_label} " + " ".join(search_terms[:4])
    logger.info("Google Scholar検索: %s", query)

    try:
        resp = requests.get("https://serpapi.com/search", params={
            "engine": "google_scholar",
            "q": query,
            "num": 5,
            "api_key": api_key,
        }, timeout=20)
        if resp.status_code != 200:
            logger.warning("Google Scholar検索失敗: status=%d", resp.status_code)
            return []

        data = resp.json()
        hits = []
        for r in data.get("organic_results", [])[:5]:
            # Scholar結果から特許IDを抽出（あれば）
            title = r.get("title", "")
            snippet = (r.get("snippet", "") or "")[:200]
            link = r.get("link", "")

            # 特許番号の抽出を試みる
            patent_id = ""
            id_match = re.search(
                r'(US\d{7,}[AB]?\d?|JP\d{4,}[AB]?\d?|WO\d{4}/?\d{6}|EP\d{7})',
                title + " " + snippet,
            )
            if id_match:
                patent_id = id_match.group(1)

            hits.append(SearchHit(
                patent_id=patent_id or title[:40],
                title=title,
                assignee=", ".join(
                    a.get("name", "") for a in r.get("publication_info", {}).get("authors", [])
                ),
                snippet=snippet,
                source="google_scholar",
                url=link,
            ))
        return hits
    except Exception as e:
        logger.warning("Google Scholar検索エラー: %s", e)
        return []


# --- 後処理 ---

def _deduplicate(hits: List[SearchHit]) -> List[SearchHit]:
    """重複除去: patent_id の数字部分が同一なら先着を優先"""
    seen: dict = {}
    result: List[SearchHit] = []
    for h in hits:
        key = h.dedup_key
        if not key or key in seen:
            continue
        seen[key] = True
        result.append(h)
    return result


def _format_results(hits: List[SearchHit], search_terms: List[str]) -> str:
    """検索結果をMarkdownテキストにフォーマット"""
    lines = [
        "## 事前検索結果",
        f"検索キーワード: {', '.join(search_terms)}",
        f"取得件数: {len(hits)}件",
        "",
    ]

    # ソース別にグルーピング
    source_labels = {
        "google_patents": "Google Patents（国際）",
        "google_patents_jp": "Google Patents（日本）",
        "google_scholar": "Google Scholar",
    }

    current_source = ""
    for h in hits:
        if h.source != current_source:
            current_source = h.source
            label = source_labels.get(current_source, current_source)
            lines.append(f"\n### {label}")

        lines.append(f"- **{h.patent_id}**: {h.title}")
        if h.assignee:
            lines.append(f"  出願人/著者: {h.assignee}")
        if h.priority_date:
            lines.append(f"  優先日: {h.priority_date}")
        if h.snippet:
            lines.append(f"  概要: {h.snippet}")
        if h.pdf_url:
            lines.append(f"  PDF: {h.pdf_url}")

    return "\n".join(lines)
