#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MCP検索サーバー（特許調査用）
Claude CLIからGoogle Patents検索を呼び出せるようにする。

使用方法:
  claude -p --mcp-config mcp_search.json "検索クエリ"

Playwright ヘッドレスブラウザで Google Patents を直接検索する。
SerpAPIキーがあれば Google Scholar・Web検索も利用可能。
"""

import os
import sys
from pathlib import Path

# プロジェクトルートをsys.pathに追加（モジュールインポート用）
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("patent-search")

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")


@mcp.tool()
def search_patents_google(query: str, num: int = 10) -> str:
    """Google Patentsで特許を検索する。

    特許調査で先行技術を探す際に使用する。
    技術用語や構成要件を日本語・英語で指定可能。

    Args:
        query: 検索クエリ（例: "エアゾール 化粧料 油状泡沫"）
        num: 取得件数（最大10）
    """
    from modules.google_patents_scraper import search_google_patents

    try:
        hits = search_google_patents(query, max_results=min(num, 10))
    except Exception as e:
        return f"検索エラー: {e}"

    if not hits:
        return "結果なし"

    results = []
    for h in hits:
        results.append(
            f"- {h.patent_id}: {h.title}\n"
            f"  出願人: {h.assignee}\n"
            f"  優先日: {h.priority_date}\n"
            f"  URL: {h.url}"
        )
    return "\n\n".join(results)


@mcp.tool()
def search_patents_google_jp(query: str, num: int = 5) -> str:
    """Google Patentsで日本特許に限定して検索する。

    日本特許に絞った先行技術調査に使用する。

    Args:
        query: 検索クエリ（例: "エアゾール 化粧料"）
        num: 取得件数（最大10）
    """
    from modules.google_patents_scraper import search_google_patents

    try:
        hits = search_google_patents(query, country="JP", max_results=min(num, 10))
    except Exception as e:
        return f"検索エラー: {e}"

    if not hits:
        return "結果なし"

    results = []
    for h in hits:
        results.append(
            f"- {h.patent_id}: {h.title}\n"
            f"  出願人: {h.assignee}\n"
            f"  優先日: {h.priority_date}\n"
            f"  URL: {h.url}"
        )
    return "\n\n".join(results)


@mcp.tool()
def search_patents_google_scholar(query: str, num: int = 5) -> str:
    """Google Scholarで学術論文・技術文献を検索する。

    技術常識の確認や、非特許文献の先行技術調査に使用する。
    ※SerpAPIキーが必要。

    Args:
        query: 検索クエリ
        num: 取得件数（最大5）
    """
    if not SERPAPI_KEY:
        return "エラー: SERPAPI_KEY が設定されていません（Google Scholar検索は利用不可）"

    import requests

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


if __name__ == "__main__":
    mcp.run(transport="stdio")
