#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""対比プロンプト生成と分節鮮度チェック。"""
from __future__ import annotations

import json
import re

from services.case_service import get_case_dir, load_case_meta
from services.comparison.common import _load_citation_for_prompt, _safe_prompt_filename
def generate_prompt_multi(case_id, citation_ids):
    """複数文献対応のプロンプト生成"""
    from modules.prompt_generator import generate_prompt as _gen

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    segments_path = case_dir / "segments.json"

    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    if not citation_ids:
        return {"error": "対象文献を選択してください"}, 400

    citations = []
    empty_ids = []
    for cit_id in citation_ids:
        cit, err = _load_citation_for_prompt(case_id, cit_id, case_dir)
        if err:
            return {"error": err}, 404
        if _is_empty_citation(cit):
            empty_ids.append((cit_id, cit))
        citations.append(cit)

    if empty_ids:
        msgs = [_empty_citation_error(cid, c) for cid, c in empty_ids]
        return {
            "error": " / ".join(msgs),
            "empty_citation_ids": [cid for cid, _ in empty_ids],
        }, 400

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)
    keywords = _filter_keywords_by_valid_segments(keywords, segs)

    field = meta.get("field", "cosmetics")
    hongan = None
    hongan_path = case_dir / "hongan.json"
    if hongan_path.exists():
        with open(hongan_path, "r", encoding="utf-8") as f:
            hongan = json.load(f)
    prompt_text = _gen(segs, citations, keywords, field, hongan=hongan)

    ids_label = "_".join(citation_ids)
    prompt_path = case_dir / "prompts" / _safe_prompt_filename(ids_label)
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt_text)

    return {
        "prompt": prompt_text,
        "char_count": len(prompt_text),
        "num_citations": len(citations),
    }, 200


def generate_prompt_single(case_id, citation_id):
    """単一文献のプロンプト生成"""
    from modules.prompt_generator import generate_prompt as _gen

    case_dir = get_case_dir(case_id)
    segments_path = case_dir / "segments.json"

    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)
    citation, err = _load_citation_for_prompt(case_id, citation_id, case_dir)
    if err:
        return {"error": err}, 404

    if _is_empty_citation(citation):
        return {
            "error": _empty_citation_error(citation_id, citation),
            "empty_citation_ids": [citation_id],
        }, 400

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)
    keywords = _filter_keywords_by_valid_segments(keywords, segs)

    hongan = None
    hongan_path = case_dir / "hongan.json"
    if hongan_path.exists():
        with open(hongan_path, "r", encoding="utf-8") as f:
            hongan = json.load(f)
    prompt_text = _gen(segs, citation, keywords, hongan=hongan)

    prompt_path = case_dir / "prompts" / _safe_prompt_filename(citation_id)
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt_text)

    return {"prompt": prompt_text, "char_count": len(prompt_text)}, 200


def check_segments_freshness(case_id):
    """現在の segments.json と responses/*.json の整合性を点検する。

    分節を Step 2 で編集した後に再対比をかけずに Step 5/6 へ進むと、新しい分節の
    judgment データが無いまま Excel に "-" が並ぶ silent stale が起きる。
    これを UI バナーで気づけるよう、サーバー側で 1 回計算して返す。

    Returns 200 OK with:
        {
          "has_responses": bool,
          "response_count": int,
          "segments_mtime": float | None,
          "oldest_response_mtime": float | None,
          "newest_response_mtime": float | None,
          "stale_by_mtime": bool,            # segments_mtime > oldest_response_mtime
          "current_segment_count": int,
          "missing_in_responses": [str],      # 現分節 ID で どの response にも無いもの
          "orphans_in_responses": {           # responses 側にあるが現分節に無い ID 群
              "<citation_id>": [str], ...
          },
          "needs_recompare": bool,            # missing or orphans があれば True
        }
    """
    case_dir = get_case_dir(case_id)
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404

    seg_path = case_dir / "segments.json"
    resp_dir = case_dir / "responses"

    out = {
        "has_responses": False,
        "response_count": 0,
        "segments_mtime": None,
        "oldest_response_mtime": None,
        "newest_response_mtime": None,
        "stale_by_mtime": False,
        "current_segment_count": 0,
        "missing_in_responses": [],
        "orphans_in_responses": {},
        "citation_ids_with_responses": [],  # 「前回選択分」の自動再対比に使う
        "needs_recompare": False,
    }

    current_ids = []
    # claim_number → そのクレーム配下の segment_id 集合 (sub_claims 判定の解決に使う)
    segs_by_claim: dict[int, list[str]] = {}
    if seg_path.exists():
        out["segments_mtime"] = seg_path.stat().st_mtime
        try:
            with open(seg_path, "r", encoding="utf-8") as f:
                segs = json.load(f)
            current_ids = _get_all_segment_ids(segs)
            for claim in segs or []:
                cn = claim.get("claim_number")
                if isinstance(cn, int):
                    segs_by_claim[cn] = [
                        s.get("id") for s in (claim.get("segments") or []) if s.get("id")
                    ]
        except (OSError, json.JSONDecodeError):
            current_ids = []
    out["current_segment_count"] = len(current_ids)
    current_set = set(current_ids)

    if not resp_dir.exists():
        return out, 200

    # 集計対象: responses/*.json (アンダースコア始まりの作業ファイルは除外)
    resp_files = [p for p in resp_dir.glob("*.json") if not p.name.startswith("_")]
    if not resp_files:
        return out, 200

    out["has_responses"] = True
    out["response_count"] = len(resp_files)
    out["citation_ids_with_responses"] = sorted(p.stem for p in resp_files)

    mtimes = [p.stat().st_mtime for p in resp_files]
    out["oldest_response_mtime"] = min(mtimes)
    out["newest_response_mtime"] = max(mtimes)
    if out["segments_mtime"] is not None and out["oldest_response_mtime"] is not None:
        out["stale_by_mtime"] = out["segments_mtime"] > out["oldest_response_mtime"]

    seen_in_responses = set()
    orphans_per_doc = {}
    for p in resp_files:
        try:
            with open(p, "r", encoding="utf-8") as f:
                rdata = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        comps = rdata.get("comparisons") or []
        ids = [(c.get("requirement_id") or "").strip() for c in comps if c.get("requirement_id")]
        # sub_claims (請求項 2 以降) は claim_number ベースで判定が入る。
        # 該当 claim_number の全 segment_id を「seen 済」として扱う (= 個別分節の判定が
        # 無くてもクレーム単位で覆われていれば missing 扱いしない)。
        sub_claims = rdata.get("sub_claims") or []
        for sc in sub_claims:
            cn = sc.get("claim_number")
            if isinstance(cn, int) and cn in segs_by_claim:
                ids.extend(segs_by_claim[cn])
        seen_in_responses.update(ids)
        # この文献の orphan = 現分節に無い response 側 ID
        orphans = sorted(set(i for i in ids if i and i not in current_set))
        if orphans:
            orphans_per_doc[p.stem] = orphans

    if current_set:
        out["missing_in_responses"] = sorted(s for s in current_set if s not in seen_in_responses)
    out["orphans_in_responses"] = orphans_per_doc
    # needs_recompare は実害がある場合のみ True にする。mtime 単独は除外:
    # 分節 ID が一致していれば古い judgment でも Excel に正しく反映されるため、
    # 「分節編集が対比より新しい」だけで警告を出すと「再対比したのに警告が消えない」
    # という UX 不具合になる (ID 整合がとれてれば実害無し)。
    out["needs_recompare"] = bool(out["missing_in_responses"]) or bool(orphans_per_doc)
    return out, 200


def _get_all_segment_ids(segs):
    """分節データから全分節IDを取得"""
    ids = []
    for claim in segs:
        for seg in claim["segments"]:
            ids.append(seg["id"])
    return ids


def _filter_keywords_by_valid_segments(keywords, segs):
    """keywords.json の各グループの segment_ids から、segments.json に
    存在しない ID (補正で消えた古い 1F/1G 等) を除外した clone を返す。

    永続化はしない (UI 側の表示は維持しつつ、prompt にだけクリーン版を渡す)。
    実体側の掃除は services.keyword_service.prune_keyword_segment_ids。
    """
    if not keywords:
        return keywords
    valid = set(_get_all_segment_ids(segs))
    cleaned = []
    for g in keywords:
        seg_ids = g.get("segment_ids") or []
        new_seg_ids = [sid for sid in seg_ids if sid in valid]
        if len(new_seg_ids) != len(seg_ids):
            g_copy = dict(g)
            g_copy["segment_ids"] = new_seg_ids
            cleaned.append(g_copy)
        else:
            cleaned.append(g)
    return cleaned


def _is_empty_citation(cit):
    """引用文献の本文が空 (テキスト抽出失敗) かを判定。

    claims / paragraphs / tables が全て空なら、プロンプトに入れても見出しだけになり
    Claude が判定できないため空扱いとする。
    """
    if (cit.get("claims") or [])[:1]:
        return False
    if (cit.get("paragraphs") or [])[:1]:
        return False
    if (cit.get("tables") or [])[:1]:
        return False
    return True


def _empty_citation_error(cit_id, cit):
    """空 citation 用のユーザー向けエラーメッセージを構築。"""
    warning = (cit.get("_warning") or "").strip()
    base = f"引用文献 '{cit_id}' のテキストが抽出できていません"
    if warning:
        base += f"（{warning}）"
    return (
        base
        + "。スキャン画像PDFの可能性があります。再アップロードまたは削除してから再実行してください。"
    )


def _normalize_doc_id(s):
    """文献 ID を比較用に正規化 (英数字以外を除去・大文字化)。

    例: 'US 2013/0040869' → 'US20130040869'
        'US20130040869A1' → 'US20130040869A1'
        'US-20130040869'  → 'US20130040869'
    """
    if not s:
        return ""
    return "".join(ch for ch in str(s) if ch.isalnum()).upper()


def _digit_groups(s):
    """文字列に含まれる連続数字列を抽出。leading zeros を除いた長さ 4 以上のものだけ返す。

    例: 'JPB003596681-000000' → ['3596681']  (003596681 は leading 0 除去で 3596681、
                                              000000 は除去後空なので捨てる)
        'JPA1993042929-000000' → ['1993042929']
        '特許第3596681号'        → ['3596681']
    """
    import re
    out = []
    for g in re.findall(r"\d+", s or ""):
        stripped = g.lstrip("0") or ""
        if len(stripped) >= 4:
            out.append(stripped)
    return out


def _canonical_digits(s):
    """全ての数字を連結、leading zeros 除去。

    例: '特開1993-042929'        → '1993042929'
        'JPA1993042929-000000'  → '1993042929000000'
        '特許第3596681号'         → '3596681'
        'JPB003596681-000000'   → '3596681000000'  (003596681 の leading 0 除去でつながる)
    """
    import re
    return "".join(re.findall(r"\d+", s or "")).lstrip("0")


def _resolve_doc_id(llm_doc_id, known_cit_ids):
    """LLM が返した document_id を、登録済 citation_id (case.yaml) にマッピング。

    マッチ順:
      1. 完全一致
      2. 英数字のみ正規化での一致
      3. 包含 (LLM 側 ⊃ citation 側、または逆)
      4. 数字列シグネチャ一致 (例: '特許第3596681号' ↔ 'JPB003596681-000000' を 3596681 で吸着)

    どれにも当てはまらなければ LLM の文字列をそのまま返す。
    """
    if not llm_doc_id or not known_cit_ids:
        return llm_doc_id
    if llm_doc_id in known_cit_ids:
        return llm_doc_id
    norm_llm = _normalize_doc_id(llm_doc_id)
    norm_map = {_normalize_doc_id(c): c for c in known_cit_ids}
    if norm_llm in norm_map:
        return norm_map[norm_llm]
    # 包含マッチ: LLM 側に余計な末尾 (A1/B2 等) がある or 逆も試行
    for nk, orig in norm_map.items():
        if nk and (nk in norm_llm or norm_llm in nk):
            return orig

    # 数字シグネチャマッチ
    # 戦略:
    #   (a) 全数字を連結した文字列が prefix/suffix 関係にあるか
    #       (e.g., LLM '特開1993-042929' → '1993042929'
    #              cit 'JPA1993042929-000000' → '1993042929000000'
    #              → LLM が cit の prefix なので一致)
    #   (b) 個別の数字グループが完全一致 or 7 桁以上の包含
    #
    # 安全弁: ≥7 桁の共通部分が必要 (4 桁の年号だけで誤マッチしないように)。
    best_match = None
    best_len = 0

    llm_canon = _canonical_digits(llm_doc_id)
    llm_digits = _digit_groups(llm_doc_id)

    if llm_canon and len(llm_canon) >= 7:
        for cit_id in known_cit_ids:
            cit_canon = _canonical_digits(cit_id)
            if not cit_canon or len(cit_canon) < 7:
                continue
            # 完全一致 → 強いマッチ
            if llm_canon == cit_canon:
                if len(llm_canon) > best_len:
                    best_len = len(llm_canon)
                    best_match = cit_id
                continue
            # prefix/suffix 関係: 短い側が長い側に含まれる
            shorter, longer = sorted([llm_canon, cit_canon], key=len)
            if shorter in longer and len(shorter) >= 7:
                if len(shorter) > best_len:
                    best_len = len(shorter)
                    best_match = cit_id

    # 個別数字グループでの照合 (fallback、短い番号体系向け)
    if not best_match and llm_digits:
        for cit_id in known_cit_ids:
            cit_digits = _digit_groups(cit_id)
            for ld in llm_digits:
                for cd in cit_digits:
                    if ld == cd and len(ld) >= 5 and len(ld) > best_len:
                        best_len = len(ld)
                        best_match = cit_id
                    elif len(ld) >= 7 and len(cd) >= 7 and (ld in cd or cd in ld):
                        common = min(len(ld), len(cd))
                        if common > best_len:
                            best_len = common
                            best_match = cit_id

    if best_match:
        return best_match

    return llm_doc_id


