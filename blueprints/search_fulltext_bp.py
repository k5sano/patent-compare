#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Blueprint routes extracted from web.py."""
from __future__ import annotations

import json
import os
import re
import subprocess
import yaml
import base64
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

bp = Blueprint("search_fulltext", __name__)


def _local_pdf_figure_images(case_id, patent_id, *, max_images=20):
    """全文取得キャッシュに画像が無い場合、ローカルPDFの図表画像を右ペインへ出す。"""
    try:
        import fitz
    except Exception:
        return []
    try:
        pdf_path = find_citation_pdf(get_case_dir(case_id) / "input", patent_id)
        if not pdf_path:
            return []
        doc = fitz.open(str(pdf_path))
    except Exception:
        return []

    out = []
    try:
        for page_index, page in enumerate(doc, start=1):
            if len(out) >= max_images:
                break
            text = page.get_text("text") or ""
            labels = re.findall(r"【\s*[図表]\s*[0-9０-９A-Za-zＡ-Ｚａ-ｚ\-－ー]+\s*】", text)
            # 表紙ロゴ等は不要。図表ラベルがあるページだけ拾う。
            if not labels:
                continue
            for idx, im in enumerate(page.get_images(full=True)):
                if len(out) >= max_images:
                    break
                try:
                    info = doc.extract_image(im[0])
                    blob = info.get("image") or b""
                    ext = (info.get("ext") or "png").lower()
                    if not blob:
                        continue
                    label = labels[idx] if idx < len(labels) else f"P{page_index} 図表{idx + 1}"
                    mime = "image/png" if ext == "png" else f"image/{ext}"
                    src = f"data:{mime};base64,{base64.b64encode(blob).decode('ascii')}"
                    out.append({
                        "src": src,
                        "label": label,
                        "context": f"ローカルPDF P{page_index}",
                        "alt": f"{patent_id} {label}",
                        "source": "local_pdf",
                    })
                except Exception:
                    continue
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return out

@bp.route("/case/<case_id>/search-run/hit/<path:patent_id>/view")
def hit_full_text_view(case_id, patent_id):
    """ヒットの全文ハイライトビュー（新タブで開く想定）。

    キャッシュが無ければ自動取得する。Step 3 のキーワードグループの色で
    `<mark>` を重畳し、グループ別ヒット数を凡例として表示。
    """
    from services.search_run_service import (
        get_hit_text, _default_text_source,
        list_runs, load_run, pkm_build_index, pkm_highlight_python, pkm_group_color,
    )
    run_id = request.args.get("run_id") or ""
    nav_context = request.args.get("nav") or ""
    nav_ids = [x.strip() for x in request.args.getlist("nav_id") if x and x.strip()]
    nav = {"prev_url": "", "next_url": "", "position": ""}
    hit_card = None

    def _hit_view_url(pid, *, rid=None, ids=None, context=""):
        values = {
            "case_id": case_id,
            "patent_id": pid,
        }
        if rid:
            values["run_id"] = rid
        if context:
            values["nav"] = context
        url = url_for("search_fulltext.hit_full_text_view", **values)
        if ids:
            prefix = "&" if "?" in url else "?"
            extra = urlencode([("nav_id", str(nav_id)) for nav_id in ids])
            url = f"{url}{prefix}{extra}"
        return url

    def _set_nav_from_ids(hit_ids, context):
        hit_ids = [str(x).strip() for x in (hit_ids or []) if str(x or "").strip()]
        if patent_id not in hit_ids:
            return False
        pos = hit_ids.index(patent_id)
        nav["position"] = f"{pos + 1} / {len(hit_ids)}"
        nav["prev_url"] = ""
        nav["next_url"] = ""
        if pos > 0:
            nav["prev_url"] = _hit_view_url(hit_ids[pos - 1], ids=hit_ids, context=context)
        if pos + 1 < len(hit_ids):
            nav["next_url"] = _hit_view_url(hit_ids[pos + 1], ids=hit_ids, context=context)
        return True

    def _set_nav_from_run(run_data, rid):
        nonlocal hit_card
        hit_ids = [
            h.get("patent_id") for h in (run_data.get("hits") or [])
            if h.get("patent_id")
        ]
        if patent_id not in hit_ids:
            return False
        pos = hit_ids.index(patent_id)
        current = (run_data.get("hits") or [])[pos] or {}
        hit_card = {
            "run_id": rid,
            "run_label": run_data.get("formula_level") or "",
            "run_source": run_data.get("source") or "",
            "patent_id": current.get("patent_id") or patent_id,
            "title": current.get("title") or "",
            "applicant": current.get("applicant") or "",
            "publication_date": current.get("publication_date") or "",
            "ipc": current.get("ipc") or [],
            "fi": current.get("fi") or [],
            "ai_score": current.get("ai_score"),
            "ai_reason": current.get("ai_reason") or "",
            "screening": current.get("screening") or "pending",
            "downloaded_as_citation": bool(current.get("downloaded_as_citation")),
        }
        nav["position"] = f"{pos + 1} / {len(hit_ids)}"
        nav["prev_url"] = ""
        nav["next_url"] = ""
        if pos > 0:
            nav["prev_url"] = _hit_view_url(hit_ids[pos - 1], rid=rid)
        if pos + 1 < len(hit_ids):
            nav["next_url"] = _hit_view_url(hit_ids[pos + 1], rid=rid)
        return True

    try:
        if nav_ids:
            _set_nav_from_ids(nav_ids, nav_context or "workspace")
        if not nav["position"] and nav_context in ("workspace", "workspace_citations"):
            meta = load_case_meta(case_id) or {}
            citation_ids = [
                c.get("id") for c in (meta.get("citations") or [])
                if c.get("id")
            ]
            _set_nav_from_ids(citation_ids, nav_context)
        if not nav["position"] and run_id:
            _set_nav_from_run(load_run(case_id, run_id) or {}, run_id)
        if not nav["position"]:
            # 古いタブや直接URLで run_id が無い場合でも、最新の検索runから前後移動を復元する。
            for r in list_runs(case_id):
                rid = r.get("run_id")
                if rid and _set_nav_from_run(load_run(case_id, rid) or {}, rid):
                    break
    except Exception:
        nav = {"prev_url": "", "next_url": "", "position": ""}

    hit = get_hit_text(case_id, patent_id)
    if not hit:
        # キャッシュ無し: 即座に取得中ローダーページを返し、ブラウザ側で fetch → reload
        src = _default_text_source(patent_id)
        src_label = {"jplatpat": "J-PlatPat", "google": "Google Patents"}.get(src, src)
        return render_template(
            "hit_view_loading.html",
            patent_id=patent_id,
            case_id=case_id,
            source=src,
            source_label=src_label,
        )
    if "error" in hit and not hit.get("description") and not hit.get("claims"):
        return render_template(
            "hit_view.html",
            case_id=case_id,
            patent_id=patent_id,
            title="（取得失敗）",
            source="google",
            source_label="エラー",
            source_url="",
            google_url=f"https://patents.google.com/?q={patent_id}",
            jplatpat_url="",
            groups=[],
            abstract_unit={"html": f'<div class="empty">エラー: {hit.get("error","")}</div>', "groups": []},
            claims_units=[],
            para_units=[],
            images=[],
            standalone_tables=[],
            nav=nav,
            hit_card=hit_card,
            total_hits=0,
            total_chars=0,
        )

    keywords = load_json_file(case_id, "keywords.json") or []
    index = pkm_build_index(keywords)

    counts_total = {}

    def _accum(c):
        for k, v in (c or {}).items():
            counts_total[k] = counts_total.get(k, 0) + v

    # Highlight 要約 (1 unit)
    abstract_h = pkm_highlight_python(hit.get("abstract") or "", index)
    _accum(abstract_h["counts"])
    abstract_unit = {
        "html": abstract_h["html"],
        "groups": sorted(int(g) for g in abstract_h["counts"].keys() if g is not None),
    } if abstract_h["html"] else None

    # Highlight 請求項 (各 claim を unit)
    claims_units = []
    for cl in (hit.get("claims") or []):
        ch = pkm_highlight_python(cl, index)
        _accum(ch["counts"])
        claims_units.append({
            "html": ch["html"],
            "groups": sorted(int(g) for g in ch["counts"].keys() if g is not None),
        })

    # Highlight 明細書本文 — 段落マーカー【XXXX】単位で分割
    fw2hw = str.maketrans("０１２３４５６７８９", "0123456789")
    desc = hit.get("description") or ""
    para_units = []
    if desc:
        # 全角・半角どちらでも【\d+】を捕捉
        parts = re.split(r'(【\s*[\d０-９]+\s*】)', desc)
        # parts[0] は最初のマーカー前のリード（例「発明の詳細な説明】【技術分野】」など）。
        if parts and parts[0].strip():
            head = pkm_highlight_python(parts[0], index)
            _accum(head["counts"])
            para_units.append({
                "pid": "",
                "marker": "",
                "html": head["html"],
                "groups": sorted(int(g) for g in head["counts"].keys() if g is not None),
            })
        for i in range(1, len(parts) - 1, 2):
            marker = (parts[i] or "").strip()
            body = parts[i + 1] if (i + 1) < len(parts) else ""
            m_pid = re.search(r'(\d+)', marker.translate(fw2hw))
            pid = m_pid.group(1).zfill(4) if m_pid else ""
            ph = pkm_highlight_python(body, index)
            _accum(ph["counts"])
            para_units.append({
                "pid": pid,
                "marker": marker,
                "html": ph["html"],
                "groups": sorted(int(g) for g in ph["counts"].keys() if g is not None),
            })

    groups_view = []
    for g in keywords:
        gid = g.get("group_id")
        groups_view.append({
            "gid": gid,
            "label": g.get("label") or f"group{gid}",
            "color": pkm_group_color(gid),
            "count": counts_total.get(gid, 0),
        })
    groups_view.sort(key=lambda x: -x["count"])

    src = (hit.get("source") or "google").lower()
    src_label = {
        "jplatpat": "J-PlatPat",
        "google": "Google Patents",
        "google_fallback": "Google Patents",  # 取得元としては Google。経緯は表示しない
    }.get(src, src)

    # Build cross-source URLs
    from modules.jplatpat_client import build_jplatpat_fixed_url
    jpp_url = build_jplatpat_fixed_url(patent_id)
    gp_url = f"https://patents.google.com/?q={patent_id}"
    src_url = hit.get("url") or (jpp_url if src == "jplatpat" else gp_url)

    total_chars = (
        len(hit.get("abstract") or "")
        + len(hit.get("description") or "")
        + sum(len(c or "") for c in (hit.get("claims") or []))
    )

    images = hit.get("images") or []
    if not images:
        images = _local_pdf_figure_images(case_id, patent_id)

    # 抽出済みの表データを image src で対応付ける (image_records ベース抽出時のみ)
    extracted_tables_by_src: dict = {}
    extracted_tables_by_label: dict = {}
    extracted_tables_all: list = []
    try:
        from services.case_service import get_citation_tables
        ct_res, ct_code = get_citation_tables(case_id, patent_id)
        if ct_code == 200 and ct_res.get("exists"):
            for t in (ct_res.get("data", {}).get("tables") or []):
                if not t.get("is_table"):
                    continue
                extracted_tables_all.append(t)
                src_url = t.get("src")
                if src_url:
                    extracted_tables_by_src[src_url] = t
                # キャプションラベル(【表1】等)でも引けるようにフォールバック
                lbl = t.get("caption_label") or t.get("title")
                if lbl:
                    extracted_tables_by_label[lbl] = t
    except Exception:
        pass

    def _build_table_display(headers_h, rows_h):
        """右ペインで読みやすい表示形に整える。横に広い表は列単位の縦表示へ変換。"""
        headers_h = list(headers_h or [])
        rows_h = [list(r or []) for r in (rows_h or [])]
        ncols = max([len(headers_h)] + [len(r) for r in rows_h] + [0])
        if ncols < 7:
            return {
                "mode": "table",
                "headers_html": headers_h,
                "rows_html": rows_h,
                "vertical_blocks": [],
            }

        blocks = []
        for ci in range(1, ncols):
            title = headers_h[ci] if ci < len(headers_h) and headers_h[ci] else f"列{ci + 1}"
            pairs = []
            for ri, row in enumerate(rows_h):
                label = row[0] if row and row[0] else f"行{ri + 1}"
                value = row[ci] if ci < len(row) else ""
                if label or value:
                    pairs.append({"label": label, "value": value})
            if pairs:
                blocks.append({"title": title, "pairs": pairs})

        return {
            "mode": "vertical",
            "headers_html": headers_h,
            "rows_html": rows_h,
            "vertical_blocks": blocks,
        }

    # images に表抽出結果を埋め込む (テンプレート側で参照)。
    # 各セルにも PKM ハイライトを適用してハイライト数を全体カウントに合算。
    matched_table_ids: set[int] = set()
    for im in images:
        src_url = im.get("src")
        match = extracted_tables_by_src.get(src_url) if src_url else None
        if not match:
            # ラベル一致もチェック (PDF 由来の場合 src は無いがラベルで対応)
            lbl = im.get("label")
            if lbl:
                match = extracted_tables_by_label.get(lbl)
        if not match:
            continue
        matched_table_ids.add(id(match))
        headers = match.get("headers") or []
        rows = match.get("rows") or []
        # ヘッダ・各セルにハイライト適用
        headers_h = []
        for h in headers:
            hh = pkm_highlight_python(str(h), index)
            _accum(hh["counts"])
            headers_h.append(hh["html"])
        rows_h = []
        unit_groups: set = set()
        for row in rows:
            cells = row.get("cells") or []
            cells_h = []
            for c in cells:
                ch = pkm_highlight_python(str(c), index)
                _accum(ch["counts"])
                for g in ch["counts"].keys():
                    if g is not None:
                        unit_groups.add(int(g))
                cells_h.append(ch["html"])
            rows_h.append(cells_h)
        display = _build_table_display(headers_h, rows_h)
        im["extracted"] = {
            "title": match.get("title") or im.get("label"),
            "n_rows": len(rows),
            "groups": sorted(unit_groups),
            **display,
        }

    standalone_tables = []
    for t in extracted_tables_all:
        if id(t) in matched_table_ids:
            continue
        headers = t.get("headers") or []
        rows = t.get("rows") or []
        headers_h = []
        unit_groups: set = set()
        for h in headers:
            hh = pkm_highlight_python(str(h), index)
            _accum(hh["counts"])
            for g in hh["counts"].keys():
                if g is not None:
                    unit_groups.add(int(g))
            headers_h.append(hh["html"])
        rows_h = []
        for row in rows:
            cells = row.get("cells") or []
            cells_h = []
            for c in cells:
                ch = pkm_highlight_python(str(c), index)
                _accum(ch["counts"])
                for g in ch["counts"].keys():
                    if g is not None:
                        unit_groups.add(int(g))
                cells_h.append(ch["html"])
            rows_h.append(cells_h)
        display = _build_table_display(headers_h, rows_h)
        standalone_tables.append({
            "title": t.get("title") or t.get("caption_label") or "抽出表",
            "n_rows": len(rows),
            "groups": sorted(unit_groups),
            **display,
        })

    return render_template(
        "hit_view.html",
        case_id=case_id,
        patent_id=patent_id,
        title=hit.get("title") or "",
        source=src,
        source_label=src_label,
        source_url=src_url,
        google_url=gp_url,
        jplatpat_url=jpp_url,
        groups=groups_view,
        abstract_unit=abstract_unit,
        claims_units=claims_units,
        para_units=para_units,
        images=images,
        standalone_tables=standalone_tables,
        nav=nav,
        hit_card=hit_card,
        total_hits=sum(counts_total.values()),
        total_chars=total_chars,
    )


