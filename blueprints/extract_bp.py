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

bp = Blueprint("extract", __name__)

@bp.route("/case/<case_id>/upload/hongan", methods=["POST"])
def upload_hongan(case_id):
    from services.case_service import upload_hongan as _upload

    if "file" not in request.files:
        return jsonify({"error": "ファイルがありません"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "ファイルが選択されていません"}), 400

    case_dir = get_case_dir(case_id)
    save_path = case_dir / "input" / file.filename
    file.save(str(save_path))

    return _svc_response(_upload(case_id, save_path))


@bp.route("/case/<case_id>/upload/citation", methods=["POST"])
def upload_citation(case_id):
    from services.case_service import upload_citation as _upload

    meta = load_case_meta(case_id)
    if not meta:
        return jsonify({"error": "案件が見つかりません"}), 404
    if "file" not in request.files:
        return jsonify({"error": "ファイルがありません"}), 400

    file = request.files["file"]
    case_dir = get_case_dir(case_id)
    save_path = case_dir / "input" / file.filename
    file.save(str(save_path))

    return _svc_response(_upload(
        case_id, save_path,
        role=request.form.get("role", "主引例"),
        label=request.form.get("label", ""),
    ))


@bp.route("/case/<case_id>/citation/<citation_id>", methods=["DELETE"])
def delete_citation(case_id, citation_id):
    from services.case_service import delete_citation as _delete
    return _svc_response(_delete(case_id, citation_id))


@bp.route("/case/<case_id>/citations/clear", methods=["DELETE"])
def clear_all_citations(case_id):
    from services.case_service import clear_all_citations as _clear
    return _svc_response(_clear(case_id))


# ===== 分節 =====

@bp.route("/case/<case_id>/hongan/refs/extract", methods=["GET"])
def extract_hongan_refs(case_id):
    """本願明細書から 【特許文献N】 を抽出して一覧を返す"""
    from services.case_service import extract_hongan_citations
    return _svc_response(extract_hongan_citations(case_id))


@bp.route("/case/<case_id>/hongan/refs/download", methods=["POST"])
def download_hongan_refs(case_id):
    """抽出した本願引用を Google Patents から DL → citation 登録"""
    from services.case_service import download_and_register_hongan_refs
    data = request.get_json(silent=True) or {}
    ref_nos = data.get("ref_nos")  # null = 全件
    return _svc_response(download_and_register_hongan_refs(case_id, ref_nos=ref_nos))


@bp.route("/case/<case_id>/hongan/bookmark", methods=["POST"])
def bookmark_hongan(case_id):
    """本願PDFにブックマークを付与した新PDFを作成し、PDF-XChangeで開く"""
    from services.case_service import create_bookmarked_hongan
    result, code = create_bookmarked_hongan(case_id)
    if code == 200 and result.get("success"):
        pdf_path = Path(result.get("path", ""))
        opened = False
        if pdf_path.exists():
            exe = next((p for p in PDFXCHANGE_CANDIDATES if Path(p).exists()), None)
            try:
                if exe:
                    subprocess.Popen([exe, str(pdf_path)], close_fds=True)
                else:
                    os.startfile(str(pdf_path))
                opened = True
            except Exception:
                opened = False
        result["opened"] = opened
    return jsonify(result), code


@bp.route("/case/<case_id>/hongan/extract-tables", methods=["POST"])
def extract_hongan_tables(case_id):
    """本願 PDF から実施例表を Vision で抽出 (SSE で進捗配信)。

    クライアント側は EventSource で接続し data: <json> を順次受信。
    最終 stage="done" イベントの後、GET /case/<id>/hongan/tables で全文取得する。
    """
    from services.case_service import stream_hongan_table_extraction
    body = request.get_json(silent=True) or {}
    model = body.get("model") or request.args.get("model") or "sonnet"
    return Response(
        stream_hongan_table_extraction(case_id, model=model),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@bp.route("/case/<case_id>/hongan/tables", methods=["GET"])
def get_hongan_tables_route(case_id):
    """抽出済みの本願表データを返す。"""
    from services.case_service import get_hongan_tables
    result, code = get_hongan_tables(case_id)
    return jsonify(result), code


@bp.route("/case/<case_id>/citation/<path:citation_id>/extract-tables",
           methods=["POST"])
def extract_citation_tables_route(case_id, citation_id):
    """1 件の引用文献から表抽出 (SSE で進捗配信)。"""
    from services.case_service import stream_citation_table_extraction
    body = request.get_json(silent=True) or {}
    force = bool(body.get("force"))
    model = body.get("model") or "sonnet"
    return Response(
        stream_citation_table_extraction(case_id, citation_id, model=model, force=force),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@bp.route("/case/<case_id>/citations/extract-tables-bulk", methods=["POST"])
def extract_citation_tables_bulk_route(case_id):
    """複数の引用文献を順次抽出 (SSE)。
    body: {"citation_ids": ["JP2024-051653", ...], "force": false, "model": "sonnet"}
    """
    from services.case_service import stream_bulk_citation_table_extraction
    body = request.get_json(silent=True) or {}
    cids = body.get("citation_ids") or []
    force = bool(body.get("force"))
    model = body.get("model") or "sonnet"
    if not cids:
        return jsonify({"error": "citation_ids が空です"}), 400
    return Response(
        stream_bulk_citation_table_extraction(case_id, cids, model=model, force=force),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@bp.route("/case/<case_id>/citation/<path:citation_id>/tables", methods=["GET"])
def get_citation_tables_route(case_id, citation_id):
    """1 件の引用文献の抽出済み表データを返す。"""
    from services.case_service import get_citation_tables
    result, code = get_citation_tables(case_id, citation_id)
    return jsonify(result), code


@bp.route("/case/<case_id>/citations/tables-status", methods=["GET"])
def get_citation_tables_status_route(case_id):
    """全引用文献の表抽出状況を返す。"""
    from services.case_service import list_citation_table_status
    result, code = list_citation_table_status(case_id)
    return jsonify(result), code


@bp.route("/case/<case_id>/citations/tables-cells", methods=["GET"])
def get_citation_tables_cells_route(case_id):
    """全引用文献の抽出済みセル文字列マップを返す (PKM ヒット集計用)。"""
    from services.case_service import get_citation_tables_cells
    result, code = get_citation_tables_cells(case_id)
    return jsonify(result), code

