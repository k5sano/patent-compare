#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Step 5 対比の直接実行。"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from services.case_service import get_case_dir, load_case_meta
from services.comparison.common import _load_citation_for_prompt, _safe_prompt_filename
from services.comparison.prompt import (
    _empty_citation_error,
    _filter_keywords_by_valid_segments,
    _get_all_segment_ids,
    _is_empty_citation,
    _resolve_doc_id,
)
from services.comparison.response import _normalize_cited_locations_inplace
from services.comparison.expert import _compare_execute_expert_squad

logger = logging.getLogger(__name__)
def _compare_execute_per_citation_parallel(
    *, case_id, citations, segs, keywords, hongan, field,
    model, known_cit_ids, max_workers=2, effort=None, mode="requirement_first",
    fallback_to_legacy=False,
):
    """個別対比 per-citation パス。

    lightweight モデルでは並列、Opus 等の重量モデルでは直列推奨。
    max_workers=1 でも ThreadPoolExecutor 経由で正常に直列実行できる。
    1プロンプトに全 citation を統合する従来方式と異なり、各 citation を
    別 LLM 呼び出しで処理することで:
      - 並列化で総所要時間を短縮（lightweight は通常 max_workers=2）
      - 1 件の失敗が他に波及しない
      - 各 prompt のサイズが小さいので引例ごとの注意が薄まりにくい
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from modules.prompt_generator import (
        generate_prompt as _gen_legacy,
        generate_prompt_requirement_first as _gen_reqfirst,
    )
    from modules.response_parser import parse_response, split_multi_response
    from modules.claude_client import (
        call_claude,
        ClaudeClientError,
        ClaudeExecutionError,
        ClaudeNotFoundError,
        ClaudeTimeoutError,
        execution_error_hint,
        model_provider,
        provider_setup_hint,
    )

    case_dir = get_case_dir(case_id)
    responses_dir = case_dir / "responses"
    responses_dir.mkdir(parents=True, exist_ok=True)
    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    max_workers = max(1, int(max_workers or 1))
    all_segment_ids = _get_all_segment_ids(segs)
    _gen = _gen_reqfirst if mode == "requirement_first" else _gen_legacy
    provider = model_provider(model)
    if provider == "glm":
        timeout = 900
    elif provider == "codex":
        timeout = 720
    else:
        timeout = 600

    logger.info(
        "Step5 execution_mode=per_citation provider=%s model=%s workers=%s citations=%s mode=%s",
        provider, model, max_workers, len(citations), mode,
    )

    def _safe_label(cit):
        label = cit.get("patent_number") or cit.get("label") or cit.get("doc_number") or "unknown"
        return "".join(ch for ch in str(label) if ch not in '/\\:*?"<>|').strip() or "unknown"

    def _one(cit):
        safe_label = _safe_label(cit)
        try:
            prompt_text = _gen(segs, [cit], keywords, field, hongan=hongan)
        except Exception as e:
            return {"doc_id": safe_label, "ok": False,
                    "error": f"prompt生成失敗: {e}", "char_count": 0, "response_length": 0}

        try:
            with open(prompts_dir / _safe_prompt_filename(safe_label, suffix=".txt"), "w", encoding="utf-8") as f:
                f.write(prompt_text)
        except OSError:
            pass

        call_kwargs = {"timeout": timeout, "model": model}
        if effort is not None:
            call_kwargs["effort"] = effort
        try:
            raw = call_claude(prompt_text, **call_kwargs)
        except ClaudeNotFoundError as e:
            return {
                "doc_id": safe_label, "ok": False, "error": str(e),
                "phase": "llm_not_available",
                "provider": provider,
                "hint": provider_setup_hint(provider),
                "char_count": len(prompt_text), "response_length": 0,
            }
        except ClaudeTimeoutError as e:
            return {
                "doc_id": safe_label, "ok": False, "error": str(e),
                "phase": "llm_timeout",
                "provider": provider,
                "timeout_sec": timeout,
                "hint": execution_error_hint(provider, str(e)),
                "char_count": len(prompt_text), "response_length": 0,
            }
        except ClaudeExecutionError as e:
            return {
                "doc_id": safe_label, "ok": False, "error": str(e),
                "phase": "llm_execution",
                "provider": provider,
                "hint": execution_error_hint(provider, str(e)),
                "char_count": len(prompt_text), "response_length": 0,
            }
        except ClaudeClientError as e:
            return {
                "doc_id": safe_label, "ok": False, "error": str(e),
                "phase": "llm_unknown",
                "provider": provider,
                "char_count": len(prompt_text), "response_length": 0,
            }

        try:
            with open(responses_dir / f"_raw_{safe_label}.txt", "w", encoding="utf-8") as f:
                f.write(raw)
        except OSError:
            pass

        result, errors = parse_response(raw, all_segment_ids)
        if not result:
            return {"doc_id": safe_label, "ok": False, "errors": errors,
                    "char_count": len(prompt_text), "response_length": len(raw)}

        per_doc = split_multi_response(result)
        saved = []
        resolved_log = []
        for doc_id, doc_result in per_doc.items():
            resolved = _resolve_doc_id(doc_id, known_cit_ids)
            if resolved != doc_id:
                resolved_log.append(f"{doc_id} → {resolved}")
            _normalize_cited_locations_inplace(doc_result)
            with open(responses_dir / f"{resolved}.json", "w", encoding="utf-8") as f:
                json.dump(doc_result, f, ensure_ascii=False, indent=2)
            saved.append(resolved)

        return {
            "doc_id": safe_label, "ok": True, "saved": saved,
            "errors": errors, "resolved": resolved_log,
            "char_count": len(prompt_text), "response_length": len(raw),
        }

    saved_docs = []
    all_errors = []
    resolved_log = []
    char_total = 0
    resp_total = 0

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_one, c): c for c in citations}
        for fut in as_completed(futures):
            try:
                r = fut.result()
            except Exception as e:
                all_errors.append(f"_one fatal: {e}")
                continue
            char_total += r.get("char_count", 0)
            resp_total += r.get("response_length", 0)
            if r.get("ok"):
                saved_docs.extend(r.get("saved", []))
                if r.get("errors"):
                    all_errors.extend(f"{r['doc_id']}: {e}" for e in r["errors"])
                if r.get("resolved"):
                    resolved_log.extend(f"{r['doc_id']}: {x}" for x in r["resolved"])
            else:
                err_msg = r.get("error") or "; ".join(r.get("errors") or [])
                phase = f" [{r.get('phase')}]" if r.get("phase") else ""
                all_errors.append(f"{r['doc_id']}{phase}: {err_msg}")

    return {
        "success": len(saved_docs) > 0,
        "errors": all_errors,
        "saved_docs": saved_docs,
        "num_docs": len(saved_docs),
        "resolved": resolved_log,
        "char_count": char_total,
        "response_length": resp_total,
        "parallel": max_workers,
        "model": model,
        "mode_used": mode,
        "fallback_to_legacy": fallback_to_legacy,
        "execution_mode": "per_citation",
        "split_by_citation": True,
    }, 200


def compare_execute(case_id, citation_ids, model=None, mode="requirement_first", effort=None):
    """直接実行: 対比プロンプト → Claude CLI → パース

    Parameters:
        model: 'opus'/'sonnet'/'haiku' のエイリアスまたはフル ID。
               None の場合 CLI 既定 (通常 Opus)。
        mode: "requirement_first" (default, 推奨) = 構成要件主体型。
              本願はキーワード経由で必要箇所のみ抜粋。
              "legacy" = 本願全文を流す旧方式。
              keywords.json が無い案件では自動的に legacy にフォールバック。
        effort: 'low'/'medium'/'high'/'xhigh'/'max'。
                None なら call_claude のデフォルト (high)。
    """
    if os.environ.get("COMPARE_MODE", "").strip().lower() == "expert_squad":
        return _compare_execute_expert_squad(
            case_id, citation_ids, model=model, mode=mode, effort=effort,
        )

    from modules.prompt_generator import (
        generate_prompt as _gen_legacy,
        generate_prompt_requirement_first as _gen_reqfirst,
    )
    from modules.response_parser import parse_response, split_multi_response
    from modules.claude_client import (
        call_claude,
        ClaudeClientError,
        ClaudeExecutionError,
        ClaudeNotFoundError,
        ClaudeTimeoutError,
        execution_error_hint,
        model_provider,
        provider_setup_hint,
    )

    # mode に応じて prompt 生成関数を切替
    _gen = _gen_reqfirst if mode == "requirement_first" else _gen_legacy

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
    empty_ids = []
    for cit_id in citation_ids:
        cit, err = _load_citation_for_prompt(case_id, cit_id, case_dir)
        if err:
            return {"error": err}, 404
        if _is_empty_citation(cit):
            empty_ids.append((cit_id, cit))
        citations.append(cit)

    if empty_ids:
        msgs = [_empty_citation_error(cid, c) for cid, c in empty_ids]
        return {
            "error": " / ".join(msgs),
            "empty_citation_ids": [cid for cid, _ in empty_ids],
        }, 400

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)
    keywords = _filter_keywords_by_valid_segments(keywords, segs)

    # mode 安全装置: requirement_first はキーワード経由で本願参酌を抽出するので、
    # keywords.json が無い (Step 3 未完了) 案件では効果が薄い → legacy にフォールバック
    fallback_to_legacy = False
    if mode == "requirement_first" and not (keywords or []):
        mode = "legacy"
        fallback_to_legacy = True
        # _gen を切り替え (mode が変わったので generate_prompt 系を選び直し)
        from modules.prompt_generator import generate_prompt as _gen_legacy_fb
        _gen = _gen_legacy_fb

    field = meta.get("field", "cosmetics")
    hongan = None
    hongan_path = case_dir / "hongan.json"
    if hongan_path.exists():
        with open(hongan_path, "r", encoding="utf-8") as f:
            hongan = json.load(f)

    # per-citation 実行:
    # - COMPARE_PER_CITATION=1 ならモデル/件数を問わず有効化 (Opus は直列推奨)。
    # - 旧来互換として COMPARE_PARALLEL>=2 かつ lightweight モデルなら自動並列。
    # 未指定時の重量モデルは従来どおり統合方式。
    import os as _os
    try:
        parallel_workers = int(_os.environ.get("COMPARE_PARALLEL", "0"))
    except ValueError:
        parallel_workers = 0
    if parallel_workers > 2:
        logger.warning(
            "COMPARE_PARALLEL=%d は方針上限(2)を超えるため2に丸めます",
            parallel_workers,
        )
        parallel_workers = 2
    per_citation_env = _os.environ.get("COMPARE_PER_CITATION", "").strip().lower()
    per_citation_explicit = per_citation_env in ("1", "true", "yes", "on")
    model_l = (model or "").lower()
    is_lightweight = (
        ("sonnet" in model_l)
        or ("haiku" in model_l)
        or ("mini" in model_l)
        or ("glm" in model_l)
    )
    use_per_citation = per_citation_explicit or (
        parallel_workers >= 2 and is_lightweight and len(citations) >= 2
    )
    if use_per_citation:
        known_cit_ids = [c.get("id") for c in (meta or {}).get("citations", []) if c.get("id")]
        if parallel_workers >= 1:
            workers = parallel_workers
        else:
            workers = 2 if is_lightweight else 1
        return _compare_execute_per_citation_parallel(
            case_id=case_id, citations=citations, segs=segs,
            keywords=keywords, hongan=hongan, field=field,
            model=model, known_cit_ids=known_cit_ids,
            max_workers=workers, effort=effort, mode=mode,
            fallback_to_legacy=fallback_to_legacy,
        )

    prompt_text = _gen(segs, citations, keywords, field, hongan=hongan)

    ids_label = "_".join(citation_ids)
    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    with open(prompts_dir / _safe_prompt_filename(ids_label), "w", encoding="utf-8") as f:
        f.write(prompt_text)

    provider = model_provider(model)
    if provider == "glm":
        timeout = 1200 if len(citations) <= 2 else 1500
    elif provider == "codex":
        timeout = 900 if len(citations) <= 2 else 1200
    else:
        timeout = 600 if len(citations) <= 2 else 900
    call_kwargs = {"timeout": timeout, "model": model}
    if effort is not None:
        call_kwargs["effort"] = effort
    logger.info(
        "Step5 execution_mode=integrated provider=%s model=%s citations=%s mode=%s prompt_chars=%s",
        provider, model, len(citations), mode, len(prompt_text),
    )
    try:
        raw_response = call_claude(prompt_text, **call_kwargs)
    except ClaudeNotFoundError as e:
        logger.warning("Step5 LLM not available provider=%s model=%s: %s", provider, model, e)
        return {
            "error": str(e),
            "phase": "llm_not_available",
            "provider": provider,
            "model": model,
            "hint": provider_setup_hint(provider),
        }, 502
    except ClaudeTimeoutError as e:
        logger.warning(
            "Step5 LLM timeout provider=%s model=%s timeout=%s prompt_chars=%s",
            provider, model, timeout, len(prompt_text),
        )
        return {
            "error": str(e),
            "phase": "llm_timeout",
            "provider": provider,
            "model": model,
            "timeout_sec": timeout,
            "prompt_chars": len(prompt_text),
            "hint": execution_error_hint(provider, str(e)),
        }, 504
    except ClaudeExecutionError as e:
        logger.warning("Step5 LLM execution error provider=%s model=%s: %s", provider, model, e)
        return {
            "error": str(e),
            "phase": "llm_execution",
            "provider": provider,
            "model": model,
            "hint": execution_error_hint(provider, str(e)),
        }, 502
    except ClaudeClientError as e:
        logger.warning("Step5 LLM unknown error provider=%s model=%s: %s", provider, model, e)
        return {
            "error": str(e),
            "phase": "llm_unknown",
            "provider": provider,
            "model": model,
        }, 502

    all_segment_ids = _get_all_segment_ids(segs)

    raw_path = case_dir / "responses" / "_last_raw_response.txt"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(raw_response)

    result, errors = parse_response(raw_response, all_segment_ids)
    if not result:
        logger.warning(
            "Step5 parse failed provider=%s model=%s response_chars=%s errors=%s",
            provider, model, len(raw_response), errors,
        )
        try:
            rel_raw_path = str(raw_path.relative_to(Path(__file__).parent.parent.resolve()))
        except ValueError:
            rel_raw_path = str(raw_path)
        return {
            "error": "LLM 応答が対比JSONとして解釈できませんでした",
            "phase": "parse_failed",
            "provider": provider,
            "model": model,
            "errors": errors,
            "raw_preview": raw_response[:300],
            "raw_path": rel_raw_path,
            "hint": "responses/_last_raw_response.txt を確認するか、別モデル/低い effort で再実行してください。",
        }, 502

    # case.yaml の citations から既知 ID を取得し、LLM 応答の document_id を
    # _resolve_doc_id で吸着 (例: 'JP5214138B2' → 'JP5214138')。
    # これをやらないと 'JP5214138B2.json' で保存されて Step 6 が拾えなくなる
    # (silent stale と同種の片手落ち)。save_response_multi 側と同じロジック。
    known_cit_ids = [c.get("id") for c in (meta or {}).get("citations", []) if c.get("id")]

    saved_docs = []
    resolved_log = []  # 解決マッピングのデバッグ情報
    if result:
        per_doc = split_multi_response(result)
        responses_dir = case_dir / "responses"
        responses_dir.mkdir(parents=True, exist_ok=True)
        for doc_id, doc_result in per_doc.items():
            resolved = _resolve_doc_id(doc_id, known_cit_ids)
            if resolved != doc_id:
                resolved_log.append(f"{doc_id} → {resolved}")
            _normalize_cited_locations_inplace(doc_result)
            resp_path = responses_dir / f"{resolved}.json"
            with open(resp_path, "w", encoding="utf-8") as f:
                json.dump(doc_result, f, ensure_ascii=False, indent=2)
            saved_docs.append(resolved)

    return {
        "success": result is not None,
        "errors": errors,
        "saved_docs": saved_docs,
        "num_docs": len(saved_docs),
        "resolved": resolved_log,  # ID 吸着の履歴 (デバッグ用)
        "char_count": len(prompt_text),
        "response_length": len(raw_response),
        "mode_used": mode,
        "fallback_to_legacy": fallback_to_legacy,
        "execution_mode": "integrated",
    }, 200


