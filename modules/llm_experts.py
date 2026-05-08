#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Expert profiles for Step 5 opt-in comparison flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from modules.json_utils import extract_json_object
from modules.llm_cache import cached_call_claude


@dataclass(frozen=True)
class ExpertProfile:
    id: str
    role: str
    prompt_template: Callable[[dict], str]
    preferred_model: str
    fallback_model: Optional[str] = None
    effort: str = "high"
    validator: Optional[Callable[[Any, dict], list[str]]] = None
    output_format: str = "json"
    cache_scope: str = "case"
    template_version: str = "v1"
    timeout_sec: int = 600
    max_parallel: int = 1


@dataclass
class ExpertResult:
    parsed: dict | list | str | None
    raw: str
    cache_hit: bool
    model_used: str
    errors: list[str]
    cost_estimate: float = 0.0
    cache_path: str = ""

    @property
    def success(self) -> bool:
        return not self.errors and self.parsed is not None


def _citation_text_for_prompt(citation: dict, max_chars: int = 50000) -> str:
    lines: list[str] = []
    used = 0

    for p in citation.get("paragraphs") or []:
        pid = str(p.get("id") or "").strip()
        text = str(p.get("text") or "").strip()
        if not text:
            continue
        line = f"【{pid}】{text}"
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line)

    for t in citation.get("tables") or []:
        tid = str(t.get("id") or t.get("title") or t.get("caption") or "表").strip()
        body = str(t.get("text") or t.get("markdown") or t.get("csv") or "").strip()
        if not body:
            rows = t.get("rows") or []
            body = "\n".join(" | ".join(str(c) for c in row) for row in rows[:20])
        if not body:
            continue
        line = f"【{tid}】{body}"
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line)

    return "\n".join(lines)


def _build_evidence_extractor_prompt(inputs: dict) -> str:
    citation = inputs.get("citation") or {}
    citation_text = inputs.get("citation_text") or _citation_text_for_prompt(citation)
    keywords = inputs.get("keywords") or []
    kw_text = ", ".join(str(k) for k in keywords if str(k).strip()) or "(なし)"
    return f"""あなたは引用文献から該当箇所を抽出する専門家です。判定はしないでください。

## 対象構成要件
- requirement_id: {inputs.get("requirement_id", "")}
- requirement_text: {inputs.get("requirement_text", "")}
- 関連キーワード: {kw_text}

## 引用文献
- citation_id: {inputs.get("citation_id", "")}

## 引用文献本文
{citation_text}

## 抽出方針
- 構成要件と関係する段落・表だけを抽出してください。
- 「グアニルシステイン」「グァニルシスティン」など、OCR ゆれ・表記ゆれも同一候補として扱ってください。
- 開示有無の最終判定はしないでください。
- evidence_paragraphs は最大5件、evidence_tables は最大3件です。
- snippet は原文を200字以内で抜粋してください。

## 出力 JSON のみ
{{
  "requirement_id": "{inputs.get("requirement_id", "")}",
  "citation_id": "{inputs.get("citation_id", "")}",
  "evidence_paragraphs": [
    {{"paragraph_id": "0035", "snippet": "...", "score": 0.9, "reason": "成分Aが明示"}}
  ],
  "evidence_tables": [
    {{"table_id": "表1", "snippet": "...", "score": 0.7, "reason": "実施例配合"}}
  ],
  "no_match_reason": null
}}
"""


def _build_evidence_section(evidence_by_req_cit: dict) -> str:
    if not evidence_by_req_cit:
        return ""
    lines = ["## 重点参酌 (expert_squad evidence_extractor による抽出)", ""]
    for key in sorted(evidence_by_req_cit):
        ev = evidence_by_req_cit.get(key) or {}
        req_id, cit_id = key.split("||", 1) if "||" in key else (key, "")
        lines.append(f"### {cit_id} / {req_id}")
        paras = ev.get("evidence_paragraphs") or []
        tables = ev.get("evidence_tables") or []
        if not paras and not tables:
            reason = ev.get("no_match_reason") or "重点箇所なし"
            lines.append(f"- 重点箇所なし: {reason}")
            continue
        for p in paras[:5]:
            lines.append(
                f"- 段落【{p.get('paragraph_id','')}】 score={p.get('score','')}: "
                f"{p.get('snippet','')} ({p.get('reason','')})"
            )
        for t in tables[:3]:
            lines.append(
                f"- 表【{t.get('table_id','')}】 score={t.get('score','')}: "
                f"{t.get('snippet','')} ({t.get('reason','')})"
            )
        lines.append("")
    return "\n".join(lines).strip()


def _build_claim_chart_judge_prompt(inputs: dict) -> str:
    from modules.prompt_generator import generate_prompt_requirement_first

    base = generate_prompt_requirement_first(
        inputs.get("segments") or [],
        inputs.get("citations") or [],
        inputs.get("keywords"),
        inputs.get("field") or "cosmetics",
        hongan=inputs.get("hongan"),
    )
    evidence_section = _build_evidence_section(inputs.get("evidence_by_req_cit") or {})
    if not evidence_section:
        return base
    return (
        base
        + "\n\n---\n\n"
        + evidence_section
        + "\n\n上記の重点参酌は候補箇所です。最終判断は既存の出力形式に厳密に従ってください。"
    )


def _paragraph_id_set(citation: dict) -> set[str]:
    out = set()
    for p in citation.get("paragraphs") or []:
        pid = str(p.get("id") or "").strip()
        if pid:
            out.add(pid)
            out.add(pid.lstrip("0") or pid)
    return out


def _validate_evidence_extractor_output(parsed: Any, inputs: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(parsed, dict):
        return ["JSON object が見つかりません"]
    if parsed.get("requirement_id") != inputs.get("requirement_id"):
        errors.append("requirement_id が入力と一致しません")
    if parsed.get("citation_id") != inputs.get("citation_id"):
        errors.append("citation_id が入力と一致しません")

    valid_para_ids = _paragraph_id_set(inputs.get("citation") or {})
    for i, p in enumerate(parsed.get("evidence_paragraphs") or []):
        pid = str(p.get("paragraph_id") or "").strip()
        if valid_para_ids and pid not in valid_para_ids and (pid.lstrip("0") or pid) not in valid_para_ids:
            errors.append(f"evidence_paragraphs[{i}].paragraph_id が citation に存在しません: {pid}")
        try:
            score = float(p.get("score"))
        except (TypeError, ValueError):
            errors.append(f"evidence_paragraphs[{i}].score が数値ではありません")
            continue
        if not 0 <= score <= 1:
            errors.append(f"evidence_paragraphs[{i}].score が 0〜1 の範囲外です")

    for i, t in enumerate(parsed.get("evidence_tables") or []):
        try:
            score = float(t.get("score"))
        except (TypeError, ValueError):
            errors.append(f"evidence_tables[{i}].score が数値ではありません")
            continue
        if not 0 <= score <= 1:
            errors.append(f"evidence_tables[{i}].score が 0〜1 の範囲外です")

    return errors


def _validate_claim_chart_judge_output(parsed: Any, inputs: dict) -> list[str]:
    from modules.response_parser import parse_response

    raw = parsed if isinstance(parsed, str) else ""
    result, errors = parse_response(raw, inputs.get("all_segment_ids") or [])
    if not result:
        return [f"parse failed: {e}" for e in (errors or ["対比JSONを抽出できませんでした"])]
    inputs["_validated_response"] = result
    inputs["_validated_errors"] = errors or []
    return []


EXPERTS: dict[str, ExpertProfile] = {
    "evidence_extractor": ExpertProfile(
        id="evidence_extractor",
        role="引用文献から特定の構成要件に関係する段落・表を抽出する専門家",
        prompt_template=_build_evidence_extractor_prompt,
        preferred_model="haiku",
        fallback_model="glm-haiku",
        effort="medium",
        validator=_validate_evidence_extractor_output,
        output_format="json",
        cache_scope="case",
        template_version="v1",
        timeout_sec=180,
        max_parallel=4,
    ),
    "claim_chart_judge": ExpertProfile(
        id="claim_chart_judge",
        role="重点段落と構成要件をもとに開示有無を判定する審査官専門家",
        prompt_template=_build_claim_chart_judge_prompt,
        preferred_model="opus",
        fallback_model="sonnet",
        effort="high",
        validator=_validate_claim_chart_judge_output,
        output_format="text",
        cache_scope="case",
        template_version="v1",
        timeout_sec=600,
        max_parallel=1,
    ),
}


def _parse_raw(raw: str, output_format: str) -> Any:
    if output_format == "json":
        return extract_json_object(raw)
    return raw


def run_expert(
    expert_id: str,
    *,
    inputs: dict,
    case_id: str | None = None,
    model_override: str | None = None,
    effort_override: str | None = None,
    skip_cache: bool = False,
) -> ExpertResult:
    """Run an expert profile and return structured errors instead of raising."""
    profile = EXPERTS.get(expert_id)
    if not profile:
        return ExpertResult(None, "", False, "", [f"unknown expert: {expert_id}"])

    try:
        prompt = profile.prompt_template(inputs)
    except Exception as e:
        return ExpertResult(None, "", False, "", [f"prompt build failed: {e}"])
    if not prompt.strip():
        return ExpertResult(None, "", False, "", ["prompt が空です"])

    models = [model_override or profile.preferred_model]
    if not model_override and profile.fallback_model:
        models.append(profile.fallback_model)
    effort = effort_override or profile.effort

    last = ExpertResult(None, "", False, "", [])
    for model in models:
        try:
            raw, meta = cached_call_claude(
                prompt,
                model=model,
                effort=effort,
                timeout=profile.timeout_sec,
                cache_scope=profile.cache_scope,
                case_id=case_id,
                template_version=profile.template_version,
                skip_cache=skip_cache,
            )
        except Exception as e:
            last = ExpertResult(None, "", False, model, [f"LLM 呼び出し失敗: {e}"])
            continue

        parsed = _parse_raw(raw, profile.output_format)
        errors = profile.validator(parsed, inputs) if profile.validator else []
        if errors:
            parsed = None
        elif expert_id == "claim_chart_judge" and inputs.get("_validated_response") is not None:
            parsed = inputs.get("_validated_response")
        result = ExpertResult(
            parsed=parsed,
            raw=raw,
            cache_hit=bool(meta.get("cache_hit")),
            model_used=model,
            errors=errors,
            cost_estimate=0.0,
            cache_path=meta.get("cache_path", ""),
        )
        if result.success:
            return result
        last = result

    return last
