#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""検索ラン管理サービス

J-PlatPat / Google Patents で検索式を実行した結果を
cases/<id>/search_runs/<run_id>.json に保存し、スクリーニング状態を管理する。

データモデル (1 ランの JSON 構造):
{
  "run_id": "20260421-215830-narrow",
  "created_at": "2026-04-21T21:58:30+09:00",
  "updated_at": "...",
  "source": "jplatpat" | "google_patents",
  "formula_level": "narrow" | "medium" | "wide" | "custom",
  "formula": "<検索式本体>",
  "search_url": "<検索結果ページ URL>",
  "hit_count": 42,
  "status": "pending" | "done" | "error",
  "error": null,
  "hits": [
    {
      "patent_id": "特開2023-123456",
      "title": "...",
      "applicant": "...",
      "publication_date": "2023-xx-xx",
      "ipc": [...], "fi": [...], "fterm": [...],
      "url": "...",
      "abstract": null,        # Phase2 で enrich
      "claim1": null,          # Phase2 で enrich
      "ai_score": null,        # Phase2 の関連度
      "ai_reason": null,
      "screening": "pending",  # pending / star / triangle / reject / hold
      "note": "",
      "downloaded_as_citation": false
    }
  ]
}
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Iterable

from services.case_service import get_case_dir


SCREENING_STATES = ("pending", "star", "triangle", "reject", "hold")


def _runs_dir(case_id: str) -> Path:
    d = get_case_dir(case_id) / "search_runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _hit_text_dir(case_id: str) -> Path:
    """ヒットの全文キャッシュ保存先（run 横断で再利用）。"""
    d = get_case_dir(case_id) / "search_runs" / "_hit_text"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _hit_bookmarks_path(case_id: str) -> Path:
    return _runs_dir(case_id) / "_hit_bookmarks.json"


def list_hit_bookmarks(case_id: str) -> list[dict]:
    """全文レビュー画面で付けた文献しおりを返す。"""
    p = _hit_bookmarks_path(case_id)
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    bookmarks = data.get("bookmarks") if isinstance(data, dict) else data
    out = []
    for item in bookmarks or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        patent_id = str(item.get("patent_id") or "").strip()
        if name and patent_id:
            out.append({
                "name": name,
                "patent_id": patent_id,
                "created_at": item.get("created_at") or "",
            })
    return out


def save_hit_bookmark(case_id: str, patent_id: str, name: str) -> dict:
    """文献に名前付きしおりを付ける。同一 name/patent_id は重複させない。"""
    patent_id = str(patent_id or "").strip()
    name = str(name or "").strip()
    if not patent_id:
        raise ValueError("patent_id is required")
    if not name:
        raise ValueError("name is required")
    bookmarks = list_hit_bookmarks(case_id)
    existing = next(
        (b for b in bookmarks if b["name"] == name and b["patent_id"] == patent_id),
        None,
    )
    if existing:
        return existing
    item = {
        "name": name,
        "patent_id": patent_id,
        "created_at": _now_iso(),
    }
    bookmarks.append(item)
    p = _hit_bookmarks_path(case_id)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"bookmarks": bookmarks}, f, ensure_ascii=False, indent=2)
    return item


def _write_hit_text_cache(case_id: str, patent_id: str, data: dict) -> None:
    p = _hit_text_dir(case_id) / f"{_safe_pid(patent_id)}.json"
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _hit_text_content_score(data: Optional[dict]) -> int:
    if not isinstance(data, dict):
        return 0
    return (
        len(data.get("description") or "")
        + len(data.get("abstract") or "")
        + sum(len(str(c or "")) for c in (data.get("claims") or []))
    )


def _merge_hit_text_with_fallback(data: dict, fallback: Optional[dict]) -> dict:
    """外部再取得が薄い場合にローカル/既存本文を失わないよう補完する。"""
    if not fallback:
        return data
    merged = dict(data or {})
    for key in ("title", "abstract", "description", "raw", "local_citation_id", "local_patent_number"):
        if not merged.get(key) and fallback.get(key):
            merged[key] = fallback.get(key)
    if not merged.get("claims") and fallback.get("claims"):
        merged["claims"] = fallback.get("claims")
    if not merged.get("images") and fallback.get("images"):
        merged["images"] = fallback.get("images")
    if not _hit_text_content_score(merged) and _hit_text_content_score(fallback):
        merged = dict(fallback)
        if data and data.get("images") and not merged.get("images"):
            merged["images"] = data.get("images")
    elif fallback.get("source") == "local_citation":
        merged.setdefault("local_citation_id", fallback.get("local_citation_id"))
        merged.setdefault("local_patent_number", fallback.get("local_patent_number"))
    return merged


def _safe_pid(patent_id: str) -> str:
    """patent_id をファイル名安全に正規化。"""
    s = (patent_id or "").strip()
    return re.sub(r'[\\/:*?"<>|]', '_', s) or "_unknown"


def _normalize_hit_text_patent_id(patent_id: str) -> str:
    """hit_text lookup 用の公報番号正規化。

    Step 4.5 の全文キャッシュは検索結果の表示名で保存されることがある
    (例: 再表2007/108460 → 再表2007_108460.json)。Step 5 側の citation_id
    は WO2007108460 のように別表記になり得るため、同一 WO 番号へ寄せる。
    """
    s = (patent_id or "").strip().upper()
    if not s:
        return ""

    m = re.match(r"再(?:公)?表\s*(\d{4})\s*[-ー－/／_ ]\s*(\d+)", s)
    if m:
        return f"WO{m.group(1)}{m.group(2).zfill(6)}"

    cleaned = re.sub(r"[\s\-/／_ー－]", "", s)
    m = re.match(r"WO(\d{4})(\d+?)(?:A\d?)?$", cleaned)
    if m:
        return f"WO{m.group(1)}{m.group(2).zfill(6)}"

    return re.sub(r"[^A-Z0-9]", "", cleaned)


def _hit_text_match_keys(patent_id: str) -> set[str]:
    """全文キャッシュ・引用JSON照合用の緩い比較キー。"""
    raw = str(patent_id or "").strip()
    if not raw:
        return set()
    keys = {raw.upper()}
    norm = _normalize_hit_text_patent_id(raw)
    if norm:
        keys.add(norm)
        keys.add(re.sub(r"(A|A\d|B|B\d|U|U\d)$", "", norm))
    try:
        from modules.citation_id import normalize_citation_id
        cit_norm = normalize_citation_id(raw)
        if cit_norm:
            keys.add(str(cit_norm).upper())
            keys.add(_normalize_hit_text_patent_id(cit_norm))
    except Exception:
        pass

    # 文字列中に JP5047668B2 のような別表記が埋まっている場合を拾う。
    for m in re.finditer(r"JP\s*[- ]?(\d[\d\s\-]*)([A-Z]\d?)?", raw.upper()):
        number = re.sub(r"\D", "", m.group(1))
        kind = (m.group(2) or "").upper()
        if number:
            keys.add(f"JP{number}{kind}")
            keys.add(f"JP{number}")

    m = re.search(r"特開\s*(\d{4})\s*[-ー－/／_ ]\s*(\d+)", raw)
    if m:
        keys.add(f"JP{m.group(1)}{m.group(2).zfill(6)}A")
        keys.add(f"{m.group(1)}{m.group(2).zfill(6)}")

    m = re.search(r"特許\s*第?\s*(\d+)\s*号?", raw)
    if m:
        keys.add(f"JP{m.group(1)}")
        keys.add(m.group(1))

    return {re.sub(r"[\s\-/／_ー－.,，．・()（）]", "", k) for k in keys if k}


def get_hit_text(case_id: str, patent_id: str) -> Optional[dict]:
    p = _hit_text_dir(case_id) / f"{_safe_pid(patent_id)}.json"
    if not p.exists():
        target_keys = _hit_text_match_keys(patent_id)
        if not target_keys:
            return None
        for cand in _hit_text_dir(case_id).glob("*.json"):
            cand_keys = _hit_text_match_keys(cand.stem)
            if cand_keys & target_keys:
                p = cand
                break
            try:
                with open(cand, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            data_keys = _hit_text_match_keys(data.get("patent_id", ""))
            if data_keys & target_keys:
                return data
        else:
            return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def list_cached_hit_texts(case_id: str, patent_ids=None) -> dict:
    """指定された patent_id 群 (または全件) のキャッシュ済 full text を一括返却。

    UI のページロード時に `window._pkmFullTexts` を復元するための bulk API。
    各クライアントが個別 GET するよりも server round-trip を 1 回に集約する。

    Returns:
        {patent_id: hit_text_data, ...} (キャッシュが無い patent_id はキー欠落)
    """
    out: dict = {}
    if patent_ids is None:
        # ディレクトリ全件
        d = _hit_text_dir(case_id)
        for p in d.glob("*.json"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # 元の patent_id は data["patent_id"] か filename stem から復元
                pid = data.get("patent_id") or p.stem
                out[pid] = data
            except (OSError, json.JSONDecodeError):
                continue
        return out
    for pid in patent_ids:
        data = get_hit_text(case_id, pid)
        if (
            data is None
            or _hit_text_content_score(data) == 0
            or (data.get("source") == "local_citation" and not data.get("title"))
        ):
            local_data = _build_hit_text_from_citation(case_id, pid)
            if local_data is not None:
                local_data["from_local_citation"] = True
                _write_hit_text_cache(case_id, pid, local_data)
                data = local_data
        if data is not None:
            out[pid] = data
    return out


def _text_from_claim(claim) -> str:
    if isinstance(claim, dict):
        text = str(claim.get("text") or claim.get("full_text") or "").strip()
        number = claim.get("number") or claim.get("id")
        if text and number and not text.startswith("【請求項"):
            return f"【請求項{number}】{text}"
        return text
    return str(claim or "").strip()


def _description_from_paragraphs(paragraphs) -> str:
    fw2hw = str.maketrans("０１２３４５６７８９", "0123456789")
    parts = []
    for para in paragraphs or []:
        if isinstance(para, dict):
            pid_raw = str(para.get("id") or para.get("number") or "").translate(fw2hw)
            text = str(para.get("text") or "").strip()
        else:
            pid_raw = ""
            text = str(para or "").strip()
        if not text:
            continue
        m = re.search(r"\d+", pid_raw)
        marker = f"【{m.group(0).zfill(4)}】" if m else ""
        parts.append(f"{marker}{text}")
    return "\n".join(parts)


def _local_hit_title(data: dict, patent_id: str, local_id: str) -> str:
    title = (
        data.get("patent_title")
        or data.get("title")
        or data.get("invention_title")
        or ""
    )
    return str(title or local_id or patent_id or "").strip()


def _build_hit_text_from_citation(case_id: str, patent_id: str) -> Optional[dict]:
    """既に Step5/6 用に抽出済みの citation JSON から全文ビュー用 cache を作る。"""
    target_keys = _hit_text_match_keys(patent_id)
    if not target_keys:
        return None
    citations_dir = get_case_dir(case_id) / "citations"
    if not citations_dir.exists():
        return None

    best_path = None
    best_data = None
    for path in sorted(citations_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        aliases = [
            path.stem,
            data.get("patent_number"),
            data.get("label"),
            data.get("doc_id"),
            data.get("publication_number"),
        ]
        cand_keys = set()
        for alias in aliases:
            cand_keys.update(_hit_text_match_keys(alias))
        if cand_keys & target_keys:
            best_path = path
            best_data = data
            break

    if not best_path or not best_data:
        return None

    claims = [_text_from_claim(c) for c in (best_data.get("claims") or [])]
    claims = [c for c in claims if c]
    description = _description_from_paragraphs(best_data.get("paragraphs") or [])
    abstract = ""
    raw_abstract = best_data.get("abstract")
    if isinstance(raw_abstract, dict):
        abstract = str(raw_abstract.get("text") or "").strip()
    elif raw_abstract:
        abstract = str(raw_abstract).strip()

    if not (claims or description or abstract):
        return None

    local_id = best_data.get("patent_number") or best_data.get("label") or best_path.stem
    title = _local_hit_title(best_data, patent_id, local_id)
    return {
        "patent_id": patent_id,
        "local_citation_id": best_path.stem,
        "local_patent_number": local_id,
        "url": "",
        "title": title,
        "abstract": abstract,
        "claims": claims,
        "description": description,
        "raw": "\n".join([abstract, "\n".join(claims), description]).strip(),
        "source": "local_citation",
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "classifications": {},
        "images": [],
    }


def _case_input_pdf_exists(case_id: str, patent_id: str) -> bool:
    target_keys = _hit_text_match_keys(patent_id)
    if not target_keys:
        return False
    input_dir = get_case_dir(case_id) / "input"
    if not input_dir.exists():
        return False
    for path in input_dir.glob("*.pdf"):
        if _hit_text_match_keys(path.stem) & target_keys:
            return True
    return False


def _default_text_source(patent_id: str) -> str:
    """patent_id から最適な取得元を決める。

    - 純粋な JP 公報 (特開/特願/特許/JP*) → J-PlatPat (canonical 日本語)
    - 再公表/再表 → Google Patents (WO 番号で直接アクセス可能、J-PlatPat は失敗しがち)
    - その他 → Google Patents
    """
    s = (patent_id or "").upper()
    # 再公表/再表 は WO の翻訳版なので Google Patents を優先
    if any(p in s for p in ("再公表", "再表")):
        return "google"
    if any(p in s for p in ("特開", "特願", "特許")):
        return "jplatpat"
    if re.match(r'^\s*JP[\s\-]?\d', s):
        return "jplatpat"
    return "google"


def _google_text_language_for(patent_id: str, requested: str = "ja") -> str:
    """Google Patents の本文取得に使う言語を決める。

    WO/EP/US 等は /ja が機械翻訳スタブだけになることがあるため、全文取得は
    /en を優先する。JP 系は J-PlatPat か Google /ja の方が読みやすい。
    """
    lang = (requested or "ja").lower()
    if lang != "ja":
        return lang
    s = (patent_id or "").strip().upper()
    if any(p in s for p in ("特開", "特願", "特許", "特表")):
        return "ja"
    if re.match(r'^\s*JP[\s\-]?\d', s):
        return "ja"
    if re.match(r'^\s*(WO|EP|US|CN|KR|DE|FR|GB|CA|AU)[\s\-\/]?\d', s):
        return "en"
    return lang


def _is_thin_google_translation(hit: dict) -> bool:
    """既存キャッシュが Google /ja の痩せた翻訳スタブなら再取得対象にする。"""
    if not isinstance(hit, dict):
        return False
    src = (hit.get("source") or "").lower()
    url = (hit.get("url") or "").lower()
    if src not in ("google", "google_fallback") and "patents.google.com" not in url:
        return False
    desc = (hit.get("description") or "").strip()
    claims = hit.get("claims") or []
    abstract = (hit.get("abstract") or "").strip()
    if "/ja" not in url:
        return False
    if len(desc) < 300 and not abstract:
        return True
    if len(desc) < 1200 and len(claims) <= 1 and desc.lower().startswith("translated from"):
        return True
    return False


_PKM_GROUP_COLORS = [
    # modules.pdf_annotator._GROUP_COLORS と同じ色。Web では 40% 透過で塗る。
    '#ff9999', '#c7a6ff', '#ff99d9', '#99bfff',
    '#99ffb3', '#ffd180', '#80f2d9', '#bfc4cc',
]


def pkm_group_color(gid: int) -> str:
    try:
        i = int(gid)
    except (TypeError, ValueError):
        return _PKM_GROUP_COLORS[0]
    return _PKM_GROUP_COLORS[(i - 1) % len(_PKM_GROUP_COLORS)]


def _pkm_rgba(hex_color: str, opacity: float = 0.4) -> str:
    h = str(hex_color or "").strip().lstrip("#")
    if len(h) != 6:
        return "rgba(248, 113, 113, 0.4)"
    try:
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
    except ValueError:
        return "rgba(248, 113, 113, 0.4)"
    return f"rgba({r}, {g}, {b}, {opacity})"


def pkm_build_index(keywords_data) -> list:
    """Step 3 のキーワードグループ辞書からハイライト用 index を作る。"""
    items = []
    for g in (keywords_data or []):
        gid = g.get("group_id")
        for kw in g.get("keywords", []) or []:
            term = (kw or {}).get("term", "")
            if isinstance(term, str):
                term = term.strip()
            if term:
                items.append({"term": term, "gid": gid})
    items.sort(key=lambda x: -len(x["term"]))
    return items


# ----------------------------------------------------------------
# OCR ゆれ吸収用の正規化
# ----------------------------------------------------------------
# 化粧品/化学分野の用語は OCR で以下のような揺れが頻出する:
#   - 小書きカタカナへの変化:   グアニル → グァニル (ア → ァ)
#   - 拗音表記 / 直音表記の混在: システイン ↔ システィン (テイ ↔ ティ)
#   - 空白の挿入:                グ アニル シ ステイン (空白で寸断)
#   - 全角英数 ↔ 半角英数 の混在
# pkm_highlight_python と JS 側の pkmHighlight ではこれらを吸収する正規化を
# かけてから検索する。原文の位置情報も idx_map で復元してハイライト範囲を維持する。

_KATAKANA_SMALL_TO_BIG = {
    "ァ": "ア", "ィ": "イ", "ゥ": "ウ", "ェ": "エ", "ォ": "オ",
    "ャ": "ヤ", "ュ": "ユ", "ョ": "ヨ", "ヮ": "ワ",
}

# 拗音表記 → 直音表記。2 文字 → 2 文字なので位置 mapping を保てる。
# 「ティ」「ディ」「ファ」等は文字数を維持したまま「テイ」「デイ」「フア」に
# 揃えることで、OCR の表記ゆれを吸収する。
_KATAKANA_DIGRAPH_RULES = [
    ("ティ", "テイ"), ("ディ", "デイ"),
    ("ファ", "フア"), ("フィ", "フイ"), ("フェ", "フエ"), ("フォ", "フオ"),
    ("ウィ", "ウイ"), ("ウェ", "ウエ"), ("ウォ", "ウオ"),
    ("シェ", "シエ"), ("ジェ", "ジエ"), ("チェ", "チエ"),
    ("ヴァ", "ヴア"), ("ヴィ", "ヴイ"), ("ヴェ", "ヴエ"), ("ヴォ", "ヴオ"),
]
_KATAKANA_DIGRAPH_MAP = dict(_KATAKANA_DIGRAPH_RULES)

# 全角数字・英字 → 半角 + 全角/特殊ハイフン類 → 半角ハイフン
_FULLWIDTH_TO_HALF = str.maketrans({
    **{chr(0xFF10 + i): str(i) for i in range(10)},        # ０〜９
    **{chr(0xFF21 + i): chr(ord("A") + i) for i in range(26)},  # Ａ〜Ｚ
    **{chr(0xFF41 + i): chr(ord("a") + i) for i in range(26)},  # ａ〜ｚ
    "－": "-",  # FULLWIDTH HYPHEN-MINUS (U+FF0D)
    "−": "-",  # MINUS SIGN (U+2212)
    "‐": "-",  # HYPHEN (U+2010)
    "—": "-",  # EM DASH (U+2014)
    "–": "-",  # EN DASH (U+2013)
    # KATAKANA-HIRAGANA PROLONGED SOUND MARK (U+30FC, ー) は意味ある音符のため残す
})


def _normalize_text_for_match(text: str):
    """OCR ゆれ吸収のための正規化。原文位置とのマッピングも返す。

    Returns:
        (normalized: str, idx_map: list[int])
        normalized[i] は原文 text[idx_map[i]] (空白除去済) に対応する。
        ハイライト時は normalized 上の検索結果を idx_map で原文位置に戻す。
    """
    if not text:
        return "", []
    # 全角数字/英字 → 半角 (1:1)
    src = text.translate(_FULLWIDTH_TO_HALF)
    out_chars: list = []
    out_idx: list = []
    i = 0
    n = len(src)
    while i < n:
        # digraph (2 文字 → 2 文字) を最優先で照合
        if i + 1 < n:
            two = src[i] + src[i + 1]
            replacement = _KATAKANA_DIGRAPH_MAP.get(two)
            if replacement:
                # replacement の各文字を out に追加 (元位置は i, i+1)
                for j, dch in enumerate(replacement):
                    out_chars.append(dch.lower())
                    out_idx.append(i + min(j, 1))
                i += 2
                continue
        ch = src[i]
        if ch.isspace():
            i += 1
            continue
        # 小書き → 大書き
        ch = _KATAKANA_SMALL_TO_BIG.get(ch, ch)
        out_chars.append(ch.lower())
        out_idx.append(i)
        i += 1
    return "".join(out_chars), out_idx


def pkm_highlight_python(text: str, index: list) -> dict:
    """JS の pkmHighlight() と同じロジックの Python 版。OCR ゆれを正規化吸収する。

    検索手順:
      1. 原文と各 term を `_normalize_text_for_match` で正規化
         (全角→半角 / 小書き→大書き / 拗音→直音 / 空白除去 / lower)
      2. 正規化テキスト上で term を検索
      3. ヒット位置を idx_map で原文位置に逆引きして <mark> でハイライト
      4. counts は normalized 上の検出回数 (重複は overlap 判定で除外)

    Returns: {"html": <escaped HTML with <mark>>, "counts": {gid: n}}
    """
    import html as _html
    t = text or ""
    if not t or not index:
        return {"html": _html.escape(t), "counts": {}}

    norm_text, idx_map = _normalize_text_for_match(t)
    positions = []
    for item in index:
        term = item.get("term") or ""
        if not term:
            continue
        norm_term, _ = _normalize_text_for_match(term)
        if not norm_term:
            continue
        L = len(norm_term)
        pos = 0
        while True:
            pos = norm_text.find(norm_term, pos)
            if pos < 0:
                break
            # 原文位置に逆引き (空白除去のため範囲が広がる)
            start = idx_map[pos]
            end = idx_map[pos + L - 1] + 1
            length = end - start
            overlap = any(
                not (end <= p["start"] or start >= p["start"] + p["length"])
                for p in positions
            )
            if not overlap:
                positions.append({"start": start, "length": length,
                                  "gid": item["gid"]})
            pos += L
    positions.sort(key=lambda p: p["start"])
    counts = {}
    for p in positions:
        counts[p["gid"]] = counts.get(p["gid"], 0) + 1
    out = []
    prev = 0
    for p in positions:
        out.append(_html.escape(t[prev:p["start"]]))
        matched = _html.escape(t[p["start"]:p["start"] + p["length"]])
        color = pkm_group_color(p["gid"])
        bg = _pkm_rgba(color)
        out.append(
            f'<mark class="pkm-mark" style="--c:{color}; --pc-hl-bg:{bg};" '
            f'data-gid="{p["gid"]}">{matched}</mark>'
        )
        prev = p["start"] + p["length"]
    out.append(_html.escape(t[prev:]))
    return {"html": "".join(out), "counts": counts}


def fetch_and_cache_hit_text(case_id: str, patent_id: str, *, force: bool = False,
                              language: str = "ja", source: str = "auto") -> dict:
    """全文を取得して案件配下にキャッシュ。force=False なら既存を返す。

    source: 'auto' (JP 系は jplatpat / それ以外 google), 'google', 'jplatpat'

    text の取得元が J-PlatPat の場合でも、実施例の表画像は Google Patents の
    description ページにのみ埋め込まれているため並列で Google にも問い合わせて
    images だけマージする (Ryzen 9 を活かして wall time を増やさない)。
    """
    if not patent_id:
        return {"error": "patent_id が空です"}
    chosen = source if source in ("google", "jplatpat") else _default_text_source(patent_id)
    cached = get_hit_text(case_id, patent_id)
    local_fallback = _build_hit_text_from_citation(case_id, patent_id)
    if cached and not force:
        if not (chosen == "google" and _is_thin_google_translation(cached)):
            cached["from_cache"] = True
            return cached

    if not force:
        local = local_fallback
        if local:
            _write_hit_text_cache(case_id, patent_id, local)
            local["from_cache"] = False
            local["from_local_citation"] = True
            return local

    if chosen == "jplatpat":
        from modules.jplatpat_client import fetch_jplatpat_full_text
        from modules.google_patents_scraper import fetch_patent_full_text
        from concurrent.futures import ThreadPoolExecutor
        google_language = _google_text_language_for(patent_id, language)
        # J-PlatPat (text canonical) と Google Patents (images) を並列取得
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_jp = ex.submit(fetch_jplatpat_full_text, patent_id, language=language)
            f_g = ex.submit(fetch_patent_full_text, patent_id, language=google_language)
            try:
                data = f_jp.result(timeout=60)
            except Exception as e:
                data = {"error": f"J-PlatPat 取得失敗: {e}"}
            try:
                g_data = f_g.result(timeout=60)
            except Exception:
                g_data = {}
        # J-PlatPat 失敗 → Google にフォールバック
        if not data.get("description") and not data.get("claims"):
            if g_data.get("description") or g_data.get("claims"):
                data = g_data
                data["source"] = "google_fallback"
        else:
            # J-PlatPat 成功: Google から images だけ拝借 (text は J-PlatPat 優先)
            if g_data.get("images") and not data.get("images"):
                data["images"] = g_data["images"]
    else:
        from modules.google_patents_scraper import fetch_patent_full_text
        google_language = _google_text_language_for(patent_id, language)
        data = fetch_patent_full_text(patent_id, language=google_language)
        data.setdefault("source", "google")

    fallback = local_fallback if _hit_text_content_score(local_fallback) else cached
    data = _merge_hit_text_with_fallback(data, fallback)
    if not _hit_text_content_score(data) and _hit_text_content_score(cached):
        data = dict(cached)
        data["from_cache"] = True
        data["warning"] = "再取得結果が空だったため、既存キャッシュを保持しました"
        return data

    _write_hit_text_cache(case_id, patent_id, data)
    data["from_cache"] = False
    return data


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _slugify(text: str, max_len: int = 20) -> str:
    t = re.sub(r'[^A-Za-z0-9._-]+', '-', text).strip('-')
    return t[:max_len] or "run"


_last_run_id_ts = {"ts": "", "seq": 0}


def new_run_id(formula_level: str = "custom") -> str:
    """重複しない run_id を生成。ミリ秒＋同一ミリ秒内シーケンスで保証。"""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    if ts == _last_run_id_ts["ts"]:
        _last_run_id_ts["seq"] += 1
        ts = f"{ts}{_last_run_id_ts['seq']:02d}"
    else:
        _last_run_id_ts["ts"] = ts
        _last_run_id_ts["seq"] = 0
    return f"{ts}-{_slugify(formula_level)}"


def list_runs(case_id: str) -> list:
    """案件配下の全検索ランのサマリリストを返す (新しい順)"""
    d = _runs_dir(case_id)
    runs = []
    for p in sorted(d.glob("*.json"), reverse=True):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            runs.append({
                "run_id": data.get("run_id") or p.stem,
                "created_at": data.get("created_at", ""),
                "source": data.get("source", ""),
                "formula_level": data.get("formula_level", ""),
                "formula": data.get("formula", ""),
                "hit_count": data.get("hit_count", len(data.get("hits", []))),
                "status": data.get("status", ""),
                "parent_run_id": data.get("parent_run_id"),
                "stars": sum(1 for h in data.get("hits", [])
                             if h.get("screening") == "star"),
                "rejects": sum(1 for h in data.get("hits", [])
                               if h.get("screening") == "reject"),
            })
        except Exception:
            continue
    return runs


_PID_RECOVERY_PATTERNS = [
    re.compile(r'(特開\s*\d{4}\s*[-ー]\s*\d+)'),
    re.compile(r'(特表\s*\d{4}\s*[-ー]\s*\d+)'),
    re.compile(r'(再(?:公)?表\s*\d{4}\s*[-ー/／]\s*\d+)'),
    re.compile(r'(特許\s*第?\s*\d+(?:号)?)'),
    re.compile(r'(WO\s*\d{4}\s*[/／-]?\s*\d+)'),
    re.compile(r'(JP\s*\d{4}[-]?\d{6}\s*[AB]\d?)', re.IGNORECASE),
    re.compile(r'(JP\s*\d{5,8}\s*B\d?)', re.IGNORECASE),
]


def _recover_pid_from_text(*texts) -> str:
    """row_text や title 等から公開番号を抽出。最初に見つかったものを返す。"""
    for t in texts:
        if not t:
            continue
        for pat in _PID_RECOVERY_PATTERNS:
            m = pat.search(t)
            if m:
                return re.sub(r'\s+', '', m.group(1))
    return ""


def _heal_run_hits(data: dict) -> bool:
    """ヒット中の patent_id 欠損を title / row_text から救出。書き換えがあれば True。"""
    changed = False
    for h in (data.get("hits") or []):
        pid = (h.get("patent_id") or "").strip()
        if pid:
            continue
        # title が公開番号らしき文字列なら昇格
        recovered = _recover_pid_from_text(h.get("title"), h.get("row_text"))
        if recovered:
            h["patent_id"] = recovered
            # title が公開番号と同一なら、タイトル欄として不適なのでクリア
            if h.get("title") and recovered.replace(" ", "") in h["title"].replace(" ", ""):
                # 別途 row_text からタイトルを再推定してもよいが、
                # まずは patent_id 復旧だけ行う
                h["title"] = ""
            changed = True
    return changed


def _enrich_hits_from_cache(case_id: str, data: dict) -> bool:
    """各 hit の title / abstract / claim1 が空なら cache (_hit_text) から補完する。

    J-PlatPat スクレイピング側でタイトル抽出に失敗した 再表/特表 系ヒットは
    cache 側 (Google Patents から取得) には完備されていることがあるため、
    load_run 時に自動マージして UI と AI スコアの両方を救う。
    """
    changed = False
    for h in (data.get("hits") or []):
        pid = (h.get("patent_id") or "").strip()
        if not pid:
            continue
        # 既に十分な情報があるならスキップ (要約か請求項1のいずれかが入っていれば OK)
        has_body = bool((h.get("abstract") or "").strip()) or bool((h.get("claim1") or "").strip())
        has_title = bool((h.get("title") or "").strip())
        if has_title and has_body:
            continue
        cached = get_hit_text(case_id, pid)
        if not cached:
            continue
        merged_anything = False
        # title
        if not has_title and cached.get("title"):
            h["title"] = cached["title"]
            merged_anything = True
        # abstract
        if not (h.get("abstract") or "").strip() and cached.get("abstract"):
            h["abstract"] = cached["abstract"]
            merged_anything = True
        # claim1: cached.claims が list なら 1 個目を採用
        if not (h.get("claim1") or "").strip():
            cl = cached.get("claims")
            if isinstance(cl, list) and cl:
                h["claim1"] = str(cl[0])
                merged_anything = True
            elif isinstance(cl, str) and cl:
                h["claim1"] = cl
                merged_anything = True
        # applicant: 出願人が空で cache にあれば補完
        if not (h.get("applicant") or "").strip() and cached.get("assignee"):
            h["applicant"] = cached["assignee"]
            merged_anything = True
        if merged_anything:
            changed = True
            # 古い AI スコアは「情報不足」前提で付けられた可能性が高いので無効化。
            # 次の「AI関連度スコア」実行時に enriched データで再評価される。
            if h.get("ai_score") is not None:
                h["ai_score"] = None
                h["ai_reason"] = (h.get("ai_reason") or "") + " [enriched: needs rescore]"
    return changed


def load_run(case_id: str, run_id: str) -> Optional[dict]:
    p = _runs_dir(case_id) / f"{run_id}.json"
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    # 古いランで再表がスラッシュ区切りのため patent_id 取得に失敗していたものを救出
    healed = _heal_run_hits(data)
    # 全文 cache に title/abstract/claim1 がある hit を自動マージ
    enriched = _enrich_hits_from_cache(case_id, data)
    if healed or enriched:
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
    return data


def save_run(case_id: str, data: dict) -> Path:
    run_id = data.get("run_id")
    if not run_id:
        raise ValueError("run_id is required")
    data["updated_at"] = _now_iso()
    p = _runs_dir(case_id) / f"{run_id}.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return p


def delete_run(case_id: str, run_id: str) -> bool:
    p = _runs_dir(case_id) / f"{run_id}.json"
    if p.exists():
        p.unlink()
        return True
    return False


def create_run_from_hits(
    case_id: str,
    *,
    formula: str,
    formula_level: str = "custom",
    source: str = "jplatpat",
    hits: Iterable[dict],
    search_url: str = "",
    status: str = "done",
    error: Optional[str] = None,
    parent_run_id: Optional[str] = None,
) -> dict:
    """JplatpatHit / 各種 hit dict からランを生成して保存。

    parent_run_id: この run が別ランをコピー編集して再実行されたものであるとき
                   親ラン id を記録する。差分表示に利用。
    """
    run_id = new_run_id(formula_level)
    normalized_hits = [_normalize_hit(h) for h in hits]
    data = {
        "run_id": run_id,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "source": source,
        "formula_level": formula_level,
        "formula": formula,
        "search_url": search_url,
        "hit_count": len(normalized_hits),
        "status": status,
        "error": error,
        "parent_run_id": parent_run_id,
        "hits": normalized_hits,
    }
    save_run(case_id, data)
    return data


def _normalize_hit(h) -> dict:
    """JplatpatHit や dict を内部標準形に整える。"""
    if hasattr(h, "to_dict"):
        d = h.to_dict()
    elif isinstance(h, dict):
        d = dict(h)
    else:
        d = {"row_text": str(h)}

    return {
        "patent_id": d.get("patent_id", "") or "",
        "title": d.get("title", "") or "",
        "applicant": d.get("applicant", "") or "",
        "publication_date": d.get("publication_date", "") or "",
        "ipc": list(d.get("ipc") or []),
        "fi": list(d.get("fi") or []),
        "fterm": list(d.get("fterm") or []),
        "url": d.get("url", "") or "",
        "abstract": d.get("abstract"),
        "claim1": d.get("claim1"),
        "ai_score": d.get("ai_score"),
        "ai_reason": d.get("ai_reason"),
        "screening": d.get("screening", "pending"),
        "note": d.get("note", ""),
        "downloaded_as_citation": bool(d.get("downloaded_as_citation", False)),
        "row_text": d.get("row_text", ""),
    }


def update_screening(
    case_id: str, run_id: str, patent_id: str, screening: str, note: Optional[str] = None
) -> Optional[dict]:
    """特定候補のスクリーニング状態を更新。"""
    if screening not in SCREENING_STATES:
        raise ValueError(f"invalid screening state: {screening}")
    data = load_run(case_id, run_id)
    if not data:
        return None
    updated = False
    for h in data.get("hits", []):
        if h.get("patent_id") == patent_id:
            h["screening"] = screening
            if note is not None:
                h["note"] = note
            updated = True
            break
    if updated:
        save_run(case_id, data)
        return data
    return None


def bulk_update_screening(
    case_id: str, run_id: str, updates: list
) -> Optional[dict]:
    """一括更新。updates = [{"patent_id": "...", "screening": "...", "note": "..."}, ...]"""
    data = load_run(case_id, run_id)
    if not data:
        return None
    index = {h.get("patent_id"): h for h in data.get("hits", [])}
    for u in updates:
        pid = u.get("patent_id")
        target = index.get(pid)
        if not target:
            continue
        scr = u.get("screening")
        if scr and scr in SCREENING_STATES:
            target["screening"] = scr
        if "note" in u:
            target["note"] = u.get("note") or ""
    save_run(case_id, data)
    return data


def _screening_match_keys(patent_id: str) -> set[str]:
    """検索ヒットと引用IDの表記ゆれを吸収する比較キー。"""
    from modules.citation_id import normalize_citation_id

    raw = str(patent_id or "").strip()
    if not raw:
        return set()
    variants = {raw}
    try:
        variants.add(normalize_citation_id(raw))
    except Exception:
        pass
    try:
        variants.add(_normalize_hit_text_patent_id(raw))
    except Exception:
        pass
    keys = set()
    for v in variants:
        s = str(v or "").strip().upper()
        if not s:
            continue
        keys.add(s)
        keys.add(re.sub(r"[\s\-/／_ー－.,，．・]", "", s))
    return {k for k in keys if k}


def hold_patents_across_runs(
    case_id: str, patent_ids: list[str], note: Optional[str] = None
) -> dict:
    """指定文献を全検索ラン内で `hold` に移し、通常候補から外せるようにする。"""
    requested = [str(pid).strip() for pid in (patent_ids or []) if str(pid or "").strip()]
    target_keys = {k for pid in requested for k in _screening_match_keys(pid)}
    matched_requested: set[str] = set()
    updated_runs = []
    updated_total = 0
    if not target_keys:
        return {"requested": 0, "updated": 0, "runs": [], "not_found": []}

    for path in sorted(_runs_dir(case_id).glob("*.json")):
        data = load_run(case_id, path.stem)
        if not data:
            continue
        run_updated = 0
        for hit in data.get("hits", []):
            hit_keys = _screening_match_keys(hit.get("patent_id", ""))
            if not (hit_keys & target_keys):
                continue
            if hit.get("screening") != "hold":
                hit["screening"] = "hold"
                if note is not None:
                    hit["note"] = note
                run_updated += 1
                updated_total += 1
            for pid in requested:
                if _screening_match_keys(pid) & hit_keys:
                    matched_requested.add(pid)
        if run_updated:
            save_run(case_id, data)
            updated_runs.append({"run_id": data.get("run_id") or path.stem, "updated": run_updated})

    return {
        "requested": len(requested),
        "updated": updated_total,
        "runs": updated_runs,
        "not_found": [pid for pid in requested if pid not in matched_requested],
    }


def _citation_card_hit_score(hit: dict, run_created_at: str = "") -> tuple:
    """引用文献カードに採用する検索ヒットの優先度。"""
    ai = hit.get("ai_score")
    try:
        ai_num = float(ai) if ai is not None else -1.0
    except (TypeError, ValueError):
        ai_num = -1.0
    return (
        _screening_priority(hit.get("screening")),
        ai_num,
        1 if hit.get("claim1") else 0,
        1 if hit.get("abstract") else 0,
        str(run_created_at or ""),
    )


def build_citation_card_hits(case_id: str, items: list[dict]) -> list[dict]:
    """Step 6 の引用文献を Step 4.5 と同じ候補カード形式で表示するための hit 群。

    items = [{"id": "WO...", "aliases": ["表示ラベル", ...]}, ...]
    検索ランに同一文献があれば AI スコア・キーワード集計元のメタ情報を流用し、
    見つからない場合も簡易カードとして返す。
    """
    normalized_items = []
    key_to_indexes: dict[str, set[int]] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or "").strip()
        if not pid:
            continue
        aliases = [pid]
        for a in item.get("aliases") or []:
            s = str(a or "").strip()
            if s and s not in aliases:
                aliases.append(s)
        idx = len(normalized_items)
        normalized_items.append({"id": pid, "aliases": aliases})
        for alias in aliases:
            for key in _screening_match_keys(alias):
                key_to_indexes.setdefault(key, set()).add(idx)

    best: list[Optional[dict]] = [None] * len(normalized_items)
    best_score: list[tuple] = [(-1, -1.0, 0, 0, "")] * len(normalized_items)

    for path in sorted(_runs_dir(case_id).glob("*.json")):
        data = load_run(case_id, path.stem)
        if not data:
            continue
        run_id = data.get("run_id") or path.stem
        run_created = data.get("created_at", "")
        for hit in data.get("hits", []):
            hit_keys = _screening_match_keys(hit.get("patent_id", ""))
            indexes = set()
            for key in hit_keys:
                indexes.update(key_to_indexes.get(key, set()))
            if not indexes:
                continue
            score = _citation_card_hit_score(hit, run_created)
            for idx in indexes:
                if score < best_score[idx]:
                    continue
                h = dict(hit)
                h["_source_run_id"] = run_id
                h["_source_formula_level"] = data.get("formula_level", "")
                h["_source_run_created_at"] = run_created
                h["_citation_id"] = normalized_items[idx]["id"]
                h["_citation_card_status"] = "matched"
                best[idx] = h
                best_score[idx] = score

    out = []
    for idx, item in enumerate(normalized_items):
        if best[idx] is not None:
            out.append(best[idx])
            continue
        local_text = get_hit_text(case_id, item["id"])
        if not local_text or not local_text.get("title"):
            local_text = _build_hit_text_from_citation(case_id, item["id"]) or local_text
        title = ""
        for alias in item["aliases"]:
            if alias != item["id"]:
                title = alias
                break
        if local_text and local_text.get("title"):
            title = local_text.get("title") or title
        h = _normalize_hit({
            "patent_id": item["id"],
            "title": title,
            "screening": "pending",
            "downloaded_as_citation": _case_input_pdf_exists(case_id, item["id"]),
        })
        h["_citation_id"] = item["id"]
        h["_citation_card_status"] = "fallback"
        out.append(h)
    return out


def merge_runs(case_id: str, run_ids: list) -> list:
    """複数ランの hits をマージし重複排除 (Phase2 向け)。

    重複判定: patent_id の国コード+数字部分 (特開2023-123456 → JP2023123456)
    """
    from modules.jplatpat_client import JplatpatHit

    seen = {}
    merged = []
    for rid in run_ids:
        data = load_run(case_id, rid)
        if not data:
            continue
        for h in data.get("hits", []):
            # 重複キー
            pid = h.get("patent_id", "")
            tmp = JplatpatHit(patent_id=pid)
            key = tmp.dedup_key or pid
            if key in seen:
                # スクリーニング状態やスコアを優先度の高いもので上書き (star > triangle > pending > hold > reject)
                existing = seen[key]
                if _screening_priority(h.get("screening")) > _screening_priority(existing.get("screening")):
                    existing["screening"] = h.get("screening", "pending")
                    existing["note"] = h.get("note", existing.get("note", ""))
                if h.get("ai_score") is not None:
                    if existing.get("ai_score") is None or h.get("ai_score") > existing.get("ai_score"):
                        existing["ai_score"] = h.get("ai_score")
                        existing["ai_reason"] = h.get("ai_reason")
                existing.setdefault("found_in_runs", []).append(rid)
                continue
            hit_copy = dict(h)
            hit_copy["found_in_runs"] = [rid]
            seen[key] = hit_copy
            merged.append(hit_copy)
    return merged


def _screening_priority(state) -> int:
    return {
        "star": 5,
        "triangle": 4,
        "pending": 3,
        "hold": 2,
        "reject": 1,
    }.get(state or "pending", 0)


def get_starred_patent_ids(case_id: str, run_ids: Optional[list] = None) -> list:
    """☆マークの付いた候補の patent_id リストを返す (未 DL のみ)。"""
    pids = []
    if run_ids is None:
        run_ids = [r["run_id"] for r in list_runs(case_id)]
    for rid in run_ids:
        data = load_run(case_id, rid)
        if not data:
            continue
        for h in data.get("hits", []):
            if h.get("screening") == "star" and not h.get("downloaded_as_citation"):
                pid = h.get("patent_id")
                if pid and pid not in pids:
                    pids.append(pid)
    return pids


def mark_downloaded(
    case_id: str, run_id: str, patent_id: str, downloaded: bool = True
) -> None:
    data = load_run(case_id, run_id)
    if not data:
        return
    for h in data.get("hits", []):
        if h.get("patent_id") == patent_id:
            h["downloaded_as_citation"] = downloaded
    save_run(case_id, data)


def enrich_run(case_id: str, run_id: str, limit: int = 20) -> Optional[dict]:
    """Google Patents の詳細ページから要約と請求項1を取得して hits を埋める。

    既に abstract / claim1 が入っている hit はスキップ。
    """
    from modules.google_patents_scraper import fetch_patent_detail

    data = load_run(case_id, run_id)
    if not data:
        return None

    enriched_count = 0
    for h in data.get("hits", []):
        if enriched_count >= limit:
            break
        if h.get("abstract") and h.get("claim1"):
            continue
        pid = h.get("patent_id") or ""
        if not pid:
            continue
        try:
            detail = fetch_patent_detail(pid)
        except Exception:
            detail = {}
        if detail.get("abstract"):
            h["abstract"] = detail["abstract"]
        if detail.get("claim1"):
            h["claim1"] = detail["claim1"]
        if detail.get("title") and not h.get("title"):
            h["title"] = detail["title"]
        if detail.get("assignee") and not h.get("applicant"):
            h["applicant"] = detail["assignee"]
        enriched_count += 1

    save_run(case_id, data)
    return data


def ai_score_run(case_id: str, run_id: str, limit: Optional[int] = None,
                 model: Optional[str] = None) -> Optional[dict]:
    """Claude を使って本願との関連度スコア (0-100) を付与する。

    本願の claim1 / 発明の名称を入力として各 hit の title+abstract+claim1 を
    評価し ai_score と ai_reason を書き込む。

    Parameters:
        limit: 処理する hit の上限。None なら未スコアの全件。
        model: 'opus'/'sonnet'/'haiku' またはフル ID。None なら CLI 既定。
    """
    from modules.claude_client import call_claude, ClaudeClientError

    data = load_run(case_id, run_id)
    if not data:
        return None

    # 本願情報を取得
    case_dir = get_case_dir(case_id)
    hongan_summary = _build_hongan_summary(case_dir)

    scored_count = 0
    for h in data.get("hits", []):
        if limit is not None and scored_count >= limit:
            break
        if h.get("ai_score") is not None:
            continue
        prompt = _build_scoring_prompt(hongan_summary, h)
        try:
            raw = call_claude(prompt, timeout=120, use_search=False, model=model)
        except (ClaudeClientError, Exception) as e:
            h["ai_reason"] = f"scoring error: {e}"
            continue

        score, reason = _parse_scoring_response(raw)
        if score is not None:
            h["ai_score"] = score
            h["ai_reason"] = reason
            scored_count += 1

    # 降順ソート用情報を追加
    save_run(case_id, data)
    return data


def ai_score_run_stream(case_id: str, run_id: str, limit: Optional[int] = None,
                        model: Optional[str] = None):
    """AI 関連度スコアを 1 件ずつ計算し、完了ごとに dict を yield する。

    画面側で「全件完了まで待つ」のではなく、計算できた hit からカードを更新する
    ためのストリーミング版。各 hit の処理後に run JSON を保存する。
    """
    from modules.claude_client import call_claude, ClaudeClientError

    data = load_run(case_id, run_id)
    if not data:
        yield {"type": "error", "error": "検索ランが見つかりません"}
        return

    case_dir = get_case_dir(case_id)
    hongan_summary = _build_hongan_summary(case_dir)
    hits = data.get("hits", [])
    targets = [
        (i, h) for i, h in enumerate(hits)
        if h.get("ai_score") is None and not str(h.get("ai_reason") or "").startswith("scoring error:")
    ]
    if limit is not None:
        targets = targets[:max(0, int(limit))]

    total = len(targets)
    yield {"type": "start", "total": total, "run_id": run_id}

    done = 0
    scored = 0
    failed = 0
    for idx, h in targets:
        prompt = _build_scoring_prompt(hongan_summary, h)
        ok = False
        try:
            raw = call_claude(prompt, timeout=120, use_search=False, model=model)
            score, reason = _parse_scoring_response(raw)
            if score is not None:
                h["ai_score"] = score
                h["ai_reason"] = reason
                ok = True
                scored += 1
            else:
                h["ai_reason"] = f"parse error: {reason}"
                failed += 1
        except (ClaudeClientError, Exception) as e:
            h["ai_reason"] = f"scoring error: {e}"
            failed += 1

        done += 1
        save_run(case_id, data)
        yield {
            "type": "score",
            "ok": ok,
            "index": idx,
            "patent_id": h.get("patent_id", ""),
            "hit": h,
            "done": done,
            "total": total,
            "scored": scored,
            "failed": failed,
        }

    yield {
        "type": "done",
        "success": True,
        "run": data,
        "done": done,
        "total": total,
        "scored": scored,
        "failed": failed,
    }


def _build_hongan_summary(case_dir: Path) -> dict:
    """本願の title + claim1 + summary を集約。"""
    summary = {"title": "", "claim1": "", "summary": ""}

    # case.yaml からタイトル
    yaml_path = case_dir / "case.yaml"
    if yaml_path.exists():
        import yaml as _yaml
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                meta = _yaml.safe_load(f) or {}
            summary["title"] = meta.get("patent_title") or meta.get("title") or ""
        except Exception:
            pass

    # segments.json から claim1
    seg_path = case_dir / "segments.json"
    if seg_path.exists():
        try:
            with open(seg_path, "r", encoding="utf-8") as f:
                segs = json.load(f)
            for c in segs:
                if c.get("claim_number") == 1:
                    parts = [s.get("text", "") for s in c.get("segments", [])]
                    summary["claim1"] = " ".join(parts)[:1200]
                    break
        except Exception:
            pass

    return summary


def _build_scoring_prompt(hongan: dict, hit: dict) -> str:
    return f"""あなたは特許先行技術調査の専門家です。下記の「本願」と「候補文献」について、
本願の新規性・進歩性否定に影響しうる関連度を 0〜100 のスコアで評価してください。
スコア基準:
- 90-100: 主引例になりうる (同一技術分野+構成要素のほとんどが開示)
- 70-89:  副引例になりうる (一部構成要素が開示)
- 40-69:  周辺技術として参考になる
- 0-39:   関連性が低い

## 本願
- 発明の名称: {hongan.get("title", "")}
- 請求項1: {hongan.get("claim1", "")[:800]}

## 候補文献
- 文献番号: {hit.get("patent_id", "")}
- タイトル: {hit.get("title", "")}
- 出願人: {hit.get("applicant", "")}
- 要約: {(hit.get("abstract") or "")[:800]}
- 請求項1: {(hit.get("claim1") or "")[:800]}

## 出力 (厳密に以下の JSON のみ)
{{"score": <0-100>, "reason": "<理由を60字以内>"}}
"""


def _parse_scoring_response(raw: str):
    """Claude の応答から score, reason を抽出。"""
    try:
        # コードフェンス除去
        txt = raw.strip()
        m = re.search(r'\{[^{}]*"score"[^{}]*\}', txt, re.DOTALL)
        if not m:
            return None, txt[:200]
        data = json.loads(m.group(0))
        score = int(data.get("score"))
        reason = str(data.get("reason", ""))[:200]
        return max(0, min(100, score)), reason
    except Exception:
        return None, raw[:200]


def _hit_key(hit: dict) -> str:
    """hit の重複判定キー (patent_id ベース、dedup_key で国別正規化)。"""
    from modules.jplatpat_client import JplatpatHit
    pid = hit.get("patent_id", "") or ""
    try:
        tmp = JplatpatHit(patent_id=pid)
        return tmp.dedup_key or pid
    except Exception:
        return pid


def compute_run_diff(case_id: str, run_id: str, base_run_id: str) -> Optional[dict]:
    """2つのランの hits を比較して差分を返す。

    Returns:
        {
          "run_id": "...",                  # 比較対象 (新)
          "base_run_id": "...",             # 比較基準 (旧)
          "formula": "...",
          "base_formula": "...",
          "common": [{"patent_id": ...}, ...],     # 両方にある
          "only_new": [{"patent_id": ...}, ...],   # run_id だけにある (新規追加)
          "only_base": [{"patent_id": ...}, ...],  # base_run_id だけにある (消失)
          "summary": {"common": N, "added": N, "removed": N},
        }
        ランが見つからなければ None。
    """
    target = load_run(case_id, run_id)
    base = load_run(case_id, base_run_id)
    if not target or not base:
        return None

    target_hits = target.get("hits", []) or []
    base_hits = base.get("hits", []) or []

    target_map = {_hit_key(h): h for h in target_hits if _hit_key(h)}
    base_map = {_hit_key(h): h for h in base_hits if _hit_key(h)}

    target_keys = set(target_map.keys())
    base_keys = set(base_map.keys())

    common_keys = target_keys & base_keys
    only_new_keys = target_keys - base_keys
    only_base_keys = base_keys - target_keys

    def _slim(h: dict) -> dict:
        return {
            "patent_id": h.get("patent_id", ""),
            "title": h.get("title", ""),
            "applicant": h.get("applicant", ""),
            "publication_date": h.get("publication_date", ""),
            "screening": h.get("screening", "pending"),
            "ai_score": h.get("ai_score"),
        }

    return {
        "run_id": run_id,
        "base_run_id": base_run_id,
        "formula": target.get("formula", ""),
        "base_formula": base.get("formula", ""),
        "common": [_slim(target_map[k]) for k in common_keys],
        "only_new": [_slim(target_map[k]) for k in only_new_keys],
        "only_base": [_slim(base_map[k]) for k in only_base_keys],
        "summary": {
            "common": len(common_keys),
            "added": len(only_new_keys),
            "removed": len(only_base_keys),
        },
    }


def validate_formula(formula: str) -> dict:
    """検索式の括弧バランス・構文簡易チェック (J-PlatPat 論理式入力用)。

    J-PlatPat 構文:
      - AND: *  (または半角スペース)
      - OR : +
      - NOT: -  (半角ハイフン)
      - 優先順位変更: [ ] (大括弧、三重まで)
      - 同種キーワード群の省略: ( ) (丸括弧) 例: (a+b+c)/TX
      - 検索キーワード末尾に構造タグ必須: /TX /TI /AB /CL /FI /FT ...

    Returns:
        {"ok": bool, "errors": [str], "warnings": [str],
         "parens_balance": int, "brackets_balance": int}
    """
    errors: list = []
    warnings: list = []
    s = formula or ""

    # 丸括弧バランス
    paren_depth = 0
    paren_min = 0
    for ch in s:
        if ch in "(（":
            paren_depth += 1
        elif ch in ")）":
            paren_depth -= 1
            if paren_depth < 0:
                paren_min = min(paren_min, paren_depth)
    if paren_depth != 0:
        errors.append(f"丸括弧のバランスが崩れています (深さ {paren_depth:+d})")
    if paren_min < 0:
        errors.append("閉じ丸括弧が開き丸括弧より先に現れています")

    # 大括弧バランス
    br_depth = 0
    br_min = 0
    br_max_nest = 0
    for ch in s:
        if ch == "[":
            br_depth += 1
            br_max_nest = max(br_max_nest, br_depth)
        elif ch == "]":
            br_depth -= 1
            if br_depth < 0:
                br_min = min(br_min, br_depth)
    if br_depth != 0:
        errors.append(f"大括弧 [ ] のバランスが崩れています (深さ {br_depth:+d})")
    if br_min < 0:
        errors.append("閉じ大括弧が開き大括弧より先に現れています")
    if br_max_nest > 3:
        warnings.append(f"大括弧の入れ子が {br_max_nest} 重です (J-PlatPat は三重まで)")

    # 全角演算子警告 (J-PlatPat は半角のみサポート)
    if re.search(r'[＊＋－]', s):
        warnings.append("全角の演算子 (＊ ＋ －) が含まれます。J-PlatPat は半角 (* + -) 必須です。")
    if re.search(r'[（）]', s):
        warnings.append("全角括弧 （ ） が含まれます。半角 ( ) 推奨です。")

    # 連続演算子の簡易検出 (AND=* OR=+ NOT=- の連続)
    if re.search(r'[*+\-]\s*[*+]', s):
        errors.append("演算子が連続しています (例: *+ など)")

    # NOT (/) の古い誤用検出
    #   構造タグとして使われる /XX (大文字) はOK、
    #   分類コードのスラッシュ (B32B1/00 等) もOK、
    #   オペレータ位置で /語 のように使っていないか確認
    if re.search(r'\s/\s*\S', s) and not re.search(r'\s/[A-Z]{2,4}', s):
        warnings.append("NOT 演算子は ' / ' ではなく半角ハイフン '-' です")

    # キーワード内ハイフンの誤用検出
    #   J-PlatPat では '-' は NOT 演算子。キーワードの一部として使いたい場合は全角 '－' にする必要がある。
    #   例: "SUS-304" "フィルム-電池" は "SUS－304" "フィルム－電池" と全角化しないとエラーになる。
    word_class = r'[\w\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]'
    if re.search(rf'{word_class}-{word_class}', s):
        warnings.append(
            "キーワードの途中にある半角 '-' は NOT 演算子と解釈されます。"
            "キーワードの一部なら全角 '－' に変換してください (🔧 式を自動修正で対応)"
        )

    # 構造タグ不足の検出 (論理式入力では必須)
    #   「キーワード)」や「キーワード]」の直後に /XX が無ければ警告。
    #   分類コード (B32B1/00 等) のスラッシュは構造タグと誤判定しないよう、
    #   ')' または ']' 直後の /[A-Z]{2,4} のみを構造タグとみなす。
    tag_pattern = re.compile(r'^\s*/[A-Z]{2,4}(?:\+[A-Z]{2,4})*\b')
    close_chars = list(re.finditer(r'[)\]]', s))
    missing_tag = False
    for m in close_chars:
        after = s[m.end():m.end() + 10]
        # 次が構造タグ → OK
        if tag_pattern.match(after):
            continue
        # 次が閉じ括弧 → 上位のグルーピング、個別判定は不要
        if re.match(r'\s*[\])]', after):
            continue
        # 次が ,数字C/N, (近傍検索の第2キーワード前) → OK
        if re.match(r'\s*,\d+[CcNn],', after):
            continue
        missing_tag = True
        break
    if missing_tag:
        warnings.append(
            "キーワード括弧の直後に構造タグ (/TX /TI /AB /CL /FI /FT など) が無い可能性があります"
        )

    # 構造タグの '+' 連結 (例: /AB+CL) は J-PlatPat で構文エラーになる
    composite_tag_matches = re.findall(r'/[A-Z]{2,4}\+[A-Z]{2,4}(?:\+[A-Z]{2,4})*', formula)
    if composite_tag_matches:
        bad = ", ".join(sorted(set(composite_tag_matches)))
        errors.append(
            f"構造タグの '+' 連結は J-PlatPat で構文エラーになります: {bad} "
            "→ (キーワード)/AB+(キーワード)/CL の形に書き直すか、片方 (例: /CL) のみにしてください"
        )

    # Fタームのフルコード直指定は J-PlatPat の論理式でエラーになりやすい。
    # 例: 4C083AC172/FT → 4C083/FC*AC17.2/FT
    # 例: 4F100AK01B/FT → 4F100/FC*AK01B/FT
    full_ft_matches = re.findall(r'\b\d[A-Z]\d{3}[A-Z]{2}\d{2,3}[A-Z]?/FT\b', s, flags=re.I)
    if full_ft_matches:
        hints = []
        try:
            from services.search_formula_builder import parse_fterm_code
            for m in sorted(set(full_ft_matches))[:5]:
                parsed = parse_fterm_code(m[:-3])
                if parsed and parsed.get("theme"):
                    hints.append(f"{m} → {parsed['theme']}/FC*{parsed['query_code']}/FT")
        except Exception:
            pass
        msg = "Fタームはテーマコードと分けて指定してください。フルコードのまま /FT に入れるとエラーになります。"
        if hints:
            msg += " 例: " + " / ".join(hints)
        warnings.append(msg)

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "parens_balance": paren_depth,
        "brackets_balance": br_depth,
    }


def get_formulas_from_keyword_dict(case_id: str) -> dict:
    """Stage 3 の keyword_dictionary.json から search_formulas を取得。

    Returns:
        {"narrow": {"formula_jplatpat": "...", "formula_google_patents": "...", "description": "..."}, ...}
        取得できなければ空 dict。
    """
    case_dir = get_case_dir(case_id)
    p = case_dir / "search" / "keyword_dictionary.json"
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    sf = data.get("search_formulas") or {}
    if not isinstance(sf, dict):
        return {}
    return sf


def get_keyword_snippets(case_id: str) -> dict:
    """Step3 の keyword_dictionary から、エディタに挿入するための語彙スニペットを返す。

    Returns:
        {
          "groups": [
            {"label": "<意味カテゴリ>",
             "terms": ["語1", "語2", ...],
             "jplatpat_group": "(語1+語2+...)"},
            ...
          ],
          "fi_codes": ["B32B1/00", ...],
          "fterm_codes": ["4F100AB01", ...],
        }
    """
    case_dir = get_case_dir(case_id)
    snippets: dict = {
        "groups": [],
        "ipc_codes": [],
        "fi_codes": [],
        "fterm_codes": [],
        "theme_codes": [],
    }

    # 優先: Stage 3 が生成した keyword_dictionary.json (より洗練された語彙)
    # フォールバック: Step 3 の keywords.json (ユーザーが Step 3 で直接編集したもの)
    p = case_dir / "search" / "keyword_dictionary.json"
    data = None
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = None
    if not data or (not data.get("keyword_groups") and not data.get("groups")):
        # Step 3 の keywords.json から再構成
        kw_path = case_dir / "keywords.json"
        if kw_path.exists():
            try:
                with open(kw_path, "r", encoding="utf-8") as f:
                    kw_groups = json.load(f) or []
            except Exception:
                kw_groups = []
            if kw_groups:
                # keywords.json のスキーマ → keyword_dictionary 互換に変換
                data = {
                    "keyword_groups": [
                        {
                            "label": g.get("label") or f"group{g.get('group_id', '')}",
                            "terms": [
                                kw.get("term", "")
                                for kw in (g.get("keywords") or [])
                                if isinstance(kw, dict) and kw.get("term")
                            ],
                            "search_codes": g.get("search_codes") or {},
                        }
                        for g in kw_groups
                        if isinstance(g, dict)
                    ],
                    "ipc": [
                        c
                        for g in kw_groups
                        for c in ((g.get("search_codes") or {}).get("ipc") or [])
                    ],
                    "fi": [
                        c
                        for g in kw_groups
                        for c in ((g.get("search_codes") or {}).get("fi") or [])
                    ],
                    "fterm": [
                        c
                        for g in kw_groups
                        for c in ((g.get("search_codes") or {}).get("fterm") or [])
                    ],
                }
    if not data:
        return snippets

    # キーワード内ハイフンは J-PlatPat では NOT 扱いになるため全角 '－' に変換
    def _sanitize_keyword(term: str) -> str:
        # 両端がワード文字の '-' を '－' に変換 (反復)
        pattern = re.compile(
            r'([\w\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF])-'
            r'([\w\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF])'
        )
        prev = None
        t = term
        while prev != t:
            prev = t
            t = pattern.sub(r'\1－\2', t)
        return t

    def _collect_codes(val):
        """入力が list[str] / list[{code:..}] / str のいずれでもコード文字列一覧を返す。"""
        out: list[str] = []
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    c = item.get("code") or item.get("id") or ""
                elif isinstance(item, str):
                    c = item
                else:
                    c = ""
                c = str(c).strip()
                if c:
                    out.append(c)
        elif isinstance(val, str) and val.strip():
            out.append(val.strip())
        return out

    group_ipc_codes: list[str] = []
    group_fi_codes: list[str] = []
    group_fterm_codes: list[str] = []
    groups_raw = data.get("keyword_groups") or data.get("groups") or []
    for g in groups_raw:
        if not isinstance(g, dict):
            continue
        label = g.get("label") or g.get("name") or g.get("category") or ""
        terms = g.get("terms") or g.get("synonyms") or g.get("keywords") or []
        clean_terms = []
        for t in terms:
            if isinstance(t, dict):
                term = t.get("term") or t.get("text") or t.get("label") or ""
            else:
                term = t
            term = str(term).strip()
            if term:
                clean_terms.append(term)
        terms = clean_terms

        code_src = g.get("search_codes") or g.get("classifications") or {}
        grp_ipc = _collect_codes(code_src.get("ipc") or g.get("ipc") or g.get("ipc_codes"))
        grp_fi = _collect_codes(code_src.get("fi") or g.get("fi") or g.get("fi_codes"))
        grp_ft = _collect_codes(code_src.get("fterm") or g.get("fterm") or g.get("fterm_codes"))
        group_ipc_codes += grp_ipc
        group_fi_codes += grp_fi
        group_fterm_codes += grp_ft

        if not terms and not (grp_ipc or grp_fi or grp_ft):
            continue
        # キーワード内 '-' を '－' に
        sanitized = [_sanitize_keyword(t) for t in terms]
        raw = "(" + "+".join(sanitized) + ")" if sanitized else ""
        snippets["groups"].append({
            "label": label,
            "terms": terms,  # 表示用は元のまま
            "terms_sanitized": sanitized,
            # 構造タグなし (他の式に組み込む用途)
            "jplatpat_group_raw": raw,
            # 既定は全文検索タグ付き (J-PlatPat 論理式入力で有効な形)
            "jplatpat_group": raw + "/TX" if raw else "",
            "ipc_codes": grp_ipc,
            "fi_codes": grp_fi,
            "fterm_codes": grp_ft,
        })

    ipc_codes = _collect_codes(data.get("ipc") or data.get("ipc_codes")) + group_ipc_codes
    fi_codes = _collect_codes(data.get("fi") or data.get("fi_codes")) + group_fi_codes
    fterm_codes = _collect_codes(data.get("fterm") or data.get("fterm_codes")) + group_fterm_codes

    # Stage 3 の classification.json もマージ (fi/fterm が構造化され別ファイルの場合)
    cl = case_dir / "search" / "classification.json"
    if cl.exists():
        try:
            with open(cl, "r", encoding="utf-8") as f:
                cdata = json.load(f)
            ipc_codes += _collect_codes(cdata.get("ipc"))
            fi_codes += _collect_codes(cdata.get("fi"))
            fterm_codes += _collect_codes(cdata.get("fterm"))
        except Exception:
            pass

    # 重複除去 (順序保持)
    def _uniq(xs: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for x in xs:
            if x and x not in seen:
                seen.add(x)
                out.append(x)
        return out

    snippets["ipc_codes"] = _uniq(ipc_codes)
    snippets["fi_codes"] = _uniq(fi_codes)
    snippets["fterm_codes"] = _uniq(fterm_codes)

    # テーマコード (F-term コード先頭 5 文字のユニーク集合, 例: "4C083AB13" → "4C083")
    theme_set: list[str] = []
    seen: set[str] = set()
    try:
        from services.search_formula_builder import parse_fterm_code
    except Exception:
        parse_fterm_code = None
    for code in snippets["fterm_codes"]:
        parsed = parse_fterm_code(code) if parse_fterm_code else None
        if parsed and parsed.get("theme"):
            th = parsed["theme"]
            if th not in seen:
                seen.add(th)
                theme_set.append(th)
    snippets["theme_codes"] = theme_set

    return snippets
