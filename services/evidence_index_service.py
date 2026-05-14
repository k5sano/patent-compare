#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Citation evidence index for paragraph/table retrieval.

The index is intentionally lightweight: it flattens public citation JSON into
paragraph and table chunks, caches that derived structure, and performs a
high-precision lexical search. It is used as a local RAG layer before asking an
LLM to re-check a comparison cell.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from services.case_service import get_case_dir


_FW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_STOPWORDS = {
    "について", "として", "する", "した", "して", "いる", "ある", "これ", "それ",
    "本願", "引例", "引用", "文献", "発明", "構成", "判断", "認定", "記載",
    "請求項", "段落", "比較例", "実施例", "表",
}
_TOKEN_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9_.%/\-]{1,}|"
    r"[一-龥ぁ-んァ-ヶーα-ωΑ-Ω][一-龥ぁ-んァ-ヶーα-ωΑ-Ω0-9０-９％%℃μµ・ー\-]{1,}"
)
_REF_RE = re.compile(
    r"比較\s*例\s*[0-9０-９]+|"
    r"実施\s*例\s*[0-9０-９]+|"
    r"表\s*[0-9０-９]+|"
    r"請求\s*項\s*[0-9０-９]+|"
    r"[0-9０-９]+(?:\.[0-9０-９]+)?\s*(?:質量|重量)?\s*[％%]",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _safe_name(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("_") or "citation"


def _read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _compact_text(value: Any) -> str:
    s = str(value or "").translate(_FW_DIGITS)
    s = s.replace("−", "-").replace("－", "-").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", "", s).lower()


def _add_term(out: list[str], seen: set[str], term: Any) -> None:
    s = re.sub(r"\s+", "", str(term or "").translate(_FW_DIGITS))
    s = s.strip("「」『』()（）[]【】,，.。:：;；")
    if len(s) < 2 or s in _STOPWORDS:
        return
    key = _compact_text(s)
    if not key or key in seen:
        return
    seen.add(key)
    out.append(s)


def extract_query_terms(*texts: Any, max_terms: int = 64) -> list[str]:
    joined = "\n".join(str(t or "") for t in texts)
    out: list[str] = []
    seen: set[str] = set()
    for m in _REF_RE.finditer(joined):
        _add_term(out, seen, m.group(0))
    for m in _TOKEN_RE.finditer(joined):
        _add_term(out, seen, m.group(0))
        if len(out) >= max_terms:
            break
    return out[:max_terms]


def _table_label(table: dict[str, Any], index: int) -> str:
    return str(
        table.get("caption_label")
        or table.get("caption")
        or table.get("title")
        or table.get("label")
        or f"表{index}"
    )


def flatten_table_text(table: dict[str, Any], *, max_chars: int = 12000) -> str:
    parts: list[str] = []
    for key in ("caption_label", "caption", "title", "section"):
        value = table.get(key)
        if value:
            parts.append(str(value))
    headers = table.get("headers") or []
    if headers:
        parts.append("\t".join(str(x) for x in headers))
    rows = table.get("rows") or table.get("data") or []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                cells = row.get("cells") or row.get("values") or []
                if cells:
                    parts.append("\t".join(str(x) for x in cells))
                else:
                    parts.append(json.dumps(row, ensure_ascii=False))
            elif isinstance(row, list):
                parts.append("\t".join(str(x) for x in row))
            elif row is not None:
                parts.append(str(row))
    content = table.get("content") or table.get("text") or ""
    if content:
        parts.append(str(content))
    text = "\n".join(_normalize_space(p) for p in parts if str(p or "").strip())
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def _citation_fingerprint(citation: dict[str, Any]) -> str:
    payload = {
        "paragraphs": [
            {
                "id": p.get("id") or p.get("number"),
                "page": p.get("page"),
                "section": p.get("section"),
                "text": p.get("text", ""),
            }
            for p in (citation.get("paragraphs") or [])
        ],
        "tables": [
            {
                "label": _table_label(t, i),
                "page": t.get("page") or t.get("page_num") or t.get("page_number"),
                "text": flatten_table_text(t),
            }
            for i, t in enumerate(citation.get("tables") or [], 1)
            if isinstance(t, dict)
        ],
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _index_path(case_id: str, citation_id: str) -> Path:
    return get_case_dir(case_id) / "indexes" / "evidence" / f"{_safe_name(citation_id)}.json"


def _load_citation(case_id: str, citation_id: str) -> dict[str, Any]:
    path = get_case_dir(case_id) / "citations" / f"{citation_id}.json"
    return _read_json(path, {}) or {}


def build_evidence_index(
    case_id: str,
    citation_id: str,
    *,
    citation: dict[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Build or load a cached paragraph/table chunk index for a citation."""
    citation = citation if citation is not None else _load_citation(case_id, citation_id)
    citation = citation or {}
    fingerprint = _citation_fingerprint(citation)
    path = _index_path(case_id, citation_id)
    cached = _read_json(path, None)
    if (
        not force
        and isinstance(cached, dict)
        and cached.get("fingerprint") == fingerprint
        and isinstance(cached.get("chunks"), list)
    ):
        return cached

    chunks: list[dict[str, Any]] = []
    for idx, para in enumerate(citation.get("paragraphs") or []):
        text = _normalize_space(para.get("text", ""))
        if not text:
            continue
        para_no = str(para.get("id") or para.get("number") or idx + 1)
        label = f"【{para_no}】"
        term_text = f"{label}\n{text}\n{para.get('section') or ''}"
        chunks.append({
            "chunk_id": f"P:{para_no}",
            "kind": "paragraph",
            "citation_id": citation_id,
            "index": idx,
            "label": label,
            "para_no": para_no,
            "page": para.get("page"),
            "section": para.get("section"),
            "text": text,
            "terms": extract_query_terms(term_text, max_terms=96),
        })

    for idx, table in enumerate(citation.get("tables") or [], 1):
        if not isinstance(table, dict):
            continue
        text = flatten_table_text(table)
        if not text.strip():
            continue
        label = _table_label(table, idx)
        chunks.append({
            "chunk_id": f"T:{idx}",
            "kind": "table",
            "citation_id": citation_id,
            "index": idx,
            "label": label,
            "page": table.get("page") or table.get("page_num") or table.get("page_number"),
            "source": table.get("source"),
            "text": text,
            "terms": extract_query_terms(f"{label}\n{text}", max_terms=128),
        })

    index = {
        "version": 1,
        "case_id": case_id,
        "citation_id": citation_id,
        "fingerprint": fingerprint,
        "created_at": _now(),
        "chunk_count": len(chunks),
        "paragraph_count": sum(1 for c in chunks if c.get("kind") == "paragraph"),
        "table_count": sum(1 for c in chunks if c.get("kind") == "table"),
        "chunks": chunks,
    }
    _write_json(path, index)
    return index


def _score_chunk(chunk: dict[str, Any], terms: list[str]) -> tuple[int, list[str]]:
    haystack = _compact_text("\n".join([
        str(chunk.get("label") or ""),
        str(chunk.get("section") or ""),
        str(chunk.get("text") or ""),
    ]))
    label = _compact_text(chunk.get("label") or "")
    score = 0
    matched: list[str] = []
    seen: set[str] = set()
    for term in terms or []:
        key = _compact_text(term)
        if len(key) < 2 or key not in haystack:
            continue
        weight = 1
        if re.match(r"^(比較例|実施例|表|請求項)\d+", key):
            weight = 12
        elif re.search(r"\d", key):
            weight = 5
        elif len(key) >= 8:
            weight = 3
        elif len(key) >= 5:
            weight = 2
        if key and key == label:
            weight += 8
        score += weight
        if key not in seen:
            seen.add(key)
            matched.append(str(term))

    chunk_terms = {_compact_text(t) for t in (chunk.get("terms") or [])}
    query_terms = {_compact_text(t) for t in (terms or [])}
    score += min(8, len((chunk_terms & query_terms) - seen))
    return score, matched[:10]


def _paragraph_hits_with_context(
    index: dict[str, Any],
    ranked: list[tuple[int, int, dict[str, Any], list[str]]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    paragraphs = [c for c in index.get("chunks") or [] if c.get("kind") == "paragraph"]
    by_idx = {int(c.get("index") or 0): c for c in paragraphs}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _score, _order, chunk, matched in ranked:
        if chunk.get("kind") != "paragraph":
            continue
        idx = int(chunk.get("index") or 0)
        for pos in range(max(0, idx - 1), idx + 2):
            ctx = by_idx.get(pos)
            if not ctx:
                continue
            para_no = str(ctx.get("para_no") or "")
            if para_no in seen:
                continue
            seen.add(para_no)
            out.append({
                "para_no": para_no,
                "page": ctx.get("page"),
                "section": ctx.get("section"),
                "text": ctx.get("text", ""),
                "matched_terms": matched if pos == idx else [],
                "source": "evidence_index",
            })
            if len(out) >= limit:
                return out
    return out


def search_evidence_index(
    case_id: str,
    citation_id: str,
    *,
    query_text: str = "",
    terms: list[str] | None = None,
    citation: dict[str, Any] | None = None,
    limit: int = 10,
    kinds: set[str] | None = None,
) -> dict[str, Any]:
    """Search paragraph/table chunks and return LLM-ready evidence snippets."""
    index = build_evidence_index(case_id, citation_id, citation=citation)
    query_terms = list(dict.fromkeys((terms or []) + extract_query_terms(query_text)))
    if not query_terms:
        return {
            "index": {
                "chunk_count": index.get("chunk_count", 0),
                "paragraph_count": index.get("paragraph_count", 0),
                "table_count": index.get("table_count", 0),
            },
            "chunks": [],
            "paragraphs": [],
            "tables": [],
            "query_terms": [],
        }

    ranked: list[tuple[int, int, dict[str, Any], list[str]]] = []
    for order, chunk in enumerate(index.get("chunks") or []):
        if kinds and chunk.get("kind") not in kinds:
            continue
        score, matched = _score_chunk(chunk, query_terms)
        if score <= 0:
            continue
        ranked.append((score, order, chunk, matched))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    ranked = ranked[: max(limit * 3, limit)]

    chunks = [
        {
            "chunk_id": chunk.get("chunk_id"),
            "kind": chunk.get("kind"),
            "label": chunk.get("label"),
            "para_no": chunk.get("para_no"),
            "page": chunk.get("page"),
            "score": score,
            "matched_terms": matched,
            "text": chunk.get("text", ""),
        }
        for score, _order, chunk, matched in ranked[:limit]
    ]
    paragraphs = _paragraph_hits_with_context(index, ranked, limit=limit)
    tables = []
    for score, _order, chunk, matched in ranked:
        if chunk.get("kind") != "table":
            continue
        tables.append({
            "index": chunk.get("index"),
            "label": chunk.get("label"),
            "page": chunk.get("page"),
            "source": chunk.get("source") or "evidence_index",
            "score": score,
            "matched_terms": matched,
            "text": chunk.get("text", ""),
        })
        if len(tables) >= min(4, limit):
            break

    return {
        "index": {
            "chunk_count": index.get("chunk_count", 0),
            "paragraph_count": index.get("paragraph_count", 0),
            "table_count": index.get("table_count", 0),
        },
        "chunks": chunks,
        "paragraphs": paragraphs,
        "tables": tables,
        "query_terms": query_terms[:32],
    }
