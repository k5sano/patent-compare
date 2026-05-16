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

bp = Blueprint("search_runs", __name__)

@bp.route("/case/<case_id>/search-run/formulas", methods=["GET"])
def search_run_formulas(case_id):
    """Stage 3 の keyword_dictionary.json から narrow/medium/wide の式を返す"""
    from services.search_run_service import get_formulas_from_keyword_dict
    return _svc_response({"formulas": get_formulas_from_keyword_dict(case_id)})


@bp.route("/case/<case_id>/search-run/list", methods=["GET"])
def search_run_list(case_id):
    from services.search_run_service import list_runs
    return _svc_response({"runs": list_runs(case_id)})


@bp.route("/case/<case_id>/search-run/<run_id>", methods=["GET"])
def search_run_get(case_id, run_id):
    from services.search_run_service import load_run
    data = load_run(case_id, run_id)
    if not data:
        return jsonify({"error": "検索ランが見つかりません"}), 404
    return jsonify(data)


@bp.route("/case/<case_id>/search-run/<run_id>", methods=["DELETE"])
def search_run_delete(case_id, run_id):
    from services.search_run_service import delete_run
    ok = delete_run(case_id, run_id)
    return jsonify({"success": ok}), (200 if ok else 404)


@bp.route("/case/<case_id>/search-run/execute", methods=["POST"])
def search_run_execute(case_id):
    """J-PlatPat (or Google Patents) で検索式を実行して run を保存。

    body: {
      "formula": "...",
      "formula_level": "narrow" | "medium" | "wide" | "custom",
      "source": "jplatpat" | "google_patents",
      "max_results": 100,
      "auto_click_search": true  # jplatpat のみ
    }
    """
    body = request.get_json() or {}
    formula = (body.get("formula") or "").strip()
    level = body.get("formula_level") or "custom"
    source = body.get("source") or "jplatpat"
    max_results = int(body.get("max_results") or 100)
    parent_run_id = body.get("parent_run_id") or None

    if not formula:
        return jsonify({"error": "検索式が空です"}), 400

    from services.search_run_service import create_run_from_hits

    try:
        if source == "jplatpat":
            from modules.jplatpat_client import run_jplatpat_search, JPLATPAT_SEARCH_URL
            hits = run_jplatpat_search(
                formula,
                max_results=max_results,
                auto_click_search=bool(body.get("auto_click_search", True)),
            )
            search_url = JPLATPAT_SEARCH_URL
        elif source == "google_patents":
            from modules.google_patents_scraper import search_google_patents
            raw = search_google_patents(formula, max_results=max_results)
            hits = [{
                "patent_id": h.patent_id, "title": h.title,
                "applicant": h.assignee, "publication_date": h.priority_date,
                "url": h.url,
            } for h in raw]
            search_url = f"https://patents.google.com/?q={formula}"
        elif source == "formula_only":
            # 検索実行はせず、式だけ run として保存 (後で手動貼付/別ツールで検索するため)
            hits = []
            from modules.jplatpat_client import JPLATPAT_SEARCH_URL
            search_url = JPLATPAT_SEARCH_URL
        else:
            return jsonify({"error": f"unknown source: {source}"}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"検索エラー: {e}"}), 500

    data = create_run_from_hits(
        case_id,
        formula=formula,
        formula_level=level,
        source=source,
        hits=hits,
        search_url=search_url,
        parent_run_id=parent_run_id,
    )

    # 親ランがあれば差分サマリを同梱して返す
    diff = None
    if parent_run_id:
        from services.search_run_service import compute_run_diff
        try:
            diff_full = compute_run_diff(case_id, data["run_id"], parent_run_id)
            if diff_full:
                diff = diff_full.get("summary")
        except Exception:
            diff = None

    return jsonify({"success": True, "run": data, "diff_summary": diff})


# ==== J-PlatPat 半自動化セッション ====

@bp.route("/case/<case_id>/search-run/jplatpat/open", methods=["POST"])
def jplatpat_session_open(case_id):
    """J-PlatPat を可視ブラウザで開き、セッションを維持する。"""
    from services.jplatpat_session import get_session
    sess = get_session()
    try:
        r = sess.open(timeout=45)
    except Exception as e:
        return jsonify({"ok": False, "error": f"起動エラー: {e}"}), 500
    return jsonify(r)


@bp.route("/case/<case_id>/search-run/jplatpat/fill", methods=["POST"])
def jplatpat_session_fill(case_id):
    """現在の J-PlatPat ページ (論理式入力タブ) に式をフィル。"""
    from services.jplatpat_session import get_session
    body = request.get_json() or {}
    formula = (body.get("formula") or "").strip()
    if not formula:
        return jsonify({"ok": False, "error": "formula が必要です"}), 400
    sess = get_session()
    r = sess.fill(formula, timeout=25)
    return jsonify(r)


@bp.route("/case/<case_id>/search-run/jplatpat/scrape", methods=["POST"])
def jplatpat_session_scrape(case_id):
    """J-PlatPat の現在の検索結果を取り込み、run として保存。"""
    from services.jplatpat_session import get_session
    from services.search_run_service import create_run_from_hits
    body = request.get_json() or {}
    formula = (body.get("formula") or "").strip()
    level = body.get("formula_level") or "custom"
    max_results = int(body.get("max_results") or 100)
    parent_run_id = body.get("parent_run_id") or None
    save_run = bool(body.get("save_run", True))

    sess = get_session()
    r = sess.scrape(max_results=max_results, timeout=40)
    if not r.get("ok"):
        return jsonify(r), 400

    hits = r.get("hits") or []

    if not save_run:
        return jsonify({"ok": True, "hits": hits, "count": len(hits)})

    if not formula:
        return jsonify({"ok": False, "error": "formula が必要です (ラン保存時)"}), 400

    try:
        data = create_run_from_hits(
            case_id,
            formula=formula,
            formula_level=level,
            source="jplatpat_manual",
            hits=hits,
            search_url="https://www.j-platpat.inpit.go.jp/s0100",
            parent_run_id=parent_run_id,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"ラン保存エラー: {e}"}), 500

    diff = None
    if parent_run_id:
        from services.search_run_service import compute_run_diff
        try:
            diff_full = compute_run_diff(case_id, data["run_id"], parent_run_id)
            if diff_full:
                diff = diff_full.get("summary")
        except Exception:
            diff = None

    return jsonify({"ok": True, "run": data, "count": len(hits), "diff_summary": diff})


@bp.route("/case/<case_id>/search-run/hit/<path:patent_id>/text", methods=["GET"])
def get_hit_full_text(case_id, patent_id):
    """キャッシュ済みヒット全文を返す（無ければ 404）。"""
    from services.search_run_service import get_hit_text
    d = get_hit_text(case_id, patent_id)
    if d is None:
        return jsonify({"error": "未取得"}), 404
    return jsonify(d)



@bp.route("/case/<case_id>/search-run/hits/cached-full-texts", methods=["POST"])
def list_cached_hit_full_texts(case_id):
    """指定 patent_id 群 (省略時は全件) のキャッシュ済 full text を一括返却。

    Step 4.5 の候補一覧描画時に呼び出して、`window._pkmFullTexts` をページロード時
    から復元するためのエンドポイント。これでサーバ側ファイルキャッシュに保存
    された全文取得結果が、ページ再読込後の候補カウント表示に反映される。
    """
    from services.search_run_service import list_cached_hit_texts
    body = request.get_json(silent=True) or {}
    pids = body.get("patent_ids")
    if pids is not None and not isinstance(pids, list):
        return jsonify({"error": "patent_ids は配列で指定してください"}), 400
    texts = list_cached_hit_texts(case_id, pids)
    return jsonify({"texts": texts, "count": len(texts)})


@bp.route("/case/<case_id>/hit-bookmarks", methods=["GET"])
def hit_bookmarks_list(case_id):
    """全文レビューの名前付きしおり一覧を返す。"""
    from services.search_run_service import list_hit_bookmarks
    bookmarks = list_hit_bookmarks(case_id)
    names = sorted({b.get("name") for b in bookmarks if b.get("name")})
    return jsonify({"bookmarks": bookmarks, "names": names, "count": len(bookmarks)})


@bp.route("/case/<case_id>/hit-bookmarks", methods=["POST"])
def hit_bookmarks_save(case_id):
    """全文レビューの文献に名前付きしおりを付ける。"""
    from services.search_run_service import list_hit_bookmarks, save_hit_bookmark
    body = request.get_json(silent=True) or {}
    patent_id = str(body.get("patent_id") or "").strip()
    name = str(body.get("name") or "").strip()
    if not patent_id:
        return jsonify({"error": "patent_id が必要です"}), 400
    if not name:
        return jsonify({"error": "しおり名を入力してください"}), 400
    try:
        item = save_hit_bookmark(case_id, patent_id, name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    bookmarks = list_hit_bookmarks(case_id)
    names = sorted({b.get("name") for b in bookmarks if b.get("name")})
    return jsonify({"ok": True, "bookmark": item, "bookmarks": bookmarks, "names": names})


@bp.route("/case/<case_id>/search-runs/citation-cards", methods=["POST"])
def search_run_citation_cards(case_id):
    """Step 6 の引用文献を Step 4.5 候補カード用 hit 形式で返す。"""
    from services.search_run_service import build_citation_card_hits

    body = request.get_json(silent=True) or {}
    items = body.get("items")
    if items is None:
        pids = body.get("patent_ids") or []
        items = [{"id": pid, "aliases": [pid]} for pid in pids]
    if not isinstance(items, list):
        return jsonify({"error": "items は配列で指定してください"}), 400
    hits = build_citation_card_hits(case_id, items)
    return jsonify({"hits": hits, "count": len(hits)})


@bp.route("/case/<case_id>/search-run/hit/<path:patent_id>/fetch-text", methods=["POST"])
def fetch_hit_full_text(case_id, patent_id):
    """ヒット全文を取得してキャッシュ。source='auto' / 'google' / 'jplatpat'。"""
    from services.search_run_service import fetch_and_cache_hit_text
    body = request.get_json(silent=True) or {}
    force = bool(body.get("force"))
    language = (body.get("language") or "ja").strip() or "ja"
    source = (body.get("source") or "auto").strip() or "auto"
    try:
        data = fetch_and_cache_hit_text(case_id, patent_id, force=force,
                                         language=language, source=source)
    except Exception as e:
        return jsonify({"error": f"取得エラー: {e}"}), 500
    if "error" in data and not data.get("description") and not data.get("claims"):
        return jsonify(data), 500
    return jsonify(data)


@bp.route("/case/<case_id>/search-run/jplatpat/status", methods=["GET"])
def jplatpat_session_status(case_id):
    from services.jplatpat_session import get_session
    sess = get_session()
    return jsonify(sess.status())


@bp.route("/case/<case_id>/search-run/jplatpat/close", methods=["POST"])
def jplatpat_session_close(case_id):
    from services.jplatpat_session import reset_session
    reset_session()
    return jsonify({"ok": True})


@bp.route("/case/<case_id>/search-run/<run_id>/screening", methods=["POST"])
def search_run_screening(case_id, run_id):
    """単一候補のスクリーニング状態更新。

    body: {"patent_id": "...", "screening": "star|triangle|reject|hold|pending", "note": "..."}
    """
    from services.search_run_service import update_screening
    body = request.get_json() or {}
    pid = body.get("patent_id")
    scr = body.get("screening")
    note = body.get("note")
    if not pid or not scr:
        return jsonify({"error": "patent_id と screening が必要です"}), 400
    try:
        data = update_screening(case_id, run_id, pid, scr, note=note)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not data:
        return jsonify({"error": "候補が見つかりません"}), 404
    return jsonify({"success": True})


@bp.route("/case/<case_id>/search-run/<run_id>/screening/bulk", methods=["POST"])
def search_run_screening_bulk(case_id, run_id):
    from services.search_run_service import bulk_update_screening
    body = request.get_json() or {}
    updates = body.get("updates") or []
    data = bulk_update_screening(case_id, run_id, updates)
    if not data:
        return jsonify({"error": "検索ランが見つかりません"}), 404
    return jsonify({"success": True, "updated": len(updates)})


@bp.route("/case/<case_id>/search-runs/hold-patents", methods=["POST"])
def search_run_hold_patents(case_id):
    """指定文献を検索候補の待機BOX (`screening=hold`) へ移す。"""
    from services.search_run_service import hold_patents_across_runs

    body = request.get_json() or {}
    patent_ids = body.get("patent_ids") or []
    note = body.get("note")
    if not isinstance(patent_ids, list) or not patent_ids:
        return jsonify({"error": "patent_ids が必要です"}), 400
    result = hold_patents_across_runs(case_id, patent_ids, note=note)
    return jsonify({"success": True, **result})


@bp.route("/case/<case_id>/search-run/<run_id>/download-starred", methods=["POST"])
def search_run_download_starred(case_id, run_id):
    """☆付き候補を一括でPDFダウンロード→引用文献登録。"""
    from services.search_run_service import load_run, mark_downloaded
    from services.search_service import search_download as svc_search_download

    data = load_run(case_id, run_id)
    if not data:
        return jsonify({"error": "検索ランが見つかりません"}), 404

    body = request.get_json() or {}
    role = body.get("role", "主引例")

    pids = [
        h.get("patent_id") for h in data.get("hits", [])
        if h.get("screening") == "star" and not h.get("downloaded_as_citation")
        and h.get("patent_id")
    ]
    if not pids:
        return jsonify({"error": "☆の候補がありません"}), 400

    result, code = svc_search_download(case_id, pids, role=role)
    # DL 成功のものにフラグを付ける
    for r in (result.get("results") or []):
        if r.get("success"):
            mark_downloaded(case_id, run_id, r.get("patent_id"), True)
    result["run_id"] = run_id
    return jsonify(result), code


@bp.route("/case/<case_id>/search-run/<run_id>/ai-score", methods=["POST"])
def search_run_ai_score(case_id, run_id):
    """AIで本願関連度スコアを付与 (Phase 2)"""
    from services.search_run_service import ai_score_run
    body = request.get_json() or {}
    try:
        # limit=None で未スコアの全件を処理。明示で {"limit": N} 指定があれば従う。
        raw_limit = body.get("limit")
        limit = int(raw_limit) if raw_limit not in (None, "", 0) else None
        data = ai_score_run(case_id, run_id, limit=limit, model=body.get("model"))
    except NotImplementedError:
        return jsonify({"error": "AIスコア機能は未実装 (Phase2)"}), 501
    if not data:
        return jsonify({"error": "検索ランが見つかりません"}), 404
    return jsonify({"success": True, "run": data})


@bp.route("/case/<case_id>/search-run/<run_id>/ai-score-stream", methods=["POST"])
def search_run_ai_score_stream(case_id, run_id):
    """AI関連度スコアを 1 件ずつ返す NDJSON ストリーム。"""
    from services.search_run_service import ai_score_run_stream
    body = request.get_json(silent=True) or {}
    raw_limit = body.get("limit")
    limit = int(raw_limit) if raw_limit not in (None, "", 0) else None
    model = body.get("model")

    def generate():
        for event in ai_score_run_stream(case_id, run_id, limit=limit, model=model):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return Response(generate(), mimetype="application/x-ndjson")


@bp.route("/case/<case_id>/search-run/feedback/not-terms", methods=["GET"])
def search_run_feedback_not_terms(case_id):
    """却下された候補から NOT 語候補を抽出 (Phase 3)"""
    from services.auto_service import feedback_not_terms
    return jsonify(feedback_not_terms(case_id))


@bp.route("/case/<case_id>/search-run/merge", methods=["POST"])
def search_run_merge(case_id):
    """複数ランの hits をマージ＆重複排除して返す (永続化しない、プレビュー用)。

    body: {"run_ids": ["...", "..."]}
    """
    from services.search_run_service import merge_runs, list_runs
    body = request.get_json() or {}
    run_ids = body.get("run_ids") or []
    if not run_ids:
        # 指定なしなら全ランマージ
        run_ids = [r["run_id"] for r in list_runs(case_id)]
    merged = merge_runs(case_id, run_ids)
    return jsonify({
        "success": True,
        "run_ids": run_ids,
        "hit_count": len(merged),
        "hits": merged,
    })


@bp.route("/case/<case_id>/search-run/<run_id>/enrich", methods=["POST"])
def search_run_enrich(case_id, run_id):
    """候補の要約・請求項1を Google Patents から取得して埋める (Phase 2)"""
    from services.search_run_service import enrich_run
    body = request.get_json() or {}
    try:
        data = enrich_run(case_id, run_id, limit=int(body.get("limit") or 20))
    except NotImplementedError:
        return jsonify({"error": "Enrich 機能は未実装 (Phase2)"}), 501
    if not data:
        return jsonify({"error": "検索ランが見つかりません"}), 404
    return jsonify({"success": True, "run": data})


@bp.route("/case/<case_id>/search-run/<run_id>/diff", methods=["GET"])
def search_run_diff(case_id, run_id):
    """ラン run_id と base ランの hits 差分を返す。

    query: ?base=<base_run_id>  (省略時は run の parent_run_id)
    """
    from services.search_run_service import compute_run_diff, load_run
    base = request.args.get("base") or ""
    if not base:
        run = load_run(case_id, run_id)
        if run:
            base = run.get("parent_run_id") or ""
    if not base:
        return jsonify({"error": "base ラン ID が未指定かつ parent_run_id も存在しません"}), 400
    diff = compute_run_diff(case_id, run_id, base)
    if diff is None:
        return jsonify({"error": "ランが見つかりません"}), 404
    return jsonify(diff)


@bp.route("/case/<case_id>/search-run/validate-formula", methods=["POST"])
def search_run_validate_formula(case_id):
    """検索式の括弧バランス・構文チェック。

    body: {"formula": "..."}
    """
    from services.search_run_service import validate_formula
    body = request.get_json() or {}
    formula = body.get("formula") or ""
    return jsonify(validate_formula(formula))


@bp.route("/case/<case_id>/search-run/snippets", methods=["GET"])
def search_run_snippets(case_id):
    """検索式エディタ用のキーワード/FI/Fterm 挿入候補を返す。"""
    from services.search_run_service import get_keyword_snippets
    return jsonify(get_keyword_snippets(case_id))


