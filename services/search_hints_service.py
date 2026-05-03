"""予備検索ヒント (hongan_analysis.json section 7) のパーサ + Step 3 適用。

section 7 の 7.2 / 7.3 / 7.4 は LLM が自然文で出力するため、
そのままでは Step 3 / Step 4.5 で機械処理できない。
本サービスは構造化パースと Step 3 キーワードグループへの適用を提供する。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from services.case_service import get_case_dir, load_case_meta


HINT_TYPE_SYNONYM = "hint:同義語"
HINT_TYPE_BROADER = "hint:上位"
HINT_TYPE_NARROWER = "hint:下位"
HINT_TYPES = (HINT_TYPE_SYNONYM, HINT_TYPE_BROADER, HINT_TYPE_NARROWER)

# 7.2 行先頭区切り (≒ / →)
_SEP_SYNONYM = "≒"
_SEP_HIERARCHY = "→"

# 7.3 行のカテゴリプレフィクス
_CLS_PREFIX_RE = re.compile(
    r"^\s*(FI|F[Iｉ]|F\s*ターム|F[ターｰ]+ム|F-?term|CPC|IPC)\s*[:：]\s*(.+)$",
    re.IGNORECASE,
)
# 1 件分のコードと説明 ("A61K 8/36 (界面活性剤を含む化粧料)")
_CODE_RE = re.compile(r"^\s*([0-9A-Za-z][0-9A-Za-z \-/.　]*?)(?:\s*[（(]([^）)]+)[）)])?\s*$")


def _load_hongan_analysis(case_id: str) -> dict | None:
    case_dir = get_case_dir(case_id)
    p = case_dir / "analysis" / "hongan_analysis.json"
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _get_section7_items(ha: dict) -> dict:
    """section 7 の id -> value 辞書を返す。"""
    out = {}
    for sec in ha.get("sections") or []:
        if str(sec.get("id")) != "7":
            continue
        for item in sec.get("items") or []:
            iid = str(item.get("id") or "")
            out[iid] = item.get("value")
    return out


def _split_top(text: str, sep: str) -> tuple[str, str] | None:
    """先頭の区切り (≒ / →) で 1 回だけ分割。"""
    idx = text.find(sep)
    if idx < 0:
        return None
    return text[:idx].strip(), text[idx + len(sep):].strip()


# 丸カッコ内の / や , は分割対象から除外するため一時的にプレースホルダに置換
_PAREN_SLASH = ""
_PAREN_COMMA = ""


def _mask_parens(text: str) -> str:
    """text 内のカッコ深さ > 0 の `/` `,` `、` `，` をプレースホルダに置換。"""
    out = []
    depth = 0
    for ch in text:
        if ch in "(（":
            depth += 1
            out.append(ch)
        elif ch in ")）":
            depth = max(0, depth - 1)
            out.append(ch)
        elif depth > 0:
            if ch == "/":
                out.append(_PAREN_SLASH)
            elif ch in ",、，":
                out.append(_PAREN_COMMA)
            else:
                out.append(ch)
        else:
            out.append(ch)
    return "".join(out)


def _unmask(s: str) -> str:
    return s.replace(_PAREN_SLASH, "/").replace(_PAREN_COMMA, ",")


def _split_terms(rest: str) -> list[tuple[str, str]]:
    """rest を / で区切り、各要素を (kind, term) に分類。

    kind は HINT_TYPE_* のいずれか。ラベル無しは synonym 扱い。
    "下位: A, B, C" のように : 後にカンマ区切りで複数あるケースも展開。
    括弧内の `/` `,` は保護される (例: `(フィトステリル/ベヘニル)` や `1,3-ブチレングリコール` は割らない)。
    """
    masked = _mask_parens(rest)
    # 数値カンマ (1,3- や 2,6- など) も保護
    masked = re.sub(r"(?<=\d)([,、，])(?=\d)", _PAREN_COMMA, masked)
    out = []
    for raw in masked.split("/"):
        seg = raw.strip()
        if not seg:
            continue
        kind = HINT_TYPE_SYNONYM
        body = seg
        m = re.match(r"^(上位|下位|類義|類似|同義)\s*[:：]\s*(.+)$", seg)
        if m:
            label = m.group(1)
            body = m.group(2).strip()
            if label == "上位":
                kind = HINT_TYPE_BROADER
            elif label == "下位":
                kind = HINT_TYPE_NARROWER
            else:
                kind = HINT_TYPE_SYNONYM
        for piece in re.split(r"[,、，]", body):
            term = _unmask(piece).strip()
            if term:
                out.append((kind, term))
    return out


def _parse_synonyms(items_72) -> list[dict]:
    """7.2 同義語・上位/下位概念をパース。

    Returns:
        [{"main": "...", "synonyms": [..], "broader": [..], "narrower": [..]}]
    """
    if not items_72 or not isinstance(items_72, list):
        return []
    out = []
    for raw in items_72:
        line = (str(raw) or "").strip()
        if not line:
            continue
        # ≒ または → で main / rest 分割 (≒ を優先)
        parts = _split_top(line, _SEP_SYNONYM) or _split_top(line, _SEP_HIERARCHY)
        if not parts:
            continue
        main, rest = parts
        if not main:
            continue
        groups = {"main": main, "synonyms": [], "broader": [], "narrower": []}
        for kind, term in _split_terms(rest):
            if kind == HINT_TYPE_BROADER:
                groups["broader"].append(term)
            elif kind == HINT_TYPE_NARROWER:
                groups["narrower"].append(term)
            else:
                groups["synonyms"].append(term)
        out.append(groups)
    return out


def _parse_classifications(items_73) -> dict:
    """7.3 優先度の高い分類コードをパース。

    Returns:
        {"fi": [{code, desc}], "cpc": [...], "ipc": [...], "fterm": [...]}
    """
    out = {"fi": [], "cpc": [], "ipc": [], "fterm": []}
    if not items_73 or not isinstance(items_73, list):
        return out

    def _norm_kind(label: str) -> str:
        s = label.strip().lower().replace(" ", "")
        if s in ("fi", "fｉ"):
            return "fi"
        if "term" in s or "ターム" in s or "tarm" in s:
            return "fterm"
        if s == "cpc":
            return "cpc"
        if s == "ipc":
            return "ipc"
        return ""

    for raw in items_73:
        line = (str(raw) or "").strip()
        if not line:
            continue
        m = _CLS_PREFIX_RE.match(line)
        if not m:
            continue
        kind = _norm_kind(m.group(1))
        if not kind:
            continue
        body = m.group(2).strip()
        # コード, コード, ... のカンマ区切り (全角カンマ含む)
        # ただし "A61K 8/36 (界面活性剤を含む化粧料), A61K 8/37 (...)" のように
        # 説明内に , が無い前提でシンプルに分割。
        for piece in re.split(r"[,、，]", body):
            cm = _CODE_RE.match(piece)
            if not cm:
                continue
            code = cm.group(1).strip()
            desc = (cm.group(2) or "").strip()
            if code:
                out[kind].append({"code": code, "desc": desc})
    return out


def _parse_noise(items_74) -> list[str]:
    if not items_74 or not isinstance(items_74, list):
        return []
    return [str(x).strip() for x in items_74 if str(x).strip()]


def parse_search_hints(case_id: str) -> tuple[dict, int]:
    """hongan_analysis.json の 7.2/7.3/7.4 を構造化して返す。"""
    ha = _load_hongan_analysis(case_id)
    if ha is None:
        return {"error": "本願分析 (Step 2 SUB 3) が未実行です。先に実行してください。"}, 400
    items = _get_section7_items(ha)
    return {
        "synonyms": _parse_synonyms(items.get("7.2")),
        "classifications": _parse_classifications(items.get("7.3")),
        "noise": _parse_noise(items.get("7.4")),
    }, 200


# ------------------------------------------------------------
# Step 3 への適用
# ------------------------------------------------------------

def _norm(s: str) -> str:
    return (s or "").strip().lower()


# label/desc に頻出する汎用接尾辞・かっこ書きを除去して核心語を取り出すための noise パターン
_NOISE_RE = re.compile(
    r"（[^）]*）|\([^)]*\)|"
    r"を含む化粧料|を含有する化粧料|を含有化粧料|を含む|を含有する|含有|含む|"
    r"化粧料|化粧品|組成物|"
    r"性$|の$"
)


_KATAKANA_SEI_KANJI = re.compile(r"([゠-ヿ぀-ゟ])性(?=[一-鿿])")


def _norm_loose(s: str) -> str:
    """汎用接尾辞 / カッコ書き / 末尾の「性」「の」を除去して比較しやすくする。

    また「カナ + 性 + 漢字」(例: アニオン性界面) の中間「性」も削除する。
    「活性剤」(漢字+性+漢字) は残す。

    例:
        "アニオン性界面活性剤（常温固体）" -> "アニオン界面活性剤"
        "アニオン界面活性剤"             -> "アニオン界面活性剤"
        "界面活性剤を含む化粧料"         -> "界面活性剤"
    """
    s = _norm(s)
    # カナ + 性 + 漢字 の「性」のみ削除 (活性剤 等の漢字+性 は影響しない)
    s = _KATAKANA_SEI_KANJI.sub(r"\1", s)
    prev = None
    while prev != s:
        prev = s
        s = _NOISE_RE.sub("", s)
    return s.strip()


def _find_group_for_term(groups: list, target: str) -> dict | None:
    """target が group.label に含まれる/含む / または既存 keyword/synonym と一致するグループを返す。

    優先順位:
        1. label 完全一致 (生)
        2. loose 完全一致 (汎用接尾辞除去)
        3. loose 部分一致 (短い方が長い方に含まれる)
        4. 既存 keyword 一致 (loose 含む)
    """
    tn = _norm(target)
    tloose = _norm_loose(target)
    if not tn:
        return None
    # 1
    for g in groups:
        if _norm(g.get("label")) == tn:
            return g
    # 2
    for g in groups:
        if _norm_loose(g.get("label") or "") == tloose and tloose:
            return g
    # 3
    if tloose:
        for g in groups:
            ll = _norm_loose(g.get("label") or "")
            if ll and (ll in tloose or tloose in ll):
                return g
    # 4
    for g in groups:
        for kw in g.get("keywords") or []:
            kn = _norm(kw.get("term"))
            kl = _norm_loose(kw.get("term") or "")
            if kn == tn or (tloose and kl == tloose):
                return g
    return None


def _add_keyword(group: dict, term: str, kind: str, source: str) -> bool:
    """重複しなければ keywords に追加。追加した場合 True。"""
    if not term:
        return False
    tn = _norm(term)
    for kw in group.get("keywords") or []:
        if _norm(kw.get("term")) == tn:
            return False
    group.setdefault("keywords", []).append({
        "term": term.strip(),
        "type": kind,
        "source": source,
    })
    return True


def _add_classification(group: dict, kind: str, code: str, desc: str) -> bool:
    """search_codes.{kind} に追加。重複は無視。追加した場合 True。"""
    if not code:
        return False
    sc = group.setdefault("search_codes", {})
    bucket = sc.setdefault(kind, [])
    for ex in bucket:
        if (ex.get("code") or "").strip() == code.strip():
            # desc が空なら埋める
            if desc and not ex.get("desc"):
                ex["desc"] = desc
            return False
    bucket.append({"code": code.strip(), "desc": desc.strip()})
    return True


def apply_search_hints_to_keywords(case_id: str) -> tuple[dict, int]:
    """parse_search_hints の結果を keywords.json に反映する。

    7.2 同義語/上位/下位 → 既存グループの keywords に追加 (type=hint:同義語/上位/下位)
    7.3 分類コード → 既存グループの search_codes.{fi|cpc|ipc|fterm} に追加

    マッチしないものは「未分類 (予備検索ヒント)」グループにまとめて入れる。
    UI 側で type が hint:* の語は自動的に赤字ハイライトする。
    """
    parsed, status = parse_search_hints(case_id)
    if status != 200:
        return parsed, status

    from services.keyword_service import _load_keywords, _save_keywords  # noqa: PLC0415
    case_dir = get_case_dir(case_id)
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404
    groups, kw_path = _load_keywords(case_id)
    groups = list(groups or [])
    if kw_path is None:
        kw_path = case_dir / "keywords.json"

    next_id = max((g.get("group_id", 0) for g in groups), default=0) + 1

    def _ensure_unsorted_group() -> dict:
        nonlocal next_id
        for g in groups:
            if (g.get("label") or "").strip() == "予備検索ヒント (未分類)":
                return g
        g = {
            "group_id": next_id,
            "label": "予備検索ヒント (未分類)",
            "segment_ids": [],
            "keywords": [],
            "search_codes": {},
        }
        groups.append(g)
        next_id += 1
        return g

    summary = {
        "synonyms_added": 0,
        "synonyms_skipped_unmatched": 0,
        "codes_added": 0,
        "codes_unmatched_to_unsorted": 0,
        "added_terms_by_group": {},  # {group_id: [{"term", "type"}]}
        "added_codes_by_group": {},  # {group_id: [{"kind", "code"}]}
    }

    def _track_term(g, term, kind):
        gid = g.get("group_id")
        summary["added_terms_by_group"].setdefault(gid, []).append(
            {"term": term, "type": kind}
        )

    def _track_code(g, kind, code):
        gid = g.get("group_id")
        summary["added_codes_by_group"].setdefault(gid, []).append(
            {"kind": kind, "code": code}
        )

    # --- 7.2 同義語適用 ---
    for syn in parsed["synonyms"]:
        main = syn["main"]
        related = (
            [(t, HINT_TYPE_SYNONYM) for t in syn["synonyms"]]
            + [(t, HINT_TYPE_BROADER) for t in syn["broader"]]
            + [(t, HINT_TYPE_NARROWER) for t in syn["narrower"]]
        )
        target = _find_group_for_term(groups, main)
        if target is None:
            # main の broader 側 / narrower 側 のいずれかが既存グループ label と一致するならそこへ
            for t, _kind in related:
                target = _find_group_for_term(groups, t)
                if target:
                    break
        if target is None:
            summary["synonyms_skipped_unmatched"] += 1
            continue
        # main を保証 (グループ label と完全一致でなければ追加)
        if _norm(target.get("label")) != _norm(main):
            if _add_keyword(target, main, HINT_TYPE_SYNONYM, "予備検索ヒント"):
                summary["synonyms_added"] += 1
                _track_term(target, main, HINT_TYPE_SYNONYM)
        for term, kind in related:
            if _add_keyword(target, term, kind, "予備検索ヒント"):
                summary["synonyms_added"] += 1
                _track_term(target, term, kind)

    # --- 7.3 分類コード適用 ---
    cls = parsed["classifications"]
    for kind in ("fi", "cpc", "ipc", "fterm"):
        for entry in cls.get(kind, []):
            code = entry["code"]
            desc = entry.get("desc", "")
            target = None
            if desc:
                target = _find_group_for_term(groups, desc)
            if target is None:
                target = _ensure_unsorted_group()
                summary["codes_unmatched_to_unsorted"] += 1
            if _add_classification(target, kind, code, desc):
                summary["codes_added"] += 1
                _track_code(target, kind, code)

    _save_keywords(kw_path, groups)
    return {
        "success": True,
        **summary,
        "noise": parsed["noise"],
        "groups": groups,
    }, 200
