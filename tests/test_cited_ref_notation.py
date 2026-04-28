"""Tests for modules.cited_ref_notation."""
from __future__ import annotations

import pytest

from modules.cited_ref_notation import (
    ParsedRef,
    comment_of,
    display_judgment,
    expand,
    expand_ref,
    memo_of,
    normalize_judgment,
    parse,
)


class TestParseParagraph:
    def test_single(self):
        p = parse("20")
        assert len(p.refs) == 1
        assert p.refs[0].kind == "para"
        assert p.refs[0].values == [20]

    def test_comma_list(self):
        p = parse("21,39")
        assert p.refs[0].kind == "para"
        assert p.refs[0].values == [21, 39]

    def test_range(self):
        p = parse("41-45")
        assert p.refs[0].kind == "para"
        assert p.refs[0].values == [41, 42, 43, 44, 45]

    def test_mixed(self):
        p = parse("21,39,41-45")
        assert p.refs[0].values == [21, 39, 41, 42, 43, 44, 45]


class TestParseClaim:
    def test_single(self):
        p = parse("CL1")
        assert p.refs[0].kind == "claim"
        assert p.refs[0].values == [1]

    def test_comma(self):
        p = parse("CL1,5")
        assert p.refs[0].values == [1, 5]

    def test_range(self):
        p = parse("CL1-3")
        assert p.refs[0].values == [1, 2, 3]

    def test_lowercase_prefix(self):
        p = parse("cl2")
        assert p.refs[0].kind == "claim"
        assert p.refs[0].values == [2]


class TestParseFigure:
    def test_simple(self):
        p = parse("F1")
        assert p.refs[0].kind == "figure"
        assert p.refs[0].values == ["1"]

    def test_with_letter_suffix(self):
        p = parse("F1a")
        assert p.refs[0].values == ["1a"]

    def test_uppercase_letter(self):
        p = parse("F5C")
        assert p.refs[0].values == ["5C"]

    def test_mixed_with_repeated_prefix(self):
        # ユーザーは F の繰り返しを許容
        p = parse("F1,6,F1a,F5C")
        assert p.refs[0].values == ["1", "6", "1a", "5C"]

    def test_range(self):
        p = parse("F1-3")
        assert p.refs[0].values == ["1", "2", "3"]


class TestParseTableEtc:
    @pytest.mark.parametrize("prefix,kind", [
        ("T", "table"),
        ("K", "chem"),
        ("E", "formula"),
        ("S", "eq"),
    ])
    def test_simple(self, prefix, kind):
        p = parse(f"{prefix}4")
        assert p.refs[0].kind == kind
        assert p.refs[0].values == [4]


class TestParsePage:
    def test_full(self):
        p = parse("P1A2-4")
        r = p.refs[0]
        assert r.kind == "page"
        assert r.page == 1
        assert r.quad == "A"
        assert r.line_from == 2
        assert r.line_to == 4

    def test_quad_only(self):
        r = parse("P3R").refs[0]
        assert r.page == 3 and r.quad == "R" and r.line_from is None

    def test_no_quad_with_lines(self):
        # P + 数字 + 行のみ (Quad なし) - 現仕様では文法的にこれは動かない可能性
        # P5-3-7 のような形は今のところサポートしない
        r = parse("P5").refs[0]
        assert r.kind == "page" and r.page == 5

    @pytest.mark.parametrize("c,label_part", [
        ("A", "左上欄"),
        ("B", "右上欄"),
        ("C", "左下欄"),
        ("D", "右下欄"),
        ("L", "左欄"),
        ("R", "右欄"),
    ])
    def test_quad_labels(self, c, label_part):
        out = expand(f"P2{c}")
        assert label_part in out


class TestParseColumn:
    def test_column_only(self):
        r = parse("C4").refs[0]
        assert r.kind == "column" and r.column == 4 and r.line_from is None

    def test_column_with_line(self):
        r = parse("C4G12").refs[0]
        assert r.column == 4 and r.line_from == 12 and r.line_to is None

    def test_column_with_range(self):
        r = parse("C4G12-15").refs[0]
        assert r.column == 4 and r.line_from == 12 and r.line_to == 15


class TestSeparator:
    def test_multiple_kinds(self):
        p = parse("20;F2;CL3;T4")
        kinds = [r.kind for r in p.refs]
        assert kinds == ["para", "figure", "claim", "table"]

    def test_full_width_semicolon(self):
        p = parse("20；F2；CL3")
        assert [r.kind for r in p.refs] == ["para", "figure", "claim"]

    def test_full_width_chars(self):
        p = parse("２０；Ｆ２；ＣＬ３")
        assert [r.kind for r in p.refs] == ["para", "figure", "claim"]
        assert p.refs[0].values == [20]
        assert p.refs[1].values == ["2"]
        assert p.refs[2].values == [3]


class TestCommentMemo:
    def test_comment_after_quote(self):
        p = parse('20;"備考メモ')
        assert p.refs[0].kind == "para"
        assert p.comment == "備考メモ"
        assert p.memo == ""

    def test_memo_after_slash(self):
        p = parse('20;//防備録')
        assert p.memo == "防備録"
        assert p.comment == ""

    def test_both(self):
        p = parse('20;"備考;//メモ')
        assert p.comment == "備考"
        assert p.memo == "メモ"

    def test_quote_inline(self):
        # トークン内 (区切り前) で " が出てもコメントとして拾う
        p = parse('20"備考だけ')
        assert p.refs[0].values == [20]
        assert p.comment == "備考だけ"

    def test_comment_only(self):
        p = parse('"先行例の段落不明')
        assert p.refs == []
        assert p.comment == "先行例の段落不明"

    def test_helpers(self):
        s = '20;F2;"備考;//メモ'
        assert comment_of(s) == "備考"
        assert memo_of(s) == "メモ"


class TestExpand:
    def test_full_example(self):
        out = expand('20;F2;CL3;T4;"備考メモ;//防備録', with_comment=True)
        assert "段落【0020】" in out
        assert "図2" in out
        assert "請求項3" in out
        assert "表4" in out
        assert "備考メモ" in out
        # メモは with_memo=False のため出ない
        assert "防備録" not in out

    def test_para_range_expansion(self):
        out = expand("21,39,41-45")
        assert out == "段落【0021】、【0039】、【0041】〜【0045】"

    def test_claim_range(self):
        assert expand("CL1-3,5") == "請求項1〜3、5"

    def test_figure_mix(self):
        assert expand("F1,6,F1a") == "図1、6、1a"

    def test_page_full(self):
        assert expand("P1A2-4") == "1ページ左上欄2〜4行"

    def test_column_line(self):
        assert expand("C4G12-15") == "4カラム12〜15行"

    def test_empty(self):
        assert expand("") == ""

    def test_unknown_passthrough(self):
        out = expand("XYZ123")
        # 解釈不能はそのまま raw を残す
        assert "XYZ123" in out


class TestJudgmentNormalize:
    @pytest.mark.parametrize("inp,expected", [
        ("?", "△"),
        ("？", "△"),
        # 該当箇所なし: ! を正、x も互換
        ("!", "×"),
        ("！", "×"),
        ("x", "×"),
        ("X", "×"),
        ("ｘ", "×"),
        # ○ (一致) は「先頭に何もつけない」 → 空文字
        ("o", ""),
        ("O", ""),
        ("ｏ", ""),
        ("Ｏ", ""),
        ("△", "△"),
        ("×", "×"),
        ("○", ""),
        ("", ""),
    ])
    def test_shortcuts(self, inp, expected):
        assert normalize_judgment(inp) == expected

    def test_passthrough_long_text(self):
        # 既にテキスト記述になっているものは触らない
        assert normalize_judgment("一致しない") == "一致しない"


class TestDisplayJudgment:
    """既存データ (judgment='○' で保存済み) を表示用に変換"""

    @pytest.mark.parametrize("inp,expected", [
        ("○", ""),
        ("o", ""),
        ("O", ""),
        ("△", "△"),
        ("×", "×"),
        ("", ""),
    ])
    def test_display(self, inp, expected):
        assert display_judgment(inp) == expected
