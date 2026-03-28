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


def _get_search_dir(case_dir):
    """search/ サブディレクトリを返す（なければ作成）"""
    d = Path(case_dir) / "search"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_search_data(case_dir, filename):
    """search/ 配下のJSONファイルを読み込む（なければ None）"""
    p = Path(case_dir) / "search" / filename
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _save_search_data(case_dir, filename, data):
    """search/ 配下にJSONファイルを保存"""
    d = _get_search_dir(case_dir)
    with open(d / filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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
    """新規案件作成: 公開番号から本願PDFを自動DL・抽出"""
    import re as _re
    from modules.patent_downloader import download_patent_pdf
    from modules.pdf_extractor import extract_patent_pdf
    from modules.claim_segmenter import segment_claims

    data = request.get_json() or {}
    case_number = (data.get("case_number") or "").strip()
    year = data.get("year", "")
    month = data.get("month", "")
    field = data.get("field", "cosmetics")

    if not case_number:
        return jsonify({"error": "案件番号を入力してください"}), 400

    # case_number: "2025-47348" → case_id: "2025-47348"
    case_id = case_number
    case_dir = get_case_dir(case_id)
    if case_dir.exists():
        return jsonify({"error": f"案件 '{case_id}' は既に存在します", "case_id": case_id}), 409

    # 公開番号 → Google Patents用ID: "2025-47348" → "JP2025047348A"
    m = _re.match(r'(\d{4})-(\d+)', case_number)
    if m:
        year, serial = m.group(1), m.group(2)
        jp_id = f"JP{year}{serial.zfill(6)}A"
    else:
        # ハイフンなしや他のフォーマットの場合はそのまま試行
        jp_id = case_number

    # ディレクトリ作成
    for sub in ["input", "citations", "prompts", "responses", "output"]:
        (case_dir / sub).mkdir(parents=True, exist_ok=True)

    # 初期メタ情報を保存（DL失敗時にも案件は残る）
    meta = {
        "case_id": case_id,
        "jp_id": jp_id,
        "title": "",
        "field": field,
        "year": year,
        "month": month,
        "citations": [],
    }
    save_case_meta(case_id, meta)

    # PDF自動ダウンロード
    dl_result = download_patent_pdf(jp_id, case_dir / "input", timeout=60)
    if not dl_result["success"]:
        return jsonify({
            "success": True,
            "case_id": case_id,
            "pdf_downloaded": False,
            "error": dl_result.get("error", "PDFダウンロード失敗"),
            "google_patents_url": dl_result.get("google_patents_url", ""),
        })

    # PDF抽出
    try:
        result = extract_patent_pdf(dl_result["path"], "hongan")
        with open(case_dir / "hongan.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        # メタ情報更新
        patent_number = result.get("patent_number", "")
        patent_title = result.get("patent_title", "")
        meta["patent_number"] = patent_number
        meta["patent_title"] = patent_title
        meta["title"] = patent_title
        save_case_meta(case_id, meta)

        # 自動分節
        num_segments = 0
        if result.get("claims"):
            segs = segment_claims(result["claims"])
            with open(case_dir / "segments.json", "w", encoding="utf-8") as f:
                json.dump(segs, f, ensure_ascii=False, indent=2)
            num_segments = sum(len(c.get("segments", [])) for c in segs)

        return jsonify({
            "success": True,
            "case_id": case_id,
            "pdf_downloaded": True,
            "patent_number": patent_number,
            "patent_title": patent_title,
            "num_claims": len(result.get("claims", [])),
            "num_paragraphs": len(result.get("paragraphs", [])),
            "num_segments": num_segments,
        })

    except Exception as e:
        return jsonify({
            "success": True,
            "case_id": case_id,
            "pdf_downloaded": True,
            "error": f"PDF抽出エラー: {str(e)}",
        })


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

    # keyword_dictionary.json があれば優先的に使用
    kw_dict = _load_search_data(case_dir, "keyword_dictionary.json")
    if kw_dict:
        from modules.search_prompt_generator import convert_keyword_dict_to_groups
        result = convert_keyword_dict_to_groups(kw_dict, segs)
    else:
        result = _suggest(hongan, segs, field)

    with open(case_dir / "keywords.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return jsonify(result)


@app.route("/case/<case_id>/keywords", methods=["GET"])
def get_keywords(case_id):
    """キーワードデータを取得"""
    case_dir = get_case_dir(case_id)
    kw_path = case_dir / "keywords.json"
    if not kw_path.exists():
        return jsonify({"error": "キーワードデータがありません"}), 404
    with open(kw_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return jsonify(data)


@app.route("/case/<case_id>/keywords/add", methods=["POST"])
def add_keyword(case_id):
    """キーワードをグループに追加"""
    case_dir = get_case_dir(case_id)
    kw_path = case_dir / "keywords.json"
    if not kw_path.exists():
        return jsonify({"error": "キーワードデータがありません"}), 404

    data = request.get_json() or {}
    group_id = data.get("group_id")
    term = (data.get("term") or "").strip()
    if not term:
        return jsonify({"error": "キーワードを入力してください"}), 400

    with open(kw_path, "r", encoding="utf-8") as f:
        groups = json.load(f)

    for group in groups:
        if group["group_id"] == group_id:
            group["keywords"].append({
                "term": term,
                "source": "手動",
                "type": "手動追加",
            })
            break
    else:
        return jsonify({"error": f"グループ{group_id}が見つかりません"}), 404

    with open(kw_path, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)

    return jsonify({"success": True})


@app.route("/case/<case_id>/keywords/delete", methods=["POST"])
def delete_keyword(case_id):
    """キーワードをグループから削除"""
    case_dir = get_case_dir(case_id)
    kw_path = case_dir / "keywords.json"
    if not kw_path.exists():
        return jsonify({"error": "キーワードデータがありません"}), 404

    data = request.get_json() or {}
    group_id = data.get("group_id")
    term = data.get("term", "")

    with open(kw_path, "r", encoding="utf-8") as f:
        groups = json.load(f)

    for group in groups:
        if group["group_id"] == group_id:
            group["keywords"] = [
                kw for kw in group["keywords"] if kw["term"] != term
            ]
            break
    else:
        return jsonify({"error": f"グループ{group_id}が見つかりません"}), 404

    with open(kw_path, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)

    return jsonify({"success": True})


@app.route("/case/<case_id>/keywords/group/add", methods=["POST"])
def add_keyword_group(case_id):
    """キーワードグループを新規追加"""
    case_dir = get_case_dir(case_id)
    kw_path = case_dir / "keywords.json"

    groups = []
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            groups = json.load(f)

    data = request.get_json() or {}
    label = (data.get("label") or "").strip() or "新規グループ"
    segment_ids = data.get("segment_ids", [])

    new_id = max((g["group_id"] for g in groups), default=0) + 1
    groups.append({
        "group_id": new_id,
        "label": label,
        "segment_ids": segment_ids,
        "keywords": [],
        "search_codes": {},
    })

    with open(kw_path, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)

    return jsonify({"success": True, "group_id": new_id})


@app.route("/case/<case_id>/keywords/group/delete", methods=["POST"])
def delete_keyword_group(case_id):
    """キーワードグループを削除"""
    case_dir = get_case_dir(case_id)
    kw_path = case_dir / "keywords.json"
    if not kw_path.exists():
        return jsonify({"error": "キーワードデータがありません"}), 404

    data = request.get_json() or {}
    group_id = data.get("group_id")

    with open(kw_path, "r", encoding="utf-8") as f:
        groups = json.load(f)

    groups = [g for g in groups if g["group_id"] != group_id]

    with open(kw_path, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)

    return jsonify({"success": True})


@app.route("/case/<case_id>/keywords/group/update", methods=["POST"])
def update_keyword_group(case_id):
    """キーワードグループのラベル・関連分節を更新"""
    case_dir = get_case_dir(case_id)
    kw_path = case_dir / "keywords.json"
    if not kw_path.exists():
        return jsonify({"error": "キーワードデータがありません"}), 404

    data = request.get_json() or {}
    group_id = data.get("group_id")

    with open(kw_path, "r", encoding="utf-8") as f:
        groups = json.load(f)

    for group in groups:
        if group["group_id"] == group_id:
            if "label" in data:
                group["label"] = data["label"]
            if "segment_ids" in data:
                group["segment_ids"] = data["segment_ids"]
            break
    else:
        return jsonify({"error": f"グループ{group_id}が見つかりません"}), 404

    with open(kw_path, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)

    return jsonify({"success": True})


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


@app.route("/case/<case_id>/response/<citation_id>", methods=["GET"])
def get_response(case_id, citation_id):
    """引用文献の対比結果JSONを返す"""
    case_dir = get_case_dir(case_id)
    resp_path = case_dir / "responses" / f"{citation_id}.json"
    if not resp_path.exists():
        return jsonify({"error": "回答データがありません"}), 404
    with open(resp_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return jsonify(data)


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


@app.route("/case/<case_id>/annotate/<citation_id>", methods=["POST"])
def annotate_citation(case_id, citation_id):
    """引用文献PDFに対比結果の注釈を追加"""
    from modules.pdf_annotator import annotate_citation_pdf

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return jsonify({"error": "案件が見つかりません"}), 404

    # 対比結果
    resp_path = case_dir / "responses" / f"{citation_id}.json"
    if not resp_path.exists():
        return jsonify({"error": f"対比結果がありません: {citation_id}"}), 404

    with open(resp_path, "r", encoding="utf-8") as f:
        response_data = json.load(f)

    # 引用文献構造化データ
    cit_path = case_dir / "citations" / f"{citation_id}.json"
    if not cit_path.exists():
        return jsonify({"error": f"引用文献データがありません: {citation_id}"}), 404

    with open(cit_path, "r", encoding="utf-8") as f:
        citation_data = json.load(f)

    # 元PDF検索: input/ 内から該当ファイルを探す
    pdf_path = _find_citation_pdf(case_dir / "input", citation_id)
    if not pdf_path:
        return jsonify({"error": f"引用文献PDFが見つかりません: {citation_id}"}), 404

    # キーワード
    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    # 注釈PDF生成
    import re
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', citation_id)
    output_path = case_dir / "output" / f"{safe_name}_annotated.pdf"
    (case_dir / "output").mkdir(parents=True, exist_ok=True)

    try:
        result = annotate_citation_pdf(
            pdf_path, output_path, response_data, citation_data, keywords)
        return jsonify({
            "success": True,
            "filename": output_path.name,
            "labels": result["labels"],
            "highlights": result["highlights"],
            "bookmarks": result["bookmarks"],
        })
    except Exception as e:
        return jsonify({"error": f"注釈生成エラー: {str(e)}"}), 500


@app.route("/case/<case_id>/annotate/all", methods=["POST"])
def annotate_all_citations(case_id):
    """全引用文献の注釈PDFを一括生成"""
    from modules.pdf_annotator import annotate_citation_pdf

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return jsonify({"error": "案件が見つかりません"}), 404

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    import re
    results = []
    for cit in meta.get("citations", []):
        cit_id = cit["id"]
        resp_path = case_dir / "responses" / f"{cit_id}.json"
        cit_path = case_dir / "citations" / f"{cit_id}.json"
        pdf_path = _find_citation_pdf(case_dir / "input", cit_id)

        if not resp_path.exists() or not cit_path.exists() or not pdf_path:
            results.append({"citation_id": cit_id, "success": False,
                            "error": "必要なファイルがありません"})
            continue

        with open(resp_path, "r", encoding="utf-8") as f:
            response_data = json.load(f)
        with open(cit_path, "r", encoding="utf-8") as f:
            citation_data = json.load(f)

        safe_name = re.sub(r'[<>:"/\\|?*]', '_', cit_id)
        output_path = case_dir / "output" / f"{safe_name}_annotated.pdf"

        try:
            result = annotate_citation_pdf(
                pdf_path, output_path, response_data, citation_data, keywords)
            results.append({
                "citation_id": cit_id, "success": True,
                "filename": output_path.name, **result,
            })
        except Exception as e:
            results.append({"citation_id": cit_id, "success": False,
                            "error": str(e)})

    success_count = sum(1 for r in results if r["success"])
    return jsonify({"results": results, "success_count": success_count})


def _find_citation_pdf(input_dir, citation_id):
    """input/ディレクトリから引用文献IDに対応するPDFを探す"""
    import re
    if not input_dir.exists():
        return None

    # 全角数字→半角変換
    fw2hw = str.maketrans("０１２３４５６７８９", "0123456789")

    # citation_idから数字部分を抽出（全角も対応）
    cid_hw = citation_id.translate(fw2hw)
    normalized = re.sub(r'[\s\-/]', '', cid_hw)
    digits = re.sub(r'[^\d]', '', cid_hw)

    for pdf_file in input_dir.glob("*.pdf"):
        stem = pdf_file.stem
        stem_hw = stem.translate(fw2hw)
        stem_normalized = re.sub(r'[\s\-/]', '', stem_hw)
        # 完全一致
        if stem_normalized == normalized:
            return pdf_file
        # 番号部分が含まれるか
        if len(digits) >= 6 and digits in stem_normalized:
            return pdf_file

    return None


@app.route("/case/<case_id>/search/prompt", methods=["POST"])
def search_prompt(case_id):
    """先行技術検索プロンプトを生成"""
    from modules.search_prompt_generator import generate_search_prompt
    from modules.search_injector import inject_search_results

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return jsonify({"error": "案件が見つかりません"}), 404

    segments_path = case_dir / "segments.json"
    if not segments_path.exists():
        return jsonify({"error": "分節データがありません。Step 2を完了してください。"}), 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    field = meta.get("field", "cosmetics")
    prompt_text = generate_search_prompt(segs, keywords, field, case_meta=meta)

    # SerpAPIで事前検索し、結果をプロンプトに注入
    prompt_text = inject_search_results(prompt_text, segs, keywords, field)

    # ファイルに保存
    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = prompts_dir / "search_prompt.txt"
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt_text)

    return jsonify({
        "prompt": prompt_text,
        "char_count": len(prompt_text),
    })


@app.route("/case/<case_id>/search/response", methods=["POST"])
def search_response(case_id):
    """先行技術検索の回答をパース"""
    from modules.search_prompt_generator import parse_search_response

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return jsonify({"error": "案件が見つかりません"}), 404

    raw_text = request.get_json().get("text", "")
    if not raw_text.strip():
        return jsonify({"error": "テキストが空です"}), 400

    candidates, errors = parse_search_response(raw_text)

    if candidates:
        # search_candidates.json に保存
        from datetime import datetime
        save_data = {
            "generated_at": datetime.now().isoformat(),
            "candidates": candidates,
        }
        with open(case_dir / "search_candidates.json", "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)

    return jsonify({
        "success": candidates is not None,
        "candidates": candidates or [],
        "errors": errors,
    })


@app.route("/case/<case_id>/search/download", methods=["POST"])
def search_download(case_id):
    """候補文献のPDFをダウンロードして引用文献に登録"""
    from modules.patent_downloader import download_patent_pdf
    from modules.pdf_extractor import extract_patent_pdf

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return jsonify({"error": "案件が見つかりません"}), 404

    data = request.get_json() or {}
    patent_ids = data.get("patent_ids", [])
    if not patent_ids and data.get("patent_id"):
        patent_ids = [data["patent_id"]]

    if not patent_ids:
        return jsonify({"error": "patent_id を指定してください"}), 400

    results = []
    for patent_id in patent_ids:
        role = data.get("role", "主引例")

        # PDFダウンロード試行
        dl_result = download_patent_pdf(patent_id, case_dir / "input")

        if dl_result["success"]:
            # PDF抽出
            try:
                extracted = extract_patent_pdf(dl_result["path"], "citation")
                doc_id = extracted.get("patent_number", patent_id)
                extracted["role"] = role
                extracted["label"] = doc_id

                # citations/ に保存
                (case_dir / "citations").mkdir(parents=True, exist_ok=True)
                with open(case_dir / "citations" / f"{doc_id}.json", "w", encoding="utf-8") as f:
                    json.dump(extracted, f, ensure_ascii=False, indent=2)

                # case.yaml 更新
                citations = meta.get("citations", [])
                if not any(c["id"] == doc_id for c in citations):
                    citations.append({"id": doc_id, "role": role, "label": doc_id})
                    meta["citations"] = citations
                    save_case_meta(case_id, meta)

                results.append({
                    "patent_id": patent_id,
                    "doc_id": doc_id,
                    "success": True,
                    "num_claims": len(extracted.get("claims", [])),
                    "num_paragraphs": len(extracted.get("paragraphs", [])),
                })
            except Exception as e:
                results.append({
                    "patent_id": patent_id,
                    "success": False,
                    "error": f"PDF抽出エラー: {str(e)}",
                    "google_patents_url": dl_result.get("google_patents_url", ""),
                })
        else:
            results.append({
                "patent_id": patent_id,
                "success": False,
                "error": dl_result.get("error", "ダウンロード失敗"),
                "google_patents_url": dl_result.get("google_patents_url", ""),
            })

    # search_candidates.json のステータス更新
    candidates_path = case_dir / "search_candidates.json"
    if candidates_path.exists():
        with open(candidates_path, "r", encoding="utf-8") as f:
            cand_data = json.load(f)
        for r in results:
            for c in cand_data.get("candidates", []):
                if c["patent_id"] == r["patent_id"]:
                    c["status"] = "downloaded" if r["success"] else "failed"
        with open(candidates_path, "w", encoding="utf-8") as f:
            json.dump(cand_data, f, ensure_ascii=False, indent=2)

    return jsonify({"results": results})


@app.route("/case/<case_id>/inventive-step/prompt", methods=["POST"])
def inventive_step_prompt(case_id):
    """進歩性判断プロンプトを生成"""
    from modules.inventive_step_analyzer import generate_inventive_step_prompt

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return jsonify({"error": "案件が見つかりません"}), 404

    segments_path = case_dir / "segments.json"
    if not segments_path.exists():
        return jsonify({"error": "分節データがありません"}), 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    # 全回答データ読み込み
    responses = {}
    responses_dir = case_dir / "responses"
    if responses_dir.exists():
        for rfile in responses_dir.glob("*.json"):
            with open(rfile, "r", encoding="utf-8") as f:
                responses[rfile.stem] = json.load(f)

    if not responses:
        return jsonify({"error": "対比結果がありません。Step 5を完了してください。"}), 400

    # 引用文献メタ情報
    citations_meta = {}
    for cit in meta.get("citations", []):
        cit_path = case_dir / "citations" / f"{cit['id']}.json"
        if cit_path.exists():
            with open(cit_path, "r", encoding="utf-8") as f:
                citations_meta[cit["id"]] = json.load(f)

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    field = meta.get("field", "cosmetics")
    prompt_text = generate_inventive_step_prompt(segs, responses, citations_meta, keywords, field)

    # ファイルに保存
    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    with open(prompts_dir / "inventive_step_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt_text)

    return jsonify({
        "prompt": prompt_text,
        "char_count": len(prompt_text),
    })


@app.route("/case/<case_id>/inventive-step/response", methods=["POST"])
def inventive_step_response(case_id):
    """進歩性判断の回答をパース"""
    from modules.inventive_step_analyzer import parse_inventive_step_response

    case_dir = get_case_dir(case_id)
    if not load_case_meta(case_id):
        return jsonify({"error": "案件が見つかりません"}), 404

    raw_text = request.get_json().get("text", "")
    if not raw_text.strip():
        return jsonify({"error": "テキストが空です"}), 400

    data, errors = parse_inventive_step_response(raw_text)

    if data:
        with open(case_dir / "inventive_step.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    return jsonify({
        "success": data is not None,
        "data": data,
        "errors": errors,
    })


@app.route("/case/<case_id>/meta", methods=["POST"])
def update_case_meta(case_id):
    """案件メタ情報（特許番号・題名等）を更新"""
    meta = load_case_meta(case_id)
    if not meta:
        return jsonify({"error": "案件が見つかりません"}), 404

    data = request.get_json() or {}
    for key in ["patent_number", "patent_title", "title", "field", "year", "month"]:
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


# ===== Claude CLI 直接実行 =====

@app.route("/api/claude-status")
def claude_status():
    """Claude CLI の利用可能状態を返す"""
    from modules.claude_client import is_claude_available, _load_serpapi_key
    return jsonify({
        "available": is_claude_available(),
        "search_available": bool(_load_serpapi_key()),
    })


@app.route("/api/serpapi-key", methods=["POST"])
def set_serpapi_key():
    """SerpAPIキーを config.yaml に保存"""
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


@app.route("/case/<case_id>/search/execute", methods=["POST"])
def search_execute(case_id):
    """直接実行: 先行技術検索プロンプト → Claude CLI → パース"""
    from modules.search_prompt_generator import generate_search_prompt, parse_search_response
    from modules.claude_client import call_claude, ClaudeClientError
    from modules.search_injector import inject_search_results

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return jsonify({"error": "案件が見つかりません"}), 404

    segments_path = case_dir / "segments.json"
    if not segments_path.exists():
        return jsonify({"error": "分節データがありません。Step 2を完了してください。"}), 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    field = meta.get("field", "cosmetics")
    prompt_text = generate_search_prompt(segs, keywords, field, case_meta=meta)

    # SerpAPIで事前検索し、結果をプロンプトに注入
    prompt_text = inject_search_results(prompt_text, segs, keywords, field)

    # プロンプト保存
    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    with open(prompts_dir / "search_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt_text)

    # Claude CLI 呼び出し（検索ツール付き）
    try:
        raw_response = call_claude(prompt_text, timeout=600, use_search=True)
    except ClaudeClientError as e:
        return jsonify({"error": str(e), "phase": "claude_call"}), 502

    # パース
    candidates, errors = parse_search_response(raw_response)

    if candidates:
        from datetime import datetime
        save_data = {
            "generated_at": datetime.now().isoformat(),
            "candidates": candidates,
        }
        with open(case_dir / "search_candidates.json", "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)

    return jsonify({
        "success": candidates is not None,
        "candidates": candidates or [],
        "errors": errors,
        "char_count": len(prompt_text),
        "response_length": len(raw_response),
    })


@app.route("/case/<case_id>/execute", methods=["POST"])
def compare_execute(case_id):
    """直接実行: 対比プロンプト → Claude CLI → パース"""
    from modules.prompt_generator import generate_prompt as _gen
    from modules.response_parser import parse_response, split_multi_response
    from modules.claude_client import call_claude, ClaudeClientError

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    segments_path = case_dir / "segments.json"

    if not segments_path.exists():
        return jsonify({"error": "分節データがありません"}), 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    data = request.get_json() or {}
    citation_ids = data.get("citation_ids", [])
    if not citation_ids:
        return jsonify({"error": "対象文献を選択してください"}), 400

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

    # プロンプト保存
    ids_label = "_".join(citation_ids)
    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    with open(prompts_dir / f"{ids_label}_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt_text)

    # Claude CLI 呼び出し（文献数に応じてタイムアウト調整）
    timeout = 600 if len(citations) <= 2 else 900
    try:
        raw_response = call_claude(prompt_text, timeout=timeout)
    except ClaudeClientError as e:
        return jsonify({"error": str(e), "phase": "claude_call"}), 502

    # パース
    all_segment_ids = []
    for claim in segs:
        for seg in claim["segments"]:
            all_segment_ids.append(seg["id"])

    result, errors = parse_response(raw_response, all_segment_ids)

    saved_docs = []
    if result:
        per_doc = split_multi_response(result)
        responses_dir = case_dir / "responses"
        responses_dir.mkdir(parents=True, exist_ok=True)
        for doc_id, doc_result in per_doc.items():
            resp_path = responses_dir / f"{doc_id}.json"
            with open(resp_path, "w", encoding="utf-8") as f:
                json.dump(doc_result, f, ensure_ascii=False, indent=2)
            saved_docs.append(doc_id)

    return jsonify({
        "success": result is not None,
        "errors": errors,
        "saved_docs": saved_docs,
        "num_docs": len(saved_docs),
        "char_count": len(prompt_text),
        "response_length": len(raw_response),
    })


@app.route("/case/<case_id>/inventive-step/execute", methods=["POST"])
def inventive_step_execute(case_id):
    """直接実行: 進歩性判断プロンプト → Claude CLI → パース"""
    from modules.inventive_step_analyzer import (
        generate_inventive_step_prompt, parse_inventive_step_response
    )
    from modules.claude_client import call_claude, ClaudeClientError

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return jsonify({"error": "案件が見つかりません"}), 404

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
        return jsonify({"error": "対比結果がありません。Step 5を完了してください。"}), 400

    citations_meta = {}
    for cit in meta.get("citations", []):
        cit_path = case_dir / "citations" / f"{cit['id']}.json"
        if cit_path.exists():
            with open(cit_path, "r", encoding="utf-8") as f:
                citations_meta[cit["id"]] = json.load(f)

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    field = meta.get("field", "cosmetics")
    prompt_text = generate_inventive_step_prompt(
        segs, responses, citations_meta, keywords, field
    )

    # プロンプト保存
    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    with open(prompts_dir / "inventive_step_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt_text)

    # Claude CLI 呼び出し
    try:
        raw_response = call_claude(prompt_text, timeout=600)
    except ClaudeClientError as e:
        return jsonify({"error": str(e), "phase": "claude_call"}), 502

    # パース
    data, errors = parse_inventive_step_response(raw_response)

    if data:
        with open(case_dir / "inventive_step.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    return jsonify({
        "success": data is not None,
        "data": data,
        "errors": errors,
        "char_count": len(prompt_text),
        "response_length": len(raw_response),
    })


# ===== 分節別キーワード提案 =====

def _sync_to_keyword_groups(case_dir, seg_keywords, field):
    """segment_keywords.json → keywords.json への同期変換"""
    COLOR_NAMES = {
        1: "赤", 2: "紫", 3: "マゼンタ", 4: "青",
        5: "緑", 6: "オレンジ", 7: "ティール",
    }

    groups = []
    for i, item in enumerate(seg_keywords):
        if not item.get("keywords"):
            continue
        group_id = i + 1
        groups.append({
            "group_id": group_id,
            "label": item["segment_text"][:20] if item.get("segment_text") else item["segment_id"],
            "color": COLOR_NAMES.get(group_id, "黒"),
            "segment_ids": [item["segment_id"]],
            "keywords": [
                {"term": kw["term"], "source": kw.get("source", ""), "type": kw.get("type", "")}
                for kw in item["keywords"]
            ],
            "search_codes": {},
        })

    kw_path = case_dir / "keywords.json"
    with open(kw_path, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)


@app.route("/case/<case_id>/keywords/suggest-by-segment", methods=["POST"])
def suggest_keywords_by_segment(case_id):
    """分節別キーワード提案（パイプライン版: Step 1→2→3）

    Returns:
        segment_keywords.json 形式のリスト
    """
    from modules.keyword_recommender import recommend_regex

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return jsonify({"error": "案件が見つかりません"}), 404

    hongan_path = case_dir / "hongan.json"
    segments_path = case_dir / "segments.json"
    if not hongan_path.exists() or not segments_path.exists():
        return jsonify({"error": "本願テキストまたは分節データがありません"}), 400

    with open(hongan_path, "r", encoding="utf-8") as f:
        hongan = json.load(f)
    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    field = meta.get("field", "cosmetics")
    result = recommend_regex(segs, hongan, field)

    # segment_keywords.json に保存
    with open(case_dir / "segment_keywords.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return jsonify(result)


@app.route("/case/<case_id>/keywords/segments", methods=["GET"])
def get_segment_keywords(case_id):
    """分節別キーワードを取得"""
    case_dir = get_case_dir(case_id)
    sk_path = case_dir / "segment_keywords.json"
    if not sk_path.exists():
        return jsonify({"error": "分節別キーワードがありません。先に提案を実行してください。"}), 404
    with open(sk_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return jsonify(data)


@app.route("/case/<case_id>/keywords/add-to-segment", methods=["POST"])
def add_keyword_to_segment(case_id):
    """テキスト選択からキーワードを分節に追加"""
    case_dir = get_case_dir(case_id)
    sk_path = case_dir / "segment_keywords.json"

    data = request.get_json() or {}
    term = (data.get("term") or "").strip()
    segment_id = (data.get("segment_id") or "").strip()

    if not term or not segment_id:
        return jsonify({"error": "term と segment_id は必須です"}), 400

    # segment_keywords.json を読み込み（なければ空リストから開始）
    seg_keywords = []
    if sk_path.exists():
        with open(sk_path, "r", encoding="utf-8") as f:
            seg_keywords = json.load(f)

    # 該当分節を探す
    found = False
    for item in seg_keywords:
        if item["segment_id"] == segment_id:
            if not any(kw["term"] == term for kw in item["keywords"]):
                item["keywords"].append({
                    "term": term,
                    "source": "manual",
                    "type": "手動追加",
                })
            found = True
            break

    if not found:
        seg_keywords.append({
            "segment_id": segment_id,
            "segment_text": "",
            "keywords": [{"term": term, "source": "manual", "type": "手動追加"}],
        })

    with open(sk_path, "w", encoding="utf-8") as f:
        json.dump(seg_keywords, f, ensure_ascii=False, indent=2)

    meta = load_case_meta(case_id)
    field = meta.get("field", "cosmetics") if meta else "cosmetics"
    _sync_to_keyword_groups(case_dir, seg_keywords, field)

    return jsonify({"success": True})


@app.route("/case/<case_id>/keywords/remove-from-segment", methods=["POST"])
def remove_keyword_from_segment(case_id):
    """キーワードを分節から削除"""
    case_dir = get_case_dir(case_id)
    sk_path = case_dir / "segment_keywords.json"

    if not sk_path.exists():
        return jsonify({"error": "分節別キーワードがありません"}), 404

    data = request.get_json() or {}
    term = (data.get("term") or "").strip()
    segment_id = (data.get("segment_id") or "").strip()

    if not term or not segment_id:
        return jsonify({"error": "term と segment_id は必須です"}), 400

    with open(sk_path, "r", encoding="utf-8") as f:
        seg_keywords = json.load(f)

    for item in seg_keywords:
        if item["segment_id"] == segment_id:
            item["keywords"] = [kw for kw in item["keywords"] if kw["term"] != term]
            break

    with open(sk_path, "w", encoding="utf-8") as f:
        json.dump(seg_keywords, f, ensure_ascii=False, indent=2)

    meta = load_case_meta(case_id)
    field = meta.get("field", "cosmetics") if meta else "cosmetics"
    _sync_to_keyword_groups(case_dir, seg_keywords, field)

    return jsonify({"success": True})


# ===== 3段階検索ワークフロー =====

@app.route("/case/<case_id>/search/presearch/prompt", methods=["POST"])
def presearch_prompt(case_id):
    """Stage 1: 予備検索プロンプトを生成"""
    from modules.search_prompt_generator import generate_presearch_prompt

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return jsonify({"error": "案件が見つかりません"}), 404

    segments_path = case_dir / "segments.json"
    hongan_path = case_dir / "hongan.json"
    if not segments_path.exists():
        return jsonify({"error": "分節データがありません。Step 2を完了してください。"}), 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    hongan = None
    if hongan_path.exists():
        with open(hongan_path, "r", encoding="utf-8") as f:
            hongan = json.load(f)

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    field = meta.get("field", "cosmetics")
    prompt_text = generate_presearch_prompt(segs, hongan, keywords, field, case_meta=meta)

    # 保存
    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    with open(prompts_dir / "presearch_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt_text)

    return jsonify({"prompt": prompt_text, "char_count": len(prompt_text)})


@app.route("/case/<case_id>/search/presearch/parse", methods=["POST"])
def presearch_parse(case_id):
    """Stage 1: 予備検索の回答をパースして保存"""
    from modules.search_prompt_generator import parse_presearch_response

    case_dir = get_case_dir(case_id)
    if not load_case_meta(case_id):
        return jsonify({"error": "案件が見つかりません"}), 404

    raw_text = (request.get_json() or {}).get("text", "")
    if not raw_text.strip():
        return jsonify({"error": "テキストが空です"}), 400

    tech_analysis, candidates, search_formulas, errors = parse_presearch_response(raw_text)

    saved = []
    if tech_analysis:
        _save_search_data(case_dir, "tech_analysis.json", tech_analysis)
        saved.append("tech_analysis")
    if candidates:
        _save_search_data(case_dir, "presearch_candidates.json", candidates)
        saved.append("presearch_candidates")
    if search_formulas:
        _save_search_data(case_dir, "presearch_formulas.json", search_formulas)
        saved.append("presearch_formulas")

    return jsonify({
        "success": tech_analysis is not None,
        "tech_analysis": tech_analysis,
        "candidates": candidates or [],
        "search_formulas": search_formulas or [],
        "errors": errors,
        "saved": saved,
    })


@app.route("/case/<case_id>/search/classify/prompt", methods=["POST"])
def classify_prompt(case_id):
    """Stage 2: 分類特定プロンプトを生成"""
    from modules.search_prompt_generator import generate_classification_prompt

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return jsonify({"error": "案件が見つかりません"}), 404

    segments_path = case_dir / "segments.json"
    hongan_path = case_dir / "hongan.json"
    if not segments_path.exists():
        return jsonify({"error": "分節データがありません"}), 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    hongan = None
    if hongan_path.exists():
        with open(hongan_path, "r", encoding="utf-8") as f:
            hongan = json.load(f)

    # Stage 1の結果を読み込み
    tech_analysis = _load_search_data(case_dir, "tech_analysis.json")
    presearch_candidates = _load_search_data(case_dir, "presearch_candidates.json")

    if not tech_analysis:
        return jsonify({"error": "技術構造化データがありません。Stage 1を先に完了してください。"}), 400

    field = meta.get("field", "cosmetics")
    prompt_text = generate_classification_prompt(segs, hongan, field, tech_analysis, presearch_candidates)

    # 保存
    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    with open(prompts_dir / "classification_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt_text)

    return jsonify({"prompt": prompt_text, "char_count": len(prompt_text)})


@app.route("/case/<case_id>/search/classify/parse", methods=["POST"])
def classify_parse(case_id):
    """Stage 2: 分類特定の回答をパースして保存"""
    from modules.search_prompt_generator import parse_classification_response

    case_dir = get_case_dir(case_id)
    if not load_case_meta(case_id):
        return jsonify({"error": "案件が見つかりません"}), 404

    raw_text = (request.get_json() or {}).get("text", "")
    if not raw_text.strip():
        return jsonify({"error": "テキストが空です"}), 400

    classification, errors = parse_classification_response(raw_text)

    if classification:
        _save_search_data(case_dir, "classification.json", classification)

    return jsonify({
        "success": classification is not None,
        "classification": classification,
        "errors": errors,
    })


@app.route("/case/<case_id>/search/keywords/prompt", methods=["POST"])
def keyword_dict_prompt(case_id):
    """Stage 3: キーワード辞書プロンプトを生成"""
    from modules.search_prompt_generator import generate_keyword_prompt

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return jsonify({"error": "案件が見つかりません"}), 404

    segments_path = case_dir / "segments.json"
    hongan_path = case_dir / "hongan.json"
    if not segments_path.exists():
        return jsonify({"error": "分節データがありません"}), 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    hongan = None
    if hongan_path.exists():
        with open(hongan_path, "r", encoding="utf-8") as f:
            hongan = json.load(f)

    # Stage 1-2の結果を読み込み
    tech_analysis = _load_search_data(case_dir, "tech_analysis.json")
    classification = _load_search_data(case_dir, "classification.json")
    presearch_candidates = _load_search_data(case_dir, "presearch_candidates.json")

    if not tech_analysis:
        return jsonify({"error": "技術構造化データがありません。Stage 1を先に完了してください。"}), 400

    field = meta.get("field", "cosmetics")
    prompt_text = generate_keyword_prompt(
        segs, hongan, field, tech_analysis, classification, presearch_candidates
    )

    # 保存
    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    with open(prompts_dir / "keyword_dict_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt_text)

    return jsonify({"prompt": prompt_text, "char_count": len(prompt_text)})


@app.route("/case/<case_id>/search/keywords/parse", methods=["POST"])
def keyword_dict_parse(case_id):
    """Stage 3: キーワード辞書の回答をパースして保存"""
    from modules.search_prompt_generator import (
        parse_keyword_response, convert_keyword_dict_to_groups,
    )

    case_dir = get_case_dir(case_id)
    if not load_case_meta(case_id):
        return jsonify({"error": "案件が見つかりません"}), 404

    raw_text = (request.get_json() or {}).get("text", "")
    if not raw_text.strip():
        return jsonify({"error": "テキストが空です"}), 400

    keyword_dictionary, errors = parse_keyword_response(raw_text)

    if keyword_dictionary:
        _save_search_data(case_dir, "keyword_dictionary.json", keyword_dictionary)

        # keywords.json にも変換して保存
        segments_path = Path(case_dir) / "segments.json"
        if segments_path.exists():
            with open(segments_path, "r", encoding="utf-8") as f:
                segs = json.load(f)
            groups = convert_keyword_dict_to_groups(keyword_dictionary, segs)
            kw_path = Path(case_dir) / "keywords.json"
            with open(kw_path, "w", encoding="utf-8") as f:
                json.dump(groups, f, ensure_ascii=False, indent=2)

    return jsonify({
        "success": keyword_dictionary is not None,
        "keyword_dictionary": keyword_dictionary,
        "errors": errors,
    })


@app.route("/case/<case_id>/search/status", methods=["GET"])
def search_status(case_id):
    """3段階検索の進捗状況を返す"""
    case_dir = get_case_dir(case_id)
    if not load_case_meta(case_id):
        return jsonify({"error": "案件が見つかりません"}), 404

    status = {
        "stage1": {
            "tech_analysis": _load_search_data(case_dir, "tech_analysis.json") is not None,
            "presearch_candidates": _load_search_data(case_dir, "presearch_candidates.json") is not None,
            "presearch_formulas": _load_search_data(case_dir, "presearch_formulas.json") is not None,
        },
        "stage2": {
            "classification": _load_search_data(case_dir, "classification.json") is not None,
        },
        "stage3": {
            "keyword_dictionary": _load_search_data(case_dir, "keyword_dictionary.json") is not None,
        },
    }

    # 完了ステージ数を計算
    completed = 0
    if status["stage1"]["tech_analysis"]:
        completed = 1
    if status["stage2"]["classification"]:
        completed = 2
    if status["stage3"]["keyword_dictionary"]:
        completed = 3

    status["completed_stages"] = completed
    return jsonify(status)


@app.route("/case/<case_id>/search/data/<filename>", methods=["GET"])
def get_search_data(case_id, filename):
    """search/ 配下のデータを取得"""
    case_dir = get_case_dir(case_id)
    if not load_case_meta(case_id):
        return jsonify({"error": "案件が見つかりません"}), 404

    # 許可するファイル名のみ
    allowed = {
        "tech_analysis.json", "presearch_candidates.json",
        "presearch_formulas.json", "classification.json",
        "keyword_dictionary.json",
    }
    if filename not in allowed:
        return jsonify({"error": "不正なファイル名です"}), 400

    data = _load_search_data(case_dir, filename)
    if data is None:
        return jsonify({"error": f"{filename} がありません"}), 404

    return jsonify(data)


@app.route("/case/<case_id>/search/stage-execute", methods=["POST"])
def stage_execute(case_id):
    """3段階検索の直接実行: prompt生成 → Claude CLI → parse"""
    from modules.search_prompt_generator import (
        generate_presearch_prompt, parse_presearch_response,
        generate_classification_prompt, parse_classification_response,
        generate_keyword_prompt, parse_keyword_response,
        convert_keyword_dict_to_groups,
    )
    from modules.claude_client import call_claude, ClaudeClientError

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return jsonify({"error": "案件が見つかりません"}), 404

    data = request.get_json() or {}
    stage = data.get("stage")
    if stage not in (1, 2, 3):
        return jsonify({"error": "stage は 1, 2, 3 のいずれかを指定してください"}), 400

    segments_path = Path(case_dir) / "segments.json"
    hongan_path = Path(case_dir) / "hongan.json"
    if not segments_path.exists():
        return jsonify({"error": "分節データがありません"}), 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)
    hongan = None
    if hongan_path.exists():
        with open(hongan_path, "r", encoding="utf-8") as f:
            hongan = json.load(f)

    keywords = None
    kw_path = Path(case_dir) / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    field = meta.get("field", "cosmetics")

    # Stage別のプロンプト生成
    if stage == 1:
        prompt_text = generate_presearch_prompt(segs, hongan, keywords, field, case_meta=meta)
    elif stage == 2:
        tech_analysis = _load_search_data(case_dir, "tech_analysis.json")
        presearch_candidates = _load_search_data(case_dir, "presearch_candidates.json")
        if not tech_analysis:
            return jsonify({"error": "Stage 1を先に完了してください"}), 400
        prompt_text = generate_classification_prompt(segs, hongan, field, tech_analysis, presearch_candidates)
    else:  # stage == 3
        tech_analysis = _load_search_data(case_dir, "tech_analysis.json")
        classification = _load_search_data(case_dir, "classification.json")
        presearch_candidates = _load_search_data(case_dir, "presearch_candidates.json")
        if not tech_analysis:
            return jsonify({"error": "Stage 1を先���完了してください"}), 400
        prompt_text = generate_keyword_prompt(segs, hongan, field, tech_analysis, classification, presearch_candidates)

    # Claude CLI 呼び出し
    try:
        raw_response = call_claude(prompt_text, timeout=600)
    except ClaudeClientError as e:
        return jsonify({"error": str(e), "phase": "claude_call"}), 502

    # Stage別のパース・保存
    result = {"success": False, "errors": []}

    if stage == 1:
        tech_analysis, candidates, search_formulas, errors = parse_presearch_response(raw_response)
        result["errors"] = errors
        if tech_analysis:
            _save_search_data(case_dir, "tech_analysis.json", tech_analysis)
            result["tech_analysis"] = tech_analysis
            result["success"] = True
        if candidates:
            _save_search_data(case_dir, "presearch_candidates.json", candidates)
            result["candidates"] = candidates
        if search_formulas:
            _save_search_data(case_dir, "presearch_formulas.json", search_formulas)
            result["search_formulas"] = search_formulas

    elif stage == 2:
        classification, errors = parse_classification_response(raw_response)
        result["errors"] = errors
        if classification:
            _save_search_data(case_dir, "classification.json", classification)
            result["classification"] = classification
            result["success"] = True

    else:  # stage == 3
        keyword_dictionary, errors = parse_keyword_response(raw_response)
        result["errors"] = errors
        if keyword_dictionary:
            _save_search_data(case_dir, "keyword_dictionary.json", keyword_dictionary)
            result["keyword_dictionary"] = keyword_dictionary
            result["success"] = True
            # keywords.json にも変換
            groups = convert_keyword_dict_to_groups(keyword_dictionary, segs)
            with open(kw_path, "w", encoding="utf-8") as f:
                json.dump(groups, f, ensure_ascii=False, indent=2)

    return jsonify(result)


if __name__ == "__main__":
    # templates ディレクトリがなければ作成
    (PROJECT_ROOT / "templates").mkdir(exist_ok=True)
    print("PatentCompare Web GUI")
    print("http://localhost:5000")
    app.run(debug=True, host="0.0.0.0", port=5000)
