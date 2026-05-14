#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Blueprint routes extracted from web.py."""
from __future__ import annotations

import json
import os
import re
import subprocess
import yaml
from pathlib import Path
from urllib.parse import urlencode
from flask import (
    Blueprint, current_app, render_template, request, redirect, url_for,
    flash, jsonify, send_file, Response
)

from services.case_service import (
    get_case_dir, load_case_meta, list_all_cases,
    load_json_file, find_citation_pdf,
)
from ._helpers import (
    PROJECT_ROOT, PDFXCHANGE_CANDIDATES, _launch_pdf_xchange,
    _open_with_pdf_xchange, _svc_response,
)

bp = Blueprint("comparison", __name__)

@bp.route("/case/<case_id>/prompt", methods=["POST"])
def generate_prompt_multi(case_id):
    from services.comparison_service import generate_prompt_multi
    data = request.get_json() or {}
    return _svc_response(generate_prompt_multi(case_id, data.get("citation_ids", [])))


@bp.route("/case/<case_id>/prompt/<citation_id>", methods=["GET"])
def generate_prompt_single(case_id, citation_id):
    from services.comparison_service import generate_prompt_single
    return _svc_response(generate_prompt_single(case_id, citation_id))


@bp.route("/case/<case_id>/response", methods=["POST"])
def save_response_multi(case_id):
    from services.comparison_service import save_response_multi
    return _svc_response(save_response_multi(case_id, request.get_json().get("text", "")))


@bp.route("/case/<case_id>/response/<citation_id>", methods=["POST"])
def save_response_single(case_id, citation_id):
    from services.comparison_service import save_response_single
    return _svc_response(save_response_single(case_id, citation_id, request.get_json().get("text", "")))


@bp.route("/case/<case_id>/response/<citation_id>", methods=["GET"])
def get_response(case_id, citation_id):
    from services.comparison_service import get_response
    return _svc_response(get_response(case_id, citation_id))


@bp.route("/case/<case_id>/response/<citation_id>/edit-cell", methods=["POST"])
def edit_comparison_cell(case_id, citation_id):
    """対比表セルを手動修正 (LLM 判定の上書き)。

    body: {
        target_kind: "comparison" | "sub_claim",
        target_key:  "1A" (req_id) or 7 (claim_number),
        fields: {judgment, judgment_reason, cited_location, cited_text}
    }
    """
    from services.comparison_service import update_comparison_cell
    body = request.get_json(silent=True) or {}
    return _svc_response(update_comparison_cell(
        case_id, citation_id,
        body.get("target_kind", "comparison"),
        body.get("target_key", ""),
        body.get("fields") or {},
    ))


@bp.route("/case/<case_id>/comparison/unmet-cells", methods=["GET"])
def comparison_unmet_cells(case_id):
    from services.comparison_service import list_unmet_cells
    citation_ids = request.args.getlist("citation_id")
    if not citation_ids:
        raw = (request.args.get("citation_ids") or "").strip()
        if raw:
            citation_ids = [x.strip() for x in raw.split(",") if x.strip()]
    return _svc_response(list_unmet_cells(case_id, citation_ids if citation_ids else None))


@bp.route("/case/<case_id>/comparison/<citation_id>/cell-context", methods=["GET"])
def comparison_cell_context(case_id, citation_id):
    from services.comparison_service import build_cell_context
    segment_id = (request.args.get("segment_id") or "").strip()
    target_kind = (request.args.get("target_kind") or "comparison").strip()
    if not segment_id:
        return jsonify({"error": "segment_id は必須です"}), 400
    return jsonify(build_cell_context(case_id, citation_id, segment_id, target_kind=target_kind))


@bp.route("/case/<case_id>/comparison/<citation_id>/chat", methods=["GET", "POST"])
def comparison_cell_chat(case_id, citation_id):
    from services.comparison_service import chat_cell, get_cell_chat_history
    if request.method == "GET":
        segment_id = (request.args.get("segment_id") or "").strip()
        target_kind = (request.args.get("target_kind") or "comparison").strip()
        if not segment_id:
            return jsonify({"error": "segment_id は必須です"}), 400
        return _svc_response(get_cell_chat_history(case_id, citation_id, segment_id, target_kind=target_kind))
    body = request.get_json(silent=True) or {}
    return _svc_response(chat_cell(
        case_id,
        citation_id,
        body.get("segment_id", ""),
        body.get("message", ""),
        model=body.get("model"),
        target_kind=body.get("target_kind", "comparison"),
    ))


@bp.route("/case/<case_id>/comparison/<citation_id>/judgment/override", methods=["POST"])
def comparison_judgment_override(case_id, citation_id):
    from services.comparison_service import apply_judgment_override
    body = request.get_json(silent=True) or {}
    return _svc_response(apply_judgment_override(
        case_id,
        citation_id,
        body.get("segment_id", ""),
        body.get("fields") or {},
        user_note=body.get("user_note", ""),
        chat_ref=body.get("chat_ref"),
        target_kind=body.get("target_kind", "comparison"),
    ))


@bp.route("/case/<case_id>/citation/<citation_id>/paragraph/<para_id>", methods=["GET"])
def get_citation_paragraph(case_id, citation_id, para_id):
    """対比結果で参照された段落 (例: 【0053】) の本文を返す。"""
    case_dir = get_case_dir(case_id)
    p = case_dir / "citations" / f"{citation_id}.json"
    if not p.is_file():
        return jsonify({"error": f"文献データが見つかりません: {citation_id}"}), 404
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.loads(f.read().replace("\x00", ""), strict=False)
    except (OSError, ValueError) as e:
        return jsonify({"error": f"文献データ読込エラー: {e}"}), 500

    fw2hw = str.maketrans("０１２３４５６７８９", "0123456789")
    wanted = (para_id or "").translate(fw2hw).strip().lstrip("0") or "0"
    for para in data.get("paragraphs", []) or []:
        pid = str(para.get("id", "")).translate(fw2hw).strip().lstrip("0") or "0"
        if pid == wanted:
            return jsonify({
                "id": str(para.get("id", "")),
                "page": para.get("page"),
                "section": para.get("section"),
                "text": para.get("text", ""),
            })
    return jsonify({"error": f"段落【{para_id}】は {citation_id} に存在しません"}), 404


@bp.route("/case/<case_id>/export/full-report", methods=["POST"])
def export_full_report(case_id):
    """完成版対比表 (本願解析 + 対比表 + 進歩性判断 の 3 タブ統合 Excel)"""
    from services.comparison_service import export_full_report
    body = request.get_json(silent=True) or {}
    selected = body.get("citation_ids")
    return _svc_response(export_full_report(case_id, selected_citation_ids=selected))


@bp.route("/case/<case_id>/export/excel", methods=["POST"])
def export_excel(case_id):
    from services.comparison_service import export_excel
    body = request.get_json(silent=True) or {}
    selected = body.get("citation_ids")  # None なら全件、リストなら絞り込み
    return _svc_response(export_excel(case_id, selected_citation_ids=selected))



@bp.route("/case/<case_id>/annotate/<citation_id>", methods=["POST"])
def annotate_citation(case_id, citation_id):
    from services.comparison_service import annotate_citation
    data = request.get_json(silent=True) or {}
    force = bool(data.get("force_new_file"))
    result, code = annotate_citation(case_id, citation_id, force_new_file=force)
    if code == 200 and result.get("success"):
        pdf_path = get_case_dir(case_id) / "output" / result["filename"]
        result["opened"] = _open_with_pdf_xchange(pdf_path)
    return jsonify(result), code


@bp.route("/case/<case_id>/annotate/all", methods=["POST"])
def annotate_all_citations(case_id):
    from services.comparison_service import annotate_all_citations
    result, code = annotate_all_citations(case_id)
    if code == 200:
        opened_count = 0
        case_out = get_case_dir(case_id) / "output"
        for r in result.get("results", []):
            if r.get("success") and r.get("filename"):
                if _open_with_pdf_xchange(case_out / r["filename"]):
                    opened_count += 1
        result["opened_count"] = opened_count
    return jsonify(result), code


def _open_with_pdf_xchange(pdf_path):
    """PDFをPDF-XChange Editorで開く。成功ならTrue。"""
    p = Path(pdf_path)
    if not p.exists():
        return False
    exe = next((c for c in PDFXCHANGE_CANDIDATES if Path(c).exists()), None)
    try:
        if exe:
            subprocess.Popen([exe, str(p)], close_fds=True)
        else:
            os.startfile(str(p))
        return True
    except Exception:
        return False


@bp.route("/case/<case_id>/execute", methods=["POST"])
def compare_execute(case_id):
    from services.comparison_service import compare_execute
    data = request.get_json() or {}
    return _svc_response(compare_execute(
        case_id,
        data.get("citation_ids", []),
        model=data.get("model"),
        mode=data.get("mode") or "requirement_first",  # A: デフォルト切替
        effort=data.get("effort"),
    ))


# ===== 進歩性 =====

@bp.route("/case/<case_id>/inventive-step/prompt", methods=["POST"])
def inventive_step_prompt(case_id):
    from services.comparison_service import inventive_step_prompt
    return _svc_response(inventive_step_prompt(case_id))


@bp.route("/case/<case_id>/inventive-step/response", methods=["POST"])
def inventive_step_response(case_id):
    from services.comparison_service import inventive_step_response
    return _svc_response(inventive_step_response(case_id, request.get_json().get("text", "")))


@bp.route("/case/<case_id>/inventive-step/execute", methods=["POST"])
def inventive_step_execute(case_id):
    from services.comparison_service import inventive_step_execute
    body = request.get_json(silent=True) or {}
    return _svc_response(inventive_step_execute(
        case_id, model=body.get("model"), effort=body.get("effort")
    ))


# ===== API =====

