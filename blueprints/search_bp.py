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

bp = Blueprint("search", __name__)

@bp.route("/case/<case_id>/hongan/classification/fetch", methods=["POST"])
def fetch_hongan_classification(case_id):
    """本願の公開番号で J-PlatPat に問い合わせて書誌情報 (IPC/FI/Fターム/テーマ) を取得・保存"""
    from services.case_service import fetch_hongan_classification_from_jplatpat
    return _svc_response(fetch_hongan_classification_from_jplatpat(case_id))


@bp.route("/case/<case_id>/dossier/opd/open", methods=["POST"])
def opd_dossier_open(case_id):
    """本願のJ-PlatPat固定URLからOPD画面を可視ブラウザで開く。"""
    from services.opd_dossier_service import get_session
    sess = get_session()
    result = sess.open(case_id, timeout=70)
    return jsonify(result), (200 if result.get("ok") else 400)


@bp.route("/case/<case_id>/dossier/opd/collect", methods=["POST"])
def opd_dossier_collect(case_id):
    """現在のOPD画面から対象書類候補を収集して dossier/opd_index.json に保存する。"""
    from services.opd_dossier_service import get_session
    sess = get_session()
    result = sess.collect(case_id, timeout=60)
    return jsonify(result), (200 if result.get("ok") else 400)


@bp.route("/case/<case_id>/dossier/opd/index", methods=["GET"])
def opd_dossier_index(case_id):
    from services.opd_dossier_service import load_opd_index
    return _svc_response(load_opd_index(case_id))


@bp.route("/case/<case_id>/dossier/opd/citation-candidates", methods=["GET"])
def opd_dossier_citation_candidates(case_id):
    from services.opd_dossier_service import extract_citation_candidates
    return _svc_response(extract_citation_candidates(case_id))


@bp.route("/case/<case_id>/dossier/opd/ocr/rebuild", methods=["POST"])
def opd_dossier_ocr_rebuild(case_id):
    from services.opd_dossier_service import rebuild_ocr_reports
    return _svc_response(rebuild_ocr_reports(case_id))


@bp.route("/case/<case_id>/dossier/opd/rejections", methods=["GET"])
def opd_dossier_rejections(case_id):
    from services.opd_dossier_service import get_rejection_documents
    return _svc_response(get_rejection_documents(case_id))


@bp.route("/case/<case_id>/dossier/opd/rejections/summarize", methods=["POST"])
def opd_dossier_rejections_summarize(case_id):
    from services.opd_dossier_service import summarize_rejection_documents
    data = request.get_json(silent=True) or {}
    return _svc_response(summarize_rejection_documents(
        case_id,
        model=data.get("model"),
        force=bool(data.get("force")),
    ))


@bp.route("/case/<case_id>/dossier/opd/rejections/download", methods=["POST"])
def opd_dossier_rejections_download(case_id):
    from services.opd_dossier_service import get_session
    data = request.get_json(silent=True) or {}
    sess = get_session()
    result = sess.download_rejection_pdfs(
        case_id,
        target_indices=data.get("target_indices"),
        timeout=180,
    )
    return jsonify(result), (200 if result.get("ok") else 400)


@bp.route("/case/<case_id>/dossier/opd/rejections/upload", methods=["POST"])
def opd_dossier_rejections_upload(case_id):
    from services.opd_dossier_service import ingest_opd_pdf_file
    if "file" not in request.files:
        return jsonify({"error": "ファイルがありません"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "ファイル名が空です"}), 400
    case_dir = get_case_dir(case_id)
    tmp_dir = case_dir / "dossier" / "_upload_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    save_path = tmp_dir / file.filename
    file.save(str(save_path))
    return _svc_response(ingest_opd_pdf_file(
        case_id,
        save_path,
        label=request.form.get("label", file.filename),
        kind=request.form.get("kind", ""),
    ))


@bp.route("/case/<case_id>/dossier/opd/status", methods=["GET"])
def opd_dossier_status(case_id):
    from services.opd_dossier_service import get_session
    return jsonify(get_session().status())


@bp.route("/case/<case_id>/dossier/opd/close", methods=["POST"])
def opd_dossier_close(case_id):
    from services.opd_dossier_service import reset_session
    reset_session()
    return jsonify({"ok": True})


@bp.route("/api/claude-status")
def claude_status():
    from modules.claude_client import llm_status
    status = llm_status()
    return jsonify(status)


@bp.route("/api/serpapi-key", methods=["POST"])
def set_serpapi_key():
    data = request.get_json() or {}
    key = (data.get("key") or "").strip()

    config_path = PROJECT_ROOT / "config.yaml"
    cfg = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    cfg["serpapi_key"] = key
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
    return jsonify({"success": True})


# ===== 検索 =====

@bp.route("/case/<case_id>/search/prompt", methods=["POST"])
def search_prompt(case_id):
    from services.search_service import search_prompt
    return _svc_response(search_prompt(case_id))


@bp.route("/case/<case_id>/search/response", methods=["POST"])
def search_response(case_id):
    from services.search_service import search_response
    return _svc_response(search_response(case_id, request.get_json().get("text", "")))


@bp.route("/case/<case_id>/search/download", methods=["POST"])
def search_download(case_id):
    from services.search_service import search_download
    data = request.get_json() or {}
    patent_ids = data.get("patent_ids", [])
    if not patent_ids and data.get("patent_id"):
        patent_ids = [data["patent_id"]]
    return _svc_response(search_download(case_id, patent_ids, data.get("role", "主引例")))


@bp.route("/case/<case_id>/citation/jplatpat-download", methods=["POST"])
def jplatpat_download_citation(case_id):
    from services.jplatpat_pdf_service import download_citation_pdf_from_jplatpat
    data = request.get_json() or {}
    return _svc_response(download_citation_pdf_from_jplatpat(
        case_id,
        data.get("patent_id", ""),
        data.get("role", "主引例"),
    ))


@bp.route("/case/<case_id>/search/execute", methods=["POST"])
def search_execute(case_id):
    from services.search_service import search_execute
    body = request.get_json(silent=True) or {}
    return _svc_response(search_execute(case_id, model=body.get("model")))


@bp.route("/case/<case_id>/search/presearch/prompt", methods=["POST"])
def presearch_prompt(case_id):
    from services.search_service import presearch_prompt
    return _svc_response(presearch_prompt(case_id))


@bp.route("/case/<case_id>/search/presearch/parse", methods=["POST"])
def presearch_parse(case_id):
    from services.search_service import presearch_parse
    return _svc_response(presearch_parse(case_id, (request.get_json() or {}).get("text", "")))


@bp.route("/case/<case_id>/search/classify/prompt", methods=["POST"])
def classify_prompt(case_id):
    from services.search_service import classify_prompt
    return _svc_response(classify_prompt(case_id))


@bp.route("/case/<case_id>/search/classify/parse", methods=["POST"])
def classify_parse(case_id):
    from services.search_service import classify_parse
    return _svc_response(classify_parse(case_id, (request.get_json() or {}).get("text", "")))


@bp.route("/case/<case_id>/search/keywords/prompt", methods=["POST"])
def keyword_dict_prompt(case_id):
    from services.search_service import keyword_dict_prompt
    return _svc_response(keyword_dict_prompt(case_id))


@bp.route("/case/<case_id>/search/keywords/parse", methods=["POST"])
def keyword_dict_parse(case_id):
    from services.search_service import keyword_dict_parse
    return _svc_response(keyword_dict_parse(case_id, (request.get_json() or {}).get("text", "")))


@bp.route("/case/<case_id>/search/status", methods=["GET"])
def search_status(case_id):
    from services.search_service import search_status
    return _svc_response(search_status(case_id))


@bp.route("/case/<case_id>/search/data/<filename>", methods=["GET"])
def get_search_data(case_id, filename):
    from services.search_service import get_search_data_file
    return _svc_response(get_search_data_file(case_id, filename))


@bp.route("/case/<case_id>/search/stage-execute", methods=["POST"])
def stage_execute(case_id):
    from services.search_service import stage_execute
    data = request.get_json() or {}
    return _svc_response(stage_execute(case_id, data.get("stage"), model=data.get("model")))


@bp.route("/case/<case_id>/search/stage-execute-stream", methods=["POST"])
def stage_execute_stream(case_id):
    from services.search_service import stage_execute_stream
    body = request.get_json(silent=True) or {}
    result = stage_execute_stream(case_id, model=body.get("model"))
    if isinstance(result, tuple):
        data, code = result
        return jsonify(data), code
    # result is a generator
    return Response(result, mimetype="application/x-ndjson")


# ===== ISR / 書面意見 取り込み =====

@bp.route("/case/<case_id>/search-report/list", methods=["GET"])
def search_report_list(case_id):
    from services.search_report_service import list_reports
    return _svc_response(list_reports(case_id))


@bp.route("/case/<case_id>/search-report/upload", methods=["POST"])
def search_report_upload(case_id):
    from services.search_report_service import upload_report

    if "file" not in request.files:
        return jsonify({"error": "ファイルがありません"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "ファイルが選択されていません"}), 400

    case_dir = get_case_dir(case_id)
    tmp_dir = case_dir / "search_reports"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    save_path = tmp_dir / file.filename
    file.save(str(save_path))

    return _svc_response(upload_report(case_id, save_path, file.filename))


@bp.route("/case/<case_id>/search-report/<path:filename>", methods=["DELETE"])
def search_report_delete(case_id, filename):
    from services.search_report_service import delete_report
    return _svc_response(delete_report(case_id, filename))


@bp.route("/case/<case_id>/search-report/<path:filename>/summarize", methods=["POST"])
def search_report_summarize(case_id, filename):
    from services.search_report_service import summarize_box_v
    body = request.get_json(silent=True) or {}
    model = body.get("model") or request.args.get("model")
    return _svc_response(summarize_box_v(case_id, filename, model=model))


@bp.route("/case/<case_id>/search-report/<path:filename>/fetch", methods=["POST"])
def search_report_fetch(case_id, filename):
    from services.search_report_service import fetch_cited_documents
    data = request.get_json() or {}
    nums = data.get("nums", [])
    return _svc_response(fetch_cited_documents(case_id, filename, nums))


@bp.route("/case/<case_id>/search-report/<path:filename>/pdf")
def search_report_pdf(case_id, filename):
    pdf_path = get_case_dir(case_id) / "search_reports" / filename
    if not pdf_path.exists():
        return jsonify({"error": "PDFが見つかりません"}), 404
    return send_file(str(pdf_path), mimetype="application/pdf", as_attachment=False)


# ===== J-PlatPat 検索ラン (Step 4.5) =====

