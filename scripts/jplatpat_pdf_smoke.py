#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""J-PlatPat PDF ダウンロード機能の手動動作確認スクリプト。

ユニットテストでは pure function だけを検証している (tests/test_jplatpat_pdf_downloader.py)。
実際の Playwright + Chromium + ネットワーク経由の DL が動くかは、このスクリプトで
1 件試して確認する。

使い方:
    # 環境チェックだけ (Playwright/Chromium が入っているか)
    python scripts/jplatpat_pdf_smoke.py --check

    # 実 DL を試す (画面が立ち上がる headed モード)
    python scripts/jplatpat_pdf_smoke.py 特開2024-123456 ./tmp_smoke

    # headless で
    python scripts/jplatpat_pdf_smoke.py 特開2024-123456 ./tmp_smoke --headless

セットアップ:
    pip install playwright
    playwright install chromium

メモ:
    - 出力 PDF は <save_dir>/<doc_id>.pdf に保存される (例: JP2024123456A.pdf)
    - 失敗時は dict の 'error' フィールドに原因が入る
    - 60s 経ってもタイムアウトする場合、J-PlatPat 側の遅延 / セレクタ変更を疑う
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

# プロジェクトルートを PYTHONPATH に通す (modules/services を import できるよう)
_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parent.parent))


def _check_environment() -> int:
    """Playwright + Chromium の有無を確認"""
    print("=== 環境チェック ===")
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        print("[OK]  playwright がインストールされています")
    except ImportError:
        print("[NG]  playwright が見つかりません: pip install playwright")
        return 1

    # Chromium バイナリの存在確認
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
                browser.close()
                print("[OK]  Playwright bundled Chromium が起動できます")
            except Exception as e:
                print(f"[警告] bundled Chromium 起動失敗: {e}")
                # フォールバック: システム Chrome
                for path in [
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                ]:
                    if Path(path).exists():
                        print(f"[OK]  System Chrome 検出: {path}")
                        break
                else:
                    print("[NG]  Chromium も System Chrome も無し: playwright install chromium")
                    return 2
    except Exception as e:
        print(f"[NG]  Playwright 初期化失敗: {e}")
        return 3

    print("\n環境 OK。実 DL を試すには:")
    print("    python scripts/jplatpat_pdf_smoke.py 特開2024-123456 ./tmp_smoke")
    return 0


def _do_download(patent_id: str, save_dir: Path, headless: bool) -> int:
    from modules.jplatpat_pdf_downloader import (
        download_jplatpat_pdf,
        normalize_jp_patent_number,
    )

    print(f"=== J-PlatPat PDF ダウンロード Smoke Test ===")
    print(f"入力: {patent_id}")
    try:
        target = normalize_jp_patent_number(patent_id)
        print(f"正規化: {target.display_number} ({target.kind}) → {target.doc_id}")
        print(f"固定 URL: {target.fixed_url}")
    except ValueError as e:
        print(f"[NG]  入力正規化失敗: {e}")
        return 10

    save_dir.mkdir(parents=True, exist_ok=True)
    print(f"保存先: {save_dir}")
    print(f"headless: {headless}")
    print()

    started = time.time()

    def on_progress(message: str) -> None:
        elapsed = time.time() - started
        print(f"  [{elapsed:5.1f}s] {message}")

    result = download_jplatpat_pdf(
        patent_id, save_dir, headless=headless, on_progress=on_progress
    )

    print()
    print("=== 結果 ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result.get("success"):
        path = Path(result["path"])
        if path.exists():
            print(f"[OK]  PDF 保存: {path} ({path.stat().st_size:,} bytes / {result.get('num_pages')} ページ)")
            return 0
        else:
            print(f"[NG]  success=True だがファイルが存在しない: {path}")
            return 11
    else:
        print(f"[NG]  ダウンロード失敗: {result.get('error')}")
        return 12


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("patent_id", nargs="?", help="特許番号 (例: 特開2024-123456 / 特許7250676 / JP7250676B2)")
    parser.add_argument("save_dir", nargs="?", default=None, help="保存先ディレクトリ")
    parser.add_argument("--check", action="store_true", help="環境チェックのみ実行 (DL しない)")
    parser.add_argument("--headless", action="store_true", help="ヘッドレスで実行 (画面なし)")
    parser.add_argument("--cleanup", action="store_true", help="完了後に save_dir を削除")
    args = parser.parse_args()

    if args.check or (not args.patent_id):
        rc = _check_environment()
        if not args.check and not args.patent_id:
            print("\n--check のみ実行しました。実 DL するには patent_id と save_dir を指定してください。")
        return rc

    if not args.save_dir:
        parser.error("save_dir を指定してください")

    save_dir = Path(args.save_dir).resolve()
    rc = _do_download(args.patent_id, save_dir, headless=args.headless)
    if args.cleanup and save_dir.exists():
        try:
            shutil.rmtree(save_dir)
            print(f"--cleanup: {save_dir} を削除しました")
        except OSError as e:
            print(f"[警告] cleanup 失敗: {e}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
