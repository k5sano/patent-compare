#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
対比用プロンプト自動生成モジュール（最重要）

入力:
- segments: 請求項分節
- citations: 引用文献構造化テキスト（1件 or 複数件）
- keywords: キーワードグループ（任意）

出力: Claudeチャットに貼り付けるプロンプト文字列
"""

import json
import yaml
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()

def _load_prompt_config():
    """config.yaml からプロンプト生成設定を読み込む"""
    config_path = _PROJECT_ROOT / "config.yaml"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            prompt_cfg = cfg.get("prompt", {})
            return prompt_cfg
        except Exception:
            pass
    return {}

_prompt_cfg = _load_prompt_config()

# テキスト量上限（全文献合計）
MAX_TOTAL_CHARS = _prompt_cfg.get("max_total_chars", 80000)

# セクション優先順位（テキスト量超過時のトリミング用）
SECTION_PRIORITY = _prompt_cfg.get("section_priority",
    ["実施例", "比較例", "請求項", "手段", "効果", "実施形態", "課題", "背景技術", "技術分野"])


def _build_task_definition(num_citations):
    """タスク定義セクション"""
    if num_citations == 1:
        return """## タスク
あなたは日本の特許審査における拒絶理由構成を支援する先行技術調査の専門家です。
以下の本願（出願中の特許）の請求項の構成要件と、引用文献（先行技術）を対比し、
各構成要件が引用文献に開示されているかを判定してください。"""
    else:
        return f"""## タスク
あなたは日本の特許審査における拒絶理由構成を支援する先行技術調査の専門家です。
以下の本願（出願中の特許）の請求項の構成要件と、{num_citations}件の引用文献（先行技術）をそれぞれ対比し、
各構成要件が各引用文献に開示されているかを判定してください。
文献ごとに独立して判定を行い、文献ごとにJSON結果を出力してください。"""


def _build_citation_priority_rules():
    """引用優先順位ルール"""
    return """## 引用箇所の優先順位
引用文献から該当記載を探す際は、以下の優先順位で引用してください：
1. **実施例**（具体的な配合例、実験データ、数値データ）— 最も証拠力が強い
2. **検出された表（配合表・比較例データ等）** — 本文とは別に冒頭の「### 【検出された表】」セクションにまとめて提示します。**表中の成分・配合量・物性値は最も信頼性の高い証拠**なので必ず検討対象に含めてください
3. **詳細な定義が書かれた箇所**（「本発明において○○とは」等の定義段落）
4. **請求項（クレーム）** — 権利範囲として明確
5. **一言でも言及がある箇所** — 最低限の開示

**重要**: 「### 【検出された表】」セクションの内容は段落本文と同じ出典（【XXXX】で段落番号を記載）ですが、実施例・比較例の数値表としてはここから引用することを推奨します。cited_location には「表X（段落【XXXX】）」の形で表番号と段落番号を併記してください。"""


def _build_judgment_criteria():
    """判定基準"""
    return """## 判定基準
各構成要件について以下の3段階で判定してください：
- **○（一致）**: 引用文献に同一又は実質的に同一の構成が明確に記載されている
- **△（部分一致）**: 上位概念での記載がある、数値範囲が一部重複する、類似の構成がある等
- **×（不一致）**: 引用文献に対応する記載が見当たらない"""


def _build_field_notes(field):
    """分野特有の注意事項"""
    if field == "cosmetics":
        return """## 化粧品分野の注意事項
- **成分名の表記ゆれに注意**: INCI名、和名、商品名、化学名が混在します
  - 例: 「BG」=「1,3-ブチレングリコール」=「1,3-Butylene Glycol」
  - 例: 「ペリセア」=「ジラウロイルグルタミン酸リシンNa」
- **配合量**: 成分だけでなく配合量（質量%等）の範囲も対比してください
- **配合理由**: 成分がどのような目的で配合されているかも重要です
  （例: 「保湿剤として」「乳化剤として」「防腐剤として」）
- **上位概念・下位概念の関係**:
  - 引用文献が下位概念（具体名）を開示 → 本願の上位概念に対して○
  - 引用文献が上位概念のみ → 本願の下位概念に対して△
- **実施例の配合表**: 表中の配合成分と配合量は最も信頼性の高い証拠です"""
    elif field == "laminate":
        return """## 積層体分野の注意事項
- **層構成の対応関係**: 層の数、順序、各層の材料を対比してください
- **材料名の同義語に注意**:
  - 例: 「PET」=「ポリエチレンテレフタレート」
  - 例: 「ナイロン」=「ポリアミド」=「PA」
- **厚さ・物性値**: 数値範囲の重複を確認してください
- **製法の限定**: 「二軸延伸」「蒸着」等の製法限定も構成要件です"""
    return ""


def _build_segments_section(segments):
    """本願の請求項分節セクション"""
    lines = ["## 本願の請求項 構成要件"]
    for claim in segments:
        claim_num = claim["claim_number"]
        dep_type = "独立" if claim["is_independent"] else f"従属（→請求項{','.join(map(str, claim['dependencies']))}）"
        lines.append(f"\n### 請求項{claim_num}（{dep_type}）")
        for seg in claim["segments"]:
            lines.append(f"- **{seg['id']}**: {seg['text']}")
    return "\n".join(lines)


def _build_keywords_section(keywords):
    """キーワードグループセクション"""
    if not keywords:
        return ""

    lines = ["## キーワードグループ（参照用）"]
    lines.append("以下は本願の構成要件に対応するキーワードグループです。表記ゆれの参考にしてください。")
    for group in keywords:
        lines.append(f"\n### グループ{group['group_id']}: {group['label']}（分節: {', '.join(group['segment_ids'])}）")
        for kw in group["keywords"]:
            lines.append(f"- {kw['term']}（{kw['type']}、出典: {kw['source']}）")
    return "\n".join(lines)


def _trim_citation_text(citation, max_chars):
    """引用文献テキストをセクション優先順位に基づいてトリミング。

    順序:
      1. 請求の範囲（claims）
      2. 検出された表（tables）— 実施例の配合表は最重要証拠なので常に優先
      3. セクション優先順位に基づく段落（SECTION_PRIORITY）
      4. 残りの段落
    表として既に含めた段落は 3/4 で重複させない。
    """
    paragraphs = citation.get("paragraphs", [])
    claims = citation.get("claims", [])
    tables = citation.get("tables", []) or []

    by_section = {}
    for para in paragraphs:
        section = para.get("section", "その他")
        by_section.setdefault(section, []).append(para)

    selected = []
    total_chars = 0

    claims_text = ""
    if claims:
        claims_lines = ["### 【特許請求の範囲】"]
        for cl in claims:
            claims_lines.append(f"【請求項{cl['number']}】{cl['text']}")
        claims_text = "\n".join(claims_lines)
        total_chars += len(claims_text)

    # 表（実施例の配合表など）を最優先で含める
    tables_lines = []
    table_para_ids = set()
    if tables:
        tables_lines.append("### 【検出された表（実施例の配合表・比較例データ等）】")
        for t in tables:
            pid = str(t.get("paragraph_id", "")).strip()
            tid = t.get("id", "表?")
            page = t.get("page", "?")
            section = t.get("section", "")
            content = t.get("content", "") or ""
            if not content:
                continue
            header = f"#### {tid}（段落【{pid}】 p.{page}"
            if section:
                header += f" / {section}"
            header += "）"
            entry = f"{header}\n{content}"
            if total_chars + len(entry) > max_chars:
                # 予算オーバーでも最低 1 つは入れる（claims を削ってでも）
                if not table_para_ids:
                    tables_lines.append(entry)
                    total_chars += len(entry)
                    if pid:
                        table_para_ids.add(pid)
                break
            tables_lines.append(entry)
            total_chars += len(entry)
            if pid:
                table_para_ids.add(pid)
        if len(tables_lines) == 1:
            # ヘッダだけ残ったらリセット
            tables_lines = []

    for section_name in SECTION_PRIORITY:
        if section_name == "請求項":
            continue
        paras = by_section.get(section_name, [])
        for para in paras:
            if para["id"] in table_para_ids:
                continue  # 表で既に含めた段落はスキップ
            para_text = f"【{para['id']}】{para['text']}"
            if total_chars + len(para_text) > max_chars:
                break
            selected.append(para)
            total_chars += len(para_text)

    included_ids = {p["id"] for p in selected} | table_para_ids
    for para in paragraphs:
        if para["id"] not in included_ids:
            para_text = f"【{para['id']}】{para['text']}"
            if total_chars + len(para_text) > max_chars:
                break
            selected.append(para)
            total_chars += len(para_text)

    selected.sort(key=lambda p: p["id"])

    lines = []
    if claims_text:
        lines.append(claims_text)
    if tables_lines:
        lines.append("\n".join(tables_lines))
    current_section = None
    for para in selected:
        section = para.get("section", "")
        if section != current_section:
            lines.append(f"\n### 【{section}】")
            current_section = section
        lines.append(f"【{para['id']}】{para['text']}")

    return "\n".join(lines)


def _build_citations_section(citations):
    """複数引用文献テキストセクション"""
    num = len(citations)
    # 文献あたりの文字数上限を均等配分
    per_citation_chars = MAX_TOTAL_CHARS // max(num, 1)

    sections = []
    for i, citation in enumerate(citations, 1):
        doc_id = citation.get("patent_number", citation.get("file_name", "不明"))
        role = citation.get("role", "主引例")
        label = citation.get("label", doc_id)

        lines = [f"## 引用文献{i}: {label}（{doc_id}）"]
        lines.append(f"役割: {role}")
        lines.append("")
        full_text = _trim_citation_text(citation, per_citation_chars)
        lines.append(full_text)
        sections.append("\n".join(lines))

    return "\n\n---\n\n".join(sections)


def _build_output_format_multi(citations, segments):
    """複数文献対応の出力フォーマット指定"""
    # 請求項1の分節IDリスト
    claim1_ids = []
    sub_claims = []
    for claim in segments:
        if claim["claim_number"] == 1:
            for seg in claim["segments"]:
                claim1_ids.append(seg["id"])
        else:
            sub_claims.append(claim)

    # 文献情報リスト
    doc_list = []
    for cit in citations:
        doc_id = cit.get("patent_number", cit.get("file_name", "不明"))
        role = cit.get("role", "主引例")
        doc_list.append({"id": doc_id, "role": role})

    # 比較結果のサンプル（最初の分節2つ分）
    example_comparisons = []
    for seg_id in claim1_ids[:2]:
        example_comparisons.append(f"""        {{
            "requirement_id": "{seg_id}",
            "judgment": "○ or △ or ×",
            "judgment_reason": "判定理由を具体的に記載",
            "cited_location": "引用箇所（段落番号、請求項番号、表番号等）",
            "section_type": "実施例 or 定義 or クレーム or 言及",
            "cited_text": "引用文献の該当記載をそのまま抜粋",
            "formulation_reason": "配合理由があれば記載（化粧品分野）",
            "note": "補足があれば"
        }}""")
    comparisons_str = ",\n".join(example_comparisons)

    sub_claim_example = ""
    if sub_claims:
        sub_claim_example = f""",
    "sub_claims": [
        {{
            "claim_number": {sub_claims[0]['claim_number']},
            "requirement_text": "追加の限定事項テキスト",
            "judgment": "○ or △ or ×",
            "judgment_reason": "判定理由",
            "cited_location": "引用箇所",
            "note": ""
        }}
    ]"""

    # 単一文献の場合
    if len(citations) == 1:
        doc_id = doc_list[0]["id"]
        role = doc_list[0]["role"]
        return f"""## 出力フォーマット
以下のJSON形式で回答してください。必ず全ての構成要件（{', '.join(claim1_ids)}）について判定を含めてください。

```json
{{
    "document_id": "{doc_id}",
    "document_role": "{role}",
    "comparisons": [
{comparisons_str},
        ... （全ての構成要件 {', '.join(claim1_ids)} について記載）
    ]{sub_claim_example},
    "overall_summary": "この引用文献の概要と本願との関連性を3-5文で記述",
    "category_suggestion": "X or Y or A（X=単独で拒絶可能, Y=組合せで拒絶可能, A=参考文献）",
    "rejection_relevance": "拒絶理由との関連性（例: 【進歩性欠如の主引例候補】）"
}}
```

### 出力時の注意
- judgment は必ず「○」「△」「×」のいずれかを使用してください
- cited_location は段落番号（【XXXX】）、請求項番号、表番号等を具体的に記載してください
- cited_text は引用文献の記載をそのまま抜粋してください（要約ではなく原文）
- ×（不一致）の場合でも judgment_reason に「該当する記載なし」等の理由を記載してください"""

    # 複数文献の場合: results 配列でラップ
    doc_examples = []
    for d in doc_list[:2]:  # サンプルは2件まで
        doc_examples.append(f"""    {{
        "document_id": "{d['id']}",
        "document_role": "{d['role']}",
        "comparisons": [
{comparisons_str},
            ... （全構成要件 {', '.join(claim1_ids)} について）
        ]{sub_claim_example},
        "overall_summary": "文献の概要",
        "category_suggestion": "X or Y or A",
        "rejection_relevance": "拒絶理由との関連性"
    }}""")
    doc_examples_str = ",\n".join(doc_examples)

    remaining = ""
    if len(doc_list) > 2:
        remaining = f"\n        ... （残り{len(doc_list) - 2}件も同じ形式で）"

    return f"""## 出力フォーマット
**{len(citations)}件の文献それぞれについて**、以下のJSON形式で回答してください。
全文献の結果を `results` 配列にまとめてください。
必ず全ての構成要件（{', '.join(claim1_ids)}）について各文献ごとに判定を含めてください。

```json
{{
    "results": [
{doc_examples_str}{remaining}
    ]
}}
```

### 出力時の注意
- **文献ごとに独立して判定**してください（文献間の組合せ判断は不要）
- judgment は必ず「○」「△」「×」のいずれかを使用してください
- cited_location は段落番号（【XXXX】）、請求項番号、表番号等を具体的に記載してください
- cited_text は引用文献の記載をそのまま抜粋してください（要約ではなく原文）
- ×（不一致）の場合でも judgment_reason に「該当する記載なし」等の理由を記載してください
- **全{len(citations)}件の文献について必ず結果を含めてください**"""


def generate_prompt(segments, citations, keywords=None, field="cosmetics"):
    """対比プロンプトを生成するメインエントリポイント

    Parameters:
        segments: 請求項分節データ (segments.json)
        citations: 引用文献データ。dict(1件) or list[dict](複数件)
        keywords: キーワードグループ (keywords.json)、任意
        field: "cosmetics" | "laminate"

    Returns:
        プロンプト文字列
    """
    # 後方互換: dict1件の場合はリストに変換
    if isinstance(citations, dict):
        citations = [citations]

    num = len(citations)

    # フィールドを引用文献メタから取得（あれば）
    if citations and hasattr(citations[0], 'get'):
        field = citations[0].get("field", field)

    sections = [
        _build_task_definition(num),
        _build_citation_priority_rules(),
        _build_judgment_criteria(),
        _build_field_notes(field),
        _build_segments_section(segments),
        _build_keywords_section(keywords),
        _build_citations_section(citations),
        _build_output_format_multi(citations, segments),
    ]

    prompt = "\n\n---\n\n".join(s for s in sections if s.strip())
    return prompt
