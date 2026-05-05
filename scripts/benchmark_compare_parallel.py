#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Step 5 対比実行の「単一プロセス vs Sonnet 並列」実測ベンチマーク

ユーザ仮説検証用:
  「Sonnet 3 並列は CLI 起動オーバーヘッド + prompt cache miss で
   かえって遅くなる」を実測で確認する。

使い方:
    cd patent-compare
    python scripts/benchmark_compare_parallel.py <case_id> [--limit N] [--workers W]

実行内容:
  1. case の citations を読み込む (既存の対比結果は触らずに別フォルダへ書き戻す)
  2. 同じ citations に対して以下を順番に実行し時間計測:
     a) Sonnet, COMPARE_PARALLEL=0 (統合プロンプト 1 回)
     b) Sonnet, COMPARE_PARALLEL=W (citation 単位 W 並列)
  3. 経過秒数・各 citation の所要・total token (取れる範囲で) を比較

注意:
  - 実 Claude OAuth クォータを消費する (Sonnet で 2 回分, 約 $0.5〜$2)
  - 既存 case の prompts/responses は別ディレクトリ
    (output/_bench/<timestamp>/) にコピーされ、本物の対比結果は壊されない
"""

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

# 親ディレクトリを sys.path に追加
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("case_id")
    p.add_argument("--limit", type=int, default=None,
                   help="対比対象 citation 件数を絞る (デフォルト: 全件)")
    p.add_argument("--workers", type=int, default=3,
                   help="並列実行時の max_workers (デフォルト: 3)")
    p.add_argument("--model", default="sonnet",
                   help="使用モデル (デフォルト: sonnet)")
    p.add_argument("--skip-parallel", action="store_true",
                   help="並列モードをスキップ (単一のみ実行)")
    p.add_argument("--skip-single", action="store_true",
                   help="単一モードをスキップ (並列のみ実行)")
    p.add_argument("--compare-mode", choices=["legacy", "requirement_first", "both"],
                   default="legacy",
                   help="prompt 方式: legacy (既定) / requirement_first / both で両方計測")
    return p.parse_args()


def main():
    args = parse_args()

    from services.case_service import load_case_meta, get_case_dir
    from services.comparison_service import compare_execute

    meta = load_case_meta(args.case_id)
    if not meta:
        print(f"案件 '{args.case_id}' が見つかりません")
        sys.exit(1)

    cit_ids = [c["id"] for c in meta.get("citations", [])]
    if args.limit:
        cit_ids = cit_ids[: args.limit]
    if len(cit_ids) < 2:
        print(f"citations が {len(cit_ids)} 件しかなく、比較に意味がない")
        sys.exit(1)

    case_dir = get_case_dir(args.case_id)
    bench_dir = case_dir / "output" / f"_bench_{datetime.now():%Y%m%d_%H%M%S}"
    bench_dir.mkdir(parents=True, exist_ok=True)

    # 元の responses をバックアップ (本物の結果を壊さない)
    responses_dir = case_dir / "responses"
    backup_dir = bench_dir / "responses_backup"
    if responses_dir.exists():
        shutil.copytree(str(responses_dir), str(backup_dir))
        print(f"既存 responses をバックアップ: {backup_dir}")

    print(f"\n対比対象: {len(cit_ids)} 件")
    print(f"  ids: {cit_ids[:5]}{'...' if len(cit_ids) > 5 else ''}")
    print(f"モデル: {args.model}")

    results = []

    def run_one(label, parallel_workers, mode):
        os.environ["COMPARE_PARALLEL"] = str(parallel_workers)
        print(f"\n{'='*60}\n{label} (COMPARE_PARALLEL={parallel_workers}, mode={mode})\n{'='*60}")
        # 元の responses を復元してから実行
        if responses_dir.exists():
            shutil.rmtree(str(responses_dir))
        if backup_dir.exists():
            shutil.copytree(str(backup_dir), str(responses_dir))

        t0 = time.time()
        try:
            result, code = compare_execute(args.case_id, cit_ids, model=args.model, mode=mode)
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  EXCEPTION after {elapsed:.1f}s: {e}")
            return {"label": label, "elapsed": elapsed, "error": str(e)}
        elapsed = time.time() - t0
        info = {
            "label": label,
            "compare_mode": mode,
            "parallel_workers": parallel_workers,
            "elapsed_sec": round(elapsed, 1),
            "status_code": code,
            "num_docs": result.get("num_docs"),
            "saved_docs": result.get("saved_docs", []),
            "char_count": result.get("char_count"),
            "response_length": result.get("response_length"),
            "errors": result.get("errors", [])[:5],
            "parallel_metric": result.get("parallel"),
        }
        print(f"  elapsed: {elapsed:.1f}s")
        print(f"  num_docs: {info['num_docs']}, char_count: {info['char_count']}, "
              f"response_length: {info['response_length']}")
        if info['errors']:
            print(f"  errors (first 5): {info['errors']}")
        results.append(info)
        return info

    modes = ["legacy", "requirement_first"] if args.compare_mode == "both" else [args.compare_mode]
    for mode in modes:
        if not args.skip_single:
            run_one(f"Sonnet 単一 / mode={mode}", parallel_workers=0, mode=mode)
        if not args.skip_parallel:
            run_one(f"Sonnet {args.workers}並列 / mode={mode}",
                    parallel_workers=args.workers, mode=mode)

    # 結果保存
    out = bench_dir / "result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "case_id": args.case_id,
            "citation_count": len(cit_ids),
            "model": args.model,
            "workers": args.workers,
            "runs": results,
            "timestamp": datetime.now().isoformat(),
        }, f, ensure_ascii=False, indent=2)
    print(f"\n結果 JSON: {out}")

    # 元の responses を最終的に復元
    if responses_dir.exists():
        shutil.rmtree(str(responses_dir))
    if backup_dir.exists():
        shutil.copytree(str(backup_dir), str(responses_dir))
        print(f"responses を元の状態に復元しました")

    # サマリ
    if len(results) >= 2:
        print(f"\n{'='*60}\n結果サマリ\n{'='*60}")
        for r in results:
            print(f"  {r['label']:50} {r['elapsed_sec']:7.1f}s  "
                  f"({r['num_docs']} docs)")
        if not (results[0].get('error') or results[1].get('error')):
            t1, t2 = results[0]['elapsed_sec'], results[1]['elapsed_sec']
            ratio = t2 / t1 if t1 else float('inf')
            diff = t2 - t1
            print(f"\n  並列 / 単一 = {ratio:.2f}x  (差分 {diff:+.1f}秒)")
            if ratio < 0.85:
                print(f"  → 並列の方が明らかに高速 (15%以上短縮)")
            elif ratio > 1.15:
                print(f"  → 単一の方が高速 (並列で 15%以上の遅延)")
            else:
                print(f"  → ほぼ同等 (±15% 以内)")


if __name__ == "__main__":
    main()
