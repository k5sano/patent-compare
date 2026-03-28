#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
請求項自動分節モジュール

入力: 請求項テキスト（claims配列）
出力: 構成要件リスト（{claim_number}{alphabet} 形式）

分節ルール:
- 「、」「と、」「を含有し、」等で区切られた要素を分離
- 「前記○○が」「前記○○における」等の限定を検出
- 数値範囲を1つの要素として保持
- 末尾の物の名前を独立した要素に
"""

import re
from string import ascii_uppercase


# 分節の切れ目パターン（優先順位順）
SPLIT_PATTERNS = [
    # 明示的な構成要件の区切り
    re.compile(r'(?<=、)\s*(?=(?:前記|さらに|また|かつ))'),
    # 「を含有し、」「を含み、」「からなり、」
    re.compile(r'(を含有し|を含み|からなり|を有し|を備え|であって|であり|において)([、,]\s*)'),
    # 「と、」（列挙の最終要素直前）
    re.compile(r'(?<=と)[、,]\s*'),
]

# 物の名前パターン（末尾の製品カテゴリ）
PRODUCT_PATTERNS = re.compile(
    r'(?:化粧料|組成物|製剤|組成|フィルム|積層体|積層フィルム|シート|不織布|'
    r'成形体|構造体|容器|粉体|乳化物|ゲル|液剤|スプレー|エアゾール|'
    r'泡沫|方法|製造方法|処理方法|使用方法)[\s。．.]*$'
)

# 数値範囲パターン（分割しないようにする）
NUMERIC_RANGE = re.compile(
    r'\d+\.?\d*\s*(?:～|~|−|-|から)\s*\d+\.?\d*\s*'
    r'(?:質量%|重量%|mass%|wt%|vol%|体積%|%|mm|μm|nm|cm|m|g|mg|kg|mL|L|Pa|MPa|kPa|℃|°C)'
)

# 数値条件パターン
NUMERIC_CONDITION = re.compile(
    r'(?:\d+\.?\d*\s*(?:質量%|重量%|mass%|wt%|vol%|体積%|%|mm|μm|nm|cm)'
    r'(?:\s*(?:以上|以下|未満|超|より(?:多い|少ない|大きい|小さい)))?)'
)


def _protect_numeric_ranges(text):
    """数値範囲中のカンマ等を保護する"""
    protected = text
    for m in NUMERIC_RANGE.finditer(text):
        original = m.group()
        replaced = original.replace('、', '\x00').replace(',', '\x01')
        protected = protected.replace(original, replaced)
    return protected


def _restore_numeric_ranges(text):
    """保護した数値範囲を復元"""
    return text.replace('\x00', '、').replace('\x01', ',')


def _split_claim_text(claim_text):
    """請求項テキストを構成要件に分節"""
    segments = []

    # 数値範囲を保護
    text = _protect_numeric_ranges(claim_text)

    # ステップ1: 主要な切れ目で分割
    # 「であって、」「を含有し、」等で大きく分ける
    major_splits = re.split(
        r'(であって[、,]|において[、,])',
        text
    )

    parts = []
    i = 0
    while i < len(major_splits):
        part = major_splits[i]
        # セパレータ部分を前のパートに結合
        if i + 1 < len(major_splits) and re.match(r'であって[、,]|において[、,]', major_splits[i + 1]):
            part = part + major_splits[i + 1]
            i += 2
        else:
            i += 1
        if part.strip():
            parts.append(part.strip())

    if not parts:
        parts = [text]

    # ステップ2: 各パート内をさらに分割
    for part in parts:
        sub_segments = _split_sub_elements(part)
        segments.extend(sub_segments)

    # 数値範囲を復元
    segments = [_restore_numeric_ranges(s.strip()) for s in segments if s.strip()]

    # 末尾の物の名前を分離
    segments = _separate_product_name(segments)

    return segments


def _split_sub_elements(text):
    """サブ要素への分割（「を含有し、」「、」等で分割）"""
    # 「を含有し、」「を含み、」「であり、」等のパターンで分割
    result = re.split(
        r'(?<=を含有し)[、,]\s*'
        r'|(?<=を含み)[、,]\s*'
        r'|(?<=からなり)[、,]\s*'
        r'|(?<=を有し)[、,]\s*'
        r'|(?<=を備え)[、,]\s*'
        r'|(?<=であり)[、,]\s*',
        text
    )

    # さらに細かい分割：列挙パターン
    final = []
    for part in result:
        if not part.strip():
            continue
        # 「(A)と、(B)と、(C)と を含有する」パターン
        sub = re.split(r'(?<=と)[、,]\s*(?=\S)', part)
        if len(sub) > 1:
            final.extend(sub)
        else:
            # 「(A)、(B)、(C)及び(D)を含有する」パターン（長い列挙）
            # ただし、短い句読点区切りは分割しない
            enum_parts = re.split(r'[、,]\s*(?=(?:前記|さらに|及び|並びに))', part)
            if len(enum_parts) > 1:
                final.extend(enum_parts)
            else:
                final.append(part)

    return final


def _separate_product_name(segments):
    """末尾の「...化粧料。」のような物の名前を独立した要素にする"""
    if not segments:
        return segments

    last = segments[-1]
    m = PRODUCT_PATTERNS.search(last)
    if m and len(last) > len(m.group()) + 5:
        # 物の名前部分を分離
        product_text = last[m.start():].strip().rstrip('。．.')
        before_text = last[:m.start()].strip().rstrip('、,')
        if before_text and product_text:
            segments[-1] = before_text
            segments.append(product_text)

    return segments


def segment_single_claim(claim):
    """単一の請求項を分節"""
    claim_num = claim["number"]
    text = claim["text"]
    is_independent = claim.get("is_independent", True)

    if is_independent:
        raw_segments = _split_claim_text(text)
    else:
        # 従属請求項は追加限定部分のみ分節
        # 「請求項Xに記載の...であって、...」の「...」部分を抽出
        dep_match = re.search(
            r'記載の.+?(?:であって|において|のうち|における)[、,]?\s*(.+)',
            text, re.DOTALL
        )
        if dep_match:
            additional_text = dep_match.group(1)
            raw_segments = _split_claim_text(additional_text)
        else:
            raw_segments = [text]

    # アルファベット付きIDを生成
    segments = []
    for i, seg_text in enumerate(raw_segments):
        alpha = ascii_uppercase[i] if i < 26 else f"A{i - 25}"
        segments.append({
            "id": f"{claim_num}{alpha}",
            "text": seg_text,
        })

    return {
        "claim_number": claim_num,
        "is_independent": is_independent,
        "dependencies": claim.get("dependencies", []),
        "full_text": text,
        "segments": segments,
    }


def segment_claims(claims):
    """全請求項を分節

    Parameters:
        claims: pdf_extractor.pyの出力のclaims配列

    Returns:
        分節結果のリスト
    """
    results = []
    for claim in claims:
        result = segment_single_claim(claim)
        results.append(result)
    return results
