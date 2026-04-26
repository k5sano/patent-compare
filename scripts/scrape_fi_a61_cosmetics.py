# -*- coding: utf-8 -*-
"""J-PlatPat FI 分類ページから化粧品関連 (A61K8 + A61Q 全主要分類) を抽出して
構造化 JSON 辞書を生成する。

出力: dictionaries/cosmetics/fi_a61_cosmetics_tree.json (既存 fterm 辞書と同形式)

使い方:
    python scripts/scrape_fi_a61_cosmetics.py [--out path]

戦略:
    1. A61K のメイングループ一覧から A61K8/00 の fiList URL を取得
    2. A61K8/00 の fiList ページから全サブコード + 階層を抽出
    3. A61Q のメイングループ一覧を取得 → 各メインの fiList を順次抽出
    4. 全コード + 説明 + 階層 (depth) + parent をマージして JSON 出力

階層判定:
    fiList ページのコード行は「A61K 8/01」のように番号で表示され、
    前段にドット ("・", "・・") があるとサブ階層。これを depth に変換する。
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

URL_BASE = "https://www.j-platpat.inpit.go.jp/cache/classify/patent/PMGS_HTML/jpp/FI/ja"
SELECT = "202601"  # 2026 年 1 月時点


def _ja_norm(s: str) -> str:
    """全角空白・タブ・改行を整理"""
    s = s.replace("　", " ").replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def _depth_from_dots(label: str) -> tuple[int, str]:
    """ラベル先頭の 中黒 ("・") の数で depth を決め、ラベル本体を返す。

    例: "・・水溶性化合物" → depth=2, "水溶性化合物"
    """
    m = re.match(r"^([・]+)\s*(.*)$", label)
    if not m:
        return 0, label
    return len(m.group(1)), m.group(2).strip()


def _clean_label(label: str) -> str:
    """テーブル右側の付随セル (HB / CC / テーマコード 4桁) や (注)/(索引) 等の
    フラグメントが連結している場合、ラベル本体だけ残すように掃除する。"""
    s = label
    # 末尾の "HB CC 4C083" 系を削除
    s = re.sub(r"\s+HB\s+CC\s+\d[A-Z]\d{3}\s*$", "", s)
    s = re.sub(r"\s+HB\s+CC\s*$", "", s)
    s = re.sub(r"\s+\(注\)/\(索引\)\s*$", "", s)
    s = re.sub(r"\s+\(注\)\s*$", "", s)
    s = re.sub(r"\s+\d[A-Z]\d{3}\s*$", "", s)  # 末尾のテーマコード単独
    return s.strip()


def _parse_filist_page(page) -> list[dict]:
    """fiList ページから {code, label, depth_dots, line_text} のリストを抽出する。

    DOM は J-PlatPat 独特なのでテキストベースで安全に拾う。各行は通常
    [コード セル][説明セル][HB][CC][テーマ] のテーブル行で、コードセル直下に
    ドット付き説明が続く。テーブル全体を innerText で取り改行で split。
    """
    text = page.evaluate("() => document.body.innerText") or ""
    lines = [_ja_norm(l) for l in text.split("\n") if _ja_norm(l)]

    # コード行の正規表現: A61K 8/00 / A61K8/01 / A61Q 1/00 等
    code_re = re.compile(r"^[A-H]\d{2}[A-Z]\s*\d+/\d+(?:[A-Z]?)?$")

    items: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if code_re.match(line):
            code = re.sub(r"\s+", "", line).rstrip("@")  # 末尾アット記号があれば落とす
            # 直後の数行のうち、説明文っぽいものを拾う (HB/CC/テーマコードはスキップ)
            label_chunks = []
            j = i + 1
            scanned = 0
            while j < len(lines) and scanned < 6:
                l2 = lines[j]
                # スキップすべき行
                if l2 in ("HB", "CC", "(注)/(索引)") or re.match(r"^[34][A-Z]\d{3}$", l2):
                    j += 1
                    scanned += 1
                    continue
                # 次のコード行が来たら説明集めおわり
                if code_re.match(l2):
                    break
                # 「移行先」「閉じる」等のメニュー文言は無視
                if l2 in ("移行先", "閉じる", "English"):
                    j += 1
                    scanned += 1
                    continue
                label_chunks.append(l2)
                j += 1
                scanned += 1
                # 1 行で十分なケースが多いので、説明っぽい行を 1 つ取れたら抜ける
                if len(l2) > 2:
                    break
            label_full = " ".join(label_chunks).strip()
            depth_dots, label_body = _depth_from_dots(label_full)
            items.append({
                "code": code,
                "label": _clean_label(label_body),
                "raw_label": label_full,
                "depth_dots": depth_dots,
            })
            i = j
        else:
            i += 1
    return items


def _fetch_filist(page, code_url_part: str) -> list[dict]:
    """fiList<X>.html を開いて項目リストを返す。

    code_url_part: 例 "A61K8_00" / "A61Q1_00"
    """
    url = f"{URL_BASE}/fiList/fiList{code_url_part}.html?select={SELECT}"
    print(f"  fetching {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1500)
    return _parse_filist_page(page)


def _fetch_main_group_codes(page, subclass: str) -> list[dict]:
    """A61K / A61Q 等のメイングループ一覧 (例: A61Q1/00, A61Q3/00, ...) を抽出する。

    Returns:
        [{"code": "A61Q1/00", "label": "メイクアップ剤..."}, ...]
    """
    url = f"{URL_BASE}/fiMainGroup/fiMainGroup{subclass}.html?select={SELECT}"
    print(f"  fetching {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1500)
    return _parse_filist_page(page)  # 同じ抽出で OK


def _to_url_part(code: str) -> str:
    """A61K8/00 → A61K8_00 のように URL 用に変換"""
    return code.replace("/", "_").replace(" ", "")


def scrape() -> dict:
    from playwright.sync_api import sync_playwright

    nodes: dict[str, dict] = {}
    reverse_index: dict[str, list[str]] = {}

    def add_node(code: str, label: str, depth: int, parent: str | None):
        if code in nodes:
            # 重複したら label がより詳しい方を残す
            if len(label) > len(nodes[code].get("label", "")):
                nodes[code]["label"] = label
            return
        nodes[code] = {
            "label": label,
            "definition": "",
            "depth": depth,
            "parent": parent,
            "children": [],
            "examples": [],
        }
        if parent and parent in nodes:
            if code not in nodes[parent]["children"]:
                nodes[parent]["children"].append(code)

    def _enroll(code: str, label: str):
        # reverse_index にラベル単語を登録 (簡易)
        for tok in re.findall(r"[一-龥ぁ-んァ-ヶ]{2,}|[A-Za-z]{3,}", label):
            reverse_index.setdefault(tok, [])
            if code not in reverse_index[tok]:
                reverse_index[tok].append(code)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                     args=["--disable-dev-shm-usage", "--disable-gpu"])
        try:
            ctx = browser.new_context(locale="ja-JP",
                                       viewport={"width": 1280, "height": 900})
            page = ctx.new_page()

            # ===== A61K のメイングループ → A61K8/00 だけ詳細展開 =====
            print("[1/3] A61K main group page")
            a61k_main = _fetch_main_group_codes(page, "A61K")
            # サブクラス自体をルートに
            add_node("A61K", "医薬用，歯科用又は化粧用製剤", 0, None)
            for it in a61k_main:
                code, label = it["code"], it["label"]
                if code.startswith("A61K"):
                    # A61K8/00 のみ「化粧品関連メイングループ」として登録、他は skip
                    add_node(code, label, 1, "A61K")
                    _enroll(code, label)

            # A61K8/00 の詳細
            print("[2/3] A61K8/00 fiList (cosmetics detail)")
            time.sleep(0.6)
            try:
                a61k8 = _fetch_filist(page, "A61K8_00")
                for it in a61k8:
                    code, label = it["code"], it["label"]
                    if not code.startswith("A61K8"):
                        continue
                    depth = 2 + it["depth_dots"]  # A61K8/00 (depth1) + dot levels
                    # 親決定: 直近の上位 dot レベルの最後ノード or A61K8/00
                    # ここは簡易に A61K8/00 を親にする (ツリー再構成は post-process で)
                    add_node(code, label, depth, "A61K8/00")
                    _enroll(code, label)
            except Exception as e:
                print(f"  ! A61K8 fetch failed: {e}")

            # ===== A61Q メイングループ + 各メインの fiList =====
            print("[3/3] A61Q main groups + fiList drill")
            time.sleep(0.6)
            a61q_main = _fetch_main_group_codes(page, "A61Q")
            add_node("A61Q", "化粧品または類似化粧品製剤の特定の用途", 0, None)
            for it in a61q_main:
                code, label = it["code"], it["label"]
                if code.startswith("A61Q"):
                    add_node(code, label, 1, "A61Q")
                    _enroll(code, label)
            # 各 A61Q*/00 を drill
            for it in a61q_main:
                code = it["code"]
                if not code.startswith("A61Q") or not code.endswith("/00"):
                    continue
                url_part = _to_url_part(code)
                time.sleep(0.4)
                try:
                    sub = _fetch_filist(page, url_part)
                    for s in sub:
                        scode, slabel = s["code"], s["label"]
                        if not scode.startswith("A61Q") or scode == code:
                            continue
                        depth = 2 + s["depth_dots"]
                        add_node(scode, slabel, depth, code)
                        _enroll(scode, slabel)
                except Exception as e:
                    print(f"  ! {code} fetch failed: {e}")
        finally:
            browser.close()

    # 親子整合性の post-process: depth_dots ベースで最近 ancestor を再計算
    # (簡易実装: ツリー走査せず、現状の parent ヒント + depth で問題ないなら OK)

    return {
        "theme": "FI:A61",
        "name": "化粧品関連 FI 分類 (A61K8 + A61Q)",
        "generated_at": time.strftime("%Y-%m-%d"),
        "source": "https://www.j-platpat.inpit.go.jp/cache/classify/patent/PMGS_HTML/jpp/FI/ja/",
        "select_version": SELECT,
        "nodes": nodes,
        "reverse_index": reverse_index,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(
        PROJECT_ROOT / "dictionaries" / "cosmetics" / "fi_a61_cosmetics_tree.json"))
    args = ap.parse_args()

    print(f"Scraping FI A61K8 + A61Q from J-PlatPat (select={SELECT}) ...")
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
