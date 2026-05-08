#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""J-PlatPat 書誌情報取得 smoke。

使い方:
    python scripts/jplatpat_bibliography_smoke.py --check
    python scripts/jplatpat_bibliography_smoke.py 特開2024-108988
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _check() -> int:
    ok = True
    print("=== J-PlatPat bibliography smoke check ===")
    print(f"python: {sys.version.split()[0]}")
    print(f"requests: {requests.__version__}")
    print(f"playwright: {'installed' if shutil.which('playwright') else 'CLI not found (direct API smoke can still run)'}")
    try:
        r = requests.get("https://www.j-platpat.inpit.go.jp/p0000", timeout=15)
        print(f"jplatpat: HTTP {r.status_code}")
        ok = ok and r.status_code < 500
    except Exception as e:
        print(f"jplatpat: NG {e}")
        ok = False
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("number", nargs="?", default="特開2024-108988",
                        help="取得対象番号 (例: 特開2024-108988 / 特許7250676)")
    parser.add_argument("--check", action="store_true", help="環境チェックのみ")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    if args.check:
        return _check()

    from modules.jplatpat_bibliography import fetch_jplatpat_bibliography
    data = fetch_jplatpat_bibliography(args.number, timeout=args.timeout)
    summary = {
        "patent_number": data.get("patent_number"),
        "application_number": data.get("application_number"),
        "application_date": data.get("application_date"),
        "applicants": data.get("applicants"),
        "inventors": data.get("inventors"),
        "ipc": data.get("ipc"),
        "fi": data.get("fi"),
        "theme_code": data.get("theme_code"),
        "fterm_count": len(data.get("fterm") or []),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
