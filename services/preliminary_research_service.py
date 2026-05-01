#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""予備調査サービス: 分野別レシピ読込 → 検索URL生成 → メモ保存。

Step 2 の予備調査サブタブから呼ばれる。化粧品/汎用などの分野ごとに「どの情報源を
当たるか」を YAML レシピで定義し、ユーザーが入力した成分名から検索 URL を生成して
ワンクリックでブラウザを開くだけのシンプルな機能。

レシピ形式は templates/preliminary_research/<field>.yaml を参照。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

import yaml

from services import case_service
from services.case_service import get_case_dir, load_case_meta


def _project_root() -> Path:
    """毎回 case_service.PROJECT_ROOT を参照することでテストの monkeypatch に追従。"""
    return case_service.PROJECT_ROOT


def _templates_dir() -> Path:
    return _project_root() / "templates" / "preliminary_research"


def list_available_fields() -> list[str]:
    """利用可能な分野レシピのスラッグ一覧 (YAML stem)"""
    d = _templates_dir()
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.yaml"))


def load_recipe(field: str) -> dict:
    """分野名からレシピ YAML を読み込む。見つからない場合は generic を返す。

    generic も無ければ最低限のスケルトンを返す (UI が落ちないように)。
    """
    d = _templates_dir()
    path = d / f"{field}.yaml"
    if not path.exists():
        path = d / "generic.yaml"
    if not path.exists():
        return {
            "field": "generic",
            "display_name": "汎用",
            "sources": [],
            "synonym_expansion": {"enabled": False},
        }
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def generate_search_urls(recipe: dict, queries: Iterable[str]) -> list[dict]:
    """レシピの各 source について、各クエリの検索 URL を生成。

    並び順: priority 昇順 → クエリ入力順 → source 入力順 (安定ソート)。
    UI 側で「情報源ごと×クエリごと」のテーブルとして表示できる形に整える。
    """
    queries = [q for q in (queries or []) if q and q.strip()]
    if not queries:
        return []
    sources = recipe.get("sources") or []
    results = []
    # クエリ入力順を保つため index を付与
    for q_idx, query in enumerate(queries):
        q = query.strip()
        for s_idx, source in enumerate(sources):
            tmpl = source.get("search_url_template") or ""
            if "{query}" not in tmpl:
                continue
            encoded = quote(q, encoding=source.get("encoding") or "utf-8")
            url = tmpl.replace("{query}", encoded)
            results.append({
                "source_id": source.get("id", f"source_{s_idx}"),
                "source_name": source.get("name", ""),
                "description": source.get("description", ""),
                "query": q,
                "url": url,
                "priority": source.get("priority", 999),
                "_q_idx": q_idx,
                "_s_idx": s_idx,
            })
    # priority → クエリ順 → source 順
    results.sort(key=lambda r: (r["priority"], r["_q_idx"], r["_s_idx"]))
    # 内部用 index を落とす
    for r in results:
        r.pop("_q_idx", None)
        r.pop("_s_idx", None)
    return results


def _safe_section_title(component: str) -> str:
    """Markdown セクション見出しに使う前にサニタイズ (改行/制御文字を除去)"""
    s = (component or "").strip()
    s = re.sub(r"[\r\n\t]+", " ", s)
    return s or "(無題)"


def save_note(case_id: str, component: str, note: str,
              urls_opened: list[str] | None = None,
              queries: list[str] | None = None,
              field: str | None = None) -> dict:
    """予備調査メモを cases/<case_id>/analysis/hongan_understanding.md に追記。

    既存ファイルがあれば末尾に追記する (上書きしない)。
    """
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません", "_status": 404}

    case_dir = get_case_dir(case_id)
    analysis_dir = case_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    md_path = analysis_dir / "hongan_understanding.md"

    title = _safe_section_title(component)
    lines = [f"\n\n## 予備調査: {title}"]
    if field:
        lines.append(f"\n_分野: {field}_")
    if queries:
        qs = ", ".join(q for q in queries if q)
        if qs:
            lines.append(f"\n**採用クエリ**: {qs}")
    lines.append("")
    lines.append((note or "").strip() or "_(メモなし)_")
    if urls_opened:
        lines.append("\n### 参照した情報源")
        for url in urls_opened:
            u = (url or "").strip()
            if u:
                lines.append(f"- {u}")

    body = "\n".join(lines) + "\n"
    with md_path.open("a", encoding="utf-8") as f:
        f.write(body)
    root = _project_root()
    try:
        rel = md_path.relative_to(root)
        saved_to = str(rel)
    except ValueError:
        saved_to = str(md_path)
    return {
        "success": True,
        "saved_to": saved_to,
        "appended_chars": len(body),
    }
