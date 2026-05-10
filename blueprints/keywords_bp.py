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

bp = Blueprint("keywords", __name__)

@bp.route("/case/<case_id>/keywords", methods=["GET"])
def get_keywords(case_id):
    from services.keyword_service import get_keywords
    return _svc_response(get_keywords(case_id))


@bp.route("/case/<case_id>/keywords/add", methods=["POST"])
def add_keyword(case_id):
    from services.keyword_service import add_keyword
    data = request.get_json() or {}
    return _svc_response(add_keyword(case_id, data.get("group_id"), data.get("term", "")))


@bp.route("/case/<case_id>/keywords/delete", methods=["POST"])
def delete_keyword(case_id):
    from services.keyword_service import delete_keyword
    data = request.get_json() or {}
    return _svc_response(delete_keyword(case_id, data.get("group_id"), data.get("term", "")))


@bp.route("/case/<case_id>/keywords/edit", methods=["POST"])
def edit_keyword(case_id):
    from services.keyword_service import edit_keyword
    data = request.get_json() or {}
    return _svc_response(edit_keyword(case_id, data.get("group_id"), data.get("old_term"), data.get("new_term")))


@bp.route("/case/<case_id>/keywords/suggest", methods=["POST"])
def suggest_keywords(case_id):
    from services.keyword_service import suggest_keywords
    body = request.get_json(silent=True) or {}
    return _svc_response(suggest_keywords(case_id, model=body.get("model")))


@bp.route("/case/<case_id>/keywords/suggest-by-segment", methods=["POST"])
def suggest_keywords_by_segment(case_id):
    from services.keyword_service import suggest_keywords_by_segment
    return _svc_response(suggest_keywords_by_segment(case_id))


@bp.route("/case/<case_id>/keywords/group/add", methods=["POST"])
def add_keyword_group(case_id):
    from services.keyword_service import add_keyword_group
    data = request.get_json() or {}
    return _svc_response(add_keyword_group(case_id, data.get("label", ""), data.get("segment_ids", [])))


@bp.route("/case/<case_id>/keywords/group/delete", methods=["POST"])
def delete_keyword_group(case_id):
    from services.keyword_service import delete_keyword_group
    data = request.get_json() or {}
    return _svc_response(delete_keyword_group(case_id, data.get("group_id")))


@bp.route("/case/<case_id>/keywords/group/update", methods=["POST"])
def update_keyword_group(case_id):
    from services.keyword_service import update_keyword_group
    data = request.get_json() or {}
    return _svc_response(update_keyword_group(case_id, data.get("group_id"), data))


@bp.route("/case/<case_id>/keywords/rebuild-from-tech-analysis", methods=["POST"])
def rebuild_keywords_from_tech_analysis_route(case_id):
    from services.keyword_service import rebuild_keywords_from_tech_analysis
    return _svc_response(rebuild_keywords_from_tech_analysis(case_id))


@bp.route("/case/<case_id>/keywords/tech-analysis-candidates", methods=["GET"])
def tech_analysis_candidates_route(case_id):
    """Step 4 Stage 1 の各 element からキーワード候補を element 単位で返す。
    UI で表示してユーザーがチェックボックスで選別する。"""
    from services.keyword_service import get_tech_analysis_candidates
    return _svc_response(get_tech_analysis_candidates(case_id))


@bp.route("/case/<case_id>/keywords/add-from-tech-analysis", methods=["POST"])
def add_from_tech_analysis_route(case_id):
    """ユーザーが選択した tech_analysis 候補を既存グループに追記。"""
    from services.keyword_service import add_tech_analysis_keywords
    body = request.get_json(silent=True) or {}
    return _svc_response(add_tech_analysis_keywords(case_id, body.get("selections")))


@bp.route("/case/<case_id>/keywords/reassign-to-tech-analysis", methods=["POST"])
def reassign_keywords_to_tech_analysis_route(case_id):
    """旧API互換: Step 4 技術構造化項目を正として keywords.json を再構築する。"""
    from services.keyword_service import reassign_keywords_to_tech_analysis
    return _svc_response(reassign_keywords_to_tech_analysis(case_id))


@bp.route("/case/<case_id>/keywords/prune-segment-ids", methods=["POST"])
def prune_keyword_segment_ids_route(case_id):
    """segments.json に存在しない古い segment_id を keywords.json から削除
    (請求項補正で 1A〜1G が 1A〜1E に縮約された後の整合化)。"""
    from services.keyword_service import prune_keyword_segment_ids
    return _svc_response(prune_keyword_segment_ids(case_id))


@bp.route("/case/<case_id>/keywords/search-hints/preview", methods=["GET"])
def search_hints_preview_route(case_id):
    """予備検索ヒント (hongan_analysis 7.2/7.3/7.4) の構造化結果を返す (適用前確認用)。"""
    from services.search_hints_service import parse_search_hints
    return _svc_response(parse_search_hints(case_id))


@bp.route("/case/<case_id>/keywords/search-hints/apply", methods=["POST"])
def search_hints_apply_route(case_id):
    """予備検索ヒントを Step 3 のキーワードグループに反映 (同義語追加 + 分類コード振り分け)。"""
    from services.search_hints_service import apply_search_hints_to_keywords
    return _svc_response(apply_search_hints_to_keywords(case_id))


@bp.route("/case/<case_id>/search-formula/build", methods=["GET"])
def search_formula_build_route(case_id):
    """検索式自動生成 (Phase C)。

    Query params:
        level: 'l0' (default)
        include_main_fterm: '1' なら L0 にメイン F-term も AND 結合
    """
    from services.search_formula_builder import build_l0
    level = (request.args.get("level") or "l0").lower()
    include_ft = request.args.get("include_main_fterm") in ("1", "true", "yes")
    if level == "l0":
        return _svc_response(build_l0(case_id, include_main_fterm=include_ft))
    return _svc_response(({"error": f"未対応の level: {level}"}, 400))


@bp.route("/case/<case_id>/keywords/fterm/candidates", methods=["GET"])
def fterm_candidates(case_id):
    from services.keyword_service import fterm_candidates
    return _svc_response(fterm_candidates(case_id))


@bp.route("/case/<case_id>/keywords/fi/candidates", methods=["GET"])
def fi_candidates(case_id):
    from services.keyword_service import fi_candidates
    return _svc_response(fi_candidates(case_id))



@bp.route("/case/<case_id>/keywords/fterm/add", methods=["POST"])
def add_fterm(case_id):
    from services.keyword_service import add_fterm
    data = request.get_json() or {}
    return _svc_response(add_fterm(case_id, data.get("group_id"), data.get("code"), data.get("desc", "")))


@bp.route("/case/<case_id>/keywords/fterm/delete", methods=["POST"])
def delete_fterm(case_id):
    from services.keyword_service import delete_fterm
    data = request.get_json() or {}
    return _svc_response(delete_fterm(case_id, data.get("group_id"), data.get("code")))


@bp.route("/case/<case_id>/keywords/fi/add", methods=["POST"])
def add_fi(case_id):
    from services.keyword_service import add_fi
    data = request.get_json() or {}
    return _svc_response(add_fi(case_id, data.get("group_id"), data.get("code"), data.get("desc", "")))


@bp.route("/case/<case_id>/keywords/fi/delete", methods=["POST"])
def delete_fi(case_id):
    from services.keyword_service import delete_fi
    data = request.get_json() or {}
    return _svc_response(delete_fi(case_id, data.get("group_id"), data.get("code")))


@bp.route("/case/<case_id>/keywords/segments", methods=["GET"])
def get_segment_keywords(case_id):
    data = load_json_file(case_id, "segment_keywords.json")
    if data is None:
        return jsonify({"error": "分節別キーワードがありません。先に提案を実行してください。"}), 404
    return jsonify(data)


@bp.route("/case/<case_id>/keywords/add-to-segment", methods=["POST"])
def add_keyword_to_segment(case_id):
    from services.keyword_service import add_keyword_to_segment
    data = request.get_json() or {}
    return _svc_response(add_keyword_to_segment(case_id, data.get("segment_id"), data.get("term")))


@bp.route("/case/<case_id>/keywords/remove-from-segment", methods=["POST"])
def remove_keyword_from_segment(case_id):
    from services.keyword_service import remove_keyword_from_segment
    data = request.get_json() or {}
    return _svc_response(remove_keyword_from_segment(case_id, data.get("segment_id"), data.get("term")))


@bp.route("/case/<case_id>/keywords/update-segment-keyword", methods=["POST"])
def update_segment_keyword(case_id):
    from services.keyword_service import update_segment_keyword
    data = request.get_json() or {}
    return _svc_response(update_segment_keyword(case_id, data.get("segment_id"), data.get("old_term"), data.get("new_term")))


# ===== 対比・プロンプト・回答・Excel =====

