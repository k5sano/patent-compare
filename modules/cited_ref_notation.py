#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""対比表「該当箇所」セルの統一記法パーサ／展開モジュール。

仕様: memory/reference_cited_ref_notation.md と同期。

入力例: ``20;F2;CL3,5;T4;P1A2-4;C4G12-15;/備考メモ;//防備録メモ``
       (`"` も互換として受け入れる: ``"備考メモ`` ≡ ``/備考メモ``)
出力 (parse): {
    "refs": [
        {"kind": "para",   "values": [20]},
        {"kind": "figure", "values": ["2"]},
        {"kind": "claim",  "values": [3, 5]},
        {"kind": "table",  "values": [4]},
        {"kind": "page",   "page": 1, "quad": "A", "from": 2, "to": 4},
        {"kind": "column", "column": 4, "line_from": 12, "line_to": 15},
    ],
    "comment": "備考メモ",
    "memo":    "防備録メモ",
}
出力 (expand): "段落【0020】、図2、請求項3、5、表4、1ページ左上欄2〜4行、4カラム12〜15行"

判定セル用の正規化:
    normalize_judgment("?")  → "△"
    normalize_judgment("x")  → "×"
    normalize_judgment("o")  → "○"
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# ============================================================
# 正規化ユーティリティ
# ============================================================

_FW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_FW_ALPHA = str.maketrans(
    "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz",
)
_FW_PUNCT = str.maketrans({
    "；": ";", "，": ",", "－": "-", "ー": "-", "～": "-", "〜": "-",
    "（": "(", "）": ")", "［": "[", "］": "]", "／": "/", "　": " ",
})


def _normalize(s: str) -> str:
    if not s:
        return ""
    return s.translate(_FW_DIGITS).translate(_FW_ALPHA).translate(_FW_PUNCT)


# ============================================================
# データクラス
# ============================================================

@dataclass
class ParsedRef:
    """個別トークン (1 つの ; 区切り単位) の解析結果"""
    kind: str  # "para", "claim", "figure", "table", "chem", "formula", "eq", "page", "column", "unknown"
    raw: str = ""  # 元のトークン文字列 (";" は含まない)
    values: list = field(default_factory=list)  # para/claim/figure/table/chem/formula/eq 用
    # page 用
    page: int | None = None
    quad: str | None = None  # "A"|"B"|"C"|"D"|"L"|"R"
    line_from: int | None = None
    line_to: int | None = None
    # column 用
    column: int | None = None


@dataclass
class ParsedNotation:
    refs: list[ParsedRef] = field(default_factory=list)
    comment: str = ""  # "..." 部分
    memo: str = ""     # //... 部分
    raw: str = ""

    def is_empty(self) -> bool:
        return not (self.refs or self.comment or self.memo)


# ============================================================
# 判定セル正規化
# ============================================================

_JUDGMENT_MAP = {
    "?": "△", "？": "△",
    # × (該当箇所なし) のショートカットは ! を正とする (旧: x も互換のため許容)
    "!": "×", "！": "×",
    "x": "×", "X": "×", "ｘ": "×", "Ｘ": "×",
    # ○ (一致) は「先頭に何もつけない」慣行 → 空文字に正規化
    "o": "", "O": "", "ｏ": "", "Ｏ": "",
    "△": "△", "×": "×", "○": "",
}


def normalize_judgment(s: str) -> str:
    """判定セルのショートカット入力を正規化記号に変換。

    - `?` / `？`            → `△`
    - `x` / `X` / `ｘ` / `Ｘ`  → `×`
    - `o` / `O` / `ｏ` / `Ｏ` / `○` → `""` (一致は先頭に何もつけない慣行)
    - 既に `△` `×` がある場合はそのまま
    - 自由記述（「該当なし」等）はそのまま返す
    """
    if not s:
        return ""
    s = s.strip()
    # 完全一致のショートカット
    if s in _JUDGMENT_MAP:
        return _JUDGMENT_MAP[s]
    # 1 文字目だけ評価（例: "?  partial" の頭文字）
    if s[0] in _JUDGMENT_MAP:
        return _JUDGMENT_MAP[s[0]]
    return s  # 既にフルテキストならそのまま


def display_judgment(j: str) -> str:
    """既に保存済みの judgment 値を「対比表セル表示用」に変換。

    ○ は空、△/× は記号、既知外文字列はそのまま。"""
    if not j:
        return ""
    j = j.strip()
    if j in ("○", "o", "O", "ｏ", "Ｏ"):
        return ""
    return j


# ============================================================
# パーサ
# ============================================================

# 各トークンを判別する正規表現
# 順序重要: 接頭辞が長いものから（CL > C）
_TOK_CLAIM   = re.compile(r"^CL([0-9,\-]+)$", re.IGNORECASE)
_TOK_PAGE    = re.compile(r"^P([0-9]+)([ABCDLR])?(?:([0-9]+)(?:-([0-9]+))?)?$", re.IGNORECASE)
_TOK_COLUMN  = re.compile(r"^C([0-9]+)(?:G([0-9]+)(?:-([0-9]+))?)?$", re.IGNORECASE)
_TOK_FIGURE  = re.compile(r"^F([0-9a-zA-Z,\-]+)$")          # F1, F1a, F5C, F1,6, F1-3
_TOK_TABLE   = re.compile(r"^T([0-9,\-]+)$", re.IGNORECASE)
_TOK_CHEM    = re.compile(r"^K([0-9,\-]+)$", re.IGNORECASE)
_TOK_FORMULA = re.compile(r"^E([0-9,\-]+)$", re.IGNORECASE)
_TOK_EQ      = re.compile(r"^S([0-9,\-]+)$", re.IGNORECASE)
_TOK_PARA    = re.compile(r"^[0-9]+(?:[,\-][0-9]+)*$")     # 数字のみ（カンマ・ハイフンで連結）


def _expand_int_list(spec: str) -> list[int]:
    """``"21,39,41-45"`` → [21, 39, 41, 42, 43, 44, 45]"""
    out: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            a, b = chunk.split("-", 1)
            try:
                ai, bi = int(a), int(b)
                if ai <= bi:
                    out.extend(range(ai, bi + 1))
                else:
                    out.append(ai)
                    out.append(bi)
            except ValueError:
                pass
        else:
            try:
                out.append(int(chunk))
            except ValueError:
                pass
    return out


def _expand_str_list(spec: str) -> list[str]:
    """図番のように英字混じりも許容。範囲は数字のみ展開し、英字混じりは個別扱い。

    ``"1,6"`` → ["1", "6"];  ``"1-3"`` → ["1","2","3"];  ``"1a,5C"`` → ["1a","5C"]
    """
    out: list[str] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk and re.fullmatch(r"\d+-\d+", chunk):
            a, b = chunk.split("-", 1)
            ai, bi = int(a), int(b)
            if ai <= bi:
                out.extend(str(i) for i in range(ai, bi + 1))
            else:
                out.append(a)
                out.append(b)
        else:
            out.append(chunk)
    return out


def _classify_token(tok: str) -> ParsedRef:
    """1 個のトークン (";" 区切り内) を分類して ParsedRef にする。"""
    raw = tok
    tok = tok.strip()
    if not tok:
        return ParsedRef(kind="unknown", raw=raw)

    if m := _TOK_CLAIM.match(tok):
        return ParsedRef(kind="claim", raw=raw, values=_expand_int_list(m.group(1)))
    if m := _TOK_PAGE.match(tok):
        page = int(m.group(1))
        quad = m.group(2).upper() if m.group(2) else None
        lf = int(m.group(3)) if m.group(3) else None
        lt = int(m.group(4)) if m.group(4) else None
        return ParsedRef(kind="page", raw=raw, page=page, quad=quad, line_from=lf, line_to=lt)
    if m := _TOK_COLUMN.match(tok):
        col = int(m.group(1))
        lf = int(m.group(2)) if m.group(2) else None
        lt = int(m.group(3)) if m.group(3) else None
        return ParsedRef(kind="column", raw=raw, column=col, line_from=lf, line_to=lt)
    if m := _TOK_FIGURE.match(tok):
        # F1,6,F1a,F5C のように接頭辞 F が繰り返し記入されても許容するため
        # カンマ分割後に各要素の先頭 F を剥がしてから展開する。
        body = re.sub(r"(?:^|,)F", lambda mo: "," if mo.group(0).startswith(",") else "", m.group(1))
        return ParsedRef(kind="figure", raw=raw, values=_expand_str_list(body))
    if m := _TOK_TABLE.match(tok):
        return ParsedRef(kind="table", raw=raw, values=_expand_int_list(m.group(1)))
    if m := _TOK_CHEM.match(tok):
        return ParsedRef(kind="chem", raw=raw, values=_expand_int_list(m.group(1)))
    if m := _TOK_FORMULA.match(tok):
        return ParsedRef(kind="formula", raw=raw, values=_expand_int_list(m.group(1)))
    if m := _TOK_EQ.match(tok):
        return ParsedRef(kind="eq", raw=raw, values=_expand_int_list(m.group(1)))
    if _TOK_PARA.match(tok):
        return ParsedRef(kind="para", raw=raw, values=_expand_int_list(tok))

    return ParsedRef(kind="unknown", raw=raw)


def _find_single_slash(tok: str) -> int:
    """`//` の一部ではない単独の `/` の最初の位置を返す。なければ -1。"""
    i = 0
    while i < len(tok):
        if tok[i] == "/":
            if i + 1 < len(tok) and tok[i + 1] == "/":
                i += 2  # `//` はメモ記号なのでスキップ
                continue
            return i
        i += 1
    return -1


def parse(text: str) -> ParsedNotation:
    """記法文字列を ParsedNotation に分解する。

    - 全角→半角正規化
    - ``/..`` でコメント開始（最初の ``;`` または末尾まで）。`"..` も互換でコメント扱い
    - ``//..`` でメモ開始（最初の ``;`` または末尾まで）
    - 残りを ``;`` で分割し、各トークンを ParsedRef に分類
    """
    if not text:
        return ParsedNotation()
    src = _normalize(text).strip()
    if not src:
        return ParsedNotation(raw=text)

    notation = ParsedNotation(raw=text)

    # ; で分割しつつ、" / // が来たらその後ろを comment/memo に振る。
    # 単純化のため、; 区切りでまずトークン化し、各トークンの先頭で判定する。
    tokens = src.split(";")
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        # 行頭判定: // が最優先（メモ）、続いて " or 単独/（コメント）
        if tok.startswith("//"):
            body = tok[2:].strip()
            if notation.memo:
                notation.memo += " / " + body
            else:
                notation.memo = body
            continue
        if tok.startswith('"') or tok.startswith("/"):
            body = tok[1:].strip()
            if notation.comment:
                notation.comment += " / " + body
            else:
                notation.comment = body
            continue

        # 通常のトークンだが、内部に "/`/`// が混じっていれば分離
        # 例: `20"備考` `20/備考` → para 20 + comment "備考"
        memo_idx = tok.find("//")
        quote_idx = tok.find('"')
        slash_idx = _find_single_slash(tok)

        candidates = []
        if memo_idx >= 0:
            candidates.append((memo_idx, "m", 2))
        if quote_idx >= 0:
            candidates.append((quote_idx, "c", 1))
        if slash_idx >= 0:
            candidates.append((slash_idx, "c", 1))

        if candidates:
            candidates.sort()
            cut, kind, sep_len = candidates[0]
            head = tok[:cut].strip()
            tail = tok[cut + sep_len:].strip()
            if head:
                notation.refs.append(_classify_token(head))
            if kind == "c":
                notation.comment = (notation.comment + " / " + tail) if notation.comment else tail
            else:
                notation.memo = (notation.memo + " / " + tail) if notation.memo else tail
            continue

        notation.refs.append(_classify_token(tok))

    return notation


# ============================================================
# 展開（日本語表記）
# ============================================================

_QUAD_LABEL = {
    "A": "左上欄",
    "B": "右上欄",
    "C": "左下欄",
    "D": "右下欄",
    "L": "左欄",
    "R": "右欄",
}


def _format_int_list(xs: Iterable[int]) -> str:
    """[20, 39, 41, 42, 43] → '20、39、41〜43'"""
    xs = list(xs)
    if not xs:
        return ""
    # 連続区間をまとめる
    runs: list[tuple[int, int]] = []
    a = b = xs[0]
    for x in xs[1:]:
        if x == b + 1:
            b = x
        else:
            runs.append((a, b))
            a = b = x
    runs.append((a, b))
    parts = []
    for s, e in runs:
        if s == e:
            parts.append(str(s))
        elif e == s + 1:
            parts.append(f"{s}、{e}")
        else:
            parts.append(f"{s}〜{e}")
    return "、".join(parts)


def _format_para_list(xs: Iterable[int]) -> str:
    """段落番号は4桁ゼロ埋め: 20 → 【0020】"""
    xs = list(xs)
    if not xs:
        return ""
    runs: list[tuple[int, int]] = []
    a = b = xs[0]
    for x in xs[1:]:
        if x == b + 1:
            b = x
        else:
            runs.append((a, b))
            a = b = x
    runs.append((a, b))
    parts = []
    for s, e in runs:
        if s == e:
            parts.append(f"【{s:04d}】")
        else:
            parts.append(f"【{s:04d}】〜【{e:04d}】")
    return "".join(parts) if all(s == e for s, e in runs) else "、".join(parts)


def _format_str_list(xs: Iterable[str]) -> str:
    """図番などの文字列リストを '1、6、1a' のように整形。連番は範囲表記に。"""
    xs = list(xs)
    if not xs:
        return ""
    # 数字のみのもの／英字混じりのもの に分けて、数字のみ部分は範囲化
    pure: list[int] = []
    mixed: list[str] = []
    for x in xs:
        if x.isdigit():
            pure.append(int(x))
        else:
            mixed.append(x)
    out_parts: list[str] = []
    if pure:
        out_parts.append(_format_int_list(pure))
    if mixed:
        out_parts.extend(mixed)
    return "、".join(p for p in out_parts if p)


def expand_ref(ref: ParsedRef) -> str:
    """1 個の ParsedRef を日本語に展開。"""
    k = ref.kind
    if k == "para":
        return f"段落{_format_para_list(ref.values)}"
    if k == "claim":
        return f"請求項{_format_int_list(ref.values)}"
    if k == "figure":
        return f"図{_format_str_list(ref.values)}"
    if k == "table":
        return f"表{_format_int_list(ref.values)}"
    if k == "chem":
        return f"化{_format_int_list(ref.values)}"
    if k == "formula":
        return f"式{_format_int_list(ref.values)}"
    if k == "eq":
        return f"数{_format_int_list(ref.values)}"
    if k == "page":
        out = f"{ref.page}ページ"
        if ref.quad:
            out += _QUAD_LABEL.get(ref.quad, ref.quad)
        if ref.line_from is not None:
            if ref.line_to is not None and ref.line_to != ref.line_from:
                out += f"{ref.line_from}〜{ref.line_to}行"
            else:
                out += f"{ref.line_from}行"
        return out
    if k == "column":
        out = f"{ref.column}カラム"
        if ref.line_from is not None:
            if ref.line_to is not None and ref.line_to != ref.line_from:
                out += f"{ref.line_from}〜{ref.line_to}行"
            else:
                out += f"{ref.line_from}行"
        return out
    return ref.raw


def expand(text: str, *, with_comment: bool = False, with_memo: bool = False) -> str:
    """記法文字列を日本語に展開。

    - with_comment=True: コメント部分を「（備考: ...）」付きで合成
    - with_memo=True:    メモ部分を「（メモ: ...）」付きで合成（通常 False）
    """
    p = parse(text)
    if p.is_empty():
        return ""
    parts = [expand_ref(r) for r in p.refs if r.kind != "unknown"]
    # unknown は raw のまま落とし込む（ユーザーが自由記述したケース）
    parts.extend(r.raw for r in p.refs if r.kind == "unknown" and r.raw)
    out = "、".join(parts)
    if with_comment and p.comment:
        out = (out + "（備考: " + p.comment + "）") if out else f"備考: {p.comment}"
    if with_memo and p.memo:
        out = (out + "（メモ: " + p.memo + "）") if out else f"メモ: {p.memo}"
    return out


def comment_of(text: str) -> str:
    """コメント部分のみ返す（Excel 備考列用）。"""
    return parse(text).comment


def memo_of(text: str) -> str:
    """メモ部分のみ返す。エクスポート対象外であることに注意。"""
    return parse(text).memo


# ============================================================
# CLI（動作確認用）
# ============================================================
if __name__ == "__main__":
    import sys
    samples = sys.argv[1:] or [
        '20;F2;CL3;T4;"備考;//自分メモ',
        "21,39,41-45",
        "CL1-3,5",
        "F1,6,F1a,F5C",
        "P1A2-4",
        "C4G12-15",
        '20"備考だけ',
        "//メモだけ",
    ]
    for s in samples:
        p = parse(s)
        print(f"in : {s}")
        print(f"  refs    = {p.refs}")
        print(f"  comment = {p.comment!r}")
        print(f"  memo    = {p.memo!r}")
        print(f"  expand  = {expand(s, with_comment=True, with_memo=True)}")
        print()
