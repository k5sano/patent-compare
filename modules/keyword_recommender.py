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
    "配合", "処方", "調製", "混合", "工程", "方法", "手段", "構成", "形態",
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
    """正規表現ベースのキーワード抽出（辞書活用強化版）

    Parameters:
        segments: 請求項分節データ (segments.json)
        hongan: 本願構造化テキスト (hongan.json)
        field: "cosmetics" | "laminate"

    Returns:
        分節ごとのキーワードリスト
    """
    synonyms = _load_dict(field, "synonyms.json")
    inci_ja = _load_dict(field, "inci_ja.json") if field == "cosmetics" else {}

    # 第1パス: 基本抽出で分節ごとの用語を収集
    seg_terms = {}  # seg_id -> [term strings]
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

            # 7. upper_concepts.json から下位概念・Fterm兄弟語を展開
            base_terms = [kw["term"] for kw in keywords if kw["source"] == "regex"]
            expanded = _expand_upper_concepts(base_terms, field)
            keywords.extend(expanded)

            # 8. ingredient_to_fterm.json の逆引きで兄弟語追加
            fterm_siblings = _expand_fterm_siblings(base_terms, field)
            keywords.extend(fterm_siblings)

            # 分節ごとの用語を記録（実施例マッチング用）
            seg_terms[seg_id] = base_terms

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

    # 第2パス: 明細書の実施例から具体名を追加
    concrete_by_seg = _extract_concrete_from_examples(hongan, seg_terms, field)
    for item in results:
        sid = item["segment_id"]
        if sid in concrete_by_seg:
            existing = {kw["term"] for kw in item["keywords"]}
            for ckw in concrete_by_seg[sid]:
                if ckw["term"] not in existing:
                    item["keywords"].append(ckw)
                    existing.add(ckw["term"])

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


# --- 辞書活用: 上位概念展開 + Fterm兄弟語 ---

def _expand_upper_concepts(terms, field):
    """upper_concepts.json から下位概念・Fterm兄弟語を展開"""
    upper_concepts = _load_dict(field, "upper_concepts.json")
    if not upper_concepts:
        return []

    expanded = []
    for term in terms:
        # term が上位概念辞書のキーにある → そのエントリの同義語・兄弟語を取得
        entry = upper_concepts.get(term)
        if not entry:
            continue
        # same_fterm_siblings: 同じFtermの兄弟成分
        for sib in entry.get("same_fterm_siblings", []):
            expanded.append({"term": sib, "source": "dict", "type": "Fterm兄弟語"})
        # broader_fterm_siblings: より広いFtermの兄弟成分
        for bsib in entry.get("broader_fterm_siblings", []):
            expanded.append({"term": bsib, "source": "dict", "type": "上位Fterm兄弟語"})
        # brand_names
        for bn in entry.get("brand_names", []):
            expanded.append({"term": bn, "source": "dict", "type": "商品名"})

    # 逆引き: terms の各語が upper_concepts の upper_concepts リストに含まれている場合
    # → そのキー（下位概念）をキーワードに追加
    for ingredient, data in upper_concepts.items():
        for term in terms:
            if term in data.get("upper_concepts", []):
                expanded.append({"term": ingredient, "source": "dict", "type": "下位概念"})
                break

    return expanded


def _expand_fterm_siblings(terms, field):
    """ingredient_to_fterm.json の逆引きで同じFtermを持つ成分を追加"""
    i2f = _load_dict(field, "ingredient_to_fterm.json")
    if not i2f:
        return []

    # まず terms の Fterm コードを集める
    fterm_codes = set()
    for term in terms:
        codes = i2f.get(term, [])
        fterm_codes.update(codes)

    if not fterm_codes:
        return []

    # 同じ Fterm を持つ他の成分を追加（最大10件）
    siblings = []
    count = 0
    for ingredient, codes in i2f.items():
        if ingredient in terms:
            continue
        if any(c in fterm_codes for c in codes):
            siblings.append({"term": ingredient, "source": "dict", "type": "Fterm兄弟語"})
            count += 1
            if count >= 10:
                break

    return siblings


def _extract_concrete_from_examples(hongan, seg_terms, field):
    """明細書の実施例セクションから具体的な用語を抽出し分節キーワードに紐付け"""
    if not hongan:
        return {}

    from modules.keyword_suggester import INGREDIENT_PATTERN_JP, MATERIAL_PATTERN
    pattern = INGREDIENT_PATTERN_JP if field == "cosmetics" else MATERIAL_PATTERN

    # seg_terms: {seg_id: [term1, term2, ...]}
    concrete_by_seg = {}  # seg_id -> [keywords]

    for para in hongan.get("paragraphs", []):
        if para.get("section") not in ("実施例", "実施形態"):
            continue
        text = para.get("text", "")
        found = pattern.findall(text)
        for name in found:
            if len(name) < 2:
                continue
            # どの分節に紐付くか: 分節のキーワードと関連性チェック
            for seg_id, terms in seg_terms.items():
                for t in terms:
                    if t in name or name in t:
                        concrete_by_seg.setdefault(seg_id, []).append({
                            "term": name,
                            "source": "spec",
                            "type": "実施例具体名",
                        })
                        break

    return concrete_by_seg


# --- keyword_dictionary.json からのキーワード構築 ---

def recommend_from_dictionary(case_dir, segments):
    """keyword_dictionary.json が存在すればそこからキーワードを構築

    各分節テキストを tech_analysis の要素(A-F)テキストとマッチングし、
    該当要素のコア語・拡張語をその分節のキーワードとして採用。

    Returns:
        segment_keywords 形式のリスト、またはNone（ファイルなしの場合）
    """
    kw_dict_path = Path(case_dir) / "search" / "keyword_dictionary.json"
    if not kw_dict_path.exists():
        return None

    try:
        with open(kw_dict_path, "r", encoding="utf-8") as f:
            kw_dict = json.load(f)
    except Exception:
        return None

    elements = kw_dict.get("elements", {})
    if not elements:
        return None

    # tech_analysis から要素→分節IDのマッピングを取得
    tech_path = Path(case_dir) / "search" / "tech_analysis.json"
    element_seg_map = {}
    if tech_path.exists():
        try:
            with open(tech_path, "r", encoding="utf-8") as f:
                tech = json.load(f)
            for key, elem in tech.get("elements", {}).items():
                for sid in elem.get("segment_ids", []):
                    element_seg_map.setdefault(sid, []).append(key)
        except Exception:
            pass

    # 全分節IDを収集
    all_seg_ids = []
    seg_texts = {}
    for claim in segments:
        is_indep = claim.get("is_independent", claim.get("claim_number") == 1)
        if not is_indep:
            continue
        for seg in claim.get("segments", []):
            all_seg_ids.append(seg["id"])
            seg_texts[seg["id"]] = seg["text"]

    # 各分節にキーワードを割り当て
    results = []
    for seg_id in all_seg_ids:
        keywords = []
        seg_text = seg_texts.get(seg_id, "")

        # 方法1: tech_analysis のマッピングから要素を取得
        matched_elements = element_seg_map.get(seg_id, [])

        # 方法2: マッピングがなければ分節テキストとの語彙マッチ
        if not matched_elements:
            for key, elem in elements.items():
                core_terms = [ct.get("term", "") for ct in elem.get("core_terms", [])]
                for t in core_terms[:5]:
                    if t and t in seg_text:
                        matched_elements.append(key)
                        break

        # マッチした要素のキーワードを収集
        seen = set()
        for elem_key in matched_elements:
            elem = elements.get(elem_key, {})
            for ct in elem.get("core_terms", []):
                term = ct.get("term", "")
                if term and term not in seen:
                    seen.add(term)
                    keywords.append({
                        "term": term,
                        "source": ct.get("source", "dictionary"),
                        "type": "コア語",
                        "tier": "core",
                        "element": elem_key,
                    })
            for et in elem.get("extended_terms", []):
                term = et.get("term", "")
                if term and term not in seen:
                    seen.add(term)
                    keywords.append({
                        "term": term,
                        "source": et.get("source", "dictionary"),
                        "type": "拡張語",
                        "tier": "extended",
                        "element": elem_key,
                    })

        results.append({
            "segment_id": seg_id,
            "segment_text": seg_text,
            "keywords": keywords,
        })

    return results


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


def recommend_ai(segments, hongan, field, case_dir=None):
    """Claude APIベースのキーワード提案（3段階検索データ活用版）

    ANTHROPIC_API_KEY 環境変数が未設定の場合は空リストを返す（エラーにしない）。
    case_dir が指定されていれば、search/tech_analysis.json や
    search/presearch_candidates.json を参考情報として活用する。

    Parameters:
        segments: 請求項分節データ
        hongan: 本願構造化テキスト
        field: 技術分野
        case_dir: 案件ディレクトリ（3段階データ活用用、optional）

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

    # 3段階検索の中間データを参考情報として追加
    tech_analysis_text = ""
    presearch_text = ""
    if case_dir:
        tech_path = Path(case_dir) / "search" / "tech_analysis.json"
        if tech_path.exists():
            try:
                with open(tech_path, "r", encoding="utf-8") as f:
                    tech = json.load(f)
                lines = [f"コア文: {tech.get('core_sentence', '')}"]
                for key, elem in tech.get("elements", {}).items():
                    label = elem.get("label", key)
                    terms = ", ".join(elem.get("terms_ja", [])[:5])
                    lines.append(f"  {key} ({label}): {terms}")
                tech_analysis_text = "\n".join(lines)
            except Exception:
                pass

        cand_path = Path(case_dir) / "search" / "presearch_candidates.json"
        if cand_path.exists():
            try:
                with open(cand_path, "r", encoding="utf-8") as f:
                    cands = json.load(f)
                lines = []
                for c in (cands if isinstance(cands, list) else [])[:3]:
                    pid = c.get("patent_id", "?")
                    kts = ", ".join(c.get("key_terms_found", [])[:8])
                    lines.append(f"  {pid}: {kts}")
                presearch_text = "\n".join(lines)
            except Exception:
                pass

    extra_context = ""
    if tech_analysis_text:
        extra_context += f"\n\n【技術構造化（予備検索結果）】\n{tech_analysis_text}"
    if presearch_text:
        extra_context += f"\n\n【代表文献の語彙】\n{presearch_text}"

    field_specific = ""
    if field == "cosmetics":
        field_specific = "\n- 化粧品分野: INCI名、商品名（ブランド名）、化学名の3つを含めること"
    elif field == "laminate":
        field_specific = "\n- 積層体分野: 樹脂略称（PE/PP/PET等）と正式名称の両方を含めること"

    prompt = f"""あなたは{field_label}分野の特許調査の専門家です。
以下は特許請求項の構成要件を分節したリストです。
各分節について、先行技術検索（Google Patents、J-PlatPat）に使うべきキーワードを提案してください。

【分野】{field_label}

【分節リスト】
{chr(10).join(seg_lines)}

【本願明細書の参考情報】
{description_excerpt}{extra_context}

【出力形式】JSON配列で返してください:
[
  {{
    "segment_id": "1A",
    "keywords_ja": ["日本語キーワード1", "同義語", "上位概念", ...],
    "keywords_en": ["English keyword", "INCI name", ...],
    "numeric_conditions": ["5000ppm以上", ...],
    "search_phrases": ["油性 エアゾール 化粧料", ...],
    "tier_core": ["高適合コア語1", "コア語2"],
    "tier_extended": ["漏れ防止拡張語1"],
    "not_terms": ["ノイズ除外語1"]
  }}
]

【提案の指針】
- 代表公報の実語彙を優先して採用してください（推定より公報語彙が上位）
- キーワードをコア語（高適合、狭い検索式用）と拡張語（漏れ防止、広い検索式用）に分類
- NOT語（ノイズ除外語）も提案してください
- 日本語: 特許公報で使われる表記、一般名、化学名、商品名、上位概念、下位概念を幅広く
- 英語: INCI名、学術用語、米国特許で使われる表現
- 数値条件: 分節中に数値範囲があれば検索用に整形
- 検索フレーズ: Google Patentsに投げる2〜4語の組み合わせ
- 分節に技術的内容がない場合（「化粧料」等の製品カテゴリのみ）はキーワード少なめでOK{field_specific}
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

            # tier_core: 高適合コア語（優先度高）
            for term in item.get("tier_core", []):
                keywords.append({"term": term, "source": "ai", "type": "AI提案(コア語)", "tier": "core"})

            # tier_extended: 漏れ防止拡張語
            for term in item.get("tier_extended", []):
                keywords.append({"term": term, "source": "ai", "type": "AI提案(拡張語)", "tier": "extended"})

            # not_terms: ノイズ除外語
            for term in item.get("not_terms", []):
                keywords.append({"term": term, "source": "ai", "type": "AI提案(NOT語)", "tier": "not"})

            # 従来フィールドも引き続きパース
            for term in item.get("keywords_ja", []):
                keywords.append({"term": term, "source": "ai", "type": "AI提案(日本語)"})
            for term in item.get("keywords_en", []):
                keywords.append({"term": term, "source": "ai", "type": "AI提案(英語)"})
            for term in item.get("numeric_conditions", []):
                keywords.append({"term": term, "source": "ai", "type": "AI提案(数値条件)"})
            for phrase in item.get("search_phrases", []):
                keywords.append({"term": phrase, "source": "ai", "type": "AI提案(検索フレーズ)"})

            # 重複除去（term が同じものは最初のものを優先）
            seen = set()
            unique = []
            for kw in keywords:
                if kw["term"] not in seen:
                    seen.add(kw["term"])
                    unique.append(kw)

            results.append({
                "segment_id": seg_id,
                "segment_text": seg_texts.get(seg_id, ""),
                "keywords": unique,
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

def suggest_keywords_by_segment(segments, hongan, field, case_dir=None):
    """分節別キーワード提案のメインエントリポイント

    優先順位:
    1. keyword_dictionary.json があればそれをベースに構築（3段階検索完了時）
    2. なければ Phase 1（正規表現）+ Phase 2（AI）をマージ

    Parameters:
        segments: 請求項分節データ (segments.json)
        hongan: 本願構造化テキスト (hongan.json)
        field: "cosmetics" | "laminate"
        case_dir: 案件ディレクトリ（3段階データ活用用、optional）

    Returns:
        分節ごとのキーワードリスト (segment_keywords.json 形式)
    """
    # 優先: keyword_dictionary.json からの構築を試みる
    if case_dir:
        dict_results = recommend_from_dictionary(case_dir, segments)
        if dict_results:
            # 辞書ベースの結果に regex の追加候補を補完
            regex_results = recommend_regex(segments, hongan, field)
            regex_by_seg = {r["segment_id"]: r for r in regex_results}

            for item in dict_results:
                seg_id = item["segment_id"]
                if seg_id in regex_by_seg:
                    existing = {kw["term"] for kw in item["keywords"]}
                    for kw in regex_by_seg[seg_id]["keywords"]:
                        if kw["term"] not in existing:
                            item["keywords"].append(kw)
                            existing.add(kw["term"])

            logger.info("keyword_dictionary.json ベースで %d 分節のキーワードを構築", len(dict_results))
            return dict_results

    # フォールバック: Phase 1 + Phase 2
    # Phase 1: 正規表現
    regex_results = recommend_regex(segments, hongan, field)

    # Phase 2: AI
    ai_results = recommend_ai(segments, hongan, field, case_dir=case_dir)

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
