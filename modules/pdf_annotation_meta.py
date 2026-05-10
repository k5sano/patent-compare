#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""注釈PDFの鮮度判定用メタデータ。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path


def annotation_meta_path(pdf_path) -> Path:
    p = Path(pdf_path)
    return p.with_suffix(".meta.json")


def file_sha256(path) -> str | None:
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _hash_case_file(case_dir: Path, rel: str) -> dict:
    p = case_dir / rel
    return {
        "path": rel,
        "sha256": file_sha256(p),
        "exists": p.exists(),
    }


def build_annotation_inputs(case_dir, kind: str, *, citation_id: str | None = None) -> dict:
    case_dir = Path(case_dir)
    inputs = {
        "segments": _hash_case_file(case_dir, "segments.json"),
        "keywords": _hash_case_file(case_dir, "keywords.json"),
    }
    if kind == "hongan":
        inputs["hongan"] = _hash_case_file(case_dir, "hongan.json")
        inputs["related_paragraphs"] = _hash_case_file(case_dir, "related_paragraphs.json")
    elif kind == "citation" and citation_id:
        inputs["response"] = _hash_case_file(case_dir, f"responses/{citation_id}.json")
        inputs["citation"] = _hash_case_file(case_dir, f"citations/{citation_id}.json")
    return inputs


def write_annotation_meta(
    pdf_path,
    *,
    case_id: str,
    kind: str,
    case_dir,
    citation_id: str | None = None,
    source_pdf: str | None = None,
) -> Path:
    p = Path(pdf_path)
    meta = {
        "schema": 1,
        "kind": kind,
        "case_id": case_id,
        "citation_id": citation_id or "",
        "pdf_filename": p.name,
        "source_pdf": Path(source_pdf).name if source_pdf else "",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": build_annotation_inputs(case_dir, kind, citation_id=citation_id),
    }
    mp = annotation_meta_path(p)
    with mp.open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return mp


def read_annotation_meta(pdf_path) -> dict | None:
    mp = annotation_meta_path(pdf_path)
    if not mp.exists():
        return None
    try:
        with mp.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def evaluate_annotation_freshness(
    pdf_path,
    *,
    case_dir,
    kind: str,
    citation_id: str | None = None,
) -> dict:
    if not pdf_path:
        return {"state": "missing", "reasons": [], "meta": None}
    p = Path(pdf_path)
    if not p.exists():
        return {"state": "missing", "reasons": [], "meta": None}
    meta = read_annotation_meta(p)
    if not meta:
        return {"state": "unknown", "reasons": ["作成時情報がありません"], "meta": None}

    current = build_annotation_inputs(case_dir, kind, citation_id=citation_id)
    old = meta.get("inputs") or {}
    reasons = []
    for key, cur in current.items():
        prev = old.get(key) or {}
        if prev.get("sha256") != cur.get("sha256"):
            reasons.append(key)

    return {
        "state": "stale" if reasons else "latest",
        "reasons": reasons,
        "meta": meta,
    }
