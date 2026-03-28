#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
分節別キーワード提案モジュール

キーワード収集4ステップパイプライン:
  Step 1: 全請求項・全分節から正規表現で語句ピックアップ (Python)
  Step 2: 明細書から関連語をAIで拾う (AI 1回目)
  Step 3: 辞書キー一覧からAIが照合→Pythonが辞書展開 (AI 2回目)
  Step 4: UIで人間が修正（本モジュール外）

入力:
- segments: 請求項分節 (segments.json)
- hongan: 本願構造化テキスト (hongan.json)
- field: "cosmetics" | "laminate" | etc.

出力:
- 分節ごとのキーワードリスト (segment_keywords.json 形式)
"""

import os
import re
import json
import logging
import functools
from pathlib import Path

from modules.fterm_dict import (
    expand_term, all_tree_keys, get_synonyms, get_inci, codes_for_term
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# ============================================================
# 辞書キャッシュ
# ============================================================

@functools.lru_cache(maxsize=32)
def _cached_load_dict(field, dict_name):
    """辞書ファイルを読み込み（キャッシュ付き）"""
    dict_path = PROJECT_ROOT / "dictionaries" / field / dict_name
    if dict_path.exists():
        try:
            with open(dict_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return json.dumps(data, ensure_ascii=False)
        except Exception:
            pass
    return "{}"


def _get_dict(field, dict_name):
    """キャッシュから辞書を取得（デシリアライズ済み）"""
    return json.loads(_cached_load_dict(field, dict_name))


def _load_dict(field, dict_name):
    """分野辞書を読み込み（キャッシュなし版、後方互換）"""
    dict_path = PROJECT_ROOT / "dictionaries" / field / dict_name
    if dict_path.exists():
        try:
            with open(dict_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


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
RE_PAREN_LABEL = re.compile(r'[\(（]([A-Za-zＡ-Ｚ])[\)）]\s*([^、。\)）]{2,20})')


# ============================================================
# Step 1: 正規表現による語句ピックアップ（共通部品）
# ============================================================

def _pick_terms_from_text(text):
    """テキストから語句を機械的にピックアップ

    全パターン（カタカナ・漢字・数値条件・括弧ラベル）を適用し、
    STOP_WORDS 除外・重複除去済みのリストを返す。

    Returns:
        list of dict: [{"term": str, "source": "claim", "type": str}, ...]
    """
    terms = []
    seen = set()

    def _add(term, kw_type):
        if term and term not in seen and term not in STOP_WORDS:
            seen.add(term)
            terms.append({"term": term, "source": "claim", "type": kw_type})

    # カタカナ語（3文字以上）
    for m in RE_KATAKANA.finditer(text):
        t = m.group()
        if len(t) >= 3:
            _add(t, "カタカナ抽出")

    # 漢字語（2文字以上）
    for m in RE_KANJI.finditer(text):
        t = m.group()
        if len(t) >= 2:
            _add(t, "漢字抽出")

    # 数値条件（範囲）
    for m in RE_NUMERIC.finditer(text):
        _add(m.group(), "数値条件")

    # 数値条件（単独、範囲と重複しないもの）
    for m in RE_NUMERIC_SINGLE.finditer(text):
        _add(m.group(), "数値条件")

    # 括弧ラベル
    for m in RE_PAREN_LABEL.finditer(text):
        label = m.group(1)
        content = m.group(2).strip()
        _add(f"({label}){content}", "括弧ラベル")

    return terms


# ============================================================
# Step 2: AI による明細書関連語探索
# ============================================================

def _build_spec_excerpt(hongan, max_chars=8000):
    """明細書テキストを段落番号付きで整形（Step 2 入力用）

    優先セクション順に段落を連結し、max_chars 以内に収める。
    """
    if not hongan:
        return ""

    priority_sections = ["手段", "実施形態", "実施例", "効果", "技術分野", "背景技術"]
    lines = []
    total = 0
    seen_ids = set()

    for section in priority_sections:
        for para in hongan.get("paragraphs", []):
            if para.get("section") == section and para["id"] not in seen_ids:
                line = f"【{para['id']}】{para['text']}"
                if total + len(line) > max_chars:
                    return "\n".join(lines)
                lines.append(line)
                total += len(line)
                seen_ids.add(para["id"])

    # 優先セクションで埋まらなかった場合、残り段落も追加
    for para in hongan.get("paragraphs", []):
        if para["id"] not in seen_ids:
            line = f"【{para['id']}】{para['text']}"
            if total + len(line) > max_chars:
                break
            lines.append(line)
            total += len(line)
            seen_ids.add(para["id"])

    return "\n".join(lines)


def _ai_find_related_in_spec(seg_keywords, spec_text, field="cosmetics"):
    """Step 2: 明細書から関連語を AI で拾う

    Parameters:
        seg_keywords: Step 1 の結果リスト
        spec_text: _build_spec_excerpt() の出力
        field: 技術分野

    Returns:
        list of dict or None（エラー/スキップ時）
        [{"seg":"1A", "term":"スクワラン",
          "related":["流動パラフィン","イソドデカン"], "para":"0025"}, ...]
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.info("ANTHROPIC_API_KEY 未設定 — Step 2 スキップ")
        return None

    if not spec_text.strip():
        logger.info("明細書テキストなし — Step 2 スキップ")
        return None

    # 分節別キーワードを簡潔に整形
    seg_lines = []
    for item in seg_keywords:
        terms = [kw["term"] for kw in item["keywords"] if kw["type"] != "数値条件"]
        if terms:
            seg_lines.append(f'{item["segment_id"]}: {", ".join(terms[:15])}')

    if not seg_lines:
        return None

    field_role = {
        "cosmetics": "化粧品技術者・化学の専門家",
        "laminate": "高分子材料・積層フィルム技術の専門家",
    }.get(field, "当該技術分野の専門家")

    prompt = f"""あなたは{field_role}であり、特許調査に精通しています。
以下の特許請求項のキーワードについて、明細書中で関連して言及されている語句を探してください。

【分節別キーワード】
{chr(10).join(seg_lines)}

【明細書テキスト】
{spec_text}

【出力形式】フラットなJSON配列のみ返してください（説明文不要）:
[{{"seg":"1A","term":"スクワラン","related":["流動パラフィン","イソドデカン"],"para":"0025"}}]

【ルール】
- 明細書に実際に書かれている語句のみ抽出すること。推測で語句を追加しない。
- related: そのキーワードと同じ段落または近接段落で関連して言及されている技術用語。
- para: 関連語が出現する段落番号（4桁文字列）。複数段落にまたがる場合は最初の段落。
- 関連語がない分節・キーワードは出力に含めない。
- 各キーワードにつき related は最大5語。"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        result = _extract_json_array(response.content[0].text)
        if result:
            logger.info("Step 2: AI から %d 件の関連語データを取得", len(result))
        else:
            logger.warning("Step 2: AI応答からJSONを抽出できませんでした")
        return result
    except ImportError:
        logger.warning("anthropic パッケージ未インストール — Step 2 スキップ")
        return None
    except Exception as e:
        logger.warning("Step 2 AI エラー: %s", e)
        return None


def _merge_step2_results(results, ai_spec):
    """Step 2 の AI 結果を results にマージ"""
    seg_map = {r["segment_id"]: r for r in results}
    added = 0
    for item in ai_spec:
        seg_id = item.get("seg", "")
        if seg_id not in seg_map:
            continue
        para = item.get("para", "")
        source = f"spec:【{para}】" if para else "spec"
        existing_terms = {kw["term"] for kw in seg_map[seg_id]["keywords"]}
        for rel in item.get("related", []):
            if isinstance(rel, str) and rel.strip() and rel not in existing_terms:
                seg_map[seg_id]["keywords"].append({
                    "term": rel.strip(),
                    "source": source,
                    "type": "明細書関連語",
                })
                existing_terms.add(rel)
                added += 1
    logger.info("Step 2 マージ: %d 語追加", added)


# ============================================================
# Step 3: AI による辞書キー照合 + Python 辞書展開
# ============================================================

def _ai_find_related_in_dicts(all_keywords, field):
    """Step 3: 収集済みキーワードと辞書キー一覧を AI に渡し、対応を返す

    Parameters:
        all_keywords: Step 1+2 済みの results リスト
        field: 技術分野

    Returns:
        list of dict or None
        [{"term":"スクワラン", "synonyms_key":"スクワラン",
          "upper_key":"スクワラン", "inci_key":"スクワラン"}, ...]
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.info("ANTHROPIC_API_KEY 未設定 — Step 3 スキップ")
        return None

    # 辞書キー一覧を取得
    tree_keys = all_tree_keys(field)
    syn_keys  = sorted(get_synonyms(field).keys())
    inci_keys = sorted(get_inci(field).keys()) if field == "cosmetics" else []

    if not tree_keys and not syn_keys:
        logger.info("辞書なし（field=%s） — Step 3 スキップ", field)
        return None

    # 収集済みキーワード一覧（重複除去、数値条件除外）
    term_set = set()
    for item in all_keywords:
        for kw in item["keywords"]:
            if kw["type"] != "数値条件":
                term_set.add(kw["term"])
    terms_list = sorted(term_set)

    if not terms_list:
        return None

    max_keys = 200
    combined_display = ", ".join((tree_keys + syn_keys + inci_keys)[:max_keys])

    field_role = {
        "cosmetics": "化粧品技術者・化学の専門家",
        "laminate": "高分子材料・積層フィルム技術の専門家",
    }.get(field, "当該技術分野の専門家")

    prompt = f"""あなたは{field_role}であり、特許調査に精通しています。
以下のキーワードそれぞれについて、辞書キー一覧から技術的に対応するキーを選んでください。

【キーワード一覧】
{", ".join(terms_list[:60])}

【辞書キー一覧】
{combined_display}

【出力形式】フラットなJSON配列のみ返してください（説明文不要）:
[{{"term":"スクワラン","matched_key":"スクワラン"}}]

【ルール】
- 辞書キー一覧に実在するキーのみ記入。該当なしは出力に含めない。
- 意味的に同一または直接の包含関係にあるもののみマッチ。遠い関連はマッチしない。"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        result = _extract_json_array(response.content[0].text)
        if result:
            logger.info("Step 3: AI から %d 件の辞書照合データを取得", len(result))
        else:
            logger.warning("Step 3: AI応答からJSONを抽出できませんでした")
        return result
    except ImportError:
        logger.warning("anthropic パッケージ未インストール — Step 3 スキップ")
        return None
    except Exception as e:
        logger.warning("Step 3 AI エラー: %s", e)
        return None


def _merge_step3_results(results, ai_dict, field):
    """Step 3: AI照合結果で木構造辞書を引き、results にマージ"""
    synonyms = get_synonyms(field)
    inci_ja  = get_inci(field)

    expansion = {}
    for item in ai_dict:
        term = item.get("term", "")
        matched_key = item.get("matched_key", "")
        if not term or not matched_key:
            continue
        extras = []

        # 木構造辞書で展開（兄弟語・上位概念・同分類例）
        tree_exp = expand_term(matched_key, field)
        for sib in tree_exp["siblings"]:
            extras.append({"term": sib, "source": "dict:tree/siblings", "type": "Fterm兄弟語"})
        for anc in tree_exp["ancestors"]:
            extras.append({"term": anc, "source": "dict:tree/ancestors", "type": "上位概念"})
        for ex in tree_exp["examples"]:
            extras.append({"term": ex, "source": "dict:tree/examples", "type": "同分類例"})

        # synonyms 展開
        if matched_key in synonyms:
            syns = synonyms[matched_key]
            for syn in (syns if isinstance(syns, list) else [syns]):
                extras.append({"term": syn, "source": "dict:synonyms", "type": "同義語"})

        # INCI 展開
        if matched_key in inci_ja:
            e = inci_ja[matched_key]
            name = e.get("inci_name", "") if isinstance(e, dict) else e
            if name:
                extras.append({"term": name, "source": "dict:inci", "type": "INCI英名"})

        if extras:
            expansion[term] = extras

    added = 0
    for r in results:
        existing = {kw["term"] for kw in r["keywords"]}
        to_add = []
        for kw in r["keywords"]:
            for ext in expansion.get(kw["term"], []):
                if ext["term"] not in existing:
                    to_add.append(ext)
                    existing.add(ext["term"])
        r["keywords"].extend(to_add)
        added += len(to_add)
    logger.info("Step 3 マージ: %d 語追加", added)


# ============================================================
# JSON 抽出ユーティリティ
# ============================================================

def _extract_json_array(raw_text):
    """テキストからJSON配列を抽出"""
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


# ============================================================
# メイン: キーワード収集パイプライン
# ============================================================

def recommend_regex(segments, hongan, field):
    """キーワード収集パイプライン（Step 1→2→3）

    Step 1: 全請求項・全分節から正規表現で語句ピックアップ
    Step 2: AI で明細書から関連語を拾う（1回）
    Step 3: AI で辞書キーを照合→Python が辞書展開（1回）

    ANTHROPIC_API_KEY 未設定時は Step 1 のみで返す。
    各 Step でエラーが発生してもスキップして次へ進む。

    Parameters:
        segments: 請求項分節データ (segments.json)
        hongan: 本願構造化テキスト (hongan.json)
        field: "cosmetics" | "laminate" | etc.

    Returns:
        list of dict:
        [{"segment_id": "1A", "segment_text": "...",
          "keywords": [{"term": str, "source": str, "type": str}, ...]}, ...]
    """
    # === Step 1: 正規表現抽出（全請求項・全分節） ===
    results = []
    for claim in segments:
        for seg in claim.get("segments", []):
            keywords = _pick_terms_from_text(seg["text"])
            results.append({
                "segment_id": seg["id"],
                "segment_text": seg["text"],
                "keywords": keywords,
            })

    logger.info("Step 1 完了: %d 分節、計 %d 語抽出",
                len(results),
                sum(len(r["keywords"]) for r in results))

    # === Step 2: AI 明細書探索 ===
    spec_text = _build_spec_excerpt(hongan, max_chars=8000)
    if spec_text:
        ai_spec = _ai_find_related_in_spec(results, spec_text, field)
        if ai_spec:
            _merge_step2_results(results, ai_spec)

    # === Step 3: AI 辞書照合 + Python 展開 ===
    ai_dict = _ai_find_related_in_dicts(results, field)
    if ai_dict:
        _merge_step3_results(results, ai_dict, field)

    # === 最終重複除去 ===
    for item in results:
        seen = set()
        unique = []
        for kw in item["keywords"]:
            if kw["term"] not in seen:
                seen.add(kw["term"])
                unique.append(kw)
        item["keywords"] = unique

    logger.info("パイプライン完了: %d 分節、計 %d 語",
                len(results),
                sum(len(r["keywords"]) for r in results))

    return results




# ============================================================
# keyword_dictionary.json からのキーワード構築（後方互換）
# ============================================================

def recommend_from_dictionary(case_dir, segments):
    """keyword_dictionary.json が存在すればそこからキーワードを構築"""
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
    all_seg_ids = []
    seg_texts = {}
    for claim in segments:
        is_indep = claim.get("is_independent", claim.get("claim_number") == 1)
        if not is_indep:
            continue
        for seg in claim.get("segments", []):
            all_seg_ids.append(seg["id"])
            seg_texts[seg["id"]] = seg["text"]
    results = []
    for seg_id in all_seg_ids:
        keywords = []
        seg_text = seg_texts.get(seg_id, "")
        matched_elements = element_seg_map.get(seg_id, [])
        if not matched_elements:
            for key, elem in elements.items():
                core_terms = [ct.get("term", "") for ct in elem.get("core_terms", [])]
                for t in core_terms[:5]:
                    if t and t in seg_text:
                        matched_elements.append(key)
                        break
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
