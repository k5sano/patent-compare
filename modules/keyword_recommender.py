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
            model="claude-sonnet-4-5-20250929",
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
    syn_keys = sorted(_get_dict(field, "synonyms.json").keys())
    uc_keys = sorted(_get_dict(field, "upper_concepts.json").keys())
    inci_keys = sorted(_get_dict(field, "inci_ja.json").keys()) if field == "cosmetics" else []

    if not syn_keys and not uc_keys and not inci_keys:
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

    # キー一覧を制限（プロンプトサイズ抑制）
    max_keys = 150
    syn_display = ", ".join(syn_keys[:max_keys])
    uc_display = ", ".join(uc_keys[:max_keys])
    inci_display = ", ".join(inci_keys[:max_keys])

    field_role = {
        "cosmetics": "化粧品技術者・化学の専門家",
        "laminate": "高分子材料・積層フィルム技術の専門家",
    }.get(field, "当該技術分野の専門家")

    prompt = f"""あなたは{field_role}であり、特許調査に精通しています。
以下のキーワードそれぞれについて、各辞書のキー一覧から技術的に対応するキーを選んでください。

【キーワード一覧】
{", ".join(terms_list[:60])}

【辞書キー一覧】
synonyms: {syn_display}
upper_concepts: {uc_display}
inci: {inci_display}

【出力形式】フラットなJSON配列のみ返してください（説明文不要）:
[{{"term":"スクワラン","synonyms_key":"スクワラン","upper_key":"スクワラン","inci_key":"スクワラン"}}]

【ルール】
- 各フィールドには辞書キー一覧に実在するキーのみ記入。該当なしは null。
- 意味的に同一または直接の包含関係にあるもののみマッチ。遠い関連はマッチしない。
- 辞書にマッチしないキーワードは出力に含めなくてよい。"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-5-20250929",
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
    """Step 3 の AI 結果で辞書を引き、results にマージ

    AI が返した辞書キーで Python が辞書の中身を展開する。
    """
    synonyms = _get_dict(field, "synonyms.json")
    uc = _get_dict(field, "upper_concepts.json")
    inci_ja = _get_dict(field, "inci_ja.json")

    # term → 辞書展開結果のマップを構築
    expansion = {}  # term -> [{"term":..., "source":..., "type":...}]
    for item in ai_dict:
        term = item.get("term", "")
        if not term:
            continue
        extras = []

        # synonyms 展開
        sk = item.get("synonyms_key")
        if sk and sk in synonyms:
            syns = synonyms[sk]
            if isinstance(syns, list):
                for syn in syns:
                    extras.append({"term": syn, "source": "dict:synonyms", "type": "同義語"})
            elif isinstance(syns, str):
                extras.append({"term": syns, "source": "dict:synonyms", "type": "同義語"})

        # upper_concepts 展開
        uk = item.get("upper_key")
        if uk and uk in uc:
            entry = uc[uk]
            for up in entry.get("upper_concepts", []):
                extras.append({"term": up, "source": "dict:upper_concepts", "type": "上位概念"})
            for sib in entry.get("same_fterm_siblings", [])[:5]:
                extras.append({"term": sib, "source": "dict:upper_concepts", "type": "Fterm兄弟語"})
            for bsib in entry.get("broader_fterm_siblings", [])[:3]:
                extras.append({"term": bsib, "source": "dict:upper_concepts", "type": "上位Fterm兄弟語"})
            for bn in entry.get("brand_names", []):
                extras.append({"term": bn, "source": "dict:upper_concepts", "type": "商品名"})

        # inci 展開
        ik = item.get("inci_key")
        if ik and ik in inci_ja:
            e = inci_ja[ik]
            if isinstance(e, dict):
                name = e.get("inci_name", "")
                if name:
                    extras.append({"term": name, "source": "dict:inci", "type": "INCI英名"})
                cas = e.get("cas_number", "")
                if cas:
                    extras.append({"term": cas, "source": "dict:inci", "type": "CAS番号"})
            elif isinstance(e, str) and e:
                extras.append({"term": e, "source": "dict:inci", "type": "INCI英名"})

        if extras:
            expansion[term] = extras

    # 各分節のキーワードに展開結果を追加
    added = 0
    for r in results:
        existing = {kw["term"] for kw in r["keywords"]}
        to_add = []
        for kw in r["keywords"]:
            if kw["term"] in expansion:
                for ext in expansion[kw["term"]]:
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
# 後方互換: 既存関数（recommend_semantic 等から呼ばれる）
# ============================================================

def _match_ingredient_group(term, field):
    """regex抽出語が ingredient_groups.json のどのカテゴリに該当するか判定"""
    ig = _get_dict(field, "ingredient_groups.json")
    if not ig:
        return []
    matches = []
    for cat_name, cat_data in ig.items():
        if term == cat_name or term in cat_name or cat_name in term:
            matches.append({"category": cat_name, "sub_group": None, "match_type": "category"})
            continue
        if "sub_groups" in cat_data:
            for sg_name, sg_items in cat_data["sub_groups"].items():
                if term == sg_name or term in sg_name or sg_name in term:
                    matches.append({"category": cat_name, "sub_group": sg_name, "match_type": "sub_group"})
                    break
                if term in sg_items:
                    matches.append({"category": cat_name, "sub_group": sg_name, "match_type": "example"})
                    break
        elif "examples" in cat_data:
            if term in cat_data["examples"]:
                matches.append({"category": cat_name, "sub_group": None, "match_type": "example"})
    return matches


def _match_fterm_labels(term, field):
    """regex抽出語が Fterm のラベルまたは例示に該当するか判定"""
    fterm_files = {
        "cosmetics": "fterm_4c083_structure.json",
        "laminate": "fterm_4f100_structure.json",
    }
    fterm_name = fterm_files.get(field, "")
    if not fterm_name:
        return []
    ft = _get_dict(field, fterm_name)
    matches = []
    for cat_code, cat_data in ft.get("categories", {}).items():
        for ecode, edata in cat_data.get("entries", {}).items():
            label = edata.get("label", "")
            if term and label and (term in label or label in term):
                matches.append({"fterm_code": ecode, "label": label, "match_type": "label"})
            elif term in edata.get("examples", []):
                matches.append({"fterm_code": ecode, "label": label, "match_type": "example"})
    return matches


def _match_upper_concepts_reverse(term, field):
    """regex抽出語が upper_concepts の上位概念リストに含まれるか判定"""
    uc = _get_dict(field, "upper_concepts.json")
    if not uc:
        return []
    lower_terms = []
    for ingredient, data in uc.items():
        if term in data.get("upper_concepts", []):
            lower_terms.append(ingredient)
    return lower_terms


def _extract_description_excerpt(hongan, max_chars=3000):
    """明細書から実施例・定義部分を抜粋（recommend_semantic 用、後方互換）"""
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


def _expand_upper_concepts(terms, field):
    """upper_concepts.json から下位概念・Fterm兄弟語を展開"""
    upper_concepts = _load_dict(field, "upper_concepts.json")
    if not upper_concepts:
        return []
    expanded = []
    for term in terms:
        entry = upper_concepts.get(term)
        if not entry:
            continue
        for sib in entry.get("same_fterm_siblings", []):
            expanded.append({"term": sib, "source": "dict", "type": "Fterm兄弟語"})
        for bsib in entry.get("broader_fterm_siblings", []):
            expanded.append({"term": bsib, "source": "dict", "type": "上位Fterm兄弟語"})
        for bn in entry.get("brand_names", []):
            expanded.append({"term": bn, "source": "dict", "type": "商品名"})
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
    fterm_codes = set()
    for term in terms:
        codes = i2f.get(term, [])
        fterm_codes.update(codes)
    if not fterm_codes:
        return []
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
    concrete_by_seg = {}
    for para in hongan.get("paragraphs", []):
        if para.get("section") not in ("実施例", "実施形態"):
            continue
        text = para.get("text", "")
        found = pattern.findall(text)
        for name in found:
            if len(name) < 2:
                continue
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


# ============================================================
# recommend_semantic（後方互換: 既存フロー維持）
# ============================================================

def _build_dict_catalog(field):
    """辞書カテゴリのカタログをAIプロンプト用にコンパクトに生成"""
    lines = []
    ig = _get_dict(field, "ingredient_groups.json")
    if ig:
        lines.append("【成分グループ辞書】")
        for cat_name, cat_data in ig.items():
            if "sub_groups" in cat_data:
                subs = list(cat_data["sub_groups"].keys())
                examples = []
                for sg_name, sg_items in cat_data["sub_groups"].items():
                    examples.extend(sg_items[:2])
                lines.append(
                    f"  {cat_name}: サブ=[{', '.join(subs)}] "
                    f"例=[{', '.join(examples[:8])}]"
                )
            elif "examples" in cat_data:
                exs = cat_data["examples"][:5]
                lines.append(f"  {cat_name}: 例=[{', '.join(exs)}]")
    fterm_files = {
        "cosmetics": "fterm_4c083_structure.json",
        "laminate": "fterm_4f100_structure.json",
    }
    fterm_name = fterm_files.get(field, "")
    if fterm_name:
        ft = _get_dict(field, fterm_name)
        if ft:
            lines.append("\n【Fterm分類】")
            for cat_code, cat_data in ft.get("categories", {}).items():
                label = cat_data.get("label", "")
                entries = cat_data.get("entries", {})
                entry_samples = []
                for ecode, edata in list(entries.items())[:4]:
                    entry_samples.append(f"{ecode}:{edata.get('label', '')}")
                lines.append(f"  {cat_code}({label}): {', '.join(entry_samples)}")
    uc = _get_dict(field, "upper_concepts.json")
    if uc:
        lines.append("\n【上位概念辞書】")
        for ingredient, data in uc.items():
            uppers = data.get("upper_concepts", [])
            lines.append(f"  {ingredient} → {', '.join(uppers[:4])}")
    return "\n".join(lines)


def _call_ai_for_semantic_analysis(segments, hongan, field):
    """AIに分節の技術的意味理解＋辞書カテゴリマッピングを依頼"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.info("ANTHROPIC_API_KEY 未設定 — semantic分析スキップ")
        return None
    seg_lines = []
    seg_ids = []
    for claim in segments:
        is_indep = claim.get("is_independent", claim.get("claim_number") == 1)
        if not is_indep:
            continue
        for seg in claim.get("segments", []):
            seg_lines.append(f'{seg["id"]}: {seg["text"]}')
            seg_ids.append(seg["id"])
    if not seg_lines:
        return None
    field_label = {"cosmetics": "化粧品", "laminate": "積層体"}.get(field, field)
    dict_catalog = _build_dict_catalog(field)
    description_excerpt = _extract_description_excerpt(hongan, max_chars=2000)
    prompt = f"""あなたは{field_label}分野の特許調査の専門家です。
以下の請求項分節それぞれについて、技術的意味を理解し、当方の辞書カテゴリへのマッピングを行ってください。

【分節リスト】
{chr(10).join(seg_lines)}

【辞書カタログ】
{dict_catalog}

【本願明細書（参考）】
{description_excerpt}

【出力形式】JSON配列で返してください:
[
  {{
    "segment_id": "1A",
    "technical_meaning": "この分節は油性基剤に関する記述で…",
    "ingredient_group_matches": ["油剤", "油剤/炭化水素油"],
    "fterm_matches": ["AC01", "AC02"],
    "upper_concept_matches": ["流動パラフィン", "スクワラン"],
    "additional_terms": {{
      "core": ["特許公報で頻出する検索コア語"],
      "extended": ["漏れ防止の拡張語"],
      "not": ["ノイズ除外語"]
    }}
  }}
]

【重要な指示】
- ingredient_group_matches: 辞書カタログの「成分グループ辞書」を参照し、分節内容に該当するカテゴリ名を記入。サブグループまで特定できる場合は「カテゴリ/サブグループ」形式（例: 油剤/シリコーン油）。
- fterm_matches: 辞書カタログの「Fterm分類」を参照し、該当するFtermコード（AA01等）を記入。
- upper_concept_matches: 辞書カタログの「上位概念辞書」を参照し、分節に関連する具体成分名を記入。
- additional_terms: 辞書カタログに載っていないが検索に有用な語。core=高適合語、extended=漏れ防止語、not=ノイズ除外語。
- 辞書カタログに完全一致しなくても、意味的に最も近いカテゴリを選んでください。
- 技術的内容が薄い分節（「化粧料」のみ等）は各フィールドを空配列にしてください。
- 1つの分節が複数カテゴリに該当する場合は全て列挙してください。
"""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = response.content[0].text
        ai_data = _extract_json_array(raw_text)
        if not ai_data:
            logger.warning("semantic分析: JSONを抽出できませんでした")
            return None
        result = {}
        for item in ai_data:
            sid = item.get("segment_id", "")
            if sid in seg_ids:
                result[sid] = item
        logger.info("semantic分析: %d/%d 分節の結果を取得", len(result), len(seg_ids))
        return result
    except ImportError:
        logger.warning("anthropic パッケージ未インストール — semantic分析スキップ")
        return None
    except Exception as e:
        logger.warning("semantic分析エラー: %s", e)
        return None


def _expand_from_ai_mappings(ai_mapping, field):
    """AIのカテゴリマッピングから辞書を使った確定的キーワード展開"""
    keywords = []
    seen = set()

    def _add(term, source, kw_type, tier="core"):
        if term and term not in seen:
            seen.add(term)
            keywords.append({"term": term, "source": source, "type": kw_type, "tier": tier})

    additional = ai_mapping.get("additional_terms", {})
    if isinstance(additional, dict):
        for term in additional.get("core", []):
            _add(term, "ai_semantic", "AI意味理解(コア語)", "core")
        for term in additional.get("extended", []):
            _add(term, "ai_semantic", "AI意味理解(拡張語)", "extended")
        for term in additional.get("not", []):
            _add(term, "ai_semantic", "AI意味理解(NOT語)", "not")

    ig = _get_dict(field, "ingredient_groups.json")
    for match_name in ai_mapping.get("ingredient_group_matches", []):
        parts = match_name.split("/")
        cat_name = parts[0]
        sub_name = parts[1] if len(parts) > 1 else None
        cat_data = ig.get(cat_name)
        if not cat_data:
            for key in ig:
                if cat_name in key or key in cat_name:
                    cat_data = ig[key]
                    cat_name = key
                    break
        if not cat_data:
            continue
        _add(cat_name, "dict_ig", "成分グループ", "core")
        if "sub_groups" in cat_data:
            if sub_name:
                sg_items = cat_data["sub_groups"].get(sub_name)
                if not sg_items:
                    for sg_key in cat_data["sub_groups"]:
                        if sub_name in sg_key or sg_key in sub_name:
                            sg_items = cat_data["sub_groups"][sg_key]
                            sub_name = sg_key
                            break
                if sg_items:
                    _add(sub_name, "dict_ig", "サブグループ", "core")
                    for item in sg_items[:8]:
                        _add(item, "dict_ig", f"{cat_name}/{sub_name}", "extended")
            else:
                for sg_key, sg_items in cat_data["sub_groups"].items():
                    _add(sg_key, "dict_ig", f"{cat_name}サブ", "core")
                    for item in sg_items[:3]:
                        _add(item, "dict_ig", f"{cat_name}/{sg_key}", "extended")
        elif "examples" in cat_data:
            for item in cat_data["examples"][:8]:
                _add(item, "dict_ig", f"{cat_name}例", "extended")

    fterm_files = {
        "cosmetics": "fterm_4c083_structure.json",
        "laminate": "fterm_4f100_structure.json",
    }
    fterm_name = fterm_files.get(field, "")
    if fterm_name:
        ft = _get_dict(field, fterm_name)
        categories = ft.get("categories", {})
        for fcode in ai_mapping.get("fterm_matches", []):
            cat_code = fcode[:2] if len(fcode) >= 2 else ""
            cat_data = categories.get(cat_code, {})
            entry = cat_data.get("entries", {}).get(fcode, {})
            if entry:
                label = entry.get("label", "")
                if label:
                    _add(label, "dict_fterm", f"Fterm({fcode})", "core")
                for ex in entry.get("examples", [])[:5]:
                    _add(ex, "dict_fterm", f"Fterm({fcode})例", "extended")

    uc_dict = _get_dict(field, "upper_concepts.json")
    for uc_name in ai_mapping.get("upper_concept_matches", []):
        entry = uc_dict.get(uc_name)
        if not entry:
            for key in uc_dict:
                if uc_name in key or key in uc_name:
                    entry = uc_dict[key]
                    uc_name = key
                    break
        if not entry:
            continue
        _add(uc_name, "dict_uc", "上位概念キー", "core")
        for up in entry.get("upper_concepts", []):
            _add(up, "dict_uc", "上位概念", "core")
        for sib in entry.get("same_fterm_siblings", []):
            _add(sib, "dict_uc", "Fterm兄弟語", "extended")
        for bsib in entry.get("broader_fterm_siblings", [])[:5]:
            _add(bsib, "dict_uc", "上位Fterm兄弟語", "extended")
        for bn in entry.get("brand_names", []):
            _add(bn, "dict_uc", "商品名", "extended")

    i2f = _get_dict(field, "ingredient_to_fterm.json")
    if i2f:
        target_codes = set()
        target_codes.update(ai_mapping.get("fterm_matches", []))
        for uc_name in ai_mapping.get("upper_concept_matches", []):
            target_codes.update(i2f.get(uc_name, []))
        if target_codes:
            count = 0
            for ingredient, codes in i2f.items():
                if ingredient in seen:
                    continue
                if any(c in target_codes for c in codes):
                    _add(ingredient, "dict_i2f", "Fterm逆引き", "extended")
                    count += 1
                    if count >= 8:
                        break

    synonyms = _get_dict(field, "synonyms.json")
    if synonyms:
        core_terms = [kw["term"] for kw in keywords if kw.get("tier") == "core"]
        for term in core_terms:
            for syn in synonyms.get(term, []):
                _add(syn, "dict_syn", "同義語", "extended")

    inci_ja = _get_dict(field, "inci_ja.json")
    if inci_ja:
        core_terms = [kw["term"] for kw in keywords if kw.get("tier") == "core"]
        for term in core_terms:
            if term in inci_ja:
                entry = inci_ja[term]
                inci_name = entry.get("inci_name", entry) if isinstance(entry, dict) else entry
                if inci_name:
                    _add(inci_name, "dict_inci", "INCI英名", "extended")

    return keywords


def recommend_semantic(segments, hongan, field):
    """AI意味理解 + 辞書確定展開によるキーワード提案（後方互換）"""
    ai_result = _call_ai_for_semantic_analysis(segments, hongan, field)
    if not ai_result:
        return []
    results = []
    for claim in segments:
        is_indep = claim.get("is_independent", claim.get("claim_number") == 1)
        if not is_indep:
            continue
        for seg in claim.get("segments", []):
            seg_id = seg["id"]
            ai_mapping = ai_result.get(seg_id)
            if not ai_mapping:
                results.append({
                    "segment_id": seg_id,
                    "segment_text": seg["text"],
                    "keywords": [],
                    "technical_meaning": "",
                })
                continue
            keywords = _expand_from_ai_mappings(ai_mapping, field)
            results.append({
                "segment_id": seg_id,
                "segment_text": seg["text"],
                "keywords": keywords,
                "technical_meaning": ai_mapping.get("technical_meaning", ""),
            })
    return results
