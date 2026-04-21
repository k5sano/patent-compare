#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""特許番号（引用文献ID）の正規化ユーティリティ。

`WO2019180364` と `WO2019180364A1` のように、同じ特許に対して
種別コード（Kind Code）の有無だけが異なる表記が混在する問題を解消する。

方針:
    - 2文字国コード + 数字ベースの国際形式末尾に付く種別コード
      (A / A1 / A2 / B / B1 / B2 / C / C1 / T / T1 / U など) を除去する。
    - 日本語表記（特開・特許第・再公表WO）は原則そのまま残す。
    - 空白・スラッシュなどの表記ゆれは除去する。
"""

from __future__ import annotations

import re
from typing import Iterable, List, Dict, Any


# 国際公報形式: 2文字国コード + (数字 or 数字-数字) + 末尾種別コード(任意)
#   WO2019180364A1, EP3719056A1, FR3088205A1,
#   JP6199984B2, JP2024-032096A, US20200123456A1 などに対応
_INTL_PATENT_RE = re.compile(r"^([A-Z]{2})(\d[\d\-]*?)([A-Z]\d?)?$")


def normalize_citation_id(doc_id: str) -> str:
    """特許番号から末尾の種別コードを除去した正規形を返す。

    >>> normalize_citation_id("WO2019180364A1")
    'WO2019180364'
    >>> normalize_citation_id("WO2019180364")
    'WO2019180364'
    >>> normalize_citation_id("JP6199984B2")
    'JP6199984'
    >>> normalize_citation_id("JP2024-032096A")
    'JP2024-032096'
    >>> normalize_citation_id("特開2024-032096")
    '特開2024-032096'
    >>> normalize_citation_id("  WO 2019/180364 A1 ")
    'WO2019180364'
    """
    if not doc_id:
        return doc_id

    s = str(doc_id).strip()
    # 空白 / スラッシュ / ハイフン代替文字 を除去
    s = s.replace(" ", "").replace("\u3000", "").replace("/", "")
    s = s.replace("−", "-").replace("－", "-")

    m = _INTL_PATENT_RE.match(s)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    return s


def dedupe_citations(citations: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """引用文献リストから同一特許の重複を統合する。

    入力例:
        [{"id": "WO2019180364", "role": "主引例候補", "label": "WO2019/180364A1"},
         {"id": "WO2019180364A1", "role": "主引例候補", "label": "WO2019/180364A1"}]

    出力例:
        [{"id": "WO2019180364", "role": "主引例候補", "label": "WO2019/180364A1"}]

    マージ規則:
        - id: 正規化後の形式
        - role / label: より長い（＝情報量が多い）側を優先
        - その他のキーは最初に見つかったものを保持
    """
    result: List[Dict[str, Any]] = []
    index: Dict[str, int] = {}

    for cit in citations:
        if not isinstance(cit, dict):
            continue
        raw_id = cit.get("id", "")
        norm = normalize_citation_id(raw_id)
        if not norm:
            continue

        if norm not in index:
            new_cit = dict(cit)
            new_cit["id"] = norm
            index[norm] = len(result)
            result.append(new_cit)
        else:
            existing = result[index[norm]]
            for key in ("role", "label"):
                incoming = cit.get(key) or ""
                current = existing.get(key) or ""
                if len(str(incoming)) > len(str(current)):
                    existing[key] = incoming

    return result
