#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""既存 cases/ 配下の case.yaml を正規化して引用文献IDの重複を排除する。

使い方:
    python tools/normalize_case_citations.py           # dry-run
    python tools/normalize_case_citations.py --apply   # 実際に書き換える

挙動:
    - 各 case.yaml の citations[] を modules.citation_id.dedupe_citations で正規化
    - citations/ と responses/ 配下のJSONファイルを走査し、
      「正規化形と種別コード付き形が両方ある」ケースを検出してレポート
      （実ファイルは touch せず、警告のみ出力）
"""

from __future__ import annotations

import argparse
import sys
import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.citation_id import normalize_citation_id, dedupe_citations  # noqa: E402


def _scan_case(case_dir: Path) -> dict:
    """1件の case を走査して diff を集計。"""
    yaml_path = case_dir / "case.yaml"
    if not yaml_path.exists():
        return {"case_id": case_dir.name, "skipped": True, "reason": "no case.yaml"}

    with open(yaml_path, "r", encoding="utf-8") as f:
        meta = yaml.safe_load(f) or {}

    before = meta.get("citations", []) or []
    after = dedupe_citations(before)

    removed_ids = []
    if len(before) != len(after):
        after_ids = {c["id"] for c in after}
        seen = set()
        for c in before:
            cid = c.get("id", "")
            norm = normalize_citation_id(cid)
            if cid != norm or norm in seen:
                removed_ids.append(cid)
            seen.add(norm)

    orphan_files: list[str] = []
    for sub in ("citations", "responses"):
        d = case_dir / sub
        if not d.exists():
            continue
        stems = {f.stem: f for f in d.glob("*.json")}
        for stem, f in stems.items():
            if stem.startswith("_"):
                continue
            norm = normalize_citation_id(stem)
            if norm != stem and norm in stems:
                orphan_files.append(str(f.relative_to(case_dir)))

    return {
        "case_id": case_dir.name,
        "before_count": len(before),
        "after_count": len(after),
        "removed_ids": removed_ids,
        "orphan_files": orphan_files,
        "changed": len(before) != len(after),
        "_meta": meta,
        "_after": after,
        "_yaml_path": yaml_path,
    }


def main(apply: bool = False) -> int:
    cases_dir = PROJECT_ROOT / "cases"
    if not cases_dir.exists():
        print("cases/ ディレクトリが見つかりません", file=sys.stderr)
        return 1

    total_changed = 0
    total_orphans = 0

    for case_dir in sorted(cases_dir.iterdir()):
        if not case_dir.is_dir() or not (case_dir / "case.yaml").exists():
            continue
        info = _scan_case(case_dir)
        if info.get("skipped"):
            continue

        marker = "[変更]" if info["changed"] else "[OK  ]"
        print(f"{marker} {info['case_id']}: citations {info['before_count']} -> {info['after_count']}")
        if info["removed_ids"]:
            for rid in info["removed_ids"]:
                print(f"         重複排除: {rid}")
        if info["orphan_files"]:
            print(f"         [!] 正規化形と重複するファイル:")
            for f in info["orphan_files"]:
                print(f"           - {f}")

        total_changed += int(info["changed"])
        total_orphans += len(info["orphan_files"])

        if apply and info["changed"]:
            meta = info["_meta"]
            meta["citations"] = info["_after"]
            with open(info["_yaml_path"], "w", encoding="utf-8") as f:
                yaml.dump(meta, f, allow_unicode=True, default_flow_style=False)

    mode = "適用" if apply else "dry-run"
    print()
    print(f"=== {mode}完了: {total_changed}件のcase.yamlで変更 / 重複ファイル合計 {total_orphans}件 ===")
    if not apply and total_changed:
        print("実際に書き換えるには --apply を付けて再実行してください。")
    if total_orphans:
        print("重複ファイル（citations/*.json, responses/*.json）は手動確認してください。")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="変更を実際に適用する")
    args = ap.parse_args()
    sys.exit(main(apply=args.apply))
