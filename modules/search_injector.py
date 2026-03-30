#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
検索結果をプロンプトに注入するモジュール（v3.0: Playwright直接検索）

Claude CLIを呼ぶ前にPython側で検索を実行し、
結果をプロンプト末尾に付加する。

戦略:
1. Google Patents（国際）  — Playwright ヘッドレスブラウザ直接検索
2. Google Patents（JP限定）— 同上、country:JP フィルタ
3. Google Scholar          — SerpAPI google_scholar エンジン（オプション）
"""

import os
import re
import logging
import concurrent.futures
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import List, Optional

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
    is_patent: bool = True    # Scholar結果で特許IDが取れなかった場合 False

    @property
    def dedup_key(self) -> str:
        """重複判定キー: 国コード + 数字部分"""
        upper_id = self.patent_id.upper().strip()
        prefix_match = re.match(r'^(US|JP|WO|EP|KR|CN|DE|FR|GB)', upper_id)
        prefix = prefix_match.group(1) if prefix_match else ""
        digits = re.sub(r'[^\d]', '', self.patent_id)
        if not digits:
            return ""
        return f"{prefix}{digits}"


# --- 設定読み込み ---

def _load_serpapi_key() -> str:
    """config.yaml または環境変数からSerpAPIキーを読み込む"""
    env_key = os.environ.get("SERPAPI_KEY", "")
    if env_key:
        return env_key

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
    tech_analysis: Optional[dict] = None,
) -> str:
    """プロンプトに検索結果を注入する

    3つの検索を並列実行し、全体10秒でタイムアウト。
    検索失敗・タイムアウト時もプロンプトはそのまま返す。

    Args:
        prompt_text: 元のプロンプト
        segments: 請求項分節データ (segments.json)
        keywords: キーワードグループ (keywords.json, optional)
        field: 技術分野
        tech_analysis: tech_analysis.json のデータ (optional)

    Returns:
        検索結果を注���したプロンプト
    """
    api_key = _load_serpapi_key()
    # SerpAPIキーがなくてもPlaywright検索（Google Patents）は実行可能

    # 複数の検索式を生成（分節ごと / キーワードグループごと）
    queries = _build_search_queries(segments, keywords, field,
                                    tech_analysis=tech_analysis)
    if not queries:
        logger.info("検索式生成不可 — 事前検索スキップ")
        return prompt_text

    # _format_results 用にフラットなキーワードリストも保持
    search_terms = _extract_search_keywords(segments, keywords, field,
                                            tech_analysis=tech_analysis)

    logger.info("事前検索: %d検索式を並列実行 — %s", len(queries), queries)

    # --- 検索式ごとに国際+JP、全件並列実行 ---
    # 各検索式 × (国際, JP) + Scholar(あれば) = 最大 N*2+1 並列
    all_hits: List[SearchHit] = []
    futures = {}
    num_workers = min(len(queries) * 2 + (1 if api_key else 0), 6)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        for i, q in enumerate(queries):
            futures[executor.submit(
                _search_google_patents_query, q, "", 5
            )] = f"patents_{i}"
            futures[executor.submit(
                _search_google_patents_query, q, "JP", 5
            )] = f"patents_jp_{i}"

        if api_key:
            futures[executor.submit(
                _search_google_scholar, api_key, search_terms, field
            )] = "scholar"
        else:
            logger.info("SerpAPIキー未設定 — Google Scholar検索スキップ（Patents検索は実行）")

        try:
            for future in concurrent.futures.as_completed(futures, timeout=60):
                try:
                    all_hits.extend(future.result())
                except Exception as e:
                    logger.warning("検索失敗（スキップ）: %s — %s", futures[future], e)
        except concurrent.futures.TimeoutError:
            logger.warning("事前検索タイムアウト（60秒）— 取得済み結果で続行")

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
        + "※上記は事前検索の参考情報です。"
    )


# --- キーワード抽出 ---

_STOP_WORDS = {
    "前記", "含有", "含有する", "からなる", "有する", "備える",
    "において", "であって", "であり", "おける", "よる", "する",
    "された", "される", "および", "ならびに", "または", "もしくは",
    "以上", "以下", "未満", "超える", "含む", "特徴", "記載",
}


def _extract_search_keywords(
    segments: list,
    keywords: Optional[list] = None,
    field: str = "cosmetics",
    tech_analysis: Optional[dict] = None,
) -> List[str]:
    """分節とキーワードグループから検索用語を抽出

    優先順位:
    1. tech_analysis から技術概念語（AI分析済み、最も精度が高い）
    2. keywords.json のキーワードグループ（ユーザー選定済み）
    3. 正規表現による分節からの簡易抽出（フォールバック）
    """
    terms: List[str] = []

    # 1. tech_analysis から主要技術概念を取得（最優先）
    if tech_analysis and isinstance(tech_analysis, dict):
        for seg_key, seg_data in tech_analysis.items():
            if not isinstance(seg_data, dict):
                continue
            # technical_concept フィールド
            concept = seg_data.get("technical_concept", "")
            if concept and len(concept) >= 2:
                terms.append(concept)
            # keywords リスト
            ta_keywords = seg_data.get("keywords", [])
            if isinstance(ta_keywords, list):
                for kw in ta_keywords[:2]:
                    kw_str = str(kw).strip()
                    if kw_str and len(kw_str) >= 2:
                        terms.append(kw_str)

    # 2. キーワードグループがあればそこから取得（ユーザーが選定済み）
    if keywords and isinstance(keywords, list):
        for group in keywords:
            for kw in group.get("keywords", [])[:3]:
                term = kw.get("term", "") if isinstance(kw, dict) else str(kw)
                if term and len(term) >= 2:
                    terms.append(term)

    # 3. 分節から名詞句を簡易抽出（キーワードが不足する場合）
    #    請求項1の全分節を走査 + ストップワード除外
    if len(terms) < 4:
        for claim in segments:
            if claim.get("claim_number") != 1:
                continue
            for seg in claim.get("segments", []):
                text = seg.get("text", "")
                # カタカナ3文字以上 or 漢字2文字以上
                words = re.findall(
                    r'[ァ-ヴー]{3,}|[一-龥]{2,}(?:剤|物|体|油|酸|液|層|膜|比|料)?',
                    text,
                )
                for w in words:
                    if w not in _STOP_WORDS and len(w) >= 2:
                        terms.append(w)
            break

    # 重複除去して最大10語（tech_analysisがある場合は語数増加）
    return list(dict.fromkeys(terms))[:10]


def _build_search_queries(
    segments: list,
    keywords: Optional[list] = None,
    field: str = "cosmetics",
    tech_analysis: Optional[dict] = None,
) -> List[str]:
    """複数の検索式を生成する

    tech_analysis がある場合は分節ごとに検索式を生成。
    なければキーワードグループごと、最終手段は全語結合の1式。
    """
    queries: List[str] = []

    # 1. tech_analysis から分節ごとの検索式
    if tech_analysis and isinstance(tech_analysis, dict):
        for seg_key, seg_data in tech_analysis.items():
            if not isinstance(seg_data, dict):
                continue
            parts = []
            concept = seg_data.get("technical_concept", "")
            if concept:
                parts.append(concept)
            ta_keywords = seg_data.get("keywords", [])
            if isinstance(ta_keywords, list):
                for kw in ta_keywords[:3]:
                    kw_str = str(kw).strip()
                    if kw_str and len(kw_str) >= 2:
                        parts.append(kw_str)
            if parts:
                queries.append(" ".join(parts))

    # 2. キーワードグループごとの検索式
    if keywords and isinstance(keywords, list):
        for group in keywords:
            parts = []
            for kw in group.get("keywords", [])[:4]:
                term = kw.get("term", "") if isinstance(kw, dict) else str(kw)
                if term and len(term) >= 2:
                    parts.append(term)
            if parts:
                queries.append(" ".join(parts))

    # 3. フォールバック: 全キーワードを1式にまとめる
    if not queries:
        all_terms = _extract_search_keywords(segments, keywords, field,
                                             tech_analysis=tech_analysis)
        if all_terms:
            queries.append(" ".join(all_terms[:5]))

    # 重複除去
    return list(dict.fromkeys(queries))


# --- 検索戦略 ---

def _search_google_patents_query(
    query: str, country: str = "", max_results: int = 5,
) -> List[SearchHit]:
    """Playwright経由でGoogle Patents検索（1検索式）"""
    from modules.google_patents_scraper import search_google_patents

    source = "google_patents_jp" if country == "JP" else "google_patents"
    label = f"Google Patents{'(' + country + ')' if country else ''}"
    logger.info("%s検索 (Playwright): %s", label, query)

    try:
        raw_hits = search_google_patents(query, country=country, max_results=max_results)
        return [
            SearchHit(
                patent_id=h.patent_id,
                title=h.title,
                assignee=h.assignee,
                priority_date=h.priority_date,
                snippet=h.snippet,
                source=source,
                url=h.url,
                pdf_url=h.pdf_url,
                is_patent=h.is_patent,
            )
            for h in raw_hits
        ]
    except Exception as e:
        logger.warning("%s検索エラー: %s", label, e)
        return []


def _search_google_scholar(
    api_key: str,
    search_terms: List[str],
    field: str = "cosmetics",
) -> List[SearchHit]:
    """SerpApi経由でGoogle Scholar検索（特許含む学術文献）"""
    import requests

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

            is_patent = bool(patent_id)
            display_id = patent_id if patent_id else f"[Scholar] {title[:50]}"

            hits.append(SearchHit(
                patent_id=display_id,
                title=title,
                assignee=", ".join(
                    a.get("name", "") for a in r.get("publication_info", {}).get("authors", [])
                ),
                snippet=snippet,
                source="google_scholar",
                url=link,
                is_patent=is_patent,
            ))
        return hits
    except Exception as e:
        logger.warning("Google Scholar検索エラー: %s", e)
        return []


# --- 後処理 ---

def _deduplicate(hits: List[SearchHit]) -> List[SearchHit]:
    """重複除去: 国コード+数字部分が同一なら先着を優先
    非特許文献（is_patent=False）はURLベースで重複判定"""
    seen_patents: dict = {}
    seen_urls: set = set()
    result: List[SearchHit] = []

    for h in hits:
        if h.is_patent:
            key = h.dedup_key
            if not key or key in seen_patents:
                continue
            seen_patents[key] = True
        else:
            url_key = h.url.strip().lower()
            if url_key in seen_urls:
                continue
            if url_key:
                seen_urls.add(url_key)

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
        if h.url:
            lines.append(f"  URL: {h.url}")

    return "\n".join(lines)
