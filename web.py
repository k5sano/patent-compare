#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PatentCompare Web GUI
シンプルなFlaskベースのWeb UI — ルーティング層
ビジネスロジックは services/ に分離。
"""

import json
import os
import subprocess
import yaml
from pathlib import Path
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, send_file, Response
)

from modules.app_config import load_env, get_app_config
from services.case_service import (
    get_case_dir, load_case_meta, list_all_cases,
    load_json_file, find_citation_pdf,
)

PROJECT_ROOT = Path(__file__).parent.resolve()

# .env を読み込み (Flask 初期化より前)
load_env()
_app_cfg = get_app_config()

app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))
app.secret_key = _app_cfg.secret_key
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100MB
app.json.ensure_ascii = False
# 開発用: static (case.js/case.css) と template の編集を即反映
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True


@app.after_request
def _no_cache_for_html(resp):
    """動的 HTML レスポンスのブラウザキャッシュも抑止。"""
    ct = resp.headers.get("Content-Type", "")
    if ct.startswith("text/html"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


# ===== ヘルパー =====

def _svc_response(result, status_code=None):
    """サービスの (data, status_code) タプルを jsonify に変換"""
    if isinstance(result, tuple):
        data, code = result
        return jsonify(data), code
    return jsonify(result)


# ===== ホーム・案件管理 =====

@app.route("/")
def index():
    cases = list_all_cases()
    return render_template("index.html", cases=cases)


@app.route("/case/new", methods=["POST"])
def new_case():
    from services.case_service import create_case

    data = request.get_json() or {}
    case_number = (data.get("case_number") or "").strip()
    if not case_number:
        return jsonify({"error": "案件番号を入力してください"}), 400

    result = create_case(
        case_number,
        year=data.get("year", ""),
        month=data.get("month", ""),
        field=data.get("field", "cosmetics"),
    )
    if "error" in result and "case_id" in result and result.get("error") and not result.get("success"):
        return jsonify(result), 409
    return jsonify(result)


@app.route("/case/<case_id>")
def case_detail(case_id):
    meta = load_case_meta(case_id)
    if not meta:
        flash("案件が見つかりません。", "error")
        return redirect(url_for("index"))

    # fallback 検索でフォルダ名が解決された場合、正規 URL にリダイレクト
    canonical_id = meta.get("case_id", case_id)
    if canonical_id != case_id:
        return redirect(url_for("case_detail", case_id=canonical_id))

    case_dir = get_case_dir(case_id)

    hongan = load_json_file(case_id, "hongan.json")
    segments = load_json_file(case_id, "segments.json")
    keywords = load_json_file(case_id, "keywords.json")
    related_paragraphs = load_json_file(case_id, "related_paragraphs.json") or {}

    citations = []
    for cit in meta.get("citations", []):
        cit_path = case_dir / "citations" / f"{cit['id']}.json"
        resp_path = case_dir / "responses" / f"{cit['id']}.json"
        cit["_has_data"] = cit_path.exists()
        cit["_has_response"] = resp_path.exists()
        cit["_has_pdf"] = bool(find_citation_pdf(case_dir / "input", cit['id']))
        cit["_category"] = ""
        if resp_path.exists():
            try:
                import json as _json
                with open(resp_path, "r", encoding="utf-8") as f:
                    rdata = _json.loads(f.read().replace('\x00', ''), strict=False)
                cit["_category"] = (rdata.get("category_suggestion", "") or "").upper()[:1]
            except Exception:
                pass
        citations.append(cit)

    excel_files = list((case_dir / "output").glob("*.xlsx")) if (case_dir / "output").exists() else []

    return render_template("case.html",
                           meta=meta, hongan=hongan, segments=segments,
                           keywords=keywords, citations=citations,
                           related_paragraphs=related_paragraphs,
                           excel_files=excel_files, case_id=case_id)


@app.route("/case/<case_id>/meta", methods=["POST"])
def update_case_meta_route(case_id):
    from services.case_service import update_case_meta
    return _svc_response(update_case_meta(case_id, request.get_json() or {}))


@app.route("/case/<case_id>/delete", methods=["POST"])
def delete_case(case_id):
    from services.case_service import delete_case as _delete
    _delete(case_id)
    flash(f"案件 '{case_id}' を削除しました。", "success")
    return redirect(url_for("index"))


# ===== PDF アップロー���・引用文献管理 =====

@app.route("/case/<case_id>/upload/hongan", methods=["POST"])
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


@app.route("/case/<case_id>/upload/citation", methods=["POST"])
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


@app.route("/case/<case_id>/citation/<citation_id>", methods=["DELETE"])
def delete_citation(case_id, citation_id):
    from services.case_service import delete_citation as _delete
    return _svc_response(_delete(case_id, citation_id))


@app.route("/case/<case_id>/citations/clear", methods=["DELETE"])
def clear_all_citations(case_id):
    from services.case_service import clear_all_citations as _clear
    return _svc_response(_clear(case_id))


# ===== 分節 =====

@app.route("/case/<case_id>/segments", methods=["GET"])
def get_segments(case_id):
    data = load_json_file(case_id, "segments.json")
    if data is None:
        return jsonify({"error": "分節データがありません"}), 404
    return jsonify(data)


@app.route("/case/<case_id>/segments", methods=["POST"])
def save_segments(case_id):
    from services.case_service import save_json_file
    save_json_file(case_id, "segments.json", request.get_json())
    return jsonify({"success": True})


# ===== キーワード =====

@app.route("/case/<case_id>/keywords", methods=["GET"])
def get_keywords(case_id):
    from services.keyword_service import get_keywords
    return _svc_response(get_keywords(case_id))


@app.route("/case/<case_id>/keywords/add", methods=["POST"])
def add_keyword(case_id):
    from services.keyword_service import add_keyword
    data = request.get_json() or {}
    return _svc_response(add_keyword(case_id, data.get("group_id"), data.get("term", "")))


@app.route("/case/<case_id>/keywords/delete", methods=["POST"])
def delete_keyword(case_id):
    from services.keyword_service import delete_keyword
    data = request.get_json() or {}
    return _svc_response(delete_keyword(case_id, data.get("group_id"), data.get("term", "")))


@app.route("/case/<case_id>/keywords/edit", methods=["POST"])
def edit_keyword(case_id):
    from services.keyword_service import edit_keyword
    data = request.get_json() or {}
    return _svc_response(edit_keyword(case_id, data.get("group_id"), data.get("old_term"), data.get("new_term")))


@app.route("/case/<case_id>/keywords/suggest", methods=["POST"])
def suggest_keywords(case_id):
    from services.keyword_service import suggest_keywords
    return _svc_response(suggest_keywords(case_id))


@app.route("/case/<case_id>/keywords/suggest-by-segment", methods=["POST"])
def suggest_keywords_by_segment(case_id):
    from services.keyword_service import suggest_keywords_by_segment
    return _svc_response(suggest_keywords_by_segment(case_id))


@app.route("/case/<case_id>/keywords/group/add", methods=["POST"])
def add_keyword_group(case_id):
    from services.keyword_service import add_keyword_group
    data = request.get_json() or {}
    return _svc_response(add_keyword_group(case_id, data.get("label", ""), data.get("segment_ids", [])))


@app.route("/case/<case_id>/keywords/group/delete", methods=["POST"])
def delete_keyword_group(case_id):
    from services.keyword_service import delete_keyword_group
    data = request.get_json() or {}
    return _svc_response(delete_keyword_group(case_id, data.get("group_id")))


@app.route("/case/<case_id>/keywords/group/update", methods=["POST"])
def update_keyword_group(case_id):
    from services.keyword_service import update_keyword_group
    data = request.get_json() or {}
    return _svc_response(update_keyword_group(case_id, data.get("group_id"), data))


@app.route("/case/<case_id>/keywords/fterm/candidates", methods=["GET"])
def fterm_candidates(case_id):
    from services.keyword_service import fterm_candidates
    return _svc_response(fterm_candidates(case_id))


@app.route("/case/<case_id>/keywords/fterm/add", methods=["POST"])
def add_fterm(case_id):
    from services.keyword_service import add_fterm
    data = request.get_json() or {}
    return _svc_response(add_fterm(case_id, data.get("group_id"), data.get("code"), data.get("desc", "")))


@app.route("/case/<case_id>/keywords/fterm/delete", methods=["POST"])
def delete_fterm(case_id):
    from services.keyword_service import delete_fterm
    data = request.get_json() or {}
    return _svc_response(delete_fterm(case_id, data.get("group_id"), data.get("code")))


@app.route("/case/<case_id>/keywords/segments", methods=["GET"])
def get_segment_keywords(case_id):
    data = load_json_file(case_id, "segment_keywords.json")
    if data is None:
        return jsonify({"error": "分節別キーワードがありません。先に提案を実行してください。"}), 404
    return jsonify(data)


@app.route("/case/<case_id>/keywords/add-to-segment", methods=["POST"])
def add_keyword_to_segment(case_id):
    from services.keyword_service import add_keyword_to_segment
    data = request.get_json() or {}
    return _svc_response(add_keyword_to_segment(case_id, data.get("segment_id"), data.get("term")))


@app.route("/case/<case_id>/keywords/remove-from-segment", methods=["POST"])
def remove_keyword_from_segment(case_id):
    from services.keyword_service import remove_keyword_from_segment
    data = request.get_json() or {}
    return _svc_response(remove_keyword_from_segment(case_id, data.get("segment_id"), data.get("term")))


@app.route("/case/<case_id>/keywords/update-segment-keyword", methods=["POST"])
def update_segment_keyword(case_id):
    from services.keyword_service import update_segment_keyword
    data = request.get_json() or {}
    return _svc_response(update_segment_keyword(case_id, data.get("segment_id"), data.get("old_term"), data.get("new_term")))


# ===== 対比・プロンプト・回答・Excel =====

@app.route("/case/<case_id>/prompt", methods=["POST"])
def generate_prompt_multi(case_id):
    from services.comparison_service import generate_prompt_multi
    data = request.get_json() or {}
    return _svc_response(generate_prompt_multi(case_id, data.get("citation_ids", [])))


@app.route("/case/<case_id>/prompt/<citation_id>", methods=["GET"])
def generate_prompt_single(case_id, citation_id):
    from services.comparison_service import generate_prompt_single
    return _svc_response(generate_prompt_single(case_id, citation_id))


@app.route("/case/<case_id>/response", methods=["POST"])
def save_response_multi(case_id):
    from services.comparison_service import save_response_multi
    return _svc_response(save_response_multi(case_id, request.get_json().get("text", "")))


@app.route("/case/<case_id>/response/<citation_id>", methods=["POST"])
def save_response_single(case_id, citation_id):
    from services.comparison_service import save_response_single
    return _svc_response(save_response_single(case_id, citation_id, request.get_json().get("text", "")))


@app.route("/case/<case_id>/response/<citation_id>", methods=["GET"])
def get_response(case_id, citation_id):
    from services.comparison_service import get_response
    return _svc_response(get_response(case_id, citation_id))


@app.route("/case/<case_id>/export/excel", methods=["POST"])
def export_excel(case_id):
    from services.comparison_service import export_excel
    return _svc_response(export_excel(case_id))


@app.route("/case/<case_id>/download/<path:filename>")
def download_file(case_id, filename):
    case_dir = get_case_dir(case_id)
    file_path = case_dir / "output" / filename
    if not file_path.exists():
        flash("ファイルが見つかりません。", "error")
        return redirect(url_for("case_detail", case_id=case_id))
    return send_file(str(file_path), as_attachment=True)


def _resolve_hongan_pdf_path(case_id):
    """本願PDFの実ファイルパスを解決。見つからなければ None。"""
    case_dir = get_case_dir(case_id)
    hongan_path = case_dir / "hongan.json"

    if hongan_path.exists():
        try:
            with open(hongan_path, "r", encoding="utf-8") as f:
                hongan = json.load(f)
            src = hongan.get("source_pdf")
            if src:
                candidate = case_dir / "input" / src
                if candidate.exists():
                    return candidate
        except Exception:
            pass

    # フォールバック: input/ の中で引用文献 ID を含まない PDF を探す
    input_dir = case_dir / "input"
    if input_dir.exists():
        meta = load_case_meta(case_id) or {}
        cit_ids = [c["id"] for c in meta.get("citations", [])]
        for p in input_dir.glob("*.pdf"):
            if not any(cid and cid in p.stem for cid in cit_ids):
                return p
    return None


PDFXCHANGE_CANDIDATES = [
    r"C:\Program Files\Tracker Software\PDF Editor\PDFXEdit.exe",
    r"C:\Program Files (x86)\Tracker Software\PDF Editor\PDFXEdit.exe",
]


@app.route("/case/<case_id>/hongan/pdf")
def view_hongan_pdf(case_id):
    """本願PDFをブラウザでインライン表示（フォールバック用）"""
    pdf_path = _resolve_hongan_pdf_path(case_id)
    if pdf_path is None:
        return jsonify({"error": "本願PDFが見つかりません"}), 404
    return send_file(str(pdf_path), mimetype="application/pdf", as_attachment=False)


@app.route("/case/<case_id>/hongan/open", methods=["POST"])
def open_hongan_pdf(case_id):
    """本願PDFをPDF-XChange Editorで開く（サーバーホスト上で起動）"""
    pdf_path = _resolve_hongan_pdf_path(case_id)
    if pdf_path is None:
        return jsonify({"error": "本願PDFが見つかりません"}), 404
    return _launch_pdf_xchange(pdf_path)


def _launch_pdf_xchange(pdf_path):
    if _open_with_pdf_xchange(pdf_path):
        exe = next((p for p in PDFXCHANGE_CANDIDATES if Path(p).exists()), None)
        return jsonify({"success": True,
                        "opened_with": "PDF-XChange Editor" if exe else "OS default"})
    return jsonify({"error": "起動失敗"}), 500


@app.route("/case/<case_id>/segments/related", methods=["GET"])
def get_related_paragraphs(case_id):
    """キャッシュ済みの関連段落マッピングを返す"""
    data = load_json_file(case_id, "related_paragraphs.json") or {}
    return jsonify({"related": data})


@app.route("/case/<case_id>/segments/related", methods=["POST"])
def compute_related_paragraphs_route(case_id):
    """分節に対して関連段落を検出・保存"""
    from services.case_service import compute_related_paragraphs
    return _svc_response(compute_related_paragraphs(case_id))


@app.route("/case/<case_id>/hongan/bookmark", methods=["POST"])
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


@app.route("/case/<case_id>/annotate/<citation_id>", methods=["POST"])
def annotate_citation(case_id, citation_id):
    from services.comparison_service import annotate_citation
    result, code = annotate_citation(case_id, citation_id)
    if code == 200 and result.get("success"):
        pdf_path = get_case_dir(case_id) / "output" / result["filename"]
        result["opened"] = _open_with_pdf_xchange(pdf_path)
    return jsonify(result), code


@app.route("/case/<case_id>/annotate/all", methods=["POST"])
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


@app.route("/case/<case_id>/execute", methods=["POST"])
def compare_execute(case_id):
    from services.comparison_service import compare_execute
    data = request.get_json() or {}
    return _svc_response(compare_execute(case_id, data.get("citation_ids", [])))


# ===== 進歩性 =====

@app.route("/case/<case_id>/inventive-step/prompt", methods=["POST"])
def inventive_step_prompt(case_id):
    from services.comparison_service import inventive_step_prompt
    return _svc_response(inventive_step_prompt(case_id))


@app.route("/case/<case_id>/inventive-step/response", methods=["POST"])
def inventive_step_response(case_id):
    from services.comparison_service import inventive_step_response
    return _svc_response(inventive_step_response(case_id, request.get_json().get("text", "")))


@app.route("/case/<case_id>/inventive-step/execute", methods=["POST"])
def inventive_step_execute(case_id):
    from services.comparison_service import inventive_step_execute
    return _svc_response(inventive_step_execute(case_id))


# ===== API =====

@app.route("/api/claude-status")
def claude_status():
    from modules.claude_client import is_claude_available, _load_serpapi_key
    return jsonify({
        "available": is_claude_available(),
        "search_available": bool(_load_serpapi_key()),
    })


@app.route("/api/serpapi-key", methods=["POST"])
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

@app.route("/case/<case_id>/search/prompt", methods=["POST"])
def search_prompt(case_id):
    from services.search_service import search_prompt
    return _svc_response(search_prompt(case_id))


@app.route("/case/<case_id>/search/response", methods=["POST"])
def search_response(case_id):
    from services.search_service import search_response
    return _svc_response(search_response(case_id, request.get_json().get("text", "")))


@app.route("/case/<case_id>/search/download", methods=["POST"])
def search_download(case_id):
    from services.search_service import search_download
    data = request.get_json() or {}
    patent_ids = data.get("patent_ids", [])
    if not patent_ids and data.get("patent_id"):
        patent_ids = [data["patent_id"]]
    return _svc_response(search_download(case_id, patent_ids, data.get("role", "主引例")))


@app.route("/case/<case_id>/search/execute", methods=["POST"])
def search_execute(case_id):
    from services.search_service import search_execute
    return _svc_response(search_execute(case_id))


@app.route("/case/<case_id>/search/presearch/prompt", methods=["POST"])
def presearch_prompt(case_id):
    from services.search_service import presearch_prompt
    return _svc_response(presearch_prompt(case_id))


@app.route("/case/<case_id>/search/presearch/parse", methods=["POST"])
def presearch_parse(case_id):
    from services.search_service import presearch_parse
    return _svc_response(presearch_parse(case_id, (request.get_json() or {}).get("text", "")))


@app.route("/case/<case_id>/search/classify/prompt", methods=["POST"])
def classify_prompt(case_id):
    from services.search_service import classify_prompt
    return _svc_response(classify_prompt(case_id))


@app.route("/case/<case_id>/search/classify/parse", methods=["POST"])
def classify_parse(case_id):
    from services.search_service import classify_parse
    return _svc_response(classify_parse(case_id, (request.get_json() or {}).get("text", "")))


@app.route("/case/<case_id>/search/keywords/prompt", methods=["POST"])
def keyword_dict_prompt(case_id):
    from services.search_service import keyword_dict_prompt
    return _svc_response(keyword_dict_prompt(case_id))


@app.route("/case/<case_id>/search/keywords/parse", methods=["POST"])
def keyword_dict_parse(case_id):
    from services.search_service import keyword_dict_parse
    return _svc_response(keyword_dict_parse(case_id, (request.get_json() or {}).get("text", "")))


@app.route("/case/<case_id>/search/status", methods=["GET"])
def search_status(case_id):
    from services.search_service import search_status
    return _svc_response(search_status(case_id))


@app.route("/case/<case_id>/search/data/<filename>", methods=["GET"])
def get_search_data(case_id, filename):
    from services.search_service import get_search_data_file
    return _svc_response(get_search_data_file(case_id, filename))


@app.route("/case/<case_id>/search/stage-execute", methods=["POST"])
def stage_execute(case_id):
    from services.search_service import stage_execute
    data = request.get_json() or {}
    return _svc_response(stage_execute(case_id, data.get("stage")))


@app.route("/case/<case_id>/search/stage-execute-stream", methods=["POST"])
def stage_execute_stream(case_id):
    from services.search_service import stage_execute_stream
    import types
    result = stage_execute_stream(case_id)
    if isinstance(result, tuple):
        data, code = result
        return jsonify(data), code
    # result is a generator
    return Response(result, mimetype="application/x-ndjson")


# ===== ISR / 書面意見 取り込み =====

@app.route("/case/<case_id>/search-report/list", methods=["GET"])
def search_report_list(case_id):
    from services.search_report_service import list_reports
    return _svc_response(list_reports(case_id))


@app.route("/case/<case_id>/search-report/upload", methods=["POST"])
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


@app.route("/case/<case_id>/search-report/<path:filename>", methods=["DELETE"])
def search_report_delete(case_id, filename):
    from services.search_report_service import delete_report
    return _svc_response(delete_report(case_id, filename))


@app.route("/case/<case_id>/search-report/<path:filename>/summarize", methods=["POST"])
def search_report_summarize(case_id, filename):
    from services.search_report_service import summarize_box_v
    return _svc_response(summarize_box_v(case_id, filename))


@app.route("/case/<case_id>/search-report/<path:filename>/fetch", methods=["POST"])
def search_report_fetch(case_id, filename):
    from services.search_report_service import fetch_cited_documents
    data = request.get_json() or {}
    nums = data.get("nums", [])
    return _svc_response(fetch_cited_documents(case_id, filename, nums))


@app.route("/case/<case_id>/search-report/<path:filename>/pdf")
def search_report_pdf(case_id, filename):
    pdf_path = get_case_dir(case_id) / "search_reports" / filename
    if not pdf_path.exists():
        return jsonify({"error": "PDFが見つかりません"}), 404
    return send_file(str(pdf_path), mimetype="application/pdf", as_attachment=False)


# ===== J-PlatPat 検索ラン (Step 4.5) =====

@app.route("/case/<case_id>/search-run/formulas", methods=["GET"])
def search_run_formulas(case_id):
    """Stage 3 の keyword_dictionary.json から narrow/medium/wide の式を返す"""
    from services.search_run_service import get_formulas_from_keyword_dict
    return _svc_response({"formulas": get_formulas_from_keyword_dict(case_id)})


@app.route("/case/<case_id>/search-run/list", methods=["GET"])
def search_run_list(case_id):
    from services.search_run_service import list_runs
    return _svc_response({"runs": list_runs(case_id)})


@app.route("/case/<case_id>/search-run/<run_id>", methods=["GET"])
def search_run_get(case_id, run_id):
    from services.search_run_service import load_run
    data = load_run(case_id, run_id)
    if not data:
        return jsonify({"error": "検索ランが見つかりません"}), 404
    return jsonify(data)


@app.route("/case/<case_id>/search-run/<run_id>", methods=["DELETE"])
def search_run_delete(case_id, run_id):
    from services.search_run_service import delete_run
    ok = delete_run(case_id, run_id)
    return jsonify({"success": ok}), (200 if ok else 404)


@app.route("/case/<case_id>/search-run/execute", methods=["POST"])
def search_run_execute(case_id):
    """J-PlatPat (or Google Patents) で検索式を実行して run を保存。

    body: {
      "formula": "...",
      "formula_level": "narrow" | "medium" | "wide" | "custom",
      "source": "jplatpat" | "google_patents",
      "max_results": 50,
      "auto_click_search": true  # jplatpat のみ
    }
    """
    body = request.get_json() or {}
    formula = (body.get("formula") or "").strip()
    level = body.get("formula_level") or "custom"
    source = body.get("source") or "jplatpat"
    max_results = int(body.get("max_results") or 50)
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

@app.route("/case/<case_id>/search-run/jplatpat/open", methods=["POST"])
def jplatpat_session_open(case_id):
    """J-PlatPat を可視ブラウザで開き、セッションを維持する。"""
    from services.jplatpat_session import get_session
    sess = get_session()
    try:
        r = sess.open(timeout=45)
    except Exception as e:
        return jsonify({"ok": False, "error": f"起動エラー: {e}"}), 500
    return jsonify(r)


@app.route("/case/<case_id>/search-run/jplatpat/fill", methods=["POST"])
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


@app.route("/case/<case_id>/search-run/jplatpat/scrape", methods=["POST"])
def jplatpat_session_scrape(case_id):
    """J-PlatPat の現在の検索結果を取り込み、run として保存。"""
    from services.jplatpat_session import get_session
    from services.search_run_service import create_run_from_hits
    body = request.get_json() or {}
    formula = (body.get("formula") or "").strip()
    level = body.get("formula_level") or "custom"
    max_results = int(body.get("max_results") or 50)
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


@app.route("/case/<case_id>/search-run/jplatpat/status", methods=["GET"])
def jplatpat_session_status(case_id):
    from services.jplatpat_session import get_session
    sess = get_session()
    return jsonify(sess.status())


@app.route("/case/<case_id>/search-run/jplatpat/close", methods=["POST"])
def jplatpat_session_close(case_id):
    from services.jplatpat_session import reset_session
    reset_session()
    return jsonify({"ok": True})


@app.route("/case/<case_id>/search-run/<run_id>/screening", methods=["POST"])
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


@app.route("/case/<case_id>/search-run/<run_id>/screening/bulk", methods=["POST"])
def search_run_screening_bulk(case_id, run_id):
    from services.search_run_service import bulk_update_screening
    body = request.get_json() or {}
    updates = body.get("updates") or []
    data = bulk_update_screening(case_id, run_id, updates)
    if not data:
        return jsonify({"error": "検索ランが見つかりません"}), 404
    return jsonify({"success": True, "updated": len(updates)})


@app.route("/case/<case_id>/search-run/<run_id>/download-starred", methods=["POST"])
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


@app.route("/case/<case_id>/search-run/<run_id>/ai-score", methods=["POST"])
def search_run_ai_score(case_id, run_id):
    """AIで本願関連度スコアを付与 (Phase 2)"""
    from services.search_run_service import ai_score_run
    body = request.get_json() or {}
    try:
        data = ai_score_run(case_id, run_id, limit=int(body.get("limit") or 20))
    except NotImplementedError:
        return jsonify({"error": "AIスコア機能は未実装 (Phase2)"}), 501
    if not data:
        return jsonify({"error": "検索ランが見つかりません"}), 404
    return jsonify({"success": True, "run": data})


@app.route("/case/<case_id>/search-run/feedback/not-terms", methods=["GET"])
def search_run_feedback_not_terms(case_id):
    """却下された候補から NOT 語候補を抽出 (Phase 3)"""
    from services.auto_service import feedback_not_terms
    return jsonify(feedback_not_terms(case_id))


@app.route("/case/<case_id>/search-run/merge", methods=["POST"])
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


@app.route("/case/<case_id>/search-run/<run_id>/enrich", methods=["POST"])
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


@app.route("/case/<case_id>/search-run/<run_id>/diff", methods=["GET"])
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


@app.route("/case/<case_id>/search-run/validate-formula", methods=["POST"])
def search_run_validate_formula(case_id):
    """検索式の括弧バランス・構文チェック。

    body: {"formula": "..."}
    """
    from services.search_run_service import validate_formula
    body = request.get_json() or {}
    formula = body.get("formula") or ""
    return jsonify(validate_formula(formula))


@app.route("/case/<case_id>/search-run/snippets", methods=["GET"])
def search_run_snippets(case_id):
    """検索式エディタ用のキーワード/FI/Fterm 挿入候補を返す。"""
    from services.search_run_service import get_keyword_snippets
    return jsonify(get_keyword_snippets(case_id))


# ===== オートモード =====

@app.route("/auto/run", methods=["POST"])
def auto_run():
    from services.auto_service import auto_run as _auto_run

    data = request.get_json() or {}
    case_ids = data.get("case_ids", [])
    steps = data.get("steps", [
        "keywords", "presearch", "download_citations", "compare", "excel"
    ])

    if not case_ids:
        return jsonify({"error": "案件が選択されていません"}), 400

    return Response(
        _auto_run(case_ids, steps),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    (PROJECT_ROOT / "templates").mkdir(exist_ok=True)
    print("PatentCompare Web GUI")
    print(f"http://{_app_cfg.host}:{_app_cfg.port}  (debug={_app_cfg.debug})")
    if _app_cfg.debug and _app_cfg.host == "0.0.0.0":
        print("!!! WARNING: debug=True + host=0.0.0.0 はLANからコード実行可能です。")
        print("!!!          PATENT_COMPARE_HOST=127.0.0.1 もしくは PATENT_COMPARE_DEBUG=0 推奨。")
    app.run(debug=_app_cfg.debug, host=_app_cfg.host, port=_app_cfg.port)
