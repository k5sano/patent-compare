#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Google Patents ブラウザスクレイパー

Playwright (ヘッドレス Chromium) を使って Google Patents を直接検索する。
APIキー不要。

使い方:
    from modules.google_patents_scraper import search_google_patents
    hits = search_google_patents("エアゾール 化粧料 油状泡沫", max_results=10)
    hits_jp = search_google_patents("エアゾール 化粧料", country="JP", max_results=5)
"""

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)


@dataclass
class PatentHit:
    """検索結果1件"""
    patent_id: str
    title: str = ""
    assignee: str = ""
    priority_date: str = ""
    snippet: str = ""
    source: str = "google_patents"
    url: str = ""
    pdf_url: str = ""
    is_patent: bool = True


def search_google_patents(
    query: str,
    country: str = "",
    max_results: int = 10,
    language: str = "ja",
    timeout_ms: int = 20000,
) -> List[PatentHit]:
    """Google Patents をヘッドレスブラウザで検索する。

    Args:
        query: 検索クエリ (自然言語 or Google Patents 構文)
        country: 国コード制限 (例: "JP") → query に "country:JP" を付加
        max_results: 最大取得件数
        language: 言語設定 (デフォルト: "ja")
        timeout_ms: ページ読み込みタイムアウト (ms)

    Returns:
        PatentHit のリスト
    """
    if country:
        query = f"{query} country:{country}"

    encoded_q = quote_plus(query)
    url = (
        f"https://patents.google.com/?q={encoded_q}"
        f"&oq={encoded_q}&hl={language}&num={max_results}"
    )

    logger.info("Google Patents ブラウザ検索: %s", query)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error(
            "playwright 未インストール: "
            "pip install playwright && playwright install chromium"
        )
        return []

    hits: List[PatentHit] = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                locale=language,
            )
            page = context.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)

            hits = _extract_results(page, max_results)
            browser.close()

    except Exception as e:
        logger.warning("Google Patents ブラウザ検索エラー: %s", e)
        return []

    logger.info("Google Patents 検索結果: %d件", len(hits))
    return hits


def _extract_results(page, max_results: int) -> List[PatentHit]:
    """ページから検索結果を抽出する"""
    hits: List[PatentHit] = []

    # Google Patents: <search-result-item> > <article> 内に結果がある。
    # 特許IDは <state-modifier data-result="patent/JP2024037328A/ja"> に格納。
    items = page.query_selector_all("search-result-item")

    if not items:
        # フォールバック: HTMLテキストから正規表現抽出
        logger.info("search-result-item なし — HTML正規表現で抽出")
        return _extract_from_html(page.content(), max_results)

    for item in items[:max_results]:
        try:
            hit = _parse_result_item(item)
            if hit:
                hits.append(hit)
        except Exception as e:
            logger.debug("結果アイテムのパースエラー: %s", e)

    return hits


def _parse_result_item(item) -> Optional[PatentHit]:
    """1件の <search-result-item> をパース"""

    # --- 特許ID ---
    # state-modifier[data-result] の値: "patent/JP2024037328A/ja"
    sm = item.query_selector("state-modifier[data-result]")
    if not sm:
        return None

    data_result = sm.get_attribute("data-result") or ""
    m = re.search(r"patent/([^/]+)", data_result)
    if not m:
        return None

    patent_id = m.group(1)
    url = f"https://patents.google.com/patent/{patent_id}"

    # --- タイトル ---
    title = ""
    h3 = item.query_selector("h3")
    if h3:
        title = (h3.inner_text() or "").strip()

    # --- メタデータ（出願人・日付） ---
    # span.style-scope.search-result-item にメタデータが格納される:
    #   [国コード, patent_id, patent_id, 発明者, 発明者, ...]
    # .metadata にまとめて: "JP JP2024037328A Inventor Name Organization"
    assignee = ""
    priority_date = ""

    # .metadata テキストから出願人を抽出
    meta_el = item.query_selector(".metadata")
    if meta_el:
        meta_text = (meta_el.inner_text() or "").strip()
        # patent_id より後の部分を取得（国コード・IDを除去）
        parts = meta_text.split()
        # 国コード(2文字)とpatent_id文字列を除外して残りを出願人とする
        assignee_parts = []
        for part in parts:
            if len(part) <= 3 and part.isalpha():
                continue  # 国コード (JP, US, EP...)
            if patent_id in part:
                continue  # patent_id
            assignee_parts.append(part)
        assignee = " ".join(assignee_parts)

    # abstract div からPriority日付を抽出
    abstract_div = item.query_selector("div.abstract, .abstract")
    if abstract_div:
        full_text = (abstract_div.inner_text() or "").strip()
        date_m = re.search(r"Priority\s+(\d{4}-\d{2}-\d{2})", full_text)
        if date_m:
            priority_date = date_m.group(1)

    return PatentHit(
        patent_id=patent_id,
        title=title or patent_id,
        assignee=assignee,
        priority_date=priority_date,
        source="google_patents",
        url=url,
    )


def _extract_from_html(html: str, max_results: int) -> List[PatentHit]:
    """HTMLテキストから正規表現で特許IDを抽出（フォールバック）"""
    hits = []
    seen = set()

    for m in re.finditer(r'patent/([A-Z]{2}\d{4,}[A-Z]?\d?)', html):
        if len(hits) >= max_results:
            break

        patent_id = m.group(1)
        if patent_id in seen:
            continue
        seen.add(patent_id)

        hits.append(PatentHit(
            patent_id=patent_id,
            title=patent_id,
            source="google_patents",
            url=f"https://patents.google.com/patent/{patent_id}",
        ))

    return hits


# --- 便利関数 ---

def search_patents_with_keywords(
    keywords: List[str],
    country: str = "",
    max_results: int = 10,
) -> List[PatentHit]:
    """キーワードリストで検索（スペース結合）"""
    query = " ".join(keywords[:8])
    return search_google_patents(query, country=country, max_results=max_results)
