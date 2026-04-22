#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
J-PlatPat 検索クライアント (Playwright 可視ブラウザ)

J-PlatPat は API を提供しておらず Angular SPA のため、ヘッドレス自動化は
DOM 変更や CAPTCHA に弱い。本モジュールはユーザーが目視確認できる headed
モードで起動し、検索式を自動入力 → ユーザーが「検索」をクリック (or 自動)
→ 結果一覧を走査して hits を返す。

使い方:
    from modules.jplatpat_client import run_jplatpat_search
    hits = run_jplatpat_search("(化粧料+メイクアップ)*シリコーン", max_results=50)
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field as dc_field, asdict
from typing import List, Optional, Callable

logger = logging.getLogger(__name__)


# J-PlatPat 検索画面 URL
JPLATPAT_SEARCH_URL = "https://www.j-platpat.inpit.go.jp/s0100"


@dataclass
class JplatpatHit:
    """J-PlatPat 検索結果 1 件"""
    patent_id: str = ""           # 文献番号 (例: 特開2023-123456)
    title: str = ""               # 発明の名称
    applicant: str = ""           # 出願人
    publication_date: str = ""    # 公開日 (YYYY-MM-DD)
    ipc: List[str] = dc_field(default_factory=list)
    fi: List[str] = dc_field(default_factory=list)
    fterm: List[str] = dc_field(default_factory=list)
    url: str = ""                 # 詳細ページ URL
    row_text: str = ""            # 一覧テキスト (生) - パース失敗時のデバッグ用

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def dedup_key(self) -> str:
        """重複判定キー (特開2023-123456 → JP2023123456)"""
        pid = self.patent_id.strip()
        if not pid:
            return ""
        # 国コード抽出
        m = re.match(r'^(US|JP|WO|EP|KR|CN|DE|FR|GB)', pid.upper())
        prefix = m.group(1) if m else ""
        if not prefix:
            if pid.startswith(("特開", "特許", "特表", "特願", "再表", "再公表")):
                prefix = "JP"
        digits = re.sub(r'[^\d]', '', pid)
        return f"{prefix}{digits}" if digits else pid


# --- パッチ方針: 検索式の入力欄 / 検索ボタン / 結果テーブルは J-PlatPat
#     の DOM 変更で壊れやすいため、セレクタは複数フォールバックで試す。 ---

_INPUT_SELECTORS = [
    # 論理式入力モード (推奨)
    'textarea[placeholder*="検索式"]',
    'textarea[formcontrolname="searchExpression"]',
    'input[placeholder*="検索式"]',
    # 簡易検索フォールバック
    'input[placeholder*="キーワード"]',
    'textarea[formcontrolname*="keyword"]',
]

_SEARCH_BUTTON_SELECTORS = [
    'button:has-text("検索")',
    'button[mat-flat-button]:has-text("検索")',
    'button.search-button',
]

_LOGIC_TAB_SELECTORS = [
    # 論理式入力タブ
    'div[role="tab"]:has-text("論理式")',
    'mat-tab:has-text("論理式")',
    'button:has-text("論理式入力")',
]


def run_jplatpat_search(
    formula: str,
    *,
    max_results: int = 50,
    auto_click_search: bool = True,
    wait_for_user_ms: int = 0,
    on_progress: Optional[Callable[[str], None]] = None,
    persistent_profile: Optional[str] = None,
) -> List[JplatpatHit]:
    """J-PlatPat を可視ブラウザで開き、検索式を投入して結果一覧を返す。

    Args:
        formula: 論理式 (J-PlatPat 構文: AND=半角空白, OR=+, NOT=-, フィールド=/TI,/AB,/CL,/FI,/FT)
        max_results: 返す最大件数
        auto_click_search: 検索式入力後に検索ボタンを自動クリックするか。
            False の場合はユーザーが手動でクリック。
        wait_for_user_ms: 検索ボタンクリック後、結果スクレイピング前にユーザーの操作を
            待つミリ秒 (ページング等したい場合に利用)。
        on_progress: 進捗コールバック (str -> None)。UI 連携用。
        persistent_profile: 永続プロファイルのパス (ログイン状態維持用)。

    Returns:
        JplatpatHit のリスト。
    """
    def _log(msg: str):
        logger.info(msg)
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _log("playwright が未インストールです: pip install playwright && playwright install chromium")
        return []

    hits: List[JplatpatHit] = []

    with sync_playwright() as p:
        launch_kwargs = {"headless": False, "args": ["--disable-blink-features=AutomationControlled"]}
        if persistent_profile:
            browser_ctx = p.chromium.launch_persistent_context(
                user_data_dir=persistent_profile, **launch_kwargs
            )
            page = browser_ctx.new_page()
            browser = None
        else:
            browser = p.chromium.launch(**launch_kwargs)
            browser_ctx = browser.new_context(locale="ja-JP", viewport={"width": 1280, "height": 900})
            page = browser_ctx.new_page()

        try:
            _log(f"J-PlatPat を開く: {JPLATPAT_SEARCH_URL}")
            page.goto(JPLATPAT_SEARCH_URL, wait_until="domcontentloaded", timeout=30000)

            # 論理式入力タブに切替え (存在すれば)
            _log("論理式入力タブに切替え")
            for sel in _LOGIC_TAB_SELECTORS:
                try:
                    loc = page.locator(sel).first
                    if loc.count() > 0:
                        loc.click(timeout=3000)
                        page.wait_for_timeout(400)
                        break
                except Exception:
                    continue

            # 検索式を入力
            _log(f"検索式を入力: {formula[:80]}{'...' if len(formula) > 80 else ''}")
            input_el = None
            for sel in _INPUT_SELECTORS:
                try:
                    loc = page.locator(sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=2000):
                        input_el = loc
                        break
                except Exception:
                    continue

            if not input_el:
                _log("警告: 検索式入力欄を自動特定できませんでした。"
                      "手動で式を貼り付けて検索してください。")
                page.wait_for_timeout(max(wait_for_user_ms, 5000))
            else:
                input_el.click()
                input_el.fill("")
                input_el.type(formula, delay=5)
                page.wait_for_timeout(300)

                if auto_click_search:
                    for sel in _SEARCH_BUTTON_SELECTORS:
                        try:
                            btn = page.locator(sel).first
                            if btn.count() > 0 and btn.is_visible(timeout=1000):
                                _log("検索ボタンをクリック")
                                btn.click()
                                break
                        except Exception:
                            continue

            # 結果テーブル or 件数表示を待つ
            _log("検索結果を待機中")
            try:
                page.wait_for_selector(
                    'table tbody tr, .result-list, .result-row, .mat-row, .no-result, .result-count',
                    timeout=30000,
                )
            except Exception:
                _log("警告: 結果の読み込みがタイムアウトしました")

            if wait_for_user_ms > 0:
                _log(f"ユーザー操作待機 ({wait_for_user_ms}ms)")
                page.wait_for_timeout(wait_for_user_ms)

            hits = _extract_hits(page, max_results, on_progress=on_progress)
            _log(f"取得: {len(hits)}件")
        finally:
            try:
                page.wait_for_timeout(800)
                if browser:
                    browser.close()
                else:
                    browser_ctx.close()
            except Exception:
                pass

    return hits


def _extract_hits(page, max_results: int, on_progress=None) -> List[JplatpatHit]:
    """結果一覧からヒットを抽出する。複数パターンでフォールバック。"""
    hits: List[JplatpatHit] = []

    # 試行 1: table tbody tr
    rows = page.locator('table tbody tr').all()
    if not rows:
        # 試行 2: mat-row
        rows = page.locator('mat-row, .mat-row').all()
    if not rows:
        # 試行 3: div.result-row
        rows = page.locator('.result-row, .result-item').all()

    logger.debug("candidate rows: %d", len(rows))

    for row in rows[:max_results]:
        try:
            hit = _parse_row(row)
            if hit.patent_id or hit.title:
                hits.append(hit)
        except Exception as e:
            logger.debug("row parse error: %s", e)
            continue

    return hits


# 文献番号パターン
_PATENT_ID_PATTERNS = [
    re.compile(r'(特開\s*\d{4}\s*[-ー]\s*\d+)'),
    re.compile(r'(特表\s*\d{4}\s*[-ー]\s*\d+)'),
    re.compile(r'(特許\s*第?\s*\d+(?:号)?)'),
    re.compile(r'(再表\s*\d{4}\s*[-ー]\s*\d+)'),
    re.compile(r'(WO\s*\d{4}\s*[/]?\s*\d+)'),
    re.compile(r'(JP\s*\d{4}[-]?\d{6}\s*[AB]\d?)', re.IGNORECASE),
    re.compile(r'(JP\s*\d{5,8}\s*B\d?)', re.IGNORECASE),
]

# 日付パターン
_DATE_PATTERNS = [
    re.compile(r'(\d{4})[/\-.年](\d{1,2})[/\-.月](\d{1,2})'),
]


def _parse_row(row) -> JplatpatHit:
    """1 行からヒット情報を抽出。J-PlatPat のレイアウトは変動しうるため、
    テキスト全体から正規表現で抜き出す頑健な実装。"""
    text = row.inner_text(timeout=2000)
    text = text.replace("\t", " ").strip()

    hit = JplatpatHit(row_text=text)

    # 文献番号
    for pat in _PATENT_ID_PATTERNS:
        m = pat.search(text)
        if m:
            hit.patent_id = re.sub(r'\s+', '', m.group(1))
            break

    # 公開日
    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if m:
            hit.publication_date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            break

    # タイトルと出願人は行のテキストから推定 (J-PlatPat のレイアウトはセルが
    # "文献番号\n公開日\n出願人\n発明の名称\nFI" のように縦並びになりがち)
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    # タイトル: 最も長い行 (もしくは見出し的な行) を採用
    if lines:
        candidates = [
            ln for ln in lines
            if 6 <= len(ln) <= 120
            and not any(p.search(ln) for p in _PATENT_ID_PATTERNS)
            and not any(p.search(ln) for p in _DATE_PATTERNS)
        ]
        if candidates:
            hit.title = max(candidates, key=len)
        # 出願人: "株式会社" などを含む行
        for ln in lines:
            if any(k in ln for k in ("株式会社", "有限会社", "合同会社", "大学", "公団", "研究所", "Corporation", "Ltd", "Inc")):
                hit.applicant = ln
                break

    # 分類 (IPC/FI/Fterm) 抽出: 行中に "A61K 8/" のようなパターンが並ぶ
    ipc_matches = re.findall(r'[A-H]\d{2}[A-Z]\s*\d+/\d+', text)
    if ipc_matches:
        hit.ipc = list(dict.fromkeys(ipc_matches))

    # 詳細 URL: 行中の anchor
    try:
        anchor = row.locator('a').first
        if anchor.count() > 0:
            href = anchor.get_attribute("href") or ""
            if href:
                if href.startswith("/"):
                    hit.url = f"https://www.j-platpat.inpit.go.jp{href}"
                else:
                    hit.url = href
    except Exception:
        pass

    return hit


# --- CLI テスト用 ---

def _cli():
    import argparse
    parser = argparse.ArgumentParser(description="J-PlatPat 検索 CLI")
    parser.add_argument("formula", help="検索式 (J-PlatPat 論理式)")
    parser.add_argument("--max", type=int, default=20)
    parser.add_argument("--no-auto-click", action="store_true")
    parser.add_argument("--wait", type=int, default=0, help="検索後のユーザー操作待機ms")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    hits = run_jplatpat_search(
        args.formula,
        max_results=args.max,
        auto_click_search=not args.no_auto_click,
        wait_for_user_ms=args.wait,
    )
    print(f"\n=== {len(hits)} hits ===")
    for h in hits:
        print(f"- {h.patent_id:20s} {h.publication_date:12s} {h.title[:60]}")


if __name__ == "__main__":
    _cli()
