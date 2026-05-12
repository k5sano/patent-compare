#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""対比表のExcel/完成版レポート出力。"""
from __future__ import annotations

import json

from services.case_service import get_case_dir, load_case_meta


def _load_keywords(case_dir):
    kw_path = case_dir / "keywords.json"
    if not kw_path.exists():
        return None
    try:
        with kw_path.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def export_full_report(case_id, selected_citation_ids=None):
    """完成版対比表 (本願解析 / 対比表 / 進歩性判断 の 3 タブ統合) を生成。

    selected_citation_ids: None または空ならすべての回答済文献を対象、
        指定があればその ID のみ (export_excel と同じ挙動)。
    """
    from modules.excel_writer import write_full_report

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    segs_path = case_dir / "segments.json"
    if not segs_path.exists():
        return {"error": "分節データがありません"}, 400
    with open(segs_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    # 全 response (回答済) を集める
    all_responses = {}
    resp_dir = case_dir / "responses"
    if resp_dir.exists():
        for p in resp_dir.glob("*.json"):
            if p.name.startswith("_"):
                continue
            try:
                with p.open(encoding="utf-8") as f:
                    all_responses[p.stem] = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
    if not all_responses:
        return {"error": "対比結果がありません。Step 5 で対比を実行してください"}, 400

    # 選択フィルタ適用 (export_excel と同じロジック)
    if selected_citation_ids:
        sel_set = set(selected_citation_ids)
        responses = {k: v for k, v in all_responses.items() if k in sel_set}
        if not responses:
            return {
                "error": "選択された文献に回答データがありません。",
            }, 400
        meta = dict(meta)
        meta["citations"] = [c for c in meta.get("citations", []) if c.get("id") in sel_set]
    else:
        responses = all_responses

    citations_meta = {}
    for cit in meta.get("citations", []):
        cit_path = case_dir / "citations" / f"{cit['id']}.json"
        if cit_path.exists():
            try:
                with cit_path.open(encoding="utf-8") as f:
                    citations_meta[cit["id"]] = json.load(f)
            except (OSError, json.JSONDecodeError):
                pass

    # 本願分析結果 (任意 — なくても出力)
    hongan_analysis = None
    han_path = case_dir / "analysis" / "hongan_analysis.json"
    if han_path.exists():
        try:
            with han_path.open(encoding="utf-8") as f:
                hongan_analysis = json.load(f)
        except (OSError, json.JSONDecodeError):
            hongan_analysis = None

    # 進歩性判断結果 (任意)
    inventive_step = None
    inv_path = case_dir / "inventive_step.json"
    if inv_path.exists():
        try:
            with inv_path.open(encoding="utf-8") as f:
                inventive_step = json.load(f)
        except (OSError, json.JSONDecodeError):
            inventive_step = None

    # 選択時はファイル名にサフィックス
    if selected_citation_ids and len(responses) < len(all_responses):
        fname = f"{meta['case_id']}_完成版対比表_{len(responses)}件.xlsx"
    else:
        fname = f"{meta['case_id']}_完成版対比表.xlsx"
    output_path = case_dir / "output" / fname
    keywords = _load_keywords(case_dir)

    write_full_report(
        output_path=str(output_path),
        case_meta=meta,
        segments=segs,
        responses=responses,
        citations_meta=citations_meta,
        hongan_analysis=hongan_analysis,
        inventive_step=inventive_step,
        keywords=keywords,
    )
    return {
        "success": True,
        "filename": output_path.name,
        "path": str(output_path),
        "num_citations": len(responses),
        "tabs": {
            "本願解析結果": hongan_analysis is not None,
            "対比表": True,
            "進歩性判断": inventive_step is not None,
        },
    }, 200


def export_excel(case_id, selected_citation_ids=None):
    """Excel 対比表を出力。

    Parameters:
        case_id: 案件 ID
        selected_citation_ids: 出力対象の citation_id リスト。
            None または空なら回答済の全文献を対象にする (従来挙動)。
            指定があれば、そのうち回答済のものだけを出力対象にする。
    """
    from modules.excel_writer import write_comparison_table

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    segments_path = case_dir / "segments.json"

    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    # 全回答ファイル
    all_responses = {}
    responses_dir = case_dir / "responses"
    if responses_dir.exists():
        for rfile in responses_dir.glob("*.json"):
            with open(rfile, "r", encoding="utf-8") as f:
                all_responses[rfile.stem] = json.load(f)

    if not all_responses:
        return {"error": "回答データがありません"}, 400

    # 選択フィルタ適用
    if selected_citation_ids:
        sel_set = set(selected_citation_ids)
        responses = {k: v for k, v in all_responses.items() if k in sel_set}
        if not responses:
            return {
                "error": "選択された文献に回答データがありません。"
                f" 指定 {len(sel_set)} 件のうち回答済 0 件",
            }, 400
    else:
        responses = all_responses

    # case_meta も同じ選択で絞り込み (write_comparison_table 内で
    # citations 順を決めるのに使用される)
    if selected_citation_ids:
        sel_set = set(selected_citation_ids)
        meta = dict(meta)  # 元データを破壊しないようにコピー
        meta["citations"] = [c for c in meta.get("citations", []) if c.get("id") in sel_set]

    citations_meta = {}
    for cit in meta.get("citations", []):
        cit_path = case_dir / "citations" / f"{cit['id']}.json"
        if cit_path.exists():
            with open(cit_path, "r", encoding="utf-8") as f:
                citations_meta[cit["id"]] = json.load(f)

    keywords = _load_keywords(case_dir)

    # ファイル名: 選択時はサフィックス付き (上書き防止 + 識別性)
    if selected_citation_ids and len(responses) < len(all_responses):
        suffix = f"_対比表_{len(responses)}件.xlsx"
    else:
        suffix = "_対比表.xlsx"
    output_path = case_dir / "output" / f"{meta['case_id']}{suffix}"

    write_comparison_table(
        output_path=str(output_path),
        case_meta=meta,
        segments=segs,
        responses=responses,
        citations_meta=citations_meta,
        keywords=keywords,
    )

    return {
        "success": True,
        "filename": output_path.name,
        "path": str(output_path),
        "num_citations": len(responses),
    }, 200


