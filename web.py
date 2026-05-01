#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PatentCompare Web GUI
シンプルなFlaskベースのWeb UI — ルーティング層
ビジネスロジックは services/ に分離。
"""

import json
import os
import re
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
        application_number=(data.get("application_number") or "").strip(),
    )
    if "error" in result and "case_id" in result and result.get("error") and not result.get("success"):
        return jsonify(result), 409
    return jsonify(result)


@app.route("/case/parse-batch", methods=["POST"])
def parse_batch_input():
    """複数行の特許番号入力をパースしてプレビュー用リストを返す。"""
    from services.case_service import _parse_patent_input, get_case_dir
    data = request.get_json() or {}
    text = data.get("text", "") or ""
    entries = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        p = _parse_patent_input(s)
        exists = p.get("case_id") and get_case_dir(p["case_id"]).exists()
        entries.append({
            "original": s,
            "kind": p["kind"],
            "case_id": p["case_id"],
            "patent_number": p["patent_number"],
            "application_number": p["application_number"],
            "exists": bool(exists),
        })
    return jsonify({"entries": entries})


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
    if keywords:
        # F-term の desc が空なら辞書から補完して表示する (in-place)
        from services.keyword_service import enrich_fterm_groups
        field = (meta or {}).get("field", "cosmetics")
        enrich_fterm_groups(keywords, field)
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

    # 予備調査タブ用 (Step 2 サブタブ): 利用可能分野リストとデフォルト分野
    from services.preliminary_research_service import (
        list_available_fields as _prelim_fields,
    )
    prelim_fields = _prelim_fields() or ["generic"]
    prelim_default_field = (
        meta.get("field") if meta.get("field") in prelim_fields else None
    ) or _load_prelim_default() or (
        "cosmetics" if "cosmetics" in prelim_fields else prelim_fields[0]
    )

    # 分節↔対比 整合性 (Step 5/6 のバナー + Step 2 保存時の警告に使う)
    from services.comparison_service import check_segments_freshness
    freshness, _ = check_segments_freshness(case_id)
    if not isinstance(freshness, dict):
        freshness = {}

    return render_template("case.html",
                           meta=meta, hongan=hongan, segments=segments,
                           keywords=keywords, citations=citations,
                           related_paragraphs=related_paragraphs,
                           excel_files=excel_files, case_id=case_id,
                           prelim_fields=prelim_fields,
                           prelim_default_field=prelim_default_field,
                           freshness=freshness)


def _load_prelim_default():
    """config.yaml の preliminary_research.default_field を読む (キャッシュなし、軽量なので毎回 OK)"""
    try:
        import yaml as _yaml
        cfg_path = PROJECT_ROOT / "config.yaml"
        if not cfg_path.exists():
            return None
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = _yaml.safe_load(f) or {}
        return (cfg.get("preliminary_research") or {}).get("default_field")
    except Exception:
        return None


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


@app.route("/case/<case_id>/segments/freshness", methods=["GET"])
def segments_freshness(case_id):
    """現在の segments.json と responses/*.json の整合性チェック (silent stale 検出)"""
    from services.comparison_service import check_segments_freshness
    return _svc_response(check_segments_freshness(case_id))


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


@app.route("/case/<case_id>/keywords/rebuild-from-tech-analysis", methods=["POST"])
def rebuild_keywords_from_tech_analysis_route(case_id):
    from services.keyword_service import rebuild_keywords_from_tech_analysis
    return _svc_response(rebuild_keywords_from_tech_analysis(case_id))


@app.route("/case/<case_id>/keywords/fterm/candidates", methods=["GET"])
def fterm_candidates(case_id):
    from services.keyword_service import fterm_candidates
    return _svc_response(fterm_candidates(case_id))


@app.route("/case/<case_id>/hongan/classification/fetch", methods=["POST"])
def fetch_hongan_classification(case_id):
    """本願の公開番号で J-PlatPat に問い合わせて書誌情報 (IPC/FI/Fターム/テーマ) を取得・保存"""
    from services.case_service import fetch_hongan_classification_from_jplatpat
    return _svc_response(fetch_hongan_classification_from_jplatpat(case_id))


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


@app.route("/case/<case_id>/citation/<citation_id>/paragraph/<para_id>", methods=["GET"])
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


@app.route("/case/<case_id>/export/excel", methods=["POST"])
def export_excel(case_id):
    from services.comparison_service import export_excel
    body = request.get_json(silent=True) or {}
    selected = body.get("citation_ids")  # None なら全件、リストなら絞り込み
    return _svc_response(export_excel(case_id, selected_citation_ids=selected))


@app.route("/case/<case_id>/download/<path:filename>")
def download_file(case_id, filename):
    case_dir = get_case_dir(case_id)
    file_path = case_dir / "output" / filename
    if not file_path.exists():
        flash("ファイルが見つかりません。", "error")
        return redirect(url_for("case_detail", case_id=case_id))
    return send_file(str(file_path), as_attachment=True)


def _resolve_hongan_pdf_path(case_id):
    """本願PDFの実ファイルパスを解決。見つからなければ None。

    優先順位:
      1. hongan.json の source_pdf (作成時に記録)
      2. patent_number で始まる PDF (例: 特開2024-051653.pdf)
      3. case_id 文字列を含む PDF (例: 2024-051653 を含むファイル)
      4. JP{year}{serial} 形式の PDF (例: JP2024051653A.pdf)
      5. input/ の中で引用文献 ID を含まない最初の PDF (最終フォールバック)
    """
    import re as _re
    case_dir = get_case_dir(case_id)
    hongan_path = case_dir / "hongan.json"
    meta = load_case_meta(case_id) or {}
    input_dir = case_dir / "input"

    patent_number = ""
    if hongan_path.exists():
        try:
            with open(hongan_path, "r", encoding="utf-8") as f:
                hongan = json.load(f)
            # 1. source_pdf に記録された名前
            src = hongan.get("source_pdf")
            if src:
                candidate = input_dir / Path(str(src)).name
                if candidate.exists():
                    return candidate
            patent_number = (hongan.get("patent_number") or "").strip()
        except Exception:
            pass
    if not patent_number:
        patent_number = (meta.get("patent_number") or "").strip()

    if not input_dir.exists():
        return None

    # 2. ファイル名に「本願」を含むものは最優先 (ユーザーが手動マーク)
    for p in input_dir.glob("*.pdf"):
        if "本願" in p.stem:
            return p

    # 3. patent_number 一致 (前方一致)
    if patent_number:
        for p in input_dir.glob("*.pdf"):
            if p.stem == patent_number:
                return p
        for p in input_dir.glob("*.pdf"):
            if p.stem.startswith(patent_number):
                return p

    # 4. case_id 文字列を stem に含む (例: 2024-051653 を含む)
    if case_id:
        for p in input_dir.glob("*.pdf"):
            if case_id in p.stem:
                return p

    # 5. JP{year}{serial}A / JPA{year}{serial} 形式 (Google Patents/J-PlatPat DL 名)
    m = _re.match(r'^(\d{4})-(\d+)$', case_id or "")
    if m:
        joined = f"{m.group(1)}{m.group(2).zfill(6)}"  # 例: 2024051653
        for p in input_dir.glob("*.pdf"):
            if joined in p.stem:
                return p

    # 5. 最終フォールバック: 引用文献 ID を含まない PDF (alphabetically first を避け、
    #    並びを安定させるため stem 長さでソートして「短い名前 = 余計な接頭辞のないもの」優先)
    cit_ids = [c["id"] for c in meta.get("citations", [])]
    candidates = [
        p for p in input_dir.glob("*.pdf")
        if not any(cid and cid in p.stem for cid in cit_ids)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: (len(p.stem), p.name))
    return candidates[0]


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


@app.route("/case/<case_id>/hongan/extract-tables", methods=["POST"])
def extract_hongan_tables(case_id):
    """本願 PDF から実施例表を Vision で抽出 (SSE で進捗配信)。

    クライアント側は EventSource で接続し data: <json> を順次受信。
    最終 stage="done" イベントの後、GET /case/<id>/hongan/tables で全文取得する。
    """
    from services.case_service import stream_hongan_table_extraction
    return Response(
        stream_hongan_table_extraction(case_id),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/case/<case_id>/hongan/tables", methods=["GET"])
def get_hongan_tables_route(case_id):
    """抽出済みの本願表データを返す。"""
    from services.case_service import get_hongan_tables
    result, code = get_hongan_tables(case_id)
    return jsonify(result), code


@app.route("/case/<case_id>/citation/<path:citation_id>/extract-tables",
           methods=["POST"])
def extract_citation_tables_route(case_id, citation_id):
    """1 件の引用文献から表抽出 (SSE で進捗配信)。"""
    from services.case_service import stream_citation_table_extraction
    body = request.get_json(silent=True) or {}
    force = bool(body.get("force"))
    return Response(
        stream_citation_table_extraction(case_id, citation_id, force=force),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/case/<case_id>/citations/extract-tables-bulk", methods=["POST"])
def extract_citation_tables_bulk_route(case_id):
    """複数の引用文献を順次抽出 (SSE)。
    body: {"citation_ids": ["JP2024-051653", ...], "force": false}
    """
    from services.case_service import stream_bulk_citation_table_extraction
    body = request.get_json(silent=True) or {}
    cids = body.get("citation_ids") or []
    force = bool(body.get("force"))
    if not cids:
        return jsonify({"error": "citation_ids が空です"}), 400
    return Response(
        stream_bulk_citation_table_extraction(case_id, cids, force=force),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/case/<case_id>/citation/<path:citation_id>/tables", methods=["GET"])
def get_citation_tables_route(case_id, citation_id):
    """1 件の引用文献の抽出済み表データを返す。"""
    from services.case_service import get_citation_tables
    result, code = get_citation_tables(case_id, citation_id)
    return jsonify(result), code


@app.route("/case/<case_id>/citations/tables-status", methods=["GET"])
def get_citation_tables_status_route(case_id):
    """全引用文献の表抽出状況を返す。"""
    from services.case_service import list_citation_table_status
    result, code = list_citation_table_status(case_id)
    return jsonify(result), code


@app.route("/case/<case_id>/citations/tables-cells", methods=["GET"])
def get_citation_tables_cells_route(case_id):
    """全引用文献の抽出済みセル文字列マップを返す (PKM ヒット集計用)。"""
    from services.case_service import get_citation_tables_cells
    result, code = get_citation_tables_cells(case_id)
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


@app.route("/case/<case_id>/search-run/hit/<path:patent_id>/text", methods=["GET"])
def get_hit_full_text(case_id, patent_id):
    """キャッシュ済みヒット全文を返す（無ければ 404）。"""
    from services.search_run_service import get_hit_text
    d = get_hit_text(case_id, patent_id)
    if d is None:
        return jsonify({"error": "未取得"}), 404
    return jsonify(d)


@app.route("/case/<case_id>/search-run/hit/<path:patent_id>/view")
def hit_full_text_view(case_id, patent_id):
    """ヒットの全文ハイライトビュー（新タブで開く想定）。

    キャッシュが無ければ自動取得する。Step 3 のキーワードグループの色で
    `<mark>` を重畳し、グループ別ヒット数を凡例として表示。
    """
    from services.search_run_service import (
        get_hit_text, _default_text_source,
        pkm_build_index, pkm_highlight_python, pkm_group_color,
    )
    hit = get_hit_text(case_id, patent_id)
    if not hit:
        # キャッシュ無し: 即座に取得中ローダーページを返し、ブラウザ側で fetch → reload
        src = _default_text_source(patent_id)
        src_label = {"jplatpat": "J-PlatPat", "google": "Google Patents"}.get(src, src)
        return render_template(
            "hit_view_loading.html",
            patent_id=patent_id,
            case_id=case_id,
            source=src,
            source_label=src_label,
        )
    if "error" in hit and not hit.get("description") and not hit.get("claims"):
        return render_template(
            "hit_view.html",
            patent_id=patent_id,
            title="（取得失敗）",
            source="google",
            source_label="エラー",
            source_url="",
            google_url=f"https://patents.google.com/?q={patent_id}",
            jplatpat_url="",
            groups=[],
            abstract_unit={"html": f'<div class="empty">エラー: {hit.get("error","")}</div>', "groups": []},
            claims_units=[],
            para_units=[],
            total_hits=0,
            total_chars=0,
        )

    keywords = load_json_file(case_id, "keywords.json") or []
    index = pkm_build_index(keywords)

    counts_total = {}

    def _accum(c):
        for k, v in (c or {}).items():
            counts_total[k] = counts_total.get(k, 0) + v

    # Highlight 要約 (1 unit)
    abstract_h = pkm_highlight_python(hit.get("abstract") or "", index)
    _accum(abstract_h["counts"])
    abstract_unit = {
        "html": abstract_h["html"],
        "groups": sorted(int(g) for g in abstract_h["counts"].keys() if g is not None),
    } if abstract_h["html"] else None

    # Highlight 請求項 (各 claim を unit)
    claims_units = []
    for cl in (hit.get("claims") or []):
        ch = pkm_highlight_python(cl, index)
        _accum(ch["counts"])
        claims_units.append({
            "html": ch["html"],
            "groups": sorted(int(g) for g in ch["counts"].keys() if g is not None),
        })

    # Highlight 明細書本文 — 段落マーカー【XXXX】単位で分割
    fw2hw = str.maketrans("０１２３４５６７８９", "0123456789")
    desc = hit.get("description") or ""
    para_units = []
    if desc:
        # 全角・半角どちらでも【\d+】を捕捉
        parts = re.split(r'(【\s*[\d０-９]+\s*】)', desc)
        # parts[0] は最初のマーカー前のリード（例「発明の詳細な説明】【技術分野】」など）。
        if parts and parts[0].strip():
            head = pkm_highlight_python(parts[0], index)
            _accum(head["counts"])
            para_units.append({
                "pid": "",
                "marker": "",
                "html": head["html"],
                "groups": sorted(int(g) for g in head["counts"].keys() if g is not None),
            })
        for i in range(1, len(parts) - 1, 2):
            marker = (parts[i] or "").strip()
            body = parts[i + 1] if (i + 1) < len(parts) else ""
            m_pid = re.search(r'(\d+)', marker.translate(fw2hw))
            pid = m_pid.group(1).zfill(4) if m_pid else ""
            ph = pkm_highlight_python(body, index)
            _accum(ph["counts"])
            para_units.append({
                "pid": pid,
                "marker": marker,
                "html": ph["html"],
                "groups": sorted(int(g) for g in ph["counts"].keys() if g is not None),
            })

    groups_view = []
    for g in keywords:
        gid = g.get("group_id")
        groups_view.append({
            "gid": gid,
            "label": g.get("label") or f"group{gid}",
            "color": pkm_group_color(gid),
            "count": counts_total.get(gid, 0),
        })
    groups_view.sort(key=lambda x: -x["count"])

    src = (hit.get("source") or "google").lower()
    src_label = {
        "jplatpat": "J-PlatPat",
        "google": "Google Patents",
        "google_fallback": "Google Patents",  # 取得元としては Google。経緯は表示しない
    }.get(src, src)

    # Build cross-source URLs
    from modules.jplatpat_client import build_jplatpat_fixed_url
    jpp_url = build_jplatpat_fixed_url(patent_id)
    gp_url = f"https://patents.google.com/?q={patent_id}"
    src_url = hit.get("url") or (jpp_url if src == "jplatpat" else gp_url)

    total_chars = (
        len(hit.get("abstract") or "")
        + len(hit.get("description") or "")
        + sum(len(c or "") for c in (hit.get("claims") or []))
    )

    images = hit.get("images") or []

    # 抽出済みの表データを image src で対応付ける (image_records ベース抽出時のみ)
    extracted_tables_by_src: dict = {}
    extracted_tables_by_label: dict = {}
    try:
        from services.case_service import get_citation_tables
        ct_res, ct_code = get_citation_tables(case_id, patent_id)
        if ct_code == 200 and ct_res.get("exists"):
            for t in (ct_res.get("data", {}).get("tables") or []):
                if not t.get("is_table"):
                    continue
                src_url = t.get("src")
                if src_url:
                    extracted_tables_by_src[src_url] = t
                # キャプションラベル(【表1】等)でも引けるようにフォールバック
                lbl = t.get("caption_label") or t.get("title")
                if lbl:
                    extracted_tables_by_label[lbl] = t
    except Exception:
        pass

    # images に表抽出結果を埋め込む (テンプレート側で参照)。
    # 各セルにも PKM ハイライトを適用してハイライト数を全体カウントに合算。
    for im in images:
        src_url = im.get("src")
        match = extracted_tables_by_src.get(src_url) if src_url else None
        if not match:
            # ラベル一致もチェック (PDF 由来の場合 src は無いがラベルで対応)
            lbl = im.get("label")
            if lbl:
                match = extracted_tables_by_label.get(lbl)
        if not match:
            continue
        headers = match.get("headers") or []
        rows = match.get("rows") or []
        # ヘッダ・各セルにハイライト適用
        headers_h = []
        for h in headers:
            hh = pkm_highlight_python(str(h), index)
            _accum(hh["counts"])
            headers_h.append(hh["html"])
        rows_h = []
        unit_groups: set = set()
        for row in rows:
            cells = row.get("cells") or []
            cells_h = []
            for c in cells:
                ch = pkm_highlight_python(str(c), index)
                _accum(ch["counts"])
                for g in ch["counts"].keys():
                    if g is not None:
                        unit_groups.add(int(g))
                cells_h.append(ch["html"])
            rows_h.append(cells_h)
        im["extracted"] = {
            "title": match.get("title") or im.get("label"),
            "headers_html": headers_h,
            "rows_html": rows_h,
            "n_rows": len(rows),
            "groups": sorted(unit_groups),
        }

    return render_template(
        "hit_view.html",
        patent_id=patent_id,
        title=hit.get("title") or "",
        source=src,
        source_label=src_label,
        source_url=src_url,
        google_url=gp_url,
        jplatpat_url=jpp_url,
        groups=groups_view,
        abstract_unit=abstract_unit,
        claims_units=claims_units,
        para_units=para_units,
        images=images,
        total_hits=sum(counts_total.values()),
        total_chars=total_chars,
    )


@app.route("/case/<case_id>/search-run/hit/<path:patent_id>/fetch-text", methods=["POST"])
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
        # limit=None で未スコアの全件を処理。明示で {"limit": N} 指定があれば従う。
        raw_limit = body.get("limit")
        limit = int(raw_limit) if raw_limit not in (None, "", 0) else None
        data = ai_score_run(case_id, run_id, limit=limit)
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


# ===== 本願分析 (Step 2 サブタブ SUB 3) =====

@app.route("/case/<case_id>/hongan-analysis", methods=["GET"])
def hongan_analysis_load(case_id):
    """既存の分析結果 (cases/<id>/analysis/hongan_analysis.json) を返す"""
    from services.hongan_analysis_service import load_existing_analysis
    return _svc_response(load_existing_analysis(case_id))


@app.route("/case/<case_id>/hongan-analysis/run", methods=["POST"])
def hongan_analysis_run(case_id):
    """本願分析テンプレートを実行 (auto + LLM 一括) して保存・返却する"""
    from services.hongan_analysis_service import run_analysis
    data = request.get_json() or {}
    version = data.get("version") or "v0.1"
    skip_llm = bool(data.get("skip_llm"))
    return _svc_response(run_analysis(case_id, version=version, skip_llm=skip_llm))


@app.route("/case/<case_id>/hongan-analysis/item", methods=["POST"])
def hongan_analysis_update_item(case_id):
    """単一項目の value を更新 (ユーザー編集による下線/ハイライトの反映)"""
    from services.hongan_analysis_service import update_item_value
    data = request.get_json() or {}
    return _svc_response(
        update_item_value(case_id, data.get("item_id", ""), data.get("value"))
    )


# ===== 予備調査 (Step 2 サブタブ) =====

@app.route("/api/preliminary_research/fields", methods=["GET"])
def prelim_list_fields():
    """利用可能な分野レシピのスラッグ一覧"""
    from services.preliminary_research_service import list_available_fields
    return jsonify({"fields": list_available_fields()})


@app.route("/api/preliminary_research/expand_synonyms", methods=["POST"])
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


@app.route("/api/preliminary_research/generate_urls", methods=["POST"])
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


@app.route("/api/preliminary_research/save_note", methods=["POST"])
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
