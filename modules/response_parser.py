#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Claude回答パースモジュール

入力: Claudeチャットからコピーしたテキスト（JSON含む）
出力: 検証済みの対比結果dict + エラーリスト

処理:
1. テキストからJSON部分を抽出
2. JSONパース
3. バリデーション
4. 不足があれば補完プロンプト生成
"""

import re
import json


def _extract_json_from_text(raw_text):
    """テキストからJSON部分を抽出

    対応パターン:
    - ```json ... ``` ブロック
    - { で始まり } で終わるテキスト
    - 文献ごとに分割されたJSON（```json が複数回）
    - 途中で切れたJSON（閉じ括弧の補完）

    Returns:
        dict: 単一文献の場合は {"comparisons": ...} 形式
              複数文献の場合は {"results": [...]} 形式
    """
    import logging
    logger = logging.getLogger(__name__)

    def _is_valid(data):
        if not isinstance(data, dict):
            return False
        # 複数文献: {"results": [...]}
        if "results" in data and isinstance(data["results"], list):
            return True
        # 単一文献: {"comparisons": [...]}
        if "comparisons" in data:
            return True
        return False

    def _is_single_doc(data):
        """単一文献の結果かどうか"""
        return isinstance(data, dict) and "comparisons" in data

    # パターン1: ```json ... ``` ブロック
    json_block_pattern = re.compile(r'```(?:json)?\s*\n?(.*?)\n?\s*```', re.DOTALL)
    matches = json_block_pattern.findall(raw_text)

    for match in matches:
        try:
            data = json.loads(match)
            if _is_valid(data):
                return data
        except json.JSONDecodeError:
            continue

    # パターン1b: 複数の```jsonブロック → 各ブロックが単一文献 → results配列に結合
    if len(matches) > 1:
        single_docs = []
        for match in matches:
            try:
                data = json.loads(match)
                if _is_single_doc(data):
                    single_docs.append(data)
            except json.JSONDecodeError:
                # 途中で切れたブロックの修復を試行
                repaired = _try_repair_json(match)
                if repaired and _is_single_doc(repaired):
                    single_docs.append(repaired)
        if single_docs:
            logger.info("複数JSONブロックを結合: %d文献", len(single_docs))
            return {"results": single_docs}

    # パターン1c: 単一の```jsonブロックだが途中で切れている
    if len(matches) == 1:
        repaired = _try_repair_json(matches[0])
        if repaired and _is_valid(repaired):
            logger.info("途中切れJSONを修復")
            return repaired

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
                    if _is_valid(data):
                        return data
                except json.JSONDecodeError:
                    start = None
                    continue

    # パターン3: 最大の { で始まるテキスト片（閉じ切れていない場合の修復）
    first_brace = raw_text.find('{')
    if first_brace >= 0:
        candidate = raw_text[first_brace:]
        repaired = _try_repair_json(candidate)
        if repaired and _is_valid(repaired):
            logger.info("閉じ切れていないJSONを修復")
            return repaired

    return None


def _try_repair_json(text):
    """途中で切れたJSONの修復を試みる

    Claudeの長い回答がトークン上限で途切れた場合に、
    閉じ括弧を補完してパースを試行する。
    """
    text = text.strip()
    if not text:
        return None

    # まずそのままパースを試行
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 末尾の不完全な文字列値を削除（途中で切れた "judgment_reason": "..." 等）
    # 最後の完全なプロパティまで巻き戻す
    truncated = text.rstrip()

    # 末尾のゴミ（途中の文字列値、カンマ等）を除去
    # 安全な末尾文字: }, ], ", 数字, true, false, null
    while truncated and truncated[-1] not in '{}[]"0123456789elfsu':
        truncated = truncated[:-1].rstrip()

    # 途中で切れた文字列リテラルを閉じる
    # ダブルクォートの数が奇数なら閉じクォートを追加
    if truncated.count('"') % 2 == 1:
        truncated += '"'

    # 閉じ括弧を補完
    open_braces = 0
    open_brackets = 0
    in_string = False
    escape = False
    for ch in truncated:
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            open_braces += 1
        elif ch == '}':
            open_braces -= 1
        elif ch == '[':
            open_brackets += 1
        elif ch == ']':
            open_brackets -= 1

    # 末尾のカンマを除去（JSON的に不正）
    truncated = truncated.rstrip()
    if truncated and truncated[-1] == ',':
        truncated = truncated[:-1]

    # 閉じ括弧を追加
    closing = ']' * max(0, open_brackets) + '}' * max(0, open_braces)
    candidate = truncated + closing

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _validate_response(data, required_segment_ids):
    """回答データのバリデーション"""
    errors = []

    if not isinstance(data, dict):
        errors.append("回答がJSON objectではありません。")
        return errors

    # comparisons の存在チェック
    comparisons = data.get("comparisons", [])
    if not comparisons:
        errors.append("comparisons 配列が空です。")
        return errors

    # 全構成要件が含まれているかチェック
    found_ids = set()
    for comp in comparisons:
        req_id = comp.get("requirement_id", "")
        found_ids.add(req_id)

    missing = set(required_segment_ids) - found_ids
    if missing:
        errors.append(f"以下の構成要件の判定がありません: {', '.join(sorted(missing))}")

    # 各比較結果のバリデーション
    valid_judgments = {"○", "△", "×"}
    for comp in comparisons:
        req_id = comp.get("requirement_id", "?")

        # judgment チェック
        judgment = comp.get("judgment", "")
        if judgment not in valid_judgments:
            errors.append(f"{req_id}: judgment '{judgment}' は無効です（○△×のいずれかを使用してください）")

        # cited_location チェック（×以外は必須）
        if judgment != "×" and not comp.get("cited_location", "").strip():
            errors.append(f"{req_id}: cited_location が空です（引用箇所を記載してください）")

        # judgment_reason チェック
        if not comp.get("judgment_reason", "").strip():
            errors.append(f"{req_id}: judgment_reason が空です")

    # sub_claims のバリデーション（任意）
    for sub in data.get("sub_claims", []):
        judgment = sub.get("judgment", "")
        if judgment and judgment not in valid_judgments:
            claim_num = sub.get("claim_number", "?")
            errors.append(f"請求項{claim_num}: judgment '{judgment}' は無効です")

    return errors


def generate_supplement_prompt(data, errors, required_segment_ids):
    """バリデーションエラーに基づく補完プロンプトを生成"""
    lines = ["前回の回答に以下の不足がありました。不足部分のみ追加で回答してください。\n"]

    for err in errors:
        lines.append(f"- {err}")

    # 欠落した構成要件があれば明示
    if data:
        found_ids = {comp.get("requirement_id") for comp in data.get("comparisons", [])}
        missing = set(required_segment_ids) - found_ids
        if missing:
            lines.append(f"\n以下の構成要件について、同じJSON形式で判定結果を返してください:")
            for mid in sorted(missing):
                lines.append(f"- {mid}")

    lines.append("\n前回と同じJSON形式で出力してください。")

    return "\n".join(lines)


def parse_response(raw_text, required_segment_ids):
    """Claude回答をパースして検証

    Parameters:
        raw_text: Claudeチャットからの回答テキスト
        required_segment_ids: 必須の構成要件IDリスト (["1A","1B","1C",...])

    Returns:
        (parsed_data, errors)
        - parsed_data: パース済み
            単一文献: {"document_id":..., "comparisons":[...], ...}
            複数文献: {"results": [{"document_id":..., "comparisons":[...]}, ...]}
        - errors: エラーメッセージのリスト（空なら成功）
    """
    data = _extract_json_from_text(raw_text)

    if data is None:
        return None, ["JSONデータを抽出できませんでした。Claudeの回答にJSON形式が含まれていることを確認してください。"]

    errors = []

    # 複数文献形式: {"results": [...]}
    if "results" in data and isinstance(data["results"], list):
        for i, result in enumerate(data["results"]):
            doc_id = result.get("document_id", f"文献{i+1}")
            result_errors = _validate_response(result, required_segment_ids)
            for err in result_errors:
                errors.append(f"[{doc_id}] {err}")
    else:
        # 単一文献形式
        errors = _validate_response(data, required_segment_ids)

    return data, errors


def split_multi_response(data):
    """複数文献の回答を文献ごとに分割

    Parameters:
        data: parse_response() の返り値(parsed_data)

    Returns:
        dict: {document_id: single_response_dict, ...}
    """
    if data is None:
        return {}

    # 複数文献形式
    if "results" in data and isinstance(data["results"], list):
        result = {}
        for item in data["results"]:
            doc_id = item.get("document_id", "unknown")
            result[doc_id] = item
        return result

    # 単一文献形式
    doc_id = data.get("document_id", "unknown")
    return {doc_id: data}


def merge_responses(existing_data, supplement_data):
    """既存の回答データに補完データをマージ"""
    if existing_data is None:
        return supplement_data

    if supplement_data is None:
        return existing_data

    # comparisons をマージ
    existing_ids = {c["requirement_id"] for c in existing_data.get("comparisons", [])}
    for comp in supplement_data.get("comparisons", []):
        if comp["requirement_id"] not in existing_ids:
            existing_data.setdefault("comparisons", []).append(comp)
        else:
            # 既存のを上書き
            for i, ec in enumerate(existing_data["comparisons"]):
                if ec["requirement_id"] == comp["requirement_id"]:
                    existing_data["comparisons"][i] = comp
                    break

    # sub_claims をマージ
    if "sub_claims" in supplement_data:
        existing_claims = {sc.get("claim_number") for sc in existing_data.get("sub_claims", [])}
        for sc in supplement_data["sub_claims"]:
            if sc.get("claim_number") not in existing_claims:
                existing_data.setdefault("sub_claims", []).append(sc)

    # その他のフィールドを補完
    for key in ["overall_summary", "category_suggestion", "rejection_relevance"]:
        if key in supplement_data and (key not in existing_data or not existing_data[key]):
            existing_data[key] = supplement_data[key]

    return existing_data
