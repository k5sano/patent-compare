# -*- coding: utf-8 -*-
"""
分節テキストと本願段落のマッチング

各分節について、本願明細書の段落の中で最も関連性が高いものを
トークン照合ベースで検出する。日本語特許を主対象。
"""

import re

_STOPWORDS = {
    "こと", "もの", "これら", "それ", "この", "その", "ため", "とき",
    "ある", "する", "される", "なる", "いる", "ない", "できる",
    "より", "以上", "以下", "前記", "後記", "当該", "以外",
    "方法", "工程", "組成", "物品", "用途", "装置", "システム",
    "発明", "実施", "形態", "態様",
    "請求", "項",
}

_KANJI_RE = re.compile(r"[一-龯々〆ヶ]{2,}")
_KATAKANA_RE = re.compile(r"[ァ-ヴー]{2,}")
_ALNUM_RE = re.compile(r"[A-Za-z][A-Za-z0-9]+")

# 段落タイプ判定（定義 / 例示）
_DEF_PATTERNS = [
    re.compile(r"とは[、。\s]"),
    re.compile(r"を意味する"),
    re.compile(r"と定義"),
    re.compile(r"を[い言]う[。、\s]"),
    re.compile(r"と称する"),
    re.compile(r"を指す"),
]
_EX_PATTERNS = [
    re.compile(r"としては[、\s]"),
    re.compile(r"例えば"),
    re.compile(r"挙げ(ら|る|て)"),
    re.compile(r"の例として"),
    re.compile(r"具体例"),
    re.compile(r"例示"),
]


def classify_paragraph(text: str) -> str:
    """段落テキストを '定義' / '例示' / '' に分類する"""
    if not text:
        return ""
    if any(p.search(text) for p in _DEF_PATTERNS):
        return "定義"
    if any(p.search(text) for p in _EX_PATTERNS):
        return "例示"
    return ""


def _extract_tokens(text: str) -> set:
    tokens = set()
    for pat in (_KANJI_RE, _KATAKANA_RE, _ALNUM_RE):
        for m in pat.findall(text or ""):
            if m in _STOPWORDS:
                continue
            tokens.add(m)
    return tokens


def find_related_paragraphs(segments, paragraphs, top_n=5, min_matches=2):
    """各分節について関連段落を検出する。定義/例示の型も付与。

    Returns:
        dict: { seg_id: [{"id": "0015", "page": 3, "score": 120,
                           "type": "定義", "matched": [...]}, ...] }
          type は "定義" / "例示" / ""
    """
    # 全段落を一度だけ分類
    paragraph_types = {}
    for p in paragraphs or []:
        paragraph_types[p.get("id", "")] = classify_paragraph(p.get("text", ""))

    result = {}
    for claim in segments or []:
        for seg in claim.get("segments", []):
            sid = seg.get("id")
            if not sid:
                continue
            tokens = _extract_tokens(seg.get("text", ""))
            if not tokens:
                result[sid] = []
                continue

            scored = []
            for p in paragraphs or []:
                ptext = p.get("text", "")
                if not ptext:
                    continue
                matched = [t for t in tokens if t in ptext]
                if len(matched) < min_matches:
                    continue
                pid = p.get("id", "")
                # 区別マッチ数を主、長さ合計を従、定義/例示にはボーナス
                score = len(matched) * 100 + sum(len(t) for t in matched)
                ptype = paragraph_types.get(pid, "")
                if ptype:
                    score += 20
                scored.append({
                    "id": pid,
                    "page": p.get("page", 1),
                    "score": score,
                    "type": ptype,
                    "matched": sorted(matched, key=len, reverse=True)[:5],
                })
            scored.sort(key=lambda x: x["score"], reverse=True)
            result[sid] = scored[:top_n]
    return result
