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

bp = Blueprint("chat_analysis", __name__)

@bp.route("/case/<case_id>/chat/threads", methods=["GET"])
def chat_list_threads(case_id):
    from services.chat_service import list_threads
    topic = request.args.get("topic") or None
    return _svc_response(list_threads(case_id, topic=topic))


@bp.route("/case/<case_id>/chat/threads", methods=["POST"])
def chat_create_thread(case_id):
    from services.chat_service import create_thread
    data = request.get_json() or {}
    return _svc_response(create_thread(
        case_id, topic=data.get("topic", "free"), title=data.get("title", "")
    ))


@bp.route("/case/<case_id>/chat/threads/<thread_id>", methods=["GET"])
def chat_load_thread(case_id, thread_id):
    from services.chat_service import load_thread
    return _svc_response(load_thread(case_id, thread_id))


@bp.route("/case/<case_id>/chat/threads/<thread_id>", methods=["DELETE"])
def chat_delete_thread(case_id, thread_id):
    from services.chat_service import delete_thread
    return _svc_response(delete_thread(case_id, thread_id))


@bp.route("/case/<case_id>/chat/threads/<thread_id>/message", methods=["POST"])
def chat_post_message(case_id, thread_id):
    """Chat メッセージ送信。LLM 呼び出しは数分〜十数分かかることがあり、
    途中で何が起きても必ず JSON で返す (broad except)。

    無防備に raise すると Flask が応答せずブラウザ側が 'Failed to fetch' に
    なって状態が分からなくなる (chat は 1 ターンのコストが高いため致命)。
    """
    import traceback
    from services.chat_service import append_message_and_reply
    data = request.get_json() or {}
    try:
        return _svc_response(
            append_message_and_reply(
                case_id, thread_id, data.get("content", ""), model=data.get("model"),
            )
        )
    except Exception as e:
        current_app.logger.exception("chat_post_message failed")
        return jsonify({
            "error": f"chat 内部エラー: {type(e).__name__}: {e}",
            "traceback": traceback.format_exc().splitlines()[-3:],
        }), 500


@bp.route("/case/<case_id>/chat/threads/<thread_id>/apply", methods=["POST"])
def chat_apply_suggestion(case_id, thread_id):
    from services.chat_service import apply_suggestion
    data = request.get_json() or {}
    return _svc_response(
        apply_suggestion(case_id, thread_id, data.get("suggestion_id", ""))
    )


# ===== 本願分析 (Step 2 サブタブ SUB 3) =====

@bp.route("/case/<case_id>/hongan-analysis", methods=["GET"])
def hongan_analysis_load(case_id):
    """既存の分析結果 (cases/<id>/analysis/hongan_analysis.json) を返す"""
    from services.hongan_analysis_service import load_existing_analysis
    return _svc_response(load_existing_analysis(case_id))


@bp.route("/case/<case_id>/hongan-analysis/run", methods=["POST"])
def hongan_analysis_run(case_id):
    """本願分析テンプレートを実行 (auto + LLM 一括) して保存・返却する"""
    from services.hongan_analysis_service import run_analysis
    data = request.get_json() or {}
    version = data.get("version") or "v0.1"
    skip_llm = bool(data.get("skip_llm"))
    return _svc_response(run_analysis(
        case_id, version=version, skip_llm=skip_llm, model=data.get("model"),
    ))


@bp.route("/case/<case_id>/hongan-analysis/item", methods=["POST"])
def hongan_analysis_update_item(case_id):
    """単一項目の value を更新 (ユーザー編集による下線/ハイライトの反映)"""
    from services.hongan_analysis_service import update_item_value
    data = request.get_json() or {}
    return _svc_response(
        update_item_value(case_id, data.get("item_id", ""), data.get("value"))
    )


# ===== 予備調査 (Step 2 サブタブ) =====

@bp.route("/api/preliminary_research/fields", methods=["GET"])
def prelim_list_fields():
    """利用可能な分野レシピのスラッグ一覧"""
    from services.preliminary_research_service import list_available_fields
    return jsonify({"fields": list_available_fields()})


@bp.route("/api/preliminary_research/expand_synonyms", methods=["POST"])
def prelim_expand_synonyms():
    """成分名/技術用語の表記揺れを LLM で展開して候補リストを返す"""
    from services.preliminary_research_service import load_recipe
    from modules.synonym_expander import expand_synonyms

    data = request.get_json() or {}
    term = (data.get("term") or "").strip()
    field = data.get("field") or "generic"
    if not term:
        return jsonify({"error": "term は必須です"}), 400

    recipe = load_recipe(field)
    syn_cfg = recipe.get("synonym_expansion") or {}
    if not syn_cfg.get("enabled"):
        return jsonify({"synonyms": [term]})

    synonyms = expand_synonyms(term, syn_cfg.get("prompt_hint"))
    return jsonify({"synonyms": synonyms})


@bp.route("/api/preliminary_research/term_overview", methods=["POST"])
def prelim_term_overview():
    """本願明細書での term の扱い (定義/例示/実施例/効果/臨界的効果) を LLM で要約。"""
    from modules.term_overview import summarize_term_in_hongan
    from services.case_service import get_case_dir
    import json as _json

    data = request.get_json() or {}
    term = (data.get("term") or "").strip()
    case_id = (data.get("case_id") or "").strip()
    if not term:
        return jsonify({"error": "term は必須です"}), 400
    if not case_id:
        return jsonify({"error": "case_id は必須です"}), 400

    case_dir = get_case_dir(case_id)
    p = case_dir / "hongan.json"
    if not p.exists():
        return jsonify({"error": "本願データ (hongan.json) がありません"}), 404
    try:
        with open(p, "r", encoding="utf-8") as f:
            hongan = _json.load(f)
    except (OSError, _json.JSONDecodeError) as e:
        return jsonify({"error": f"hongan.json 読み込み失敗: {e}"}), 500

    return jsonify(summarize_term_in_hongan(term, hongan))


@bp.route("/api/preliminary_research/generate_urls", methods=["POST"])
def prelim_generate_urls():
    """採用クエリと分野から検索 URL を生成"""
    from services.preliminary_research_service import (
        load_recipe, generate_search_urls,
    )
    data = request.get_json() or {}
    queries = data.get("queries") or []
    field = data.get("field") or "generic"
    if not isinstance(queries, list) or not queries:
        return jsonify({"error": "queries (1 件以上) を指定してください"}), 400

    recipe = load_recipe(field)
    urls = generate_search_urls(recipe, queries)
    return jsonify({"urls": urls, "field": recipe.get("field", field)})


@bp.route("/api/preliminary_research/save_note", methods=["POST"])
def prelim_save_note():
    """予備調査メモを cases/<case_id>/analysis/hongan_understanding.md に追記"""
    from services.preliminary_research_service import save_note
    data = request.get_json() or {}
    case_id = data.get("case_id")
    if not case_id:
        return jsonify({"error": "case_id は必須です"}), 400
    result = save_note(
        case_id=case_id,
        component=data.get("component", ""),
        note=data.get("note", ""),
        urls_opened=data.get("urls_opened") or [],
        queries=data.get("queries") or [],
        field=data.get("field"),
    )
    status = result.pop("_status", 200) if isinstance(result, dict) else 200
    return jsonify(result), status


# ===== オートモード =====

