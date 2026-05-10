#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Step 5 expert squad 実行経路。"""
from __future__ import annotations

import json

from services.case_service import get_case_dir, load_case_meta
from services.comparison.common import _load_citation_for_prompt
from services.comparison.prompt import (
    _empty_citation_error,
    _filter_keywords_by_valid_segments,
    _get_all_segment_ids,
    _is_empty_citation,
    _resolve_doc_id,
)
from services.comparison.response import _normalize_cited_locations_inplace
def _flatten_requirements(segs):
    out = []
    for claim in segs or []:
        for seg in claim.get("segments") or []:
            sid = seg.get("id")
            if sid:
                out.append({"id": sid, "text": seg.get("text", "")})
    return out


def _keywords_for_requirement(keywords, req_id):
    out = []
    for group in keywords or []:
        seg_ids = group.get("segment_ids") or []
        if req_id not in seg_ids:
            continue
        for key in ("term", "keyword", "label"):
            val = group.get(key)
            if isinstance(val, str) and val.strip():
                out.append(val.strip())
        for kw in group.get("keywords") or []:
            if isinstance(kw, str) and kw.strip():
                out.append(kw.strip())
            elif isinstance(kw, dict):
                val = kw.get("term") or kw.get("keyword")
                if isinstance(val, str) and val.strip():
                    out.append(val.strip())
    return list(dict.fromkeys(out))


def _compare_execute_expert_squad(case_id, citation_ids, *, model=None,
                                  mode="requirement_first", effort=None):
    """Opt-in Step 5 expert squad path.

    Phase 1:
      1. Extract evidence for each (citation, requirement) pair in parallel.
      2. Run the existing requirement-first judge prompt once with evidence markers.
      3. Save responses/<citation_id>.json using the existing parser/normalizers.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from modules.llm_experts import EXPERTS, run_expert
    from modules.response_parser import split_multi_response

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    segments_path = case_dir / "segments.json"

    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400
    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    if not citation_ids:
        return {"error": "対象文献を選択してください"}, 400

    citation_pairs = []
    empty_ids = []
    for cit_id in citation_ids:
        cit, err = _load_citation_for_prompt(case_id, cit_id, case_dir)
        if err:
            return {"error": err}, 404
        if _is_empty_citation(cit):
            empty_ids.append((cit_id, cit))
        citation_pairs.append((cit_id, cit))
    if empty_ids:
        msgs = [_empty_citation_error(cid, c) for cid, c in empty_ids]
        return {
            "error": " / ".join(msgs),
            "empty_citation_ids": [cid for cid, _ in empty_ids],
        }, 400

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)
    keywords = _filter_keywords_by_valid_segments(keywords, segs)

    field = (meta or {}).get("field", "cosmetics")
    hongan = None
    hongan_path = case_dir / "hongan.json"
    if hongan_path.exists():
        with open(hongan_path, "r", encoding="utf-8") as f:
            hongan = json.load(f)

    requirements = _flatten_requirements(segs)
    evidence_by_req_cit = {}
    extractor_meta = []

    def _extract(cit_id, cit, req):
        inputs = {
            "requirement_id": req["id"],
            "requirement_text": req["text"],
            "keywords": _keywords_for_requirement(keywords, req["id"]),
            "citation_id": cit_id,
            "citation": cit,
        }
        result = run_expert("evidence_extractor", inputs=inputs, case_id=case_id)
        key = f"{req['id']}||{cit_id}"
        if result.success:
            payload = result.parsed
        else:
            payload = {
                "requirement_id": req["id"],
                "citation_id": cit_id,
                "evidence_paragraphs": [],
                "evidence_tables": [],
                "no_match_reason": "; ".join(result.errors) or "extractor failed",
            }
        return key, payload, {
            "requirement_id": req["id"],
            "citation_id": cit_id,
            "cache_hit": result.cache_hit,
            "model_used": result.model_used,
            "errors": result.errors,
        }

    max_workers = max(1, min(EXPERTS["evidence_extractor"].max_parallel,
                             len(requirements) * len(citation_pairs) or 1))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [
            ex.submit(_extract, cit_id, cit, req)
            for cit_id, cit in citation_pairs
            for req in requirements
        ]
        for fut in as_completed(futures):
            key, payload, meta_item = fut.result()
            evidence_by_req_cit[key] = payload
            extractor_meta.append(meta_item)

    citations = [cit for _, cit in citation_pairs]
    all_segment_ids = _get_all_segment_ids(segs)
    judge_inputs = {
        "case_id": case_id,
        "segments": segs,
        "citations": citations,
        "keywords": keywords,
        "field": field,
        "hongan": hongan,
        "evidence_by_req_cit": evidence_by_req_cit,
        "all_segment_ids": all_segment_ids,
    }
    judge = run_expert(
        "claim_chart_judge",
        inputs=judge_inputs,
        case_id=case_id,
        model_override=model,
        effort_override=effort,
    )

    responses_dir = case_dir / "responses"
    responses_dir.mkdir(parents=True, exist_ok=True)
    raw_path = responses_dir / "_last_raw_response.txt"
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(judge.raw or "")

    expert_meta = {
        "mode": "expert_squad",
        "extractor": {
            "total": len(extractor_meta),
            "cache_hits": sum(1 for x in extractor_meta if x.get("cache_hit")),
            "errors": [x for x in extractor_meta if x.get("errors")],
            "max_workers": max_workers,
        },
        "judge": {
            "cache_hit": judge.cache_hit,
            "model_used": judge.model_used,
            "errors": judge.errors,
        },
    }
    judge_errors = judge_inputs.get("_validated_errors") or []

    if not judge.success:
        return {
            "error": "expert_squad judge の応答が対比JSONとして解釈できませんでした",
            "phase": "expert_squad_judge_failed",
            "errors": judge.errors,
            "raw_preview": (judge.raw or "")[:300],
            "expert_squad_meta": expert_meta,
        }, 502

    known_cit_ids = [c.get("id") for c in (meta or {}).get("citations", []) if c.get("id")]
    saved_docs = []
    resolved_log = []
    per_doc = split_multi_response(judge.parsed)
    for doc_id, doc_result in per_doc.items():
        resolved = _resolve_doc_id(doc_id, known_cit_ids)
        if resolved != doc_id:
            resolved_log.append(f"{doc_id} → {resolved}")
        _normalize_cited_locations_inplace(doc_result)
        with open(responses_dir / f"{resolved}.json", "w", encoding="utf-8") as f:
            json.dump(doc_result, f, ensure_ascii=False, indent=2)
        saved_docs.append(resolved)

    return {
        "success": True,
        "errors": judge_errors,
        "saved_docs": saved_docs,
        "num_docs": len(saved_docs),
        "resolved": resolved_log,
        "char_count": 0,
        "response_length": len(judge.raw or ""),
        "mode_used": mode,
        "fallback_to_legacy": False,
        "expert_squad_meta": expert_meta,
    }, 200


