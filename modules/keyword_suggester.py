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

from modules.fterm_dict import codes_for_term, get_nodes, get_synonyms, get_inci
from modules.text_preprocessing import preprocess_text as _preprocess_text
from modules.description_analyzer import (
    extract_example_compounds,
    extract_description_compounds,
    COMPONENT_PATTERN,
    CONCEPT_HINTS,
)

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
    8: "黄",
    9: "シアン",
    10: "ライム",
    11: "ピンク",
    12: "ブラウン",
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
    """キーワードからFtermを逆引き（木構造版）"""
    nodes = get_nodes(field)
    fterm_results = {}
    for kw in keywords:
        term = kw["term"]
        codes = codes_for_term(term, field)
        for code in codes:
            node = nodes.get(code, {})
            fterm_results.setdefault(term, []).append({
                "code": code,
                "desc": node.get("label", ""),
                "suffix": ".1で請求項限定",
            })
    return fterm_results


def build_keyword_groups(segments, hongan, field):
    """請求項1の全分節に対してグループを生成する。

    スキップなし・上限なし。
    キーワードの優先順位:
      1. 実施例で使用された化合物（最重要）
      2. 説明段落の例示化合物
      3. 配合目的・配合量
      4. 辞書（synonyms, inci_ja）による同義語・英名
      5. Fterm辞書による展開
    """
    # 請求項1の分節を取得
    claim1_segs = []
    for claim in segments:
        if claim.get("claim_number") == 1:
            claim1_segs = claim.get("segments", [])
            break
    if not claim1_segs:
        return []

    # ① 実施例使用化合物を一括収集（全分節共通リソース）
    example_compounds = extract_example_compounds(hongan)

    # 辞書ロード（既存のまま）
    synonyms = get_synonyms(field)
    inci_ja = get_inci(field) if field == "cosmetics" else {}
    nodes = get_nodes(field)

    groups = []

    for idx, seg in enumerate(claim1_segs):
        seg_id = seg["id"]
        seg_text = _preprocess_text(seg.get("text", ""))

        seen = set()
        keywords = []

        def add(term, source, kw_type):
            t = term.strip()
            if t and len(t) >= 2 and t not in seen:
                seen.add(t)
                keywords.append({"term": t, "source": source, "type": kw_type})

        # ── 上位概念語を分節テキストから取得 ──
        concept = ""
        for m in COMPONENT_PATTERN.finditer(seg_text):
            name = m.group(0).strip()
            if len(name) >= 3:
                concept = name
                add(name, "請求項1", "上位概念")
                break
        # カタカナフォールバック
        if not concept:
            m = re.search(r'[ァ-ヴー]{4,}', seg_text)
            if m:
                concept = m.group(0)
                add(concept, "請求項1", "上位概念")

        # ── ② 実施例使用化合物を上位概念と照合して紐付け ──
        hints = CONCEPT_HINTS.get(concept, [])
        for ec in example_compounds:
            # ヒント語でマッチ、または上位概念が化合物名に含まれる
            if (any(h in ec["term"] for h in hints)
                    or (concept and concept in ec["term"])):
                add(ec["term"], ec["source"], ec["type"])

        # ── ③ 説明段落の例示・目的・配合量 ──
        if concept:
            for kw in extract_description_compounds(hongan, concept):
                add(kw["term"], kw["source"], kw["type"])

        # ── ④ 辞書: synonyms.json（既存活用） ──
        for syn in synonyms.get(concept, []):
            add(syn, "辞書(synonyms)", "同義語")

        # ── ⑤ 辞書: inci_ja.json（既存活用） ──
        inci = inci_ja.get(concept, {})
        if isinstance(inci, dict) and inci.get("inci_name"):
            add(inci["inci_name"], "辞書(INCI)", "英名(INCI)")
        elif isinstance(inci, str) and inci:
            add(inci, "辞書(INCI)", "英名(INCI)")

        # ── ⑥ Fterm辞書（既存活用） ──
        fterm_list = []
        seen_codes = set()
        for code in codes_for_term(concept, field):
            if code not in seen_codes:
                seen_codes.add(code)
                node = nodes.get(code, {})
                fterm_list.append({
                    "code": code,
                    "desc": node.get("label", ""),
                    "suffix": ".1で請求項限定",
                })

        # ── フォールバック: キーワードが0件なら分節テキストを登録 ──
        if not keywords:
            simplified = re.sub(
                r'[はがをにでのもとへや、。（）【】\s]', '', seg_text
            )
            add(simplified[:20] or seg_id, "分節テキスト", "フォールバック")

        groups.append({
            "group_id": idx + 1,
            "label": concept or seg_id,
            "color": COLOR_NAMES.get(idx + 1, "黒"),
            "segment_ids": [seg_id],
            "keywords": keywords,
            "search_codes": {"fterm": fterm_list, "fi": []},
        })

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


def build_keyword_groups_from_pipeline(pipeline_result, segments, field,
                                       hongan=None):
    """
    recommend_regex() の出力（分節×キーワード）を
    グループ構造（group_id, label, keywords, search_codes）に変換する。
    全分節についてグループを生成（空キーワードの分節もスキップしない）。

    hongan が渡された場合、description_analyzer で実施例化合物・
    明細書具体名・配合目的/量を追加エンリッチする。
    """
    nodes = get_nodes(field)
    synonyms = get_synonyms(field)
    inci_ja = get_inci(field) if field == "cosmetics" else {}

    # ── 実施例化合物を一括収集（hongan がある場合のみ） ──
    example_compounds = []
    if hongan:
        example_compounds = extract_example_compounds(hongan)

    groups = []
    group_id = 0

    for item in pipeline_result:
        seg_id = item["segment_id"]
        kws = list(item["keywords"])  # コピー（元を汚さない）
        seg_text = _preprocess_text(item.get("segment_text", ""))

        group_id += 1
        seen = {kw["term"] for kw in kws}

        def _add(term, source, kw_type):
            t = term.strip()
            if t and len(t) >= 2 and t not in seen:
                seen.add(t)
                kws.append({"term": t, "source": source, "type": kw_type})

        # ── 分節テキストから上位概念語を特定 ──
        concept = ""
        for m in COMPONENT_PATTERN.finditer(seg_text):
            name = m.group(0).strip()
            if len(name) >= 3:
                concept = name
                break
        if not concept:
            m = re.search(r'[ァ-ヴー]{4,}', seg_text)
            if m:
                concept = m.group(0)

        # ── 実施例化合物を上位概念と照合 ──
        if concept and example_compounds:
            hints = CONCEPT_HINTS.get(concept, [])
            for ec in example_compounds:
                if (any(h in ec["term"] for h in hints)
                        or (concept and concept in ec["term"])):
                    _add(ec["term"], ec["source"], ec["type"])

        # ── 明細書具体名・配合目的・配合量 ──
        if concept and hongan:
            for kw in extract_description_compounds(hongan, concept):
                _add(kw["term"], kw["source"], kw["type"])

        # ── 辞書: synonyms ──
        if concept:
            for syn in synonyms.get(concept, []):
                _add(syn, "辞書(synonyms)", "同義語")

        # ── 辞書: INCI ──
        if concept:
            inci = inci_ja.get(concept, {})
            if isinstance(inci, dict) and inci.get("inci_name"):
                _add(inci["inci_name"], "辞書(INCI)", "英名(INCI)")
            elif isinstance(inci, str) and inci:
                _add(inci, "辞書(INCI)", "英名(INCI)")

        # ── Fterm コード収集 ──
        fterm_list = []
        seen_codes = set()
        for kw in kws:
            for code in codes_for_term(kw["term"], field):
                if code not in seen_codes:
                    node = nodes.get(code, {})
                    fterm_list.append({
                        "code": code,
                        "desc": node.get("label", ""),
                        "suffix": ".1で請求項限定",
                    })
                    seen_codes.add(code)

        # ── ラベル決定 ──
        ai_label = item.get("_ai_label", "")
        if ai_label:
            label = ai_label
        elif concept:
            label = concept
        elif kws:
            label = kws[0]["term"]
        else:
            label = seg_text[:20].strip() if seg_text else seg_id

        # ── フォールバック: キーワードが0件 ──
        if not kws:
            simplified = re.sub(
                r'[はがをにでのもとへや、。（）【】\s]', '', seg_text
            )
            _add(simplified[:20] or seg_id, "分節テキスト", "フォールバック")

        groups.append({
            "group_id": group_id,
            "label": label,
            "color": COLOR_NAMES.get(group_id, "黒"),
            "segment_ids": [seg_id],
            "keywords": kws,
            "search_codes": {"fterm": fterm_list, "fi": []},
        })

    return groups
