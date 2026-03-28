#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
分節別キーワード提案モジュール

Phase 1: 正規表現による即座の抽出（辞書も活用）
Phase 2: Claude APIによるリッチな提案（anthropic SDK）

入力:
- segments: 請求項分節 (segments.json)
- hongan: 本願構造化テキスト (hongan.json)
- field: "cosmetics" | "laminate"

出力:
- 分節ごとのキーワードリスト (segment_keywords.json 形式)
"""

import os
import re
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# --- ストップワード ---

STOP_WORDS = {
    "前記", "含有", "含有する", "からなる", "有する", "備える",
    "において", "であって", "であり", "おける", "よる", "する",
    "された", "される", "および", "ならびに", "または", "もしくは",
    "以上", "以下", "未満", "超える", "含む", "特徴", "記載",
    "少なくとも", "それぞれ", "前記した", "さらに", "また",
    "これら", "それら", "当該", "所定", "各種", "種々",
    "化粧料", "組成物", "製剤",
}

# --- 正規表現パターン ---

# カタカナ語抽出（3文字以上）
RE_KATAKANA = re.compile(r'[ァ-ヴー]{3,}')

# 漢字語抽出（2文字以上、接尾辞付き）
RE_KANJI = re.compile(
    r'[一-龥]{2,}(?:剤|物|体|油|酸|液|層|膜|比|料|性|化|類|素|塩|基)?'
)

# 数値条件抽出（範囲）
RE_NUMERIC = re.compile(
    r'(\d+\.?\d*)\s*(?:～|~|−|-|から)\s*(\d+\.?\d*)\s*'
    r'(質量%|重量%|mass%|wt%|vol%|体積%|%|ppm|mm|μm|nm|℃)'
)

# 単独数値条件
RE_NUMERIC_SINGLE = re.compile(
    r'(\d+\.?\d*)\s*(質量%|重量%|mass%|wt%|ppm|mm|μm|nm|℃)\s*'
    r'(以上|以下|未満|超|を超える|より多い|より少ない)?'
)

# 括弧ラベル（(A)成分 等）
RE_PAREN_LABEL = re.compile(r'[\(（]([A-Za-zＡ-Ｚ])[\)）]\s*([^、。\)）]{2,20})')


# --- 辞書読み込み ---

def _load_dict(field, dict_name):
    """分野辞書を読み込み"""
    dict_path = PROJECT_ROOT / "dictionaries" / field / dict_name
    if dict_path.exists():
        try:
            with open(dict_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


# --- Phase 1: 正規表現ベース ---

def recommend_regex(segments, hongan, field):
    """正規表現ベースのキーワード抽出

    Parameters:
        segments: 請求項分節データ (segments.json)
        hongan: 本願構造化テキスト (hongan.json) — 現在は未使用、将来拡張用
        field: "cosmetics" | "laminate"

    Returns:
        分節ごとのキーワードリスト
    """
    synonyms = _load_dict(field, "synonyms.json")
    inci_ja = _load_dict(field, "inci_ja.json") if field == "cosmetics" else {}

    results = []
    for claim in segments:
        # 独立請求項のみ対象（is_independent がなければ claim_number==1 で判定）
        is_indep = claim.get("is_independent", claim.get("claim_number") == 1)
        if not is_indep:
            continue

        for seg in claim.get("segments", []):
            seg_id = seg["id"]
            text = seg["text"]
            keywords = []

            # 1. カタカナ語
            for m in RE_KATAKANA.finditer(text):
                term = m.group()
                if term not in STOP_WORDS and len(term) >= 3:
                    keywords.append({"term": term, "source": "regex", "type": "カタカナ抽出"})

            # 2. 漢字語
            for m in RE_KANJI.finditer(text):
                term = m.group()
                if term not in STOP_WORDS and len(term) >= 2:
                    keywords.append({"term": term, "source": "regex", "type": "漢字抽出"})

            # 3. 数値条件（範囲）
            for m in RE_NUMERIC.finditer(text):
                cond = m.group()
                keywords.append({"term": cond, "source": "regex", "type": "数値条件"})

            # 4. 単独数値条件（範囲と重複しないもの）
            existing_terms = {kw["term"] for kw in keywords}
            for m in RE_NUMERIC_SINGLE.finditer(text):
                cond = m.group()
                if cond not in existing_terms:
                    keywords.append({"term": cond, "source": "regex", "type": "数値条件"})

            # 5. 括弧ラベル
            for m in RE_PAREN_LABEL.finditer(text):
                label = m.group(1)
                content = m.group(2).strip()
                keywords.append({
                    "term": f"({label}){content}",
                    "source": "regex",
                    "type": "括弧ラベル",
                })

            # 6. 辞書から同義語・英名を付与
            for kw in list(keywords):
                term = kw["term"]
                # 同義語
                syns = synonyms.get(term, [])
                for syn in syns:
                    keywords.append({"term": syn, "source": "dict", "type": "同義語"})
                # INCI名
                if term in inci_ja:
                    inci = inci_ja[term]
                    inci_name = inci.get("inci_name", inci) if isinstance(inci, dict) else inci
                    if inci_name:
                        keywords.append({"term": inci_name, "source": "dict", "type": "INCI英名"})

            # 重複除去（term が同じものは最初の1つだけ残す）
            seen = set()
            unique = []
            for kw in keywords:
                if kw["term"] not in seen:
                    seen.add(kw["term"])
                    unique.append(kw)

            results.append({
                "segment_id": seg_id,
                "segment_text": text,
                "keywords": unique,
            })

    return results


# --- Phase 2: AI ベース ---

def _extract_description_excerpt(hongan, max_chars=3000):
    """明細書から実施例・定義部分を抜粋"""
    if not hongan:
        return ""

    priority_sections = ["実施例", "手段", "効果", "実施形態"]
    lines = []
    total = 0

    for section_name in priority_sections:
        for para in hongan.get("paragraphs", []):
            if para.get("section") == section_name:
                text = f"【{para['id']}】{para['text']}"
                if total + len(text) > max_chars:
                    break
                lines.append(text)
                total += len(text)

    return "\n".join(lines)


def _extract_json_array(raw_text):
    """テキストからJSON配列を抽出（search_prompt_generator.py と同じロジック）"""
    # パターン1: ```json ... ``` ブロック
    json_block_pattern = re.compile(r'```(?:json)?\s*\n?(.*?)\n?\s*```', re.DOTALL)
    matches = json_block_pattern.findall(raw_text)

    for match in matches:
        try:
            data = json.loads(match)
            if isinstance(data, list) and len(data) > 0:
                return data
        except json.JSONDecodeError:
            continue

    # パターン2: 最外側の [ ... ] を探す
    bracket_depth = 0
    start = None
    for i, ch in enumerate(raw_text):
        if ch == '[':
            if bracket_depth == 0:
                start = i
            bracket_depth += 1
        elif ch == ']':
            bracket_depth -= 1
            if bracket_depth == 0 and start is not None:
                candidate = raw_text[start:i + 1]
                try:
                    data = json.loads(candidate)
                    if isinstance(data, list) and len(data) > 0:
                        return data
                except json.JSONDecodeError:
                    start = None
                    continue

    return None


def recommend_ai(segments, hongan, field):
    """Claude APIベースのキーワード提案

    ANTHROPIC_API_KEY 環境変数が未設定の場合は空リストを返す（エラーにしない）。

    Parameters:
        segments: 請求項分節データ
        hongan: 本願構造化テキスト
        field: 技術分野

    Returns:
        分節ごとのキーワードリスト（source="ai"）
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.info("ANTHROPIC_API_KEY 未設定 — AI提案スキップ")
        return []

    # 分節リストを組み立て
    seg_lines = []
    seg_ids = []
    seg_texts = {}
    for claim in segments:
        is_indep = claim.get("is_independent", claim.get("claim_number") == 1)
        if not is_indep:
            continue
        for seg in claim.get("segments", []):
            seg_lines.append(f'{seg["id"]}: {seg["text"]}')
            seg_ids.append(seg["id"])
            seg_texts[seg["id"]] = seg["text"]

    if not seg_lines:
        return []

    field_label = {"cosmetics": "化粧品", "laminate": "積層体"}.get(field, field)

    # 明細書から参考情報を抽出
    description_excerpt = _extract_description_excerpt(hongan, max_chars=3000)

    prompt = f"""あなたは{field_label}分野の特許調査の専門家です。
以下は特許請求項の構成要件を分節したリストです。
各分節について、先行技術検索（Google Patents、J-PlatPat）に使うべきキーワードを提案してください。

【分野】{field_label}

【分節リスト】
{chr(10).join(seg_lines)}

【本願明細書の参考情報】
{description_excerpt}

【出力形式】JSON配列で返してください:
[
  {{
    "segment_id": "1A",
    "keywords_ja": ["日本語キーワード1", "同義語", "上位概念", ...],
    "keywords_en": ["English keyword", "INCI name", ...],
    "numeric_conditions": ["5000ppm以上", ...],
    "search_phrases": ["油性 エアゾール 化粧料", ...]
  }}
]

【提案の指針】
- 日本語: 特許公報で使われる表記、一般名、化学名、商品名、上位概念、下位概念を幅広く
- 英語: INCI名、学術用語、米国特許で使われる表現
- 数値条件: 分節中に数値範囲があれば検索用に整形
- 検索フレーズ: Google Patentsに投げる2〜4語の組み合わせ
- 分節に技術的内容がない場合（「化粧料」等の製品カテゴリのみ）はキーワード少なめでOK
"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_text = response.content[0].text
        ai_data = _extract_json_array(raw_text)

        if not ai_data:
            logger.warning("AIレスポンスからJSONを抽出できませんでした")
            return []

        # 正規化: AI結果を統一形式に変換
        results = []
        for item in ai_data:
            seg_id = item.get("segment_id", "")
            if seg_id not in seg_ids:
                continue
            keywords = []
            for term in item.get("keywords_ja", []):
                keywords.append({"term": term, "source": "ai", "type": "AI提案(日本語)"})
            for term in item.get("keywords_en", []):
                keywords.append({"term": term, "source": "ai", "type": "AI提案(英語)"})
            for term in item.get("numeric_conditions", []):
                keywords.append({"term": term, "source": "ai", "type": "AI提案(数値条件)"})
            for phrase in item.get("search_phrases", []):
                keywords.append({"term": phrase, "source": "ai", "type": "AI提案(検索フレーズ)"})

            results.append({
                "segment_id": seg_id,
                "segment_text": seg_texts.get(seg_id, ""),
                "keywords": keywords,
            })

        logger.info("AI提案: %d分節のキーワードを取得", len(results))
        return results

    except ImportError:
        logger.warning("anthropic パッケージ未インストール — AI提案スキップ")
        return []
    except Exception as e:
        logger.warning("AI キーワード提案エラー: %s", e)
        return []


# --- メインエントリポイント ---

def suggest_keywords_by_segment(segments, hongan, field):
    """分節別キーワード提案のメインエントリポイント

    Phase 1（正規表現）と Phase 2（AI）の結果をマージして返す。

    Parameters:
        segments: 請求項分節データ (segments.json)
        hongan: 本願構造化テキスト (hongan.json)
        field: "cosmetics" | "laminate"

    Returns:
        分節ごとのキーワードリスト (segment_keywords.json 形式)
    """
    # Phase 1: 正規表現
    regex_results = recommend_regex(segments, hongan, field)

    # Phase 2: AI
    ai_results = recommend_ai(segments, hongan, field)

    # マージ: regex をベースに、ai で補完
    ai_by_seg = {r["segment_id"]: r for r in ai_results}

    merged = []
    for reg in regex_results:
        seg_id = reg["segment_id"]
        all_keywords = list(reg["keywords"])

        # AI結果をマージ（重複除去）
        if seg_id in ai_by_seg:
            existing_terms = {kw["term"] for kw in all_keywords}
            for kw in ai_by_seg[seg_id]["keywords"]:
                if kw["term"] not in existing_terms:
                    all_keywords.append(kw)
                    existing_terms.add(kw["term"])

        merged.append({
            "segment_id": seg_id,
            "segment_text": reg["segment_text"],
            "keywords": all_keywords,
        })

    return merged
