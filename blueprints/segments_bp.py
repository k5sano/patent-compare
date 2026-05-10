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

bp = Blueprint("segments", __name__)

@bp.route("/case/<case_id>/segments", methods=["GET"])
def get_segments(case_id):
    data = load_json_file(case_id, "segments.json")
    if data is None:
        return jsonify({"error": "分節データがありません"}), 404
    return jsonify(data)


@bp.route("/case/<case_id>/segments", methods=["POST"])
def save_segments(case_id):
    from services.case_service import save_json_file
    data = request.get_json()
    # 分節編集が UI 表示に反映されない不具合の根治: 各 claim の full_text を
    # segments[].text の結合で再生成する。
    # full_text が古いまま (PDF 抽出時のノイズ込み) だと、対比表 UI の renderer が
    # claim.full_text を優先して表示し、編集が無視されているように見えていた。
    if isinstance(data, list):
        for c in data:
            if isinstance(c, dict) and isinstance(c.get("segments"), list):
                c["full_text"] = "".join(
                    (s.get("text") or "") for s in c["segments"]
                )
    save_json_file(case_id, "segments.json", data)
    return jsonify({"success": True})


@bp.route("/case/<case_id>/segments/freshness", methods=["GET"])
def segments_freshness(case_id):
    """現在の segments.json と responses/*.json の整合性チェック (silent stale 検出)"""
    from services.comparison_service import check_segments_freshness
    return _svc_response(check_segments_freshness(case_id))


@bp.route("/case/<case_id>/responses/prune-orphans", methods=["POST"])
def prune_orphan_comparisons_route(case_id):
    """全 response から「現 segments に無い requirement_id」のエントリを削除。"""
    from services.comparison_service import prune_orphan_comparisons
    return _svc_response(prune_orphan_comparisons(case_id))


# ===== キーワード =====

@bp.route("/case/<case_id>/segments/related", methods=["GET"])
def get_related_paragraphs(case_id):
    """キャッシュ済みの関連段落マッピングを返す"""
    data = load_json_file(case_id, "related_paragraphs.json") or {}
    return jsonify({"related": data})


@bp.route("/case/<case_id>/segments/related", methods=["POST"])
def compute_related_paragraphs_route(case_id):
    """分節に対して関連段落を検出・保存"""
    from services.case_service import compute_related_paragraphs
    return _svc_response(compute_related_paragraphs(case_id))

