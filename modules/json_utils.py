#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
JSON抽出ユーティリティ

AIの回答テキストからJSON（オブジェクト/配列）を抽出する共通モジュール。
```json ... ``` ブロック、裸の { } / [ ] 、途中切れJSONの修復に対応。
"""

import re
import json
import logging

logger = logging.getLogger(__name__)

_JSON_BLOCK_RE = re.compile(r'```(?:json)?\s*\n?(.*?)\n?\s*```', re.DOTALL)


def try_repair_json(text):
    """途中で切れたJSONの修復を試みる。

    Claudeの長い回答がトークン上限で途切れた場合に、
    閉じ括弧を補完してパースを試行する。

    Returns:
        パース済みオブジェクト、または修復不能なら None
    """
    text = text.strip()
    if not text:
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    truncated = text.rstrip()

    # 末尾のゴミ（途中の文字列値、カンマ等）を除去
    while truncated and truncated[-1] not in '{}[]"0123456789elfsu':
        truncated = truncated[:-1].rstrip()

    # 途中で切れた文字列リテラルを閉じる
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

    closing = ']' * max(0, open_brackets) + '}' * max(0, open_braces)
    candidate = truncated + closing

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def extract_json_object(raw_text, required_key=None, repair=True):
    """テキストからJSONオブジェクトを抽出する。

    Parameters:
        raw_text: AI回答テキスト
        required_key: 指定された場合、そのキーを含むオブジェクトのみを返す
        repair: True の場合、途中切れJSONの修復を試みる

    Returns:
        dict or None
    """
    def _accepts(data):
        if not isinstance(data, dict):
            return False
        if required_key and required_key not in data:
            return False
        return True

    # パターン1: ```json ... ``` ブロック
    matches = _JSON_BLOCK_RE.findall(raw_text)
    for match in matches:
        try:
            data = json.loads(match)
            if _accepts(data):
                return data
        except json.JSONDecodeError:
            continue

    # パターン1b: ```json ブロックの修復
    if repair:
        for match in matches:
            repaired = try_repair_json(match)
            if _accepts(repaired):
                logger.info("途中切れJSONブロックを修復")
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
                    if _accepts(data):
                        return data
                except json.JSONDecodeError:
                    start = None
                    continue

    # パターン3: 修復（最初の { 以降を修復試行）
    if repair:
        first_brace = raw_text.find('{')
        if first_brace >= 0:
            repaired = try_repair_json(raw_text[first_brace:])
            if _accepts(repaired):
                logger.info("閉じ切れていないJSONを修復")
                return repaired

    return None


def extract_json_array(raw_text, repair=True):
    """テキストからJSON配列を抽出する。

    Parameters:
        raw_text: AI回答テキスト
        repair: True の場合、途中切れJSONの修復を試みる

    Returns:
        list or None
    """
    def _accepts(data):
        return isinstance(data, list) and len(data) > 0

    # パターン1: ```json ... ``` ブロック
    matches = _JSON_BLOCK_RE.findall(raw_text)
    for match in matches:
        try:
            data = json.loads(match)
            if _accepts(data):
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
                    if _accepts(data):
                        return data
                except json.JSONDecodeError:
                    start = None
                    continue

    return None
