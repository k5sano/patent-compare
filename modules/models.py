#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
データモデル定義

str を継承した Enum を使用することで、既存コードの
`judgment == "○"` 等の文字列比較がそのまま動作する。
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List


class Judgment(str, Enum):
    """対比判定結果"""
    MATCH = "○"
    PARTIAL = "△"
    DIFF = "×"


class Relevance(str, Enum):
    """引用文献の関連性分類"""
    PRIMARY = "主引例候補"
    SECONDARY = "副引例候補"
    COMMON_KNOWLEDGE = "技術常識"


class Field(str, Enum):
    """技術分野"""
    COSMETICS = "cosmetics"
    LAMINATE = "laminate"


@dataclass
class AIResult:
    """AI呼び出し結果の構造化コンテナ"""
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None
