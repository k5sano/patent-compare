#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Blueprint routes extracted from web.py."""
from __future__ import annotations

import json
import os
import re
import subprocess
import yaml
from pathlib import Path
from urllib.parse import urlencode
from flask import (
    Blueprint, current_app, render_template, request, redirect, url_for,
    flash, jsonify, send_file, Response
)

from services.case_service import (
    get_case_dir, load_case_meta, list_all_cases,
    load_json_file, find_citation_pdf,
)
from ._helpers import (
    PROJECT_ROOT, PDFXCHANGE_CANDIDATES, _launch_pdf_xchange,
    _open_with_pdf_xchange, _svc_response,
)

bp = Blueprint("downloads", __name__)

@bp.route("/case/<case_id>/download/<path:filename>")
def download_file(case_id, filename):
    case_dir = get_case_dir(case_id)
    file_path = case_dir / "output" / filename
    if not file_path.exists():
        flash("ファイルが見つかりません。", "error")
        return redirect(url_for("cases.case_detail", case_id=case_id))
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


def _resolve_annotated_hongan_pdf_path(case_id):
    """本願の注釈/ブックマーク済みPDFを解決。BU旧版は除外し最新を返す。"""
    case_dir = get_case_dir(case_id)
    output_dir = case_dir / "output"
    if not output_dir.exists():
        return None

    patterns = [
        f"{case_id}_本願_bookmarked.pdf",
        f"{case_id}_本願_bookmarked_*.pdf",
        f"{case_id}_本願_annotated.pdf",
        f"{case_id}_本願_annotated_*.pdf",
        "*本願*bookmarked*.pdf",
        "*本願*annotated*.pdf",
    ]
    candidates = []
    for pat in patterns:
        candidates.extend(output_dir.glob(pat))
    candidates = [
        p for p in {p for p in candidates}
        if p.is_file() and p.suffix.lower() == ".pdf" and "_BU" not in p.stem
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    return candidates[0]


def _resolve_workspace_hongan_pdf_path(case_id):
    return _resolve_annotated_hongan_pdf_path(case_id) or _resolve_hongan_pdf_path(case_id)


PDFXCHANGE_CANDIDATES = [
    r"C:\Program Files\Tracker Software\PDF Editor\PDFXEdit.exe",
    r"C:\Program Files (x86)\Tracker Software\PDF Editor\PDFXEdit.exe",
]


@bp.route("/case/<case_id>/hongan/pdf")
def view_hongan_pdf(case_id):
    """本願PDFをブラウザでインライン表示（注釈済みPDFがあれば優先）。"""
    pdf_path = _resolve_workspace_hongan_pdf_path(case_id)
    if pdf_path is None:
        return jsonify({"error": "本願PDFが見つかりません"}), 404
    return send_file(str(pdf_path), mimetype="application/pdf", as_attachment=False)


@bp.route("/case/<case_id>/hongan/annotated/status")
def hongan_annotated_status(case_id):
    pdf_path = _resolve_annotated_hongan_pdf_path(case_id)
    from modules.pdf_annotation_meta import evaluate_annotation_freshness
    freshness = evaluate_annotation_freshness(
        pdf_path,
        case_dir=get_case_dir(case_id),
        kind="hongan",
    )
    return jsonify({
        "exists": pdf_path is not None,
        "filename": pdf_path.name if pdf_path else "",
        "state": freshness["state"],
        "reasons": freshness["reasons"],
    })


def _safe_annotation_stem(value):
    return re.sub(r'[<>:"/\\|?*]', '_', str(value or "")).strip()


def _resolve_annotated_citation_pdf_path(case_id, citation_id):
    """引用文献の現行注釈PDFを解決。BU 旧版は通常表示から除外する。"""
    case_dir = get_case_dir(case_id)
    output_dir = case_dir / "output"
    if not output_dir.exists():
        return None

    meta = load_case_meta(case_id) or {}
    stems = {_safe_annotation_stem(citation_id)}
    for cit in meta.get("citations", []):
        if cit.get("id") == citation_id:
            stems.add(_safe_annotation_stem(cit.get("label")))
            stems.add(_safe_annotation_stem(cit.get("patent_number")))

    candidates = []
    for stem in {s for s in stems if s}:
        exact = output_dir / f"{stem}_annotated.pdf"
        if exact.is_file():
            candidates.append(exact)
        candidates.extend(
            p for p in output_dir.glob(f"{stem}_annotated_*.pdf")
            if "_BU" not in p.stem
        )
    candidates = [p for p in candidates if p.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    return candidates[0]


def _list_annotated_citation_backups(case_id, citation_id):
    case_dir = get_case_dir(case_id)
    output_dir = case_dir / "output"
    if not output_dir.exists():
        return []
    meta = load_case_meta(case_id) or {}
    stems = {_safe_annotation_stem(citation_id)}
    for cit in meta.get("citations", []):
        if cit.get("id") == citation_id:
            stems.add(_safe_annotation_stem(cit.get("label")))
            stems.add(_safe_annotation_stem(cit.get("patent_number")))
    files = []
    for stem in {s for s in stems if s}:
        files.extend(output_dir.glob(f"{stem}_annotated_*_BU*.pdf"))
    out = []
    for p in sorted({p for p in files if p.is_file()}, key=lambda p: (p.stat().st_mtime, p.name), reverse=True):
        out.append({
            "filename": p.name,
            "size": p.stat().st_size,
            "mtime": p.stat().st_mtime,
        })
    return out


@bp.route("/case/<case_id>/citation/<path:citation_id>/pdf")
def view_citation_pdf(case_id, citation_id):
    """引用文献PDFをブラウザでインライン表示（注釈PDFがあれば優先）。"""
    annotated_path = _resolve_annotated_citation_pdf_path(case_id, citation_id)
    if annotated_path is not None:
        return send_file(str(annotated_path), mimetype="application/pdf", as_attachment=False)

    case_dir = get_case_dir(case_id)
    input_dir = case_dir / "input"
    pdf_path = find_citation_pdf(input_dir, citation_id)
    if pdf_path is None:
        meta = load_case_meta(case_id) or {}
        for cit in meta.get("citations", []):
            if cit.get("id") == citation_id and cit.get("label") and cit["label"] != citation_id:
                pdf_path = find_citation_pdf(input_dir, cit["label"])
                if pdf_path is not None:
                    break
    if pdf_path is None:
        return jsonify({"error": f"引用文献PDFが見つかりません: {citation_id}"}), 404
    return send_file(str(pdf_path), mimetype="application/pdf", as_attachment=False)


@bp.route("/case/<case_id>/citation/<path:citation_id>/annotated/status")
def citation_annotated_status(case_id, citation_id):
    pdf_path = _resolve_annotated_citation_pdf_path(case_id, citation_id)
    resp_path = get_case_dir(case_id) / "responses" / f"{citation_id}.json"
    from modules.pdf_annotation_meta import evaluate_annotation_freshness
    freshness = evaluate_annotation_freshness(
        pdf_path,
        case_dir=get_case_dir(case_id),
        kind="citation",
        citation_id=citation_id,
    )
    return jsonify({
        "exists": pdf_path is not None,
        "filename": pdf_path.name if pdf_path else "",
        "can_create": resp_path.exists(),
        "state": freshness["state"],
        "reasons": freshness["reasons"],
    })


@bp.route("/case/<case_id>/citation/<path:citation_id>/annotated/backups")
def list_citation_annotated_backups(case_id, citation_id):
    return jsonify({"backups": _list_annotated_citation_backups(case_id, citation_id)})


@bp.route("/case/<case_id>/citation/<path:citation_id>/annotated/backup/<path:filename>")
def view_citation_annotated_backup(case_id, citation_id, filename):
    safe_name = Path(filename).name
    backups = _list_annotated_citation_backups(case_id, citation_id)
    if not any(b["filename"] == safe_name for b in backups):
        return jsonify({"error": "旧版注釈PDFが見つかりません"}), 404
    pdf_path = get_case_dir(case_id) / "output" / safe_name
    return send_file(str(pdf_path), mimetype="application/pdf", as_attachment=False)


@bp.route("/case/<case_id>/hongan/open", methods=["POST"])
def open_hongan_pdf(case_id):
    """本願PDFをPDF-XChange Editorで開く（注釈済みPDFがあれば優先）。"""
    pdf_path = _resolve_workspace_hongan_pdf_path(case_id)
    if pdf_path is None:
        return jsonify({"error": "本願PDFが見つかりません"}), 404
    return _launch_pdf_xchange(pdf_path)


def _launch_pdf_xchange(pdf_path):
    if _open_with_pdf_xchange(pdf_path):
        exe = next((p for p in PDFXCHANGE_CANDIDATES if Path(p).exists()), None)
        return jsonify({"success": True,
                        "opened_with": "PDF-XChange Editor" if exe else "OS default"})
    return jsonify({"error": "起動失敗"}), 500

