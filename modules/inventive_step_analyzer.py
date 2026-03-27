#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
進歩性判断プロンプト生成モジュール

特許庁「特許・実用新案審査基準 第III部 第2章 第2節 進歩性」に基づき、
対比結果から進歩性の論理付けプロンプトを生成する。

参照: https://www.jpo.go.jp/system/laws/rule/guideline/patent/tukujitu_kijun/document/index/03_0202bm.pdf
"""

import json


# ========== JPO進歩性審査基準フレームワーク ==========

JPO_FRAMEWORK = """## JPO進歩性審査基準（第III部 第2章 第2節）に基づく判断フレームワーク

### 判断手順
審査官は、主引用発明から出発して以下の手順で論理付けを行う:
(1) 相違点について、否定方向の要素に基づき、副引用発明の適用や技術常識を考慮して論理付けできるか判断
(2) 論理付けできない → 進歩性あり
(3) 論理付けできる場合 → 肯定方向の要素も含め総合評価
(4) 総合評価で論理付け成立 → 進歩性なし / 不成立 → 進歩性あり

### 進歩性が【否定】される方向に働く要素

#### A. 主引用発明に副引用発明を適用する動機付け
以下の4観点を総合考慮して判断する:
(1) **技術分野の関連性** — 主引例と副引例が関連する技術分野に属するか
(2) **課題の共通性** — 主引例と副引例で解決しようとする課題が共通するか（自明な課題を含む）
(3) **作用・機能の共通性** — 主引例と副引例で作用・機能が共通するか
(4) **引用発明の内容中の示唆** — 主引例の刊行物中に副引例の適用を示唆する記載があるか

#### B. 設計変更等（当業者の通常の創作能力の発揮）
(i) 公知材料の中からの最適材料の選択
(ii) 数値範囲の最適化又は好適化
(iii) 均等物による置換
(iv) 技術の具体的適用に伴う設計変更や設計的事項の採用

#### C. 先行技術の単なる寄せ集め
発明特定事項の各々が公知で、互いに機能的・作用的に関連していない場合

### 進歩性が【肯定】される方向に働く要素

#### D. 引用発明と比較した有利な効果
(i) 引用発明とは**異質な効果**で、技術水準から予測できないもの
(ii) 引用発明と**同質だが際だって優れた効果**で、技術水準から予測できないもの

#### E. 阻害要因
(i) 副引例を適用すると主引例がその**目的に反する**ものとなる
(ii) 副引例を適用すると主引例が**機能しなくなる**
(iii) 主引例が副引例の適用を**積極的に排斥**している
(iv) 副引例が、主引例の課題に関して**劣る例として記載**されている

### 留意事項
- 後知恵に陥らないこと（請求項の知識に引きずられない）
- 主引用発明は、請求項と技術分野又は課題が同一又は近いものを選択
- 周知技術であるという理由だけで論理付けの検討を省略しない
- 商業的成功等は二次的指標として参酌可能（技術的特徴に基づく場合のみ）"""


def generate_inventive_step_prompt(segments, responses, citations_meta, keywords=None, field="cosmetics"):
    """進歩性判断プロンプトを生成

    Parameters:
        segments: 請求項分節データ (segments.json)
        responses: {doc_id: response_data} 全文献の対比結果
        citations_meta: {doc_id: citation_meta} 引用文献メタ情報
        keywords: キーワードグループ (keywords.json, optional)
        field: "cosmetics" | "laminate"

    Returns:
        プロンプト文字列
    """
    sections = [
        _build_task(),
        JPO_FRAMEWORK,
        _build_claim_summary(segments),
        _build_comparison_summary(segments, responses, citations_meta),
        _build_analysis_instructions(field),
        _build_output_format(responses),
    ]

    return "\n\n---\n\n".join(s for s in sections if s.strip())


def _build_task():
    return """## タスク
あなたは日本の特許審査における進歩性判断の専門家です。
以下の本願請求項の構成要件と、各引用文献との対比結果に基づき、
JPO進歩性審査基準に従って進歩性の論理付けを行ってください。

具体的には:
1. 主引例候補の中から最適な主引用発明を選定し、一致点・相違点を整理
2. 各相違点について、副引例や技術常識による論理付けを試みる
3. 動機付け（4観点）、設計変更等、阻害要因、有利な効果を総合評価
4. 拒絶理由通知における進歩性欠如の論理構成を提案"""


def _build_claim_summary(segments):
    lines = ["## 本願請求項の構成要件"]
    for claim in segments:
        if claim["claim_number"] == 1:
            for seg in claim["segments"]:
                lines.append(f"- **{seg['id']}**: {seg['text']}")
            break
    return "\n".join(lines)


def _build_comparison_summary(segments, responses, citations_meta):
    """全文献の対比結果をサマリ表形式で"""
    lines = ["## 対比結果サマリ"]

    # 請求項1の分節IDリスト
    seg_ids = []
    for claim in segments:
        if claim["claim_number"] == 1:
            seg_ids = [s["id"] for s in claim["segments"]]
            break

    # 文献ごとの結果
    for doc_id, resp in responses.items():
        role = resp.get("document_role", "不明")
        cat = resp.get("category_suggestion", "")
        summary = resp.get("overall_summary", "")
        lines.append(f"\n### {doc_id}（{role}、カテゴリ: {cat}）")
        lines.append(f"概要: {summary}")
        lines.append("")
        lines.append("| 構成要件 | 判定 | 引用箇所 | 判定理由 |")
        lines.append("|----------|------|----------|----------|")

        comparisons = {c["requirement_id"]: c for c in resp.get("comparisons", [])}
        for sid in seg_ids:
            comp = comparisons.get(sid, {})
            j = comp.get("judgment", "—")
            loc = comp.get("cited_location", "")
            reason = comp.get("judgment_reason", "")
            # テーブル内の改行を除去
            reason = reason.replace("\n", " ")[:100]
            lines.append(f"| {sid} | {j} | {loc} | {reason} |")

        # 一致・相違の集計
        judgments = [comparisons.get(sid, {}).get("judgment", "×") for sid in seg_ids]
        match = sum(1 for j in judgments if j == "○")
        partial = sum(1 for j in judgments if j == "△")
        diff = sum(1 for j in judgments if j == "×")
        lines.append(f"\n**集計**: ○={match}, △={partial}, ×={diff} / 全{len(seg_ids)}要件")

        # 相違点リスト
        diff_segs = [sid for sid in seg_ids if comparisons.get(sid, {}).get("judgment") == "×"]
        partial_segs = [sid for sid in seg_ids if comparisons.get(sid, {}).get("judgment") == "△"]
        if diff_segs:
            lines.append(f"**不一致（×）の構成要件**: {', '.join(diff_segs)}")
        if partial_segs:
            lines.append(f"**部分一致（△）の構成要件**: {', '.join(partial_segs)}")

        # 従属請求項
        sub_claims = resp.get("sub_claims", [])
        if sub_claims:
            sub_info = [f"請求項{sc['claim_number']}:{sc['judgment']}" for sc in sub_claims]
            lines.append(f"**従属請求項**: {', '.join(sub_info)}")

    return "\n".join(lines)


def _build_analysis_instructions(field):
    lines = ["""## 分析指示

### Step 1: 主引用発明の選定
- 対比結果から最も一致度の高い文献を主引例として選定
- 選定理由を述べること（技術分野・課題の近さ、○判定の多さ）

### Step 2: 一致点・相違点の整理
- 主引例との一致点（○の構成要件）を列挙
- 相違点（×および△の構成要件）を具体的に記述
- 各相違点の技術的意義を説明

### Step 3: 相違点ごとの論理付け
各相違点について以下を検討:
- **副引例による補完**: どの副引例がこの相違点をカバーするか
- **動機付けの4観点**:
  - (1) 技術分野の関連性
  - (2) 課題の共通性
  - (3) 作用・機能の共通性
  - (4) 引用発明の内容中の示唆
- **設計変更等**: 最適材料選択、数値範囲最適化、均等物置換、設計的事項に該当するか
- **阻害要因の有無**: 目的矛盾、機能不全、排斥、劣る例として記載

### Step 4: 有利な効果の検討
- 本願が主張する効果が引用発明と比較して有利か
- 異質な効果か、際だって優れた同質効果か
- 技術水準から予測可能か

### Step 5: 総合評価と結論
- 否定方向・肯定方向の要素を総合して進歩性を判断
- 拒絶理由通知の起案に使える論理構成を提示"""]

    if field == "cosmetics":
        lines.append("""
### 化粧品分野の特記事項
- 成分の組合せによる相乗効果は有利な効果になりうる
- 配合量の数値限定は、臨界的意義がなければ設計的事項
- 新規成分の使用自体は進歩性を基礎づけうるが、公知成分の組合せは論理付けが容易""")
    elif field == "laminate":
        lines.append("""
### 積層体分野の特記事項
- 層構成の変更は設計的事項となりやすい
- 材料の選択は公知材料の最適選択となりやすい
- 層間の相互作用による予想外の効果は有利な効果になりうる""")

    return "\n".join(lines)


def _build_output_format(responses):
    doc_ids = list(responses.keys())

    return f"""## 出力フォーマット
以下のJSON形式で回答してください。

```json
{{
    "primary_reference": {{
        "document_id": "主引例の文献ID",
        "selection_reason": "主引例として選定した理由"
    }},
    "common_features": [
        {{
            "segment_ids": ["1A", "1B"],
            "description": "一致点の説明"
        }}
    ],
    "differences": [
        {{
            "segment_id": "1G",
            "description": "相違点の具体的内容",
            "technical_significance": "この相違点の技術的意義",
            "resolution": {{
                "method": "副引例適用" | "設計変更等" | "技術常識" | "論理付け不可",
                "secondary_reference": "副引例の文献ID（該当する場合）",
                "motivation": {{
                    "technical_field": "技術分野の関連性の根拠",
                    "common_problem": "課題の共通性の根拠",
                    "common_function": "作用・機能の共通性の根拠",
                    "suggestion": "引用発明中の示唆の根拠"
                }},
                "design_change_type": "最適材料選択 | 数値範囲最適化 | 均等物置換 | 設計的事項 | null",
                "inhibiting_factors": ["阻害要因があれば記載"],
                "conclusion": "この相違点についての論理付け結論"
            }}
        }}
    ],
    "advantageous_effects": {{
        "claimed_effects": "本願が主張する効果",
        "is_heterogeneous": true | false,
        "is_remarkably_superior": true | false,
        "is_predictable": true | false,
        "assessment": "有利な効果の評価"
    }},
    "overall_assessment": {{
        "inventive_step": "あり" | "なし" | "微妙",
        "reasoning": "総合評価の理由（3〜5文）",
        "rejection_logic": "拒絶理由通知用の論理構成（引用文献の組合せと論理の流れ）",
        "vulnerable_points": "本願側が反論しやすいポイント",
        "strengthening_suggestions": "論理付けを強化するための提案"
    }}
}}
```

### 出力時の注意
- 全ての相違点（×および△の構成要件）について個別に検討すること
- 動機付けの4観点は全て検討し、該当しない場合は理由を述べること
- 論理付けが困難な相違点がある場合は正直に「論理付け不可」とすること
- 文献ID は対比結果で使用されているID（{', '.join(doc_ids)}）をそのまま使用すること"""


def parse_inventive_step_response(raw_text):
    """進歩性分析の回答をパース

    Parameters:
        raw_text: Claudeの回答テキスト

    Returns:
        (parsed_data, errors)
    """
    import re

    # JSON抽出
    json_block = re.compile(r'```(?:json)?\s*\n?(.*?)\n?\s*```', re.DOTALL)
    matches = json_block.findall(raw_text)
    for match in matches:
        try:
            data = json.loads(match)
            if isinstance(data, dict) and "overall_assessment" in data:
                return data, []
        except json.JSONDecodeError:
            continue

    # パターン2: 最外側 { ... }
    depth = 0
    start = None
    for i, ch in enumerate(raw_text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    data = json.loads(raw_text[start:i + 1])
                    if isinstance(data, dict) and "overall_assessment" in data:
                        return data, []
                except json.JSONDecodeError:
                    start = None

    return None, ["進歩性分析のJSONを抽出できませんでした。"]
