#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PatentCompare Web GUI
シンプルなFlaskベースのWeb UI — ルーティング層
ビジネスロジックは services/ に分離。
"""

import json
import yaml
from pathlib import Path
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, send_file, Response
)

from services.case_service import (
    get_case_dir, load_case_meta, list_all_cases,
    load_json_file, find_citation_pdf,
)

PROJECT_ROOT = Path(__file__).parent.resolve()

app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))
app.secret_key = "patent-compare-dev-key"
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100MB
app.json.ensure_ascii = False


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

    case_dir = get_case_dir(case_id)

    hongan = load_json_file(case_id, "hongan.json")
    segments = load_json_file(case_id, "segments.json")
    keywords = load_json_file(case_id, "keywords.json")

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


@app.route("/case/<case_id>/annotate/<citation_id>", methods=["POST"])
def annotate_citation(case_id, citation_id):
    from services.comparison_service import annotate_citation
    return _svc_response(annotate_citation(case_id, citation_id))


@app.route("/case/<case_id>/annotate/all", methods=["POST"])
def annotate_all_citations(case_id):
    from services.comparison_service import annotate_all_citations
    return _svc_response(annotate_all_citations(case_id))


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
    print("http://localhost:5000")
    app.run(debug=True, host="0.0.0.0", port=5000)
