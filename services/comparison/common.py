#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""対比分析・Excel出力・PDF注釈サービス"""

import os
import re
import json
import hashlib
import logging
import shutil
from pathlib import Path
from datetime import datetime

from services.case_service import (
    get_case_dir, load_case_meta, save_case_meta, find_citation_pdf,
)

logger = logging.getLogger(__name__)


def _backup_existing_annotated_pdf(target):
    """再注釈前の現行注釈PDFを日付+BU付きで退避する。"""
    from modules.pdf_annotation_meta import annotation_meta_path

    target = Path(target)
    if not target.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = target.with_name(f"{target.stem}_{ts}_BU{target.suffix}")
    i = 2
    while backup.exists():
        backup = target.with_name(f"{target.stem}_{ts}_BU{i}{target.suffix}")
        i += 1
    shutil.copy2(target, backup)
    meta = annotation_meta_path(target)
    if meta.exists():
        shutil.copy2(meta, annotation_meta_path(backup))
    return backup


def _safe_prompt_filename(label, suffix="_prompt.txt", max_stem_chars=80):
    """Windows-safe prompt filename with hash fallback for long citation lists."""
    raw = str(label or "prompt")
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw).strip(" ._")
    if not safe:
        safe = "prompt"
    digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:12]
    if len(safe) > max_stem_chars:
        safe = f"{safe[:max_stem_chars]}_{digest}"
    return f"{safe}{suffix}"


def _write_annotated_pdf(
    pdf_path,
    output_dir,
    safe_name,
    response_data,
    citation_data,
    keywords,
    migrate_bookmarks_from=None,
    case_id=None,
    citation_id=None,
):
    """注釈PDFを書き出す。出力先がロック中なら別名にフォールバック。

    Returns: (result_dict, actual_path)
    """
    from modules.pdf_annotator import annotate_citation_pdf
    from modules.pdf_annotation_meta import write_annotation_meta

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{safe_name}_annotated.pdf"
    migrate_from = Path(migrate_bookmarks_from) if migrate_bookmarks_from else target
    if not migrate_from.exists():
        migrate_from = None
    backup_path = None
    if target.exists():
        backup_path = _backup_existing_annotated_pdf(target)
    try:
        result = annotate_citation_pdf(
            pdf_path, target, response_data, citation_data, keywords,
            migrate_bookmarks_from=migrate_from)
        if case_id and citation_id:
            write_annotation_meta(
                target,
                case_id=case_id,
                kind="citation",
                case_dir=Path(output_dir).parent,
                citation_id=citation_id,
                source_pdf=str(pdf_path),
            )
        if backup_path:
            result["backup_filename"] = backup_path.name
        return result, target
    except Exception as e:
        msg = str(e).lower()
        if "permission denied" in msg or "cannot remove" in msg or "in use" in msg:
            ts = datetime.now().strftime("%H%M%S%f")
            alt = output_dir / f"{safe_name}_annotated_{ts}.pdf"
            result = annotate_citation_pdf(
                pdf_path, alt, response_data, citation_data, keywords,
                migrate_bookmarks_from=migrate_from)
            if case_id and citation_id:
                write_annotation_meta(
                    alt,
                    case_id=case_id,
                    kind="citation",
                    case_dir=Path(output_dir).parent,
                    citation_id=citation_id,
                    source_pdf=str(pdf_path),
                )
            result["alt_filename"] = True
            if backup_path:
                result["backup_filename"] = backup_path.name
            return result, alt
        raise


def _enrich_citation_with_hit_text(case_id, cit_id, citation):
    """citation を search_runs/_hit_text/<id>.json で補完して返す。

    Step 4.5 で「全文取得」した結果は search_runs/_hit_text に保存されるが、
    citations/<id>.json には反映されないため、Step 5 のプロンプトには行きづらい。
    本 helper で citation に hit_text の description/claims を取り込み、
    キーワード (グアニルシステイン 等) が citation 本文に明記されているのに
    対比結果で「未含有」と判定される silent miss を防ぐ。

    マージ規則:
      - paragraphs: 既存に含まれていない場合のみ末尾に「全文取得 (Step 4.5)」段落
        として追加 (重複検出は冒頭 200 文字の含有チェック)
      - claims: 既存が空のときのみ補完
      - その他フィールド (patent_number, role, label) はそのまま維持
    """
    if not citation:
        return citation
    try:
        from services.search_run_service import get_hit_text
    except ImportError:
        return citation
    hit = get_hit_text(case_id, cit_id)
    if not hit:
        return citation

    enriched = dict(citation)

    # description を 1 段落として補完
    desc = (hit.get("description") or "").strip()
    if desc:
        existing_paras = list(enriched.get("paragraphs") or [])
        existing_text = " ".join(
            (p.get("text") or "") for p in existing_paras
        )
        # 既存に description の冒頭 200 字が含まれていれば重複とみなす
        head = desc[:200]
        if not head or head not in existing_text:
            existing_paras.append({
                "id": "_hittext",
                "page": 0,
                "section": "全文取得 (Step 4.5)",
                "text": desc,
            })
            enriched["paragraphs"] = existing_paras

    # claims が citation 側で空のときのみ hit_text のもので補完
    if not (enriched.get("claims") or []):
        hit_claims = hit.get("claims") or []
        if hit_claims:
            normalized: list = []
            for i, c in enumerate(hit_claims, start=1):
                if isinstance(c, dict):
                    normalized.append({
                        "number": c.get("number") or i,
                        "text": c.get("text") or "",
                    })
                else:
                    normalized.append({"number": i, "text": str(c)})
            enriched["claims"] = normalized

    return enriched


def _load_citation_for_prompt(case_id, cit_id, case_dir):
    """citation を JSON から読み、hit_text で補完したオブジェクトを返す。

    通常 citations/<id>.json を読むだけだが、Step 4.5 の全文取得結果
    (search_runs/_hit_text/<id>.json) があれば本文を補完する。
    存在しない場合は (None, error_msg) を返す。
    """
    cit_path = case_dir / "citations" / f"{cit_id}.json"
    if not cit_path.exists():
        return None, f"引用文献 '{cit_id}' が見つかりません"
    with open(cit_path, "r", encoding="utf-8") as f:
        citation = json.load(f)
    citation = _enrich_citation_with_hit_text(case_id, cit_id, citation)
    return citation, None


def _annotate_worker(job):
    """プロセスプールのワーカー。ピックル可能にするためモジュールトップレベルに置く。

    job: (case_id, cit_id, pdf_path, output_dir, response_data, citation_data, keywords)
    """
    case_id, cit_id, pdf_path, output_dir, response_data, citation_data, keywords = job
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', cit_id)
    migrate_from = Path(output_dir) / f"{safe_name}_annotated.pdf"
    try:
        result, actual_path = _write_annotated_pdf(
            Path(pdf_path), Path(output_dir), safe_name,
            response_data, citation_data, keywords,
            migrate_bookmarks_from=migrate_from,
            case_id=case_id,
            citation_id=cit_id)
        return {"citation_id": cit_id, "success": True,
                "filename": actual_path.name, **result}
    except Exception as e:
        return {"citation_id": cit_id, "success": False, "error": str(e)}


