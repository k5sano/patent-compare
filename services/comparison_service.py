#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""対比分析・Excel出力・PDF注釈サービス"""

import os
import re
import json
from pathlib import Path
from datetime import datetime

from services.case_service import (
    get_case_dir, load_case_meta, save_case_meta, find_citation_pdf,
)


def _write_annotated_pdf(pdf_path, output_dir, safe_name, response_data, citation_data, keywords):
    """注釈PDFを書き出す。出力先がロック中なら別名にフォールバック。

    Returns: (result_dict, actual_path)
    """
    from modules.pdf_annotator import annotate_citation_pdf

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{safe_name}_annotated.pdf"
    try:
        result = annotate_citation_pdf(
            pdf_path, target, response_data, citation_data, keywords)
        return result, target
    except Exception as e:
        msg = str(e).lower()
        if "permission denied" in msg or "cannot remove" in msg or "in use" in msg:
            ts = datetime.now().strftime("%H%M%S%f")
            alt = output_dir / f"{safe_name}_annotated_{ts}.pdf"
            result = annotate_citation_pdf(
                pdf_path, alt, response_data, citation_data, keywords)
            result["alt_filename"] = True
            return result, alt
        raise


def _annotate_worker(job):
    """プロセスプールのワーカー。ピックル可能にするためモジュールトップレベルに置く。

    job: (cit_id, pdf_path, output_dir, response_data, citation_data, keywords)
    """
    cit_id, pdf_path, output_dir, response_data, citation_data, keywords = job
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', cit_id)
    try:
        result, actual_path = _write_annotated_pdf(
            Path(pdf_path), Path(output_dir), safe_name,
            response_data, citation_data, keywords)
        return {"citation_id": cit_id, "success": True,
                "filename": actual_path.name, **result}
    except Exception as e:
        return {"citation_id": cit_id, "success": False, "error": str(e)}


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
        cit_path = case_dir / "citations" / f"{cit_id}.json"
        if not cit_path.exists():
            return {"error": f"引用文献 '{cit_id}' が見つかりません"}, 404
        with open(cit_path, "r", encoding="utf-8") as f:
            cit = json.load(f)
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
    prompt_path = case_dir / "prompts" / f"{ids_label}_prompt.txt"
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
    citation_path = case_dir / "citations" / f"{citation_id}.json"

    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400
    if not citation_path.exists():
        return {"error": f"引用文献 '{citation_id}' が見つかりません"}, 404

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)
    with open(citation_path, "r", encoding="utf-8") as f:
        citation = json.load(f)

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

    prompt_path = case_dir / "prompts" / f"{citation_id}_prompt.txt"
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


def save_response_multi(case_id, raw_text):
    """複数文献対応の回答パース・保存。

    LLM の ``document_id`` は表記揺れ (例: ``US 2013/0040869`` vs ``US20130040869``) が
    起こりやすく、そのままだと ``responses/<id>.json`` のファイル名が
    case.yaml の citation id とズレて UI に取り込まれない。
    ``_resolve_doc_id`` で登録済 citation_id へ正規化してから保存する。
    """
    from modules.response_parser import parse_response, split_multi_response

    case_dir = get_case_dir(case_id)
    segments_path = case_dir / "segments.json"

    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    if not raw_text.strip():
        return {"error": "テキストが空です"}, 400

    result, errors = parse_response(raw_text, _get_all_segment_ids(segs))

    # 登録済 citation_id を取得 (LLM の document_id 揺れを吸収するため)
    meta = load_case_meta(case_id) or {}
    known_cit_ids = [c.get("id") for c in meta.get("citations", []) if c.get("id")]

    saved_docs = []
    skipped = []  # 解決不能なもの (デバッグ用)
    if result:
        per_doc = split_multi_response(result)
        for doc_id, doc_result in per_doc.items():
            resolved = _resolve_doc_id(doc_id, known_cit_ids)
            if resolved != doc_id:
                # マッピングが行われた旨を errors に warn として残す
                errors = (errors or []) + [
                    f"document_id '{doc_id}' を登録済 '{resolved}' にマッピング"
                ]
            if known_cit_ids and resolved not in known_cit_ids:
                # どの citation_id にも対応付けできなかった
                skipped.append(doc_id)
                # それでもファイルとしては保存 (運用上の保険)
            resp_path = case_dir / "responses" / f"{resolved}.json"
            # 保存時に document_id も正規化して書き戻す (UI が citId 引き当てに使う)
            if isinstance(doc_result, dict):
                doc_result["document_id"] = resolved
            _normalize_cited_locations_inplace(doc_result)
            with open(resp_path, "w", encoding="utf-8") as f:
                json.dump(doc_result, f, ensure_ascii=False, indent=2)
            saved_docs.append(resolved)

    payload = {
        "success": result is not None,
        "errors": errors,
        "saved_docs": saved_docs,
        "num_docs": len(saved_docs),
    }
    if skipped:
        payload["unresolved_doc_ids"] = skipped
    return payload, 200


def save_response_single(case_id, citation_id, raw_text):
    """単一文献の回答パース"""
    from modules.response_parser import parse_response

    case_dir = get_case_dir(case_id)
    segments_path = case_dir / "segments.json"

    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    if not raw_text.strip():
        return {"error": "テキストが空です"}, 400

    result, errors = parse_response(raw_text, _get_all_segment_ids(segs))

    if result:
        resp_path = case_dir / "responses" / f"{citation_id}.json"
        with open(resp_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    return {
        "success": result is not None,
        "errors": errors,
        "data": result,
    }, 200


def _normalize_cited_locations_inplace(data):
    """LLM 応答の cited_location を正規化して in-place で上書き。

    保存時に呼んでファイル上に正規化済み記法を残す (例: ``0023;0024`` →
    ``23,24``、`T1;T2` → `T1,2`)。読み込み側 (_decorate_comparison_with_notation)
    でも normalize するが、ファイル上を整えておくとコピペ用途で素直になる。
    """
    from modules.cited_ref_notation import normalize as _norm
    if not isinstance(data, dict):
        return data
    for key in ("comparisons", "sub_claims"):
        for item in (data.get(key) or []):
            if isinstance(item, dict) and item.get("cited_location"):
                try:
                    item["cited_location"] = _norm(item["cited_location"])
                except Exception:
                    pass
    return data


def _decorate_comparison_with_notation(data):
    """``cited_location`` 記法を正規化 + 展開して comparison/sub_claims に注入する。

    UI 側で記法を毎回パースせず済むよう、サーバで以下を補強:
      - ``cited_location``: LLM 出力ゆれ (例: ``0023;0024`` `T1;T2`) を正規化
        (``23,24`` `T1,2`)。raw 出力もこの値で上書きする。
      - ``cited_location_expanded``: 日本語展開済み (備考は含めない)
      - ``cited_location_comment``: コメント部分のみ ("..." 以降)
      - ``judgment_display``: ○ は "" に正規化済み (△/× はそのまま)
    """
    from modules.cited_ref_notation import (
        comment_of, display_judgment, expand, normalize,
    )

    def _decorate(comp):
        if not isinstance(comp, dict):
            return comp
        loc = comp.get("cited_location") or ""
        if loc:
            try:
                normalized = normalize(loc)
            except Exception:
                normalized = loc
            # 正規化結果が元と異なれば上書き (LLM 出力ゆれを補正)
            comp["cited_location"] = normalized
            comp["cited_location_expanded"] = expand(normalized, with_comment=False)
            comp["cited_location_comment"] = comment_of(normalized)
        else:
            comp["cited_location_expanded"] = ""
            comp["cited_location_comment"] = ""
        comp["judgment_display"] = display_judgment(comp.get("judgment", ""))
        return comp

    if not isinstance(data, dict):
        return data
    for c in data.get("comparisons", []) or []:
        _decorate(c)
    for s in data.get("sub_claims", []) or []:
        _decorate(s)
    return data


def get_response(case_id, citation_id):
    case_dir = get_case_dir(case_id)
    resp_path = case_dir / "responses" / f"{citation_id}.json"
    if not resp_path.exists():
        return {"error": "回答データがありません"}, 404
    with open(resp_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _decorate_comparison_with_notation(data), 200


def prune_orphan_comparisons(case_id):
    """全 response から「現 segments に無い requirement_id」のエントリを削除。

    補正で消えた旧分節 (1F, 1G 等) の判定が response 内に残ったまま、
    UI に表示できない死にデータになっているケースの掃除。
    再対比は走らせない (純粋にゴミ消し)。

    Returns:
        ({success, removed_total, removed_per_doc, valid_segment_ids}, status)
    """
    case_dir = get_case_dir(case_id)
    seg_path = case_dir / "segments.json"
    if not seg_path.exists():
        return {"error": "segments.json がありません"}, 400
    try:
        with open(seg_path, "r", encoding="utf-8") as f:
            segs = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {"error": f"segments.json 読み込み失敗: {e}"}, 500

    valid_ids = set(_get_all_segment_ids(segs))

    resp_dir = case_dir / "responses"
    if not resp_dir.exists():
        return {"success": True, "removed_total": 0,
                "removed_per_doc": {}, "valid_segment_ids": sorted(valid_ids)}, 200

    removed_per_doc: dict[str, list[str]] = {}
    removed_total = 0
    for p in resp_dir.glob("*.json"):
        if p.name.startswith("_"):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                rdata = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        comps = rdata.get("comparisons") or []
        kept = []
        removed_here = []
        for c in comps:
            rid = (c.get("requirement_id") or "").strip()
            if rid and rid not in valid_ids:
                removed_here.append(rid)
                continue
            kept.append(c)
        if removed_here:
            rdata["comparisons"] = kept
            with open(p, "w", encoding="utf-8") as f:
                json.dump(rdata, f, ensure_ascii=False, indent=2)
            removed_per_doc[p.stem] = removed_here
            removed_total += len(removed_here)

    return {
        "success": True,
        "removed_total": removed_total,
        "removed_per_doc": removed_per_doc,
        "valid_segment_ids": sorted(valid_ids),
    }, 200


def update_comparison_cell(case_id, citation_id, target_kind, target_key, fields):
    """対比表セルを手動修正する。

    Args:
        case_id: 案件 ID
        citation_id: 引例 ID (responses/<citation_id>.json)
        target_kind: "comparison" (構成要件) or "sub_claim" (従属請求項)
        target_key: comparison なら requirement_id (例 "1A"), sub_claim なら claim_number (int)
        fields: 更新する辞書 {judgment, judgment_reason, cited_location, cited_text}
                指定されたキーのみ更新 (None / 未指定はスキップ)

    Returns:
        ({success, updated, edited_at, doc}, status)
    """
    case_dir = get_case_dir(case_id)
    resp_path = case_dir / "responses" / f"{citation_id}.json"
    if not resp_path.exists():
        return {"error": f"回答データがありません: {citation_id}"}, 404
    try:
        with open(resp_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {"error": f"読み込み失敗: {e}"}, 500

    target = None
    if target_kind == "comparison":
        for c in data.get("comparisons") or []:
            if str(c.get("requirement_id")) == str(target_key):
                target = c
                break
    elif target_kind == "sub_claim":
        try:
            tk = int(target_key)
        except (TypeError, ValueError):
            return {"error": f"sub_claim は claim_number(int) が必要: {target_key}"}, 400
        for c in data.get("sub_claims") or []:
            if int(c.get("claim_number") or -1) == tk:
                target = c
                break
    else:
        return {"error": f"target_kind は 'comparison' または 'sub_claim': {target_kind}"}, 400

    if target is None:
        return {"error": f"対象が見つかりません: {target_kind} {target_key}"}, 404

    allowed = ("judgment", "judgment_reason", "cited_location", "cited_text")
    updated = {}
    for k in allowed:
        if k in fields and fields[k] is not None:
            target[k] = str(fields[k]) if not isinstance(fields[k], str) else fields[k]
            updated[k] = target[k]

    # 手動編集の証跡 (UI バッジ表示用)
    from datetime import datetime as _dt
    edited_at = _dt.now().strftime("%Y-%m-%dT%H:%M:%S")
    target["_edited_at"] = edited_at
    target["_edited_by"] = "user"

    with open(resp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return {
        "success": True,
        "updated": updated,
        "edited_at": edited_at,
        "doc": _decorate_comparison_with_notation(data),
    }, 200


def export_full_report(case_id, selected_citation_ids=None):
    """完成版対比表 (本願解析 / 対比表 / 進歩性判断 の 3 タブ統合) を生成。

    selected_citation_ids: None または空ならすべての回答済文献を対象、
        指定があればその ID のみ (export_excel と同じ挙動)。
    """
    from modules.excel_writer import write_full_report

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    segs_path = case_dir / "segments.json"
    if not segs_path.exists():
        return {"error": "分節データがありません"}, 400
    with open(segs_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    # 全 response (回答済) を集める
    all_responses = {}
    resp_dir = case_dir / "responses"
    if resp_dir.exists():
        for p in resp_dir.glob("*.json"):
            if p.name.startswith("_"):
                continue
            try:
                with p.open(encoding="utf-8") as f:
                    all_responses[p.stem] = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
    if not all_responses:
        return {"error": "対比結果がありません。Step 5 で対比を実行してください"}, 400

    # 選択フィルタ適用 (export_excel と同じロジック)
    if selected_citation_ids:
        sel_set = set(selected_citation_ids)
        responses = {k: v for k, v in all_responses.items() if k in sel_set}
        if not responses:
            return {
                "error": "選択された文献に回答データがありません。",
            }, 400
        meta = dict(meta)
        meta["citations"] = [c for c in meta.get("citations", []) if c.get("id") in sel_set]
    else:
        responses = all_responses

    citations_meta = {}
    for cit in meta.get("citations", []):
        cit_path = case_dir / "citations" / f"{cit['id']}.json"
        if cit_path.exists():
            try:
                with cit_path.open(encoding="utf-8") as f:
                    citations_meta[cit["id"]] = json.load(f)
            except (OSError, json.JSONDecodeError):
                pass

    # 本願分析結果 (任意 — なくても出力)
    hongan_analysis = None
    han_path = case_dir / "analysis" / "hongan_analysis.json"
    if han_path.exists():
        try:
            with han_path.open(encoding="utf-8") as f:
                hongan_analysis = json.load(f)
        except (OSError, json.JSONDecodeError):
            hongan_analysis = None

    # 進歩性判断結果 (任意)
    inventive_step = None
    inv_path = case_dir / "inventive_step.json"
    if inv_path.exists():
        try:
            with inv_path.open(encoding="utf-8") as f:
                inventive_step = json.load(f)
        except (OSError, json.JSONDecodeError):
            inventive_step = None

    # 選択時はファイル名にサフィックス
    if selected_citation_ids and len(responses) < len(all_responses):
        fname = f"{meta['case_id']}_完成版対比表_{len(responses)}件.xlsx"
    else:
        fname = f"{meta['case_id']}_完成版対比表.xlsx"
    output_path = case_dir / "output" / fname

    write_full_report(
        output_path=str(output_path),
        case_meta=meta,
        segments=segs,
        responses=responses,
        citations_meta=citations_meta,
        hongan_analysis=hongan_analysis,
        inventive_step=inventive_step,
    )
    return {
        "success": True,
        "filename": output_path.name,
        "path": str(output_path),
        "num_citations": len(responses),
        "tabs": {
            "本願解析結果": hongan_analysis is not None,
            "対比表": True,
            "進歩性判断": inventive_step is not None,
        },
    }, 200


def export_excel(case_id, selected_citation_ids=None):
    """Excel 対比表を出力。

    Parameters:
        case_id: 案件 ID
        selected_citation_ids: 出力対象の citation_id リスト。
            None または空なら回答済の全文献を対象にする (従来挙動)。
            指定があれば、そのうち回答済のものだけを出力対象にする。
    """
    from modules.excel_writer import write_comparison_table

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    segments_path = case_dir / "segments.json"

    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    # 全回答ファイル
    all_responses = {}
    responses_dir = case_dir / "responses"
    if responses_dir.exists():
        for rfile in responses_dir.glob("*.json"):
            with open(rfile, "r", encoding="utf-8") as f:
                all_responses[rfile.stem] = json.load(f)

    if not all_responses:
        return {"error": "回答データがありません"}, 400

    # 選択フィルタ適用
    if selected_citation_ids:
        sel_set = set(selected_citation_ids)
        responses = {k: v for k, v in all_responses.items() if k in sel_set}
        if not responses:
            return {
                "error": "選択された文献に回答データがありません。"
                f" 指定 {len(sel_set)} 件のうち回答済 0 件",
            }, 400
    else:
        responses = all_responses

    # case_meta も同じ選択で絞り込み (write_comparison_table 内で
    # citations 順を決めるのに使用される)
    if selected_citation_ids:
        sel_set = set(selected_citation_ids)
        meta = dict(meta)  # 元データを破壊しないようにコピー
        meta["citations"] = [c for c in meta.get("citations", []) if c.get("id") in sel_set]

    citations_meta = {}
    for cit in meta.get("citations", []):
        cit_path = case_dir / "citations" / f"{cit['id']}.json"
        if cit_path.exists():
            with open(cit_path, "r", encoding="utf-8") as f:
                citations_meta[cit["id"]] = json.load(f)

    # ファイル名: 選択時はサフィックス付き (上書き防止 + 識別性)
    if selected_citation_ids and len(responses) < len(all_responses):
        suffix = f"_対比表_{len(responses)}件.xlsx"
    else:
        suffix = "_対比表.xlsx"
    output_path = case_dir / "output" / f"{meta['case_id']}{suffix}"

    write_comparison_table(
        output_path=str(output_path),
        case_meta=meta,
        segments=segs,
        responses=responses,
        citations_meta=citations_meta,
    )

    return {
        "success": True,
        "filename": output_path.name,
        "path": str(output_path),
        "num_citations": len(responses),
    }, 200


def annotate_citation(case_id, citation_id, force_new_file=False):
    """引用文献PDFに注釈を追加。id (公開番号) で見つからない時は case.yaml の
    label (登録番号など別表記) もフォールバックとして探索する。

    force_new_file=True の場合は出力ファイル名にタイムスタンプを付けて必ず新規
    ファイルとして書き出す (PDF-XChange 等で古い注釈 PDF を開いたままになっても
    確実に新しいファイルが手に入るように)。"""
    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    resp_path = case_dir / "responses" / f"{citation_id}.json"
    if not resp_path.exists():
        return {"error": f"対比結果がありません: {citation_id}"}, 404

    with open(resp_path, "r", encoding="utf-8") as f:
        response_data = json.load(f)

    # citation JSON / PDF の解決には id だけでなく label も試す
    # (例: id=特開2021-20391 / label=JP7088138B2 / input/JP7088138B2.pdf)
    label = ""
    for cit in meta.get("citations", []):
        if cit.get("id") == citation_id:
            label = (cit.get("label") or "").strip()
            break

    cit_path = case_dir / "citations" / f"{citation_id}.json"
    if not cit_path.exists() and label and label != citation_id:
        alt = case_dir / "citations" / f"{label}.json"
        if alt.exists():
            cit_path = alt
    if not cit_path.exists():
        return {"error": f"引用文献データがありません: {citation_id}"}, 404

    with open(cit_path, "r", encoding="utf-8") as f:
        citation_data = json.load(f)

    pdf_path = find_citation_pdf(case_dir / "input", citation_id)
    if not pdf_path and label and label != citation_id:
        pdf_path = find_citation_pdf(case_dir / "input", label)
    if not pdf_path:
        return {"error": f"引用文献PDFが見つかりません: {citation_id}"}, 404

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    safe_name = re.sub(r'[<>:"/\\|?*]', '_', citation_id)
    if force_new_file:
        # 強制再生成: タイムスタンプ付きで別名 (確実に新規ファイル)
        ts = datetime.now().strftime("%H%M%S")
        safe_name = f"{safe_name}_{ts}"

    try:
        result, actual_path = _write_annotated_pdf(
            pdf_path, case_dir / "output", safe_name,
            response_data, citation_data, keywords)
        return {
            "success": True,
            "filename": actual_path.name,
            "labels": result["labels"],
            "highlights": result["highlights"],
            "bookmarks": result["bookmarks"],
            "alt_filename": result.get("alt_filename", False),
        }, 200
    except Exception as e:
        return {"error": f"注釈生成エラー: {str(e)}"}, 500


def annotate_all_citations(case_id, max_workers=None):
    """全引用文献の注釈PDFを並列生成。

    max_workers=None の場合は CPU 論理コア数（最大でジョブ数まで）を使用。
    Ryzen 9 等の多コアCPUで実質フル稼働。GIL回避のため ProcessPool を使用。
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    output_dir = case_dir / "output"
    jobs = []
    pre_results = []
    for cit in meta.get("citations", []):
        cit_id = cit["id"]
        resp_path = case_dir / "responses" / f"{cit_id}.json"
        cit_path = case_dir / "citations" / f"{cit_id}.json"
        pdf_path = find_citation_pdf(case_dir / "input", cit_id)

        if not resp_path.exists() or not cit_path.exists() or not pdf_path:
            missing = []
            if not resp_path.exists():
                missing.append("回答")
            if not cit_path.exists():
                missing.append("引用文献データ")
            if not pdf_path:
                missing.append("元PDF")
            from modules.patent_downloader import build_jplatpat_url
            pre_results.append({
                "citation_id": cit_id, "success": False,
                "error": f"{'/'.join(missing)}がありません",
                "jplatpat_url": build_jplatpat_url(cit_id),
            })
            continue

        with open(resp_path, "r", encoding="utf-8") as f:
            response_data = json.load(f)
        with open(cit_path, "r", encoding="utf-8") as f:
            citation_data = json.load(f)
        # ProcessPool にピックルして渡すため Path は str 化
        jobs.append((cit_id, str(pdf_path), str(output_dir),
                     response_data, citation_data, keywords))

    results = list(pre_results)
    if jobs:
        workers = max_workers or (os.cpu_count() or 4)
        workers = max(1, min(workers, len(jobs)))
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_annotate_worker, j) for j in jobs]
            for fut in as_completed(futures):
                results.append(fut.result())

    success_count = sum(1 for r in results if r["success"])
    return {"results": results, "success_count": success_count,
            "workers_used": workers if jobs else 0}, 200


def _compare_execute_per_citation_parallel(
    *, case_id, citations, segs, keywords, hongan, field,
    model, known_cit_ids, max_workers=3, effort=None,
):
    """citation ごとに個別 prompt を生成して Claude を並列呼び出し。

    Sonnet/Haiku 専用の高速パス。1プロンプトに全 citation を統合する従来方式
    （Opus 用）と異なり、各 citation を別 Claude プロセスで処理することで:
      - 並列化で総所要時間を短縮（max_workers=3）
      - 1 件の失敗が他に波及しない
      - 各 prompt のサイズが小さいので Sonnet が読みやすい
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from modules.prompt_generator import generate_prompt as _gen
    from modules.response_parser import parse_response, split_multi_response
    from modules.claude_client import call_claude, ClaudeClientError

    case_dir = get_case_dir(case_id)
    responses_dir = case_dir / "responses"
    responses_dir.mkdir(parents=True, exist_ok=True)
    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    all_segment_ids = _get_all_segment_ids(segs)

    def _safe_label(cit):
        label = cit.get("patent_number") or cit.get("label") or cit.get("doc_number") or "unknown"
        return "".join(ch for ch in str(label) if ch not in '/\\:*?"<>|').strip() or "unknown"

    def _one(cit):
        safe_label = _safe_label(cit)
        try:
            prompt_text = _gen(segs, [cit], keywords, field, hongan=hongan)
        except Exception as e:
            return {"doc_id": safe_label, "ok": False,
                    "error": f"prompt生成失敗: {e}", "char_count": 0, "response_length": 0}

        try:
            with open(prompts_dir / f"compare_prompt_{safe_label}.txt", "w", encoding="utf-8") as f:
                f.write(prompt_text)
        except OSError:
            pass

        call_kwargs = {"timeout": 600, "model": model}
        if effort is not None:
            call_kwargs["effort"] = effort
        try:
            raw = call_claude(prompt_text, **call_kwargs)
        except ClaudeClientError as e:
            return {"doc_id": safe_label, "ok": False, "error": str(e),
                    "phase": "claude_call",
                    "char_count": len(prompt_text), "response_length": 0}

        try:
            with open(responses_dir / f"_raw_{safe_label}.txt", "w", encoding="utf-8") as f:
                f.write(raw)
        except OSError:
            pass

        result, errors = parse_response(raw, all_segment_ids)
        if not result:
            return {"doc_id": safe_label, "ok": False, "errors": errors,
                    "char_count": len(prompt_text), "response_length": len(raw)}

        per_doc = split_multi_response(result)
        saved = []
        resolved_log = []
        for doc_id, doc_result in per_doc.items():
            resolved = _resolve_doc_id(doc_id, known_cit_ids)
            if resolved != doc_id:
                resolved_log.append(f"{doc_id} → {resolved}")
            _normalize_cited_locations_inplace(doc_result)
            with open(responses_dir / f"{resolved}.json", "w", encoding="utf-8") as f:
                json.dump(doc_result, f, ensure_ascii=False, indent=2)
            saved.append(resolved)

        return {
            "doc_id": safe_label, "ok": True, "saved": saved,
            "errors": errors, "resolved": resolved_log,
            "char_count": len(prompt_text), "response_length": len(raw),
        }

    saved_docs = []
    all_errors = []
    resolved_log = []
    char_total = 0
    resp_total = 0

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_one, c): c for c in citations}
        for fut in as_completed(futures, timeout=1800):
            try:
                r = fut.result()
            except Exception as e:
                all_errors.append(f"_one fatal: {e}")
                continue
            char_total += r.get("char_count", 0)
            resp_total += r.get("response_length", 0)
            if r.get("ok"):
                saved_docs.extend(r.get("saved", []))
                if r.get("errors"):
                    all_errors.extend(f"{r['doc_id']}: {e}" for e in r["errors"])
                if r.get("resolved"):
                    resolved_log.extend(f"{r['doc_id']}: {x}" for x in r["resolved"])
            else:
                err_msg = r.get("error") or "; ".join(r.get("errors") or [])
                all_errors.append(f"{r['doc_id']}: {err_msg}")

    return {
        "success": len(saved_docs) > 0,
        "errors": all_errors,
        "saved_docs": saved_docs,
        "num_docs": len(saved_docs),
        "resolved": resolved_log,
        "char_count": char_total,
        "response_length": resp_total,
        "parallel": max_workers,
        "model": model,
        "mode_used": "legacy",  # 並列版は常に legacy 統合 prompt
        "fallback_to_legacy": False,
    }, 200


def compare_execute(case_id, citation_ids, model=None, mode="requirement_first", effort=None):
    """直接実行: 対比プロンプト → Claude CLI → パース

    Parameters:
        model: 'opus'/'sonnet'/'haiku' のエイリアスまたはフル ID。
               None の場合 CLI 既定 (通常 Opus)。
        mode: "requirement_first" (default, 推奨) = 構成要件主体型。
              本願はキーワード経由で必要箇所のみ抜粋。
              "legacy" = 本願全文を流す旧方式。
              keywords.json が無い案件では自動的に legacy にフォールバック。
        effort: 'low'/'medium'/'high'/'xhigh'/'max'。
                None なら call_claude のデフォルト (high)。
    """
    from modules.prompt_generator import (
        generate_prompt as _gen_legacy,
        generate_prompt_requirement_first as _gen_reqfirst,
    )
    from modules.response_parser import parse_response, split_multi_response
    from modules.claude_client import call_claude, ClaudeClientError

    # mode に応じて prompt 生成関数を切替
    _gen = _gen_reqfirst if mode == "requirement_first" else _gen_legacy

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
        cit_path = case_dir / "citations" / f"{cit_id}.json"
        if not cit_path.exists():
            return {"error": f"引用文献 '{cit_id}' が見つかりません"}, 404
        with open(cit_path, "r", encoding="utf-8") as f:
            cit = json.load(f)
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

    # mode 安全装置: requirement_first はキーワード経由で本願参酌を抽出するので、
    # keywords.json が無い (Step 3 未完了) 案件では効果が薄い → legacy にフォールバック
    fallback_to_legacy = False
    if mode == "requirement_first" and not (keywords or []):
        mode = "legacy"
        fallback_to_legacy = True
        # _gen を切り替え (mode が変わったので generate_prompt 系を選び直し)
        from modules.prompt_generator import generate_prompt as _gen_legacy_fb
        _gen = _gen_legacy_fb

    field = meta.get("field", "cosmetics")
    hongan = None
    hongan_path = case_dir / "hongan.json"
    if hongan_path.exists():
        with open(hongan_path, "r", encoding="utf-8") as f:
            hongan = json.load(f)

    # 並列実行は環境変数 COMPARE_PARALLEL=N (N>=2) で明示的に有効化したときのみ。
    # 過去に Sonnet*3 並列でかえって遅くなる事例あり（CLI 起動オーバーヘッド +
    # prompt cache が効かない+ session が分散するため）。デフォルトは Opus と
    # 同じ「1 プロンプトに全 citation を統合」方式とする。
    import os as _os
    try:
        parallel_workers = int(_os.environ.get("COMPARE_PARALLEL", "0"))
    except ValueError:
        parallel_workers = 0
    model_l = (model or "").lower()
    is_lightweight = ("sonnet" in model_l) or ("haiku" in model_l)
    if parallel_workers >= 2 and is_lightweight and len(citations) >= 2:
        known_cit_ids = [c.get("id") for c in (meta or {}).get("citations", []) if c.get("id")]
        return _compare_execute_per_citation_parallel(
            case_id=case_id, citations=citations, segs=segs,
            keywords=keywords, hongan=hongan, field=field,
            model=model, known_cit_ids=known_cit_ids,
            max_workers=parallel_workers, effort=effort,
        )

    prompt_text = _gen(segs, citations, keywords, field, hongan=hongan)

    ids_label = "_".join(citation_ids)
    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    with open(prompts_dir / f"{ids_label}_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt_text)

    timeout = 600 if len(citations) <= 2 else 900
    call_kwargs = {"timeout": timeout, "model": model}
    if effort is not None:
        call_kwargs["effort"] = effort
    try:
        raw_response = call_claude(prompt_text, **call_kwargs)
    except ClaudeClientError as e:
        return {"error": str(e), "phase": "claude_call"}, 502

    all_segment_ids = _get_all_segment_ids(segs)

    raw_path = case_dir / "responses" / "_last_raw_response.txt"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(raw_response)

    result, errors = parse_response(raw_response, all_segment_ids)

    # case.yaml の citations から既知 ID を取得し、LLM 応答の document_id を
    # _resolve_doc_id で吸着 (例: 'JP5214138B2' → 'JP5214138')。
    # これをやらないと 'JP5214138B2.json' で保存されて Step 6 が拾えなくなる
    # (silent stale と同種の片手落ち)。save_response_multi 側と同じロジック。
    known_cit_ids = [c.get("id") for c in (meta or {}).get("citations", []) if c.get("id")]

    saved_docs = []
    resolved_log = []  # 解決マッピングのデバッグ情報
    if result:
        per_doc = split_multi_response(result)
        responses_dir = case_dir / "responses"
        responses_dir.mkdir(parents=True, exist_ok=True)
        for doc_id, doc_result in per_doc.items():
            resolved = _resolve_doc_id(doc_id, known_cit_ids)
            if resolved != doc_id:
                resolved_log.append(f"{doc_id} → {resolved}")
            _normalize_cited_locations_inplace(doc_result)
            resp_path = responses_dir / f"{resolved}.json"
            with open(resp_path, "w", encoding="utf-8") as f:
                json.dump(doc_result, f, ensure_ascii=False, indent=2)
            saved_docs.append(resolved)

    return {
        "success": result is not None,
        "errors": errors,
        "saved_docs": saved_docs,
        "num_docs": len(saved_docs),
        "resolved": resolved_log,  # ID 吸着の履歴 (デバッグ用)
        "char_count": len(prompt_text),
        "response_length": len(raw_response),
        "mode_used": mode,
        "fallback_to_legacy": fallback_to_legacy,
    }, 200


def inventive_step_prompt(case_id):
    """進歩性判断プロンプトを生成"""
    from modules.inventive_step_analyzer import generate_inventive_step_prompt

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    segments_path = case_dir / "segments.json"
    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    responses = {}
    responses_dir = case_dir / "responses"
    if responses_dir.exists():
        for rfile in responses_dir.glob("*.json"):
            with open(rfile, "r", encoding="utf-8") as f:
                responses[rfile.stem] = json.load(f)

    if not responses:
        return {"error": "対比結果がありません。Step 5を完了してください。"}, 400

    citations_meta = {}
    for cit in meta.get("citations", []):
        cit_path = case_dir / "citations" / f"{cit['id']}.json"
        if cit_path.exists():
            with open(cit_path, "r", encoding="utf-8") as f:
                citations_meta[cit["id"]] = json.load(f)

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    field = meta.get("field", "cosmetics")
    prompt_text = generate_inventive_step_prompt(segs, responses, citations_meta, keywords, field)

    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    with open(prompts_dir / "inventive_step_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt_text)

    return {"prompt": prompt_text, "char_count": len(prompt_text)}, 200


def inventive_step_response(case_id, raw_text):
    """進歩性判断の回答をパース"""
    from modules.inventive_step_analyzer import parse_inventive_step_response

    case_dir = get_case_dir(case_id)
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404

    if not raw_text.strip():
        return {"error": "テキストが空です"}, 400

    data, errors = parse_inventive_step_response(raw_text)

    if data:
        with open(case_dir / "inventive_step.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    return {"success": data is not None, "data": data, "errors": errors}, 200


def inventive_step_execute(case_id, model=None):
    """直接実行: 進歩性判断プロンプト → Claude CLI → パース"""
    from modules.inventive_step_analyzer import (
        generate_inventive_step_prompt, parse_inventive_step_response
    )
    from modules.claude_client import call_claude, ClaudeClientError

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    segments_path = case_dir / "segments.json"
    if not segments_path.exists():
        return {"error": "分節データがありません"}, 400

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    responses = {}
    responses_dir = case_dir / "responses"
    if responses_dir.exists():
        for rfile in responses_dir.glob("*.json"):
            with open(rfile, "r", encoding="utf-8") as f:
                responses[rfile.stem] = json.load(f)

    if not responses:
        return {"error": "対比結果がありません。Step 5を完了してください。"}, 400

    citations_meta = {}
    for cit in meta.get("citations", []):
        cit_path = case_dir / "citations" / f"{cit['id']}.json"
        if cit_path.exists():
            with open(cit_path, "r", encoding="utf-8") as f:
                citations_meta[cit["id"]] = json.load(f)

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    field = meta.get("field", "cosmetics")
    prompt_text = generate_inventive_step_prompt(
        segs, responses, citations_meta, keywords, field
    )

    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    with open(prompts_dir / "inventive_step_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt_text)

    try:
        raw_response = call_claude(prompt_text, timeout=600, model=model)
    except ClaudeClientError as e:
        return {"error": str(e), "phase": "claude_call"}, 502

    data, errors = parse_inventive_step_response(raw_response)

    if data:
        with open(case_dir / "inventive_step.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    return {
        "success": data is not None,
        "data": data,
        "errors": errors,
        "char_count": len(prompt_text),
        "response_length": len(raw_response),
    }, 200
