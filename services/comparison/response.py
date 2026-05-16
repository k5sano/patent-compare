#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""対比回答の保存・更新。"""
from __future__ import annotations

import json
import re

from services.case_service import get_case_dir
from services.comparison.prompt import _get_all_segment_ids, _resolve_doc_id
def save_response_multi(case_id, raw_text):
    """複数文献対応の回答パース・保存。

    LLM の ``document_id`` は表記揺れ (例: ``US 2013/0040869`` vs ``US20130040869``) が
    起こりやすく、そのままだと ``responses/<id>.json`` のファイル名が
    case.yaml の citation id とズレて UI に取り込まれない。
    ``_resolve_doc_id`` で登録済 citation_id へ正規化してから保存する。
    """
    from modules.response_parser import parse_response, split_multi_response

    case_dir = get_case_dir(case_id)
    segments_path = case_dir / "segments.json"

    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    if not raw_text.strip():
        return {"error": "テキストが空です"}, 400

    result, errors = parse_response(raw_text, _get_all_segment_ids(segs))

    # 登録済 citation_id を取得 (LLM の document_id 揺れを吸収するため)
    meta = load_case_meta(case_id) or {}
    known_cit_ids = [c.get("id") for c in meta.get("citations", []) if c.get("id")]

    saved_docs = []
    skipped = []  # 解決不能なもの (デバッグ用)
    if result:
        per_doc = split_multi_response(result)
        for doc_id, doc_result in per_doc.items():
            resolved = _resolve_doc_id(doc_id, known_cit_ids)
            if resolved != doc_id:
                # マッピングが行われた旨を errors に warn として残す
                errors = (errors or []) + [
                    f"document_id '{doc_id}' を登録済 '{resolved}' にマッピング"
                ]
            if known_cit_ids and resolved not in known_cit_ids:
                # どの citation_id にも対応付けできなかった
                skipped.append(doc_id)
                # それでもファイルとしては保存 (運用上の保険)
            resp_path = case_dir / "responses" / f"{resolved}.json"
            # 保存時に document_id も正規化して書き戻す (UI が citId 引き当てに使う)
            if isinstance(doc_result, dict):
                doc_result["document_id"] = resolved
            _normalize_cited_locations_inplace(doc_result)
            with open(resp_path, "w", encoding="utf-8") as f:
                json.dump(doc_result, f, ensure_ascii=False, indent=2)
            saved_docs.append(resolved)

    payload = {
        "success": result is not None,
        "errors": errors,
        "saved_docs": saved_docs,
        "num_docs": len(saved_docs),
    }
    if skipped:
        payload["unresolved_doc_ids"] = skipped
    return payload, 200


def save_response_single(case_id, citation_id, raw_text):
    """単一文献の回答パース"""
    from modules.response_parser import parse_response

    case_dir = get_case_dir(case_id)
    segments_path = case_dir / "segments.json"

    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    if not raw_text.strip():
        return {"error": "テキストが空です"}, 400

    result, errors = parse_response(raw_text, _get_all_segment_ids(segs))

    if result:
        _normalize_cited_locations_inplace(result)
        resp_path = case_dir / "responses" / f"{citation_id}.json"
        with open(resp_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    return {
        "success": result is not None,
        "errors": errors,
        "data": result,
    }, 200


def _normalize_cited_locations_inplace(data):
    """LLM 応答の cited_location を正規化して in-place で上書き。

    保存時に呼んでファイル上に正規化済み記法を残す (例: ``0023;0024`` →
    ``23,24``、`T1;T2` → `T1,2`)。読み込み側 (_decorate_comparison_with_notation)
    でも normalize するが、ファイル上を整えておくとコピペ用途で素直になる。
    """
    from modules.cited_ref_notation import normalize as _norm
    if not isinstance(data, dict):
        return data
    for key in ("comparisons", "sub_claims"):
        for item in (data.get(key) or []):
            if isinstance(item, dict) and item.get("cited_location"):
                try:
                    item["cited_location"] = _norm(item["cited_location"])
                except Exception:
                    pass
    return data


def _decorate_comparison_with_notation(data):
    """``cited_location`` 記法を正規化 + 展開して comparison/sub_claims に注入する。

    UI 側で記法を毎回パースせず済むよう、サーバで以下を補強:
      - ``cited_location``: LLM 出力ゆれ (例: ``0023;0024`` `T1;T2`) を正規化
        (``23,24`` `T1,2`)。raw 出力もこの値で上書きする。
      - ``cited_location_expanded``: 日本語展開済み (備考は含めない)
      - ``cited_location_comment``: コメント部分のみ ("..." 以降)
      - ``judgment_display``: ○ は "" に正規化済み (△/× はそのまま)
    """
    from modules.cited_ref_notation import (
        comment_of, display_judgment, expand, normalize,
    )

    def _decorate(comp):
        if not isinstance(comp, dict):
            return comp
        loc = comp.get("cited_location") or ""
        if loc:
            try:
                normalized = normalize(loc)
            except Exception:
                normalized = loc
            # 正規化結果が元と異なれば上書き (LLM 出力ゆれを補正)
            comp["cited_location"] = normalized
            comp["cited_location_expanded"] = expand(normalized, with_comment=False)
            comp["cited_location_comment"] = comment_of(normalized)
        else:
            comp["cited_location_expanded"] = ""
            comp["cited_location_comment"] = ""
        comp["judgment_display"] = display_judgment(comp.get("judgment", ""))
        return comp

    if not isinstance(data, dict):
        return data
    for c in data.get("comparisons", []) or []:
        _decorate(c)
    for s in data.get("sub_claims", []) or []:
        _decorate(s)
    return data


def get_response(case_id, citation_id):
    case_dir = get_case_dir(case_id)
    resp_path = case_dir / "responses" / f"{citation_id}.json"
    if not resp_path.exists():
        return {"error": "回答データがありません"}, 404
    with open(resp_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _decorate_comparison_with_notation(data), 200


def prune_orphan_comparisons(case_id):
    """全 response から「現 segments に無い requirement_id」のエントリを削除。

    補正で消えた旧分節 (1F, 1G 等) の判定が response 内に残ったまま、
    UI に表示できない死にデータになっているケースの掃除。
    再対比は走らせない (純粋にゴミ消し)。

    Returns:
        ({success, removed_total, removed_per_doc, valid_segment_ids}, status)
    """
    case_dir = get_case_dir(case_id)
    seg_path = case_dir / "segments.json"
    if not seg_path.exists():
        return {"error": "segments.json がありません"}, 400
    try:
        with open(seg_path, "r", encoding="utf-8") as f:
            segs = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {"error": f"segments.json 読み込み失敗: {e}"}, 500

    valid_ids = set(_get_all_segment_ids(segs))

    resp_dir = case_dir / "responses"
    if not resp_dir.exists():
        return {"success": True, "removed_total": 0,
                "removed_per_doc": {}, "valid_segment_ids": sorted(valid_ids)}, 200

    removed_per_doc: dict[str, list[str]] = {}
    removed_total = 0
    for p in resp_dir.glob("*.json"):
        if p.name.startswith("_"):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                rdata = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        comps = rdata.get("comparisons") or []
        kept = []
        removed_here = []
        for c in comps:
            rid = (c.get("requirement_id") or "").strip()
            if rid and rid not in valid_ids:
                removed_here.append(rid)
                continue
            kept.append(c)
        if removed_here:
            rdata["comparisons"] = kept
            with open(p, "w", encoding="utf-8") as f:
                json.dump(rdata, f, ensure_ascii=False, indent=2)
            removed_per_doc[p.stem] = removed_here
            removed_total += len(removed_here)

    return {
        "success": True,
        "removed_total": removed_total,
        "removed_per_doc": removed_per_doc,
        "valid_segment_ids": sorted(valid_ids),
    }, 200


def update_comparison_cell(case_id, citation_id, target_kind, target_key, fields):
    """対比表セルを手動修正する。

    Args:
        case_id: 案件 ID
        citation_id: 引例 ID (responses/<citation_id>.json)
        target_kind: "comparison" (構成要件) or "sub_claim" (従属請求項)
        target_key: comparison なら requirement_id (例 "1A"), sub_claim なら claim_number (int)
        fields: 更新する辞書 {judgment, judgment_reason, cited_location, cited_text}
                指定されたキーのみ更新 (None / 未指定はスキップ)

    Returns:
        ({success, updated, edited_at, doc}, status)
    """
    case_dir = get_case_dir(case_id)
    resp_path = case_dir / "responses" / f"{citation_id}.json"
    if not resp_path.exists():
        return {"error": f"回答データがありません: {citation_id}"}, 404
    try:
        with open(resp_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {"error": f"読み込み失敗: {e}"}, 500

    target = None
    if target_kind == "comparison":
        for c in data.get("comparisons") or []:
            if str(c.get("requirement_id")) == str(target_key):
                target = c
                break
    elif target_kind == "sub_claim":
        try:
            tk = int(target_key)
        except (TypeError, ValueError):
            return {"error": f"sub_claim は claim_number(int) が必要: {target_key}"}, 400
        for c in data.get("sub_claims") or []:
            if int(c.get("claim_number") or -1) == tk:
                target = c
                break
    else:
        return {"error": f"target_kind は 'comparison' または 'sub_claim': {target_kind}"}, 400

    if target is None:
        return {"error": f"対象が見つかりません: {target_kind} {target_key}"}, 404

    allowed = ("judgment", "judgment_reason", "cited_location", "cited_text")
    updated = {}
    for k in allowed:
        if k in fields and fields[k] is not None:
            target[k] = str(fields[k]) if not isinstance(fields[k], str) else fields[k]
            if k == "cited_location" and target[k]:
                try:
                    from modules.cited_ref_notation import normalize as _norm
                    target[k] = _norm(target[k])
                except Exception:
                    pass
            updated[k] = target[k]

    # 手動編集の証跡 (UI バッジ表示用)
    from datetime import datetime as _dt
    edited_at = _dt.now().strftime("%Y-%m-%dT%H:%M:%S")
    target["_edited_at"] = edited_at
    target["_edited_by"] = "user"

    with open(resp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return {
        "success": True,
        "updated": updated,
        "edited_at": edited_at,
        "doc": _decorate_comparison_with_notation(data),
    }, 200


