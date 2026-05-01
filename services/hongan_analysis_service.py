#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""本願分析テンプレート (templates/hongan_analysis_v*.yaml) に従って、
hongan.json / segments.json / classification.json から自動取得し、
LLM 項目は Claude を 1 回呼んで JSON 一括生成する。

出力: cases/<id>/analysis/hongan_analysis.json
"""

from __future__ import annotations

import functools
import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml

from services import case_service
from services.case_service import get_case_dir, load_case_meta

logger = logging.getLogger(__name__)

# 1.3 用に使う Claude のモデル (重めの分析タスクなので Opus 4.6)
_CLAUDE_MODEL = "claude-opus-4-6"

# プロンプトに含める段落本文の合計上限 (Claude の context を圧迫しないように)
_MAX_PARA_CHARS = 60000


def _project_root() -> Path:
    return case_service.PROJECT_ROOT


def _template_path(version: str = "v0.1") -> Path:
    # ファイル名は hongan_analysis_v0.1.yaml の想定
    return _project_root() / "templates" / f"hongan_analysis_{version}.yaml"


def load_template(version: str = "v0.1") -> dict:
    """テンプレート YAML を読み込む。"""
    p = _template_path(version)
    if not p.exists():
        raise FileNotFoundError(f"テンプレートが見つかりません: {p}")
    with p.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_case_data(case_id: str) -> dict:
    """分析に使う入力データを集める。"""
    case_dir = get_case_dir(case_id)
    data: dict = {}

    hongan_path = case_dir / "hongan.json"
    if hongan_path.exists():
        with hongan_path.open(encoding="utf-8") as f:
            data["hongan"] = json.load(f)

    seg_path = case_dir / "segments.json"
    if seg_path.exists():
        with seg_path.open(encoding="utf-8") as f:
            data["segments"] = json.load(f)

    cls_path = case_dir / "search" / "classification.json"
    if cls_path.exists():
        with cls_path.open(encoding="utf-8") as f:
            data["classification"] = json.load(f)

    rel_path = case_dir / "related_paragraphs.json"
    if rel_path.exists():
        with rel_path.open(encoding="utf-8") as f:
            data["related_paragraphs"] = json.load(f)

    meta = load_case_meta(case_id) or {}
    data["meta"] = meta
    return data


@functools.lru_cache(maxsize=4)
def _load_fi_codebook(field: str) -> dict:
    """FI ツリー辞書 (dictionaries/<field>/fi_*tree.json) からコード → label の dict を返す。"""
    out: dict = {}
    fdir = _project_root() / "dictionaries" / field
    if not fdir.exists():
        return out
    for p in fdir.glob("fi_*tree.json"):
        try:
            with p.open(encoding="utf-8") as f:
                tree = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        for code, node in (tree.get("nodes") or {}).items():
            if isinstance(node, dict):
                label = (node.get("label") or "").strip()
                if label and code not in out:
                    out[code] = label
    return out


def _coerce_to_code_label(entry):
    """文字列 / dict のどちらでも (code, existing_label) のタプルに正規化。"""
    if isinstance(entry, dict):
        return (str(entry.get("code") or "").strip(),
                str(entry.get("label") or "").strip())
    return (str(entry or "").strip(), "")


def _enrich_fi_codes(codes: list, field: str) -> list[dict]:
    """FI コード列を {code, label} dict のリストに正規化＋辞書補完。

    入力は文字列 list か、既に {code, label, ...} dict の list。
    """
    book = _load_fi_codebook(field)
    out = []
    for raw in codes or []:
        c, existing_label = _coerce_to_code_label(raw)
        if not c:
            continue
        label = existing_label or book.get(c) or book.get(c.replace(" ", "")) or ""
        if not label:
            import re as _re
            m = _re.match(r"^([A-Z]\d{2}[A-Z])(\d.*)$", c.replace(" ", ""))
            if m:
                spaced = f"{m.group(1)} {m.group(2)}"
                label = book.get(spaced) or ""
        out.append({"code": c, "label": label})
    return out


def _enrich_fterm_codes(codes: list[str], field: str) -> dict:
    """F-term コードのリストを theme ごとにグルーピングし suffix + label を付ける。

    Returns:
        {
          "<theme>": {
              "theme_label": "...",
              "items": [{"code": "AA161", "label": "..."}, ...]
          }
        }
    """
    try:
        from modules.fterm_dict import get_nodes
    except ImportError:
        return {}
    nodes = {}
    try:
        nodes = get_nodes(field) or {}
    except Exception:
        nodes = {}

    grouped: dict = {}
    for raw in codes or []:
        c, existing_label = _coerce_to_code_label(raw)
        if not c:
            continue
        # F-term コード "4C083AA161" → theme="4C083", suffix="AA161"
        import re as _re
        m = _re.match(r"^(\d{1,2}[A-Z]\d{3})([A-Z]{2}\d{2,3})$", c)
        if m:
            theme, suffix = m.group(1), m.group(2)
        else:
            theme, suffix = "", c
        # 辞書 lookup (既に label 済みなら優先、そうでなければ辞書を引く)
        label = existing_label
        if not label:
            for key in (c, suffix, suffix[:-1] if len(suffix) >= 4 else None):
                if key and key in nodes:
                    label = (nodes[key].get("label") or "").strip()
                    if label:
                        break
        bucket = grouped.setdefault(theme, {"theme_label": "", "items": []})
        if not bucket["theme_label"] and theme and theme in nodes:
            bucket["theme_label"] = (nodes[theme].get("label") or "").strip()
        bucket["items"].append({"code": suffix or c, "label": label})
    return grouped


def _section_text_excerpt(hongan: dict, section_names: list[str], budget: int) -> str:
    """指定セクションに属する段落本文を結合して budget 文字以内で返す。"""
    if not hongan or not section_names:
        return ""
    paragraphs = hongan.get("paragraphs") or []
    out: list[str] = []
    used = 0
    for para in paragraphs:
        if para.get("section") not in section_names:
            continue
        line = f"【{para.get('id', '')}】{para.get('text', '')}"
        if used + len(line) > budget:
            break
        out.append(line)
        used += len(line)
    return "\n".join(out)


def _independent_claims(segments: list[dict]) -> list[dict]:
    """独立請求項のみを抽出。"""
    return [c for c in (segments or []) if c.get("is_independent")]


def _claim_tree(segments: list[dict]) -> dict:
    """親→従属請求項リストの簡易ツリー。"""
    tree: dict[int, list[int]] = {}
    for c in segments or []:
        cn = c.get("claim_number")
        for dep in c.get("dependencies") or []:
            tree.setdefault(int(dep), []).append(int(cn))
    return {str(k): sorted(v) for k, v in tree.items()}


def _resolve_auto_item(item: dict, ctx: dict) -> Any:
    """type=auto の項目を既存データから解決する。

    対応 source: meta / jplatpat_classification / claim_segmenter / paragraph_matcher
    """
    src = (item.get("source") or "").strip()
    # YAML は 2.1 を float として読み込むので文字列に正規化
    iid = str(item.get("id", "")).strip()
    meta = ctx.get("meta") or {}
    hongan = ctx.get("hongan") or {}
    classification = ctx.get("classification") or {}
    segments = ctx.get("segments") or []

    if src == "meta":
        # 2.1 出願番号・出願日・公開番号・公開日 / 2.2 出願人 / 2.3 発明者
        if iid == "2.1":
            return {
                "公開番号": hongan.get("patent_number") or meta.get("patent_number") or "",
                "公開年": meta.get("year") or "",
                "公開月": meta.get("month") or "",
                "出願番号": meta.get("application_number") or "",
                "出願日": meta.get("application_date") or "",
                "公開日": meta.get("publication_date") or "",
                "_note": "公開番号は PDF 抽出から、その他は J-PlatPat 取得が必要 (未対応分は空欄)",
            }
        if iid == "2.2":
            return {"出願人": meta.get("applicant", "")} or "未取得"
        if iid == "2.3":
            return {"発明者": meta.get("inventors", [])} or "未取得"
        return ""

    if src == "jplatpat_classification":
        ipc = classification.get("ipc") or []
        fi = classification.get("fi") or []
        fterm = classification.get("fterm") or []
        theme = classification.get("theme_codes") or classification.get("theme") or []
        meta_field = (ctx.get("meta") or {}).get("field") or "cosmetics"
        # IPC は専用辞書がないので code+既存 label のみ正規化
        ipc_norm = []
        for entry in ipc:
            c, lab = _coerce_to_code_label(entry)
            if c:
                ipc_norm.append({"code": c, "label": lab})
        # テーマコードは文字列 list を期待
        theme_norm = [str(t).strip() for t in theme if t]
        return {
            "IPC": ipc_norm,
            "FI": _enrich_fi_codes(fi, meta_field),
            "Fターム_grouped": _enrich_fterm_codes(fterm, meta_field),
            "テーマコード": theme_norm,
        }

    if src == "claim_segmenter":
        if iid == "4.1":
            return [
                {
                    "claim_number": c.get("claim_number"),
                    "category": c.get("category", ""),
                    "first_segment": (c.get("segments") or [{}])[0].get("text", "")[:80],
                }
                for c in _independent_claims(segments)
            ]
        if iid == "4.2":
            return _claim_tree(segments)
        if iid == "4.3":
            return [
                {
                    "claim_number": c.get("claim_number"),
                    "is_independent": c.get("is_independent", False),
                    "segments": [
                        {"id": s.get("id"), "text": s.get("text", "")}
                        for s in (c.get("segments") or [])
                    ],
                }
                for c in (segments or [])
            ]
        return ""

    if src == "paragraph_matcher":
        # 4.5 各構成要素の明細書上の根拠箇所
        # related_paragraphs.json (services.case_service.compute_related_paragraphs)
        return ctx.get("related_paragraphs") or {}

    return ""


def _build_llm_prompt(template: dict, ctx: dict, llm_item_ids: list[str]) -> str:
    """LLM 項目を一括生成するためのプロンプト。"""
    hongan = ctx.get("hongan") or {}
    segments = ctx.get("segments") or []
    title = hongan.get("patent_title") or ""
    pn = hongan.get("patent_number") or ""

    # 請求項の構成要件 (Step 2 分節)
    seg_lines = ["【独立請求項の構成要件】"]
    for c in _independent_claims(segments):
        seg_lines.append(f"請求項{c.get('claim_number')}:")
        for s in c.get("segments") or []:
            seg_lines.append(f"  - {s.get('id')}: {s.get('text')}")
    seg_block = "\n".join(seg_lines) if len(seg_lines) > 1 else "(請求項データなし)"

    # 各セクション本文 (予算内で抜粋)
    section_groups = {
        "背景技術": _section_text_excerpt(hongan, ["背景技術"], 8000),
        "課題": _section_text_excerpt(hongan,
            ["発明が解決しようとする課題", "課題", "解決しようとする課題"], 6000),
        "手段": _section_text_excerpt(hongan,
            ["課題を解決するための手段", "手段"], 8000),
        "効果": _section_text_excerpt(hongan, ["発明の効果", "効果"], 5000),
        "実施形態": _section_text_excerpt(hongan,
            ["発明を実施するための形態", "実施形態"], 16000),
        "実施例": _section_text_excerpt(hongan, ["実施例", "比較例"], 12000),
    }

    # LLM 項目の説明セット (テンプレートから動的に組み立て)
    llm_id_set = set(llm_item_ids)
    item_specs = []
    for sec in template.get("sections") or []:
        for it in sec.get("items") or []:
            iid = str(it.get("id", "")).strip()
            if iid not in llm_id_set:
                continue
            label = it.get("label", "")
            desc = (it.get("description") or "").strip().replace("\n", " ")
            item_specs.append(f'  "{iid}": "<{label}{(": " + desc) if desc else ""}>"')
    items_json_skeleton = "{\n" + ",\n".join(item_specs) + "\n}"

    excerpt_lines = []
    for name, body in section_groups.items():
        if body.strip():
            excerpt_lines.append(f"【{name}セクション本文 (抜粋)】\n{body}")
    excerpt_block = "\n\n".join(excerpt_lines) or "(明細書本文なし)"

    return (
        f"あなたは特許サーチャーのアシスタントです。以下の本願明細書 ({pn} {title}) を読み、\n"
        f"指定の項目について端的に日本語で回答してください。\n"
        f"出力は **JSON オブジェクト 1 個のみ** で、各キーが項目 ID、値が回答 (string か array of string) です。\n"
        f"前置きや説明は不要、JSON のみを出力してください。\n\n"
        f"---- 本願の請求項 ----\n{seg_block}\n\n"
        f"---- 明細書本文の抜粋 ----\n{excerpt_block}\n\n"
        f"---- 出力すべき項目 ----\n"
        f"以下のキーをすべて含む JSON を返してください。各値は項目の趣旨に沿って簡潔に記述します。\n"
        f"配列が自然な項目 (キーワード列挙等) は array of string にしてください。\n\n"
        f"{items_json_skeleton}\n"
    )


def _extract_json_from_response(raw: str) -> dict:
    """Claude の応答から JSON オブジェクトを抜き出す。"""
    if not raw:
        return {}
    s = raw.strip()
    # コードフェンス除去
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.MULTILINE)
    s = re.sub(r"\s*```$", "", s, flags=re.MULTILINE)
    # 最初の { から最後の } までを切り出し (リーズナブルな緩和)
    first = s.find("{")
    last = s.rfind("}")
    if first == -1 or last == -1 or last <= first:
        return {}
    candidate = s[first:last + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return {}


def _collect_llm_item_ids(template: dict) -> list[str]:
    ids = []
    for sec in template.get("sections") or []:
        for it in sec.get("items") or []:
            if it.get("type") == "llm":
                # YAML が float としてパースした iid を文字列化
                ids.append(str(it.get("id", "")).strip())
    return [i for i in ids if i]


def _build_skeleton(template: dict, ctx: dict, llm_results: dict) -> dict:
    """テンプレート構造を保ちつつ、各 item に value/type を埋めた dict を返す。"""
    sections_out = []
    for sec in template.get("sections") or []:
        items_out = []
        for it in sec.get("items") or []:
            iid = str(it.get("id", "")).strip()  # YAML float → str 正規化
            entry = {
                "id": iid,
                "label": it.get("label", ""),
                "description": it.get("description", ""),
                "type": it.get("type", "manual"),
                "value": None,
            }
            t = it.get("type")
            if t == "auto":
                entry["value"] = _resolve_auto_item(it, ctx)
                entry["source"] = it.get("source", "")
            elif t == "llm":
                # llm_results は文字列キー前提
                entry["value"] = llm_results.get(iid)
            elif t == "manual":
                entry["value"] = None  # UI で入力
            items_out.append(entry)
        sections_out.append({
            "id": sec.get("id"),
            "title": sec.get("title", ""),
            "description": sec.get("description", ""),
            "items": items_out,
        })
    return {
        "template_id": template.get("template_id"),
        "version": template.get("version"),
        "sections": sections_out,
    }


def _output_path(case_id: str) -> Path:
    return get_case_dir(case_id) / "analysis" / "hongan_analysis.json"


def load_existing_analysis(case_id: str):
    """保存済みの分析結果を返す。無ければ exists=False。"""
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404
    p = _output_path(case_id)
    if not p.exists():
        return {"exists": False}, 200
    try:
        with p.open(encoding="utf-8") as f:
            return {"exists": True, "data": json.load(f)}, 200
    except (OSError, json.JSONDecodeError) as e:
        return {"error": f"読み込みエラー: {e}"}, 500


def run_analysis(case_id: str, version: str = "v0.1",
                 skip_llm: bool = False, claude_timeout: int = 300):
    """テンプレートを実行して分析結果を保存・返却する。

    skip_llm=True の場合は auto 項目だけ埋めて LLM はスキップ (テスト/プレビュー用)。
    """
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404

    try:
        template = load_template(version)
    except FileNotFoundError as e:
        return {"error": str(e)}, 400

    ctx = _load_case_data(case_id)
    if not ctx.get("hongan"):
        return {
            "error": "hongan.json がありません。Step 1 で本願 PDF を読み込んでください。",
        }, 400

    llm_ids = _collect_llm_item_ids(template)
    llm_results: dict = {}
    llm_error: str | None = None

    if llm_ids and not skip_llm:
        prompt = _build_llm_prompt(template, ctx, llm_ids)
        try:
            from modules.claude_client import call_claude, ClaudeClientError
        except ImportError as e:
            llm_error = f"claude_client import 失敗: {e}"
        else:
            try:
                raw = call_claude(prompt, timeout=claude_timeout, model=_CLAUDE_MODEL)
                llm_results = _extract_json_from_response(raw)
                if not llm_results:
                    llm_error = "LLM 応答から JSON を抽出できませんでした"
            except ClaudeClientError as e:
                llm_error = f"Claude 呼び出しエラー: {e}"
            except Exception as e:
                llm_error = f"想定外のエラー: {e}"

    result = _build_skeleton(template, ctx, llm_results)

    out_path = _output_path(case_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    response = {
        "success": True,
        "data": result,
        "llm_item_count": len(llm_ids),
        "llm_filled_count": sum(1 for k in llm_ids if llm_results.get(k)),
        "saved_to": str(out_path.relative_to(_project_root())) if out_path.is_relative_to(_project_root()) else str(out_path),
    }
    if llm_error:
        response["llm_error"] = llm_error
    return response, 200
