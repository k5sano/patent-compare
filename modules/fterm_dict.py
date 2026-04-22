#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
辞書アクセス層 - fterm_dict.py
全モジュールはここ経由で辞書にアクセスする。
直接 JSON ファイルを open するのはこのモジュールだけ。
"""
import json
import functools
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).parent.parent.resolve()


@functools.lru_cache(maxsize=8)
def _load_json(path_str: str):
    p = Path(path_str)
    if not p.exists():
        logger.warning("辞書ファイルが見つかりません: %s", path_str)
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _dict_path(field: str, name: str) -> str:
    return str(PROJECT_ROOT / "dictionaries" / field / name)


# 分野 → 辞書ファイル名のマッピング
# 2系統サポート:
#   (a) "tree"      : {theme, nodes: {CODE: {label, examples, depth, parent, children}}, reverse_index}
#   (b) "structure" : {theme_code, theme_name, categories: {GROUP: {label, entries: {CODE: {...}}}}}
#
# (b) はスケルトン定義用で、load 時に (a) 形式に正規化される。
_FTERM_DICTS: dict = {
    "cosmetics": {"file": "fterm_4c083_tree.json", "format": "tree"},
    "laminate": {"file": "fterm_4f100_structure.json", "format": "structure"},
}


def _normalize_structure_to_tree(raw: dict) -> dict:
    """カテゴリ型のスケルトン辞書を tree 形式に正規化して返す。

    structure:
        { theme_code, theme_name, categories: {GROUP: {label, entries: {CODE: {label, examples}}}} }
    -> tree:
        { theme, theme_name, nodes: {CODE: {label, examples, depth, parent, children}}, reverse_index }
    """
    theme = raw.get("theme_code") or raw.get("theme") or ""
    theme_name = raw.get("theme_name") or ""
    nodes: dict = {}
    reverse: dict = {}

    for group_code, group in (raw.get("categories") or {}).items():
        group_label = group.get("label", "")
        # グループノード（depth=1）
        nodes[group_code] = {
            "label": group_label,
            "examples": [],
            "depth": 1,
            "parent": None,
            "children": [],
            "note": group.get("note", ""),
        }
        if group_label:
            reverse.setdefault(group_label, []).append(group_code)

        entries = group.get("entries") or {}
        for code, entry in entries.items():
            label = entry.get("label", "")
            examples = entry.get("examples") or []
            nodes[code] = {
                "label": label,
                "examples": examples,
                "depth": 2,
                "parent": group_code,
                "children": [],
                "note": entry.get("note", ""),
            }
            nodes[group_code]["children"].append(code)
            # reverse_index は重複コード登録を避ける
            seen_terms: set = set()
            for term in [label] + list(examples):
                if not term or term in seen_terms:
                    continue
                seen_terms.add(term)
                bucket = reverse.setdefault(term, [])
                if code not in bucket:
                    bucket.append(code)

    return {
        "theme": theme,
        "theme_name": theme_name,
        "nodes": nodes,
        "reverse_index": reverse,
    }


# ── ツリー辞書（統一アクセサ） ─────────────────────────

def get_tree(field: str = "cosmetics") -> dict:
    info = _FTERM_DICTS.get(field)
    if not info:
        return {}
    raw = _load_json(_dict_path(field, info["file"]))
    if not raw:
        return {}
    if info["format"] == "structure":
        return _normalize_structure_to_tree(raw)
    return raw


def get_nodes(field: str = "cosmetics") -> dict:
    return get_tree(field).get("nodes", {})


def get_reverse_index(field: str = "cosmetics") -> dict:
    return get_tree(field).get("reverse_index", {})


def codes_for_term(term: str, field: str = "cosmetics") -> list:
    return get_reverse_index(field).get(term, [])


def get_ancestors(code: str, field: str = "cosmetics") -> list:
    nodes = get_nodes(field)
    ancestors = []
    cur = code
    while True:
        node = nodes.get(cur)
        if not node:
            break
        parent = node.get("parent")
        if not parent:
            break
        ancestors.append(parent)
        cur = parent
    return ancestors


def get_siblings(code: str, field: str = "cosmetics") -> list:
    nodes = get_nodes(field)
    node = nodes.get(code, {})
    parent = node.get("parent")
    if not parent:
        return [code]
    return nodes.get(parent, {}).get("children", [])


def expand_term(term: str, field: str = "cosmetics") -> dict:
    """用語から関連コード・兄弟語・上位語・例示語を展開して返す"""
    nodes = get_nodes(field)
    codes = codes_for_term(term, field)
    if not codes:
        return {"codes": [], "labels": [], "siblings": [], "ancestors": [], "examples": []}

    code = codes[0]
    node = nodes.get(code, {})
    labels = [nodes[c]["label"] for c in codes if c in nodes]
    examples = [ex for ex in node.get("examples", []) if ex != term]
    sibling_codes = get_siblings(code, field)
    sibling_examples = [
        ex
        for sc in sibling_codes
        for ex in nodes.get(sc, {}).get("examples", [])
        if ex != term
    ]
    ancestor_labels = [
        nodes[a]["label"]
        for a in get_ancestors(code, field)
        if a in nodes
    ]
    return {
        "codes": codes,
        "labels": labels,
        "siblings": sibling_examples[:10],
        "ancestors": ancestor_labels,
        "examples": examples[:10],
    }


# ── 補助辞書 ────────────────────────────────────────────────────

def get_synonyms(field: str = "cosmetics") -> dict:
    return _load_json(_dict_path(field, "synonyms.json"))


def get_inci(field: str = "cosmetics") -> dict:
    return _load_json(_dict_path(field, "inci_ja.json"))


def get_brand_names(field: str = "cosmetics") -> dict:
    return _load_json(_dict_path(field, "brand_names.json"))


def all_tree_keys(field: str = "cosmetics") -> list:
    """Step 3 AI プロンプト用: ノードラベル + reverse_index キーの結合リスト"""
    nodes = get_nodes(field)
    rev = get_reverse_index(field)
    keys = set(rev.keys())
    for node in nodes.values():
        keys.add(node.get("label", ""))
    keys.discard("")
    return sorted(keys)


def build_digest(field: str = "cosmetics", max_examples: int = 4) -> str:
    """Fterm木構造をAIプロンプト用にコンパクトな1行1ノードのテキストに縮約する。

    出力例:
        AC18: POA付加体 (例: ポリオキシエチレンオクチルドデシルエーテル, ...)
        AD04: ポリアルキレンオキシド (例: ポリエチレングリコール, PEG, ...)

    Returns:
        str: ダイジェストテキスト
    """
    nodes = get_nodes(field)
    if not nodes:
        return ""
    lines = []
    for code in sorted(nodes.keys()):
        node = nodes[code]
        label = node.get("label", "")
        if not label:
            continue
        examples = node.get("examples", [])
        if examples:
            ex_str = ", ".join(examples[:max_examples])
            if len(examples) > max_examples:
                ex_str += ", ..."
            lines.append(f"{code}: {label} (例: {ex_str})")
        else:
            lines.append(f"{code}: {label}")
    return "\n".join(lines)
