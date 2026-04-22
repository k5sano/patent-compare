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


_last_run_id_ts = {"ts": "", "seq": 0}


def new_run_id(formula_level: str = "custom") -> str:
    """重複しない run_id を生成。ミリ秒＋同一ミリ秒内シーケンスで保証。"""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    if ts == _last_run_id_ts["ts"]:
        _last_run_id_ts["seq"] += 1
        ts = f"{ts}{_last_run_id_ts['seq']:02d}"
    else:
        _last_run_id_ts["ts"] = ts
        _last_run_id_ts["seq"] = 0
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
                "parent_run_id": data.get("parent_run_id"),
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
    parent_run_id: Optional[str] = None,
) -> dict:
    """JplatpatHit / 各種 hit dict からランを生成して保存。

    parent_run_id: この run が別ランをコピー編集して再実行されたものであるとき
                   親ラン id を記録する。差分表示に利用。
    """
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
        "parent_run_id": parent_run_id,
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


def _hit_key(hit: dict) -> str:
    """hit の重複判定キー (patent_id ベース、dedup_key で国別正規化)。"""
    from modules.jplatpat_client import JplatpatHit
    pid = hit.get("patent_id", "") or ""
    try:
        tmp = JplatpatHit(patent_id=pid)
        return tmp.dedup_key or pid
    except Exception:
        return pid


def compute_run_diff(case_id: str, run_id: str, base_run_id: str) -> Optional[dict]:
    """2つのランの hits を比較して差分を返す。

    Returns:
        {
          "run_id": "...",                  # 比較対象 (新)
          "base_run_id": "...",             # 比較基準 (旧)
          "formula": "...",
          "base_formula": "...",
          "common": [{"patent_id": ...}, ...],     # 両方にある
          "only_new": [{"patent_id": ...}, ...],   # run_id だけにある (新規追加)
          "only_base": [{"patent_id": ...}, ...],  # base_run_id だけにある (消失)
          "summary": {"common": N, "added": N, "removed": N},
        }
        ランが見つからなければ None。
    """
    target = load_run(case_id, run_id)
    base = load_run(case_id, base_run_id)
    if not target or not base:
        return None

    target_hits = target.get("hits", []) or []
    base_hits = base.get("hits", []) or []

    target_map = {_hit_key(h): h for h in target_hits if _hit_key(h)}
    base_map = {_hit_key(h): h for h in base_hits if _hit_key(h)}

    target_keys = set(target_map.keys())
    base_keys = set(base_map.keys())

    common_keys = target_keys & base_keys
    only_new_keys = target_keys - base_keys
    only_base_keys = base_keys - target_keys

    def _slim(h: dict) -> dict:
        return {
            "patent_id": h.get("patent_id", ""),
            "title": h.get("title", ""),
            "applicant": h.get("applicant", ""),
            "publication_date": h.get("publication_date", ""),
            "screening": h.get("screening", "pending"),
            "ai_score": h.get("ai_score"),
        }

    return {
        "run_id": run_id,
        "base_run_id": base_run_id,
        "formula": target.get("formula", ""),
        "base_formula": base.get("formula", ""),
        "common": [_slim(target_map[k]) for k in common_keys],
        "only_new": [_slim(target_map[k]) for k in only_new_keys],
        "only_base": [_slim(base_map[k]) for k in only_base_keys],
        "summary": {
            "common": len(common_keys),
            "added": len(only_new_keys),
            "removed": len(only_base_keys),
        },
    }


def validate_formula(formula: str) -> dict:
    """検索式の括弧バランス・構文簡易チェック (J-PlatPat 論理式入力用)。

    J-PlatPat 構文:
      - AND: *  (または半角スペース)
      - OR : +
      - NOT: -  (半角ハイフン)
      - 優先順位変更: [ ] (大括弧、三重まで)
      - 同種キーワード群の省略: ( ) (丸括弧) 例: (a+b+c)/TX
      - 検索キーワード末尾に構造タグ必須: /TX /TI /AB /CL /FI /FT ...

    Returns:
        {"ok": bool, "errors": [str], "warnings": [str],
         "parens_balance": int, "brackets_balance": int}
    """
    errors: list = []
    warnings: list = []
    s = formula or ""

    # 丸括弧バランス
    paren_depth = 0
    paren_min = 0
    for ch in s:
        if ch in "(（":
            paren_depth += 1
        elif ch in ")）":
            paren_depth -= 1
            if paren_depth < 0:
                paren_min = min(paren_min, paren_depth)
    if paren_depth != 0:
        errors.append(f"丸括弧のバランスが崩れています (深さ {paren_depth:+d})")
    if paren_min < 0:
        errors.append("閉じ丸括弧が開き丸括弧より先に現れています")

    # 大括弧バランス
    br_depth = 0
    br_min = 0
    br_max_nest = 0
    for ch in s:
        if ch == "[":
            br_depth += 1
            br_max_nest = max(br_max_nest, br_depth)
        elif ch == "]":
            br_depth -= 1
            if br_depth < 0:
                br_min = min(br_min, br_depth)
    if br_depth != 0:
        errors.append(f"大括弧 [ ] のバランスが崩れています (深さ {br_depth:+d})")
    if br_min < 0:
        errors.append("閉じ大括弧が開き大括弧より先に現れています")
    if br_max_nest > 3:
        warnings.append(f"大括弧の入れ子が {br_max_nest} 重です (J-PlatPat は三重まで)")

    # 全角演算子警告 (J-PlatPat は半角のみサポート)
    if re.search(r'[＊＋－]', s):
        warnings.append("全角の演算子 (＊ ＋ －) が含まれます。J-PlatPat は半角 (* + -) 必須です。")
    if re.search(r'[（）]', s):
        warnings.append("全角括弧 （ ） が含まれます。半角 ( ) 推奨です。")

    # 連続演算子の簡易検出 (AND=* OR=+ NOT=- の連続)
    if re.search(r'[*+\-]\s*[*+]', s):
        errors.append("演算子が連続しています (例: *+ など)")

    # NOT (/) の古い誤用検出
    #   構造タグとして使われる /XX (大文字) はOK、
    #   分類コードのスラッシュ (B32B1/00 等) もOK、
    #   オペレータ位置で /語 のように使っていないか確認
    if re.search(r'\s/\s*\S', s) and not re.search(r'\s/[A-Z]{2,4}', s):
        warnings.append("NOT 演算子は ' / ' ではなく半角ハイフン '-' です")

    # キーワード内ハイフンの誤用検出
    #   J-PlatPat では '-' は NOT 演算子。キーワードの一部として使いたい場合は全角 '－' にする必要がある。
    #   例: "SUS-304" "フィルム-電池" は "SUS－304" "フィルム－電池" と全角化しないとエラーになる。
    word_class = r'[\w\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]'
    if re.search(rf'{word_class}-{word_class}', s):
        warnings.append(
            "キーワードの途中にある半角 '-' は NOT 演算子と解釈されます。"
            "キーワードの一部なら全角 '－' に変換してください (🔧 式を自動修正で対応)"
        )

    # 構造タグ不足の検出 (論理式入力では必須)
    #   「キーワード)」や「キーワード]」の直後に /XX が無ければ警告。
    #   分類コード (B32B1/00 等) のスラッシュは構造タグと誤判定しないよう、
    #   ')' または ']' 直後の /[A-Z]{2,4} のみを構造タグとみなす。
    tag_pattern = re.compile(r'^\s*/[A-Z]{2,4}(?:\+[A-Z]{2,4})*\b')
    close_chars = list(re.finditer(r'[)\]]', s))
    missing_tag = False
    for m in close_chars:
        after = s[m.end():m.end() + 10]
        # 次が構造タグ → OK
        if tag_pattern.match(after):
            continue
        # 次が閉じ括弧 → 上位のグルーピング、個別判定は不要
        if re.match(r'\s*[\])]', after):
            continue
        # 次が ,数字C/N, (近傍検索の第2キーワード前) → OK
        if re.match(r'\s*,\d+[CcNn],', after):
            continue
        missing_tag = True
        break
    if missing_tag:
        warnings.append(
            "キーワード括弧の直後に構造タグ (/TX /TI /AB+CL /CL /FI /FT など) が無い可能性があります"
        )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "parens_balance": paren_depth,
        "brackets_balance": br_depth,
    }


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


def get_keyword_snippets(case_id: str) -> dict:
    """Step3 の keyword_dictionary から、エディタに挿入するための語彙スニペットを返す。

    Returns:
        {
          "groups": [
            {"label": "<意味カテゴリ>",
             "terms": ["語1", "語2", ...],
             "jplatpat_group": "(語1+語2+...)"},
            ...
          ],
          "fi_codes": ["B32B1/00", ...],
          "fterm_codes": ["4F100AB01", ...],
        }
    """
    case_dir = get_case_dir(case_id)
    p = case_dir / "search" / "keyword_dictionary.json"
    snippets: dict = {"groups": [], "fi_codes": [], "fterm_codes": []}
    if not p.exists():
        return snippets
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return snippets

    # キーワード内ハイフンは J-PlatPat では NOT 扱いになるため全角 '－' に変換
    def _sanitize_keyword(term: str) -> str:
        # 両端がワード文字の '-' を '－' に変換 (反復)
        pattern = re.compile(
            r'([\w\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF])-'
            r'([\w\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF])'
        )
        prev = None
        t = term
        while prev != t:
            prev = t
            t = pattern.sub(r'\1－\2', t)
        return t

    groups_raw = data.get("keyword_groups") or data.get("groups") or []
    for g in groups_raw:
        if not isinstance(g, dict):
            continue
        label = g.get("label") or g.get("name") or g.get("category") or ""
        terms = g.get("terms") or g.get("synonyms") or g.get("keywords") or []
        terms = [str(t).strip() for t in terms if str(t).strip()]
        if not terms:
            continue
        # キーワード内 '-' を '－' に
        sanitized = [_sanitize_keyword(t) for t in terms]
        raw = "(" + "+".join(sanitized) + ")"
        snippets["groups"].append({
            "label": label,
            "terms": terms,  # 表示用は元のまま
            "terms_sanitized": sanitized,
            # 構造タグなし (他の式に組み込む用途)
            "jplatpat_group_raw": raw,
            # 既定は全文検索タグ付き (J-PlatPat 論理式入力で有効な形)
            "jplatpat_group": raw + "/TX",
        })

    fi = data.get("fi") or data.get("fi_codes") or []
    if isinstance(fi, list):
        snippets["fi_codes"] = [str(c).strip() for c in fi if str(c).strip()]

    fterm = data.get("fterm") or data.get("fterm_codes") or []
    if isinstance(fterm, list):
        snippets["fterm_codes"] = [str(c).strip() for c in fterm if str(c).strip()]

    return snippets
