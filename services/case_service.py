#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""案件データ管理サービス"""

import json
import re
import shutil
import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()


def get_cases_dir():
    return PROJECT_ROOT / "cases"


def get_case_dir(case_id):
    return get_cases_dir() / case_id


def _get_search_dir(case_dir):
    """search/ サブディレクトリを返す（なければ作成）"""
    d = Path(case_dir) / "search"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_search_data(case_dir, filename):
    """search/ 配下のJSONファイルを読み込む（なければ None）"""
    p = Path(case_dir) / "search" / filename
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_search_data(case_dir, filename, data):
    """search/ 配下にJSONファイルを保存"""
    d = _get_search_dir(case_dir)
    with open(d / filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_case_meta(case_id):
    """case_id に対応する案件 meta を返す。

    フォールバック順:
      1. cases/{case_id}/case.yaml が存在すればそれを読む
      2. 各案件フォルダを走査し、yaml 内 case_id / jp_id / patent_number が
         一致するものを探す (古いリンクや case_id 書換後の互換維持)
    """
    p = get_case_dir(case_id) / "case.yaml"
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            meta = yaml.safe_load(f) or {}
        # ディレクトリ名を正とする (yaml 内 case_id とのズレによる 404 回避)
        meta["case_id"] = case_id
        return meta

    cases_dir = get_cases_dir()
    if not cases_dir.exists():
        return None
    for d in cases_dir.iterdir():
        if not d.is_dir():
            continue
        cand = d / "case.yaml"
        if not cand.exists():
            continue
        try:
            with open(cand, "r", encoding="utf-8") as f:
                meta = yaml.safe_load(f) or {}
        except Exception:
            continue
        if case_id in (meta.get("case_id"), meta.get("jp_id"), meta.get("patent_number")):
            meta["case_id"] = d.name
            return meta
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
                    # ディレクトリ名と yaml 内 case_id の不整合から生じる
                    # リンク切れ (404) を防ぐため、常にディレクトリ名を正とする。
                    meta["case_id"] = d.name
                    has_hongan = (d / "hongan.json").exists()
                    has_segments = (d / "segments.json").exists()
                    has_keywords = (d / "keywords.json").exists()
                    num_citations = len(list((d / "citations").glob("*.json"))) if (d / "citations").exists() else 0
                    responses_dir = d / "responses"
                    num_responses = 0
                    num_x = 0
                    num_y = 0
                    if responses_dir.exists():
                        for rfile in responses_dir.glob("*.json"):
                            if rfile.stem.startswith("_"):
                                continue
                            num_responses += 1
                            try:
                                with open(rfile, "r", encoding="utf-8") as f:
                                    rdata = json.loads(f.read().replace('\x00', ''), strict=False)
                                cat = rdata.get("category_suggestion", "")
                                if cat.upper().startswith("X"):
                                    num_x += 1
                                elif cat.upper().startswith("Y"):
                                    num_y += 1
                            except Exception:
                                pass
                    has_excel = any((d / "output").glob("*.xlsx")) if (d / "output").exists() else False

                    meta["_has_hongan"] = has_hongan
                    meta["_has_segments"] = has_segments
                    meta["_has_keywords"] = has_keywords
                    meta["_num_citations"] = num_citations
                    meta["_num_responses"] = num_responses
                    meta["_num_x"] = num_x
                    meta["_num_y"] = num_y
                    meta["_has_excel"] = has_excel
                    cases.append(meta)
    return cases


def create_minimal_case(case_id, title="", field="cosmetics"):
    """CLI 用: PDF自動DLを伴わない最小限の案件ディレクトリを作成する。

    Returns:
        (dict, status_code)
    """
    case_dir = get_case_dir(case_id)
    if case_dir.exists():
        return {"error": f"案件 '{case_id}' は既に存在します"}, 409

    for sub in ["input", "citations", "prompts", "responses", "output"]:
        (case_dir / sub).mkdir(parents=True, exist_ok=True)

    meta = {
        "case_id": case_id,
        "title": title,
        "field": field,
        "citations": [],
    }
    save_case_meta(case_id, meta)
    return {"success": True, "case_id": case_id, "path": str(case_dir)}, 200


def compute_segments(case_id):
    """hongan.json から請求項を分節し segments.json を保存する。

    Returns:
        (dict, status_code): dict.segments は分節結果のリスト
    """
    from modules.claim_segmenter import segment_claims

    case_dir = get_case_dir(case_id)
    hongan_path = case_dir / "hongan.json"
    if not hongan_path.exists():
        return {"error": "本願テキストがありません"}, 400

    with open(hongan_path, "r", encoding="utf-8") as f:
        hongan = json.load(f)

    if not hongan.get("claims"):
        return {"error": "請求項が抽出されていません"}, 400

    segs = segment_claims(hongan["claims"])
    segments_path = case_dir / "segments.json"
    with open(segments_path, "w", encoding="utf-8") as f:
        json.dump(segs, f, ensure_ascii=False, indent=2)

    return {
        "success": True,
        "segments": segs,
        "path": str(segments_path),
        "num_claims": len(segs),
        "num_segments": sum(len(c.get("segments", [])) for c in segs),
    }, 200


def _parse_patent_input(raw):
    """ユーザ入力 (特願/特開/特許など) を分類して正規化する。

    Returns: dict with keys:
      - kind: 'application' | 'publication' | 'unknown'
      - case_id: case_id として使う文字列
      - patent_number: 特開/特許 番号 (publication の場合のみ)
      - application_number: 特願番号 (application の場合のみ)
      - jp_id: Google Patents 用 ID (publication の場合のみ、DL 試行用)
    """
    s = (raw or "").strip()
    if not s:
        return {"kind": "unknown", "case_id": "", "patent_number": "",
                "application_number": "", "jp_id": ""}
    # 全角数字・各種ハイフン・装飾除去 (frontend の _jppNormalize と同じ)
    t = s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    t = t.replace("－", "-").replace("−", "-").replace("―", "-").replace("—", "-")
    t = t.replace("／", "/")
    for noise in ("号公報", "号", "公報", "明細書"):
        t = t.replace(noise, " ")
    for b in "()（）「」【】『』":
        t = t.replace(b, " ")
    t = t.strip()

    # 特願 (application number)
    m = re.search(r'特願\s*(\d{4})\s*-\s*(\d+)', t)
    if m:
        y, n = m.group(1), m.group(2).zfill(6)
        return {
            "kind": "application",
            "case_id": f"wa-{y}-{n}",
            "patent_number": "",
            "application_number": f"特願{y}-{n}",
            "jp_id": "",
        }

    # 特開 (unexamined publication)
    m = re.search(r'特開\s*(\d{4})\s*-\s*(\d+)', t)
    if m:
        y, n = m.group(1), m.group(2).zfill(6)
        return {
            "kind": "publication",
            "case_id": f"{y}-{n}",
            "patent_number": f"特開{y}-{n}",
            "application_number": "",
            "jp_id": f"JP{y}{n}A",
        }

    # 特許 (granted)
    m = re.search(r'特許(?:第)?\s*(\d+)', t)
    if m:
        num = m.group(1)
        return {
            "kind": "publication",
            "case_id": f"jp-{num}",
            "patent_number": f"特許第{num}号",
            "application_number": "",
            "jp_id": f"JP{num}B2",
        }

    # JP2024-073024A 等
    m = re.search(r'JP\s*(\d{4})\s*[-]?\s*(\d{3,6})\s*A', t, re.I)
    if m:
        y, n = m.group(1), m.group(2).zfill(6)
        return {
            "kind": "publication",
            "case_id": f"{y}-{n}",
            "patent_number": f"特開{y}-{n}",
            "application_number": "",
            "jp_id": f"JP{y}{n}A",
        }

    # 素の yyyy-nnnnnn (従来の公開番号運用)
    m = re.match(r'^\s*(\d{4})-(\d+)\s*$', t)
    if m:
        y, n = m.group(1), m.group(2)  # zfill しない (従来互換)
        return {
            "kind": "publication",
            "case_id": f"{y}-{n}",
            "patent_number": f"特開{y}-{n.zfill(6)}",
            "application_number": "",
            "jp_id": f"JP{y}{n.zfill(6)}A",
        }

    return {"kind": "unknown", "case_id": s,
            "patent_number": "", "application_number": "", "jp_id": s}


def create_case(case_number, year="", month="", field="cosmetics",
                application_number=""):
    """新規案件を作成。特開入力時は PDF 自動 DL・抽出まで、特願入力時はフォルダ作成のみ。

    Returns:
        dict: 結果情報
    """
    from modules.patent_downloader import download_patent_pdf
    from modules.pdf_extractor import extract_patent_pdf
    from modules.claim_segmenter import segment_claims

    parsed = _parse_patent_input(case_number)
    # 明示された application_number があれば上書き
    app_num = (application_number or "").strip()
    if app_num and parsed["kind"] != "application":
        app_parsed = _parse_patent_input(app_num)
        if app_parsed["kind"] == "application":
            parsed["application_number"] = app_parsed["application_number"]

    case_id = parsed["case_id"] or case_number
    kind = parsed["kind"]

    case_dir = get_case_dir(case_id)
    if case_dir.exists():
        return {"error": f"案件 '{case_id}' は既に存在します", "case_id": case_id}

    for sub in ["input", "citations", "prompts", "responses", "output"]:
        (case_dir / sub).mkdir(parents=True, exist_ok=True)

    meta = {
        "case_id": case_id,
        "jp_id": parsed["jp_id"],
        "title": "",
        "field": field,
        "year": year,
        "month": month,
        "citations": [],
    }
    if parsed["application_number"]:
        meta["application_number"] = parsed["application_number"]
    if parsed["patent_number"]:
        meta["patent_number"] = parsed["patent_number"]
    save_case_meta(case_id, meta)

    # 特願のみ入力の場合は PDF DL をスキップ (J-PlatPat で後日確認して追記する運用)
    if kind == "application" and not parsed["jp_id"]:
        return {
            "success": True,
            "case_id": case_id,
            "kind": "application",
            "pdf_downloaded": False,
            "application_number": parsed["application_number"],
            "note": "特願のみの案件を作成しました。後で公開番号を追記するか、本願PDFを手動アップロードしてください。",
        }

    jp_id = parsed["jp_id"] or case_number
    dl_result = download_patent_pdf(jp_id, case_dir / "input", timeout=60)
    if not dl_result["success"]:
        return {
            "success": True,
            "case_id": case_id,
            "kind": kind,
            "pdf_downloaded": False,
            "error": dl_result.get("error", "PDFダウンロード失敗"),
            "google_patents_url": dl_result.get("google_patents_url", ""),
        }

    try:
        result = extract_patent_pdf(dl_result["path"], "hongan")
        with open(case_dir / "hongan.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        patent_number = result.get("patent_number", "")
        patent_title = result.get("patent_title", "")
        meta["patent_number"] = patent_number
        meta["patent_title"] = patent_title
        meta["title"] = patent_title
        save_case_meta(case_id, meta)

        num_segments = 0
        if result.get("claims"):
            segs = segment_claims(result["claims"])
            with open(case_dir / "segments.json", "w", encoding="utf-8") as f:
                json.dump(segs, f, ensure_ascii=False, indent=2)
            num_segments = sum(len(c.get("segments", [])) for c in segs)

        return {
            "success": True,
            "case_id": case_id,
            "pdf_downloaded": True,
            "patent_number": patent_number,
            "patent_title": patent_title,
            "num_claims": len(result.get("claims", [])),
            "num_paragraphs": len(result.get("paragraphs", [])),
            "num_segments": num_segments,
        }

    except Exception as e:
        return {
            "success": True,
            "case_id": case_id,
            "pdf_downloaded": True,
            "error": f"PDF抽出エラー: {str(e)}",
        }


def upload_hongan(case_id, save_path):
    """本願PDFをテキスト抽出・分節"""
    from modules.pdf_extractor import extract_patent_pdf
    from modules.claim_segmenter import segment_claims

    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    case_dir = get_case_dir(case_id)

    try:
        result = extract_patent_pdf(str(save_path), "hongan")
    except Exception as e:
        return {"error": f"PDF抽出失敗: {e}"}, 400

    # 請求項・段落がどちらも0件 → 公報ではない（経過情報PDF・ISR等の誤取込）と判定し拒否
    if not result.get("claims") and not result.get("paragraphs"):
        try:
            Path(save_path).unlink()
        except OSError:
            pass
        return {
            "error": ("請求項・段落をどちらも抽出できなかったため、本願として拒否しました。"
                      "（経過情報PDFやISR/IPERを誤ってドロップしていませんか？ "
                      "ISR/IPERは Step 4 内『ISR/書面意見から取り込み』に投入してください）"),
            "filename": Path(save_path).name,
        }, 400

    result["source_pdf"] = Path(save_path).name

    with open(case_dir / "hongan.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    extracted_number = result.get("patent_number", "")
    extracted_title = result.get("patent_title", "")
    if extracted_number:
        meta["patent_number"] = extracted_number
    if extracted_title:
        meta["patent_title"] = extracted_title
    save_case_meta(case_id, meta)

    if result.get("claims"):
        segs = segment_claims(result["claims"])
        with open(case_dir / "segments.json", "w", encoding="utf-8") as f:
            json.dump(segs, f, ensure_ascii=False, indent=2)

    return {
        "success": True,
        "patent_number": extracted_number,
        "patent_title": extracted_title,
        "num_claims": len(result.get("claims", [])),
        "num_paragraphs": len(result.get("paragraphs", [])),
    }, 200


def upload_citation(case_id, save_path, role="主引例", label=""):
    """引用文献PDFをテキスト抽出して登録"""
    from modules.pdf_extractor import extract_patent_pdf
    from modules.citation_id import normalize_citation_id

    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    case_dir = get_case_dir(case_id)

    try:
        result = extract_patent_pdf(str(save_path), "citation")
    except Exception as e:
        return {"error": f"PDF抽出失敗: {e}"}, 400

    if not result.get("claims") and not result.get("paragraphs"):
        result["_warning"] = "テキスト抽出できませんでした（スキャン画像PDFの可能性）"

    raw_doc_id = result.get("patent_number", Path(save_path).stem)
    for ch in '/\\:*?"<>|':
        raw_doc_id = raw_doc_id.replace(ch, '')
    doc_id = normalize_citation_id(raw_doc_id)
    result["role"] = role
    result["label"] = label or doc_id

    # 引用文献ID と一致するファイル名に正規化（手動アップロードで download 等の名前だと
    # find_citation_pdf / 注釈PDFがヒットしなくなるため）
    src = Path(save_path).resolve()
    input_dir = case_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = re.sub(r'[<>:"/\\|?*]', "_", doc_id).strip() or re.sub(
        r'[<>:"/\\|?*]', "_", src.stem
    )
    if not safe_stem:
        safe_stem = "citation"
    target = (input_dir / f"{safe_stem}.pdf").resolve()
    try:
        if src != target:
            if target.exists() and target != src:
                target.unlink()
            shutil.copy2(str(src), str(target))
            in_input = str(src).startswith(str(input_dir.resolve()))
            if in_input and src != target and src.is_file():
                try:
                    src.unlink()
                except OSError:
                    pass
        result["source_pdf"] = target.name
    except OSError as e:
        return {"error": f"PDFの保存(正規名へのコピー)に失敗: {e}"}, 500

    with open(case_dir / "citations" / f"{doc_id}.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    citations = meta.get("citations", [])
    if not any(normalize_citation_id(c.get("id", "")) == doc_id for c in citations):
        citations.append({"id": doc_id, "role": role, "label": label or doc_id})
        meta["citations"] = citations
        save_case_meta(case_id, meta)

    return {
        "success": True,
        "doc_id": doc_id,
        "num_claims": len(result.get("claims", [])),
        "num_paragraphs": len(result.get("paragraphs", [])),
    }, 200


def delete_citation(case_id, citation_id):
    """引用文献を個別削除"""
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    case_dir = get_case_dir(case_id)

    for subdir in ["citations", "responses", "prompts"]:
        for pattern in [f"{citation_id}.json", f"{citation_id}_prompt.txt", f"*{citation_id}*"]:
            for f in (case_dir / subdir).glob(pattern):
                f.unlink()

    meta["citations"] = [c for c in meta.get("citations", []) if c["id"] != citation_id]
    save_case_meta(case_id, meta)
    return {"success": True}, 200


def clear_all_citations(case_id):
    """全引用文献・回答をクリア"""
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    case_dir = get_case_dir(case_id)

    for subdir in ["citations", "responses", "prompts"]:
        d = case_dir / subdir
        if d.exists():
            for f in d.iterdir():
                if f.is_file():
                    f.unlink()

    meta["citations"] = []
    save_case_meta(case_id, meta)
    return {"success": True}, 200


def delete_case(case_id):
    case_dir = get_case_dir(case_id)
    if case_dir.exists():
        shutil.rmtree(str(case_dir))


def update_case_meta(case_id, data):
    """案件メタ情報を更新"""
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    for key in [
        "patent_number", "patent_title", "title", "field", "year", "month",
        "filing_date", "priority_date", "application_number",
    ]:
        if key in data:
            val = data[key]
            if val is None or (isinstance(val, str) and val.strip() == ""):
                meta.pop(key, None)
            else:
                meta[key] = val
    save_case_meta(case_id, meta)
    return {"success": True}, 200


def load_json_file(case_id, filename):
    """案件ディレクトリ直下のJSONファイルを読み込む"""
    p = get_case_dir(case_id) / filename
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                text = f.read()
            text = text.replace('\x00', '')
            return json.loads(text, strict=False)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
    return None


def save_json_file(case_id, filename, data):
    """案件ディレクトリ直下にJSONファイルを保存"""
    p = get_case_dir(case_id) / filename
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def find_citation_pdf(input_dir, citation_id):
    """input/ディレクトリから引用文献IDに対応するPDFを探す"""
    if not input_dir.exists():
        return None

    # citations/{id}.json の source_pdf（アップロード時の元ファイル名など）を優先
    try:
        cit_json = input_dir.parent / "citations" / f"{citation_id}.json"
        if cit_json.is_file():
            with open(cit_json, "r", encoding="utf-8") as f:
                d = json.load(f)
            for key in ("source_pdf", "canonical_pdf", "input_pdf"):
                hint = d.get(key)
                if not hint:
                    continue
                name = Path(str(hint)).name
                cand = input_dir / name
                if cand.is_file():
                    return cand
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass

    fw2hw = str.maketrans("０１２３４５６７８９", "0123456789")

    cid_hw = citation_id.translate(fw2hw)
    normalized = re.sub(r'[\s\-/]', '', cid_hw)
    digits = re.sub(r'[^\d]', '', cid_hw)

    for pdf_file in input_dir.glob("*.pdf"):
        stem = pdf_file.stem
        stem_hw = stem.translate(fw2hw)
        stem_normalized = re.sub(r'[\s\-/]', '', stem_hw)
        if stem_normalized == normalized:
            return pdf_file
        if len(digits) >= 6 and digits in stem_normalized:
            return pdf_file

    return None


def _resolve_hongan_pdf(case_id):
    """本願PDFの実ファイルパスを返す。見つからなければ None。"""
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

    input_dir = case_dir / "input"
    if input_dir.exists():
        meta = load_case_meta(case_id) or {}
        cit_ids = [c["id"] for c in meta.get("citations", [])]
        for p in input_dir.glob("*.pdf"):
            if not any(cid and cid in p.stem for cid in cit_ids):
                return p
    return None


def compute_related_paragraphs(case_id):
    """分節と本願段落のマッピングを計算・保存する"""
    from modules.paragraph_matcher import find_related_paragraphs

    case_dir = get_case_dir(case_id)
    segments = load_json_file(case_id, "segments.json")
    hongan = load_json_file(case_id, "hongan.json")

    if segments is None:
        return {"error": "分節データがありません"}, 400
    if hongan is None:
        return {"error": "本願データがありません"}, 400

    paragraphs = hongan.get("paragraphs", [])
    if not paragraphs:
        return {"error": "本願の段落データがありません"}, 400

    related = find_related_paragraphs(segments, paragraphs)
    save_json_file(case_id, "related_paragraphs.json", related)

    return {"success": True, "related": related}, 200


def create_bookmarked_hongan(case_id):
    """本願PDFに分節IDラベル + ブックマークを付与した新PDFを作成"""
    import fitz
    from modules.pdf_bookmark import apply_toc
    from modules.hongan_annotator import apply_hongan_annotations

    case_dir = get_case_dir(case_id)

    src_pdf = _resolve_hongan_pdf(case_id)
    if src_pdf is None:
        return {"error": "本願PDFが見つかりません。再アップロードしてください。"}, 404

    # 常に最新の分節に基づいて再計算
    res, code = compute_related_paragraphs(case_id)
    if code != 200:
        return res, code
    related = res["related"]

    segments = load_json_file(case_id, "segments.json") or []

    bookmarks = []
    claim_items = []
    para_items = []

    for claim in segments:
        for seg in claim.get("segments", []):
            sid = seg.get("id")
            stext = seg.get("text", "")
            if not sid:
                continue
            # 請求項側のアノテーション対象（関連段落があってもなくても追加）
            if stext:
                claim_items.append({"seg_id": sid, "seg_text": stext})

            paras = related.get(sid, [])
            for p in paras:
                ptype = p.get("type") or ""
                prefix = f" {ptype}" if ptype else ""
                title = f"{sid}{prefix}【{p['id']}】 (p.{p['page']})"
                bookmarks.append({"title": title, "page": p["page"]})
                para_items.append({
                    "seg_id": sid,
                    "para_id": p["id"],
                    "page": p["page"],
                })

    if not bookmarks:
        return {"error": "ブックマーク対象の関連段落が検出できませんでした"}, 400

    output_dir = case_dir / "output"
    output_dir.mkdir(exist_ok=True)
    out_pdf = output_dir / f"{case_id}_本願_bookmarked.pdf"

    doc = fitz.open(str(src_pdf))
    try:
        n_ann = apply_hongan_annotations(doc, claim_items, para_items)
        n_bm = apply_toc(doc, bookmarks)
        doc.save(str(out_pdf), garbage=3, deflate=True)
    finally:
        doc.close()

    return {
        "success": True,
        "filename": out_pdf.name,
        "path": str(out_pdf),
        "num_bookmarks": n_bm,
        "num_annotations": n_ann,
    }, 200
