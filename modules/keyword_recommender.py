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
    expand_term, all_tree_keys, get_synonyms, get_inci, codes_for_term,
    get_nodes, build_digest,
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

# CJK文字範囲（前処理用）
_CJK_CHAR = re.compile(r'[\u3040-\u30FF\u4E00-\u9FFF\uFF66-\uFF9F]')


def _preprocess_text(text: str) -> str:
    """PDF改行由来のスペースでCJK文字が分断される問題を修正。

    例:
      "ポリアルキ レングリコールエーテル" → "ポリアルキレングリコールエーテル"
      "油状泡沫 性エアゾール用 化粧料" → "油状泡沫性エアゾール用化粧料"
    """
    if not text:
        return text
    # CJK文字の間にある半角スペースを除去
    result = re.sub(
        r'(?<=[\u3040-\u30FF\u4E00-\u9FFF\uFF66-\uFF9F])\s+(?=[\u3040-\u30FF\u4E00-\u9FFF\uFF66-\uFF9F])',
        '', text
    )
    return result


# ============================================================
# Step 1: 正規表現による語句ピックアップ（共通部品）
# ============================================================

def _strip_prefix(term: str) -> str:
    """「前記」「該」「上記」等の接頭辞を除去"""
    for prefix in ("前記", "上記", "該", "当該", "前記した", "本"):
        if term.startswith(prefix) and len(term) > len(prefix):
            return term[len(prefix):]
    return term


def _pick_terms_from_text(text):
    """テキストから語句を機械的にピックアップ

    全パターン（カタカナ・漢字・数値条件・括弧ラベル）を適用し、
    STOP_WORDS 除外・重複除去済みのリストを返す。
    テキストは前処理済み（CJKスペース除去済み）を想定。

    Returns:
        list of dict: [{"term": str, "source": "claim", "type": str}, ...]
    """
    terms = []
    seen = set()

    def _add(term, kw_type):
        # 接頭辞除去
        term = _strip_prefix(term)
        if term and term not in seen and term not in STOP_WORDS and len(term) >= 2:
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
# Claude CLI フォールバック: Steps 2+3 一括実行
# ============================================================

def _cli_enrich_keywords(results, spec_text, field="cosmetics"):
    """Claude CLI (call_claude) で Steps 2+3 を一括実行。

    API key 不要。プロンプトに以下3ブロックを含める:
    1. 分節+Step1キーワード
    2. 明細書抜粋
    3. Fterm辞書ダイジェスト

    結果を results にマージする。
    """
    from modules.claude_client import call_claude, is_claude_available, ClaudeClientError

    if not is_claude_available():
        logger.warning("Claude CLI 利用不可 — CLI enrichment スキップ")
        return

    # 1. 分節+Step1キーワード 一覧
    seg_lines = []
    for item in results:
        terms = [kw["term"] for kw in item["keywords"] if kw["type"] != "数値条件"]
        term_str = ", ".join(terms[:12]) if terms else "(キーワードなし)"
        seg_lines.append(f'{item["segment_id"]}: {item["segment_text"][:60]}')
        seg_lines.append(f'  → Step1: {term_str}')
    seg_block = "\n".join(seg_lines)

    # 2. 明細書抜粋 (max 12KB)
    spec_block = spec_text[:12000] if spec_text else "(明細書テキストなし)"

    # 3. Fterm辞書ダイジェスト
    dict_block = build_digest(field)
    if not dict_block:
        dict_block = "(Fterm辞書なし)"
    else:
        dict_block = dict_block[:6000]  # 最大6KB

    # 全分節IDリスト
    all_seg_ids = [item["segment_id"] for item in results]
    seg_id_list = ", ".join(all_seg_ids)

    field_role = {
        "cosmetics": "化粧品技術者・化学の専門家",
        "laminate": "高分子材料・積層フィルム技術の専門家",
    }.get(field, "当該技術分野の専門家")

    prompt = f"""あなたは{field_role}であり、特許調査に精通しています。
以下の特許請求項の分節ごとに、明細書から関連する具体名を拾い、Fterm辞書から対応コードと関連語を選んでください。

【分節とStep1キーワード】
{seg_block}

【明細書テキスト（段落番号付き）】
{spec_block}

【Fterm辞書（コード: ラベル (例: ...)）】
{dict_block}

【出力形式】フラットなJSON配列のみ返してください（説明文不要）:
[{{"seg":"1A", "label":"グループの短いラベル",
  "spec_terms":[{{"term":"具体名","para":"0019"}}],
  "fterm_codes":["AC18"],
  "fterm_examples":["ポリオキシエチレンステアリルエーテル"]}}]

【ルール】
- 全分節について必ず出力すること（省略禁止）。対象分節: {seg_id_list}
- label: その分節の技術的テーマを表す短い名前（3〜15文字）
- spec_terms: 明細書中でその分節のキーワードに関連して言及されている具体的な物質名・技術用語。paraは段落番号。明細書に実在するもののみ。最大8語。
- fterm_codes: 辞書ダイジェスト中のコード（例: AC18, AD04）で、その分節のテーマに対応するもの。実在するコードのみ。最大5コード。
- fterm_examples: 辞書の例示語の中から、その分節に関連するもの。辞書に実在するもののみ。最大5語。
- 関連語がない分節でも seg と label は必ず出力すること。"""

    try:
        logger.info("CLI enrichment 開始: %d 分節, prompt=%d 文字",
                     len(results), len(prompt))
        raw = call_claude(prompt, timeout=300)
        enrichment = _extract_json_array(raw)
        if enrichment:
            logger.info("CLI enrichment: %d 件取得", len(enrichment))
            _merge_cli_enrichment(results, enrichment, field)
        else:
            logger.warning("CLI enrichment: JSONを抽出できませんでした")
    except ClaudeClientError as e:
        logger.warning("CLI enrichment エラー: %s", e)
    except Exception as e:
        logger.warning("CLI enrichment 予期しないエラー: %s", e)


def _merge_cli_enrichment(results, enrichment, field):
    """CLI enrichment の結果を results にマージ。

    - spec_terms → type "明細書具体名" で追加
    - fterm_codes → get_nodes()[code]["examples"] で展開、type "Fterm関連語"
    - fterm_examples → ツリーに実在するか検証後追加
    - _ai_label を results アイテムに保存
    - 同義語/INCI展開も実行
    """
    nodes = get_nodes(field)
    synonyms = get_synonyms(field)
    inci_ja = get_inci(field)

    # reverse_index で実在するexampleかを高速検証するためのセット
    from modules.fterm_dict import get_reverse_index
    rev_index = get_reverse_index(field)
    all_examples = set(rev_index.keys())
    for node in nodes.values():
        for ex in node.get("examples", []):
            all_examples.add(ex)

    seg_map = {r["segment_id"]: r for r in results}
    added_total = 0

    for item in enrichment:
        seg_id = item.get("seg", "")
        if seg_id not in seg_map:
            continue
        target = seg_map[seg_id]
        existing = {kw["term"] for kw in target["keywords"]}

        # AI提供ラベルを保存
        ai_label = item.get("label", "")
        if ai_label:
            target["_ai_label"] = ai_label

        added = 0

        # spec_terms: 明細書具体名
        for st in item.get("spec_terms", []):
            term = st.get("term", "").strip() if isinstance(st, dict) else ""
            para = st.get("para", "") if isinstance(st, dict) else ""
            if term and term not in existing and term not in STOP_WORDS:
                source = f"spec:【{para}】" if para else "spec"
                target["keywords"].append({
                    "term": term, "source": source, "type": "明細書具体名",
                })
                existing.add(term)
                added += 1

        # fterm_codes: コードからexamplesを展開
        for code in item.get("fterm_codes", []):
            node = nodes.get(code)
            if not node:
                continue
            for ex in node.get("examples", [])[:8]:
                if ex not in existing and ex not in STOP_WORDS:
                    target["keywords"].append({
                        "term": ex, "source": f"dict:fterm/{code}",
                        "type": "Fterm関連語",
                    })
                    existing.add(ex)
                    added += 1

        # fterm_examples: 実在検証後に追加
        for ex in item.get("fterm_examples", []):
            if isinstance(ex, str) and ex.strip():
                ex = ex.strip()
                if ex in all_examples and ex not in existing and ex not in STOP_WORDS:
                    target["keywords"].append({
                        "term": ex, "source": "dict:fterm/ai",
                        "type": "Fterm関連語",
                    })
                    existing.add(ex)
                    added += 1

        # 同義語/INCI展開（Step1+明細書キーワードに対して）
        for kw in list(target["keywords"]):
            term = kw["term"]
            # 同義語
            syns = synonyms.get(term, [])
            if isinstance(syns, str):
                syns = [syns]
            for syn in syns:
                if syn not in existing and syn not in STOP_WORDS:
                    target["keywords"].append({
                        "term": syn, "source": "dict:synonyms",
                        "type": "同義語",
                    })
                    existing.add(syn)
                    added += 1
            # INCI
            inci_entry = inci_ja.get(term, {})
            if isinstance(inci_entry, dict):
                name = inci_entry.get("inci_name", "")
            elif isinstance(inci_entry, str):
                name = inci_entry
            else:
                name = ""
            if name and name not in existing:
                target["keywords"].append({
                    "term": name, "source": "dict:inci",
                    "type": "INCI英名",
                })
                existing.add(name)
                added += 1

        added_total += added

    logger.info("CLI enrichment マージ: 計 %d 語追加", added_total)


# ============================================================
# メイン: キーワード収集パイプライン
# ============================================================

def recommend_regex(segments, hongan, field):
    """キーワード収集パイプライン（Step 1→2→3）

    Step 1: 全請求項・全分節から正規表現で語句ピックアップ
    Step 2: AI で明細書から関連語を拾う（1回）
    Step 3: AI で辞書キーを照合→Python が辞書展開（1回）

    ANTHROPIC_API_KEY 未設定時は Claude CLI フォールバックで Steps 2+3 を実行。
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
            preprocessed = _preprocess_text(seg["text"])
            keywords = _pick_terms_from_text(preprocessed)
            results.append({
                "segment_id": seg["id"],
                "segment_text": seg["text"],
                "keywords": keywords,
            })

    logger.info("Step 1 完了: %d 分節、計 %d 語抽出",
                len(results),
                sum(len(r["keywords"]) for r in results))

    # === 明細書テキスト準備 ===
    spec_text = _build_spec_excerpt(hongan, max_chars=12000)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        # === 既存 API パス (Steps 2+3) ===
        if spec_text:
            ai_spec = _ai_find_related_in_spec(results, spec_text, field)
            if ai_spec:
                _merge_step2_results(results, ai_spec)
        ai_dict = _ai_find_related_in_dicts(results, field)
        if ai_dict:
            _merge_step3_results(results, ai_dict, field)
    else:
        # === Claude CLI フォールバック (Steps 2+3 一括) ===
        logger.info("ANTHROPIC_API_KEY 未設定 — Claude CLI フォールバックで enrichment 実行")
        _cli_enrich_keywords(results, spec_text, field)

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
# 新パイプライン: AI技術構造化ベースのキーワード選定
# ============================================================

def _build_definition_excerpt(hongan, max_chars=8000):
    """明細書の定義・説明セクションを優先抽出。

    section が "手段", "実施形態" のパラグラフを優先。
    次に "効果", "技術分野", "背景技術"。
    """
    if not hongan:
        return ""
    priority = ["手段", "実施形態", "効果", "技術分野", "背景技術"]
    lines = []
    total = 0
    seen_ids = set()
    for section in priority:
        for para in hongan.get("paragraphs", []):
            if para.get("section") == section and para["id"] not in seen_ids:
                text = _preprocess_text(para.get("text", ""))
                line = f"【{para['id']}】{text}"
                if total + len(line) > max_chars:
                    return "\n".join(lines)
                lines.append(line)
                total += len(line)
                seen_ids.add(para["id"])
    return "\n".join(lines)


def _build_example_excerpt(hongan, max_chars=4000):
    """実施例・比較例セクションを抽出。"""
    if not hongan:
        return ""
    lines = []
    total = 0
    for para in hongan.get("paragraphs", []):
        if para.get("section") in ("実施例", "比較例"):
            text = _preprocess_text(para.get("text", ""))
            line = f"【{para['id']}】{text}"
            if total + len(line) > max_chars:
                break
            lines.append(line)
            total += len(line)
    return "\n".join(lines)


def _build_segments_text(segments):
    """請求項分節テキストをAIプロンプト用に整形（独立項のみ）"""
    lines = []
    for claim in segments:
        if not claim.get("is_independent", False):
            continue
        for seg in claim.get("segments", []):
            text = _preprocess_text(seg.get("text", ""))
            lines.append(f"[{seg['id']}] {text}")
    return "\n".join(lines)


def _build_tech_analysis_prompt(segments, hongan, field):
    """tech_analysis 用のAIプロンプトを生成。

    Returns:
        str: プロンプト文字列
    """
    seg_text = _build_segments_text(segments)
    def_text = _build_definition_excerpt(hongan, max_chars=8000)
    ex_text = _build_example_excerpt(hongan, max_chars=4000)

    # 全分節IDリスト
    all_seg_ids = []
    for claim in segments:
        for seg in claim.get("segments", []):
            all_seg_ids.append(seg["id"])
    seg_id_list = ", ".join(all_seg_ids)

    field_role = {
        "cosmetics": "化粧品化学・特許調査の専門家",
        "laminate": "高分子材料・積層フィルム技術の専門家",
    }.get(field, "当該技術分野の専門家")

    prompt = f"""あなたは{field_role}です。
以下の特許請求項と明細書から、技術構造化とキーワード抽出を行ってください。

## 請求項分節
{seg_text}

## 明細書（定義・説明セクション）
{def_text if def_text else "(なし)"}

## 明細書（実施例セクション）
{ex_text if ex_text else "(なし)"}

## 指示

請求項の構成要件を**技術概念単位**でグルーピングし、各要素について以下3つのソースからキーワードを網羅的に抽出してください。

- **claim_terms**: 請求項テキストに現れる成分名・効果・作用（原文そのまま、分割禁止）
- **definition_terms**: 明細書の定義セクションに記載された上位概念・具体名・商品名（段落番号付き）
- **example_terms**: 実施例の配合表等に記載された具体的な化合物名（段落番号付き）
- **synonyms**: 同義語（ja: 和名・化学名、en: INCI名・英語学術用語）

## 出力形式（JSONのみ、説明文不要）
```json
{{
  "core_sentence": "技術の一文要約",
  "elements": {{
    "A_xxx": {{
      "label": "要素ラベル（3〜15文字）",
      "segment_ids": ["1A", "1B"],
      "claim_terms": ["アミノ酸系カチオン界面活性剤"],
      "definition_terms": [
        {{"term": "ステアリン酸ジメチルアミノプロピルアミド", "para": "0025", "type": "具体名"}},
        {{"term": "カチナールMTB-40", "para": "0026", "type": "商品名"}}
      ],
      "example_terms": [
        {{"term": "ステアリン酸ジメチルアミノプロピルアミド", "para": "0045", "examples": ["実施例1"]}}
      ],
      "synonyms": {{
        "ja": ["第三級アミン型カチオン界面活性剤"],
        "en": ["stearamidopropyl dimethylamine"]
      }}
    }}
  }}
}}
```

## ルール（厳守）
1. definition_terms は明細書に**実際に書かれている語句のみ**。推測で追加しない。
2. example_terms は実施例の配合表に**実際に記載されている化合物名のみ**。
3. claim_terms は請求項テキストに現れる語句を**そのまま**使う。「アミノ酸系カチオン界面活性剤」を「アミノ」「カチオン」に分割しないこと。
4. **全分節**（{seg_id_list}）を必ずいずれかの要素の segment_ids に含めること。漏れ禁止。
5. 要素数に上限なし。技術的に意味のある単位で分ける。
6. label は技術内容を端的に表す日本語（3〜15文字）。"""

    return prompt


def _extract_json_object(raw_text):
    """テキストからJSON objectを抽出"""
    # パターン1: ```json ... ``` ブロック
    json_block_pattern = re.compile(r'```(?:json)?\s*\n?(.*?)\n?\s*```', re.DOTALL)
    matches = json_block_pattern.findall(raw_text)
    for match in matches:
        try:
            data = json.loads(match)
            if isinstance(data, dict) and "elements" in data:
                return data
        except json.JSONDecodeError:
            continue

    # パターン2: 最外側の { ... } を探す
    brace_depth = 0
    start = None
    for i, ch in enumerate(raw_text):
        if ch == '{':
            if brace_depth == 0:
                start = i
            brace_depth += 1
        elif ch == '}':
            brace_depth -= 1
            if brace_depth == 0 and start is not None:
                candidate = raw_text[start:i + 1]
                try:
                    data = json.loads(candidate)
                    if isinstance(data, dict) and "elements" in data:
                        return data
                except json.JSONDecodeError:
                    start = None
                    continue

    # パターン3: 途中切れJSON修復
    from modules.response_parser import _try_repair_json
    first_brace = raw_text.find('{')
    if first_brace >= 0:
        repaired = _try_repair_json(raw_text[first_brace:])
        if isinstance(repaired, dict) and "elements" in repaired:
            return repaired

    return None


def _call_ai_tech_analysis(prompt, field):
    """AIを呼び出してtech_analysisを取得。

    API key → anthropic SDK (claude-sonnet-4)
    API keyなし → Claude CLI (call_claude)
    どちらも失敗 → None

    Returns:
        dict or None: tech_analysis JSON
    """
    # 方法1: Anthropic API (API key あり)
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text
            result = _extract_json_object(raw)
            if result:
                logger.info("tech_analysis: Anthropic API で取得 (%d 要素)",
                            len(result.get("elements", {})))
                return result
            logger.warning("tech_analysis: API応答からJSONを抽出できませんでした")
        except ImportError:
            logger.info("anthropic パッケージ未インストール — CLI フォールバック")
        except Exception as e:
            logger.warning("tech_analysis API エラー: %s — CLI フォールバック", e)

    # 方法2: Claude CLI (API keyなし or API失敗)
    try:
        from modules.claude_client import call_claude, is_claude_available, ClaudeClientError
        if not is_claude_available():
            logger.warning("Claude CLI 利用不可")
            return None
        logger.info("tech_analysis: Claude CLI で実行 (prompt=%d 文字)", len(prompt))
        raw = call_claude(prompt, timeout=300)
        result = _extract_json_object(raw)
        if result:
            logger.info("tech_analysis: CLI で取得 (%d 要素)",
                        len(result.get("elements", {})))
            return result
        logger.warning("tech_analysis: CLI応答からJSONを抽出できませんでした")
    except Exception as e:
        logger.warning("tech_analysis CLI エラー: %s", e)

    return None


def _tech_analysis_to_pipeline_result(tech_analysis, segments):
    """tech_analysis を pipeline_result 形式に変換。

    1つの element が複数の segment_ids を持つ場合、
    各 segment_id に対して同じキーワードセットを持つ item を作る。

    Returns:
        list of dict: [{"segment_id": "1A", "segment_text": "...",
                        "keywords": [...], "_ai_label": "..."}, ...]
    """
    # 全分節テキストマップ
    seg_texts = {}
    for claim in segments:
        for seg in claim.get("segments", []):
            seg_texts[seg["id"]] = seg.get("text", "")

    elements = tech_analysis.get("elements", {})

    # segment_id → (element_key, element_data) マッピング
    seg_to_elements = {}
    for key, elem in elements.items():
        for sid in elem.get("segment_ids", []):
            if sid not in seg_to_elements:
                seg_to_elements[sid] = []
            seg_to_elements[sid].append((key, elem))

    results = []
    all_seg_ids = list(seg_texts.keys())

    for seg_id in all_seg_ids:
        keywords = []
        seen = set()
        ai_label = ""

        mapped_elements = seg_to_elements.get(seg_id, [])

        for elem_key, elem in mapped_elements:
            if not ai_label:
                ai_label = elem.get("label", "")

            # claim_terms
            for t in elem.get("claim_terms", []):
                if isinstance(t, str) and t.strip() and t not in seen:
                    seen.add(t)
                    keywords.append({
                        "term": t, "source": "claim", "type": "請求項語句"
                    })

            # definition_terms
            for d in elem.get("definition_terms", []):
                if isinstance(d, dict):
                    term = d.get("term", "").strip()
                    para = d.get("para", "")
                    dtype = d.get("type", "具体名")
                else:
                    term, para, dtype = str(d), "", "具体名"
                if term and term not in seen:
                    seen.add(term)
                    source = f"定義【{para}】" if para else "定義"
                    keywords.append({
                        "term": term, "source": source, "type": dtype
                    })

            # example_terms
            for e in elem.get("example_terms", []):
                if isinstance(e, dict):
                    term = e.get("term", "").strip()
                    para = e.get("para", "")
                else:
                    term, para = str(e), ""
                if term and term not in seen:
                    seen.add(term)
                    source = f"実施例【{para}】" if para else "実施例"
                    keywords.append({
                        "term": term, "source": source, "type": "実施例化合物"
                    })

            # synonyms.ja
            for t in elem.get("synonyms", {}).get("ja", []):
                if isinstance(t, str) and t.strip() and t not in seen:
                    seen.add(t)
                    keywords.append({
                        "term": t, "source": "AI同義語(JA)", "type": "同義語"
                    })

            # synonyms.en
            for t in elem.get("synonyms", {}).get("en", []):
                if isinstance(t, str) and t.strip() and t not in seen:
                    seen.add(t)
                    keywords.append({
                        "term": t, "source": "AI同義語(EN)", "type": "英名"
                    })

        item = {
            "segment_id": seg_id,
            "segment_text": seg_texts.get(seg_id, ""),
            "keywords": keywords,
        }
        if ai_label:
            item["_ai_label"] = ai_label

        results.append(item)

    return results


def _dict_expand_pipeline_result(results, field):
    """pipeline_result の各キーワードに対して辞書展開を実行。

    - codes_for_term() で Fterm コード紐付け
    - synonyms.json で同義語展開
    - inci_ja.json で INCI 名展開
    """
    synonyms = get_synonyms(field)
    inci_ja = get_inci(field)
    added_total = 0

    for item in results:
        existing = {kw["term"] for kw in item["keywords"]}

        # 現在のキーワードリストのコピーに対して展開
        for kw in list(item["keywords"]):
            term = kw["term"]

            # 同義語展開
            syns = synonyms.get(term, [])
            if isinstance(syns, str):
                syns = [syns]
            for syn in syns:
                if syn and syn not in existing and syn not in STOP_WORDS:
                    item["keywords"].append({
                        "term": syn, "source": "dict:synonyms", "type": "同義語"
                    })
                    existing.add(syn)
                    added_total += 1

            # INCI展開
            inci_entry = inci_ja.get(term, {})
            if isinstance(inci_entry, dict):
                name = inci_entry.get("inci_name", "")
            elif isinstance(inci_entry, str):
                name = inci_entry
            else:
                name = ""
            if name and name not in existing:
                item["keywords"].append({
                    "term": name, "source": "dict:inci", "type": "INCI英名"
                })
                existing.add(name)
                added_total += 1

    logger.info("辞書展開: 計 %d 語追加", added_total)


def _fallback_regex_anchors(segments, hongan, field):
    """AI非使用時のフォールバック。

    正規表現のカタカナ断片・漢字断片をやめ、
    分節テキスト自体をアンカー語として使う簡易版。
    """
    results = []
    for claim in segments:
        for seg in claim.get("segments", []):
            preprocessed = _preprocess_text(seg["text"])
            # 分節テキストから意味のある語句のみ（ストップワード除外）
            keywords = _pick_terms_from_text(preprocessed)
            results.append({
                "segment_id": seg["id"],
                "segment_text": seg["text"],
                "keywords": keywords,
            })
    # 辞書展開
    _dict_expand_pipeline_result(results, field)
    return results


def recommend_by_tech_analysis(segments, hongan, field):
    """新メインエントリポイント: AI技術構造化ベースのキーワード選定。

    AI技術構造化 → 明細書定義セクション解析 → 実施例抽出 → 辞書展開

    Parameters:
        segments: 請求項分節データ (segments.json)
        hongan: 本願構造化テキスト (hongan.json)
        field: "cosmetics" | "laminate" | etc.

    Returns:
        tuple: (tech_analysis_dict or None, pipeline_result_list)
    """
    # Step 1: AIプロンプト生成 & 呼び出し
    prompt = _build_tech_analysis_prompt(segments, hongan, field)
    logger.info("tech_analysis プロンプト生成: %d 文字", len(prompt))

    tech_analysis = _call_ai_tech_analysis(prompt, field)

    if tech_analysis and tech_analysis.get("elements"):
        # Step 2: tech_analysis → pipeline_result 変換
        results = _tech_analysis_to_pipeline_result(tech_analysis, segments)
        logger.info("tech_analysis 変換完了: %d 分節", len(results))

        # Step 3: Python側で辞書展開（AIなし）
        _dict_expand_pipeline_result(results, field)

        # 最終重複除去
        for item in results:
            seen = set()
            unique = []
            for kw in item["keywords"]:
                if kw["term"] not in seen:
                    seen.add(kw["term"])
                    unique.append(kw)
            item["keywords"] = unique

        logger.info("新パイプライン完了: %d 分節、計 %d 語",
                    len(results),
                    sum(len(r["keywords"]) for r in results))
        return tech_analysis, results
    else:
        # フォールバック: 正規表現ベース
        logger.warning("tech_analysis 取得失敗 — フォールバックで実行")
        results = _fallback_regex_anchors(segments, hongan, field)
        return None, results


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
