#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
進歩性判断プロンプト生成モジュール

特許庁「特許・実用新案審査基準 第III部 第2章 第2節 進歩性」に基づき、
対比結果から進歩性の論理付けプロンプトを生成する。

参照: https://www.jpo.go.jp/system/laws/rule/guideline/patent/tukujitu_kijun/document/index/03_0202bm.pdf
"""

import json
import re
import unicodedata
from pathlib import Path

import yaml


_PROJECT_ROOT = Path(__file__).parent.parent.resolve()


def _load_inventive_config():
    config_path = _PROJECT_ROOT / "config.yaml"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            return cfg.get("inventive_step", {}) or {}
        except Exception:
            pass
    return {}


_inventive_cfg = _load_inventive_config()
MAX_TOTAL_CHARS = int(_inventive_cfg.get("max_total_chars", 120000))
MAX_HONGAN_CHARS = int(_inventive_cfg.get("max_hongan_chars", 30000))
MAX_CITATION_EVIDENCE_CHARS = int(_inventive_cfg.get("max_citation_evidence_chars", 60000))
MAX_PER_PARA_CHARS = int(_inventive_cfg.get("max_per_para_chars", 400))
MAX_PER_TABLE_CHARS = int(_inventive_cfg.get("max_per_table_chars", 1000))


def get_inventive_step_defaults():
    return {
        "default_model": _inventive_cfg.get("default_model", "opus"),
        "default_effort": _inventive_cfg.get("default_effort", "high"),
    }


def _clip(text, limit):
    text = str(text or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[trimmed]"


def _norm_id(value):
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _id_variants(value):
    s = _norm_id(value)
    if not s:
        return set()
    variants = {s, s.upper()}
    digits = re.sub(r"\D+", "", s)
    if digits:
        stripped = digits.lstrip("0") or "0"
        variants.update({digits, stripped, f"T{stripped}", f"表{stripped}"})
    return variants


def _section_matches(item, *keywords):
    section = _norm_id(item.get("section", ""))
    text = _norm_id(item.get("text", ""))
    haystack = f"{section} {text[:120]}"
    return any(k in haystack for k in keywords)


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


def generate_inventive_step_prompt(
    segments, responses, citations_meta, keywords=None, field="cosmetics", hongan=None
):
    """進歩性判断プロンプトを生成

    Parameters:
        segments: 請求項分節データ (segments.json)
        responses: {doc_id: response_data} 全文献の対比結果
        citations_meta: {doc_id: citation_meta} 引用文献メタ情報
        keywords: キーワードグループ (keywords.json, optional)
        field: "cosmetics" | "laminate"
        hongan: hongan.json の内容 (optional)

    Returns:
        プロンプト文字列
    """
    task = _build_task()
    thinking = _build_thinking_instruction()
    reasoning = _build_reasoning_order()
    hongan_essence = _build_hongan_essence(hongan)
    claim_summary = _build_claim_summary(segments)
    comparison_summary = _build_comparison_summary(segments, responses, citations_meta)
    citation_evidence = _build_citation_evidence(responses, citations_meta)
    analysis = _build_analysis_instructions(field)
    effect_analysis = _build_effect_analysis_instructions()
    output_format = _build_output_format(responses)

    def _join(hongan_text, evidence_text):
        sections = [
            task,
            JPO_FRAMEWORK,
            thinking,
            reasoning,
            hongan_text,
            claim_summary,
            comparison_summary,
            evidence_text,
            analysis,
            effect_analysis,
            output_format,
        ]
        return "\n\n---\n\n".join(s for s in sections if s.strip())

    prompt = _join(hongan_essence, citation_evidence)
    if len(prompt) <= MAX_TOTAL_CHARS:
        return prompt

    # まず citation evidence を削って全体上限に収める。本願情報と出力形式は優先的に残す。
    overflow = len(prompt) - MAX_TOTAL_CHARS
    reduced_evidence = _clip(citation_evidence, max(0, len(citation_evidence) - overflow - 1000))
    prompt = _join(hongan_essence, reduced_evidence)
    if len(prompt) <= MAX_TOTAL_CHARS:
        return prompt

    # それでも超える場合だけ本願 essence も削る。出力フォーマットは削らない。
    overflow = len(prompt) - MAX_TOTAL_CHARS
    reduced_hongan = _clip(hongan_essence, max(0, len(hongan_essence) - overflow - 1000))
    prompt = _join(reduced_hongan, reduced_evidence)
    if len(prompt) <= MAX_TOTAL_CHARS:
        return prompt

    # 最終保険: 本文証拠を空にし、本願 essence をさらに削る。
    fixed_without_hongan = len(_join("", ""))
    hongan_budget = max(0, MAX_TOTAL_CHARS - fixed_without_hongan - 1000)
    return _join(_clip(hongan_essence, hongan_budget), "")


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


def _build_thinking_instruction():
    return """## 検討の深さ

回答では、結論だけでなく、結論に至る検討要旨を `"deliberation"` フィールドに500字以上で記載してください。
これは隠れた思考過程ではなく、審査官が後で検証できる公開可能な検討メモとして、以下を含めること。

- 主引例選定の根拠
- 効果論で迷った点
- 「特別な効果なし」と判断した場合の理由、又は「効果あり」と判断した根拠
- 数値範囲の臨界性について、比較例データで裏付けられているか
- 引例の実施例数値が本願範囲に近接又は重複している場合、単なる最適化にすぎない可能性"""


def _build_reasoning_order():
    return """## 思考順序の厳守（重要）

以下の順序で必ず検討してください。後の情報で前の判断を歪めないこと。

1. **まず引例だけを読む**: 本願の知識を一旦切り離し、各引例が単独で何を開示しているかを把握
2. **次に本願請求項だけを読む**: 主引例から見て、何が新しく見えるかを記録
3. **対比結果を確認**: その上で対比結果（○△×）を確認し、相違点を特定
4. **動機付けの検討**: 副引例を加える際、それは引例の世界の論理だけで成立するか（後知恵でないか）
5. **最後に本願の効果を検討**: 効果論で初めて本願明細書の実施例・比較例データを参照
6. **自己検証**: 上記の論理が「本願を見た後だから言える後付け」になっていないか1度見直す"""


def _format_para(para, limit=MAX_PER_PARA_CHARS):
    pid = _norm_id(para.get("id", "?"))
    text = _clip(para.get("text", ""), limit)
    return f"【{pid}】 {text}"


def _table_text(table):
    for key in ("markdown", "content", "text", "html"):
        value = table.get(key)
        if value:
            return str(value)
    headers = table.get("headers")
    rows = table.get("rows")
    if headers or rows:
        lines = []
        if headers:
            lines.append(" | ".join(str(x) for x in headers))
        for row in rows or []:
            if isinstance(row, dict):
                cells = row.get("cells", [])
            else:
                cells = row
            lines.append(" | ".join(str(x) for x in cells))
        return "\n".join(lines)
    return ""


def _build_hongan_essence(hongan):
    """本願の課題・効果・実施例段落・実施例表を圧縮抽出する。"""
    if not hongan:
        return ""

    lines = ["## 本願の技術思想（明細書からの抽出）"]
    paras = hongan.get("paragraphs", []) or []

    problems = [p for p in paras if _section_matches(p, "課題", "解決しようとする課題")]
    if problems:
        lines.append("### 解決しようとする課題")
        lines.extend(_format_para(p) for p in problems[:5])

    effects = [p for p in paras if _section_matches(p, "効果", "発明の効果")]
    if effects:
        lines.append("### 発明の効果（明細書記載）")
        lines.extend(_format_para(p) for p in effects[:6])

    tables = hongan.get("tables", []) or []
    if tables:
        lines.append("### 実施例・比較例の数値データ（表）")
        for t in tables[:5]:
            tid = _norm_id(t.get("id") or t.get("table_number") or "?")
            page = t.get("page")
            page_part = f" P{page}" if page else ""
            lines.append(f"#### 表 {tid}{page_part}")
            lines.append(_clip(_table_text(t), MAX_PER_TABLE_CHARS))

    examples = [p for p in paras if _section_matches(p, "実施例", "比較例")]
    if examples:
        lines.append("### 実施例・比較例段落")
        lines.extend(_format_para(p) for p in examples[:12])

    return _clip("\n".join(lines), MAX_HONGAN_CHARS)


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


def _extract_para_ids(loc):
    """引用箇所記法から段落番号を抽出する。例: '23,24;T1;CL3' -> ['23', '24']"""
    out = []
    for chunk in str(loc or "").split(";"):
        chunk = _norm_id(chunk).strip()
        if not chunk:
            continue
        chunk = chunk.split("/")[0].strip()
        if not chunk:
            continue
        if re.match(r"^(T|CL|F|K|E|S|P|C|G)\d", chunk, re.IGNORECASE):
            continue
        for part in chunk.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                try:
                    a, b = [int(x) for x in part.split("-", 1)]
                    if a <= b and b - a <= 100:
                        out.extend(str(i) for i in range(a, b + 1))
                except ValueError:
                    continue
            elif part.isdigit():
                out.append(str(int(part)))
    return out


def _extract_table_ids(loc):
    """引用箇所記法から表番号を抽出する。例: 'T1,2;23' -> ['1', '2']"""
    out = []
    text = _norm_id(loc)
    for match in re.finditer(r"T(\d+(?:\s*(?:,|-)\s*\d+)*)", text, re.IGNORECASE):
        body = match.group(1).replace(" ", "")
        for part in body.split(","):
            if "-" in part:
                try:
                    a, b = [int(x) for x in part.split("-", 1)]
                    if a <= b and b - a <= 100:
                        out.extend(str(i) for i in range(a, b + 1))
                except ValueError:
                    continue
            elif part.isdigit():
                out.append(str(int(part)))
    return out


def _para_map(citation):
    result = {}
    for para in citation.get("paragraphs", []) or []:
        for key in _id_variants(para.get("id")):
            result[key] = para
    return result


def _table_map(citation):
    result = {}
    for table in citation.get("tables", []) or []:
        for field in ("id", "table_number", "label", "caption"):
            for key in _id_variants(table.get(field)):
                result[key] = table
    return result


def _build_citation_evidence(responses, citations_meta):
    """対比結果で参照された段落・表の生本文を引用する。"""
    if not citations_meta:
        return ""

    lines = ["## 引用文献の証拠（本文・表からの抽出）"]

    for doc_id, resp in responses.items():
        cit = citations_meta.get(doc_id) or citations_meta.get(str(doc_id)) or {}
        if not cit:
            continue

        lines.append(f"\n### {doc_id}")

        problem_paras = [
            p for p in cit.get("paragraphs", []) or []
            if _section_matches(p, "課題", "効果", "目的")
        ]
        if problem_paras:
            lines.append("#### 引例の課題・効果")
            lines.extend(_format_para(p) for p in problem_paras[:4])

        tables = cit.get("tables", []) or []
        if tables:
            lines.append("#### 引例の実施例表・数値データ")
            for t in tables[:3]:
                tid = _norm_id(t.get("id") or t.get("table_number") or "?")
                lines.append(f"**表{tid}**")
                lines.append(_clip(_table_text(t), MAX_PER_TABLE_CHARS))

        para_lookup = _para_map(cit)
        table_lookup = _table_map(cit)
        evidence_lines = []
        for comp in resp.get("comparisons", []) or []:
            if comp.get("judgment") not in ("△", "×"):
                continue
            req_id = comp.get("requirement_id", "?")
            loc = comp.get("cited_location", "")
            for pid in _extract_para_ids(loc):
                para = para_lookup.get(pid)
                if para:
                    evidence_lines.append(f"**{req_id} ←【{_norm_id(para.get('id'))}】**: {_clip(para.get('text'), MAX_PER_PARA_CHARS)}")
            for tid in _extract_table_ids(loc):
                table = table_lookup.get(tid) or table_lookup.get(f"T{tid}")
                if table:
                    evidence_lines.append(f"**{req_id} ← 表{tid}**:\n{_clip(_table_text(table), MAX_PER_TABLE_CHARS)}")

        if evidence_lines:
            lines.append("#### 対比で参照された段落・表（△・×の根拠）")
            lines.extend(evidence_lines)

    text = "\n".join(lines)
    if text.strip() == "## 引用文献の証拠（本文・表からの抽出）":
        return ""
    return _clip(text, MAX_CITATION_EVIDENCE_CHARS)


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
- 詳細な効果論は次の専用セクションに従って検討
- 明細書の主張をそのまま受け入れず、実施例・比較例データによる裏付けを確認
- 数値範囲発明では、臨界性の有無を必ず判断

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


def _build_effect_analysis_instructions():
    return """## Step 4: 有利な効果の詳細検討（重点ステップ）

本願明細書および引用文献の実施例・数値データに基づき、以下を必ず検討すること。
**「効果が記載されているから進歩性あり」のような表面的判断は禁止**。

### 4-1. 本願が主張する効果の特定
- 明細書のどの段落に効果が記載されているか
- 効果は定量的か（数値で示されているか）、定性的か
- 効果の測定条件・評価方法

### 4-2. 効果の臨界性（数値範囲を伴う発明の場合）
本願が数値範囲を限定している場合:
- 範囲内（実施例）と範囲外（比較例）で効果に有意差があるか
- 範囲の上端・下端で効果がどう変化するか
- **臨界的意義の主張があるなら、明細書の比較例データで裏付けされているか**
- 比較例が無い、又は範囲外でも同様の効果が出ているなら **臨界性なし**

### 4-3. 引例から予測可能か
- 引例の実施例で同じ物性が測定されているか
- 引例の数値範囲が本願範囲をカバー、又は隣接しているか
- 引例の効果と本願の効果が同質か異質か
- 同質なら、本願の効果は引例から外挿で予測できる程度か、それとも顕著か

### 4-4. 効果の認定の結論
以下のいずれかを明示的に選択:
- (a) 異質な効果あり（引例から予測不可能）→ 進歩性肯定要素
- (b) 同質だが顕著な効果あり（数値で技術水準を超える）→ 進歩性肯定要素
- (c) 同質で予測可能な効果のみ → 進歩性に寄与しない
- (d) **特別な効果なし**（明細書に記載があっても、引例と差が無い、又は比較例による裏付けがない）→ 進歩性否定方向
- (e) 効果について判断する材料が不足

(d) を選択する場合は、**なぜ「特別な効果なし」と判断したか** を以下の観点で必ず述べる:
- 比較例の不在
- 数値範囲の臨界性の裏付け不足
- 引例にも同等の数値・効果が記載されている
- 効果の主張が定性的すぎて検証不能"""


def _build_output_format(responses):
    doc_ids = list(responses.keys())

    return f"""## 出力フォーマット
以下のJSON形式で回答してください。

```json
{{
    "deliberation": "公開可能な検討要旨（500字以上。主引例選定、効果論で迷った点、特別な効果なし/効果ありの根拠を含む）",
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
        "is_critical_range_supported": true | false,
        "comparative_examples_present": true | false,
        "effect_classification": "(a)異質" | "(b)顕著" | "(c)予測可能" | "(d)特別な効果なし" | "(e)判断材料不足",
        "effect_classification_reason": "上記分類を選んだ理由。特に(d)の場合は比較例の不在、臨界性不足、引例との同等性、定性的主張の限界を具体的に述べる",
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

    def _normalize(data):
        if not isinstance(data, dict):
            return data
        data.setdefault("deliberation", None)
        ae = data.setdefault("advantageous_effects", {})
        if isinstance(ae, dict):
            ae.setdefault("is_critical_range_supported", None)
            ae.setdefault("comparative_examples_present", None)
            ae.setdefault("effect_classification", None)
            ae.setdefault("effect_classification_reason", None)
        return data

    # JSON抽出
    json_block = re.compile(r'```(?:json)?\s*\n?(.*?)\n?\s*```', re.DOTALL)
    matches = json_block.findall(raw_text)
    for match in matches:
        try:
            data = json.loads(match)
            if isinstance(data, dict) and "overall_assessment" in data:
                return _normalize(data), []
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
                        return _normalize(data), []
                except json.JSONDecodeError:
                    start = None

    return None, ["進歩性分析のJSONを抽出できませんでした。"]
