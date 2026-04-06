#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""テキスト前処理ユーティリティ"""

import re

# CJK文字範囲
_CJK_CHAR = re.compile(r'[\u3040-\u30FF\u4E00-\u9FFF\uFF66-\uFF9F]')

_CJK_SPACE_RE = re.compile(
    r'(?<=[\u3040-\u30FF\u4E00-\u9FFF\uFF66-\uFF9F])\s+(?=[\u3040-\u30FF\u4E00-\u9FFF\uFF66-\uFF9F])'
)


def preprocess_text(text: str) -> str:
    """PDF改行由来のスペースでCJK文字が分断される問題を修正。

    例:
      "ポリアルキ レングリコールエーテル" → "ポリアルキレングリコールエーテル"
      "油状泡沫 性エアゾール用 化粧料" → "油状泡沫性エアゾール用化粧料"
    """
    if not text:
        return text
    return _CJK_SPACE_RE.sub('', text)
