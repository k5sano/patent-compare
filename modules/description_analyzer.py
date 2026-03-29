#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
明細書解析モジュール — 実施例化合物・具体名・配合目的・配合量の抽出

本願明細書(hongan.json)から、特に実施例で使用された化合物名を
正確に抽出し、キーワードグループに反映するためのモジュール。
"""

import re
from typing import List, Dict

from modules.keyword_recommender import _preprocess_text

# ============================================================
# 化合物名マッチ用正規表現
# ============================================================

COMPONENT_PATTERN = re.compile(
    r'(?:'
    # PPG-XX系
    r'PPG[-－]?\d+[^\s、,。）」]{0,20}'
    r'|PEG[-－]?\d+[^\s、,。）」]{0,20}'
    # ポリオキシエチレン/ポリオキシプロピレン系
    r'|ポリオキシエチレン[ァ-ヴー\w]{2,30}'
    r'|ポリオキシプロピレン[ァ-ヴー\w]{2,30}'
    # 揮発性炭化水素油系
    r'|軽質流動イソパラフィン'
    r'|軽質イソパラフィン'
    r'|イソドデカン'
    r'|イソヘキサデカン'
    r'|ウンデカン'
    r'|ドデカン'
    r'|トリデカン'
    r'|テトラデカン'
    r'|スクワラン'
    # 噴射剤系
    r'|二酸化炭素'
    r'|炭酸ガス'
    r'|ジメチルエーテル'
    r'|DME'
    r'|イソブタン'
    r'|プロパン'
    r'|ノルマルブタン'
    r'|液化石油ガス'
    # シリコーン系
    r'|シクロペンタシロキサン'
    r'|ジメチコン'
    r'|シクロメチコン'
    # 汎用: カタカナ4文字以上で化学物質名の接尾辞を持つもの
    r'|[ァ-ヴー]{4,}(?:酸|油|脂|剤|体|物|液|エーテル|エステル|オキシド|グリコール)'
    r')',
    re.UNICODE,
)

# 実施例で頻出するが化合物名ではない語（除外用）
_NON_COMPOUND_WORDS = {
    "メスシリンダー", "クレンジング", "ボリューム", "マスカラ",
    "スーパー", "ブラック", "エスエヌディ", "ストレス",
    "テスター", "コントロール", "クリーム", "ローション",
    "フォーム", "ムース", "スプレー", "ミスト",
    "サンプル", "ポイント", "エステ", "スキンケア",
    "カラー", "ブランド", "リキッド", "パウダー",
}

# ============================================================
# 脚注パターン（実施例表の注釈）
# ============================================================

_FOOTNOTE_PATTERN = re.compile(
    r'[＊\*※注]\s*\d+[）\)]\s*([^\n。．]{3,80})'
)

# ============================================================
# 列挙パターン（明細書本文）
# ============================================================

_ENUMERATION_PREFIXES = re.compile(
    r'(?:例えば|具体的には|としては|好ましくは|挙げられ|から選ばれ)[、,]?\s*'
)

# ============================================================
# 配合目的パターン
# ============================================================

_PURPOSE_PATTERN = re.compile(
    r'([ァ-ヴー\w]{2,20}(?:力|性|効果|作用)を?(?:高める|向上|付与|維持|改善|発揮|得る))'
)

# ============================================================
# 配合量パターン
# ============================================================

_AMOUNT_PATTERN = re.compile(
    r'(\d+\.?\d*)\s*質量%\s*以上\s*(\d+\.?\d*)\s*質量%\s*以下'
)

_AMOUNT_PREF_PATTERN = re.compile(
    r'(?:好ましくは|より好ましくは|さらに好ましくは)\s*'
    r'(\d+\.?\d*)\s*質量%\s*以上\s*(\d+\.?\d*)\s*質量%\s*以下'
)

# ============================================================
# 上位概念→関連ヒント語マッピング
# ============================================================

CONCEPT_HINTS: Dict[str, List[str]] = {
    "ポリアルキレングリコールエーテル": [
        "ポリオキシエチレン", "ポリオキシプロピレン",
        "PPG", "PEG", "アルキレンオキサイド", "付加モル数",
    ],
    "揮発性炭化水素油": [
        "軽質流動イソパラフィン", "軽質イソパラフィン",
        "イソドデカン", "ウンデカン", "ドデカン", "トリデカン",
    ],
    "噴射剤": [
        "イソブタン", "プロパン", "ジメチルエーテル", "DME",
        "液化石油ガス", "二酸化炭素", "炭酸ガス",
    ],
    "揮発性シリコーン": [
        "シクロペンタシロキサン", "ジメチコン",
    ],
    "エアゾール": [
        "噴射剤", "吐出", "泡沫", "フォーム",
        "二酸化炭素", "液化石油ガス",
    ],
    "界面活性剤": [
        "ノニオン", "アニオン", "カチオン", "両性",
        "ポリオキシエチレン", "ソルビタン",
    ],
}

# ============================================================
# ストップワード（化合物名として採用しない語）
# ============================================================

_STOP = {
    "からなる", "において", "であって", "であり", "する", "される",
    "される", "について", "ための", "である", "おける", "ことを",
    "よる", "とする", "含有する", "有する", "備える", "場合",
    "化粧料", "組成物", "製剤", "原液", "エアゾール用", "用化粧料",
    "ール組成物", "成物原液",
}


# ============================================================
# 公開API
# ============================================================

def extract_example_compounds(hongan: dict) -> List[dict]:
    """実施例・比較例の段落から化合物名を抽出する。

    パターンA: 脚注パターン（*1）PPG-40ブチル ...）
    パターンB: COMPONENT_PATTERN で直接マッチ

    Returns:
        [{"term": str, "source": "実施例【para_id】脚注", "type": "実施例使用化合物"}, ...]
    """
    if not hongan:
        return []

    results = []
    seen = set()

    for para in hongan.get("paragraphs", []):
        section = para.get("section", "")
        if section not in ("実施例", "比較例"):
            continue

        text = _preprocess_text(para["text"])
        para_id = para["id"]

        # パターンA: 脚注
        for m in _FOOTNOTE_PATTERN.finditer(text):
            raw = m.group(1).strip()
            term = raw
            if (term and term not in seen and term not in _STOP
                    and term not in _NON_COMPOUND_WORDS and len(term) >= 3):
                seen.add(term)
                results.append({
                    "term": term,
                    "source": f"実施例【{para_id}】脚注",
                    "type": "実施例使用化合物",
                })

        # パターンB: COMPONENT_PATTERN で直接マッチ
        for m in COMPONENT_PATTERN.finditer(text):
            term = m.group(0).strip()
            if (term and term not in seen and term not in _STOP
                    and term not in _NON_COMPOUND_WORDS and len(term) >= 3):
                seen.add(term)
                results.append({
                    "term": term,
                    "source": f"実施例【{para_id}】",
                    "type": "実施例使用化合物",
                })

    return results


def extract_description_compounds(hongan: dict, concept: str) -> List[dict]:
    """上位概念語 concept を含む段落から具体名・配合目的・配合量を抽出する。

    全section対象。concept が空の場合は空リストを返す。

    Returns:
        [
            {"term": "具体名", "source": "明細書【para_id】", "type": "具体名(本願例示)"},
            {"term": "クレンジング力を高める", "source": ..., "type": "配合目的"},
            {"term": "1.5質量%以上42質量%以下", "source": ..., "type": "配合量(好ましい)"},
        ]
    """
    if not hongan or not concept:
        return []

    # concept 本体 + CONCEPT_HINTS のヒント語で関連段落を判定
    hints = CONCEPT_HINTS.get(concept, [])
    search_words = [concept] + hints

    results = []
    seen = set()

    def _add(term, source, kw_type):
        t = term.strip()
        if t and len(t) >= 2 and t not in seen and t not in _STOP:
            seen.add(t)
            results.append({"term": t, "source": source, "type": kw_type})

    for para in hongan.get("paragraphs", []):
        text = _preprocess_text(para["text"])
        para_id = para["id"]
        source = f"明細書【{para_id}】"

        # この段落が concept に関連するか判定
        if not any(w in text for w in search_words):
            continue

        # ── 列挙パターンで具体名抽出 ──
        for m in _ENUMERATION_PREFIXES.finditer(text):
            start = m.end()
            # 列挙の終わりを探す（。や「が、」まで）
            rest = text[start:]
            end_m = re.search(r'[。．]|が[、,]', rest)
            enum_text = rest[:end_m.start()] if end_m else rest[:200]
            # 読点区切りで分割
            names = re.split(r'[、,，]', enum_text)
            for name in names:
                name = name.strip()
                if len(name) >= 3 and COMPONENT_PATTERN.search(name):
                    _add(name, source, "具体名(本願例示)")

        # ── COMPONENT_PATTERN 直接マッチ ──
        for m in COMPONENT_PATTERN.finditer(text):
            term = m.group(0).strip()
            if len(term) >= 3:
                _add(term, source, "具体名(本願例示)")

        # ── 配合目的 ──
        for m in _PURPOSE_PATTERN.finditer(text):
            _add(m.group(1), source, "配合目的")

        # ── 配合量（好ましい/より好ましい） ──
        for m in _AMOUNT_PREF_PATTERN.finditer(text):
            pref_label = "配合量(より好ましい)" if "より好ましくは" in text[:m.start()+20] else "配合量(好ましい)"
            amount = f"{m.group(1)}質量%以上{m.group(2)}質量%以下"
            _add(amount, source, pref_label)

        # ── 配合量（通常） ──
        for m in _AMOUNT_PATTERN.finditer(text):
            amount = f"{m.group(1)}質量%以上{m.group(2)}質量%以下"
            _add(amount, source, "配合量")

    return results
