#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""J-PlatPat PDF 取得を案件の引用文献登録につなぐサービス。"""

from __future__ import annotations

from services.case_service import get_case_dir, load_case_meta, upload_citation


def download_citation_pdf_from_jplatpat(case_id, patent_id, role="主引例"):
    """J-PlatPat から PDF を取得し、引用文献として登録する。"""
    from modules.jplatpat_pdf_downloader import download_jplatpat_pdf

    if not (patent_id or "").strip():
        return {"error": "patent_id を指定してください"}, 400

    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    case_dir = get_case_dir(case_id)
    (case_dir / "input").mkdir(parents=True, exist_ok=True)
    (case_dir / "citations").mkdir(parents=True, exist_ok=True)

    dl_result = download_jplatpat_pdf(patent_id, case_dir / "input", headless=False)
    if not dl_result.get("success"):
        return {
            "success": False,
            "error": dl_result.get("error", "J-PlatPat PDF 取得に失敗しました"),
        }, 400

    label = dl_result.get("doc_id") or patent_id
    up_result, status = upload_citation(case_id, dl_result["path"], role=role, label=label)
    if status >= 400:
        up_result["pdf_downloaded"] = True
        up_result["pdf_path"] = dl_result["path"]
        return up_result, status

    up_result.update({
        "pdf_downloaded": True,
        "pdf_path": dl_result["path"],
        "source": "jplatpat",
        "jplatpat_doc_number": dl_result.get("jplatpat_doc_number", ""),
        "downloaded_pages": dl_result.get("num_pages", 0),
    })
    return up_result, status
