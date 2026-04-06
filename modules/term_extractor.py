#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""正規表現による語句抽出ユーティリティ"""

import re

# ============================================================
# ストップワード
# ============================================================

STOP_WORDS = {
    # 助詞・助動詞的
    "前記", "含有", "含有する", "からなる", "有する", "備える",
    "において", "であって", "であり", "おける", "よる", "する",
    "された", "される", "および", "ならびに", "または", "もしくは",
    "以上", "以下", "未満", "超える", "含む", "特徴", "記載",
    "少なくとも", "それぞれ", "前記した", "さらに", "また",
    "これら", "それら", "当該", "所定", "各種", "種々",
    # 汎用カテゴリ語（分野を問わず）
    "化粧料", "組成物", "製剤",
    "配合", "処方", "調製", "混合", "工程", "方法", "手段", "構成", "形態",
    # 追加: 請求項構造語
    "ことを", "特徴とする", "請求項", "発明", "前項",
    "該", "上記", "下記", "場合", "範囲", "条件", "用途", "目的",
    "一つ", "一種", "複数", "少なくとも一つ",
    "第一", "第二", "第三", "第四", "第五",
    "前述", "後述", "同様", "対応", "関連",
    # 追加: 請求項断片ゴミ語
    "用化粧料", "以上含有", "種類含有", "項記載", "分以上", "吐出後",
    "前記原液", "原液組成物", "記原液", "ール組成物", "成物原液",
    "質量部", "体積比", "種以上",
}

# ============================================================
# 正規表現パターン
# ============================================================

RE_KATAKANA = re.compile(r'[ァ-ヴー]{3,}')
RE_KANJI = re.compile(
    r'[一-龥]{2,}(?:剤|物|体|油|酸|液|層|膜|比|料|性|化|類|素|塩|基)?'
)
RE_NUMERIC = re.compile(
    r'(\d+\.?\d*)\s*(?:～|~|−|-|から)\s*(\d+\.?\d*)\s*'
    r'(質量%|重量%|mass%|wt%|vol%|体積%|%|ppm|mm|μm|nm|℃)'
)
RE_NUMERIC_SINGLE = re.compile(
    r'(\d+\.?\d*)\s*(質量%|重量%|mass%|wt%|ppm|mm|μm|nm|℃)\s*'
    r'(以上|以下|未満|超|を超える|より多い|より少ない)?'
)
RE_PAREN_LABEL = re.compile(r'[\(（]([A-Za-zＡ-Ｚ])[\)）]\s*([^、。\)）\(（]{2,15})')


def strip_prefix(term: str) -> str:
    """「前記」「該」「上記」等の接頭辞を除去"""
    for prefix in ("前記", "上記", "該", "当該", "前記した", "本"):
        if term.startswith(prefix) and len(term) > len(prefix):
            return term[len(prefix):]
    return term


def pick_terms_from_text(text):
    """テキストから語句を機械的にピックアップ

    全パターン（カタカナ・漢字・数値条件・括弧ラベル）を適用し、
    STOP_WORDS 除外・重複除去済みのリストを返す。

    Returns:
        list of dict: [{"term": str, "source": "claim", "type": str}, ...]
    """
    terms = []
    seen = set()

    def _add(term, kw_type):
        term = strip_prefix(term)
        if term and term not in seen and term not in STOP_WORDS and len(term) >= 2:
            seen.add(term)
            terms.append({"term": term, "source": "claim", "type": kw_type})

    for m in RE_KATAKANA.finditer(text):
        t = m.group()
        if len(t) >= 3:
            _add(t, "カタカナ抽出")

    for m in RE_KANJI.finditer(text):
        t = m.group()
        if len(t) >= 2:
            _add(t, "漢字抽出")

    for m in RE_NUMERIC.finditer(text):
        _add(m.group(), "数値条件")

    for m in RE_NUMERIC_SINGLE.finditer(text):
        _add(m.group(), "数値条件")

    for m in RE_PAREN_LABEL.finditer(text):
        label = m.group(1)
        content = m.group(2).strip()
        _add(f"({label}){content}", "括弧ラベル")

    return terms
