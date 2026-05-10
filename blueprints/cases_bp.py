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

bp = Blueprint("cases", __name__)

@bp.route("/")
def index():
    cases = list_all_cases()
    return render_template("index.html", cases=cases)


@bp.route("/case/new", methods=["POST"])
def new_case():
    from services.case_service import create_case

    data = request.get_json() or {}
    case_number = (data.get("case_number") or "").strip()
    if not case_number:
        return jsonify({"error": "案件番号を入力してください"}), 400

    result = create_case(
        case_number,
        year=data.get("year", ""),
        month=data.get("month", ""),
        field=data.get("field", "cosmetics"),
        application_number=(data.get("application_number") or "").strip(),
    )
    if "error" in result and "case_id" in result and result.get("error") and not result.get("success"):
        return jsonify(result), 409
    return jsonify(result)


@bp.route("/case/parse-batch", methods=["POST"])
def parse_batch_input():
    """複数行の特許番号入力をパースしてプレビュー用リストを返す。"""
    from services.case_service import _parse_patent_input, get_case_dir
    data = request.get_json() or {}
    text = data.get("text", "") or ""
    entries = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        p = _parse_patent_input(s)
        exists = p.get("case_id") and get_case_dir(p["case_id"]).exists()
        entries.append({
            "original": s,
            "kind": p["kind"],
            "case_id": p["case_id"],
            "patent_number": p["patent_number"],
            "application_number": p["application_number"],
            "exists": bool(exists),
        })
    return jsonify({"entries": entries})


@bp.route("/case/<case_id>")
def case_detail(case_id):
    meta = load_case_meta(case_id)
    if not meta:
        flash("案件が見つかりません。", "error")
        return redirect(url_for("cases.index"))

    # fallback 検索でフォルダ名が解決された場合、正規 URL にリダイレクト
    canonical_id = meta.get("case_id", case_id)
    if canonical_id != case_id:
        return redirect(url_for("cases.case_detail", case_id=canonical_id))

    case_dir = get_case_dir(case_id)

    hongan = load_json_file(case_id, "hongan.json")
    segments = load_json_file(case_id, "segments.json")
    keywords = load_json_file(case_id, "keywords.json")
    inventive_step = load_json_file(case_id, "inventive_step.json")  # 進歩性判断結果 (リロード時に復元)
    if keywords:
        # F-term の desc が空なら辞書から補完して表示する (in-place)
        from services.keyword_service import enrich_fterm_groups
        field = (meta or {}).get("field", "cosmetics")
        enrich_fterm_groups(keywords, field)
    related_paragraphs = load_json_file(case_id, "related_paragraphs.json") or {}

    citations = []
    for cit in meta.get("citations", []):
        cit_path = case_dir / "citations" / f"{cit['id']}.json"
        resp_path = case_dir / "responses" / f"{cit['id']}.json"
        cit["_has_data"] = cit_path.exists()
        cit["_has_response"] = resp_path.exists()
        # PDF 検索は id (公開番号) で先に試し、見つからなければ label (登録番号など
        # 別表記) でも試す。case.yaml に id=特開2021-20391 / label=JP7088138B2 と
        # 入っていて input に JP7088138B2.pdf がある場合などに必要。
        input_dir = case_dir / "input"
        pdf_p = find_citation_pdf(input_dir, cit['id'])
        if not pdf_p and cit.get("label") and cit["label"] != cit["id"]:
            pdf_p = find_citation_pdf(input_dir, cit["label"])
        cit["_has_pdf"] = bool(pdf_p)
        cit["_category"] = ""
        if resp_path.exists():
            try:
                import json as _json
                with open(resp_path, "r", encoding="utf-8") as f:
                    rdata = _json.loads(f.read().replace('\x00', ''), strict=False)
                cit["_category"] = (rdata.get("category_suggestion", "") or "").upper()[:1]
            except Exception:
                pass
        citations.append(cit)

    excel_files = list((case_dir / "output").glob("*.xlsx")) if (case_dir / "output").exists() else []

    # 予備調査タブ用 (Step 2 サブタブ): 利用可能分野リストとデフォルト分野
    from services.preliminary_research_service import (
        list_available_fields as _prelim_fields,
    )
    prelim_fields = _prelim_fields() or ["generic"]
    prelim_default_field = (
        meta.get("field") if meta.get("field") in prelim_fields else None
    ) or _load_prelim_default() or (
        "cosmetics" if "cosmetics" in prelim_fields else prelim_fields[0]
    )

    # 分節↔対比 整合性 (Step 5/6 のバナー + Step 2 保存時の警告に使う)
    from services.comparison_service import check_segments_freshness
    freshness, _ = check_segments_freshness(case_id)
    if not isinstance(freshness, dict):
        freshness = {}

    compact_ref = request.args.get("compact") == "1"
    try:
        compact_panel = int(request.args.get("panel", "-1"))
    except ValueError:
        compact_panel = -1
    try:
        compact_sub = int(request.args.get("sub", "0"))
    except ValueError:
        compact_sub = 0

    return render_template("case.html",
                           meta=meta, hongan=hongan, segments=segments,
                           keywords=keywords, citations=citations,
                           related_paragraphs=related_paragraphs,
                           excel_files=excel_files, case_id=case_id,
                           prelim_fields=prelim_fields,
                           prelim_default_field=prelim_default_field,
                           freshness=freshness,
                           inventive_step=inventive_step,
                           compact_ref=compact_ref,
                           compact_panel=compact_panel,
                           compact_sub=compact_sub)


def _load_prelim_default():
    """config.yaml の preliminary_research.default_field を読む (キャッシュなし、軽量なので毎回 OK)"""
    try:
        import yaml as _yaml
        cfg_path = PROJECT_ROOT / "config.yaml"
        if not cfg_path.exists():
            return None
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = _yaml.safe_load(f) or {}
        return (cfg.get("preliminary_research") or {}).get("default_field")
    except Exception:
        return None


@bp.route("/case/<case_id>/meta", methods=["POST"])
def update_case_meta_route(case_id):
    from services.case_service import update_case_meta
    return _svc_response(update_case_meta(case_id, request.get_json() or {}))


@bp.route("/case/<case_id>/delete", methods=["POST"])
def delete_case(case_id):
    from services.case_service import delete_case as _delete
    _delete(case_id)
    flash(f"案件 '{case_id}' を削除しました。", "success")
    return redirect(url_for("cases.index"))


# ===== PDF アップロー���・引用文献管理 =====

