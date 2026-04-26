# -*- coding: utf-8 -*-
"""J-PlatPat FI ハンドブック (HB) ページから化粧品関連 (A61K8 + A61Q 全主要分類)
の完全階層を抽出して構造化 JSON 辞書を生成する。

出力: dictionaries/cosmetics/fi_a61_cosmetics_tree.json (既存 fterm 辞書と同形式)

使い方:
    python scripts/scrape_fi_a61_cosmetics.py [--out path]

戦略:
    1. A61K のメイングループ一覧 (fiMainGroupA61K.html) は subclass トップだけ取得
    2. A61K8/00 の HB (p1102/HANDBOOK/A61K8_00/ja/FI/202601) から
       ドット (depth) + コード + 説明 + 補足説明 + テーマコード を全抽出
    3. A61Q の各メインに対しても同様に HB ページを取る
    4. 親子関係はドット (depth) スタックで再構築

HB ページ構造:
    テーブル: [No, FI/ファセット, ドット, 説明, 補足説明, 関連分野, テーマコード]
"""

from __future__ import annotations
import argparse
import json
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

URL_BASE_FI = "https://www.j-platpat.inpit.go.jp/cache/classify/patent/PMGS_HTML/jpp/FI/ja"
URL_BASE_HB = "https://www.j-platpat.inpit.go.jp/p1102/HANDBOOK"
SELECT = "202601"  # 2026 年 1 月時点


def _ja_norm(s: str) -> str:
    """全角空白・タブ・改行を整理"""
    if not s:
        return ""
    s = s.replace("　", " ").replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def _parse_hb_table(page) -> list[dict]:
    """HB ページのテーブルを行ごとに抽出する。

    HB ページの DOM は table 要素内に <thead> + <tbody> 構造。各行 (tr) には
    cells = [No, code, depth, label, supplement, related, theme] が入る。
    DOM で拾えないケースに備えて innerText フォールバックも実装する。
    """
    # Strategy 1: rows from table[role="grid"] or table.tableSty etc.
    rows_raw = page.evaluate("""() => {
      const tables = Array.from(document.querySelectorAll('table'));
      if (!tables.length) return [];
      // 最大の table を選ぶ
      let best = null, bestCount = 0;
      for (const t of tables) {
        const trs = t.querySelectorAll('tr');
        if (trs.length > bestCount) { best = t; bestCount = trs.length; }
      }
      if (!best) return [];
      const out = [];
      for (const tr of best.querySelectorAll('tr')) {
        const tds = Array.from(tr.querySelectorAll('td, th'));
        out.push(tds.map(td => (td.innerText || td.textContent || '').replace(/\\s+/g, ' ').trim()));
      }
      return out;
    }""")

    items = []
    code_re = re.compile(r"^[A-H]\d{2}[A-Z]\s*\d+/\d+(?:[A-Z]?)?$")

    for cells in rows_raw:
        if not cells or len(cells) < 4:
            continue
        # ヘッダ行は cells[0] が "No." 等で数字でない。スキップ。
        first = cells[0].strip()
        if not first or not first[0].isdigit():
            continue
        # cells[1] = code, cells[2] = depth, cells[3] = label
        code = cells[1].strip().replace(" ", "")
        if not code_re.match(code):
            continue
        depth_raw = cells[2].strip()
        try:
            depth = int(re.sub(r"\D", "", depth_raw) or "0")
        except ValueError:
            depth = 0
        label = _ja_norm(cells[3]) if len(cells) > 3 else ""
        definition = ""
        related = ""
        theme_code = ""
        if len(cells) > 4:
            d = _ja_norm(cells[4])
            if d and d != "-":
                definition = d
        if len(cells) > 5:
            r = _ja_norm(cells[5])
            if r and r != "-":
                related = r
        if len(cells) > 6:
            t = _ja_norm(cells[6])
            if t and re.match(r"\d[A-Z]\d{3}", t):
                theme_code = t
        items.append({
            "code": code, "depth": depth, "label": label,
            "definition": definition, "related": related,
            "theme_code": theme_code,
        })

    if items:
        return items

    # Fallback: innerText を行ベースで解析
    text = page.evaluate("() => document.body.innerText") or ""
    lines = [_ja_norm(l) for l in text.split("\n")]
    # パターン: 連番(数字) / コード / ドット / 説明... の繰り返し
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        if line.isdigit() and int(line) >= 1 and int(line) < 1000:
            # 次の数行: code, depth, label, supplement?, related?, theme?
            chunks = []
            j = i + 1
            while j < n and len(chunks) < 8:
                lj = lines[j]
                if lj:
                    chunks.append(lj)
                j += 1
                # 次の連番が出てきたら break
                if lj.isdigit() and 1 <= int(lj) < 1000 and len(chunks) >= 4:
                    chunks.pop()
                    break
            if len(chunks) >= 3 and code_re.match(chunks[0]):
                code = chunks[0].replace(" ", "")
                try:
                    depth = int(chunks[1])
                except ValueError:
                    depth = 0
                label = chunks[2] if len(chunks) > 2 else ""
                definition = ""
                related = ""
                theme_code = ""
                # supplement / related / theme は順に来るが - で省略される
                rest = chunks[3:]
                if rest:
                    if rest[0] != "-":
                        definition = rest[0]
                    if len(rest) > 1 and rest[1] != "-":
                        related = rest[1]
                    if len(rest) > 2 and re.match(r"\d[A-Z]\d{3}", rest[2]):
                        theme_code = rest[2]
                items.append({
                    "code": code, "depth": depth, "label": label,
                    "definition": definition, "related": related,
                    "theme_code": theme_code,
                })
            i = j
        else:
            i += 1
    return items


def _fetch_hb(page, code_url_part: str) -> list[dict]:
    """HB (handbook) ページを開いて全行を抽出。

    code_url_part: "A61K8_00" / "A61Q1_00" 等
    """
    url = f"{URL_BASE_HB}/{code_url_part}/ja/FI/{SELECT}"
    print(f"  fetching {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1500)
    return _parse_hb_table(page)


def _fetch_main_group_codes(page, subclass: str) -> list[dict]:
    """A61K / A61Q 等のメイングループ一覧を fiMainGroup ページから取得。

    Returns:
        [{"code": "A61Q1/00", "label": "..."}, ...]
    """
    url = f"{URL_BASE_FI}/fiMainGroup/fiMainGroup{subclass}.html?select={SELECT}"
    print(f"  fetching {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1500)
    text = page.evaluate("() => document.body.innerText") or ""
    lines = [_ja_norm(l) for l in text.split("\n") if _ja_norm(l)]
    code_re = re.compile(r"^[A-H]\d{2}[A-Z]\s*\d+/\d+$")
    items = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if code_re.match(line):
            code = re.sub(r"\s+", "", line)
            label_chunks = []
            j = i + 1
            scanned = 0
            while j < len(lines) and scanned < 6:
                l2 = lines[j]
                if l2 in ("HB", "CC", "(注)/(索引)", "移行先", "閉じる", "English"):
                    j += 1
                    scanned += 1
                    continue
                if re.match(r"^[34][A-Z]\d{3}$", l2):
                    j += 1
                    scanned += 1
                    continue
                if code_re.match(l2):
                    break
                label_chunks.append(l2)
                j += 1
                scanned += 1
                if len(l2) > 2:
                    break
            label = " ".join(label_chunks).strip()
            items.append({"code": code, "label": label})
            i = j
        else:
            i += 1
    return items


def _to_url_part(code: str) -> str:
    """A61K8/00 → A61K8_00"""
    return code.replace("/", "_").replace(" ", "")


def scrape() -> dict:
    from playwright.sync_api import sync_playwright

    nodes: dict[str, dict] = {}
    reverse_index: dict[str, list[str]] = {}

    def add_node(code: str, label: str, depth: int, parent: str | None,
                  definition: str = "", related: str = "", theme_code: str = ""):
        if code in nodes:
            # 重複したら情報量が多い方を残す
            cur = nodes[code]
            if len(label) > len(cur.get("label", "")):
                cur["label"] = label
            if definition and not cur.get("definition"):
                cur["definition"] = definition
            if related and not cur.get("related"):
                cur["related"] = related
            if theme_code and not cur.get("theme_code"):
                cur["theme_code"] = theme_code
            return
        nodes[code] = {
            "label": label,
            "definition": definition,
            "related": related,
            "theme_code": theme_code,
            "depth": depth,
            "parent": parent,
            "children": [],
            "examples": [],
        }
        if parent and parent in nodes:
            if code not in nodes[parent]["children"]:
                nodes[parent]["children"].append(code)

    def _enroll(code: str, label: str):
        for tok in re.findall(r"[一-龥ぁ-んァ-ヶ]{2,}|[A-Za-z]{3,}", label):
            reverse_index.setdefault(tok, [])
            if code not in reverse_index[tok]:
                reverse_index[tok].append(code)

    def _absorb_hb_items(items: list[dict], root_code: str, root_parent: str):
        """HB 行リストをドットスタック方式で親子関係を再構築しながら nodes に追加する。

        root_code: そのページの根コード (例 'A61K8/00')
        root_parent: ルートの上位 (例 'A61K')
        """
        # ドット 0 の行が root。それ以降はドット数で親を決定。
        stack: list[tuple[int, str]] = []  # (depth, code)
        for it in items:
            code = it["code"]
            depth = it["depth"]
            # スタックを掃除して直近 < depth な親を見つける
            while stack and stack[-1][0] >= depth:
                stack.pop()
            parent = stack[-1][1] if stack else root_parent
            add_node(code, it["label"], depth + 1,  # サブクラス自体を depth=0 にしたいので +1
                     parent, it.get("definition", ""), it.get("related", ""),
                     it.get("theme_code", ""))
            _enroll(code, it["label"])
            stack.append((depth, code))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                     args=["--disable-dev-shm-usage", "--disable-gpu"])
        try:
            ctx = browser.new_context(locale="ja-JP",
                                       viewport={"width": 1280, "height": 900})
            page = ctx.new_page()

            # ===== サブクラス自体をルートに =====
            add_node("A61K", "医薬用，歯科用又は化粧用製剤", 0, None)
            add_node("A61Q", "化粧品または類似化粧品製剤の特定の用途", 0, None)

            # ===== A61K のメイングループのうち化粧品関連 (主に A61K8) を HB drill =====
            print("[1/3] A61K8/00 HB (full hierarchy)")
            time.sleep(0.5)
            try:
                items = _fetch_hb(page, "A61K8_00")
                _absorb_hb_items(items, "A61K8/00", "A61K")
                print(f"  → {len(items)} rows extracted")
            except Exception as e:
                print(f"  ! A61K8 HB failed: {e}")

            # A61K の他のメイン (記録用にメインのみ取得、化粧品周辺の歯科 6/00 等)
            print("[2/3] A61K main group catalog (top-level only, for reference)")
            time.sleep(0.4)
            try:
                a61k_main = _fetch_main_group_codes(page, "A61K")
                for it in a61k_main:
                    if not it["code"].startswith("A61K"):
                        continue
                    if it["code"].startswith("A61K8"):
                        continue  # 詳細は HB で取った
                    add_node(it["code"], it["label"], 1, "A61K")
                    _enroll(it["code"], it["label"])
            except Exception as e:
                print(f"  ! A61K main failed: {e}")

            # ===== A61Q メイングループ → 各メインの HB を順次抽出 =====
            print("[3/3] A61Q main groups + each main HB drill")
            time.sleep(0.4)
            try:
                a61q_main = _fetch_main_group_codes(page, "A61Q")
            except Exception as e:
                print(f"  ! A61Q main failed: {e}")
                a61q_main = []
            for it in a61q_main:
                code = it["code"]
                if not code.startswith("A61Q") or not code.endswith("/00"):
                    continue
                url_part = _to_url_part(code)
                time.sleep(0.4)
                try:
                    items = _fetch_hb(page, url_part)
                    _absorb_hb_items(items, code, "A61Q")
                    print(f"  → {code}: {len(items)} rows")
                except Exception as e:
                    print(f"  ! {code} HB failed: {e}")
        finally:
            browser.close()

    return {
        "theme": "FI:A61",
        "name": "化粧品関連 FI 分類 (A61K8 + A61Q)",
        "generated_at": time.strftime("%Y-%m-%d"),
        "source": "https://www.j-platpat.inpit.go.jp/p1102/HANDBOOK/{CODE}/ja/FI/{SELECT}",
        "select_version": SELECT,
        "nodes": nodes,
        "reverse_index": reverse_index,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(
        PROJECT_ROOT / "dictionaries" / "cosmetics" / "fi_a61_cosmetics_tree.json"))
    args = ap.parse_args()

    print(f"Scraping FI HB for A61K8 + A61Q (select={SELECT}) ...")
    data = scrape()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n✅ saved: {out_path}")
    print(f"   nodes: {len(data['nodes'])}")
    print(f"   reverse_index entries: {len(data['reverse_index'])}")


if __name__ == "__main__":
    main()
