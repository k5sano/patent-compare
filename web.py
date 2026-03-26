#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PatentCompare Web GUI
シンプルなFlaskベースのWeb UI
"""

import os
import json
import shutil
import yaml
from pathlib import Path
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, send_file
)

PROJECT_ROOT = Path(__file__).parent.resolve()

app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))
app.secret_key = "patent-compare-dev-key"
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100MB
app.json.ensure_ascii = False  # tojsonで日本語をエスケープしない


# ===== ヘルパー =====

def get_cases_dir():
    return PROJECT_ROOT / "cases"


def get_case_dir(case_id):
    return get_cases_dir() / case_id


def load_case_meta(case_id):
    p = get_case_dir(case_id) / "case.yaml"
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return None


def save_case_meta(case_id, meta):
    p = get_case_dir(case_id) / "case.yaml"
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(meta, f, allow_unicode=True, default_flow_style=False)


def list_all_cases():
    cases_dir = get_cases_dir()
    cases = []
    if cases_dir.exists():
        for d in sorted(cases_dir.iterdir()):
            if d.is_dir() and (d / "case.yaml").exists():
                meta = load_case_meta(d.name)
                if meta:
                    # 進捗状況を計算
                    has_hongan = (d / "hongan.json").exists()
                    has_segments = (d / "segments.json").exists()
                    has_keywords = (d / "keywords.json").exists()
                    num_citations = len(list((d / "citations").glob("*.json"))) if (d / "citations").exists() else 0
                    num_responses = len(list((d / "responses").glob("*.json"))) if (d / "responses").exists() else 0
                    has_excel = any((d / "output").glob("*.xlsx")) if (d / "output").exists() else False

                    meta["_has_hongan"] = has_hongan
                    meta["_has_segments"] = has_segments
                    meta["_has_keywords"] = has_keywords
                    meta["_num_citations"] = num_citations
                    meta["_num_responses"] = num_responses
                    meta["_has_excel"] = has_excel
                    cases.append(meta)
    return cases


# ===== ルーティング =====

@app.route("/")
def index():
    cases = list_all_cases()
    return render_template("index.html", cases=cases)


@app.route("/case/new", methods=["POST"])
def new_case():
    case_id = request.form.get("case_id", "").strip()
    title = request.form.get("title", "").strip()
    field = request.form.get("field", "cosmetics")

    if not case_id:
        flash("案件番号を入力してください。", "error")
        return redirect(url_for("index"))

    case_dir = get_case_dir(case_id)
    if case_dir.exists():
        flash(f"案件 '{case_id}' は既に存在します。", "error")
        return redirect(url_for("index"))

    for sub in ["input", "citations", "prompts", "responses", "output"]:
        (case_dir / sub).mkdir(parents=True, exist_ok=True)

    meta = {
        "case_id": case_id,
        "title": title,
        "field": field,
        "citations": [],
    }
    save_case_meta(case_id, meta)
    flash(f"案件 '{case_id}' を作成しました。", "success")
    return redirect(url_for("case_detail", case_id=case_id))


@app.route("/case/<case_id>")
def case_detail(case_id):
    meta = load_case_meta(case_id)
    if not meta:
        flash("案件が見つかりません。", "error")
        return redirect(url_for("index"))

    case_dir = get_case_dir(case_id)

    # 各種データ読み込み
    hongan = None
    if (case_dir / "hongan.json").exists():
        with open(case_dir / "hongan.json", "r", encoding="utf-8") as f:
            hongan = json.load(f)

    segments = None
    if (case_dir / "segments.json").exists():
        with open(case_dir / "segments.json", "r", encoding="utf-8") as f:
            segments = json.load(f)

    keywords = None
    if (case_dir / "keywords.json").exists():
        with open(case_dir / "keywords.json", "r", encoding="utf-8") as f:
            keywords = json.load(f)

    # 引用文献一覧
    citations = []
    for cit in meta.get("citations", []):
        cit_path = case_dir / "citations" / f"{cit['id']}.json"
        resp_path = case_dir / "responses" / f"{cit['id']}.json"
        cit["_has_data"] = cit_path.exists()
        cit["_has_response"] = resp_path.exists()
        citations.append(cit)

    # Excel存在チェック
    excel_files = list((case_dir / "output").glob("*.xlsx")) if (case_dir / "output").exists() else []

    return render_template("case.html",
                           meta=meta, hongan=hongan, segments=segments,
                           keywords=keywords, citations=citations,
                           excel_files=excel_files, case_id=case_id)


@app.route("/case/<case_id>/upload/hongan", methods=["POST"])
def upload_hongan(case_id):
    from modules.pdf_extractor import extract_patent_pdf
    from modules.claim_segmenter import segment_claims

    meta = load_case_meta(case_id)
    if not meta:
        return jsonify({"error": "案件が見つかりません"}), 404

    if "file" not in request.files:
        return jsonify({"error": "ファイルがありません"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "ファイルが選択されていません"}), 400

    case_dir = get_case_dir(case_id)
    # PDFを保存
    save_path = case_dir / "input" / file.filename
    file.save(str(save_path))

    # テキスト抽出
    result = extract_patent_pdf(str(save_path), "hongan")
    with open(case_dir / "hongan.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 特許番号・題名をcase.yamlに反映
    extracted_number = result.get("patent_number", "")
    extracted_title = result.get("patent_title", "")
    if extracted_number:
        meta["patent_number"] = extracted_number
    if extracted_title:
        meta["patent_title"] = extracted_title
    save_case_meta(case_id, meta)

    # 自動的に分節も実行
    if result.get("claims"):
        segs = segment_claims(result["claims"])
        with open(case_dir / "segments.json", "w", encoding="utf-8") as f:
            json.dump(segs, f, ensure_ascii=False, indent=2)

    return jsonify({
        "success": True,
        "patent_number": extracted_number,
        "patent_title": extracted_title,
        "num_claims": len(result.get("claims", [])),
        "num_paragraphs": len(result.get("paragraphs", [])),
    })


@app.route("/case/<case_id>/upload/citation", methods=["POST"])
def upload_citation(case_id):
    from modules.pdf_extractor import extract_patent_pdf

    meta = load_case_meta(case_id)
    if not meta:
        return jsonify({"error": "案件が見つかりません"}), 404

    if "file" not in request.files:
        return jsonify({"error": "ファイルがありません"}), 400

    file = request.files["file"]
    role = request.form.get("role", "主引例")
    label = request.form.get("label", "")

    case_dir = get_case_dir(case_id)
    save_path = case_dir / "input" / file.filename
    file.save(str(save_path))

    result = extract_patent_pdf(str(save_path), "citation")
    doc_id = result.get("patent_number", Path(file.filename).stem)
    result["role"] = role
    result["label"] = label or doc_id

    with open(case_dir / "citations" / f"{doc_id}.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # case.yamlに追加
    citations = meta.get("citations", [])
    # 重複チェック
    if not any(c["id"] == doc_id for c in citations):
        citations.append({"id": doc_id, "role": role, "label": label or doc_id})
        meta["citations"] = citations
        save_case_meta(case_id, meta)

    return jsonify({
        "success": True,
        "doc_id": doc_id,
        "num_claims": len(result.get("claims", [])),
        "num_paragraphs": len(result.get("paragraphs", [])),
    })


@app.route("/case/<case_id>/citation/<citation_id>", methods=["DELETE"])
def delete_citation(case_id, citation_id):
    """引用文献を個別削除"""
    meta = load_case_meta(case_id)
    if not meta:
        return jsonify({"error": "案件が見つかりません"}), 404

    case_dir = get_case_dir(case_id)

    # ファイル削除
    for subdir in ["citations", "responses", "prompts"]:
        for pattern in [f"{citation_id}.json", f"{citation_id}_prompt.txt", f"*{citation_id}*"]:
            for f in (case_dir / subdir).glob(pattern):
                f.unlink()

    # case.yamlから除去
    meta["citations"] = [c for c in meta.get("citations", []) if c["id"] != citation_id]
    save_case_meta(case_id, meta)

    return jsonify({"success": True})


@app.route("/case/<case_id>/citations/clear", methods=["DELETE"])
def clear_all_citations(case_id):
    """全引用文献・回答をクリア"""
    meta = load_case_meta(case_id)
    if not meta:
        return jsonify({"error": "案件が見つかりません"}), 404

    case_dir = get_case_dir(case_id)

    # citations, responses, prompts 内の全ファイルを削除
    for subdir in ["citations", "responses", "prompts"]:
        d = case_dir / subdir
        if d.exists():
            for f in d.iterdir():
                if f.is_file():
                    f.unlink()

    # case.yamlのcitationsリストをクリア
    meta["citations"] = []
    save_case_meta(case_id, meta)

    return jsonify({"success": True})


@app.route("/case/<case_id>/segments", methods=["GET"])
def get_segments(case_id):
    case_dir = get_case_dir(case_id)
    path = case_dir / "segments.json"
    if not path.exists():
        return jsonify({"error": "分節データがありません"}), 404
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return jsonify(data)


@app.route("/case/<case_id>/segments", methods=["POST"])
def save_segments(case_id):
    case_dir = get_case_dir(case_id)
    data = request.get_json()
    with open(case_dir / "segments.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return jsonify({"success": True})


@app.route("/case/<case_id>/keywords/suggest", methods=["POST"])
def suggest_keywords(case_id):
    from modules.keyword_suggester import suggest_keywords as _suggest

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)

    hongan_path = case_dir / "hongan.json"
    segments_path = case_dir / "segments.json"
    if not hongan_path.exists() or not segments_path.exists():
        return jsonify({"error": "本願テキストまたは分節データがありません"}), 400

    with open(hongan_path, "r", encoding="utf-8") as f:
        hongan = json.load(f)
    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    field = meta.get("field", "cosmetics")
    result = _suggest(hongan, segs, field)

    with open(case_dir / "keywords.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return jsonify(result)


@app.route("/case/<case_id>/prompt", methods=["POST"])
def generate_prompt_multi(case_id):
    """複数文献対応のプロンプト生成"""
    from modules.prompt_generator import generate_prompt as _gen

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    segments_path = case_dir / "segments.json"

    if not segments_path.exists():
        return jsonify({"error": "分節データがありません"}), 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    # リクエストから対象文献IDリストを取得
    data = request.get_json() or {}
    citation_ids = data.get("citation_ids", [])

    if not citation_ids:
        return jsonify({"error": "対象文献を選択してください"}), 400

    # 引用文献を読み込み
    citations = []
    for cit_id in citation_ids:
        cit_path = case_dir / "citations" / f"{cit_id}.json"
        if not cit_path.exists():
            return jsonify({"error": f"引用文献 '{cit_id}' が見つかりません"}), 404
        with open(cit_path, "r", encoding="utf-8") as f:
            citations.append(json.load(f))

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    field = meta.get("field", "cosmetics")
    prompt_text = _gen(segs, citations, keywords, field)

    # ファイルにも保存
    ids_label = "_".join(citation_ids)
    prompt_path = case_dir / "prompts" / f"{ids_label}_prompt.txt"
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt_text)

    return jsonify({
        "prompt": prompt_text,
        "char_count": len(prompt_text),
        "num_citations": len(citations),
    })


@app.route("/case/<case_id>/prompt/<citation_id>", methods=["GET"])
def generate_prompt_single(case_id, citation_id):
    """後方互換: 単一文献のプロンプト生成"""
    from modules.prompt_generator import generate_prompt as _gen

    case_dir = get_case_dir(case_id)
    segments_path = case_dir / "segments.json"
    citation_path = case_dir / "citations" / f"{citation_id}.json"

    if not segments_path.exists():
        return jsonify({"error": "分節データがありません"}), 400
    if not citation_path.exists():
        return jsonify({"error": f"引用文献 '{citation_id}' が見つかりません"}), 404

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)
    with open(citation_path, "r", encoding="utf-8") as f:
        citation = json.load(f)

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    prompt_text = _gen(segs, citation, keywords)

    prompt_path = case_dir / "prompts" / f"{citation_id}_prompt.txt"
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt_text)

    return jsonify({"prompt": prompt_text, "char_count": len(prompt_text)})


@app.route("/case/<case_id>/response", methods=["POST"])
def save_response_multi(case_id):
    """複数文献対応の回答パース・保存"""
    from modules.response_parser import parse_response, split_multi_response

    case_dir = get_case_dir(case_id)
    segments_path = case_dir / "segments.json"

    if not segments_path.exists():
        return jsonify({"error": "分節データがありません"}), 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    raw_text = request.get_json().get("text", "")
    if not raw_text.strip():
        return jsonify({"error": "テキストが空です"}), 400

    all_segment_ids = []
    for claim in segs:
        for seg in claim["segments"]:
            all_segment_ids.append(seg["id"])

    result, errors = parse_response(raw_text, all_segment_ids)

    saved_docs = []
    if result:
        # 文献ごとに分割して保存
        per_doc = split_multi_response(result)
        for doc_id, doc_result in per_doc.items():
            resp_path = case_dir / "responses" / f"{doc_id}.json"
            with open(resp_path, "w", encoding="utf-8") as f:
                json.dump(doc_result, f, ensure_ascii=False, indent=2)
            saved_docs.append(doc_id)

    return jsonify({
        "success": result is not None,
        "errors": errors,
        "saved_docs": saved_docs,
        "num_docs": len(saved_docs),
    })


@app.route("/case/<case_id>/response/<citation_id>", methods=["POST"])
def save_response_single(case_id, citation_id):
    """後方互換: 単一文献の回答パース"""
    from modules.response_parser import parse_response

    case_dir = get_case_dir(case_id)
    segments_path = case_dir / "segments.json"

    if not segments_path.exists():
        return jsonify({"error": "分節データがありません"}), 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    raw_text = request.get_json().get("text", "")
    if not raw_text.strip():
        return jsonify({"error": "テキストが空です"}), 400

    all_segment_ids = []
    for claim in segs:
        for seg in claim["segments"]:
            all_segment_ids.append(seg["id"])

    result, errors = parse_response(raw_text, all_segment_ids)

    if result:
        resp_path = case_dir / "responses" / f"{citation_id}.json"
        with open(resp_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    return jsonify({
        "success": result is not None,
        "errors": errors,
        "data": result,
    })


@app.route("/case/<case_id>/export/excel", methods=["POST"])
def export_excel(case_id):
    from modules.excel_writer import write_comparison_table

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    segments_path = case_dir / "segments.json"

    if not segments_path.exists():
        return jsonify({"error": "分節データがありません"}), 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    responses = {}
    responses_dir = case_dir / "responses"
    if responses_dir.exists():
        for rfile in responses_dir.glob("*.json"):
            with open(rfile, "r", encoding="utf-8") as f:
                responses[rfile.stem] = json.load(f)

    if not responses:
        return jsonify({"error": "回答データがありません"}), 400

    citations_meta = {}
    for cit in meta.get("citations", []):
        cit_path = case_dir / "citations" / f"{cit['id']}.json"
        if cit_path.exists():
            with open(cit_path, "r", encoding="utf-8") as f:
                citations_meta[cit["id"]] = json.load(f)

    output_path = case_dir / "output" / f"{meta['case_id']}_対比表.xlsx"
    write_comparison_table(
        output_path=str(output_path),
        case_meta=meta,
        segments=segs,
        responses=responses,
        citations_meta=citations_meta,
    )

    return jsonify({"success": True, "filename": output_path.name})


@app.route("/case/<case_id>/download/<path:filename>")
def download_file(case_id, filename):
    case_dir = get_case_dir(case_id)
    file_path = case_dir / "output" / filename
    if not file_path.exists():
        flash("ファイルが見つかりません。", "error")
        return redirect(url_for("case_detail", case_id=case_id))
    return send_file(str(file_path), as_attachment=True)


@app.route("/case/<case_id>/meta", methods=["POST"])
def update_case_meta(case_id):
    """案件メタ情報（特許番号・題名等）を更新"""
    meta = load_case_meta(case_id)
    if not meta:
        return jsonify({"error": "案件が見つかりません"}), 404

    data = request.get_json() or {}
    for key in ["patent_number", "patent_title", "title", "field"]:
        if key in data:
            meta[key] = data[key]
    save_case_meta(case_id, meta)
    return jsonify({"success": True})


@app.route("/case/<case_id>/delete", methods=["POST"])
def delete_case(case_id):
    case_dir = get_case_dir(case_id)
    if case_dir.exists():
        shutil.rmtree(str(case_dir))
    flash(f"案件 '{case_id}' を削除しました。", "success")
    return redirect(url_for("index"))


if __name__ == "__main__":
    # templates ディレクトリがなければ作成
    (PROJECT_ROOT / "templates").mkdir(exist_ok=True)
    print("PatentCompare Web GUI")
    print("http://localhost:5000")
    app.run(debug=True, host="0.0.0.0", port=5000)
