#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Copy CLAUDE.md to AGENTS.md.

CLAUDE.md is the single source of truth for project agent rules.
Run this script after changing CLAUDE.md so Codex and Claude read identical
rules.
"""
from __future__ import annotations

from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    source = root / "CLAUDE.md"
    target = root / "AGENTS.md"
    target.write_bytes(source.read_bytes())
    print(f"synced {target.relative_to(root)} from {source.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
