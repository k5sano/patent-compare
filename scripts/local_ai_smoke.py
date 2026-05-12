#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Ollama/local-ai smoke test for patent-compare."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.claude_client import (  # noqa: E402
    DEFAULT_LOCAL_AI_MODEL,
    ClaudeClientError,
    call_claude,
    is_local_ai_available,
)


def _gpu_summary() -> str:
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,memory.free",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        return "GPU: nvidia-smi で確認できませんでした"
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    if not lines:
        return "GPU: nvidia-smi の結果が空でした"
    name, total, used, free = [p.strip() for p in lines[0].split(",", 3)]
    return f"GPU: {name} / VRAM {total} MiB (used {used}, free {free})"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a local-ai/Ollama smoke test.")
    parser.add_argument("--model", default=DEFAULT_LOCAL_AI_MODEL)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    print(_gpu_summary())
    print(f"Ollama: {'available' if is_local_ai_available() else 'not available'}")
    print(f"Model: {args.model}")

    prompt = (
        "次の用途を20字以内で要約してください。\n"
        "特許比較ツールの低リスクな整形、要約、抽出をローカルLLMで補助する。"
    )
    try:
        out = call_claude(prompt, model=f"ollama:{args.model}", timeout=args.timeout)
    except ClaudeClientError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("Response:")
    print(out.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
