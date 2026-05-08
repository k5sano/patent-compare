#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SHA256 based file cache for LLM responses."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path


def _resolved_model_name(model: str | None) -> str:
    from modules.claude_client import resolve_model

    return resolve_model(model) or "__default__"


def _cache_dir(cache_scope: str, case_id: str | None) -> tuple[Path | None, str]:
    from services import case_service

    if cache_scope == "none":
        return None, "none"
    if cache_scope == "case":
        if not case_id:
            return None, "none"
        return case_service.get_case_dir(case_id) / "llm_cache", "case"
    if cache_scope == "global":
        return case_service.PROJECT_ROOT / "cases" / "_llm_cache_global", "global"
    return None, "none"


def _make_key(prompt_text: str, model: str | None, effort: str | None,
              template_version: str, use_search: bool) -> tuple[str, str, str]:
    prompt_sha = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    model_resolved = _resolved_model_name(model)
    body = json.dumps({
        "prompt_sha256": prompt_sha,
        "model": model_resolved,
        "effort": effort or "",
        "template_version": template_version,
        "use_search": bool(use_search),
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest(), prompt_sha, model_resolved


def cached_call_claude(
    prompt_text: str,
    *,
    model: str | None = None,
    effort: str | None = None,
    timeout: int = 600,
    use_search: bool = False,
    cache_scope: str = "case",
    case_id: str | None = None,
    template_version: str = "v1",
    skip_cache: bool = False,
) -> tuple[str, dict]:
    """Call ``call_claude`` with an exact prompt/model/effort/version cache.

    Returns:
        (response_text, meta)
    """
    cache_base, effective_scope = _cache_dir(cache_scope, case_id)
    key, prompt_sha, model_resolved = _make_key(
        prompt_text, model, effort, template_version, use_search
    )
    path = cache_base / f"{key}.json" if cache_base is not None else None

    meta = {
        "cache_hit": False,
        "cache_path": str(path) if path else "",
        "model": model_resolved,
        "effort": effort or "",
        "cache_scope": effective_scope,
    }

    if path is not None and not skip_cache and path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            response = data.get("response")
            if isinstance(response, str):
                meta["cache_hit"] = True
                return response, meta
        except (OSError, json.JSONDecodeError):
            pass

    from modules.claude_client import call_claude

    response = call_claude(
        prompt_text, timeout=timeout, use_search=use_search,
        model=model, effort=effort,
    )

    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump({
                "prompt_sha256": prompt_sha,
                "model": model_resolved,
                "effort": effort or "",
                "template_version": template_version,
                "use_search": bool(use_search),
                "response": response,
                "saved_at": datetime.now().isoformat(timespec="seconds"),
            }, f, ensure_ascii=False, indent=2)
        tmp.replace(path)

    return response, meta
