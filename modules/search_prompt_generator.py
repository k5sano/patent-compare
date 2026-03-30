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

    jp_id = case_meta.get("jp_id", "")
    case_id = case_meta.get("case_id", "")
    patent_number = case_meta.get("patent_number", "")
    patent_title = case_meta.get("patent_title", "")
    field = case_meta.get("field", "")
    field_label = {"cosmetics": "化粧品", "laminate": "積層体"}.get(field, field)

    if jp_id:
        lines.append(f"- 公開番号: {jp_id}")
    elif case_id:
        # case_id が "YYYY-NNNNNN" 形式なら公開番号として整形
        m = re.match(r'^(\d{4})-(\d+)$', case_id)
        if m:
            lines.append(f"- 公開番号: JP{m.group(1)}-{m.group(2)}")
        else:
            lines.append(f"- 案件ID: {case_id}")

    if patent_number:
        lines.append(f"- 特許番号: {patent_number}")
    if patent_title:
        lines.append(f"- 発明の名称: {patent_title}")
    if field_label:
        lines.append(f"- 技術分野: {field_label}")

    # 出願日・優先日があれば表示（先行技術の時期判断に重要）
    filing_date = case_meta.get("filing_date", "")
    priority_date = case_meta.get("priority_date", "")
    if filing_date:
        lines.append(f"- 出願日: {filing_date}")
    if priority_date:
        lines.append(f"- 優先日: {priority_date}")

    lines.append("")
    lines.append("※先行技術候補は上記出願日（または優先日）より前に公開された文献を優先してください。")

    return "\n".join(lines) if len(lines) > 2 else ""


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

### 候補の選定優先順位
- **本願出願人自身の先行特許を最優先で検索すること**（自社の先行出願は最も関連性が高い引例になりやすい）
- 出願日が本願より前の文献を優先（ただし技術常識は時期を問わない）
- 日本特許・米国特許・WO出願を幅広く含めること
- 出願人の文献だけでなく、他社の文献も含めて多様性を確保すること

### 引用文献チェーン（拒絶理由引例の活用）
- **本願自身が引用している文献**（明細書の背景技術・引用文献リスト）は、Y文献（進歩性否定の組合せ引例）となる可能性が高いため、必ず候補として検討すること
- 一致度の高い有望文献が見つかったら、その文献の**拒絶理由通知で引用された文献**（被引用文献）も候補として検討すること
- 特にカテゴリX（新規性否定）・Y（進歩性否定の組合せ引例）として引用された文献は、本願に対しても有力な引例となりやすい
- Google Patentsの"Cited by"や"Similar documents"も参照し、関連文献を芋づる式に辿ること

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


# ================================================================
# 3段階検索プロンプト生成
# ================================================================


def _extract_description_summary(hongan, max_chars=3000):
    """明細書の要約を抽出（実施例・定義・課題・効果を優先）"""
    if not hongan:
        return ""
    priority = ["課題", "手段", "効果", "実施形態", "実施例"]
    lines = []
    total = 0
    for section_name in priority:
        for para in hongan.get("paragraphs", []):
            if para.get("section") == section_name:
                text = f"【{para['id']}】{para['text']}"
                if total + len(text) > max_chars:
                    break
                lines.append(text)
                total += len(text)
    return "\n".join(lines)


def _build_segments_text(segments):
    """分節リストをテキストに変換"""
    lines = []
    for claim in segments:
        is_indep = claim.get("is_independent", claim.get("claim_number") == 1)
        if not is_indep:
            continue
        for seg in claim.get("segments", []):
            lines.append(f'{seg["id"]}: {seg["text"]}')
    return "\n".join(lines)


def _build_keywords_text(keywords):
    """キーワードグループをテキストに変換"""
    if not keywords:
        return "（キーワード未設定）"
    lines = []
    for g in keywords:
        terms = [kw.get("term", "") for kw in g.get("keywords", [])[:10]]
        seg_ids = ", ".join(g.get("segment_ids", []))
        lines.append(f'グループ{g.get("group_id", "?")}: [{seg_ids}] {", ".join(terms)}')
        if g.get("search_codes", {}).get("fterm"):
            fterms = [ft["code"] for ft in g["search_codes"]["fterm"][:5]]
            lines.append(f'  Fterm: {", ".join(fterms)}')
    return "\n".join(lines)


# ---- Stage 1: 予備検索 ----

_QUERY_STOP_WORDS = {
    "前記", "含有", "含有する", "からなる", "有する", "備える",
    "において", "であって", "であり", "おける", "よる", "する",
    "された", "される", "および", "ならびに", "または", "もしくは",
    "以上", "以下", "未満", "超える", "含む", "特徴", "記載",
}


def _build_recommended_queries(segments, keywords, field, case_meta=None):
    """presearchプロンプト用の推奨検索クエリを生成

    キーワードグループと分節テキストから、Claudeがツール検索に使うべき
    クエリのヒントを生成する。
    """
    queries = []

    # 0. 出願人名での検索（自社先行出願は最重要引例）
    if case_meta:
        applicant = case_meta.get("applicant", "")
        if applicant:
            # 出願人名 + 技術分野キーワードで検索
            field_kw = {"cosmetics": "化粧", "laminate": "積層"}.get(field, "")
            if field_kw:
                queries.append(f"{applicant} {field_kw}")
            else:
                queries.append(applicant)

    # 1. キーワードグループごとの検索式（日本語）
    if keywords and isinstance(keywords, list):
        for group in keywords:
            parts = []
            for kw in group.get("keywords", [])[:4]:
                term = kw.get("term", "") if isinstance(kw, dict) else str(kw)
                if term and len(term) >= 2 and term not in _QUERY_STOP_WORDS:
                    parts.append(term)
            if parts:
                queries.append(" ".join(parts))

    # 2. 分節テキストから名詞句を抽出してフォールバック検索式を作成
    if len(queries) < 2:
        for claim in segments:
            if claim.get("claim_number") != 1:
                continue
            seg_terms = []
            for seg in claim.get("segments", []):
                text = seg.get("text", "")
                words = re.findall(
                    r'[ァ-ヴー]{3,}|[一-龥]{2,}(?:剤|物|体|油|酸|液|層|膜|比|料)?',
                    text,
                )
                for w in words:
                    if w not in _QUERY_STOP_WORDS and len(w) >= 2:
                        seg_terms.append(w)
            if seg_terms:
                queries.append(" ".join(list(dict.fromkeys(seg_terms))[:5]))
            break

    # 3. 英語クエリを追加（分野に基づく簡易生成）
    if keywords and isinstance(keywords, list):
        en_parts = []
        for group in keywords:
            for kw in group.get("keywords", [])[:2]:
                if isinstance(kw, dict):
                    term = kw.get("term", "")
                    kw_type = kw.get("type", "")
                    if "EN" in kw_type.upper() or "INCI" in kw_type.upper():
                        en_parts.append(term)
        if en_parts:
            queries.append(" ".join(en_parts[:5]))

    # 重複除去して最大5式
    return list(dict.fromkeys(queries))[:5]


def generate_presearch_prompt(segments, hongan, keywords, field="cosmetics", case_meta=None):
    """Stage 1: 予備検索（プレサーチ）プロンプトを生成

    入力の自然文（請求項分節＋明細書要約）から、
    技術構造化→多言語同義語展開→検索計画を行うプロンプトを生成。

    Returns:
        プロンプト文字列
    """
    field_label = {"cosmetics": "化粧品", "laminate": "積層体"}.get(field, field)
    segments_text = _build_segments_text(segments)
    description = _extract_description_summary(hongan, max_chars=3000)
    keywords_text = _build_keywords_text(keywords)

    # 出願情報
    app_info = ""
    if case_meta:
        parts = []
        if case_meta.get("jp_id"):
            parts.append(f"出願番号: {case_meta['jp_id']}")
        if case_meta.get("patent_title"):
            parts.append(f"発明の名称: {case_meta['patent_title']}")
        if parts:
            app_info = "\n".join(parts)

    field_specific = ""
    if field == "cosmetics":
        field_specific = """
- 化粧品分野では、成分名に複数の表記があります（和名/化学名/INCI名/商品名）。すべて列挙してください。
- Ftermテーマ: 4C083（化粧料）
- 成分のFterm分類（AC:有機成分, BB:機能特定成分）も参考にしてください。"""
    elif field == "laminate":
        field_specific = """
- 積層体分野では、樹脂略称（PE, PP, PET等）と正式名称の両方を列挙してください。
- Ftermテーマ: 4F100（積層体）
- 層構成（基材層/バリア層/シーラント層等）の同義語も含めてください。"""

    # 推奨検索クエリを生成
    recommended_queries = _build_recommended_queries(segments, keywords, field, case_meta=case_meta)
    recommended_queries_section = ""
    if recommended_queries:
        lines = ["### 推奨検索クエリ（以下のクエリでツール検索を実行してください）"]
        for i, q in enumerate(recommended_queries, 1):
            lines.append(f"- 検索{i}: \"{q}\"")
        recommended_queries_section = "\n".join(lines)

    return f"""## 役割
あなたは{field_label}分野の特許調査に精通したリサーチエージェントです。
以下の特許出願の技術内容を分析し、先行技術の予備検索（プレサーチ）を行ってください。

## 重要: 検索ツールの使用

あなたは以下の検索ツールを利用できます。候補文献の提示前に**必ず**検索を実行してください：
- `mcp__patent-search__search_patents_google` — Google Patents 国際検索
- `mcp__patent-search__search_patents_google_jp` — Google Patents 日本特許限定検索
- `mcp__patent-search__search_patents_google_scholar` — Google Scholar 学術文献検索

手順:
1. まず技術構造化（Stage 1-2）を行う
2. 構造化した要素ごとに検索クエリを作成し、上記ツールで検索を実行
3. 検索結果に基づいて候補文献を提示（訓練データからの推測のみに頼らない）
4. 検索で見つからなかった候補は confidence: "low (未検証)" と明記

{recommended_queries_section}

## タスク（以下の順序で実行）

### 1. 技術の構造化
入力文から「要素・属性・関係・作用効果（What/How/Why）」に分解してください。
- 1行の技術コア文（core_sentence）を作成
- What（何の技術か）/ How（どうやって実現するか）/ Why（なぜ必要か・課題・効果）を整理
- 技術要素をA_～F_のタグで分類（プレフィックスは統一、命名は技術内容に応じて動的に決定）
  例: A_target（対象・用途）, B_ingredients（成分・材料）, C_structure（構成・構造）,
      D_action（作用・機能）, E_effect（効果）, F_stabilization（安定化・補助手段）
- 各要素に該当する分節IDを紐付け

### 2. 多言語同義語展開
各要素の用語について、以下の4言語で網羅的に列挙してください：
- **JP**: 特許公報の表記、一般名、化学名、商品名、上位概念、下位概念
- **EN**: INCI名、学術用語、米国特許の表記
- **ZH**: 中国語特許で使用される表記
- **KR**: 韓国語特許で使用される表記
{field_specific}
各要素ごとにNOT語（ノイズ除外語）も特定してください。

### 3. 先行技術候補の提示
以下のカテゴリから候補を提示してください：
- **特許**（JP/US/WO/EP/CN/KR）: 5件程度
- **論文・規格**: 3件程度（あれば）
- **製品・Web・動画**: 2件程度（あれば）

**重要: 本願出願人自身の先行特許を最優先で検索してください。** 自社の先行出願は最も関連性の高い引例になりやすいです。出願人名での検索を必ず行ってください。

**本願の引用文献:** 本願の明細書に記載された引用文献（背景技術・引用文献リスト）はY文献（進歩性否定の組合せ引例）となる可能性が高いため、必ず候補として検討してください。

**引用文献チェーン:** 一致度の高い有望文献が見つかったら、その文献の拒絶理由通知で引用された文献（X/Yカテゴリ）も候補として追加してください。Google Patentsの"Cited by"や類似文献も参照し、関連文献を芋づる式に辿ってください。

各候補には以下を付与：
- ★一致度（1-5、5が最も関連性が高い）
- key_terms_found（その文献で実際に使われているキーワード）
- classifications_found（付与されているFI/CPC/IPC）

### 4. 検索式の提示
- **J-PlatPat式**: AND=*, OR=+, NOT=-, 近接=20N, CL/TX指定必須, 500字以内
- **Google Patents英語式**: AND/OR/NOT, ワイルドカード*使用
- **候補分類コード**: FI, Fterm, CPC

## 出力フォーマット
以下のJSON形式で出力してください（Markdownテーブルではなく、純粋なJSON）：

```json
{{
  "tech_analysis": {{
    "core_sentence": "技術の一文要約",
    "what_how_why": {{
      "what": "何の技術か",
      "how": "どうやって実現するか",
      "why": "なぜ必要か（課題/効果）"
    }},
    "elements": {{
      "A_xxx": {{
        "label": "要素の日本語ラベル",
        "description": "この要素の説明",
        "terms_ja": ["日本語用語1", "日本語用語2"],
        "terms_en": ["English term1", "English term2"],
        "segment_ids": ["1A", "1B"]
      }}
    }},
    "multilingual_synonyms": {{
      "用語1": {{
        "ja": ["同義語JA1", "同義語JA2"],
        "en": ["synonym_EN1"],
        "zh": ["中文同义词"],
        "kr": ["한국어"]
      }}
    }}
  }},
  "candidates": [
    {{
      "patent_id": "JP2020-XXXXXX",
      "title": "発明の名称",
      "applicant": "出願人",
      "year": 2020,
      "relevance": "主引例候補",
      "relevant_segments": ["1A", "1B"],
      "reason": "関連する理由",
      "confidence": "high",
      "relevance_score": 5,
      "source_type": "patent",
      "key_terms_found": ["キーワード1", "キーワード2"],
      "classifications_found": {{
        "fi": ["FIコード"],
        "cpc": ["CPCコード"],
        "ipc": ["IPCコード"]
      }}
    }}
  ],
  "search_formulas": {{
    "jplatpat_narrow": "J-PlatPat用の狭い検索式（500字以内）",
    "jplatpat_wide": "J-PlatPat用の広い検索式",
    "google_patents": "Google Patents用の英語検索式",
    "candidate_fi": ["FIコード候補"],
    "candidate_fterm": ["Ftermコード候補"],
    "candidate_cpc": ["CPCコード候補"]
  }},
  "insights": ["調査における洞察1", "注意点2"]
}}
```

## 入力情報

### 出願情報
{app_info or "（未設定）"}

### 分野
{field_label}

### 請求項分節
{segments_text}

### 明細書要約
{description or "（明細書データなし）"}

### キーワードグループ
{keywords_text}
"""


def parse_presearch_response(raw_text):
    """Stage 1の回答をパースし、tech_analysis と candidates に分離

    Returns:
        (tech_analysis: dict, candidates: list, search_formulas: dict, errors: list)
    """
    errors = []

    # JSONを抽出（{ で始まるオブジェクト）
    data = _extract_json_object(raw_text)

    if not data:
        return None, None, None, ["JSONの抽出に失敗しました"]

    tech_analysis = data.get("tech_analysis")
    if not tech_analysis:
        errors.append("tech_analysis セクションが見つかりません")

    candidates_raw = data.get("candidates", [])
    candidates = []
    for i, item in enumerate(candidates_raw):
        item_errors = []
        patent_id = item.get("patent_id", "")
        if not patent_id:
            item_errors.append(f"候補{i+1}: patent_id が未設定")
            continue
        # google_patents_url 自動生成
        if not item.get("google_patents_url"):
            item["google_patents_url"] = _build_google_patents_url(patent_id)
        # jplatpat_url 自動生成（JP特許の場合）
        if _is_jp_patent(patent_id) and not item.get("jplatpat_url"):
            item["jplatpat_url"] = _build_jplatpat_url(patent_id)
        if item_errors:
            errors.extend(item_errors)
        else:
            candidates.append(item)

    search_formulas = data.get("search_formulas")
    if not search_formulas:
        errors.append("search_formulas セクションが見つかりません")

    return tech_analysis, candidates, search_formulas, errors


def _extract_json_object(raw_text):
    """テキストからJSONオブジェクト（{...}）を抽出"""
    # パターン1: ```json ... ``` ブロック
    json_block = re.compile(r'```(?:json)?\s*\n?(.*?)\n?\s*```', re.DOTALL)
    for match in json_block.findall(raw_text):
        try:
            data = json.loads(match)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue

    # パターン2: 最外側の { ... } を探す
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
                candidate = raw_text[start:i + 1]
                try:
                    data = json.loads(candidate)
                    if isinstance(data, dict):
                        return data
                except json.JSONDecodeError:
                    start = None
                    continue
    return None


# ---- Stage 2: 分類特定 ----

def generate_classification_prompt(segments, hongan, field, tech_analysis, presearch_candidates):
    """Stage 2: 分類特定プロンプトを生成

    Stage 1 の結果（tech_analysis + candidates）を前提に、
    FI/Fターム/CPC/IPCを網羅的に特定するプロンプト。
    """
    field_label = {"cosmetics": "化粧品", "laminate": "積層体"}.get(field, field)
    segments_text = _build_segments_text(segments)

    # tech_analysis から要素一覧を構築
    elements_text = ""
    if tech_analysis and tech_analysis.get("elements"):
        lines = []
        for key, elem in tech_analysis["elements"].items():
            label = elem.get("label", key)
            terms = ", ".join(elem.get("terms_ja", [])[:5])
            seg_ids = ", ".join(elem.get("segment_ids", []))
            lines.append(f"- {key} ({label}): {terms} [分節: {seg_ids}]")
        elements_text = "\n".join(lines)

    # 代表文献の情報
    candidates_text = ""
    if presearch_candidates:
        lines = []
        for c in presearch_candidates[:5]:
            pid = c.get("patent_id", "?")
            title = c.get("title", "")
            cls = c.get("classifications_found", {})
            fi_str = ", ".join(cls.get("fi", []))
            cpc_str = ", ".join(cls.get("cpc", []))
            lines.append(f"- {pid}: {title}")
            if fi_str:
                lines.append(f"  FI: {fi_str}")
            if cpc_str:
                lines.append(f"  CPC: {cpc_str}")
        candidates_text = "\n".join(lines)

    field_specific = ""
    if field == "cosmetics":
        field_specific = "- Ftermテーマコード: 4C083（化粧料）のAA～FF分類を特に精査してください"
    elif field == "laminate":
        field_specific = "- Ftermテーマコード: 4F100（積層体）のAK～YY分類を特に精査してください"

    return f"""## 役割
あなたは{field_label}分野の特許分類の専門家です。
予備検索の結果を踏まえて、FI/Fターム/CPC/IPCを網羅的に特定してください。

## 重要な制約
- **分類コードを創作しないこと**。実在しないコードを作らず、特定困難なら明記してください。
- 代表文献（下記の候補上位5件）に**実際に付与された分類**を確認して回答してください。
- **信頼度**（high/medium/low）を各コードに付けてください。
  - high: 代表文献に実際に付与されている
  - medium: 分類体系上該当するが代表文献では未確認
  - low: 該当する可能性はあるが不確実
- **技術要素（A_～F_）ごと**に対応する分類を示してください。
- 不確実な分類には note で確認方法を記載してください。
{field_specific}

## 出力フォーマット
以下のJSON形式で出力してください：

```json
{{
  "fi": [
    {{
      "code": "FIコード",
      "type": "main|sub",
      "label": "日本語ラベル",
      "confidence": "high|medium|low",
      "elements": ["A_xxx", "B_xxx"],
      "source_pubs": ["JP2020XXXXXXA"],
      "note": "備考"
    }}
  ],
  "fterm": [
    {{
      "code": "Ftermコード",
      "type": "core|related",
      "label": "日本語ラベル",
      "confidence": "high|medium|low",
      "elements": ["A_xxx"],
      "source_pubs": ["JP2020XXXXXXA"],
      "note": "備考"
    }}
  ],
  "cpc": [
    {{
      "code": "CPCコード",
      "label": "英語ラベル",
      "confidence": "high|medium|low",
      "elements": ["A_xxx"],
      "source_pubs": [],
      "note": ""
    }}
  ],
  "ipc": [
    {{
      "code": "IPCコード",
      "label": "ラベル",
      "confidence": "high|medium|low",
      "elements": ["A_xxx"],
      "source_pubs": [],
      "note": ""
    }}
  ],
  "element_classification_map": {{
    "A_xxx": {{"fi": ["..."], "fterm": ["..."], "cpc": ["..."]}},
    "B_xxx": {{"fi": ["..."], "fterm": ["..."]}}
  }}
}}
```

## 入力情報

### 技術要素（Stage 1の結果）
{elements_text or "（未設定）"}

### コア文
{tech_analysis.get("core_sentence", "") if tech_analysis else ""}

### 代表文献
{candidates_text or "（候補なし）"}

### 請求項分節
{segments_text}
"""


def parse_classification_response(raw_text):
    """Stage 2の回答をパース

    Returns:
        (classification: dict, errors: list)
    """
    errors = []
    data = _extract_json_object(raw_text)

    if not data:
        return None, ["JSONの抽出に失敗しました"]

    # 必須フィールドチェック
    for key in ["fi", "fterm"]:
        if key not in data:
            errors.append(f"{key} セクションが見つかりません")

    # オプションフィールドのデフォルト値
    data.setdefault("cpc", [])
    data.setdefault("ipc", [])
    data.setdefault("element_classification_map", {})

    return data, errors


# ---- Stage 3: キーワード確定 ----

def generate_keyword_prompt(segments, hongan, field, tech_analysis, classification, presearch_candidates):
    """Stage 3: キーワード確定プロンプトを生成

    Stage 1 + Stage 2 の結果を前提に、要素別キーワード辞書を確定する。
    """
    field_label = {"cosmetics": "化粧品", "laminate": "積層体"}.get(field, field)
    segments_text = _build_segments_text(segments)
    description = _extract_description_summary(hongan, max_chars=2000)

    # tech_analysis から要素一覧
    elements_text = ""
    if tech_analysis and tech_analysis.get("elements"):
        lines = []
        for key, elem in tech_analysis["elements"].items():
            label = elem.get("label", key)
            terms_ja = ", ".join(elem.get("terms_ja", [])[:8])
            terms_en = ", ".join(elem.get("terms_en", [])[:5])
            lines.append(f"- {key} ({label})")
            lines.append(f"  日本語: {terms_ja}")
            if terms_en:
                lines.append(f"  英語: {terms_en}")
        elements_text = "\n".join(lines)

    # 分類情報
    classification_text = ""
    if classification:
        lines = []
        for code_type in ["fi", "fterm", "cpc"]:
            codes = classification.get(code_type, [])
            if codes:
                code_strs = [f'{c["code"]}({c.get("label", "")})' for c in codes[:8]]
                lines.append(f"{code_type.upper()}: {', '.join(code_strs)}")
        classification_text = "\n".join(lines)

    # 代表文献のキーワード
    pub_terms_text = ""
    if presearch_candidates:
        lines = []
        for c in presearch_candidates[:3]:
            pid = c.get("patent_id", "?")
            kts = ", ".join(c.get("key_terms_found", [])[:10])
            if kts:
                lines.append(f"- {pid}: {kts}")
        pub_terms_text = "\n".join(lines)

    field_specific = ""
    if field == "cosmetics":
        field_specific = """
- INCI名、商品名（ブランド名）、化学名の3つを必ず含めてください
- 化粧品原料の場合、「ポリオキシエチレン～」等の長い化学名と短縮名の両方を列挙"""
    elif field == "laminate":
        field_specific = """
- 樹脂の正式名称と略称（PE/PP/PET等）の両方を必ず含めてください
- 層構成の同義語（基材層=ベースフィルム等）も含めてください"""

    return f"""## 役割
あなたは{field_label}分野の特許調査のキーワード設計の専門家です。
予備検索と分類特定の結果を踏まえて、最終的なキーワード辞書を確定してください。

## 重要な原則
- **公報語彙を最優先で採用**してください（推定語より、代表公報で実際に使われている語を優先）
- **コア語**（高適合の狭い検索式に使える）と**拡張語**（漏れ防止の広い検索式用）と**NOT語**（ノイズ除外）を分離
- **OR束**はそのまま検索式に貼れる括弧付き形式で提示
- 検索式を**狭→中→広**の3段階で提示：
  - 狭い式: 全要素ANDで強く結ぶ
  - 中くらい: D（作用）やE（効果）を緩める
  - 広い式: 分類（FI/CPC）も併用しつつ広げる
- 品質チェック: (a)代表公報語彙反映 (b)上位概念+用途制約 (c)ノイズ除外
{field_specific}

## 出力フォーマット
以下のJSON形式で出力してください：

```json
{{
  "elements": {{
    "A_xxx": {{
      "label": "要素の日本語ラベル",
      "core_terms": [
        {{"term": "キーワード", "source": "JP2020XXXXXXA", "location": "請求項1"}}
      ],
      "extended_terms": [
        {{"term": "拡張キーワード", "source": "推定", "location": ""}}
      ],
      "not_terms": ["ノイズ除外語1", "ノイズ除外語2"],
      "or_bundle_ja": "(用語1 OR 用語2 OR 用語3)",
      "or_bundle_en": "(term1 OR term2 OR term3)",
      "classifications": {{
        "fi": ["FIコード"],
        "fterm": ["Ftermコード"],
        "cpc": ["CPCコード"]
      }}
    }}
  }},
  "not_bundles": {{
    "noise_category_1": {{
      "label": "ノイズカテゴリ名",
      "terms": ["除外語1", "除外語2"]
    }}
  }},
  "search_formulas": {{
    "narrow": {{
      "description": "高適合：全要素ANDで強く結ぶ",
      "formula_jplatpat": "J-PlatPat式（500字以内、AND=*, OR=+, NOT=-）",
      "formula_google_patents": "Google Patents英語式"
    }},
    "medium": {{
      "description": "中適合：作用/効果を緩める",
      "formula_jplatpat": "...",
      "formula_google_patents": "..."
    }},
    "wide": {{
      "description": "広範囲：分類併用で広げる",
      "formula_jplatpat": "...",
      "formula_google_patents": "..."
    }}
  }},
  "quality_check": {{
    "pub_terms_covered": true,
    "upper_concept_constrained": true,
    "noise_exclusion_adequate": true,
    "notes": ["確認事項1", "確認事項2"]
  }}
}}
```

## 入力情報

### 技術要素（Stage 1）
{elements_text or "（未設定）"}

### 分類コード（Stage 2）
{classification_text or "（分類未特定 — Stage 2をスキップした場合、分類は推定でお願いします）"}

### 代表文献の語彙
{pub_terms_text or "（代表文献なし）"}

### 請求項分節
{segments_text}

### 明細書要約
{description or "（明細書データなし）"}
"""


def parse_keyword_response(raw_text):
    """Stage 3の回答をパース

    Returns:
        (keyword_dictionary: dict, errors: list)
    """
    errors = []
    data = _extract_json_object(raw_text)

    if not data:
        return None, ["JSONの抽出に失敗しました"]

    if not data.get("elements"):
        errors.append("elements セクションが見つかりません")

    # デフォルト値
    data.setdefault("not_bundles", {})
    data.setdefault("search_formulas", {})
    data.setdefault("quality_check", {})

    return data, errors


# ---- keyword_dictionary → keywords.json 変換 ----

def convert_keyword_dict_to_groups(keyword_dictionary, segments):
    """keyword_dictionary.json → 既存のkeywords.json形式に変換

    keyword_dictionary の各要素を、segment_ids に基づいて
    既存の keyword group 形式（group_id, label, color, segment_ids, keywords, search_codes）
    に変換する。

    これにより、3段階検索で確定したキーワードが、
    既存の対比プロンプト・PDF注釈フローにそのまま流せる。
    """
    COLOR_NAMES = {
        1: "赤", 2: "紫", 3: "マゼンタ", 4: "青",
        5: "緑", 6: "オレンジ", 7: "ティール",
    }

    elements = keyword_dictionary.get("elements", {})
    groups = []
    group_id = 0

    # tech_analysis の elements と segment_ids の対応を使う
    for key, elem in elements.items():
        group_id += 1
        if group_id > 7:
            break

        label = elem.get("label", key)

        # キーワードを統合
        kw_list = []
        for ct in elem.get("core_terms", []):
            kw_list.append({
                "term": ct.get("term", ""),
                "source": ct.get("source", ""),
                "type": "コア語",
                "tier": "core",
                "element": key,
            })
        for et in elem.get("extended_terms", []):
            kw_list.append({
                "term": et.get("term", ""),
                "source": et.get("source", ""),
                "type": "拡張語",
                "tier": "extended",
                "element": key,
            })

        # segment_ids の推定：tech_analysis があればそこから、なければ空
        # keyword_dictionary には直接 segment_ids が入っていないので、
        # segments データから推測する
        seg_ids = _infer_segment_ids(key, elem, segments)

        # search_codes
        search_codes = {"fterm": [], "fi": []}
        cls = elem.get("classifications", {})
        for ft_code in cls.get("fterm", []):
            search_codes["fterm"].append({"code": ft_code, "desc": "", "suffix": ""})
        for fi_code in cls.get("fi", []):
            search_codes["fi"].append({"code": fi_code, "desc": ""})

        groups.append({
            "group_id": group_id,
            "label": label,
            "color": COLOR_NAMES.get(group_id, "黒"),
            "segment_ids": seg_ids,
            "keywords": kw_list,
            "search_codes": search_codes,
        })

    return groups


def _infer_segment_ids(element_key, element_data, segments):
    """キーワード辞書の要素から関連する分節IDを推測"""
    # 要素のterms_jaがあれば、分節テキストとの一致で推測
    seg_ids = []
    terms = [t.get("term", "") for t in element_data.get("core_terms", [])]
    terms += element_data.get("terms_ja", []) if "terms_ja" in element_data else []

    if not terms:
        return seg_ids

    for claim in segments:
        is_indep = claim.get("is_independent", claim.get("claim_number") == 1)
        if not is_indep:
            continue
        for seg in claim.get("segments", []):
            text = seg.get("text", "")
            for term in terms[:5]:  # 最初の5語でマッチ
                if term and term in text:
                    seg_ids.append(seg["id"])
                    break

    return seg_ids
