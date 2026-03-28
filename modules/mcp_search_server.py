#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MCP検索サーバー（特許調査用）
Claude CLIからGoogle Patents検索を呼び出せるようにする。

使用方法:
  claude -p --mcp-config mcp_search.json "検索クエリ"

必要な環境変数:
  SERPAPI_KEY: SerpAPI のAPIキー（https://serpapi.com/）
"""

from mcp.server.fastmcp import FastMCP
import requests
import os

mcp = FastMCP("patent-search")

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")


@mcp.tool()
def search_patents_google(query: str, num: int = 10) -> str:
    """Google Patentsで特許を検索する。

    特許調査で先行技術を探す際に使用する。
    技術用語や構成要件を英語で指定すると効果的。

    Args:
        query: 検索クエリ（例: "aerosol cosmetic composition oil foam"）
        num: 取得件数（最大10）
    """
    if not SERPAPI_KEY:
        return "エラー: SERPAPI_KEY が設定されていません"

    try:
        resp = requests.get("https://serpapi.com/search", params={
            "engine": "google_patents",
            "q": query,
            "num": min(num, 10),
            "api_key": SERPAPI_KEY,
        }, timeout=30)
    except requests.RequestException as e:
        return f"検索リクエスト失敗: {e}"

    if resp.status_code != 200:
        return f"検索失敗: status={resp.status_code}"

    data = resp.json()
    results = []
    for r in data.get("organic_results", []):
        patent_id = r.get("patent_id", "")
        title = r.get("title", "")
        assignee = r.get("assignee", "")
        priority_date = r.get("priority_date", "")
        filing_date = r.get("filing_date", "")
        snippet = r.get("snippet", "")[:300]
        pdf_url = r.get("pdf", "")
        link = r.get("patent_link", "")

        results.append(
            f"- {patent_id}: {title}\n"
            f"  出願人: {assignee}\n"
            f"  優先日: {priority_date}, 出願日: {filing_date}\n"
            f"  概要: {snippet}\n"
            f"  PDF: {pdf_url}\n"
            f"  URL: {link}"
        )
    return "\n\n".join(results) if results else "結果なし"


@mcp.tool()
def search_patents_google_scholar(query: str, num: int = 5) -> str:
    """Google Scholarで学術論文・技術文献を検索する。

    技術常識の確認や、非特許文献の先行技術調査に使用する。

    Args:
        query: 検索クエリ
        num: 取得件数（最大5）
    """
    if not SERPAPI_KEY:
        return "エラー: SERPAPI_KEY が設定されていません"

    try:
        resp = requests.get("https://serpapi.com/search", params={
            "engine": "google_scholar",
            "q": query,
            "num": min(num, 5),
            "api_key": SERPAPI_KEY,
        }, timeout=30)
    except requests.RequestException as e:
        return f"検索リクエスト失敗: {e}"

    if resp.status_code != 200:
        return f"検索失敗: status={resp.status_code}"

    data = resp.json()
    results = []
    for r in data.get("organic_results", []):
        title = r.get("title", "")
        authors = ", ".join(a.get("name", "") for a in r.get("publication_info", {}).get("authors", []))
        summary = r.get("publication_info", {}).get("summary", "")
        snippet = r.get("snippet", "")[:200]
        link = r.get("link", "")

        results.append(
            f"- {title}\n"
            f"  著者: {authors}\n"
            f"  {summary}\n"
            f"  概要: {snippet}\n"
            f"  URL: {link}"
        )
    return "\n\n".join(results) if results else "結果なし"


@mcp.tool()
def search_web(query: str, num: int = 5) -> str:
    """Google検索で一般Webを検索する。

    技術常識の確認、業界動向の調査等に使用する。

    Args:
        query: 検索クエリ
        num: 取得件数（最大5）
    """
    if not SERPAPI_KEY:
        return "エラー: SERPAPI_KEY が設定されていません"

    try:
        resp = requests.get("https://serpapi.com/search", params={
            "engine": "google",
            "q": query,
            "num": min(num, 5),
            "api_key": SERPAPI_KEY,
        }, timeout=30)
    except requests.RequestException as e:
        return f"検索リクエスト失敗: {e}"

    if resp.status_code != 200:
        return f"検索失敗: status={resp.status_code}"

    data = resp.json()
    results = []
    for r in data.get("organic_results", []):
        results.append(
            f"- {r.get('title', '')}\n"
            f"  URL: {r.get('link', '')}\n"
            f"  概要: {r.get('snippet', '')[:200]}"
        )
    return "\n\n".join(results) if results else "結果なし"


if __name__ == "__main__":
    mcp.run(transport="stdio")
