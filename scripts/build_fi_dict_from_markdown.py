"""Build FI A61K + A61Q dictionary JSON from a structured markdown table.

Source: c:/Users/81903/Documents/Codex/2026-04-27/a61k-a61q-https-www-j-platpat/
        a61k_a61q_fi_search_dictionary.md

Output: dictionaries/cosmetics/fi_a61_full_tree.json (compatible with fterm_dict.py format)

Markdown columns (header row):
  | FI記号 | サブクラス | 親FI | 階層深度 | 分類タイトル | 検索キー | 注記/索引 | 参照・除外 | 出典URL |
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = Path(
    r"c:/Users/81903/Documents/Codex/2026-04-27/a61k-a61q-https-www-j-platpat/"
    r"a61k_a61q_fi_search_dictionary.md"
)
OUT = REPO / "dictionaries" / "cosmetics" / "fi_a61_full_tree.json"


_FW2HW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_FW2HW_LETTERS = str.maketrans(
    "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ",
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
)


def _normalize_url(u: str) -> str:
    """Translate legacy J-PlatPat FI URLs to the actual data path.

    Legacy:  /cache/classify/patent/PMGS_HTML/jpp/FI/ja/fiList/fiListA61K8_00.html?select=202601
    Actual:  /cache/classify/patent/PMGS_HTML/common/FI/ja/202601/fiList/fiListA61K8_00.html
    """
    if not u:
        return ""
    u = u.strip()
    # Map jpp -> common/<select>
    m = re.match(
        r"(https://www\.j-platpat\.inpit\.go\.jp/cache/classify/patent/PMGS_HTML/)"
        r"jpp/FI/ja/(.+?)(?:\?select=(\d+))?$",
        u,
    )
    if m:
        base, tail, select = m.group(1), m.group(2), (m.group(3) or "202601")
        return f"{base}common/FI/ja/{select}/{tail}"
    return u


def _normalize_code(s: str) -> str:
    s = (s or "").strip().translate(_FW2HW_DIGITS).translate(_FW2HW_LETTERS)
    s = s.replace("／", "/").replace(" ", "").replace("　", "")
    return s


def _strip_brackets(s: str) -> str:
    """Remove revision-year brackets like ［２０２０．０１］ from a label."""
    s = re.sub(r"［[^］]*］", "", s or "")
    s = re.sub(r"\[[^\]]*\]", "", s)
    return s.strip()


def _split_keywords(s: str) -> list[str]:
    """検索キー column uses '；' (full-width semicolon) as the separator."""
    if not s:
        return []
    raw = re.split(r"[；;]", s)
    out: list[str] = []
    seen: set[str] = set()
    for k in raw:
        k = k.strip()
        # remove revision marks
        k = _strip_brackets(k)
        if not k:
            continue
        if k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


def parse_markdown(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        lines = f.readlines()
    saw_header = False
    saw_separator = False
    for ln in lines:
        s = ln.rstrip("\n")
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip().strip("|").split("|")]
        # Skip header / alignment rows
        if not saw_header:
            if cells and cells[0] == "FI記号":
                saw_header = True
            continue
        if not saw_separator:
            # alignment row like |---|---|---|
            if all(re.match(r"^:?-+:?$", c) for c in cells if c):
                saw_separator = True
                continue
            saw_separator = True  # tolerate missing separator
        if len(cells) < 5:
            continue
        # pad to 9
        cells = cells + [""] * (9 - len(cells))
        code, subclass, parent, depth_s, title, search_keys, notes, refs, url = cells[:9]
        code = _normalize_code(code)
        if not code:
            continue
        parent_norm = _normalize_code(parent) if parent else ""
        try:
            depth = int(depth_s)
        except Exception:
            depth = 0
        rows.append(
            {
                "code": code,
                "subclass": _normalize_code(subclass),
                "parent": parent_norm or None,
                "depth": depth,
                "title": _strip_brackets(title),
                "search_keys": _split_keywords(search_keys),
                "notes": (notes or "").strip(),
                "refs": (refs or "").strip(),
                "url": _normalize_url(url),
            }
        )
    return rows


def build_tree(rows: list[dict]) -> dict:
    nodes: dict[str, dict] = {}
    # First pass: create node skeletons
    for r in rows:
        code = r["code"]
        if code in nodes:
            # duplicate - keep first
            continue
        nodes[code] = {
            "label": r["title"],
            "definition": r["notes"],
            "related": r["refs"],
            "theme_code": "",
            "depth": r["depth"],
            "parent": r["parent"],
            "children": [],
            "examples": list(r["search_keys"]),
            "url": r["url"],
        }
    # Second pass: populate children based on parent column
    for code, node in nodes.items():
        parent = node["parent"]
        if parent and parent in nodes and parent != code:
            if code not in nodes[parent]["children"]:
                nodes[parent]["children"].append(code)

    # Reverse index: keyword -> list of codes
    reverse: dict[str, list[str]] = {}
    for r in rows:
        for kw in r["search_keys"]:
            if not kw:
                continue
            reverse.setdefault(kw, [])
            if r["code"] not in reverse[kw]:
                reverse[kw].append(r["code"])

    # Sort reverse_index by keyword for stable output
    reverse_sorted = {k: reverse[k] for k in sorted(reverse.keys())}

    return {
        "theme": "FI:A61",
        "name": "化粧品関連 FI 分類 (A61K + A61Q 全階層)",
        "generated_at": date.today().isoformat(),
        "source": "J-PlatPat FI 分類 (2026年1月版) を構造化したMarkdownから生成",
        "source_md": str(SRC),
        "source_url_pattern": (
            "https://www.j-platpat.inpit.go.jp/cache/classify/patent/"
            "PMGS_HTML/common/FI/ja/202601/fiList/fiList<CODE>.html"
        ),
        "select_version": "202601",
        "nodes": nodes,
        "reverse_index": reverse_sorted,
    }


def main() -> int:
    if not SRC.exists():
        print(f"[error] source not found: {SRC}", file=sys.stderr)
        return 1
    rows = parse_markdown(SRC)
    if not rows:
        print("[error] parsed 0 rows", file=sys.stderr)
        return 1
    tree = build_tree(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8")
    n_nodes = len(tree["nodes"])
    n_rev = len(tree["reverse_index"])
    n_a61k = sum(1 for c in tree["nodes"] if c.startswith("A61K"))
    n_a61q = sum(1 for c in tree["nodes"] if c.startswith("A61Q"))
    print(f"[ok] wrote {OUT}")
    print(f"     nodes={n_nodes}  A61K={n_a61k}  A61Q={n_a61q}  reverse_index={n_rev}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
