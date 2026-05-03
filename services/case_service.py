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

    for sub in ["input", "citations", "prompts", "responses", "output", "analysis"]:
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

    for sub in ["input", "citations", "prompts", "responses", "output", "analysis"]:
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
        # PDF 解決時に元ファイルを正引きできるよう source_pdf を残す
        result["source_pdf"] = Path(dl_result["path"]).name
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


_HONGAN_REF_HEADER_RE = re.compile(r"【\s*特許文献\s*([0-9０-９]+)\s*】")
_FW2HW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
# マーカー直後から探す patent number 形式 (優先順)
_PATENT_ID_PATTERNS = [
    re.compile(r"特開[平昭令大明]?\s*\d{1,4}\s*[-－ー−]?\s*\d{1,7}\s*号?"),
    re.compile(r"特表[平昭令大明]?\s*\d{1,4}\s*[-－ー−]?\s*\d{1,7}\s*号?"),
    re.compile(r"特許\s*第?\s*\d{4,8}\s*号?"),
    re.compile(r"再(?:公)?表\s*\d{4}\s*[-－ー−/／]\s*\d{1,7}"),
    re.compile(r"特願\s*\d{4}\s*[-－ー−]\s*\d{1,7}"),
    re.compile(r"(?:実登|登録実用新案|実用新案登録)\s*第?\s*\d{4,8}\s*号?"),
    re.compile(r"WO\s*\d{4}\s*[/／]?\s*\d{4,7}(?:\s*[A-Z]\d?)?"),
    re.compile(r"(?:JP|US|EP|CN|KR)\s*\d{4,12}\s*[A-Z]?\d?"),
]


def _normalize_patent_id(raw: str) -> str:
    """全角数字を半角化、不要スペースを除去 (検出後の整形用)"""
    s = (raw or "").strip().translate(_FW2HW_DIGITS)
    # 連続スペース・全角スペースを除去
    s = re.sub(r"[\s　]+", "", s)
    return s


def _extract_patent_id_from_tail(text: str) -> str:
    """`【特許文献N】` 直後のテキスト断片から特許番号 1 個を抽出。"""
    if not text:
        return ""
    # 全角→半角 (検出だけのため作業コピー)
    work = text.translate(_FW2HW_DIGITS)
    # 全角ハイフン揺れ (− ー －) を - に統一
    work = work.replace("−", "-").replace("ー", "-").replace("－", "-")
    # 全角スペースを半角に
    work = work.replace("　", " ")
    for pat in _PATENT_ID_PATTERNS:
        m = pat.search(work)
        if m:
            return _normalize_patent_id(m.group(0))
    return ""


def extract_hongan_citations(case_id):
    """本願明細書 (hongan.json の paragraphs) を走査して 【特許文献N】 を抽出する。

    Returns: ({"refs": [{ref_no, patent_id, raw_text, para_id, label}, ...]}, 200)
    """
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404
    case_dir = get_case_dir(case_id)
    hongan_path = case_dir / "hongan.json"
    if not hongan_path.exists():
        return {"error": "本願データがありません"}, 404
    try:
        with hongan_path.open(encoding="utf-8") as f:
            hongan = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {"error": f"hongan.json 読込失敗: {e}"}, 500

    refs = []
    seen_pids = set()
    for para in hongan.get("paragraphs") or []:
        text = para.get("text", "")
        for m in _HONGAN_REF_HEADER_RE.finditer(text):
            ref_no_raw = m.group(1).translate(_FW2HW_DIGITS)
            try:
                ref_no = int(ref_no_raw)
            except ValueError:
                continue
            tail = text[m.end():m.end() + 200]
            cut = tail.find("【")
            if cut > 0:
                tail = tail[:cut]
            patent_id = _extract_patent_id_from_tail(tail)
            if patent_id and patent_id in seen_pids:
                continue
            if patent_id:
                seen_pids.add(patent_id)
            refs.append({
                "ref_no": ref_no,
                "patent_id": patent_id,
                "raw_text": tail.strip()[:120],
                "para_id": para.get("id"),
                "label": f"本願引用{ref_no}",
            })
    refs.sort(key=lambda r: r["ref_no"])
    return {"refs": refs}, 200


def download_and_register_hongan_refs(case_id, ref_nos=None):
    """extract_hongan_citations で抽出した特許文献を Google Patents から DL し
    citation として登録する。

    ref_nos=None なら全件、指定があればその ref_no のみ対象。

    Returns: ({"results": [{ref_no, patent_id, success, doc_id?, error?}, ...]}, 200)
    """
    extr, code = extract_hongan_citations(case_id)
    if code != 200:
        return extr, code
    refs = extr["refs"]
    if ref_nos is not None:
        ref_nos_set = set(int(n) for n in ref_nos)
        refs = [r for r in refs if r["ref_no"] in ref_nos_set]

    case_dir = get_case_dir(case_id)
    input_dir = case_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    from modules.patent_downloader import download_patent_pdf, build_jplatpat_url

    results = []
    for r in refs:
        if not r.get("patent_id"):
            results.append({
                "ref_no": r["ref_no"], "patent_id": "",
                "success": False, "error": "特許番号が抽出できなかった",
                "raw_text": r.get("raw_text", ""),
            })
            continue
        dl = download_patent_pdf(r["patent_id"], save_dir=input_dir)
        if not dl.get("success"):
            # 失敗時は J-PlatPat 固定 URL も返して UI で手動 DL を促す
            # (Google Patents は古い特許 / マイナー国の特許で失敗しやすい)
            results.append({
                "ref_no": r["ref_no"], "patent_id": r["patent_id"],
                "success": False,
                "error": dl.get("error", "DL 失敗"),
                "google_patents_url": dl.get("google_patents_url", ""),
                "jplatpat_url": build_jplatpat_url(r["patent_id"]),
            })
            continue
        # citation として登録 (role=「本願引用N」、label は doc_id を採用)
        try:
            up_res, up_code = upload_citation(
                case_id, dl["path"], role=r["label"], label="",
            )
        except Exception as e:
            results.append({
                "ref_no": r["ref_no"], "patent_id": r["patent_id"],
                "success": False, "error": f"登録失敗: {e}",
            })
            continue
        results.append({
            "ref_no": r["ref_no"], "patent_id": r["patent_id"],
            "success": (up_code == 200),
            "doc_id": up_res.get("doc_id"),
            "warning": up_res.get("warning"),
            "error": up_res.get("error") if up_code != 200 else None,
        })
    success_count = sum(1 for x in results if x["success"])
    return {"results": results, "success_count": success_count,
            "total": len(results)}, 200


def register_citation_by_patent_id(case_id, patent_id, role="主引例"):
    """公報番号 1 件を Google Patents から DL → 引用文献として登録。

    chat の suggestion (add_citation) からの呼び出しを想定。
    DL に失敗したら J-PlatPat 固定 URL を返して手動 DL を促す。

    Args:
        case_id: 案件 ID
        patent_id: 公報番号 (例: "WO2022/044362", "特開2020-132594", "JP6960743B2")
        role: 引用文献の役割 (主引例 / 副引例 / 参考 など。任意ラベル)

    Returns:
        ({success/error/doc_id/...}, status_code)
    """
    from modules.patent_downloader import download_patent_pdf, build_jplatpat_url

    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404
    pid = (patent_id or "").strip()
    if not pid:
        return {"error": "patent_id が空です"}, 400

    case_dir = get_case_dir(case_id)
    input_dir = case_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    dl = download_patent_pdf(pid, save_dir=input_dir)
    if not dl.get("success"):
        return {
            "error": dl.get("error", "PDF ダウンロードに失敗しました"),
            "patent_id": pid,
            "google_patents_url": dl.get("google_patents_url", ""),
            "jplatpat_url": build_jplatpat_url(pid),
            "hint": "Google Patents で見つからない場合は J-PlatPat の URL から手動 DL し "
                    "Step 4 「引用文献を追加」から PDF をアップロードしてください。",
        }, 502

    up_res, up_code = upload_citation(case_id, dl["path"], role=role, label="")
    if up_code != 200:
        return up_res, up_code
    up_res["patent_id"] = pid
    return up_res, 200


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

    response = {
        "success": True,
        "doc_id": doc_id,
        "num_claims": len(result.get("claims", [])),
        "num_paragraphs": len(result.get("paragraphs", [])),
    }
    if result.get("_warning"):
        response["warning"] = result["_warning"]
    return response, 200


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


def fetch_hongan_classification_from_jplatpat(case_id):
    """本願の公開番号で J-PlatPat に問い合わせて IPC/FI/Fターム/テーマコード を取得し
    cases/<id>/search/classification.json に保存する。

    既存の LLM 由来の classification.json は上書きせず、`fterm` フィールドだけは
    LLM 推測値より J-PlatPat 由来 (実付与) を優先する。

    Returns:
        ({"success": True, "classifications": {...}}, 200) | (error, code)
    """
    from modules.jplatpat_client import fetch_jplatpat_full_text

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    pn = (meta.get("patent_number") or "").strip()
    if not pn:
        return {"error": "本願の公開番号が設定されていません (case meta の patent_number)"}, 400

    # J-PlatPat 詳細ページから raw + classifications を取得
    try:
        ft = fetch_jplatpat_full_text(pn, language="ja")
    except Exception as e:
        return {"error": f"J-PlatPat 取得失敗: {e}"}, 500

    if ft.get("error"):
        return {"error": f"J-PlatPat 取得失敗: {ft['error']}"}, 500

    cls = ft.get("classifications") or {}
    if not any(cls.values()):
        return {
            "error": ("J-PlatPat 詳細ページから書誌情報を抽出できませんでした。"
                      "公開番号が正しいか、J-PlatPat の DOM 変更が無いかを確認してください。"),
        }, 500

    # 保存形式は既存 fterm_candidates が読む {"fterm": [{"code","label","type","note"},...]} に合わせる
    out = {
        "patent_number": pn,
        "source": "jplatpat",
        "fetched_at": ft.get("fetched_at"),
        "ipc": [{"code": c} for c in (cls.get("ipc") or [])],
        "fi": [{"code": c} for c in (cls.get("fi") or [])],
        "theme_codes": cls.get("theme_codes") or [],
        "fterm": [
            {"code": c, "label": "", "type": "本願付与", "note": "J-PlatPat より自動取得"}
            for c in (cls.get("fterm") or [])
        ],
    }

    search_dir = case_dir / "search"
    search_dir.mkdir(parents=True, exist_ok=True)
    out_path = search_dir / "classification.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    return {
        "success": True,
        "patent_number": pn,
        "n_ipc": len(out["ipc"]),
        "n_fi": len(out["fi"]),
        "n_fterm": len(out["fterm"]),
        "n_theme": len(out["theme_codes"]),
        "theme_codes": out["theme_codes"],
        "saved_to": str(out_path),
    }, 200


def get_hongan_tables(case_id):
    """既に抽出済みの本願表データ (cases/<id>/output/tables/hongan/tables.json) を返す。"""
    case_dir = get_case_dir(case_id)
    tables_json = case_dir / "output" / "tables" / "hongan" / "tables.json"
    if not tables_json.exists():
        return {"exists": False}, 200
    try:
        with open(tables_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {"error": f"tables.json 読み込み失敗: {e}"}, 500
    return {"exists": True, "data": data}, 200


# ---------- 引用文献の Vision 表抽出 ----------

def _safe_pid_for_path(pid: str) -> str:
    """patent_id を安全なディレクトリ名に変換 (citations/_hit_text と同方針)。"""
    return re.sub(r"[^A-Za-z0-9_\-]", "_", str(pid))


def _citation_tables_dir(case_id: str, citation_id: str) -> Path:
    return (get_case_dir(case_id) / "output" / "tables"
            / "citations" / _safe_pid_for_path(citation_id))


def get_citation_tables(case_id, citation_id):
    """引用文献 1 件の抽出済み表データを返す。"""
    p = _citation_tables_dir(case_id, citation_id) / "tables.json"
    if not p.exists():
        return {"exists": False}, 200
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {"error": f"tables.json 読み込み失敗: {e}"}, 500
    return {"exists": True, "data": data}, 200


def get_citation_tables_cells(case_id):
    """全引用文献の抽出済みセル文字列を patent_id → flat-text のマップで返す。

    PKM ハイライトの表ヒット集計用。各引用について、抽出された全表の全セルを
    タイトル + ヘッダ + 各行セルを改行連結した 1 つの文字列にまとめる。

    引用登録 (cases/<id>/citations/*.json) されていない hit でも、
    画像 records ベースで抽出済みなら output/tables/citations/* に存在するので
    そちらを直接 scan する (登録済みは tables.json の doc_id でキー復元)。
    """
    case_dir = get_case_dir(case_id)
    cells = {}
    tables_root = case_dir / "output" / "tables" / "citations"
    if not tables_root.exists():
        return {"cells": cells}, 200
    for d in sorted(tables_root.iterdir()):
        if not d.is_dir():
            continue
        tj = d / "tables.json"
        if not tj.exists():
            continue
        try:
            with open(tj, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            continue
        # doc_id (生 patent_id) をキーに、画像 records 抽出時にもマッチさせる
        cid = raw.get("doc_id") or d.name
        chunks = []
        for t in raw.get("tables", []):
            if not t.get("is_table"):
                continue
            if t.get("title"):
                chunks.append(str(t["title"]))
            for h in (t.get("headers") or []):
                chunks.append(str(h))
            for row in (t.get("rows") or []):
                for c in (row.get("cells") or []):
                    chunks.append(str(c))
        cells[cid] = "\n".join(chunks)
    return {"cells": cells}, 200


def list_citation_table_status(case_id):
    """表抽出ステータス一覧を返す (引用登録済み + 抽出済みの全 patent_id)。

    引用登録 (cases/<id>/citations/*.json) されていなくても、
    既に表抽出が走った patent_id (output/tables/citations/<safe_pid>/) は
    status に含めて UI から確認できるようにする。
    """
    case_dir = get_case_dir(case_id)
    items_map: dict[str, dict] = {}

    # 1) 引用登録済みのもの: extracted フラグをファイル存在で判定
    citations_dir = case_dir / "citations"
    if citations_dir.exists():
        for cj in sorted(citations_dir.glob("*.json")):
            cid = cj.stem
            items_map[cid] = {"citation_id": cid, "extracted": False}

    # 2) 抽出済みディレクトリを scan して patent_id を補完
    tables_root = case_dir / "output" / "tables" / "citations"
    if tables_root.exists():
        for d in sorted(tables_root.iterdir()):
            if not d.is_dir():
                continue
            tj = d / "tables.json"
            if not tj.exists():
                continue
            # 抽出済み: 中身から citation_id (= doc_id) を取得 (safe_pid 化前の生 ID)
            try:
                with open(tj, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                cid = raw.get("doc_id") or d.name
            except Exception:
                cid = d.name
                raw = {}
            entry = items_map.get(cid, {"citation_id": cid})
            entry["extracted"] = True
            entry["n_table"] = raw.get("n_table", 0)
            entry["n_error"] = raw.get("n_error", 0)
            entry["cost"] = raw.get("total_cost_usd_equivalent", 0)
            entry["extracted_at"] = raw.get("extracted_at")
            entry["source_kind"] = raw.get("source_kind", "pdf")
            items_map[cid] = entry

    return {"items": list(items_map.values())}, 200


def stream_citation_table_extraction(case_id, citation_id, *,
                                       model="sonnet", effort="low",
                                       force=False):
    """1 件の引用文献から表抽出 (SSE 形式で進捗配信)。

    抽出ソースの優先順位:
      1. cases/<id>/input/ にある PDF (引用登録済み)
      2. cases/<id>/search_runs/_hit_text/<pid>.json の images
         (Google Patents 等から既に取得済み — DL 前でも OK)
    どちらも無ければエラー。
    """
    import queue
    import threading
    from services.table_extractor import (
        extract_tables_from_pdf, extract_tables_from_image_records,
    )
    from services.search_run_service import get_hit_text

    case_dir = get_case_dir(case_id)
    out_dir = _citation_tables_dir(case_id, citation_id)
    if (out_dir / "tables.json").exists() and not force:
        yield "data: " + json.dumps(
            {"stage": "skip", "citation_id": citation_id,
             "message": "既に抽出済み (force=true で再実行可)"},
            ensure_ascii=False,
        ) + "\n\n"
        return

    pdf = find_citation_pdf(case_dir / "input", citation_id)
    image_records = None
    source_kind = None
    if pdf is not None:
        source_kind = "pdf"
    else:
        # 全文取得済みなら images から抽出 (PDF DL 不要)
        ht = get_hit_text(case_id, citation_id)
        if ht and isinstance(ht.get("images"), list) and ht["images"]:
            image_records = ht["images"]
            source_kind = "image_records"
        else:
            yield "data: " + json.dumps(
                {"stage": "error", "citation_id": citation_id,
                 "message": (f"引用文献 {citation_id} の PDF も全文 cache の images も見つかりません。"
                             "📄 ボタンで全文取得を実行してから再試行してください。")},
                ensure_ascii=False,
            ) + "\n\n"
            return

    events: "queue.Queue[dict]" = queue.Queue()

    def progress(stage, current, total, info):
        events.put({
            "stage": stage, "current": current,
            "total": total, "info": info,
            "citation_id": citation_id,
        })

    box = {}

    def run():
        try:
            if source_kind == "pdf":
                r = extract_tables_from_pdf(
                    pdf, out_dir, model=model, effort=effort, progress=progress,
                )
            else:
                r = extract_tables_from_image_records(
                    image_records, out_dir, doc_id=citation_id,
                    model=model, effort=effort, progress=progress,
                )
            box["summary"] = r
        except Exception as e:
            box["error"] = f"{type(e).__name__}: {e}"
        events.put({"stage": "_done"})

    th = threading.Thread(target=run, daemon=True)
    th.start()

    yield "data: " + json.dumps(
        {"stage": "start", "citation_id": citation_id,
         "source_kind": source_kind,
         "pdf": Path(pdf).name if pdf else None,
         "image_count": len(image_records) if image_records else None},
        ensure_ascii=False,
    ) + "\n\n"

    while True:
        ev = events.get()
        if ev.get("stage") == "_done":
            break
        yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"

    th.join(timeout=2)

    if "error" in box:
        yield "data: " + json.dumps(
            {"stage": "error", "citation_id": citation_id,
             "message": box["error"]},
            ensure_ascii=False,
        ) + "\n\n"
        return

    s = box.get("summary", {})
    yield "data: " + json.dumps({
        "stage": "done", "citation_id": citation_id,
        "summary": {
            "n_table": s.get("n_table"),
            "n_nontable": s.get("n_nontable"),
            "n_error": s.get("n_error"),
            "candidates_total": s.get("candidates_total"),
            "candidates_targeted": s.get("candidates_targeted"),
            "total_duration_ms": s.get("total_duration_ms"),
            "total_cost_usd_equivalent": s.get("total_cost_usd_equivalent"),
            "body_table_references": s.get("body_table_references", []),
        },
    }, ensure_ascii=False) + "\n\n"


def stream_bulk_citation_table_extraction(case_id, citation_ids, *,
                                            model="sonnet", effort="low",
                                            force=False):
    """複数の引用文献を順次抽出 (各 citation の進捗を中継)。"""
    yield "data: " + json.dumps(
        {"stage": "bulk_start", "total": len(citation_ids),
         "citation_ids": citation_ids},
        ensure_ascii=False,
    ) + "\n\n"
    summary_per_cid = {}
    for i, cid in enumerate(citation_ids, 1):
        yield "data: " + json.dumps(
            {"stage": "bulk_item_start", "current": i,
             "total": len(citation_ids), "citation_id": cid},
            ensure_ascii=False,
        ) + "\n\n"
        for ev_str in stream_citation_table_extraction(
            case_id, cid, model=model, effort=effort, force=force,
        ):
            yield ev_str
            # 最終 done/skip/error イベントを記録
            try:
                ev = json.loads(ev_str.split("data: ", 1)[1].strip())
                if ev.get("stage") in ("done", "skip", "error"):
                    summary_per_cid[cid] = ev
            except Exception:
                pass
    yield "data: " + json.dumps(
        {"stage": "bulk_done", "summary_per_citation": summary_per_cid},
        ensure_ascii=False,
    ) + "\n\n"


def stream_hongan_table_extraction(case_id, *, model="sonnet", effort="low"):
    """本願 PDF から実施例表を SSE で抽出 (進捗をリアルタイム配信)。

    yields: SSE 形式の "data: <json>\\n\\n" 文字列。
    各イベントは {"stage": "...", ...} の辞書。
    最終イベントは {"stage": "done", "summary": {...}} もしくは {"stage": "error", ...}。
    """
    import queue
    import threading
    from services.table_extractor import extract_tables_from_pdf

    src_pdf = _resolve_hongan_pdf(case_id)
    if src_pdf is None:
        yield "data: " + json.dumps(
            {"stage": "error", "message": "本願PDFが見つかりません"},
            ensure_ascii=False,
        ) + "\n\n"
        return

    case_dir = get_case_dir(case_id)
    out_dir = case_dir / "output" / "tables" / "hongan"

    events: "queue.Queue[dict]" = queue.Queue()

    def progress(stage, current, total, info):
        events.put({
            "stage": stage, "current": current,
            "total": total, "info": info,
        })

    result_box = {}

    def run():
        try:
            r = extract_tables_from_pdf(
                src_pdf, out_dir, model=model, effort=effort, progress=progress,
            )
            result_box["summary"] = r
        except Exception as e:
            result_box["error"] = f"{type(e).__name__}: {e}"
        events.put({"stage": "_done"})

    th = threading.Thread(target=run, daemon=True)
    th.start()

    yield "data: " + json.dumps(
        {"stage": "start", "pdf": Path(src_pdf).name},
        ensure_ascii=False,
    ) + "\n\n"

    while True:
        ev = events.get()
        if ev.get("stage") == "_done":
            break
        yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"

    th.join(timeout=2)

    if "error" in result_box:
        yield "data: " + json.dumps(
            {"stage": "error", "message": result_box["error"]},
            ensure_ascii=False,
        ) + "\n\n"
        return

    summary = result_box.get("summary", {})
    # tables 本体は重いので summary には件数・コスト・パスのみ含める。
    # 詳細は GET /case/<id>/hongan/tables で別取得。
    light = {
        "candidates_total": summary.get("candidates_total"),
        "candidates_targeted": summary.get("candidates_targeted"),
        "candidates_skipped": summary.get("candidates_skipped"),
        "n_table": summary.get("n_table"),
        "n_nontable": summary.get("n_nontable"),
        "n_error": summary.get("n_error"),
        "total_duration_ms": summary.get("total_duration_ms"),
        "total_cost_usd_equivalent": summary.get("total_cost_usd_equivalent"),
        "body_table_references": summary.get("body_table_references", []),
        "output_json": summary.get("output_json"),
    }
    yield "data: " + json.dumps(
        {"stage": "done", "summary": light},
        ensure_ascii=False,
    ) + "\n\n"


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

    # 既存の同名ファイルが PDF-XChange 等で開いていると上書きできず
    # "Permission denied" になるため、書込先を試行錯誤する。
    #   1) 既定: "{case_id}_本願_bookmarked.pdf"
    #   2) 失敗 (ロック中): "{case_id}_本願_bookmarked_{HHMMSS}.pdf"
    #   3) 古いタイムスタンプ付き出力は、新規ファイルを開いたあと残骸として残るが
    #      output/ ディレクトリを定期掃除すれば良い
    base_name = f"{case_id}_本願_bookmarked.pdf"
    out_pdf = output_dir / base_name

    # keywords.json があれば本願 PDF にもキーワードハイライトを付与
    # (引用文献注釈 PDF と同じ配色で塗る)
    kw_for_hongan = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        try:
            with kw_path.open(encoding="utf-8") as f:
                kw_for_hongan = json.load(f)
        except (OSError, json.JSONDecodeError):
            kw_for_hongan = None

    def _save(target):
        doc = fitz.open(str(src_pdf))
        try:
            n_ann_local = apply_hongan_annotations(
                doc, claim_items, para_items, keywords=kw_for_hongan)
            n_bm_local = apply_toc(doc, bookmarks)
            doc.save(str(target), garbage=3, deflate=True)
        finally:
            doc.close()
        return n_ann_local, n_bm_local

    try:
        n_ann, n_bm = _save(out_pdf)
    except Exception as e:
        msg = str(e).lower()
        # Permission denied / locked / sharing violation 系を検出して別名で保存
        is_lock = ("permission denied" in msg or "being used by another" in msg
                   or "sharing violation" in msg or "cannot remove file" in msg)
        if not is_lock:
            return {"error": f"PDF 保存エラー: {e}"}, 500
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%H%M%S")
        out_pdf = output_dir / f"{case_id}_本願_bookmarked_{ts}.pdf"
        try:
            n_ann, n_bm = _save(out_pdf)
        except Exception as e2:
            return {
                "error": (
                    "PDF 保存エラー (リトライ後も失敗): " + str(e2) +
                    "\n既存の本願PDFを開いている PDF ビューア (PDF-XChange Editor 等) を閉じてから再試行してください"
                )
            }, 500

    return {
        "success": True,
        "filename": out_pdf.name,
        "path": str(out_pdf),
        "num_bookmarks": n_bm,
        "num_annotations": n_ann,
    }, 200
