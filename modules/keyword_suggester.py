#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
キーワード＋Fterm自動提案モジュール

入力:
- 本願構造化テキスト(hongan.json)
- 請求項分節(segments.json)
- 分野("cosmetics" | "laminate")

出力:
- キーワードグループ（5-7グループ）にFtermコード付き
"""

import re
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# デフォルトの色順序
COLOR_NAMES = {
    1: "赤",
    2: "紫",
    3: "マゼンタ",
    4: "青",
    5: "緑",
    6: "オレンジ",
    7: "ティール",
}

# 成分名らしい文字列のパターン（化粧品分野）
INGREDIENT_PATTERN_JP = re.compile(
    r'(?:ポリ|メチル|エチル|プロピル|ブチル|ヘキシル|オクチル|デシル|ドデシル|'
    r'ステアリル|セチル|ベヘニル|ラウリル|ミリスチル|パルミチル|オレイル|'
    r'ジ|トリ|テトラ|ペンタ|モノ|ノニオン性|アニオン性|カチオン性|両性)*'
    r'(?:グリセリン|エタノール|スクワラン|ワセリン|パラフィン|'
    r'ヒアルロン酸|コラーゲン|セルロース|キサンタンガム|カルボマー|'
    r'酸化チタン|タルク|シリカ|マイカ|セラミド|レチノール|'
    r'トコフェロール|アスコルビン酸|ナイアシンアミド|'
    r'界面活性剤|乳化剤|防腐剤|酸化防止剤|紫外線吸収剤|'
    r'ソルビタン|グルタミン酸|アラニン|グリシン|'
    # エアゾール・噴射剤関連
    r'エアゾール|噴射剤|液化石油ガス|ジメチルエーテル|'
    r'二酸化炭素|炭酸ガス|窒素ガス|圧縮ガス|'
    r'イソペンタン|イソブタン|プロパン|ノルマルブタン|'
    # 泡沫・剤型関連
    r'泡沫|フォーム|ムース|泡状|油性|油状|'
    r'油中水型|水中油型|非水系|無水|'
    # ポリアルキレングリコールエーテル等
    r'ポリアルキレングリコール|ポリエチレングリコール|ポリプロピレングリコール|'
    r'ポリオキシエチレン|ポリオキシプロピレン|'
    r'アルキレンオキシド|エチレンオキシド|プロピレンオキシド|'
    # 揮発性炭化水素油
    r'揮発性炭化水素油|軽質イソパラフィン|イソドデカン|'
    r'シクロペンタシロキサン|揮発性シリコーン|'
    r'[ァ-ヴー]{3,}(?:酸|油|脂|剤|体|物|液|水|比|料)?)'
)

# 材料名パターン（積層体分野）
MATERIAL_PATTERN = re.compile(
    r'(?:ポリエチレン|ポリプロピレン|ポリエステル|ポリアミド|ナイロン|'
    r'ポリカーボネート|PET|PE|PP|PA|PC|PEN|EVOH|PVA|PVDC|'
    r'アルミニウム|銅|SUS|ステンレス|'
    r'エチレン[−・]ビニルアルコール|'
    r'(?:二軸)?延伸|無延伸|蒸着|コーティング|接着|'
    r'バリア層|シーラント層|基材層|中間層|表面層|'
    r'[ァ-ヴー]{3,}(?:層|膜|フィルム|シート)?)'
)

# 列挙を見つけるパターン
ENUMERATION_PATTERN = re.compile(
    r'(?:例えば|具体的には|としては|として|等の|などの|から選ばれる)[、,]?\s*(.+?)(?:[。．.]|が[、,])'
)

# 数値条件パターン
NUMERIC_KEYWORD_PATTERN = re.compile(
    r'(\d+\.?\d*)\s*(?:～|~|−|-|から)\s*(\d+\.?\d*)\s*'
    r'(質量%|重量%|mass%|wt%|vol%|体積%|%|ppm|mm|μm|nm)'
)


def load_dictionary(field, dict_name):
    """分野辞書を読み込み"""
    dict_path = PROJECT_ROOT / "dictionaries" / field / dict_name
    if dict_path.exists():
        with open(dict_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def extract_keywords_from_segments(segments, field):
    """分節テキストからキーワードを抽出"""
    keywords_by_segment = {}

    # 請求項1の分節のみ対象
    claim1 = None
    for claim in segments:
        if claim["claim_number"] == 1:
            claim1 = claim
            break

    if claim1 is None:
        return keywords_by_segment

    if field == "cosmetics":
        pattern = INGREDIENT_PATTERN_JP
    else:
        pattern = MATERIAL_PATTERN

    for seg in claim1["segments"]:
        seg_id = seg["id"]
        text = seg["text"]
        found = pattern.findall(text)
        # 重複除去しつつ順序保持
        seen = set()
        unique = []
        for kw in found:
            if kw not in seen and len(kw) >= 2:
                seen.add(kw)
                unique.append({"term": kw, "source": "請求項1", "type": "上位概念"})
        keywords_by_segment[seg_id] = unique

    return keywords_by_segment


def extract_concrete_names_from_description(hongan, field):
    """明細書から具体名を抽出"""
    concrete_names = {}  # keyword -> [concrete names]

    if field == "cosmetics":
        pattern = INGREDIENT_PATTERN_JP
    else:
        pattern = MATERIAL_PATTERN

    for para in hongan.get("paragraphs", []):
        # 列挙パターンを探す
        for m in ENUMERATION_PATTERN.finditer(para["text"]):
            enum_text = m.group(1)
            names = re.split(r'[、,，]|\s+', enum_text)
            for name in names:
                name = name.strip()
                if len(name) >= 2 and pattern.search(name):
                    concrete_names.setdefault(para["id"], []).append({
                        "term": name,
                        "source": f"【{para['id']}】",
                        "type": "具体名",
                    })

    return concrete_names


def lookup_fterm(keywords, field):
    """キーワードからFtermを逆引き"""
    if field == "cosmetics":
        ingredient_to_fterm = load_dictionary("cosmetics", "ingredient_to_fterm.json")
        fterm_structure = load_dictionary("cosmetics", "fterm_4c083_structure.json")
    elif field == "laminate":
        ingredient_to_fterm = load_dictionary("laminate", "materials_to_fterm.json")
        fterm_structure = load_dictionary("laminate", "fterm_4f100_structure.json")
    else:
        return {}

    fterm_results = {}
    for kw in keywords:
        term = kw["term"]
        fterm_codes = ingredient_to_fterm.get(term, [])
        for code in fterm_codes:
            # Fterm構造辞書からラベルを取得
            category = code[:6] if len(code) >= 6 else code  # e.g., "4C083AC12"
            theme = code[:5]  # e.g., "4C083"
            sub = code[5:7] if len(code) >= 7 else ""  # e.g., "AC"
            entry = code[5:] if len(code) >= 7 else ""  # e.g., "AC12"

            desc = ""
            cats = fterm_structure.get("categories", {})
            if sub in cats:
                entries = cats[sub].get("entries", {})
                if entry in entries:
                    desc = entries[entry].get("label", "")
                elif sub in cats:
                    desc = cats[sub].get("label", "")

            fterm_results.setdefault(term, []).append({
                "code": code,
                "desc": desc,
                "suffix": ".1で請求項限定",
            })

    return fterm_results


def build_keyword_groups(segments, hongan, field):
    """キーワードグループを構築"""
    # 1. 分節からキーワード抽出
    seg_keywords = extract_keywords_from_segments(segments, field)

    # 2. 明細書から具体名抽出
    concrete = extract_concrete_names_from_description(hongan, field)

    # 3. 辞書ロード
    ingredient_to_fterm = load_dictionary(field, "ingredient_to_fterm.json")
    synonyms = load_dictionary(field, "synonyms.json")
    inci_ja = load_dictionary(field, "inci_ja.json") if field == "cosmetics" else {}

    groups = []
    group_id = 0

    for seg_id, kw_list in seg_keywords.items():
        if not kw_list:
            continue

        group_id += 1
        if group_id > 7:
            break  # 最大7グループ

        # メインキーワード（最初のもの）をラベルに
        main_kw = kw_list[0]["term"] if kw_list else seg_id

        # このグループのキーワードを集約
        all_keywords = list(kw_list)

        # 具体名を追加
        for para_id, names in concrete.items():
            for name_entry in names:
                # メインキーワードと関連する具体名を紐付け
                if any(kw["term"] in name_entry["term"] or
                       name_entry["term"] in kw["term"]
                       for kw in kw_list):
                    all_keywords.append(name_entry)

        # 表記ゆれ（同義語）を追加
        for kw in list(kw_list):
            syns = synonyms.get(kw["term"], [])
            for syn in syns:
                all_keywords.append({
                    "term": syn,
                    "source": "辞書",
                    "type": "同義語",
                })

        # INCI名を追加
        for kw in list(kw_list):
            inci = inci_ja.get(kw["term"], {})
            if isinstance(inci, dict) and inci.get("inci_name"):
                all_keywords.append({
                    "term": inci["inci_name"],
                    "source": "辞書",
                    "type": "英名",
                })
            elif isinstance(inci, str):
                all_keywords.append({
                    "term": inci,
                    "source": "辞書",
                    "type": "英名",
                })

        # Fterm検索
        fterm_results = lookup_fterm(kw_list, field)

        search_codes = {"fterm": [], "fi": []}
        for term, fterms in fterm_results.items():
            for ft in fterms:
                if ft not in search_codes["fterm"]:
                    search_codes["fterm"].append(ft)

        # 重複除去
        seen_terms = set()
        unique_keywords = []
        for kw in all_keywords:
            if kw["term"] not in seen_terms:
                seen_terms.add(kw["term"])
                unique_keywords.append(kw)

        group = {
            "group_id": group_id,
            "label": main_kw,
            "color": COLOR_NAMES.get(group_id, "黒"),
            "segment_ids": [seg_id],
            "keywords": unique_keywords,
            "search_codes": search_codes,
        }
        groups.append(group)

    return groups


def suggest_keywords(hongan, segments, field):
    """キーワード自動提案のメインエントリポイント

    Parameters:
        hongan: 本願構造化テキスト (hongan.json)
        segments: 請求項分節 (segments.json)
        field: "cosmetics" | "laminate"

    Returns:
        キーワードグループのリスト
    """
    return build_keyword_groups(segments, hongan, field)
