#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""検索パイプラインサービス"""

import json
from pathlib import Path
from datetime import datetime

from services.case_service import (
    get_case_dir, load_case_meta, save_case_meta,
    load_search_data, save_search_data,
)


# ------------------------------------------------------------------
# 1. 先行技術検索プロンプト生成
# ------------------------------------------------------------------

def search_prompt(case_id):
    """分節+キーワードから先行技術検索プロンプトを生成し、事前検索結果を注入

    Returns:
        (dict, int): {"prompt": ..., "char_count": ...}, status_code
    """
    from modules.search_prompt_generator import generate_search_prompt
    from modules.search_injector import inject_search_results

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    segments_path = case_dir / "segments.json"
    if not segments_path.exists():
        return {"error": "分節データがありません。Step 2を完了してください。"}, 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    field = meta.get("field", "cosmetics")
    prompt_text = generate_search_prompt(segs, keywords, field, case_meta=meta)

    # SerpAPI / Playwright で事前検索し、結果をプロンプトに注入
    prompt_text = inject_search_results(prompt_text, segs, keywords, field)

    # ファイルに保存
    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    with open(prompts_dir / "search_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt_text)

    return {"prompt": prompt_text, "char_count": len(prompt_text)}, 200


# ------------------------------------------------------------------
# 2. 先行技術検索レスポンスのパース
# ------------------------------------------------------------------

def search_response(case_id, raw_text):
    """Claudeの検索回答をパースし、候補をsearch_candidates.jsonに保存

    Args:
        case_id: 案件ID
        raw_text: Claudeの回答テキスト

    Returns:
        (dict, int): {"success": ..., "candidates": ..., "errors": ...}, status_code
    """
    from modules.search_prompt_generator import parse_search_response

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    if not raw_text.strip():
        return {"error": "テキストが空です"}, 400

    candidates, errors = parse_search_response(raw_text)

    if candidates:
        save_data = {
            "generated_at": datetime.now().isoformat(),
            "candidates": candidates,
        }
        with open(case_dir / "search_candidates.json", "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)

    return {
        "success": candidates is not None,
        "candidates": candidates or [],
        "errors": errors,
    }, 200


# ------------------------------------------------------------------
# 3. 候補文献PDFダウンロード＆引用文献登録
# ------------------------------------------------------------------

def search_download(case_id, patent_ids, role="主引例"):
    """候補文献のPDFをダウンロードし、テキスト抽出して引用文献に登録

    Args:
        case_id: 案件ID
        patent_ids: ダウンロード対象の特許IDリスト
        role: 引用文献の役割（"主引例" 等）

    Returns:
        (dict, int): {"results": [...]}, status_code
    """
    from modules.patent_downloader import download_patent_pdf
    from modules.pdf_extractor import extract_patent_pdf

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    if not patent_ids:
        return {"error": "patent_id を指定してください"}, 400

    results = []
    for patent_id in patent_ids:
        dl_result = download_patent_pdf(patent_id, case_dir / "input")

        if dl_result["success"]:
            try:
                extracted = extract_patent_pdf(dl_result["path"], "citation")
                doc_id = extracted.get("patent_number", patent_id)
                extracted["role"] = role
                extracted["label"] = doc_id

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

    return {"results": results}, 200


# ------------------------------------------------------------------
# 4. 先行技術検索パイプライン直接実行
# ------------------------------------------------------------------

def search_execute(case_id, model=None):
    """プロンプト生成 → Claude CLI 呼び出し → パース を一気通貫で実行

    Returns:
        (dict, int): {"success": ..., "candidates": ..., ...}, status_code
    """
    from modules.search_prompt_generator import generate_search_prompt, parse_search_response
    from modules.claude_client import call_claude, ClaudeClientError
    from modules.search_injector import inject_search_results

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    segments_path = case_dir / "segments.json"
    if not segments_path.exists():
        return {"error": "分節データがありません。Step 2を完了してください。"}, 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    field = meta.get("field", "cosmetics")
    prompt_text = generate_search_prompt(segs, keywords, field, case_meta=meta)

    # 事前検索結果を注入
    prompt_text = inject_search_results(prompt_text, segs, keywords, field)

    # プロンプト保存
    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    with open(prompts_dir / "search_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt_text)

    # Claude CLI 呼び出し
    try:
        raw_response = call_claude(prompt_text, timeout=600, use_search=False, model=model)
    except ClaudeClientError as e:
        return {"error": str(e), "phase": "claude_call"}, 502

    # パース
    candidates, errors = parse_search_response(raw_response)

    if candidates:
        save_data = {
            "generated_at": datetime.now().isoformat(),
            "candidates": candidates,
        }
        with open(case_dir / "search_candidates.json", "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)

    return {
        "success": candidates is not None,
        "candidates": candidates or [],
        "errors": errors,
        "char_count": len(prompt_text),
        "response_length": len(raw_response),
    }, 200


# ------------------------------------------------------------------
# 5. Stage 1: 予備検索プロンプト生成
# ------------------------------------------------------------------

def presearch_prompt(case_id):
    """予備検索プロンプトを生成して保存

    Returns:
        (dict, int): {"prompt": ..., "char_count": ...}, status_code
    """
    from modules.search_prompt_generator import generate_presearch_prompt

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    segments_path = case_dir / "segments.json"
    hongan_path = case_dir / "hongan.json"
    if not segments_path.exists():
        return {"error": "分節データがありません。Step 2を完了してください。"}, 400

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

    return {"prompt": prompt_text, "char_count": len(prompt_text)}, 200


# ------------------------------------------------------------------
# 6. Stage 1: 予備検索レスポンスのパース
# ------------------------------------------------------------------

def presearch_parse(case_id, raw_text):
    """予備検索の回答をパースし、tech_analysis / candidates / formulas を保存

    Args:
        case_id: 案件ID
        raw_text: Claudeの回答テキスト

    Returns:
        (dict, int): {"success": ..., "tech_analysis": ..., ...}, status_code
    """
    from modules.search_prompt_generator import parse_presearch_response

    case_dir = get_case_dir(case_id)
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404

    if not raw_text.strip():
        return {"error": "テキストが空です"}, 400

    tech_analysis, candidates, search_formulas, errors = parse_presearch_response(raw_text)

    saved = []
    if tech_analysis:
        save_search_data(case_dir, "tech_analysis.json", tech_analysis)
        saved.append("tech_analysis")
    if candidates:
        save_search_data(case_dir, "presearch_candidates.json", candidates)
        saved.append("presearch_candidates")
    if search_formulas:
        save_search_data(case_dir, "presearch_formulas.json", search_formulas)
        saved.append("presearch_formulas")

    return {
        "success": tech_analysis is not None,
        "tech_analysis": tech_analysis,
        "candidates": candidates or [],
        "search_formulas": search_formulas or [],
        "errors": errors,
        "saved": saved,
    }, 200


# ------------------------------------------------------------------
# 7. Stage 2: 分類特定プロンプト生成
# ------------------------------------------------------------------

def classify_prompt(case_id):
    """分類特定プロンプトを生成して保存

    Returns:
        (dict, int): {"prompt": ..., "char_count": ...}, status_code
    """
    from modules.search_prompt_generator import generate_classification_prompt

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    segments_path = case_dir / "segments.json"
    hongan_path = case_dir / "hongan.json"
    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    hongan = None
    if hongan_path.exists():
        with open(hongan_path, "r", encoding="utf-8") as f:
            hongan = json.load(f)

    # Stage 1 の結果を読み込み
    tech_analysis = load_search_data(case_dir, "tech_analysis.json")
    presearch_candidates = load_search_data(case_dir, "presearch_candidates.json")

    if not tech_analysis:
        return {"error": "技術構造化データがありません。Stage 1を先に完了してください。"}, 400

    field = meta.get("field", "cosmetics")
    prompt_text = generate_classification_prompt(
        segs, hongan, field, tech_analysis, presearch_candidates
    )

    # 保存
    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    with open(prompts_dir / "classification_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt_text)

    return {"prompt": prompt_text, "char_count": len(prompt_text)}, 200


# ------------------------------------------------------------------
# 8. Stage 2: 分類特定レスポンスのパース
# ------------------------------------------------------------------

def classify_parse(case_id, raw_text):
    """分類特定の回答をパースし、classification.json を保存

    Args:
        case_id: 案件ID
        raw_text: Claudeの回答テキスト

    Returns:
        (dict, int): {"success": ..., "classification": ..., "errors": ...}, status_code
    """
    from modules.search_prompt_generator import parse_classification_response

    case_dir = get_case_dir(case_id)
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404

    if not raw_text.strip():
        return {"error": "テキストが空です"}, 400

    classification, errors = parse_classification_response(raw_text)

    if classification:
        save_search_data(case_dir, "classification.json", classification)

    return {
        "success": classification is not None,
        "classification": classification,
        "errors": errors,
    }, 200


# ------------------------------------------------------------------
# 9. Stage 3: キーワード辞書プロンプト生成
# ------------------------------------------------------------------

def keyword_dict_prompt(case_id):
    """キーワード辞書プロンプトを生成して保存

    Returns:
        (dict, int): {"prompt": ..., "char_count": ...}, status_code
    """
    from modules.search_prompt_generator import generate_keyword_prompt

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    segments_path = case_dir / "segments.json"
    hongan_path = case_dir / "hongan.json"
    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    hongan = None
    if hongan_path.exists():
        with open(hongan_path, "r", encoding="utf-8") as f:
            hongan = json.load(f)

    # Stage 1-2 の結果を読み込み
    tech_analysis = load_search_data(case_dir, "tech_analysis.json")
    classification = load_search_data(case_dir, "classification.json")
    presearch_candidates = load_search_data(case_dir, "presearch_candidates.json")

    if not tech_analysis:
        return {"error": "技術構造化データがありません。Stage 1を先に完了してください。"}, 400

    field = meta.get("field", "cosmetics")
    prompt_text = generate_keyword_prompt(
        segs, hongan, field, tech_analysis, classification, presearch_candidates
    )

    # 保存
    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    with open(prompts_dir / "keyword_dict_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt_text)

    return {"prompt": prompt_text, "char_count": len(prompt_text)}, 200


# ------------------------------------------------------------------
# 10. Stage 3: キーワード辞書レスポンスのパース
# ------------------------------------------------------------------

def keyword_dict_parse(case_id, raw_text):
    """キーワード辞書の回答をパースし、keyword_dictionary.json と keywords.json を保存

    Args:
        case_id: 案件ID
        raw_text: Claudeの回答テキスト

    Returns:
        (dict, int): {"success": ..., "keyword_dictionary": ..., "errors": ...}, status_code
    """
    from modules.search_prompt_generator import (
        parse_keyword_response, convert_keyword_dict_to_groups,
    )

    case_dir = get_case_dir(case_id)
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404

    if not raw_text.strip():
        return {"error": "テキストが空です"}, 400

    keyword_dictionary, errors = parse_keyword_response(raw_text)

    if keyword_dictionary:
        save_search_data(case_dir, "keyword_dictionary.json", keyword_dictionary)

        # keywords.json にも変換して保存
        segments_path = Path(case_dir) / "segments.json"
        if segments_path.exists():
            with open(segments_path, "r", encoding="utf-8") as f:
                segs = json.load(f)
            groups = convert_keyword_dict_to_groups(keyword_dictionary, segs)
            kw_path = Path(case_dir) / "keywords.json"
            with open(kw_path, "w", encoding="utf-8") as f:
                json.dump(groups, f, ensure_ascii=False, indent=2)

    return {
        "success": keyword_dictionary is not None,
        "keyword_dictionary": keyword_dictionary,
        "errors": errors,
    }, 200


# ------------------------------------------------------------------
# 11. 3段階検索の進捗状況
# ------------------------------------------------------------------

def search_status(case_id):
    """3段階検索の進捗状況を返す

    Returns:
        (dict, int): {"stage1": ..., "stage2": ..., "stage3": ..., "completed_stages": N}, status_code
    """
    case_dir = get_case_dir(case_id)
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404

    status = {
        "stage1": {
            "tech_analysis": load_search_data(case_dir, "tech_analysis.json") is not None,
            "presearch_candidates": load_search_data(case_dir, "presearch_candidates.json") is not None,
            "presearch_formulas": load_search_data(case_dir, "presearch_formulas.json") is not None,
        },
        "stage2": {
            "classification": load_search_data(case_dir, "classification.json") is not None,
        },
        "stage3": {
            "keyword_dictionary": load_search_data(case_dir, "keyword_dictionary.json") is not None,
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
    return status, 200


# ------------------------------------------------------------------
# 12. search/ 配下のデータファイル取得
# ------------------------------------------------------------------

def get_search_data_file(case_id, filename):
    """search/ 配下の指定ファイルを読み込んで返す（許可リストで検証）

    Args:
        case_id: 案件ID
        filename: 読み込むファイル名

    Returns:
        (dict|list, int): ファイル内容またはエラー, status_code
    """
    case_dir = get_case_dir(case_id)
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404

    allowed = {
        "tech_analysis.json",
        "presearch_candidates.json",
        "presearch_formulas.json",
        "classification.json",
        "keyword_dictionary.json",
    }
    if filename not in allowed:
        return {"error": "不正なファイル名です"}, 400

    data = load_search_data(case_dir, filename)
    if data is None:
        return {"error": f"{filename} がありません"}, 404

    return data, 200


# ------------------------------------------------------------------
# 13. 3段階検索の個別ステージ直接実行
# ------------------------------------------------------------------

def stage_execute(case_id, stage, model=None):
    """指定ステージのプロンプト生成 → Claude CLI → パース → 保存 を一気通貫で実行

    Args:
        case_id: 案件ID
        stage: 実行するステージ (1, 2, or 3)
        model: モデル名 ('opus'/'sonnet'/'haiku') または完全 ID。

    Returns:
        (dict, int): 実行結果, status_code
    """
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
        return {"error": "案件が見つかりません"}, 404

    if stage not in (1, 2, 3):
        return {"error": "stage は 1, 2, 3 のいずれかを指定してください"}, 400

    segments_path = Path(case_dir) / "segments.json"
    hongan_path = Path(case_dir) / "hongan.json"
    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400

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

    # --- Stage 別のプロンプト生成 ---
    if stage == 1:
        prompt_text = generate_presearch_prompt(segs, hongan, keywords, field, case_meta=meta)
    elif stage == 2:
        tech_analysis = load_search_data(case_dir, "tech_analysis.json")
        presearch_candidates = load_search_data(case_dir, "presearch_candidates.json")
        if not tech_analysis:
            return {"error": "Stage 1を先に完了してください"}, 400
        prompt_text = generate_classification_prompt(
            segs, hongan, field, tech_analysis, presearch_candidates
        )
    else:  # stage == 3
        tech_analysis = load_search_data(case_dir, "tech_analysis.json")
        classification = load_search_data(case_dir, "classification.json")
        presearch_candidates = load_search_data(case_dir, "presearch_candidates.json")
        if not tech_analysis:
            return {"error": "Stage 1を先に完了してください"}, 400
        prompt_text = generate_keyword_prompt(
            segs, hongan, field, tech_analysis, classification, presearch_candidates
        )

    # --- Claude CLI 呼び出し（Stage 1 はウェブ検索を有効化） ---
    try:
        raw_response = call_claude(
            prompt_text, timeout=600, use_search=(stage == 1), model=model,
        )
    except ClaudeClientError as e:
        return {"error": str(e), "phase": "claude_call"}, 502

    # --- Stage 別のパース・保存 ---
    result = {"success": False, "errors": []}

    if stage == 1:
        tech_analysis, candidates, search_formulas, errors = parse_presearch_response(raw_response)
        result["errors"] = errors
        if tech_analysis:
            save_search_data(case_dir, "tech_analysis.json", tech_analysis)
            result["tech_analysis"] = tech_analysis
            result["success"] = True
        if candidates:
            save_search_data(case_dir, "presearch_candidates.json", candidates)
            result["candidates"] = candidates
        if search_formulas:
            save_search_data(case_dir, "presearch_formulas.json", search_formulas)
            result["search_formulas"] = search_formulas

    elif stage == 2:
        classification, errors = parse_classification_response(raw_response)
        result["errors"] = errors
        if classification:
            save_search_data(case_dir, "classification.json", classification)
            result["classification"] = classification
            result["success"] = True

    else:  # stage == 3
        keyword_dictionary, errors = parse_keyword_response(raw_response)
        result["errors"] = errors
        if keyword_dictionary:
            save_search_data(case_dir, "keyword_dictionary.json", keyword_dictionary)
            result["keyword_dictionary"] = keyword_dictionary
            result["success"] = True
            # keywords.json にも変換
            groups = convert_keyword_dict_to_groups(keyword_dictionary, segs)
            with open(kw_path, "w", encoding="utf-8") as f:
                json.dump(groups, f, ensure_ascii=False, indent=2)

    return result, 200


# ------------------------------------------------------------------
# 14. Stage 1 ストリーミング実行
# ------------------------------------------------------------------

def stage_execute_stream(case_id, model=None):
    """Stage 1 をストリーミング実行し、進捗イベントをNDJSONで返すジェネレータを返す

    Returns:
        (generator, int): NDJSONイベントを yield するジェネレータ関数, status_code
        (dict, int): エラー時は通常のエラーdict
    """
    from modules.search_prompt_generator import (
        generate_presearch_prompt, parse_presearch_response,
    )
    from modules.claude_client import call_claude_stream

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    segments_path = Path(case_dir) / "segments.json"
    hongan_path = Path(case_dir) / "hongan.json"
    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400

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
    prompt_text = generate_presearch_prompt(segs, hongan, keywords, field, case_meta=meta)

    def generate():
        for evt in call_claude_stream(prompt_text, timeout=600, use_search=True, model=model):
            if evt["type"] == "done":
                full_response = evt["response"]
                # パース・保存
                tech_analysis, candidates, search_formulas, errors = parse_presearch_response(full_response)
                if tech_analysis:
                    save_search_data(case_dir, "tech_analysis.json", tech_analysis)
                if candidates:
                    save_search_data(case_dir, "presearch_candidates.json", candidates)
                if search_formulas:
                    save_search_data(case_dir, "presearch_formulas.json", search_formulas)

                result = {
                    "type": "result",
                    "success": bool(tech_analysis),
                    "errors": errors,
                }
                if tech_analysis:
                    result["tech_analysis"] = tech_analysis
                if candidates:
                    result["candidates"] = candidates
                if search_formulas:
                    result["search_formulas"] = search_formulas
                yield json.dumps(result, ensure_ascii=False) + "\n"
            elif evt["type"] == "error":
                yield json.dumps({"type": "error", "message": evt["message"]}, ensure_ascii=False) + "\n"
            else:
                # search, candidate, status イベントをそのまま転送
                yield json.dumps(evt, ensure_ascii=False) + "\n"

    return generate()
