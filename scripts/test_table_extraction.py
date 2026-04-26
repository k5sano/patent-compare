# -*- coding: utf-8 -*-
"""
Phase 0 検証スクリプト: 1 案件の入力 PDF (本願 + 引用文献) から表を抽出して
精度・速度・サブスク消費量を実測する。

使い方:
    python scripts/test_table_extraction.py [case_id] [--max-images N] [--model sonnet] [--limit-pdfs M]

デフォルト:
    case_id = 2024-051653
    --model = sonnet (Phase 0 検証で精度・コストともベスト)
    --max-images = なし (全候補)
    --limit-pdfs = 1 (まず本願 PDF だけ)

出力:
    cases/<case_id>/output/tables/<doc_id>/
        images/<doc_id>_p<n>_x<xref>.png  ← 抽出した画像
        tables.json                          ← 抽出結果 (per-PDF)
    cases/<case_id>/output/tables/SUMMARY.json  ← 全 PDF の統計サマリ
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.table_extractor import extract_tables_from_pdf  # noqa: E402


def _progress(stage: str, current: int, total: int, info: str):
    if stage == "scan":
        print(f"  [scan] {info}", flush=True)
    elif stage == "extract":
        print(f"  [{current}/{total}] extracting {info} ...", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case_id", nargs="?", default="2024-051653")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--max-images", type=int, default=None,
                    help="1 PDF あたりの抽出枚数上限 (デバッグ用)")
    ap.add_argument("--limit-pdfs", type=int, default=1,
                    help="処理する PDF 数の上限。1 なら本願 PDF だけが目安")
    ap.add_argument("--include", nargs="*", default=None,
                    help="ファイル名に含まれる文字列でフィルタ (例: --include 特開 WO)")
    ap.add_argument("--include-uncaptioned", action="store_true",
                    help="表キャプションが無い画像も Claude に投げる (Phase 0 の挙動)")
    args = ap.parse_args()

    case_dir = PROJECT_ROOT / "cases" / args.case_id
    if not case_dir.exists():
        print(f"!! case dir not found: {case_dir}", file=sys.stderr)
        sys.exit(1)
    input_dir = case_dir / "input"
    pdfs = sorted(input_dir.glob("*.pdf"))
    if args.include:
        pdfs = [p for p in pdfs if any(s in p.name for s in args.include)]
    if args.limit_pdfs:
        pdfs = pdfs[:args.limit_pdfs]
    if not pdfs:
        print(f"!! no PDFs in {input_dir}", file=sys.stderr)
        sys.exit(1)

    out_root = case_dir / "output" / "tables"
    out_root.mkdir(parents=True, exist_ok=True)

    print(f"=== Phase 0 table extraction ===")
    print(f"  case_id : {args.case_id}")
    print(f"  pdfs    : {len(pdfs)}")
    print(f"  model   : {args.model}")
    print(f"  max_imgs: {args.max_images}")
    print(f"  out_root: {out_root}")
    print()

    overall = {
        "case_id": args.case_id,
        "model": args.model,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "per_pdf": [],
        "totals": {
            "candidates": 0,
            "n_table": 0,
            "n_nontable": 0,
            "n_error": 0,
            "duration_ms": 0,
            "cost_usd_equivalent": 0.0,
        },
    }

    t_start = time.monotonic()
    for pi, pdf in enumerate(pdfs, 1):
        print(f"\n--- [{pi}/{len(pdfs)}] {pdf.name} ---", flush=True)
        out_dir = out_root / pdf.stem
        try:
            res = extract_tables_from_pdf(
                pdf, out_dir, model=args.model,
                max_images=args.max_images,
                include_uncaptioned=args.include_uncaptioned,
                progress=_progress,
            )
        except Exception as e:
            print(f"  !! extraction failed: {e}")
            overall["per_pdf"].append({
                "doc_id": pdf.stem, "error": str(e),
            })
            continue
        print(f"  candidates={res['candidates_total']} targeted={res['candidates_targeted']}"
              f" skipped={res['candidates_skipped']} table={res['n_table']}"
              f" nontable={res['n_nontable']} error={res['n_error']}"
              f" duration={res['total_duration_ms']/1000:.1f}s"
              f" cost=${res['total_cost_usd_equivalent']:.4f}")
        overall["per_pdf"].append({
            "doc_id": res["doc_id"],
            "candidates": res["candidates_total"],
            "targeted": res["candidates_targeted"],
            "skipped": res["candidates_skipped"],
            "n_table": res["n_table"],
            "n_nontable": res["n_nontable"],
            "n_error": res["n_error"],
            "duration_ms": res["total_duration_ms"],
            "cost_usd_equivalent": res["total_cost_usd_equivalent"],
            "output_json": res["output_json"],
        })
        t = overall["totals"]
        t["candidates"] += res["candidates_total"]
        t["n_table"] += res["n_table"]
        t["n_nontable"] += res["n_nontable"]
        t["n_error"] += res["n_error"]
        t["duration_ms"] += res["total_duration_ms"]
        t["cost_usd_equivalent"] = round(
            t["cost_usd_equivalent"] + res["total_cost_usd_equivalent"], 4
        )
    overall["wall_time_s"] = round(time.monotonic() - t_start, 1)
    overall["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    summary_path = out_root / "SUMMARY.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(overall, f, ensure_ascii=False, indent=2)

    print()
    print("=== TOTALS ===")
    t = overall["totals"]
    print(f"  PDFs processed   : {len(overall['per_pdf'])}")
    print(f"  candidates total : {t['candidates']}")
    print(f"  tables OK        : {t['n_table']}")
    print(f"  non-table images : {t['n_nontable']}")
    print(f"  errors           : {t['n_error']}")
    print(f"  total LLM time   : {t['duration_ms']/1000:.1f}s")
    print(f"  wall time        : {overall['wall_time_s']}s")
    print(f"  cost equivalent  : ${t['cost_usd_equivalent']:.4f}")
    print(f"  summary saved    : {summary_path}")


if __name__ == "__main__":
    main()
