#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""J-PlatPat Playwright セッションマネージャ。

Flask の各リクエストから呼び出される半自動化 API を、専用ワーカースレッドで
Playwright (sync_api) を動かすことで実現する。

フロー:
  1. open()          : ブラウザを起動し J-PlatPat を開く (ユーザーが論理式タブへ手動で切替)
  2. fill(formula)   : 現在表示中の論理式 textarea に式を入力
  3. scrape(max)     : 検索結果テーブルから hits を抽出
  4. close()         : セッション終了
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


JPLATPAT_URL = "https://www.j-platpat.inpit.go.jp/s0100"


# ---- 入力欄／結果テーブル候補セレクタ (jplatpat_client.py と同じものを再利用可能) ----
_LOGIC_TEXTAREA_SELECTORS = [
    'textarea[formcontrolname="searchFormula"]',
    'textarea[formcontrolname*="ormula" i]',
    'textarea[placeholder*="論理式"]',
    'textarea[aria-label*="論理式"]',
    'textarea.logical-formula',
    'mat-tab-body[aria-hidden="false"] textarea',
    '.mat-mdc-tab-body-active textarea',
    # フォールバック
    'textarea',
]

_RESULT_ROW_SELECTORS = [
    'table tbody tr',
    'mat-row, .mat-row',
    '.result-row, .result-item',
]

_DISMISS_SELECTORS = [
    'button:has-text("閉じる")',
    'button:has-text("OK")',
    'button:has-text("同意")',
    'button:has-text("はい")',
    'button:has-text("続ける")',
    'button[aria-label*="close" i]',
    'button[aria-label*="閉じる"]',
    'mat-dialog-container button:has-text("閉じる")',
    'mat-dialog-container button:has-text("OK")',
]


class _WorkerCommand:
    """ワーカスレッドに渡すコマンド。"""
    __slots__ = ("op", "kwargs", "result_q")

    def __init__(self, op: str, kwargs: Dict[str, Any]):
        self.op = op
        self.kwargs = kwargs
        self.result_q: queue.Queue = queue.Queue(maxsize=1)


class JPlatpatSession:
    """Playwright の browser/page を専用スレッドで保持する。"""

    def __init__(self):
        self._cmd_q: queue.Queue[_WorkerCommand] = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._started_at: float = 0.0
        self._ready_event: threading.Event = threading.Event()
        self._launch_error: Optional[str] = None

    # ---- 外部 API ----
    def is_alive(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def open(self, *, url: str = JPLATPAT_URL, timeout: int = 40) -> Dict[str, Any]:
        if not self.is_alive():
            self._start_worker()
        # ブラウザ起動完了を待つ (最大 35 秒)
        if not self._ready_event.wait(timeout=min(timeout, 35)):
            return {"ok": False, "error": "ブラウザ起動がタイムアウトしました (35s)"}
        if self._launch_error:
            return {"ok": False, "error": self._launch_error}
        return self._submit("goto", {"url": url}, timeout=timeout)

    def fill(self, formula: str, *, timeout: int = 20) -> Dict[str, Any]:
        if not self.is_alive():
            return {"ok": False, "error": "セッションが開かれていません。先に J-PlatPat を開いてください。"}
        return self._submit("fill_formula", {"formula": formula}, timeout=timeout)

    def scrape(self, *, max_results: int = 50, timeout: int = 30) -> Dict[str, Any]:
        if not self.is_alive():
            return {"ok": False, "error": "セッションが開かれていません。先に J-PlatPat を開いてください。"}
        return self._submit("scrape", {"max_results": max_results}, timeout=timeout)

    def status(self) -> Dict[str, Any]:
        if not self.is_alive():
            return {"alive": False}
        r = self._submit("status", {}, timeout=5)
        r["alive"] = True
        return r

    def close(self) -> Dict[str, Any]:
        if not self.is_alive():
            return {"ok": True, "note": "not running"}
        try:
            self._submit("stop", {}, timeout=5)
        except Exception:
            pass
        self._running = False
        return {"ok": True}

    # ---- 内部 ----
    def _start_worker(self):
        self._running = True
        self._ready_event = threading.Event()
        self._launch_error = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="jplatpat-worker")
        self._thread.start()
        self._started_at = time.time()

    def _submit(self, op: str, kwargs: Dict[str, Any], *, timeout: int) -> Dict[str, Any]:
        cmd = _WorkerCommand(op, kwargs)
        self._cmd_q.put(cmd)
        try:
            return cmd.result_q.get(timeout=timeout)
        except queue.Empty:
            return {"ok": False, "error": f"タイムアウト ({timeout}s) op={op}"}

    def _run(self):
        """ワーカスレッド本体: Playwright セッションの起動とコマンド処理。"""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error("playwright が未インストール")
            self._running = False
            return

        self._p = None
        self._browser = None
        self._ctx = None
        self._page = None

        try:
            try:
                self._p = sync_playwright().start()
            except Exception as e:
                logger.exception("sync_playwright start failed")
                self._launch_error = f"Playwright 初期化失敗: {e}"
                self._ready_event.set()
                return

            if not self._launch_browser_and_page():
                self._ready_event.set()
                return

            # 起動完了を通知
            self._ready_event.set()

            while self._running:
                try:
                    cmd = self._cmd_q.get(timeout=1.0)
                except queue.Empty:
                    # 定期的にブラウザ生存確認
                    if not self._is_browser_alive():
                        logger.warning("browser no longer connected — worker stopping")
                        break
                    continue
                if cmd.op == "stop":
                    cmd.result_q.put({"ok": True})
                    break
                try:
                    # コマンド実行前にブラウザ/ページ生存チェック、必要なら再生成
                    if not self._ensure_page_alive():
                        cmd.result_q.put({
                            "ok": False,
                            "error": "ブラウザが閉じられました。再度『① J-PlatPat を開く』を押してください。",
                        })
                        break
                    result = self._handle(self._page, cmd.op, cmd.kwargs)
                    cmd.result_q.put(result)
                except Exception as e:
                    logger.exception("jplatpat op error: %s", cmd.op)
                    cmd.result_q.put({"ok": False, "error": f"{type(e).__name__}: {e}"})
        finally:
            self._cleanup()
            self._running = False
            logger.info("jplatpat worker stopped")

    def _launch_browser_and_page(self) -> bool:
        """browser / context / page を新規起動。成功すれば True。"""
        try:
            self._browser = self._p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as e:
            logger.exception("chromium launch failed")
            self._launch_error = (
                f"ブラウザ起動失敗: {e}. 'playwright install chromium' を実行してください。"
            )
            return False

        if not self._browser.is_connected():
            self._launch_error = (
                "ブラウザ起動直後に切断されました。'playwright install chromium' を実行し、再試行してください。"
            )
            return False

        try:
            self._ctx = self._browser.new_context(
                locale="ja-JP",
                viewport={"width": 1280, "height": 900},
            )
            try:
                self._ctx.grant_permissions(
                    ["clipboard-read", "clipboard-write"],
                    origin="https://www.j-platpat.inpit.go.jp",
                )
            except Exception:
                pass
            self._page = self._ctx.new_page()
            logger.info("jplatpat browser ready: page=%s", self._page)
            return True
        except Exception as e:
            logger.exception("context/page creation failed")
            self._launch_error = f"ページ生成失敗: {e}"
            return False

    def _is_browser_alive(self) -> bool:
        try:
            if self._browser is None:
                return False
            return self._browser.is_connected()
        except Exception:
            return False

    def _is_page_alive(self) -> bool:
        try:
            if self._page is None:
                return False
            if not self._is_browser_alive():
                return False
            return not self._page.is_closed()
        except Exception:
            return False

    def _ensure_page_alive(self) -> bool:
        """ページが閉じていたら同じ context で新しいタブを開いて差し替える。"""
        if self._is_page_alive():
            return True
        # context 自体がまだ生きていれば、新しい page を作れる
        try:
            if self._ctx is not None and self._is_browser_alive():
                self._page = self._ctx.new_page()
                logger.info("page recreated")
                return True
        except Exception:
            pass
        return False

    def _cleanup(self) -> None:
        try:
            if self._ctx:
                self._ctx.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._p:
                self._p.stop()
        except Exception:
            pass

    # ---- コマンドディスパッチ ----
    def _handle(self, page, op: str, kw: Dict[str, Any]) -> Dict[str, Any]:
        if op == "goto":
            return self._op_goto(page, kw.get("url") or JPLATPAT_URL)
        if op == "fill_formula":
            return self._op_fill(page, kw.get("formula") or "")
        if op == "scrape":
            return self._op_scrape(page, int(kw.get("max_results") or 50))
        if op == "status":
            return {"ok": True, "url": (page.url if page else "")}
        return {"ok": False, "error": f"unknown op: {op}"}

    def _op_goto(self, page, url: str) -> Dict[str, Any]:
        logger.info("jplatpat goto: %s", url)
        last_err: Optional[Exception] = None
        for attempt in range(3):
            # 閉じられていたら再生成
            if not self._is_page_alive():
                if not self._ensure_page_alive():
                    return {"ok": False, "error": "ブラウザが接続されていません。"}
                page = self._page
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1000)
                _dismiss_modals(page)
                return {"ok": True, "url": page.url}
            except Exception as e:
                last_err = e
                msg = str(e)
                logger.warning("goto attempt %d failed: %s", attempt + 1, msg)
                if "Target" in msg and ("closed" in msg.lower() or "Closed" in msg):
                    # ページが閉じられたので新しいページを作る
                    try:
                        if self._ctx is not None and self._is_browser_alive():
                            self._page = self._ctx.new_page()
                            page = self._page
                            continue
                    except Exception:
                        pass
                # それ以外は少し待って再試行
                try:
                    page.wait_for_timeout(800)
                except Exception:
                    pass
        return {"ok": False, "error": f"J-PlatPat への遷移に失敗しました: {last_err}"}

    def _op_fill(self, page, formula: str) -> Dict[str, Any]:
        if not formula:
            return {"ok": False, "error": "formula が空です"}
        logger.info("jplatpat fill: %s", formula[:60])

        # まずクリップボードへ (失敗時の手動貼付用)
        try:
            page.evaluate(
                "(f) => navigator.clipboard && navigator.clipboard.writeText(f)",
                formula,
            )
        except Exception:
            pass

        _dismiss_modals(page)

        # 論理式 textarea を探す
        ta = _find_logic_textarea(page)
        if not ta:
            return {
                "ok": False,
                "error": "論理式 textarea が見つかりません。J-PlatPat で「論理式入力」タブを開いてから再試行してください。",
            }

        try:
            ta.click()
            page.wait_for_timeout(150)
            try:
                ta.fill(formula)
            except Exception:
                ta.fill("")
                ta.type(formula, delay=8)
            try:
                ta.evaluate("el => el.dispatchEvent(new Event('input', {bubbles: true}))")
            except Exception:
                pass
            page.wait_for_timeout(200)
            actual = ""
            try:
                actual = ta.input_value(timeout=1500) or ""
            except Exception:
                pass
            if not actual.strip():
                # キーボードで再投入
                try:
                    ta.click()
                    page.keyboard.type(formula, delay=6)
                    page.wait_for_timeout(200)
                    actual = ta.input_value(timeout=1000) or ""
                except Exception:
                    pass
            if not actual.strip():
                return {"ok": False, "error": "入力は試みましたが値が反映されませんでした (クリップボードに式あり、Ctrl+V で手動貼付してください)"}
            return {"ok": True, "filled_chars": len(actual)}
        except Exception as e:
            return {"ok": False, "error": f"入力エラー: {e}"}

    def _op_scrape(self, page, max_results: int) -> Dict[str, Any]:
        logger.info("jplatpat scrape (max=%d)", max_results)
        _dismiss_modals(page)

        # 結果行が現れるまで少し待つ
        rows = []
        for sel in _RESULT_ROW_SELECTORS:
            try:
                page.wait_for_selector(sel, timeout=2000)
                rows = page.locator(sel).all()
                if rows:
                    break
            except Exception:
                continue

        if not rows:
            return {"ok": False, "error": "結果テーブルが見つかりませんでした。J-PlatPat で検索を実行してから「結果を取り込む」を押してください。", "hits": []}

        from modules.jplatpat_client import _parse_row  # reuse

        hits: List[Dict[str, Any]] = []
        for row in rows[:max_results]:
            try:
                hit = _parse_row(row)
                if hit.patent_id or hit.title:
                    hits.append(hit.to_dict())
            except Exception:
                continue
        return {"ok": True, "hits": hits, "count": len(hits)}


# ---- ユーティリティ ----
def _dismiss_modals(page) -> None:
    for _ in range(2):
        closed = False
        for sel in _DISMISS_SELECTORS:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=250):
                    loc.click(timeout=800)
                    page.wait_for_timeout(300)
                    closed = True
                    break
            except Exception:
                continue
        if not closed:
            break


def _find_logic_textarea(page):
    for sel in _LOGIC_TEXTAREA_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                try:
                    if loc.is_visible(timeout=800):
                        return loc
                except Exception:
                    continue
        except Exception:
            continue
    return None


# ---- シングルトン ----
_SESSION: Optional[JPlatpatSession] = None
_SESSION_LOCK = threading.Lock()


def get_session() -> JPlatpatSession:
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            _SESSION = JPlatpatSession()
        return _SESSION


def reset_session() -> None:
    """セッションを完全破棄し、次回 get_session() で新規インスタンスを作る。"""
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is not None:
            try:
                _SESSION.close()
                # 完全終了を少し待つ
                if _SESSION._thread is not None:
                    _SESSION._thread.join(timeout=3)
            except Exception:
                pass
        _SESSION = None
