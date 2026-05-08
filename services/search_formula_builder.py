"""J-PlatPat 検索式の自動生成 (Phase C)。

サーチャーが実務で使う段階的検索戦略をルールベースで実装。
LLM 不要なので 1 秒で式が出る。

検索演算子 (J-PlatPat 論理式入力):
    AND: ` * `
    OR : `+`
    NOT: `-` (※ キーワード文字列内では半角 - を全角ーに変換)
    フィールド指定: `語/TX` `コード/FI` `コード/FT` `テーマ/FC` `出願人/AP`

戦略レベル:
    L0: 出願人 × FI (× メイン F-term)  — 出願人ポートフォリオ確認 (X 文献はここで出る事も)
    L1: FI × F-term 厳格 — 狭く深く (Phase C-2)
    L2: FI 緩和 + キーワード OR 置換  — 中広 (Phase C-2)
    L3: キーワード OR + 実施例化合物  — 広い (Phase C-2)
    L4: 競合他社 × FI                — 横断 (Phase C-3)
"""
from __future__ import annotations

import json
import re

from services.case_service import get_case_dir, load_case_meta
from services.keyword_service import _load_keywords  # noqa: PLC0415 import-time OK


# --------------------------------------------------------------
# ユーティリティ
# --------------------------------------------------------------

def to_jplatpat_term(s: str) -> str:
    """J-PlatPat 用にキーワード文字列を正規化。

    - 半角 ASCII ハイフン (-) → 全角長音 (ー) (J-PlatPat の `-` は NOT 演算子扱いになるため)
    - 全角空白 → 半角空白 (1 つ)
    - 連続空白を 1 つに圧縮
    - 前後空白除去
    """
    if not s:
        return ""
    out = s.replace("-", "ー")
    out = out.replace("　", " ")
    out = re.sub(r"\s+", " ", out).strip()
    return out


def field(code: str, tag: str) -> str:
    """`<code>/<TAG>` を返す。code が複数語含む場合はそのまま (J-PlatPat は空白を AND として読まない)。"""
    code = (code or "").strip()
    return f"{code}/{tag}" if code else ""


# 分類コード正規化 / 検証
_FI_CODE_RE = re.compile(r"^[A-Z]\d{2}[A-Z]\s*\d+/\d+(?:[A-Z]\d*)?$", re.IGNORECASE)
# 例: A61K 8/36, A61K8/36, A61Q5/02C
_FTERM_FULL_RE = re.compile(r"^(\d[A-Z]\d{3})([A-Z]{2})(\d{2,3})([A-Z])?$", re.IGNORECASE)
_FTERM_SHORT_RE = re.compile(r"^([A-Z]{2})(\d{2,3})([A-Z])?(?:\.([12]?))?$", re.IGNORECASE)
_THEME_CODE_RE = re.compile(r"^\d[A-Z]\d{3}$", re.IGNORECASE)
# 例: 4C083AB172 → theme=4C083, query_code=AB17.2
# 例: 4C083AD05 → theme=4C083, query_code=AD05.
# 例: 4F100AK01B → theme=4F100, query_code=AK01B


def normalize_fi_code(s: str) -> str:
    """FI コードを論理式に貼れる形に。空白除去 (J-PlatPat の論理式では空白が AND と
    誤認される恐れがあるため)。"""
    return re.sub(r"\s+", "", (s or "").strip())


def normalize_fterm_code(s: str) -> str:
    """F-term コードを正規化。

    J-PlatPat 検索式ではテーマコード付き `4C083AC172/FT` は使わず、
    テーマコード `4C083/FC` を別途 AND し、Fタームは `AC17.2/FT` とする。
    4C083 の末尾 1/2 は付加コードなので、短縮コード側では `.1` / `.2` に変換する。
    4C083 の付加コード未指定の 2 桁コードは、`.1` / `.2` を拾うため末尾 `.` を付ける。
    """
    parsed = parse_fterm_code(s)
    if parsed:
        return parsed["query_code"]
    s = re.sub(r"\s+", "", (s or "").strip())
    return s.replace(":", "").replace("：", "")


def is_valid_fi(code: str) -> bool:
    return bool(_FI_CODE_RE.match(normalize_fi_code(code)))


def is_valid_fterm(code: str) -> bool:
    return parse_fterm_code(code) is not None


def is_valid_theme_code(code: str) -> bool:
    return bool(_THEME_CODE_RE.match(re.sub(r"\s+", "", (code or "").strip())))


def parse_fterm_code(s: str) -> dict | None:
    """Fタームを検索式用コードへ分解する。

    Returns:
        {"raw": "4C083AC172", "theme": "4C083", "query_code": "AC17.2"}
        {"raw": "4C083AD05", "theme": "4C083", "query_code": "AD05."}
        {"raw": "4F100AK01B", "theme": "4F100", "query_code": "AK01B"}
        短縮形 `AC17.2` の場合 theme は ""。
    """
    raw = re.sub(r"\s+", "", (s or "").strip())
    raw = raw.replace(":", "").replace("：", "")
    if not raw:
        return None
    m = _FTERM_FULL_RE.match(raw)
    if m:
        theme = m.group(1).upper()
        axis = m.group(2).upper()
        number = m.group(3)
        layer_suffix = (m.group(4) or "").upper()
        addon = (
            number[-1]
            if theme == "4C083" and len(number) == 3 and number[-1] in ("1", "2")
            else ""
        )
        if addon:
            # 4C083 の付加コード 1/2 は検索式ではドット付きにする。
            base_number = number[:-1]
            query_code = f"{axis}{base_number}.{addon}{layer_suffix}"
        elif theme == "4C083" and len(number) == 2 and not layer_suffix:
            # 4C083AD05/FT では AD05.1/AD05.2 がヒットしないため AD05./FT にする。
            query_code = f"{axis}{number}."
        else:
            query_code = f"{axis}{number}{layer_suffix}"
        return {"raw": raw, "theme": theme, "query_code": query_code}
    m = _FTERM_SHORT_RE.match(raw)
    if m:
        query_code = f"{m.group(1).upper()}{m.group(2)}{(m.group(3) or '').upper()}"
        if m.group(4) is not None:
            query_code += f".{m.group(4)}"
        return {"raw": raw, "theme": "", "query_code": query_code}
    return None


def fterm_formula_parts(codes: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Fタームコード列から J-PlatPat 検索式 parts を生成する。

    テーマごとに `(4C083/FC * (AC17.2+AB08.2)/FT)` の形で返す。
    テーマ不明の短縮形は `AC17.2/FT` として返す。

    Returns:
        (parts, skipped_invalid, normalized_query_codes)
    """
    by_theme: dict[str, list[str]] = {}
    no_theme: list[str] = []
    skipped: list[str] = []
    normalized: list[str] = []
    seen_per_theme: set[tuple[str, str]] = set()
    for raw in codes:
        parsed = parse_fterm_code(raw)
        if not parsed:
            skipped.append(raw)
            continue
        theme = parsed["theme"]
        q = parsed["query_code"]
        key = (theme, q)
        if key in seen_per_theme:
            continue
        seen_per_theme.add(key)
        normalized.append(q if not theme else f"{theme}:{q}")
        if theme:
            by_theme.setdefault(theme, []).append(q)
        else:
            no_theme.append(q)

    parts: list[str] = []
    for theme, qs in by_theme.items():
        ft = field(qs[0], "FT") if len(qs) == 1 else f"({'+'.join(qs)})/FT"
        parts.append(f"({field(theme, 'FC')} * {ft})")
    for q in no_theme:
        parts.append(field(q, "FT"))
    return parts, skipped, normalized


def or_bundle(items: list[str], tag: str, normalize=None, validate=None) -> tuple[str, list[str]]:
    """OR 束 `(語1+語2+語3)/<TAG>` を返す。

    1 件なら括弧無し `語/<TAG>`。0 件なら空文字。
    Returns: (formula_str, skipped_invalid_codes)
    """
    norm = []
    seen = set()
    skipped = []
    for raw in items:
        t = (normalize(raw) if normalize else to_jplatpat_term(raw))
        if not t or t in seen:
            continue
        if validate and not validate(t):
            skipped.append(raw)
            continue
        seen.add(t)
        norm.append(t)
    if not norm:
        return "", skipped
    if len(norm) == 1:
        return field(norm[0], tag), skipped
    return f"({'+'.join(norm)})/{tag}", skipped


def and_join(parts: list[str]) -> str:
    """AND 結合 (空文字を除外、 ` * ` 区切り)。"""
    return " * ".join(p for p in parts if p)


# --------------------------------------------------------------
# 案件データ取得
# --------------------------------------------------------------

def _load_hongan(case_id: str) -> dict | None:
    case_dir = get_case_dir(case_id)
    p = case_dir / "hongan.json"
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _get_applicant(case_id: str) -> str | None:
    h = _load_hongan(case_id)
    if not h:
        return None
    a = h.get("applicant")
    if isinstance(a, list) and a:
        return a[0]
    if isinstance(a, str) and a.strip():
        return a.strip()
    return None


def _collect_classification_codes(case_id: str, kind: str) -> list[dict]:
    """keywords.json の各グループから kind のコードを収集 (重複除去)。

    Returns: [{"code", "desc", "group_id", "group_label"}]
    """
    out = []
    seen = set()
    groups, _ = _load_keywords(case_id)
    for g in groups or []:
        for entry in (g.get("search_codes") or {}).get(kind, []):
            code = (entry.get("code") or "").strip()
            if not code or code in seen:
                continue
            seen.add(code)
            out.append({
                "code": code,
                "desc": entry.get("desc", ""),
                "group_id": g.get("group_id"),
                "group_label": g.get("label", ""),
            })
    # 不足時は classification.json をフォールバック
    if not out:
        case_dir = get_case_dir(case_id)
        cls_path = case_dir / "search" / "classification.json"
        if cls_path.exists():
            try:
                with open(cls_path, "r", encoding="utf-8") as f:
                    cls = json.load(f)
                for entry in cls.get(kind, []):
                    code = (entry.get("code") or "").strip()
                    if not code or code in seen:
                        continue
                    seen.add(code)
                    out.append({
                        "code": code,
                        "desc": entry.get("label", "") or entry.get("desc", ""),
                        "group_id": None,
                        "group_label": "",
                    })
            except (json.JSONDecodeError, OSError):
                pass
    return out


def _main_segment_groups(case_id: str) -> list[dict]:
    """第一請求項のメイン構成 (segment_ids が 1A/1B/... を含む) のグループを返す。"""
    out = []
    groups, _ = _load_keywords(case_id)
    for g in groups or []:
        seg_ids = g.get("segment_ids") or []
        # 1 から始まり、後続が英字 (1A/1B/...)。請求項 1 のサブ分節
        if any(re.match(r"^1[A-Za-z]+$", str(sid)) for sid in seg_ids):
            out.append(g)
    return out


def _collect_main_fterms(case_id: str) -> list[dict]:
    """請求項 1 メイン構成グループの F-term を収集。"""
    out = []
    seen = set()
    for g in _main_segment_groups(case_id):
        for entry in (g.get("search_codes") or {}).get("fterm", []):
            code = (entry.get("code") or "").strip()
            if not code or code in seen:
                continue
            seen.add(code)
            out.append({
                "code": code,
                "desc": entry.get("desc", ""),
                "group_id": g.get("group_id"),
                "group_label": g.get("label", ""),
            })
    return out


# --------------------------------------------------------------
# Level 0: 出願人 × FI [× メイン F-term]
# --------------------------------------------------------------

def build_l0(case_id: str, include_main_fterm: bool = False) -> tuple[dict, int]:
    """L0: 出願人 × FI [× 第一請求項メイン構成の F-term]。

    出願人ポートフォリオ確認用。X 文献 (進歩性否定材料) が
    出願人自身の過去出願に含まれるケースで有効。

    Args:
        include_main_fterm: True なら第一請求項のメイン構成 F-term も AND 結合
    """
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404

    applicant = _get_applicant(case_id)
    if not applicant:
        return {
            "error": "hongan.json に出願人 (applicant) がありません。"
                     "Step 1 → 「書誌情報を再抽出」を実行してください。"
        }, 400

    fi_codes = _collect_classification_codes(case_id, "fi")
    if not fi_codes:
        return {
            "error": "FI コードがありません。"
                     "Step 4 Stage 2 (分類特定) を実行するか、"
                     "Step 3 → 「📥 予備検索ヒントを取り込む」で 7.3 を反映してください。"
        }, 400

    fi_part, fi_skipped = or_bundle(
        [c["code"] for c in fi_codes], "FI",
        normalize=normalize_fi_code, validate=is_valid_fi,
    )
    if not fi_part:
        return {
            "error": "有効な FI コードがありません (形式不正のものはスキップされました)。"
                     f"スキップ: {fi_skipped[:5]}"
        }, 400

    parts = [
        field(to_jplatpat_term(applicant), "AP"),
        fi_part,
    ]
    warnings = []
    if fi_skipped:
        warnings.append(f"FI として不正な形式の語をスキップしました: {fi_skipped}")

    main_fterm_codes_used = []
    if include_main_fterm:
        main_fterms = _collect_main_fterms(case_id)
        ft_parts, ft_skipped, main_fterm_codes_used = fterm_formula_parts(
            [c["code"] for c in main_fterms]
        )
        if ft_parts:
            parts.extend(ft_parts)
        if ft_skipped:
            warnings.append(f"F-term として不正な形式の語をスキップしました: {ft_skipped}")
        if not ft_parts:
            warnings.append("第一請求項メイン構成 (segment_ids 1A/1B/...) のグループに有効な F-term が無いため、F-term は省略しました。")

    formula = and_join(parts)
    return {
        "level": "L0",
        "name": "出願人 × FI" + (" × メイン F-term" if main_fterm_codes_used else ""),
        "formula": formula,
        "components": {
            "applicant": applicant,
            "fi_codes": fi_codes,
            "main_fterm_codes": main_fterm_codes_used,
        },
        "warnings": warnings,
    }, 200
