#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""進歩性判断プロンプト・回答処理。"""
from __future__ import annotations

import json

from services.case_service import get_case_dir, load_case_meta


def _load_hongan(case_dir):
    hongan_path = case_dir / "hongan.json"
    if hongan_path.exists():
        with open(hongan_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


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
    hongan = _load_hongan(case_dir)
    prompt_text = generate_inventive_step_prompt(
        segs, responses, citations_meta, keywords, field, hongan=hongan
    )

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


def inventive_step_execute(case_id, model=None, effort=None):
    """直接実行: 進歩性判断プロンプト → Claude CLI → パース。

    進歩性判断は証拠量が大きく推論負荷も高いため、既定モデルは opus 推奨。
    """
    from modules.inventive_step_analyzer import (
        generate_inventive_step_prompt,
        get_inventive_step_defaults,
        parse_inventive_step_response,
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
    hongan = _load_hongan(case_dir)
    prompt_text = generate_inventive_step_prompt(
        segs, responses, citations_meta, keywords, field, hongan=hongan
    )

    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    with open(prompts_dir / "inventive_step_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt_text)

    defaults = get_inventive_step_defaults()
    model = model or defaults["default_model"]
    effort = effort or defaults["default_effort"]
    try:
        raw_response = call_claude(prompt_text, timeout=1200, model=model, effort=effort)
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
