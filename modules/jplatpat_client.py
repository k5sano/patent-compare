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
    'textarea[formcontrolname*="logical"]',
    'input[placeholder*="検索式"]',
    # 選択式検索 / 特許・実用新案検索のキーワード欄
    'input[placeholder*="キーワード"]',
    'textarea[placeholder*="キーワード"]',
    'textarea[formcontrolname*="keyword" i]',
    'input[formcontrolname*="keyword" i]',
    # フォーム内の最大の textarea / input (最後の手段)
    'textarea:visible',
    'input[type="text"]:visible',
]

_SEARCH_BUTTON_SELECTORS = [
    'button:has-text("検索")',
    'button[mat-flat-button]:has-text("検索")',
    'button[mat-raised-button]:has-text("検索")',
    'button[color="primary"]:has-text("検索")',
    'button.search-button',
]

_LOGIC_TAB_SELECTORS = [
    # Angular Material (MDC) tab のラベル要素 — J-PlatPat はこれ
    '.mat-mdc-tab .mdc-tab__text-label:has-text("論理式入力")',
    '.mdc-tab__text-label:has-text("論理式入力")',
    '.mat-mdc-tab:has-text("論理式入力")',
    '.mat-tab-label-content:has-text("論理式入力")',
    '.mat-tab-label:has-text("論理式入力")',
    # role ベース
    '[role="tab"]:has-text("論理式入力")',
    '[role="tab"]:has-text("論理式")',
    # 汎用フォールバック
    'button:has-text("論理式入力")',
    'a:has-text("論理式入力")',
    'mat-tab:has-text("論理式")',
    'label:has-text("論理式")',
    # ラジオボタン型の切替え
    'mat-radio-button:has-text("論理式")',
    'input[type="radio"] + label:has-text("論理式")',
]

# 論理式入力タブに切り替わった後に現れる textarea (検証用)
_LOGIC_TEXTAREA_SELECTORS = [
    'textarea[formcontrolname="searchFormula"]',
    'textarea[formcontrolname*="ormula" i]',
    'textarea[placeholder*="論理式"]',
    'textarea[aria-label*="論理式"]',
    'textarea.logical-formula',
    # 最後の手段: 論理式タブ配下の textarea
    'mat-tab-body[aria-hidden="false"] textarea',
    '.mat-mdc-tab-body-active textarea',
]

# 初期表示されるモーダル / 同意ダイアログを閉じるセレクタ
_DISMISS_SELECTORS = [
    'button:has-text("閉じる")',
    'button:has-text("OK")',
    'button:has-text("同意")',
    'button:has-text("はい")',
    'button:has-text("続ける")',
    'button[aria-label*="close" i]',
    'button[aria-label*="閉じる"]',
    '.modal button.close',
    'mat-dialog-container button:has-text("閉じる")',
    'mat-dialog-container button:has-text("OK")',
]


def run_jplatpat_search(
    formula: str,
    *,
    max_results: int = 50,
    auto_click_search: bool = True,
    wait_for_user_ms: int = 0,
    manual_fallback_wait_ms: int = 180000,
    on_progress: Optional[Callable[[str], None]] = None,
    persistent_profile: Optional[str] = None,
) -> List[JplatpatHit]:
    """J-PlatPat を可視ブラウザで開き、検索式を投入して結果一覧を返す。

    自動入力に失敗した場合はクリップボードに式を置き、画面にバナーを表示して
    ユーザーが手動で貼付＆検索できるよう最大 manual_fallback_wait_ms ミリ秒待機する。

    Args:
        formula: 論理式 (J-PlatPat 構文: AND=半角空白 or *, OR=+, NOT=半角/)
        max_results: 返す最大件数
        auto_click_search: 検索式入力後に検索ボタンを自動クリックするか。
            False の場合はユーザーが手動でクリック。
        wait_for_user_ms: 検索ボタンクリック後、結果スクレイピング前にユーザーの操作を
            待つミリ秒 (ページング等したい場合に利用)。
        manual_fallback_wait_ms: 自動入力失敗時にユーザー操作を待つ最大ミリ秒 (既定 3 分)。
            結果テーブルが現れた時点で早期終了する。
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

        # クリップボード読み書き権限 (手動貼付フォールバック用)
        try:
            browser_ctx.grant_permissions(
                ["clipboard-read", "clipboard-write"],
                origin="https://www.j-platpat.inpit.go.jp",
            )
        except Exception:
            pass

        try:
            _log(f"J-PlatPat を開く: {JPLATPAT_SEARCH_URL}")
            page.goto(JPLATPAT_SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1500)

            # クリップボードに式をセット (ユーザーが貼りやすいように)
            try:
                page.evaluate(
                    "(f) => navigator.clipboard && navigator.clipboard.writeText(f)", formula
                )
                _log("検索式をクリップボードにコピーしました")
            except Exception:
                pass

            # 初期モーダル (同意ダイアログ等) を閉じる
            _dismiss_modals(page, _log)

            # タブグループが描画されるまで少し待つ
            try:
                page.wait_for_selector(
                    '.mat-mdc-tab-group, mat-tab-group, [role="tablist"]',
                    timeout=8000,
                )
                _log("タブグループを検出")
            except Exception:
                _log("警告: タブグループが見つかりません (画面構造が変わっている可能性)")

            # 論理式入力タブに切替え (最大3回リトライ + JSクリックフォールバック)
            tab_switched = _switch_to_logic_tab(page, _log)
            if not tab_switched:
                _log("論理式タブ切替に失敗。現在のタブで入力を試行します")

            # 検索式を入力
            _log(f"検索式を入力: {formula[:80]}{'...' if len(formula) > 80 else ''}")
            # 論理式タブが有効なら論理式 textarea を優先して探す
            input_el = _find_logic_textarea(page) if tab_switched else None
            if not input_el:
                input_el = _find_visible_input(page)

            if not input_el:
                _log("警告: 検索式入力欄を自動特定できませんでした")
                _show_manual_banner(page, formula)
                fill_ok = False
            else:
                try:
                    input_el.click()
                    page.wait_for_timeout(200)
                    # まず fill で一気に投入 (高速) → input イベント dispatch
                    try:
                        input_el.fill(formula)
                    except Exception:
                        input_el.fill("")
                        input_el.type(formula, delay=8)
                    try:
                        input_el.evaluate(
                            "el => el.dispatchEvent(new Event('input', {bubbles: true}))"
                        )
                    except Exception:
                        pass
                    page.wait_for_timeout(300)

                    # 入力値が反映されたか検証 (Angular form と双方向バインドが失敗する事例あり)
                    try:
                        actual = input_el.input_value(timeout=1500) or ""
                    except Exception:
                        actual = ""
                    if actual.strip() == "":
                        _log("入力値が空のまま。type で再投入")
                        try:
                            input_el.click()
                            page.keyboard.type(formula, delay=6)
                            page.wait_for_timeout(300)
                            actual = input_el.input_value(timeout=1000) or ""
                        except Exception:
                            pass
                    if actual.strip() == "":
                        _log("警告: 入力が反映されませんでした")
                        _show_manual_banner(page, formula)
                        fill_ok = False
                    else:
                        fill_ok = True
                        _log(f"検索式を入力しました ({len(actual)}文字)")
                except Exception as e:
                    _log(f"自動入力に失敗: {e}")
                    _show_manual_banner(page, formula)
                    fill_ok = False

            if fill_ok and auto_click_search:
                clicked = False
                for sel in _SEARCH_BUTTON_SELECTORS:
                    try:
                        btn = page.locator(sel).first
                        if btn.count() > 0 and btn.is_visible(timeout=1000):
                            _log(f"検索ボタンをクリック ({sel})")
                            btn.click()
                            clicked = True
                            break
                    except Exception:
                        continue
                if not clicked:
                    _log("検索ボタンが見つかりません。Enter キーで検索を試みます")
                    try:
                        page.keyboard.press("Enter")
                    except Exception:
                        pass

            # 検索後のモーダル (件数が多い等) を閉じる
            page.wait_for_timeout(1500)
            _dismiss_modals(page, _log)

            # 結果テーブル or 件数表示を待つ
            _log("検索結果を待機中")
            result_selector = 'table tbody tr, .result-list, .result-row, .mat-row, .no-result, .result-count, [class*="result" i]'
            timeout_ms = manual_fallback_wait_ms if not fill_ok else 45000
            try:
                page.wait_for_selector(result_selector, timeout=timeout_ms)
                _log("結果が表示されました")
            except Exception:
                _log(f"警告: 結果の読み込みが {timeout_ms}ms でタイムアウトしました")

            # 結果テーブル出現後の追加モーダル閉じ
            _dismiss_modals(page, _log)

            if wait_for_user_ms > 0:
                _log(f"ユーザー操作待機 ({wait_for_user_ms}ms)")
                page.wait_for_timeout(wait_for_user_ms)

            hits = _extract_hits(page, max_results, on_progress=on_progress)
            _log(f"取得: {len(hits)}件")
        finally:
            try:
                page.wait_for_timeout(1500)
                if browser:
                    browser.close()
                else:
                    browser_ctx.close()
            except Exception:
                pass

    return hits


def _dismiss_modals(page, log_fn) -> None:
    """出現中のモーダルダイアログを閉じる (複数回試行)"""
    for _ in range(3):
        closed = False
        for sel in _DISMISS_SELECTORS:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=300):
                    loc.click(timeout=1000)
                    log_fn(f"モーダルを閉じました: {sel}")
                    page.wait_for_timeout(400)
                    closed = True
                    break
            except Exception:
                continue
        if not closed:
            break


def _switch_to_logic_tab(page, log_fn) -> bool:
    """論理式入力タブに切り替える。切替成功を textarea 出現で検証。"""
    def _logic_textarea_visible() -> bool:
        for sel in _LOGIC_TEXTAREA_SELECTORS:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=500):
                    return True
            except Exception:
                continue
        return False

    # 既に論理式タブが開いている可能性をまず確認
    if _logic_textarea_visible():
        log_fn("論理式 textarea が既に表示されています")
        return True

    for attempt in range(3):
        clicked = False
        for sel in _LOGIC_TAB_SELECTORS:
            try:
                loc = page.locator(sel).first
                cnt = loc.count()
                if cnt == 0:
                    continue
                # force click: 他要素にオーバーラップされていても押す
                try:
                    loc.scroll_into_view_if_needed(timeout=1000)
                except Exception:
                    pass
                try:
                    loc.click(timeout=2000, force=True)
                    log_fn(f"タブ切替 試行{attempt+1}: {sel}")
                    clicked = True
                    break
                except Exception:
                    # JS クリックにフォールバック
                    try:
                        loc.evaluate("el => el.click()")
                        log_fn(f"タブ切替 JS 試行{attempt+1}: {sel}")
                        clicked = True
                        break
                    except Exception:
                        continue
            except Exception:
                continue

        page.wait_for_timeout(900)
        if _logic_textarea_visible():
            log_fn(f"論理式タブへの切替を検証 (試行{attempt+1})")
            return True
        if not clicked:
            # クリック候補が 1 つも見つからなかった場合は早期終了
            break

    return False


def _find_logic_textarea(page):
    """論理式入力タブの textarea を返す。見つからなければ None。"""
    for sel in _LOGIC_TEXTAREA_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                try:
                    if loc.is_visible(timeout=1500):
                        return loc
                except Exception:
                    continue
        except Exception:
            continue
    return None


def _find_visible_input(page):
    """候補セレクタから可視の入力欄を返す。見つからなければ None。"""
    for sel in _INPUT_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                try:
                    if loc.is_visible(timeout=1500):
                        return loc
                except Exception:
                    continue
        except Exception:
            continue
    return None


def _show_manual_banner(page, formula: str) -> None:
    """画面上部にクリップボードから貼付を促すバナーを表示。"""
    escaped = (formula or "").replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    js = """
    (f) => {
      const id = '__pc_banner__';
      let el = document.getElementById(id);
      if (el) el.remove();
      el = document.createElement('div');
      el.id = id;
      el.style.cssText = [
        'position:fixed', 'top:10px', 'left:50%', 'transform:translateX(-50%)',
        'background:#b91c1c', 'color:#fff', 'padding:14px 22px',
        'border-radius:10px', 'z-index:2147483647', 'font-size:15px',
        'box-shadow:0 8px 24px rgba(0,0,0,.4)', 'max-width:80vw',
        'font-family:sans-serif', 'line-height:1.5'
      ].join(';');
      el.innerHTML =
        '<div style=\"font-weight:700; margin-bottom:4px;\">自動入力に失敗しました</div>' +
        '<div>検索式はクリップボードにコピー済みです。</div>' +
        '<div>検索フォームをクリック → <b>Ctrl + V</b> で貼付 → <b>検索</b> ボタンをクリックしてください。</div>' +
        '<div style=\"margin-top:6px; font-family:monospace; background:rgba(0,0,0,.25); padding:4px 8px; border-radius:4px; font-size:12px; max-height:80px; overflow:auto; word-break:break-all;\">' + f.replace(/</g, '&lt;') + '</div>';
      document.body.appendChild(el);
      // 20秒後に自動で半透明化
      setTimeout(() => { try { el.style.opacity = '0.35'; } catch(e){} }, 20000);
    }
    """
    try:
        page.evaluate(js, formula)
    except Exception:
        pass


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


# 文献番号パターン (公開番号 = 取得対象としたい識別子)
_PATENT_ID_PATTERNS = [
    re.compile(r'(特開\s*\d{4}\s*[-ー]\s*\d+)'),
    re.compile(r'(特表\s*\d{4}\s*[-ー]\s*\d+)'),
    re.compile(r'(特許\s*第?\s*\d+(?:号)?)'),
    # 再公表/再表: J-PlatPat 一覧では `再表2012/029514` のスラッシュ区切りで出るため
    # `[-ー/／]` を許容する
    re.compile(r'(再(?:公)?表\s*\d{4}\s*[-ー/／]\s*\d+)'),
    re.compile(r'(WO\s*\d{4}\s*[/／-]?\s*\d+)'),
    re.compile(r'(JP\s*\d{4}[-]?\d{6}\s*[AB]\d?)', re.IGNORECASE),
    re.compile(r'(JP\s*\d{5,8}\s*B\d?)', re.IGNORECASE),
]

# タイトル候補から除外したい識別子パターン（出願番号 特願 など）
_NON_TITLE_PATTERNS = list(_PATENT_ID_PATTERNS) + [
    re.compile(r'特願\s*\d{4}\s*[-ー]\s*\d+'),
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
        _ipc_re = re.compile(r'^[A-H]\d{2}[A-Z]\s*\d+/\d+\s*$')
        _applicant_kw = ("株式会社", "有限会社", "合同会社", "大学", "公団",
                         "研究所", "Corporation", "Ltd", "Inc")
        candidates = [
            ln for ln in lines
            if 4 <= len(ln) <= 120
            and not any(p.search(ln) for p in _NON_TITLE_PATTERNS)
            and not any(p.search(ln) for p in _DATE_PATTERNS)
            and not _ipc_re.match(ln)
            and not any(k in ln for k in _applicant_kw)
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

def build_jplatpat_fixed_url(patent_id: str) -> str:
    """patent_id から J-PlatPat 固定 URL を組み立てる。対応外なら空文字。"""
    pid = (patent_id or "").strip()
    if not pid:
        return ""
    # 全角→半角の最低限の正規化
    pid = pid.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    pid = pid.replace("－", "-").replace("ー", "-").replace("／", "/")
    pid = re.sub(r'(号公報|号|公報)', '', pid).strip()
    B = "https://www.j-platpat.inpit.go.jp/c1801/PU"

    m = re.search(r'特開\s*(\d{4})\s*-\s*(\d+)', pid)
    if m:
        return f"{B}/JP-{m.group(1)}-{m.group(2).zfill(6)}/11/ja"
    m = re.search(r'特願\s*(\d{4})\s*-\s*(\d+)', pid)
    if m:
        return f"{B}/JP-{m.group(1)}-{m.group(2).zfill(6)}/10/ja"
    m = re.search(r'特許(?:第)?\s*(\d+)', pid)
    if m:
        return f"{B}/JP-{m.group(1)}/15/ja"
    m = re.search(r'JP\s*(\d{4})\s*[-]?\s*(\d{3,6})\s*A', pid, re.I)
    if m:
        return f"{B}/JP-{m.group(1)}-{m.group(2).zfill(6)}/11/ja"
    m = re.search(r'JP\s*(\d{5,8})\s*B\d?', pid, re.I)
    if m:
        return f"{B}/JP-{m.group(1)}/15/ja"
    m = re.search(r'WO\s*(\d{4})\s*[/-]?\s*(\d+)', pid, re.I)
    if m:
        return f"{B}/WO-A-{m.group(1)}-{m.group(2).zfill(6)}/50/ja"
    m = re.search(r'US\s*(\d{4})\s*[/-]?\s*(\d+)\s*A\d?', pid, re.I)
    if m:
        return f"{B}/US-{m.group(1)}{m.group(2)}/50/ja"
    m = re.search(r'US\s*([\d,]+)\s*B\d?', pid, re.I)
    if m:
        return f"{B}/US-{m.group(1).replace(',', '')}/50/ja"
    m = re.search(r'EP\s*(\d+)', pid, re.I)
    if m:
        return f"{B}/EP-{m.group(1)}/50/ja"
    m = re.search(r'CN\s*(\d+)', pid, re.I)
    if m:
        return f"{B}/CN-{m.group(1)}/50/ja"
    m = re.search(r'KR\s*(\d{4})\s*[-]?\s*(\d+)', pid, re.I)
    if m:
        return f"{B}/KR-{m.group(1)}-{m.group(2).zfill(7)}/50/ja"
    return ""


def fetch_jplatpat_full_text(patent_id: str, *, language: str = "ja",
                              timeout_ms: int = 45000) -> dict:
    """J-PlatPat の固定 URL から公報全文（claims+description）を取得する。

    戦略:
      1. 固定 URL `c1801/PU/...` にアクセス（landing/メタ情報ページ）
      2. ページ中の文献番号リンクをクリック → 詳細ページ p0200 が新タブで開く
      3. アコーディオン（「開く」トグル）を全て JS で押下して全セクションを展開
      4. document.body.textContent を取得（inner_text は折りたたみで欠ける）
      5. 「要約 / 請求の範囲 / 発明の詳細な説明」を切り出して返す

    Returns:
        {"patent_id", "url", "title", "abstract", "claims": [text], "description": "...",
         "fetched_at": iso, "source": "jplatpat", "raw": "..."}
    """
    import datetime
    fixed_url = build_jplatpat_fixed_url(patent_id)
    result = {
        "patent_id": patent_id,
        "url": fixed_url,
        "title": "",
        "abstract": "",
        "claims": [],
        "description": "",
        "raw": "",
        "source": "jplatpat",
        "fetched_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    if not fixed_url:
        result["error"] = f"J-PlatPat 固定 URL を組み立てられない番号です: {patent_id}"
        return result

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        result["error"] = "playwright 未インストール"
        return result

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage", "--disable-gpu",
                      "--disable-blink-features=AutomationControlled"],
            )
            try:
                ctx = browser.new_context(locale="ja-JP",
                                          viewport={"width": 1024, "height": 900})
                # 画像/フォント/動画をブロック (CSS は J-PlatPat の表示判定に使われるので残す)
                def _block(route):
                    rt = route.request.resource_type
                    if rt in ("image", "font", "media"):
                        route.abort()
                    else:
                        route.continue_()
                try:
                    ctx.route("**/*", _block)
                except Exception:
                    pass
                page = ctx.new_page()
                page.goto(fixed_url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(3500)
                # 公開番号セルをクリック → 詳細ページ (p0200) が新タブで開く
                # patent_id 文字列のバリエーションをいくつか試す
                opened = None
                # 取得したい文字列パターン: 特開YYYY-NNNNNN / 特許第NNNN号 / WO-A-... など
                # 入力 patent_id から複数の表記を作る
                cands = [patent_id, patent_id.replace(" ", "")]
                m = re.search(r'(\d{4})\s*[-]?\s*(\d+)', patent_id)
                if m:
                    cands.append(f"{m.group(1)}-{m.group(2)}")
                m_jp = re.search(r'JP\s*(\d{5,8})\s*B', patent_id, re.I)
                if m_jp:
                    cands.append(f"特許第{m_jp.group(1)}号")
                    cands.append(f"特許{m_jp.group(1)}")
                m_jpa = re.search(r'JP\s*(\d{4})\s*[-]?\s*(\d{3,6})\s*A', patent_id, re.I)
                if m_jpa:
                    cands.append(f"特開{m_jpa.group(1)}-{m_jpa.group(2)}")
                # 重複除去 (順序保持)
                seen = set()
                cands = [c for c in cands if c and not (c in seen or seen.add(c))]

                for label in cands:
                    try:
                        loc = page.locator(f'text={label}').first
                        if loc.count() > 0 and loc.is_visible(timeout=1500):
                            with ctx.expect_page(timeout=12000) as new_info:
                                loc.click()
                            opened = new_info.value
                            break
                    except Exception:
                        continue
                if not opened:
                    result["error"] = f"詳細ページへの遷移に失敗 (試行: {cands})"
                    return result

                opened.wait_for_load_state("domcontentloaded", timeout=20000)
                opened.wait_for_timeout(3000)

                # 全アコーディオンを開く（最大 8 ラウンド、変化が無くなるまで）
                for _ in range(8):
                    n = opened.evaluate("""() => {
                      let n = 0;
                      document.querySelectorAll('*').forEach(el => {
                        if (el.children.length === 0 && el.textContent && el.textContent.trim() === '開く') {
                          let cur = el;
                          for (let i = 0; i < 8 && cur; i++) {
                            if (cur.tagName === 'BUTTON' || cur.tagName === 'A' ||
                                cur.getAttribute('role') === 'button' || cur.onclick) {
                              cur.click(); n++; return;
                            }
                            cur = cur.parentElement;
                          }
                          if (el.parentElement) { el.parentElement.click(); n++; }
                        }
                      });
                      return n;
                    }""")
                    if not n:
                        break
                    opened.wait_for_timeout(800)
                opened.wait_for_timeout(800)

                full_text = opened.evaluate("() => document.body.textContent") or ""
                full_text = re.sub(r'[ \t　]+', ' ', full_text)
                full_text = re.sub(r'\n{3,}', '\n\n', full_text).strip()
                result["raw"] = full_text

                # タイトル (次の【...】 or "(NN)" バイブリオ・マーカーまで)
                m = re.search(r'【発明の名称】\s*([^【\n]{1,200})', full_text)
                if m:
                    title = m.group(1).strip()
                    # "(51)【国際特許分類】" 等が続くことがあるので括弧マーカーで切る
                    title = re.split(r'\s*\(\d{2}\)', title, maxsplit=1)[0].strip()
                    result["title"] = title[:300]

                # 要約
                m = re.search(r'要約.*?\(57\)\s*【要約】\s*(.*?)(?=【選択図】|請求の範囲|発明の詳細な説明|$)',
                              full_text, re.DOTALL)
                if m:
                    result["abstract"] = m.group(1).strip()[:3000]

                # 請求の範囲（個別請求項に分割）
                m = re.search(r'請求の範囲\s*閉じる\s*(.*?)(?=発明の詳細な説明|図面|$)',
                              full_text, re.DOTALL)
                if m:
                    cl_block = m.group(1)
                    # 各 【請求項N】内容 を抽出 (preamble の (57)【特許請求の範囲】等は捨てる)
                    pairs = re.findall(r'【請求項\s*(\d+)】\s*(.+?)(?=【請求項\s*\d+】|$)',
                                       cl_block, re.DOTALL)
                    for _num, body in pairs:
                        body = body.strip()
                        if body:
                            result["claims"].append(body)

                # 発明の詳細な説明
                m = re.search(r'発明の詳細な説明\s*閉じる\s*(.*?)(?=図面|要約\s*閉じる|$)',
                              full_text, re.DOTALL)
                if m:
                    result["description"] = m.group(1).strip()
                else:
                    # フォールバック: claims以降の長い塊
                    after_claims = full_text.find("発明の詳細な説明")
                    if after_claims > 0:
                        result["description"] = full_text[after_claims:].strip()
            finally:
                browser.close()
    except Exception as e:
        logger.warning("fetch_jplatpat_full_text error (%s): %s", patent_id, e)
        result["error"] = f"{type(e).__name__}: {e}"

    return result


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
