#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""対比分析・Excel出力・PDF注釈サービス"""

import os
import re
import json
from pathlib import Path
from datetime import datetime

from services.case_service import (
    get_case_dir, load_case_meta, save_case_meta, find_citation_pdf,
)


def _write_annotated_pdf(pdf_path, output_dir, safe_name, response_data, citation_data, keywords):
    """注釈PDFを書き出す。出力先がロック中なら別名にフォールバック。

    Returns: (result_dict, actual_path)
    """
    from modules.pdf_annotator import annotate_citation_pdf

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{safe_name}_annotated.pdf"
    try:
        result = annotate_citation_pdf(
            pdf_path, target, response_data, citation_data, keywords)
        return result, target
    except Exception as e:
        msg = str(e).lower()
        if "permission denied" in msg or "cannot remove" in msg or "in use" in msg:
            ts = datetime.now().strftime("%H%M%S%f")
            alt = output_dir / f"{safe_name}_annotated_{ts}.pdf"
            result = annotate_citation_pdf(
                pdf_path, alt, response_data, citation_data, keywords)
            result["alt_filename"] = True
            return result, alt
        raise


def _annotate_worker(job):
    """プロセスプールのワーカー。ピックル可能にするためモジュールトップレベルに置く。

    job: (cit_id, pdf_path, output_dir, response_data, citation_data, keywords)
    """
    cit_id, pdf_path, output_dir, response_data, citation_data, keywords = job
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', cit_id)
    try:
        result, actual_path = _write_annotated_pdf(
            Path(pdf_path), Path(output_dir), safe_name,
            response_data, citation_data, keywords)
        return {"citation_id": cit_id, "success": True,
                "filename": actual_path.name, **result}
    except Exception as e:
        return {"citation_id": cit_id, "success": False, "error": str(e)}


def generate_prompt_multi(case_id, citation_ids):
    """複数文献対応のプロンプト生成"""
    from modules.prompt_generator import generate_prompt as _gen

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    segments_path = case_dir / "segments.json"

    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    if not citation_ids:
        return {"error": "対象文献を選択してください"}, 400

    citations = []
    for cit_id in citation_ids:
        cit_path = case_dir / "citations" / f"{cit_id}.json"
        if not cit_path.exists():
            return {"error": f"引用文献 '{cit_id}' が見つかりません"}, 404
        with open(cit_path, "r", encoding="utf-8") as f:
            citations.append(json.load(f))

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    field = meta.get("field", "cosmetics")
    prompt_text = _gen(segs, citations, keywords, field)

    ids_label = "_".join(citation_ids)
    prompt_path = case_dir / "prompts" / f"{ids_label}_prompt.txt"
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt_text)

    return {
        "prompt": prompt_text,
        "char_count": len(prompt_text),
        "num_citations": len(citations),
    }, 200


def generate_prompt_single(case_id, citation_id):
    """単一文献のプロンプト生成"""
    from modules.prompt_generator import generate_prompt as _gen

    case_dir = get_case_dir(case_id)
    segments_path = case_dir / "segments.json"
    citation_path = case_dir / "citations" / f"{citation_id}.json"

    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400
    if not citation_path.exists():
        return {"error": f"引用文献 '{citation_id}' が見つかりません"}, 404

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

    return {"prompt": prompt_text, "char_count": len(prompt_text)}, 200


def _get_all_segment_ids(segs):
    """分節データから全分節IDを取得"""
    ids = []
    for claim in segs:
        for seg in claim["segments"]:
            ids.append(seg["id"])
    return ids


def save_response_multi(case_id, raw_text):
    """複数文献対応の回答パース・保存"""
    from modules.response_parser import parse_response, split_multi_response

    case_dir = get_case_dir(case_id)
    segments_path = case_dir / "segments.json"

    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    if not raw_text.strip():
        return {"error": "テキストが空です"}, 400

    result, errors = parse_response(raw_text, _get_all_segment_ids(segs))

    saved_docs = []
    if result:
        per_doc = split_multi_response(result)
        for doc_id, doc_result in per_doc.items():
            resp_path = case_dir / "responses" / f"{doc_id}.json"
            with open(resp_path, "w", encoding="utf-8") as f:
                json.dump(doc_result, f, ensure_ascii=False, indent=2)
            saved_docs.append(doc_id)

    return {
        "success": result is not None,
        "errors": errors,
        "saved_docs": saved_docs,
        "num_docs": len(saved_docs),
    }, 200


def save_response_single(case_id, citation_id, raw_text):
    """単一文献の回答パース"""
    from modules.response_parser import parse_response

    case_dir = get_case_dir(case_id)
    segments_path = case_dir / "segments.json"

    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    if not raw_text.strip():
        return {"error": "テキストが空です"}, 400

    result, errors = parse_response(raw_text, _get_all_segment_ids(segs))

    if result:
        resp_path = case_dir / "responses" / f"{citation_id}.json"
        with open(resp_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    return {
        "success": result is not None,
        "errors": errors,
        "data": result,
    }, 200


def get_response(case_id, citation_id):
    case_dir = get_case_dir(case_id)
    resp_path = case_dir / "responses" / f"{citation_id}.json"
    if not resp_path.exists():
        return {"error": "回答データがありません"}, 404
    with open(resp_path, "r", encoding="utf-8") as f:
        return json.load(f), 200


def export_excel(case_id):
    from modules.excel_writer import write_comparison_table

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    segments_path = case_dir / "segments.json"

    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    responses = {}
    responses_dir = case_dir / "responses"
    if responses_dir.exists():
        for rfile in responses_dir.glob("*.json"):
            with open(rfile, "r", encoding="utf-8") as f:
                responses[rfile.stem] = json.load(f)

    if not responses:
        return {"error": "回答データがありません"}, 400

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

    return {
        "success": True,
        "filename": output_path.name,
        "path": str(output_path),
    }, 200


def annotate_citation(case_id, citation_id):
    """引用文献PDFに注釈を追加"""
    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    resp_path = case_dir / "responses" / f"{citation_id}.json"
    if not resp_path.exists():
        return {"error": f"対比結果がありません: {citation_id}"}, 404

    with open(resp_path, "r", encoding="utf-8") as f:
        response_data = json.load(f)

    cit_path = case_dir / "citations" / f"{citation_id}.json"
    if not cit_path.exists():
        return {"error": f"引用文献データがありません: {citation_id}"}, 404

    with open(cit_path, "r", encoding="utf-8") as f:
        citation_data = json.load(f)

    pdf_path = find_citation_pdf(case_dir / "input", citation_id)
    if not pdf_path:
        return {"error": f"引用文献PDFが見つかりません: {citation_id}"}, 404

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    safe_name = re.sub(r'[<>:"/\\|?*]', '_', citation_id)

    try:
        result, actual_path = _write_annotated_pdf(
            pdf_path, case_dir / "output", safe_name,
            response_data, citation_data, keywords)
        return {
            "success": True,
            "filename": actual_path.name,
            "labels": result["labels"],
            "highlights": result["highlights"],
            "bookmarks": result["bookmarks"],
            "alt_filename": result.get("alt_filename", False),
        }, 200
    except Exception as e:
        return {"error": f"注釈生成エラー: {str(e)}"}, 500


def annotate_all_citations(case_id, max_workers=None):
    """全引用文献の注釈PDFを並列生成。

    max_workers=None の場合は CPU 論理コア数（最大でジョブ数まで）を使用。
    Ryzen 9 等の多コアCPUで実質フル稼働。GIL回避のため ProcessPool を使用。
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    output_dir = case_dir / "output"
    jobs = []
    pre_results = []
    for cit in meta.get("citations", []):
        cit_id = cit["id"]
        resp_path = case_dir / "responses" / f"{cit_id}.json"
        cit_path = case_dir / "citations" / f"{cit_id}.json"
        pdf_path = find_citation_pdf(case_dir / "input", cit_id)

        if not resp_path.exists() or not cit_path.exists() or not pdf_path:
            missing = []
            if not resp_path.exists():
                missing.append("回答")
            if not cit_path.exists():
                missing.append("引用文献データ")
            if not pdf_path:
                missing.append("元PDF")
            from modules.patent_downloader import build_jplatpat_url
            pre_results.append({
                "citation_id": cit_id, "success": False,
                "error": f"{'/'.join(missing)}がありません",
                "jplatpat_url": build_jplatpat_url(cit_id),
            })
            continue

        with open(resp_path, "r", encoding="utf-8") as f:
            response_data = json.load(f)
        with open(cit_path, "r", encoding="utf-8") as f:
            citation_data = json.load(f)
        # ProcessPool にピックルして渡すため Path は str 化
        jobs.append((cit_id, str(pdf_path), str(output_dir),
                     response_data, citation_data, keywords))

    results = list(pre_results)
    if jobs:
        workers = max_workers or (os.cpu_count() or 4)
        workers = max(1, min(workers, len(jobs)))
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_annotate_worker, j) for j in jobs]
            for fut in as_completed(futures):
                results.append(fut.result())

    success_count = sum(1 for r in results if r["success"])
    return {"results": results, "success_count": success_count,
            "workers_used": workers if jobs else 0}, 200


def compare_execute(case_id, citation_ids):
    """直接実行: 対比プロンプト → Claude CLI → パース"""
    from modules.prompt_generator import generate_prompt as _gen
    from modules.response_parser import parse_response, split_multi_response
    from modules.claude_client import call_claude, ClaudeClientError

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    segments_path = case_dir / "segments.json"

    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    if not citation_ids:
        return {"error": "対象文献を選択してください"}, 400

    citations = []
    for cit_id in citation_ids:
        cit_path = case_dir / "citations" / f"{cit_id}.json"
        if not cit_path.exists():
            return {"error": f"引用文献 '{cit_id}' が見つかりません"}, 404
        with open(cit_path, "r", encoding="utf-8") as f:
            citations.append(json.load(f))

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    field = meta.get("field", "cosmetics")
    prompt_text = _gen(segs, citations, keywords, field)

    ids_label = "_".join(citation_ids)
    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    with open(prompts_dir / f"{ids_label}_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt_text)

    timeout = 600 if len(citations) <= 2 else 900
    try:
        raw_response = call_claude(prompt_text, timeout=timeout)
    except ClaudeClientError as e:
        return {"error": str(e), "phase": "claude_call"}, 502

    all_segment_ids = _get_all_segment_ids(segs)

    raw_path = case_dir / "responses" / "_last_raw_response.txt"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(raw_response)

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

    return {
        "success": result is not None,
        "errors": errors,
        "saved_docs": saved_docs,
        "num_docs": len(saved_docs),
        "char_count": len(prompt_text),
        "response_length": len(raw_response),
    }, 200


def inventive_step_prompt(case_id):
    """進歩性判断プロンプトを生成"""
    from modules.inventive_step_analyzer import generate_inventive_step_prompt

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    segments_path = case_dir / "segments.json"
    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    responses = {}
    responses_dir = case_dir / "responses"
    if responses_dir.exists():
        for rfile in responses_dir.glob("*.json"):
            with open(rfile, "r", encoding="utf-8") as f:
                responses[rfile.stem] = json.load(f)

    if not responses:
        return {"error": "対比結果がありません。Step 5を完了してください。"}, 400

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

    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    with open(prompts_dir / "inventive_step_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt_text)

    return {"prompt": prompt_text, "char_count": len(prompt_text)}, 200


def inventive_step_response(case_id, raw_text):
    """進歩性判断の回答をパース"""
    from modules.inventive_step_analyzer import parse_inventive_step_response

    case_dir = get_case_dir(case_id)
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404

    if not raw_text.strip():
        return {"error": "テキストが空です"}, 400

    data, errors = parse_inventive_step_response(raw_text)

    if data:
        with open(case_dir / "inventive_step.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    return {"success": data is not None, "data": data, "errors": errors}, 200


def inventive_step_execute(case_id):
    """直接実行: 進歩性判断プロンプト → Claude CLI → パース"""
    from modules.inventive_step_analyzer import (
        generate_inventive_step_prompt, parse_inventive_step_response
    )
    from modules.claude_client import call_claude, ClaudeClientError

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    segments_path = case_dir / "segments.json"
    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    responses = {}
    responses_dir = case_dir / "responses"
    if responses_dir.exists():
        for rfile in responses_dir.glob("*.json"):
            with open(rfile, "r", encoding="utf-8") as f:
                responses[rfile.stem] = json.load(f)

    if not responses:
        return {"error": "対比結果がありません。Step 5を完了してください。"}, 400

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

    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    with open(prompts_dir / "inventive_step_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt_text)

    try:
        raw_response = call_claude(prompt_text, timeout=600)
    except ClaudeClientError as e:
        return {"error": str(e), "phase": "claude_call"}, 502

    data, errors = parse_inventive_step_response(raw_response)

    if data:
        with open(case_dir / "inventive_step.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    return {
        "success": data is not None,
        "data": data,
        "errors": errors,
        "char_count": len(prompt_text),
        "response_length": len(raw_response),
    }, 200
