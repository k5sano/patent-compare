#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Web 自動化の偵察フェーズ用スクリプト (CLAUDE.md §第1優先)。

新規サイト / 新規取得対象を扱うときに、Playwright で対象サイトを開いて
ユーザが手で操作し、その間の **全 HTTP 通信** を NDJSON で記録する。
記録結果から内部 API エンドポイント候補を抽出して短いサマリを出す。

設計方針:
- いきなり「自動操作」までは書かない。ブラウザを開いて待機するだけ。
  ユーザが PDF 表示・検索実行などの操作を実行 → ウィンドウを閉じる /
  Ctrl-C で停止 → サマリを出す。
- httpx/requests の選定は不要。Playwright の page.on('request')/
  page.on('response') だけで通信を記録。
- 記録は NDJSON 1 行 1 トラフィック。後段の解析スクリプトを足しやすい形。

使い方:
    # 1. 偵察セッション開始 (画面が立ち上がる)
    python tools/recon.py https://www.j-platpat.inpit.go.jp/p0000 \
        --out docs/recon/jplatpat_p0000_20260506.ndjson

    # 2. ブラウザで対象操作 (例: 番号入力 → 検索 → PDF 表示)

    # 3. ブラウザを閉じる (または Ctrl-C)
    #    → 自動でサマリが出力される

    # サマリだけ後から見たい場合
    python tools/recon.py --summarize docs/recon/jplatpat_p0000_20260506.ndjson

サマリで見るべきもの:
    - 目的のデータ (PDF / JSON) を返している URL のパターン
    - そのリクエストに必要な Cookie / Authorization / Referer / CSRF
    - POST ボディの形 (JSON フィールド構造)
    - 署名付き URL や blob:、postMessage 経由のデータ受け渡しの有無

セットアップ:
    pip install playwright
    playwright install chromium
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


def _open_recording_session(start_url: str, out_path: Path, *, headless: bool = False) -> int:
    """Playwright を起動し、start_url を開いて全 HTTP 通信を NDJSON 出力。

    ユーザがブラウザを閉じると終了。Ctrl-C でも終了。終了後にサマリを出す。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[NG] playwright が未インストール: pip install playwright && playwright install chromium",
              file=sys.stderr)
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fp = out_path.open("w", encoding="utf-8")
    request_count = 0
    start_ts = time.time()

    def _on_request(request):
        nonlocal request_count
        try:
            entry = {
                "ts": time.time() - start_ts,
                "kind": "request",
                "method": request.method,
                "url": request.url,
                "resource_type": request.resource_type,
                "headers": dict(request.headers),
                "post_data": request.post_data,
            }
            fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
            fp.flush()
            request_count += 1
        except Exception as e:
            print(f"[警告] request 記録失敗: {e}", file=sys.stderr)

    def _on_response(response):
        try:
            req = response.request
            entry = {
                "ts": time.time() - start_ts,
                "kind": "response",
                "method": req.method,
                "url": response.url,
                "status": response.status,
                "headers": dict(response.headers),
                "resource_type": req.resource_type,
            }
            # text/json っぽい body はサンプリングする (重い PDF/画像は除外)
            ct = (response.headers.get("content-type") or "").lower()
            if any(t in ct for t in ("json", "javascript", "xml", "text")):
                try:
                    body = response.text()
                    if body and len(body) < 8000:
                        entry["body_sample"] = body
                    else:
                        entry["body_sample_truncated"] = True
                        entry["body_size"] = len(body or "")
                except Exception:
                    pass
            fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
            fp.flush()
        except Exception as e:
            print(f"[警告] response 記録失敗: {e}", file=sys.stderr)

    print(f"=== 偵察セッション開始 ===")
    print(f"開始 URL: {start_url}")
    print(f"出力: {out_path}")
    print(f"ブラウザを閉じると自動終了します (または Ctrl-C)")
    print(f"---")

    rc = 0
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=headless)
        except Exception as e:
            print(f"[NG] ブラウザ起動失敗: {e}", file=sys.stderr)
            fp.close()
            return 2
        context = browser.new_context(locale="ja-JP", viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.on("request", _on_request)
        page.on("response", _on_response)

        try:
            page.goto(start_url, wait_until="domcontentloaded", timeout=30000)
            print("ページがロードされました。対象操作を実行してください。")
            # close を待つ
            page.wait_for_event("close", timeout=0)
        except KeyboardInterrupt:
            print("\nCtrl-C で停止")
        except Exception as e:
            # ブラウザを閉じた等の正常停止と、ページ NG エラーを区別したい
            msg = str(e).lower()
            if "target closed" in msg or "browser closed" in msg or "closed" in msg:
                print("ブラウザが閉じられました")
            else:
                print(f"[警告] セッション中に例外: {e}")
                rc = 3
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass

    fp.close()
    elapsed = time.time() - start_ts
    print(f"\n記録完了: {request_count} request / {elapsed:.1f} 秒")
    print(f"サマリ:")
    _print_summary(out_path)
    return rc


_STATIC_RESOURCE_RE = re.compile(r"\.(css|js|woff2?|ttf|eot|otf|png|jpe?g|gif|svg|ico|webp|map)(\?|$)", re.I)
_NOISE_HOSTS = (
    "google-analytics.com", "googletagmanager.com", "doubleclick.net",
    "googleapis.com/gstatic", "fonts.googleapis.com", "fonts.gstatic.com",
    "facebook.com", "facebook.net",
)


def _is_static_or_noise(url: str, resource_type: str = "") -> bool:
    if resource_type in ("stylesheet", "script", "image", "font", "media"):
        return True
    if _STATIC_RESOURCE_RE.search(url):
        return True
    host = urlparse(url).hostname or ""
    return any(noise in host for noise in _NOISE_HOSTS)


def _summarize(ndjson_path: Path):
    """NDJSON を読んで集計データを返す。"""
    response_by_endpoint: dict[tuple, dict] = defaultdict(lambda: {
        "method": "", "count": 0, "statuses": Counter(),
        "content_types": Counter(), "first_url": "", "is_xhr_or_api": False,
        "first_body_sample": "",
    })
    total_resp = 0

    with ndjson_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("kind") != "response":
                continue
            url = rec.get("url", "")
            resource_type = rec.get("resource_type", "")
            if _is_static_or_noise(url, resource_type):
                continue
            total_resp += 1
            method = rec.get("method", "GET")
            parsed = urlparse(url)
            # URL を「ホスト + path」だけにグルーピング (クエリ無視)
            key = (method, parsed.netloc, parsed.path)
            ct = (rec.get("headers") or {}).get("content-type", "").split(";")[0].strip()
            entry = response_by_endpoint[key]
            entry["method"] = method
            entry["count"] += 1
            entry["statuses"][rec.get("status", 0)] += 1
            if ct:
                entry["content_types"][ct] += 1
            if not entry["first_url"]:
                entry["first_url"] = url
            if resource_type in ("xhr", "fetch"):
                entry["is_xhr_or_api"] = True
            if not entry["first_body_sample"] and rec.get("body_sample"):
                entry["first_body_sample"] = rec["body_sample"][:300]

    return total_resp, response_by_endpoint


def _print_summary(ndjson_path: Path):
    total_resp, by_endpoint = _summarize(ndjson_path)
    print(f"記録された response: {total_resp} 件 (静的リソース・解析タグは除外)")
    print()
    # XHR/fetch + JSON content type のものを上に出す (= 内部 API 候補)
    candidates = []
    others = []
    for key, info in by_endpoint.items():
        is_api = info["is_xhr_or_api"] or any(
            "json" in ct.lower() for ct in info["content_types"]
        )
        if is_api:
            candidates.append((key, info))
        else:
            others.append((key, info))

    candidates.sort(key=lambda x: -x[1]["count"])
    others.sort(key=lambda x: -x[1]["count"])

    if candidates:
        print("--- 内部 API 候補 (XHR/fetch または JSON 応答) ---")
        for key, info in candidates[:15]:
            method, host, path = key
            ct = ", ".join(f"{c}({n})" for c, n in info["content_types"].most_common(2))
            statuses = ", ".join(f"{s}({n})" for s, n in info["statuses"].most_common(3))
            print(f"  {method:6s} {host}{path}")
            print(f"         × {info['count']} / status={statuses} / ct={ct}")
            if info["first_body_sample"]:
                sample = info["first_body_sample"].replace("\n", " ")[:120]
                print(f"         body[:120] = {sample}")
        print()

    if others:
        print(f"--- その他 (HTML/text 等) {len(others)} エンドポイント ---")
        for key, info in others[:5]:
            method, host, path = key
            print(f"  {method:6s} {host}{path}  × {info['count']}")
        if len(others) > 5:
            print(f"  ... ({len(others)-5} 件省略)")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("target", nargs="?",
                        help="偵察対象 URL (記録モード) または NDJSON パス (--summarize 時)")
    parser.add_argument("--out", default=None,
                        help="記録の出力先 NDJSON パス (省略時は docs/recon/recon_<timestamp>.ndjson)")
    parser.add_argument("--summarize", action="store_true",
                        help="既存 NDJSON のサマリだけを出す (target に NDJSON パスを指定)")
    parser.add_argument("--headless", action="store_true",
                        help="ヘッドレスで起動 (通常は手動操作のため非推奨)")
    args = parser.parse_args()

    if args.summarize:
        if not args.target:
            parser.error("--summarize の場合は target に NDJSON パスを指定してください")
        ndjson_path = Path(args.target)
        if not ndjson_path.exists():
            print(f"[NG] NDJSON が見つかりません: {ndjson_path}", file=sys.stderr)
            return 4
        _print_summary(ndjson_path)
        return 0

    if not args.target:
        parser.print_help()
        return 0

    # 記録モード
    if args.out:
        out_path = Path(args.out)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        host = urlparse(args.target).hostname or "site"
        host = re.sub(r"[^A-Za-z0-9]", "_", host)
        out_path = Path("docs/recon") / f"recon_{host}_{ts}.ndjson"

    return _open_recording_session(args.target, out_path, headless=args.headless)


if __name__ == "__main__":
    sys.exit(main())
