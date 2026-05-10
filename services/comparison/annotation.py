#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""引用文献PDF注釈。"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

from services.case_service import get_case_dir, load_case_meta, find_citation_pdf
from services.comparison.common import _annotate_worker, _write_annotated_pdf
def annotate_citation(case_id, citation_id, force_new_file=False):
    """引用文献PDFに注釈を追加。id (公開番号) で見つからない時は case.yaml の
    label (登録番号など別表記) もフォールバックとして探索する。

    force_new_file=True の場合は出力ファイル名にタイムスタンプを付けて必ず新規
    ファイルとして書き出す (PDF-XChange 等で古い注釈 PDF を開いたままになっても
    確実に新しいファイルが手に入るように)。"""
    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    resp_path = case_dir / "responses" / f"{citation_id}.json"
    if not resp_path.exists():
        return {"error": f"対比結果がありません: {citation_id}"}, 404

    with open(resp_path, "r", encoding="utf-8") as f:
        response_data = json.load(f)

    # citation JSON / PDF の解決には id だけでなく label も試す
    # (例: id=特開2021-20391 / label=JP7088138B2 / input/JP7088138B2.pdf)
    label = ""
    for cit in meta.get("citations", []):
        if cit.get("id") == citation_id:
            label = (cit.get("label") or "").strip()
            break

    cit_path = case_dir / "citations" / f"{citation_id}.json"
    if not cit_path.exists() and label and label != citation_id:
        alt = case_dir / "citations" / f"{label}.json"
        if alt.exists():
            cit_path = alt
    if not cit_path.exists():
        return {"error": f"引用文献データがありません: {citation_id}"}, 404

    with open(cit_path, "r", encoding="utf-8") as f:
        citation_data = json.load(f)

    pdf_path = find_citation_pdf(case_dir / "input", citation_id)
    if not pdf_path and label and label != citation_id:
        pdf_path = find_citation_pdf(case_dir / "input", label)
    if not pdf_path:
        return {"error": f"引用文献PDFが見つかりません: {citation_id}"}, 404

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    base_safe_name = re.sub(r'[<>:"/\\|?*]', '_', citation_id)
    migrate_from = case_dir / "output" / f"{base_safe_name}_annotated.pdf"
    safe_name = base_safe_name
    if force_new_file:
        # 強制再生成: タイムスタンプ付きで別名 (確実に新規ファイル)
        ts = datetime.now().strftime("%H%M%S")
        safe_name = f"{safe_name}_{ts}"

    try:
        result, actual_path = _write_annotated_pdf(
            pdf_path, case_dir / "output", safe_name,
            response_data, citation_data, keywords,
            migrate_bookmarks_from=migrate_from,
            case_id=case_id,
            citation_id=citation_id)
        return {
            "success": True,
            "filename": actual_path.name,
            "labels": result["labels"],
            "highlights": result["highlights"],
            "bookmarks": result["bookmarks"],
            "migrated_bookmarks": result.get("migrated_bookmarks", 0),
            "backup_filename": result.get("backup_filename"),
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
        jobs.append((case_id, cit_id, str(pdf_path), str(output_dir),
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


