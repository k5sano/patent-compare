"""
JPO Fターム一覧 PDF (J-PlatPat 出力) を座標ベースでパースして辞書 JSON を作る。

PDF 構造 (x座標で列を識別):
  - ヘッダ行: コード列 (AA01, AA02, ... AA10) が等間隔で並ぶ
  - それ以降: 各コード列の下に、ラベル行が縦に並ぶ (同じ x 座標)
  - ラベル行は「・」の繰り返しで depth を示す
  - ラベルが長いと複数行に折り返される
"""
import re
import json
import sys
import unicodedata
from pathlib import Path

import fitz

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
SRC = PROJECT_ROOT.parent

FTERM_SOURCES = [
    {
        "pdf": SRC / "4F100 Fタームリスト.pdf",
        "theme_code": "4F100",
        "theme_name": "積層体",
        "ipc": "B32B1/00-43/00",
        "out": PROJECT_ROOT / "dictionaries" / "laminate" / "fterm_4f100_structure.json",
    },
    {
        "pdf": SRC / "包材　Fタームリスト.pdf",
        "theme_code": "3E086",
        "theme_name": "被包体、包装体、容器",
        "ipc": "B65D65/00-65/46",
        "out": PROJECT_ROOT / "dictionaries" / "laminate" / "fterm_3e086_structure.json",
    },
]

CODE_RE = re.compile(r"^[A-Z]{2}\d{2}$")
AXIS_RE = re.compile(r"^[A-Z]{2}$")


def load_spans(pdf_path: Path):
    """全ページの text span を (page, y, x, text) のリストで返す。"""
    doc = fitz.open(str(pdf_path))
    spans = []
    for pidx, page in enumerate(doc):
        d = page.get_text("dict")
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                bbox = line["bbox"]
                y = (bbox[1] + bbox[3]) / 2
                x = bbox[0]
                text = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                if text:
                    spans.append((pidx, y, x, text))
    doc.close()
    return spans


def group_rows(spans, y_tol=2.0):
    """y 座標が近いものを同一行にグルーピング。"""
    pages = {}
    for p, y, x, t in spans:
        pages.setdefault(p, []).append((y, x, t))
    all_rows = []  # [(page, y_avg, [(x, text), ...]), ...]
    for p in sorted(pages):
        items = sorted(pages[p])  # y昇順
        cur_rows = []
        for y, x, t in items:
            if cur_rows and abs(cur_rows[-1][0] - y) <= y_tol:
                cur_rows[-1][1].append((x, t))
            else:
                cur_rows.append([y, [(x, t)]])
        for y, cells in cur_rows:
            cells.sort(key=lambda c: c[0])
            all_rows.append((p, y, cells))
    return all_rows


def is_code_row(cells):
    """コードセル (AA01 形式) が 2 個以上あり、コード列の左 (x<140) 以外がすべてコード。"""
    # PDF のコード列は x>=140 に並ぶ。その左に紛れるラベル残差は無視する
    code_cells = [(x, t) for x, t in cells if x >= 140.0]
    if len(code_cells) < 2:
        return False
    return all(CODE_RE.match(t) for _, t in code_cells)


def strip_depth(label: str):
    """先頭の「・」群を数えて depth を返し、ラベル本体を返す。"""
    m = re.match(r"^([\u30fb・]+)(.*)$", label)
    if m:
        return len(m.group(1)), m.group(2).strip()
    return 0, label.strip()


def assign_label_to_column(x, code_columns_sorted, col_width):
    """x 座標をコード列レンジに割り当て。

    J-PlatPat PDF ではラベル行がコード行より左 (約 15pt) にインデントされて
    始まる。そのため各列の左端は code_x - 18 まで広げる。
    """
    left_pad = 18.0
    for i, cx in enumerate(code_columns_sorted):
        if i + 1 < len(code_columns_sorted):
            next_cx = code_columns_sorted[i + 1]
            # 次の列の開始までを自列のレンジとする
            right = next_cx - left_pad
        else:
            right = cx + col_width + 10
        left = cx - left_pad
        if left <= x < right:
            return cx
    return None


def split_multi_bullet(raw_label: str):
    """ラベル中に「・」の連続が出現する場合、そこで項目を分割する。

    例: "・・無機金属化合物・・・けい酸塩"
      -> ["・・無機金属化合物", "・・・けい酸塩"]

    先頭「・」の後、非「・」文字を経て再び「・」が 2 個以上連続する箇所を区切りとする。
    """
    # 先頭の「・」群を保持
    m = re.match(r"^([\u30fb・]+)(.*)$", raw_label)
    if not m:
        return [raw_label]
    head_dots = m.group(1)
    rest = m.group(2)
    # rest 内の ・・ (2個以上) で分割
    parts = re.split(r"([\u30fb・]{2,})", rest)
    # parts は [text, dots, text, dots, ...] の形
    items = [head_dots + parts[0]]
    for k in range(1, len(parts), 2):
        dots = parts[k]
        text = parts[k + 1] if k + 1 < len(parts) else ""
        items.append(dots + text)
    # 空文字列は除外
    return [s for s in items if s.strip(" ・\u30fb")]


def parse_pdf(pdf_path: Path, theme_code: str):
    spans = load_spans(pdf_path)
    rows = group_rows(spans)

    axis_labels = {}
    code_labels = {}  # code -> raw label
    code_order = []   # コード出現順

    i = 0
    while i < len(rows):
        p, y, cells = rows[i]
        if is_code_row(cells):
            # コード列セル (x>=140) のみを使う
            code_cells = [(x, t) for x, t in cells if x >= 140.0]
            codes = [t for _, t in code_cells]
            code_x_list = sorted({x for x, _ in code_cells})
            code_by_x = {x: t for x, t in code_cells}
            # 列幅推定
            if len(code_x_list) >= 2:
                col_width = code_x_list[1] - code_x_list[0]
            else:
                col_width = 42.5
            for c in codes:
                if c not in code_labels:
                    code_order.append(c)
                    code_labels[c] = []

            # 次のコード行までを収集
            j = i + 1
            while j < len(rows):
                p2, y2, c2 = rows[j]
                if p2 != p:
                    break
                if is_code_row(c2):
                    break
                for x2, t2 in c2:
                    if AXIS_RE.match(t2):
                        continue
                    if CODE_RE.match(t2):
                        continue
                    if re.match(r"^B\d{2}[A-Z]", t2):
                        continue
                    if any(ng in t2 for ng in [
                        "j-platpat", "Fターム", "English", "一階層", "テーマ",
                        "リスト", "閉じる", "戻る", "2026/",
                    ]):
                        continue
                    cx = assign_label_to_column(x2, code_x_list, col_width)
                    if cx is not None:
                        code = code_by_x[cx]
                        code_labels[code].append((y2, x2, t2))
                j += 1
            i = j
            continue
        i += 1

    # コードラベルを y→x 順に連結
    # その後、連続「・」で分割して余剰項目を次のコードに振り分ける
    raw_labels = {}  # code -> joined raw text
    for code, parts in code_labels.items():
        parts.sort()
        raw_labels[code] = "".join(t for _, _, t in parts)

    # split_multi_bullet で分割し、コードに再割り当て
    entries = {}
    overflow = []  # 余剰項目（所有者不明）
    # codes はソートされた順で処理
    sorted_codes = sorted(raw_labels.keys())
    for idx, code in enumerate(sorted_codes):
        raw = raw_labels[code].strip()
        if not raw:
            entries[code] = {"label": "", "depth": 0}
            continue
        pieces = split_multi_bullet(raw)
        if not pieces:
            entries[code] = {"label": "", "depth": 0}
            continue
        # 先頭を自分のラベルに、残りを overflow に
        d, lbl = strip_depth(pieces[0])
        entries[code] = {"label": lbl, "depth": d}
        for extra in pieces[1:]:
            overflow.append(extra)

    # overflow を、空ラベル持ちの次コードに順に流し込む
    if overflow:
        # 空ラベルのコードリスト（昇順）
        empty_codes = [c for c in sorted_codes if not entries[c]["label"]]
        for piece in overflow:
            if not empty_codes:
                break
            target = empty_codes.pop(0)
            d, lbl = strip_depth(piece)
            entries[target] = {"label": lbl, "depth": d}

    # 観点名を推定: 各 AA00/AB00/... セルの直下に axis の label がある (位置が近い)
    # シンプルに: 各ページで CODE_RE.match(t) and t.endswith("00") を発見したら、
    # 同じ x 範囲の直下数行で「・」を含まない最初のテキストを観点名とする
    for pidx in sorted({s[0] for s in spans}):
        page_spans = [(s[1], s[2], s[3]) for s in spans if s[0] == pidx]
        page_spans.sort()
        for i2, (y2, x2, t2) in enumerate(page_spans):
            if not (CODE_RE.match(t2) and t2.endswith("00")):
                continue
            axis = t2[:2]
            if axis in axis_labels:
                continue
            # 同じ x±20, y～y+40 の範囲で「・」始まりでない短いテキスト
            cands = []
            for y3, x3, t3 in page_spans:
                if y3 <= y2:
                    continue
                if y3 > y2 + 40:
                    break
                if abs(x3 - x2) > 25:
                    continue
                if CODE_RE.match(t3) or AXIS_RE.match(t3):
                    continue
                if t3.startswith("・") or t3.startswith("\u30fb"):
                    continue
                if re.match(r"^B\d{2}[A-Z]", t3):
                    continue
                cands.append((y3, t3))
            if cands:
                # 改行で分かれている可能性があるので y 順に連結
                axis_labels[axis] = "".join(t for _, t in sorted(cands))

    return entries, axis_labels


def build_structure(theme_code, theme_name, ipc, entries, axis_label):
    categories = {}
    for code in sorted(entries.keys()):
        axis = code[:2]
        if axis not in categories:
            categories[axis] = {
                "label": axis_label.get(axis, ""),
                "entries": {},
            }
        categories[axis]["entries"][code] = {
            "label": entries[code]["label"],
            "depth": entries[code]["depth"],
            "examples": [],
        }
    return {
        "theme_code": theme_code,
        "theme_name": theme_name,
        "ipc": ipc,
        "source": "J-PlatPat Fタームリスト PDF (座標ベースパース)",
        "version": "0.3.0-official",
        "categories": categories,
    }


def main():
    for src in FTERM_SOURCES:
        pdf = src["pdf"]
        if not pdf.exists():
            for cand in pdf.parent.iterdir():
                if src["theme_code"] in cand.name:
                    pdf = cand; break
        print(f"\n=== {pdf.name} ({src['theme_code']}) ===")
        entries, axis_label = parse_pdf(pdf, src["theme_code"])
        print(f"  axes={len(axis_label)}, entries={len(entries)}")
        for axis in sorted(axis_label):
            count = sum(1 for c in entries if c.startswith(axis))
            print(f"    {axis} {axis_label[axis]!r}: {count}")
        data = build_structure(src["theme_code"], src["theme_name"], src["ipc"], entries, axis_label)
        src["out"].write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  -> {src['out'].relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
