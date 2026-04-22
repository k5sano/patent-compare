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


# 分野 → 辞書ファイル情報 (1 分野につき 1 つ以上のテーマを並べられる)
# 2系統サポート:
#   (a) "tree"      : {theme, nodes: {CODE: {label, examples, depth, parent, children}}, reverse_index}
#   (b) "structure" : {theme_code, theme_name, categories: {GROUP: {label, entries: {CODE: {...}}}}}
#
# (b) は load 時に (a) 形式に正規化される。
# 複数テーマの場合は nodes を全テーマ分マージし、reverse_index も統合する。
_FTERM_DICTS: dict = {
    "cosmetics": [
        {"file": "fterm_4c083_tree.json", "format": "tree"},
    ],
    "laminate": [
        # 4F100: 積層体 (B32B1/00-43/00)
        {"file": "fterm_4f100_structure.json", "format": "structure"},
        # 3E086: 被包体、包装体、容器 (B65D65/00-65/46)
        {"file": "fterm_3e086_structure.json", "format": "structure"},
    ],
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
            "theme": theme,
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
                "theme": theme,
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

def _merge_trees(trees: list) -> dict:
    """複数の tree 形式辞書を統合する。

    F-term は同じコード (例 AA01) が異なるテーマで異なる意味を持つため、
    衝突するコードは `{theme}:{code}` 形式の prefixed キーでも格納する。
    後方互換のため、unprefixed キー (例 "AA01") も維持し、最初に出現した
    テーマのノードを返す (tests/UI からの素のコード参照を壊さないため)。

    reverse_index は衝突時に prefixed 形式でコードを格納し、どのテーマの
    どのコードに該当するかを明示する。
    """
    themes = []
    theme_names = []
    code_theme_map: dict = {}  # code -> [themes]
    for t in trees:
        theme = t.get("theme") or ""
        for code in (t.get("nodes") or {}).keys():
            code_theme_map.setdefault(code, []).append(theme)

    def is_conflict(code: str) -> bool:
        return len(code_theme_map.get(code, [])) > 1

    def rev_key(code: str, theme: str) -> str:
        """reverse_index 用: 衝突時は prefixed、それ以外は素のコード。"""
        if is_conflict(code) and theme:
            return f"{theme}:{code}"
        return code

    merged_nodes: dict = {}
    merged_reverse: dict = {}
    for t in trees:
        if not t:
            continue
        theme = t.get("theme") or ""
        if theme:
            themes.append(theme)
        if t.get("theme_name"):
            theme_names.append(t["theme_name"])
        for code, node in (t.get("nodes") or {}).items():
            # parent / children は reverse_index と同じ方針で書き換え
            parent = node.get("parent")
            new_parent = rev_key(parent, theme) if parent is not None else None
            new_children = [rev_key(c, theme) for c in node.get("children", [])]
            normalized = {
                **node,
                "theme": theme,
                "parent": new_parent,
                "children": new_children,
            }
            # 常に prefixed キー ({theme}:{code}) で保存
            prefixed = f"{theme}:{code}" if theme else code
            merged_nodes[prefixed] = normalized
            # 後方互換: unprefixed キーは最初のテーマのノードを保持
            if code not in merged_nodes:
                merged_nodes[code] = normalized
        for term, codes in (t.get("reverse_index") or {}).items():
            bucket = merged_reverse.setdefault(term, [])
            for c in codes:
                nk = rev_key(c, theme)
                if nk not in bucket:
                    bucket.append(nk)
    return {
        "theme": "+".join(themes),
        "theme_name": " / ".join(theme_names),
        "themes": themes,
        "nodes": merged_nodes,
        "reverse_index": merged_reverse,
    }


def get_tree(field: str = "cosmetics") -> dict:
    infos = _FTERM_DICTS.get(field)
    if not infos:
        return {}
    # 後方互換: 単一 dict で登録された場合も list に揃える
    if isinstance(infos, dict):
        infos = [infos]
    trees = []
    for info in infos:
        raw = _load_json(_dict_path(field, info["file"]))
        if not raw:
            continue
        if info["format"] == "structure":
            trees.append(_normalize_structure_to_tree(raw))
        else:
            trees.append(raw)
    if not trees:
        return {}
    if len(trees) == 1:
        return trees[0]
    return _merge_trees(trees)


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
    # 複数テーマ統合時の重複 (unprefixed "AA01" と "4F100:AA01" の両方) を排除。
    # unprefixed キーと同じ node を持つ prefixed キーが存在する場合、prefixed 側のみ残す。
    seen_ids = set()
    lines = []
    for code in sorted(nodes.keys()):
        node = nodes[code]
        # 同じ node オブジェクトが unprefixed/prefixed 両方で参照される場合は片方のみ
        node_id = id(node)
        if node_id in seen_ids:
            continue
        seen_ids.add(node_id)
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
