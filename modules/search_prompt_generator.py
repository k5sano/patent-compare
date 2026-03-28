#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
先行技術検索プロンプト生成モジュール

入力:
- segments: 請求項分節 (segments.json)
- keywords: キーワードグループ (keywords.json)
- field: 分野

出力:
- Claudeに投げる先行技術検索プロンプト
- Claudeの回答からの候補パース
"""

import re
import json


def generate_search_prompt(segments, keywords, field="cosmetics", case_meta=None):
    """分節+キーワードから先行技術検索プロンプトを生成

    Parameters:
        segments: 請求項分節データ (segments.json)
        keywords: キーワードグループ (keywords.json)
        field: "cosmetics" | "laminate"
        case_meta: 案件メタ情報 (case.yaml, optional)

    Returns:
        プロンプト文字列
    """
    sections = [
        _build_search_task(field),
        _build_application_info(case_meta),
        _build_claim_segments(segments),
        _build_keyword_groups(keywords),
        _build_fterm_info(keywords),
        _build_search_instructions(field),
        _build_output_format(),
    ]

    return "\n\n---\n\n".join(s for s in sections if s.strip())


def _build_search_task(field):
    field_label = {"cosmetics": "化粧品", "laminate": "積層体"}.get(field, field)
    return f"""## タスク
あなたは{field_label}分野の特許調査の専門家です。
以下の請求項の構成要件に対し、先行技術候補となる特許文献を検索・提案してください。

各構成要件をカバーしうる公知文献を幅広く検索し、進歩性の拒絶理由通知を構成するのに適した
主引例候補・副引例候補・技術常識を示す文献をリストアップしてください。"""


def _build_application_info(case_meta):
    """出願情報セクションを構築"""
    if not case_meta:
        return ""

    lines = ["## 本願の出願情報"]
    patent_number = case_meta.get("patent_number", "")
    patent_title = case_meta.get("patent_title", "")
    case_id = case_meta.get("case_id", "")
    field = case_meta.get("field", "")
    field_label = {"cosmetics": "化粧品", "laminate": "積層体"}.get(field, field)

    if case_id:
        lines.append(f"- 公開番号: JP{case_id}")
    if patent_number:
        lines.append(f"- 特許番号: {patent_number}")
    if patent_title:
        lines.append(f"- 発明の名称: {patent_title}")
    if field_label:
        lines.append(f"- 技術分野: {field_label}")

    return "\n".join(lines) if len(lines) > 1 else ""


def _build_fterm_info(keywords):
    """キーワードグループからFターム情報を抽出して出力"""
    if not keywords:
        return ""

    fterms = []
    for group in keywords:
        codes = group.get("search_codes", {})
        if codes:
            for code_type, values in codes.items():
                if isinstance(values, list):
                    fterms.extend(values)
                elif isinstance(values, str) and values:
                    fterms.append(values)

    if not fterms:
        return ""

    lines = ["## Fターム・IPC情報"]
    lines.append("以下の分類コードも検索の参考にしてください。")
    for ft in fterms:
        lines.append(f"- {ft}")
    return "\n".join(lines)


def _build_claim_segments(segments):
    lines = ["## 本願の請求項 構成要件"]
    for claim in segments:
        claim_num = claim["claim_number"]
        dep_type = ("独立" if claim["is_independent"]
                    else f"従属（→請求項{','.join(map(str, claim['dependencies']))}）")
        lines.append(f"\n### 請求項{claim_num}（{dep_type}）")
        for seg in claim["segments"]:
            lines.append(f"- **{seg['id']}**: {seg['text']}")
    return "\n".join(lines)


def _build_keyword_groups(keywords):
    if not keywords:
        return ""

    lines = ["## キーワードグループ"]
    lines.append("以下は各構成要件に対応するキーワードです。検索の参考にしてください。")
    for group in keywords:
        seg_str = ", ".join(group.get("segment_ids", []))
        lines.append(f"\n### グループ{group['group_id']}: {group['label']}（分節: {seg_str}）")
        for kw in group.get("keywords", []):
            lines.append(f"- {kw['term']}（{kw.get('type', '')}）")
    return "\n".join(lines)


def _build_search_instructions(field):
    lines = ["""## 検索指針

### 候補文献の選定基準
1. **主引例候補（1～3件）**: 請求項1の構成要件を最も多くカバーする文献。理想的には独立請求項の70%以上の構成要件に対応。
2. **副引例候補（2～5件）**: 主引例でカバーされない構成要件（×判定となりうるもの）を補完する文献。
3. **技術常識文献（1～3件）**: 当該分野で周知・慣用の技術であることを示す文献（教科書的特許、総説特許）。

### 候補の多様性
- 同一出願人の文献に偏らないこと
- 出願日が本願より前の文献を優先（ただし技術常識は時期を問わない）
- 日本特許・米国特許・WO出願を幅広く含めること

### 特許番号のフォーマット
- 日本: JP + 公開番号（例: JPH05-123456, JP2024-037328）
- 米国: US + 番号（例: US5286475, US2020/0123456A1）
- WO: WO + 番号（例: WO2003/012345A1）
- 欧州: EP + 番号（例: EP1234567A1）"""]

    if field == "cosmetics":
        lines.append("""
### 化粧品分野の留意点
- 成分名の表記ゆれ（INCI名、和名、商品名）に注意して幅広く検索
- 配合量の数値範囲が重複する文献を優先
- エアゾール、乳化、粉体分散等の剤型に関する文献も考慮""")
    elif field == "laminate":
        lines.append("""
### 積層体分野の留意点
- 層構成（層数、材料、厚さ）が類似する文献を優先
- フィルム製法（延伸、蒸着、コーティング）に関する文献も考慮
- 用途（包装、光学、電子材料等）の類似性も考慮""")

    return "\n".join(lines)


def _build_output_format():
    return """## 出力フォーマット
以下のJSON配列で回答してください。5〜15件程度の候補を提案してください。

```json
[
  {
    "patent_id": "US5286475",
    "title": "Anhydrous cosmetic composition in aerosol form",
    "applicant": "L'Oreal",
    "year": 1994,
    "relevance": "主引例候補",
    "relevant_segments": ["1A", "1B", "1C"],
    "reason": "油状泡沫性エアゾール化粧料に関する発明であり、本願の構成要件1A〜1Cに相当する構成を開示している。",
    "confidence": "high",
    "google_patents_url": "https://patents.google.com/patent/US5286475/en"
  },
  {
    "patent_id": "JP2020-123456",
    "title": "化粧料組成物",
    "applicant": "株式会社○○",
    "year": 2020,
    "relevance": "副引例候補",
    "relevant_segments": ["1G", "1H"],
    "reason": "ポリアルキレングリコールエーテルを含有する化粧料組成物に関し...",
    "confidence": "medium",
    "google_patents_url": "https://patents.google.com/patent/JP2020123456A/ja"
  }
]
```

### 各フィールドの説明
- **patent_id**: 特許番号（公開番号 or 登録番号）
- **title**: 発明の名称
- **applicant**: 出願人
- **year**: 公開年
- **relevance**: 「主引例候補」「副引例候補」「技術常識」のいずれか
- **relevant_segments**: 関連する構成要件のID（例: ["1A", "1B"]）
- **reason**: この文献が候補となる理由（2〜3文で）
- **confidence**: 候補としての確信度。`"high"` = 実在を確認済み・内容の関連性が高い、`"medium"` = 番号は確からしいが内容未確認、`"low"` = 記憶に基づく推測
- **google_patents_url**: Google PatentsのURL。以下の形式:
  - US特許: `https://patents.google.com/patent/US{番号}/en`
  - JP特許: `https://patents.google.com/patent/JP{番号}A/ja`（公開）or `JP{番号}B2/ja`（登録）
  - WO: `https://patents.google.com/patent/WO{番号}A1/en`
  - EP: `https://patents.google.com/patent/EP{番号}A1/en`

### 注意事項
- patent_idには実在する特許番号を使用してください
- google_patents_urlは `https://patents.google.com/patent/` で始まる正しい形式にしてください
- 主引例候補は請求項1の構成要件の多くをカバーする文献を選んでください
- 副引例は主引例でカバーされない構成要件を補う文献を選んでください"""


def parse_search_response(raw_text):
    """Claudeの回答からJSON配列を抽出・バリデーション

    Parameters:
        raw_text: Claudeの回答テキスト

    Returns:
        (candidates, errors)
        - candidates: 候補リスト（パース成功時）or None
        - errors: エラーメッセージのリスト
    """
    data = _extract_json_array(raw_text)

    if data is None:
        return None, ["JSON配列を抽出できませんでした。Claudeの回答にJSON形式の候補リストが含まれていることを確認してください。"]

    errors = []
    validated = []

    for i, item in enumerate(data):
        item_errors = _validate_candidate(item, i)
        errors.extend(item_errors)

        # 必須フィールドを補完
        candidate = {
            "patent_id": item.get("patent_id", f"UNKNOWN-{i+1}"),
            "title": item.get("title", ""),
            "applicant": item.get("applicant", ""),
            "year": item.get("year"),
            "relevance": item.get("relevance", "副引例候補"),
            "relevant_segments": item.get("relevant_segments", []),
            "reason": item.get("reason", ""),
            "google_patents_url": item.get("google_patents_url", ""),
            "status": "pending",
        }

        # google_patents_url が無ければ生成
        if not candidate["google_patents_url"]:
            candidate["google_patents_url"] = _build_google_patents_url(candidate["patent_id"])

        # J-PlatPatリンクをJP特許に対して生成
        if _is_jp_patent(candidate["patent_id"]):
            candidate["jplatpat_url"] = _build_jplatpat_url(candidate["patent_id"])

        validated.append(candidate)

    return validated, errors


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


def _validate_candidate(item, index):
    """候補アイテムのバリデーション"""
    errors = []
    if not isinstance(item, dict):
        errors.append(f"候補{index+1}: オブジェクトではありません")
        return errors

    if not item.get("patent_id"):
        errors.append(f"候補{index+1}: patent_id がありません")

    valid_relevance = {"主引例候補", "副引例候補", "技術常識"}
    rel = item.get("relevance", "")
    if rel and rel not in valid_relevance:
        errors.append(f"候補{index+1} ({item.get('patent_id', '?')}): relevance '{rel}' は無効です")

    return errors


def _build_google_patents_url(patent_id):
    """特許番号からGoogle Patents URLを構築"""
    cleaned = re.sub(r'[\s\-/]', '', patent_id)

    if cleaned.upper().startswith("US"):
        return f"https://patents.google.com/patent/{cleaned}/en"
    elif cleaned.upper().startswith("WO"):
        return f"https://patents.google.com/patent/{cleaned}/en"
    elif cleaned.upper().startswith("EP"):
        return f"https://patents.google.com/patent/{cleaned}/en"
    elif cleaned.upper().startswith("JP"):
        return f"https://patents.google.com/patent/{cleaned}/ja"
    else:
        return f"https://patents.google.com/patent/{cleaned}/en"


def _is_jp_patent(patent_id):
    """日本特許かどうか判定"""
    cleaned = patent_id.strip().upper()
    return (cleaned.startswith("JP") or
            cleaned.startswith("特開") or
            cleaned.startswith("特許"))


def _build_jplatpat_url(patent_id):
    """日本特許のJ-PlatPat固定URLを構築

    参照: pe-sawaki.com/tool/jppl
    形式: https://www.j-platpat.inpit.go.jp/c1801/PU/JP-{番号}/{種別}/ja
    種別: 10=公開特許, 11=公表特許, 15=登録特許
    """
    cleaned = re.sub(r'[\s]', '', patent_id)

    # 「特開YYYY-NNNNNN」形式
    m = re.match(r'特開(\d{4})[−\-](\d+)', cleaned)
    if m:
        return f"https://www.j-platpat.inpit.go.jp/c1801/PU/JP-{m.group(1)}-{m.group(2)}/10/ja"

    # 「JPYYYY-NNNNNN」形式
    m = re.match(r'JP[−\-]?(\d{4})[−\-](\d+)', cleaned, re.IGNORECASE)
    if m:
        return f"https://www.j-platpat.inpit.go.jp/c1801/PU/JP-{m.group(1)}-{m.group(2)}/10/ja"

    # 「JPH05-NNNNNN」等の昭和・平成・令和形式
    m = re.match(r'JP([HS])(\d{2})[−\-](\d+)', cleaned, re.IGNORECASE)
    if m:
        era = m.group(1).upper()
        num = m.group(2)
        serial = m.group(3)
        return f"https://www.j-platpat.inpit.go.jp/c1801/PU/JP-{era}{num}-{serial}/10/ja"

    # フォールバック: そのまま使う
    num_part = re.sub(r'^JP[−\-]?', '', cleaned, flags=re.IGNORECASE)
    return f"https://www.j-platpat.inpit.go.jp/c1801/PU/JP-{num_part}/10/ja"
