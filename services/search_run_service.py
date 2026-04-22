#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""検索ラン管理サービス

J-PlatPat / Google Patents で検索式を実行した結果を
cases/<id>/search_runs/<run_id>.json に保存し、スクリーニング状態を管理する。

データモデル (1 ランの JSON 構造):
{
  "run_id": "20260421-215830-narrow",
  "created_at": "2026-04-21T21:58:30+09:00",
  "updated_at": "...",
  "source": "jplatpat" | "google_patents",
  "formula_level": "narrow" | "medium" | "wide" | "custom",
  "formula": "<検索式本体>",
  "search_url": "<検索結果ページ URL>",
  "hit_count": 42,
  "status": "pending" | "done" | "error",
  "error": null,
  "hits": [
    {
      "patent_id": "特開2023-123456",
      "title": "...",
      "applicant": "...",
      "publication_date": "2023-xx-xx",
      "ipc": [...], "fi": [...], "fterm": [...],
      "url": "...",
      "abstract": null,        # Phase2 で enrich
      "claim1": null,          # Phase2 で enrich
      "ai_score": null,        # Phase2 の関連度
      "ai_reason": null,
      "screening": "pending",  # pending / star / triangle / reject / hold
      "note": "",
      "downloaded_as_citation": false
    }
  ]
}
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Iterable

from services.case_service import get_case_dir


SCREENING_STATES = ("pending", "star", "triangle", "reject", "hold")


def _runs_dir(case_id: str) -> Path:
    d = get_case_dir(case_id) / "search_runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _slugify(text: str, max_len: int = 20) -> str:
    t = re.sub(r'[^A-Za-z0-9._-]+', '-', text).strip('-')
    return t[:max_len] or "run"


def new_run_id(formula_level: str = "custom") -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{ts}-{_slugify(formula_level)}"


def list_runs(case_id: str) -> list:
    """案件配下の全検索ランのサマリリストを返す (新しい順)"""
    d = _runs_dir(case_id)
    runs = []
    for p in sorted(d.glob("*.json"), reverse=True):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            runs.append({
                "run_id": data.get("run_id") or p.stem,
                "created_at": data.get("created_at", ""),
                "source": data.get("source", ""),
                "formula_level": data.get("formula_level", ""),
                "formula": data.get("formula", ""),
                "hit_count": data.get("hit_count", len(data.get("hits", []))),
                "status": data.get("status", ""),
                "stars": sum(1 for h in data.get("hits", [])
                             if h.get("screening") == "star"),
                "rejects": sum(1 for h in data.get("hits", [])
                               if h.get("screening") == "reject"),
            })
        except Exception:
            continue
    return runs


def load_run(case_id: str, run_id: str) -> Optional[dict]:
    p = _runs_dir(case_id) / f"{run_id}.json"
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_run(case_id: str, data: dict) -> Path:
    run_id = data.get("run_id")
    if not run_id:
        raise ValueError("run_id is required")
    data["updated_at"] = _now_iso()
    p = _runs_dir(case_id) / f"{run_id}.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return p


def delete_run(case_id: str, run_id: str) -> bool:
    p = _runs_dir(case_id) / f"{run_id}.json"
    if p.exists():
        p.unlink()
        return True
    return False


def create_run_from_hits(
    case_id: str,
    *,
    formula: str,
    formula_level: str = "custom",
    source: str = "jplatpat",
    hits: Iterable[dict],
    search_url: str = "",
    status: str = "done",
    error: Optional[str] = None,
) -> dict:
    """JplatpatHit / 各種 hit dict からランを生成して保存。"""
    run_id = new_run_id(formula_level)
    normalized_hits = [_normalize_hit(h) for h in hits]
    data = {
        "run_id": run_id,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "source": source,
        "formula_level": formula_level,
        "formula": formula,
        "search_url": search_url,
        "hit_count": len(normalized_hits),
        "status": status,
        "error": error,
        "hits": normalized_hits,
    }
    save_run(case_id, data)
    return data


def _normalize_hit(h) -> dict:
    """JplatpatHit や dict を内部標準形に整える。"""
    if hasattr(h, "to_dict"):
        d = h.to_dict()
    elif isinstance(h, dict):
        d = dict(h)
    else:
        d = {"row_text": str(h)}

    return {
        "patent_id": d.get("patent_id", "") or "",
        "title": d.get("title", "") or "",
        "applicant": d.get("applicant", "") or "",
        "publication_date": d.get("publication_date", "") or "",
        "ipc": list(d.get("ipc") or []),
        "fi": list(d.get("fi") or []),
        "fterm": list(d.get("fterm") or []),
        "url": d.get("url", "") or "",
        "abstract": d.get("abstract"),
        "claim1": d.get("claim1"),
        "ai_score": d.get("ai_score"),
        "ai_reason": d.get("ai_reason"),
        "screening": d.get("screening", "pending"),
        "note": d.get("note", ""),
        "downloaded_as_citation": bool(d.get("downloaded_as_citation", False)),
        "row_text": d.get("row_text", ""),
    }


def update_screening(
    case_id: str, run_id: str, patent_id: str, screening: str, note: Optional[str] = None
) -> Optional[dict]:
    """特定候補のスクリーニング状態を更新。"""
    if screening not in SCREENING_STATES:
        raise ValueError(f"invalid screening state: {screening}")
    data = load_run(case_id, run_id)
    if not data:
        return None
    updated = False
    for h in data.get("hits", []):
        if h.get("patent_id") == patent_id:
            h["screening"] = screening
            if note is not None:
                h["note"] = note
            updated = True
            break
    if updated:
        save_run(case_id, data)
        return data
    return None


def bulk_update_screening(
    case_id: str, run_id: str, updates: list
) -> Optional[dict]:
    """一括更新。updates = [{"patent_id": "...", "screening": "...", "note": "..."}, ...]"""
    data = load_run(case_id, run_id)
    if not data:
        return None
    index = {h.get("patent_id"): h for h in data.get("hits", [])}
    for u in updates:
        pid = u.get("patent_id")
        target = index.get(pid)
        if not target:
            continue
        scr = u.get("screening")
        if scr and scr in SCREENING_STATES:
            target["screening"] = scr
        if "note" in u:
            target["note"] = u.get("note") or ""
    save_run(case_id, data)
    return data


def merge_runs(case_id: str, run_ids: list) -> list:
    """複数ランの hits をマージし重複排除 (Phase2 向け)。

    重複判定: patent_id の国コード+数字部分 (特開2023-123456 → JP2023123456)
    """
    from modules.jplatpat_client import JplatpatHit

    seen = {}
    merged = []
    for rid in run_ids:
        data = load_run(case_id, rid)
        if not data:
            continue
        for h in data.get("hits", []):
            # 重複キー
            pid = h.get("patent_id", "")
            tmp = JplatpatHit(patent_id=pid)
            key = tmp.dedup_key or pid
            if key in seen:
                # スクリーニング状態やスコアを優先度の高いもので上書き (star > triangle > pending > hold > reject)
                existing = seen[key]
                if _screening_priority(h.get("screening")) > _screening_priority(existing.get("screening")):
                    existing["screening"] = h.get("screening", "pending")
                    existing["note"] = h.get("note", existing.get("note", ""))
                if h.get("ai_score") is not None:
                    if existing.get("ai_score") is None or h.get("ai_score") > existing.get("ai_score"):
                        existing["ai_score"] = h.get("ai_score")
                        existing["ai_reason"] = h.get("ai_reason")
                existing.setdefault("found_in_runs", []).append(rid)
                continue
            hit_copy = dict(h)
            hit_copy["found_in_runs"] = [rid]
            seen[key] = hit_copy
            merged.append(hit_copy)
    return merged


def _screening_priority(state) -> int:
    return {
        "star": 5,
        "triangle": 4,
        "pending": 3,
        "hold": 2,
        "reject": 1,
    }.get(state or "pending", 0)


def get_starred_patent_ids(case_id: str, run_ids: Optional[list] = None) -> list:
    """☆マークの付いた候補の patent_id リストを返す (未 DL のみ)。"""
    pids = []
    if run_ids is None:
        run_ids = [r["run_id"] for r in list_runs(case_id)]
    for rid in run_ids:
        data = load_run(case_id, rid)
        if not data:
            continue
        for h in data.get("hits", []):
            if h.get("screening") == "star" and not h.get("downloaded_as_citation"):
                pid = h.get("patent_id")
                if pid and pid not in pids:
                    pids.append(pid)
    return pids


def mark_downloaded(
    case_id: str, run_id: str, patent_id: str, downloaded: bool = True
) -> None:
    data = load_run(case_id, run_id)
    if not data:
        return
    for h in data.get("hits", []):
        if h.get("patent_id") == patent_id:
            h["downloaded_as_citation"] = downloaded
    save_run(case_id, data)


def enrich_run(case_id: str, run_id: str, limit: int = 20) -> Optional[dict]:
    """Google Patents の詳細ページから要約と請求項1を取得して hits を埋める。

    既に abstract / claim1 が入っている hit はスキップ。
    """
    from modules.google_patents_scraper import fetch_patent_detail

    data = load_run(case_id, run_id)
    if not data:
        return None

    enriched_count = 0
    for h in data.get("hits", []):
        if enriched_count >= limit:
            break
        if h.get("abstract") and h.get("claim1"):
            continue
        pid = h.get("patent_id") or ""
        if not pid:
            continue
        try:
            detail = fetch_patent_detail(pid)
        except Exception:
            detail = {}
        if detail.get("abstract"):
            h["abstract"] = detail["abstract"]
        if detail.get("claim1"):
            h["claim1"] = detail["claim1"]
        if detail.get("title") and not h.get("title"):
            h["title"] = detail["title"]
        if detail.get("assignee") and not h.get("applicant"):
            h["applicant"] = detail["assignee"]
        enriched_count += 1

    save_run(case_id, data)
    return data


def ai_score_run(case_id: str, run_id: str, limit: int = 20) -> Optional[dict]:
    """Claude を使って本願との関連度スコア (0-100) を付与する。

    本願の claim1 / 発明の名称を入力として各 hit の title+abstract+claim1 を
    評価し ai_score と ai_reason を書き込む。
    """
    from modules.claude_client import call_claude, ClaudeClientError

    data = load_run(case_id, run_id)
    if not data:
        return None

    # 本願情報を取得
    case_dir = get_case_dir(case_id)
    hongan_summary = _build_hongan_summary(case_dir)

    scored_count = 0
    for h in data.get("hits", []):
        if scored_count >= limit:
            break
        if h.get("ai_score") is not None:
            continue
        prompt = _build_scoring_prompt(hongan_summary, h)
        try:
            raw = call_claude(prompt, timeout=120, use_search=False)
        except (ClaudeClientError, Exception) as e:
            h["ai_reason"] = f"scoring error: {e}"
            continue

        score, reason = _parse_scoring_response(raw)
        if score is not None:
            h["ai_score"] = score
            h["ai_reason"] = reason
            scored_count += 1

    # 降順ソート用情報を追加
    save_run(case_id, data)
    return data


def _build_hongan_summary(case_dir: Path) -> dict:
    """本願の title + claim1 + summary を集約。"""
    summary = {"title": "", "claim1": "", "summary": ""}

    # case.yaml からタイトル
    yaml_path = case_dir / "case.yaml"
    if yaml_path.exists():
        import yaml as _yaml
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                meta = _yaml.safe_load(f) or {}
            summary["title"] = meta.get("patent_title") or meta.get("title") or ""
        except Exception:
            pass

    # segments.json から claim1
    seg_path = case_dir / "segments.json"
    if seg_path.exists():
        try:
            with open(seg_path, "r", encoding="utf-8") as f:
                segs = json.load(f)
            for c in segs:
                if c.get("claim_number") == 1:
                    parts = [s.get("text", "") for s in c.get("segments", [])]
                    summary["claim1"] = " ".join(parts)[:1200]
                    break
        except Exception:
            pass

    return summary


def _build_scoring_prompt(hongan: dict, hit: dict) -> str:
    return f"""あなたは特許先行技術調査の専門家です。下記の「本願」と「候補文献」について、
本願の新規性・進歩性否定に影響しうる関連度を 0〜100 のスコアで評価してください。
スコア基準:
- 90-100: 主引例になりうる (同一技術分野+構成要素のほとんどが開示)
- 70-89:  副引例になりうる (一部構成要素が開示)
- 40-69:  周辺技術として参考になる
- 0-39:   関連性が低い

## 本願
- 発明の名称: {hongan.get("title", "")}
- 請求項1: {hongan.get("claim1", "")[:800]}

## 候補文献
- 文献番号: {hit.get("patent_id", "")}
- タイトル: {hit.get("title", "")}
- 出願人: {hit.get("applicant", "")}
- 要約: {(hit.get("abstract") or "")[:800]}
- 請求項1: {(hit.get("claim1") or "")[:800]}

## 出力 (厳密に以下の JSON のみ)
{{"score": <0-100>, "reason": "<理由を60字以内>"}}
"""


def _parse_scoring_response(raw: str):
    """Claude の応答から score, reason を抽出。"""
    try:
        # コードフェンス除去
        txt = raw.strip()
        m = re.search(r'\{[^{}]*"score"[^{}]*\}', txt, re.DOTALL)
        if not m:
            return None, txt[:200]
        data = json.loads(m.group(0))
        score = int(data.get("score"))
        reason = str(data.get("reason", ""))[:200]
        return max(0, min(100, score)), reason
    except Exception:
        return None, raw[:200]


def get_formulas_from_keyword_dict(case_id: str) -> dict:
    """Stage 3 の keyword_dictionary.json から search_formulas を取得。

    Returns:
        {"narrow": {"formula_jplatpat": "...", "formula_google_patents": "...", "description": "..."}, ...}
        取得できなければ空 dict。
    """
    case_dir = get_case_dir(case_id)
    p = case_dir / "search" / "keyword_dictionary.json"
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    sf = data.get("search_formulas") or {}
    if not isinstance(sf, dict):
        return {}
    return sf
