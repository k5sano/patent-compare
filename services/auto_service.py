#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""オートモード（バッチ自動処理）サービス"""

import json
import logging
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from services.case_service import (
    get_case_dir, load_case_meta, save_case_meta,
    load_search_data, save_search_data,
)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# リトライ付き Claude CLI 呼び出し
# ------------------------------------------------------------------

def _call_claude_with_retry(prompt_text, timeout=600, use_search=False,
                            max_retries=3, base_delay=30):
    """Claude CLI をリトライ付きで呼び出す。

    エラーコード1（レートリミット等）の場合、指数バックオフで再試行する。
    """
    from modules.claude_client import call_claude, ClaudeClientError

    last_error = None
    for attempt in range(max_retries):
        try:
            return call_claude(prompt_text, timeout=timeout, use_search=use_search)
        except ClaudeClientError as e:
            last_error = e
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)  # 30s, 60s, 120s
                logger.warning("Claude CLI エラー (試行%d/%d): %s — %d秒後にリトライ",
                               attempt + 1, max_retries, str(e)[:100], delay)
                time.sleep(delay)
            else:
                logger.error("Claude CLI エラー (最終試行): %s", str(e)[:200])
    raise last_error


# ------------------------------------------------------------------
# ユーティリティ
# ------------------------------------------------------------------

def _sse_event(event_type, data):
    """SSEイベント文字列を生成"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ------------------------------------------------------------------
# 各ステップの実装
# ------------------------------------------------------------------

def _auto_suggest_keywords(case_dir, segs, hongan, field):
    """キーワード自動提案"""
    from modules.keyword_recommender import recommend_by_tech_analysis
    from modules.keyword_suggester import build_keyword_groups_from_pipeline

    tech_analysis, pipeline_result = recommend_by_tech_analysis(segs, hongan, field)

    if tech_analysis:
        with open(case_dir / "tech_analysis.json", "w", encoding="utf-8") as f:
            json.dump(tech_analysis, f, ensure_ascii=False, indent=2)

    with open(case_dir / "segment_keywords.json", "w", encoding="utf-8") as f:
        json.dump(pipeline_result, f, ensure_ascii=False, indent=2)

    ai_groups = build_keyword_groups_from_pipeline(
        pipeline_result, segs, field, hongan=hongan
    )

    with open(case_dir / "keywords.json", "w", encoding="utf-8") as f:
        json.dump(ai_groups, f, ensure_ascii=False, indent=2)

    return ai_groups


def _auto_presearch(case_dir, segs, hongan, field, meta):
    """予備検索: プロンプト生成 → リアル検索注入 → Claude CLI → パース → 検証 → 保存"""
    from modules.search_prompt_generator import generate_presearch_prompt, parse_presearch_response
    from modules.search_injector import inject_search_results

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    # tech_analysis.json が既に存在すれば読み込んで検索キーワード抽出に活用
    ta = load_search_data(case_dir, "tech_analysis.json")

    prompt = generate_presearch_prompt(segs, hongan, keywords, field, case_meta=meta)

    # Playwright事前検索（Google Patents）を注入
    prompt = inject_search_results(prompt, segs, keywords, field, tech_analysis=ta)

    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    with open(prompts_dir / "presearch_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt)

    # MCP検索サーバーも有効化（リトライ付き）
    raw_response = _call_claude_with_retry(prompt, timeout=600, use_search=True)

    tech_analysis, candidates, search_formulas, errors = parse_presearch_response(raw_response)

    if tech_analysis:
        save_search_data(case_dir, "tech_analysis.json", tech_analysis)
    if candidates:
        # 候補文献の実在性をGoogle Patentsで検証
        candidates = _verify_candidates(candidates)
        save_search_data(case_dir, "presearch_candidates.json", candidates)
    if search_formulas:
        save_search_data(case_dir, "presearch_formulas.json", search_formulas)

    return candidates or []


def _verify_candidates(candidates):
    """候補文献の特許番号がGoogle Patentsで実在するか並列検証する

    非特許文献（論文・規格等）はスキップ。
    実在確認できた場合は出願人情報を上書きし verified=True フラグを付与。
    最大6並列でPlaywright検索を実行し、全体60秒でタイムアウト。
    """
    from modules.google_patents_scraper import search_google_patents

    # 検証対象と非対象を分離
    to_verify = []  # (index, candidate, patent_id)
    for i, c in enumerate(candidates):
        pid = c.get("patent_id", c.get("doc_number", ""))
        if not pid:
            continue
        if any(tag in pid for tag in ("論文", "規格", "製品", "[Scholar]")):
            c["verified"] = None  # 検証対象外
            continue
        to_verify.append((i, c, pid))

    if not to_verify:
        return candidates

    def _check_one(pid):
        """1件の特許番号を検証"""
        return search_google_patents(pid, max_results=1)

    # Google Patents へのアクセスは google_patents_throttle で2秒間隔に
    # スロットルされる（並列起動しても直列化されるだけ）。
    # ThreadPool構造は維持しつつ max_workers=1 でロボット判定回避を明示。
    logger.info("候補検証開始: %d件を逐次検証（GPレート制御）", len(to_verify))
    with ThreadPoolExecutor(max_workers=1) as executor:
        future_map = {
            executor.submit(_check_one, pid): (i, c, pid)
            for i, c, pid in to_verify
        }
        try:
            for future in as_completed(future_map, timeout=60):
                idx, c, pid = future_map[future]
                try:
                    hits = future.result()
                    if hits:
                        c["verified"] = True
                        if hits[0].assignee:
                            c["applicant"] = hits[0].assignee
                        if hits[0].title:
                            c["title_verified"] = hits[0].title
                        logger.info("候補検証OK: %s → %s", pid, hits[0].assignee or "(出願人不明)")
                    else:
                        c["verified"] = False
                        logger.warning("候補検証NG: %s — Google Patentsで見つかりません", pid)
                except Exception as e:
                    c["verified"] = False
                    logger.warning("候補検証エラー: %s — %s", pid, e)
        except Exception:
            logger.warning("候補検証タイムアウト（60秒）— 検証済み分で続行")

    verified_count = sum(1 for c in candidates if c.get("verified") is True)
    logger.info("候補検証完了: %d/%d件が実在確認", verified_count, len(to_verify))
    return candidates


def _auto_download_citations(case_id, case_dir, meta):
    """候補文献のPDFダウンロード + テキスト抽出（並列）"""
    from modules.patent_downloader import download_patent_pdf_smart, build_jplatpat_url
    from modules.pdf_extractor import extract_patent_pdf

    candidates = load_search_data(case_dir, "presearch_candidates.json")
    if not candidates or not isinstance(candidates, list):
        return {"total": 0, "downloaded": 0, "errors": ["候補がありません"]}

    # 候補をスコア順にソートし、上位6件を対象
    # relevance_score があればそれを使い、なければ出現順
    scored = []
    for c in candidates:
        score = c.get("relevance_score", 0)
        rel = c.get("relevance", c.get("role", ""))
        # 主引例・X引例を優先
        if any(k in rel for k in ("主引例", "X引例", "X/Y")):
            score = max(score, 10)
        elif any(k in rel for k in ("副引例", "Y引例")):
            score = max(score, 5)
        scored.append((score, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    targets = [c for _, c in scored[:6]]

    (case_dir / "citations").mkdir(parents=True, exist_ok=True)

    import re as _re

    def _clean_patent_id(raw_id):
        """patent_idから余計な文字を除去して正規化"""
        if not raw_id:
            return ""
        # 括弧付き注釈を除去: （推定）、(本願自身)、(推定: ...)等
        cleaned = _re.sub(r'[\(（][^)）]*[\)）]', '', raw_id).strip()
        # XXXXXX等のプレースホルダーを含む場合は無効
        if 'XXXX' in cleaned or 'xxxx' in cleaned:
            return ""
        # 「推定:」「推定：」を含む場合も無効
        if '推定' in cleaned:
            return ""
        # 先頭の「推定: 」等のプレフィックスを除去
        cleaned = _re.sub(r'^[^A-Za-z0-9]*', '', cleaned)
        # 末尾のゴミを除去
        cleaned = _re.sub(r'[^A-Za-z0-9]+$', '', cleaned)
        return cleaned

    # 既にダウンロード済みのものを除外
    to_download = []
    already_done = 0
    for c in targets:
        patent_id = _clean_patent_id(c.get("patent_id", c.get("doc_number", "")))
        if not patent_id:
            continue
        doc_id = patent_id
        for ch in '/\\:*?"<>| ':
            doc_id = doc_id.replace(ch, '')
        cit_path = case_dir / "citations" / f"{doc_id}.json"
        if cit_path.exists():
            already_done += 1
        else:
            to_download.append((c, patent_id, doc_id))

    if not to_download:
        return {"total": len(targets), "downloaded": already_done, "errors": []}

    meta_lock = threading.Lock()

    def _download_one(candidate, patent_id, doc_id):
        """1件のPDFダウンロード + テキスト抽出"""
        dl_result = download_patent_pdf_smart(
            patent_id, case_dir / "input", timeout=60, headless=True,
        )
        if not dl_result["success"]:
            return None, f"{patent_id}: DL失敗 - {dl_result.get('error', '')}"

        extracted = extract_patent_pdf(dl_result["path"], "citation")
        actual_doc_id = extracted.get("patent_number", doc_id)
        for ch in '/\\:*?"<>| ':
            actual_doc_id = actual_doc_id.replace(ch, '')

        rel = candidate.get("relevance", candidate.get("role", "副引例候補"))
        extracted["role"] = rel
        extracted["label"] = patent_id

        with open(case_dir / "citations" / f"{actual_doc_id}.json", "w", encoding="utf-8") as f:
            json.dump(extracted, f, ensure_ascii=False, indent=2)

        return (actual_doc_id, rel, patent_id), None

    downloaded = already_done
    errors = []
    failed_pids = set()
    # Google Patents へのアクセスは google_patents_throttle で2秒間隔にスロットル
    # されるため、ThreadPool は形だけ残して max_workers=1 で逐次化（ロボット判定回避）
    logger.info("引例DL開始: %d件を逐次ダウンロード（%d件はキャッシュ済み, GPレート制御）",
                len(to_download), already_done)

    with ThreadPoolExecutor(max_workers=1) as executor:
        futures = {
            executor.submit(_download_one, c, pid, did): (pid, c)
            for c, pid, did in to_download
        }
        for future in as_completed(futures, timeout=300):
            pid, candidate = futures[future]
            try:
                result, error = future.result()
                if error:
                    errors.append(error)
                    failed_pids.add(pid)
                elif result:
                    actual_doc_id, rel, patent_id = result
                    # meta更新はスレッドセーフに
                    with meta_lock:
                        citations = meta.get("citations", [])
                        if not any(ct["id"] == actual_doc_id for ct in citations):
                            citations.append({"id": actual_doc_id, "role": rel, "label": patent_id})
                            meta["citations"] = citations
                            save_case_meta(case_id, meta)
                    downloaded += 1
                    logger.info("引例DL完了: %s", pid)
            except Exception as e:
                errors.append(f"{pid}: {str(e)}")
                failed_pids.add(pid)
                logger.warning("引例DLエラー: %s — %s", pid, e)

    # DL失敗した候補に confidence:"low" と J-PlatPat URL を付与
    if failed_pids:
        cand_data = load_search_data(case_dir, "presearch_candidates.json")
        if cand_data and isinstance(cand_data, list):
            updated = False
            for c in cand_data:
                pid = c.get("patent_id", "")
                if pid in failed_pids:
                    c["confidence"] = "low"
                    c["dl_failed"] = True
                    jplatpat = build_jplatpat_url(pid)
                    if jplatpat:
                        c["jplatpat_url"] = jplatpat
                    updated = True
            if updated:
                save_search_data(case_dir, "presearch_candidates.json", cand_data)
        logger.info("DL失敗 %d件に confidence=low を付与", len(failed_pids))

    logger.info("引例DL完了: %d/%d件成功", downloaded, len(targets))
    return {"total": len(targets), "downloaded": downloaded, "errors": errors}


def _auto_compare(case_id, case_dir, segs, meta, field):
    """対比: 引用文献ごとに個別プロンプト生成 → 並列Claude実行 → パース

    引例を1件ずつ個別にClaudeへ送ることで:
    - コンテキストサイズを削減（全引例まとめより大幅に小さい）
    - 並列実行で総所要時間を短縮
    - 1件の失敗が他に影響しない
    """
    from modules.prompt_generator import generate_prompt as _gen
    from modules.response_parser import parse_response, split_multi_response

    # metaを最新のファイルから再読込（DLステップでmeta更新済み）
    meta = load_case_meta(case_id) or meta

    from services.comparison_service import _is_empty_citation

    citations_map = {}  # doc_id -> citation_data
    skipped_empty = []  # テキスト抽出失敗で対比不能だったID
    responses_dir = case_dir / "responses"
    for cit in meta.get("citations", []):
        cit_path = case_dir / "citations" / f"{cit['id']}.json"
        resp_path = responses_dir / f"{cit['id']}.json"
        # 既にresponseが存在する引用文献はスキップ
        if cit_path.exists() and not resp_path.exists():
            with open(cit_path, "r", encoding="utf-8") as f:
                cit_data = json.load(f)
            if _is_empty_citation(cit_data):
                skipped_empty.append(cit["id"])
                logger.warning(
                    "引用文献 %s: テキスト未抽出のため対比をスキップ", cit["id"]
                )
                continue
            citations_map[cit["id"]] = cit_data

    if not citations_map:
        # 全件回答済みか、引用文献なし
        existing = len([c for c in meta.get("citations", [])
                       if (responses_dir / f"{c['id']}.json").exists()]) if responses_dir.exists() else 0
        if existing > 0:
            return {"num_docs": existing, "errors": [], "skipped": True,
                    "skipped_empty": skipped_empty}
        if skipped_empty:
            raise Exception(
                "対比できる引用文献がありません: "
                + ", ".join(skipped_empty)
                + " はテキスト抽出に失敗しています (スキャン画像PDFの可能性)"
            )
        raise Exception("引用文献がありません")

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    all_segment_ids = []
    for claim in segs:
        for seg in claim["segments"]:
            all_segment_ids.append(seg["id"])

    responses_dir = case_dir / "responses"
    responses_dir.mkdir(parents=True, exist_ok=True)
    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    def _compare_one(doc_id, citation_data):
        """1件の引用文献に対して対比を実行"""
        prompt_text = _gen(segs, [citation_data], keywords, field)

        # 個別プロンプトも保存
        with open(prompts_dir / f"compare_prompt_{doc_id}.txt", "w", encoding="utf-8") as f:
            f.write(prompt_text)

        raw_response = _call_claude_with_retry(prompt_text, timeout=600)

        with open(responses_dir / f"_raw_{doc_id}.txt", "w", encoding="utf-8") as f:
            f.write(raw_response)

        result, errors = parse_response(raw_response, all_segment_ids)
        return doc_id, result, errors

    saved_docs = []
    all_errors = []
    logger.info("対比実行開始: %d件を並列処理", len(citations_map))

    with ThreadPoolExecutor(max_workers=min(len(citations_map), 3)) as executor:
        futures = {
            executor.submit(_compare_one, doc_id, cit_data): doc_id
            for doc_id, cit_data in citations_map.items()
        }
        for future in as_completed(futures, timeout=900):
            doc_id = futures[future]
            try:
                doc_id, result, errors = future.result()
                all_errors.extend(errors or [])
                if result:
                    per_doc = split_multi_response(result)
                    for rid, doc_result in per_doc.items():
                        resp_path = responses_dir / f"{rid}.json"
                        with open(resp_path, "w", encoding="utf-8") as f:
                            json.dump(doc_result, f, ensure_ascii=False, indent=2)
                        saved_docs.append(rid)
                    logger.info("対比完了: %s", doc_id)
                else:
                    all_errors.append(f"{doc_id}: パース結果なし")
                    logger.warning("対比パース失敗: %s", doc_id)
            except Exception as e:
                all_errors.append(f"{doc_id}: {str(e)}")
                logger.warning("対比エラー: %s — %s", doc_id, e)

    logger.info("対比完了: %d/%d件成功", len(saved_docs), len(citations_map))
    if skipped_empty:
        all_errors.append(
            "テキスト未抽出のため対比をスキップ: " + ", ".join(skipped_empty)
        )
    return {"num_docs": len(saved_docs), "errors": all_errors,
            "skipped_empty": skipped_empty}


def _auto_export_excel(case_dir, segs, meta):
    """Excel出力"""
    from modules.excel_writer import write_comparison_table

    responses = {}
    responses_dir = case_dir / "responses"
    if responses_dir.exists():
        for rfile in responses_dir.glob("*.json"):
            if rfile.stem.startswith("_"):
                continue
            with open(rfile, "r", encoding="utf-8") as f:
                responses[rfile.stem] = json.load(f)

    if not responses:
        raise Exception("回答データがありません")

    citations_meta = {}
    for cit in meta.get("citations", []):
        cit_path = case_dir / "citations" / f"{cit['id']}.json"
        if cit_path.exists():
            with open(cit_path, "r", encoding="utf-8") as f:
                citations_meta[cit["id"]] = json.load(f)

    output_dir = case_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{meta['case_id']}_対比表.xlsx"

    write_comparison_table(
        output_path=str(output_path),
        case_meta=meta,
        segments=segs,
        responses=responses,
        citations_meta=citations_meta,
    )

    return output_path


# ------------------------------------------------------------------
# 請求項1充足チェック・追加サーチ
# ------------------------------------------------------------------

def _check_claim1_coverage(case_dir, segs):
    """請求項1の全分節がX文献で○になっているかチェック。

    Returns:
        dict: {
            "covered": bool,         # 全分節が充足されているか
            "claim1_ids": [str],     # 請求項1の分節IDリスト
            "uncovered_ids": [str],  # 充足されていない分節IDリスト
            "x_count": int,          # X文献数
            "y_count": int,          # Y文献数
            "best_x_coverage": {},   # 最もカバー率の高いX文献の分析
        }
    """
    # 請求項1の分節IDを取得
    claim1_ids = []
    for claim in segs:
        if claim.get("claim_number") == 1 or claim.get("is_independent", False):
            claim1_ids = [s["id"] for s in claim.get("segments", [])]
            break

    if not claim1_ids:
        return {"covered": True, "claim1_ids": [], "uncovered_ids": [],
                "x_count": 0, "y_count": 0, "best_x_coverage": {}}

    responses_dir = case_dir / "responses"
    if not responses_dir.exists():
        return {"covered": False, "claim1_ids": claim1_ids,
                "uncovered_ids": claim1_ids, "x_count": 0, "y_count": 0,
                "best_x_coverage": {}}

    # 全responseを読み込んでX/Y分類とカバレッジを分析
    x_docs = {}  # doc_id -> {seg_id: judgment}
    y_count = 0
    for rfile in responses_dir.glob("*.json"):
        if rfile.stem.startswith("_"):
            continue
        try:
            with open(rfile, "r", encoding="utf-8") as f:
                rdata = json.loads(f.read().replace('\x00', ''), strict=False)
            cat = (rdata.get("category_suggestion", "") or "").upper()
            if cat.startswith("Y"):
                y_count += 1
            elif cat.startswith("X"):
                coverage = {}
                for comp in rdata.get("comparisons", []):
                    rid = comp.get("requirement_id", "")
                    if rid in claim1_ids:
                        coverage[rid] = comp.get("judgment", "")
                x_docs[rfile.stem] = coverage
        except Exception:
            pass

    # X文献のいずれかで全分節が○かチェック
    for doc_id, coverage in x_docs.items():
        if all(coverage.get(sid, "") == "\u25cb" for sid in claim1_ids):
            return {"covered": True, "claim1_ids": claim1_ids,
                    "uncovered_ids": [], "x_count": len(x_docs),
                    "y_count": y_count, "best_x_coverage": {}}

    # 全X文献を合算して、どの分節が充足されていないか
    covered_by_any_x = set()
    best_x = {}
    best_x_count = 0
    for doc_id, coverage in x_docs.items():
        matched = sum(1 for sid in claim1_ids if coverage.get(sid, "") == "\u25cb")
        if matched > best_x_count:
            best_x_count = matched
            best_x = {"doc_id": doc_id, "matched": matched,
                      "total": len(claim1_ids), "coverage": coverage}
        for sid in claim1_ids:
            if coverage.get(sid, "") == "\u25cb":
                covered_by_any_x.add(sid)

    uncovered = [sid for sid in claim1_ids if sid not in covered_by_any_x]

    return {
        "covered": False,
        "claim1_ids": claim1_ids,
        "uncovered_ids": uncovered,
        "x_count": len(x_docs),
        "y_count": y_count,
        "best_x_coverage": best_x,
    }


def _build_additional_search_prompt(segs, hongan, keywords, field, uncovered_ids, coverage_info, meta):
    """不充足分節に焦点を当てた追加サーチプロンプトを生成"""
    # 不充足分節のテキストを収集
    uncovered_segments = []
    for claim in segs:
        for seg in claim.get("segments", []):
            if seg["id"] in uncovered_ids:
                uncovered_segments.append(seg)

    seg_text = "\n".join(f'[{s["id"]}] {s["text"]}' for s in uncovered_segments)

    # 既存引用文献リスト
    existing_docs = []
    best = coverage_info.get("best_x_coverage", {})
    if best:
        existing_docs.append(f'最良X文献: {best.get("doc_id", "?")} '
                            f'({best.get("matched", 0)}/{best.get("total", 0)}分節充足)')

    field_label = {"cosmetics": "化粧品", "laminate": "積層体"}.get(field, field)

    # 明細書から不充足分節の関連記述を抽出
    spec_hints = []
    if hongan:
        for seg in uncovered_segments:
            seg_text_clean = seg["text"][:50]
            for para in hongan.get("paragraphs", [])[:200]:
                ptext = para.get("text", "")
                # 分節テキストのキーワードが明細書に含まれるか簡易チェック
                keywords_in_seg = [w for w in seg_text_clean.split() if len(w) >= 3]
                if any(kw in ptext for kw in keywords_in_seg[:3]):
                    spec_hints.append(f'【{para["id"]}】{ptext[:100]}')
                    if len(spec_hints) >= 10:
                        break
            if len(spec_hints) >= 10:
                break

    prompt = f"""あなたは{field_label}分野の特許調査の専門家です。

## 追加サーチ依頼

以下の特許請求項の構成要件のうち、先行技術調査で**充足するX引例が見つかっていない分節**があります。
これらの分節を充足しうる先行技術文献を追加で探してください。

## 不充足分節（これらを開示する文献を探す）
{seg_text}

## 既存の調査状況
- X文献: {coverage_info.get('x_count', 0)}件、Y文献: {coverage_info.get('y_count', 0)}件
{chr(10).join('- ' + d for d in existing_docs)}
- 上記の文献では以下の分節が充足されていません: {', '.join(uncovered_ids)}

"""

    if spec_hints:
        prompt += f"""## 明細書の関連記述（参考）
{chr(10).join(spec_hints[:8])}

"""

    # キーワードがあれば不充足分節のキーワードを追加
    if keywords:
        kw_lines = []
        for group in keywords:
            seg_ids = group.get("segment_ids", [])
            if any(sid in uncovered_ids for sid in seg_ids):
                terms = [kw["term"] for kw in group.get("keywords", [])[:10]]
                if terms:
                    kw_lines.append(f'  {group.get("label", "")}: {", ".join(terms)}')
        if kw_lines:
            prompt += f"""## 関連キーワード
{chr(10).join(kw_lines)}

"""

    prompt += """## 指示
1. 上記の不充足分節を**直接的に開示している**先行技術文献を探してください
2. 特に実施例・具体的な記載で充足できるX引例候補を優先
3. 既に見つかっている文献とは**異なる文献**を提案すること

## 出力形式（JSONのみ、説明文不要）
```json
[
  {
    "patent_id": "JP2020-123456A",
    "title": "発明の名称",
    "applicant": "出願人",
    "relevance": "主引例候補",
    "relevant_segments": ["1E", "1F"],
    "reason": "この文献が不充足分節をどのように充足するかの説明",
    "confidence": "high/medium/low",
    "relevance_score": 5
  }
]
```

## ルール
- patent_id はできるだけ実在する特許番号を記載（知識から想起できるもの）
- 検索ツールが使えない場合でも、知識に基づいて候補を提案してください
- 最大10件まで
- relevant_segments には不充足分節のIDのみ記載
"""
    return prompt


def _auto_additional_search(case_dir, segs, hongan, field, meta, uncovered_ids, coverage_info):
    """不充足分節に焦点を当てた追加サーチを実行"""
    from modules.search_prompt_generator import parse_presearch_response

    keywords = None
    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    prompt = _build_additional_search_prompt(
        segs, hongan, keywords, field, uncovered_ids, coverage_info, meta
    )

    prompts_dir = case_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    with open(prompts_dir / "additional_search_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt)

    # 検索ツール付きで試行、失敗したら検索なしでフォールバック
    try:
        raw_response = _call_claude_with_retry(prompt, timeout=600, use_search=True,
                                                max_retries=1, base_delay=10)
    except Exception:
        logger.info("追加サーチ: 検索ツール付き失敗 → 検索なしでフォールバック")
        raw_response = _call_claude_with_retry(prompt, timeout=600, use_search=False)

    # デバッグ用にraw responseを保存
    with open(prompts_dir / "additional_search_response.txt", "w", encoding="utf-8") as f:
        f.write(raw_response)

    # パース
    from modules.json_utils import extract_json_array, extract_json_object
    candidates = extract_json_array(raw_response)
    # オブジェクト形式で返ってきた場合の対応（{candidates: [...]}等）
    if not candidates:
        obj = extract_json_object(raw_response)
        if obj:
            for key in ("candidates", "results", "patents", "references"):
                if isinstance(obj.get(key), list):
                    candidates = obj[key]
                    break

    if candidates:
        from modules.patent_downloader import build_jplatpat_url, build_google_patents_url
        # 既存候補とマージして保存
        existing = load_search_data(case_dir, "presearch_candidates.json") or []
        existing_ids = {c.get("patent_id", "") for c in existing}
        added = []
        for c in candidates:
            pid = c.get("patent_id", "")
            if pid and pid not in existing_ids:
                c["source"] = "additional_search"
                # J-PlatPat / Google Patents URL を付与
                jp_url = build_jplatpat_url(pid)
                if jp_url:
                    c["jplatpat_url"] = jp_url
                gp_url = build_google_patents_url(pid)
                if gp_url:
                    c["google_patents_url"] = gp_url
                existing.append(c)
                added.append(c)
                existing_ids.add(pid)

        if added:
            save_search_data(case_dir, "presearch_candidates.json", existing)

        return added

    return []


# ------------------------------------------------------------------
# メインエントリポイント（SSEジェネレータ）
# ------------------------------------------------------------------

def _auto_formula_search(case_dir, case_id, meta, *, source="google_patents",
                         levels=("narrow", "medium"), max_results=30,
                         auto_dl_top_n=0, score_threshold=70):
    """Stage 3 の search_formulas から検索式を実行して候補を収集し
    search_run として保存する。auto_dl_top_n > 0 の場合、AI スコア上位 N 件を
    自動で引用文献に登録する。"""
    from services.search_run_service import (
        get_formulas_from_keyword_dict, create_run_from_hits,
        enrich_run, ai_score_run, load_run, mark_downloaded,
    )
    formulas = get_formulas_from_keyword_dict(case_id)
    if not formulas:
        return {"runs": 0, "total_hits": 0, "downloaded": 0}

    total_hits = 0
    runs_created = []

    for level in levels:
        spec = formulas.get(level)
        if not spec:
            continue
        formula = (spec.get("formula_google_patents") if source == "google_patents"
                   else spec.get("formula_jplatpat")) or ""
        formula = formula.strip()
        if not formula:
            continue

        if source == "google_patents":
            from modules.google_patents_scraper import search_google_patents
            raw = search_google_patents(formula, max_results=max_results)
            hits = [{
                "patent_id": h.patent_id, "title": h.title,
                "applicant": h.assignee, "publication_date": h.priority_date,
                "url": h.url,
            } for h in raw]
        elif source == "jplatpat":
            # auto モードでは visible モードは避ける。必要なら別フラグで
            from modules.jplatpat_client import run_jplatpat_search
            raw = run_jplatpat_search(formula, max_results=max_results,
                                       auto_click_search=True)
            hits = [h.to_dict() for h in raw]
        else:
            continue

        total_hits += len(hits)
        run_data = create_run_from_hits(
            case_id, formula=formula, formula_level=level,
            source=source, hits=hits,
        )
        runs_created.append(run_data["run_id"])

    if not runs_created:
        return {"runs": 0, "total_hits": 0, "downloaded": 0}

    # auto_dl_top_n > 0 ならスコアリング & 上位N件を自動DL
    downloaded = 0
    if auto_dl_top_n > 0:
        for rid in runs_created:
            try:
                enrich_run(case_id, rid, limit=max_results)
                ai_score_run(case_id, rid, limit=max_results)
            except Exception as e:
                logger.warning("auto enrich/score error: %s", e)
                continue

            data = load_run(case_id, rid)
            if not data:
                continue
            scored = sorted(
                [h for h in data["hits"] if h.get("ai_score") is not None],
                key=lambda h: h["ai_score"], reverse=True,
            )[:auto_dl_top_n]
            top = [h for h in scored if h["ai_score"] >= score_threshold]
            if not top:
                continue

            from services.search_service import search_download
            pids = [h["patent_id"] for h in top if h.get("patent_id")]
            if pids:
                try:
                    result, _ = search_download(case_id, pids, role="副引例")
                    for r in (result.get("results") or []):
                        if r.get("success"):
                            mark_downloaded(case_id, rid, r.get("patent_id"), True)
                            downloaded += 1
                except Exception as e:
                    logger.warning("auto search_download error: %s", e)

    return {"runs": len(runs_created), "total_hits": total_hits, "downloaded": downloaded}


def feedback_not_terms(case_id: str, min_occurrences: int = 2) -> dict:
    """スクリーニング結果 (reject) から NOT 語候補を抽出する。

    reject された候補の title / abstract / applicant に頻出するが、
    star/triangle の候補には出現しない語を NOT 語として提案。
    """
    from services.search_run_service import list_runs, load_run
    from collections import Counter
    import re as _re

    reject_tokens = Counter()
    positive_tokens = Counter()

    for run_summary in list_runs(case_id):
        data = load_run(case_id, run_summary["run_id"])
        if not data:
            continue
        for h in data.get("hits", []):
            scr = h.get("screening") or "pending"
            text = " ".join([
                h.get("title") or "", h.get("applicant") or "",
                (h.get("abstract") or "")[:500],
            ])
            tokens = _re.findall(r'[ァ-ヴー]{3,}|[一-龥]{2,}(?:剤|物|体|油|酸|液|層|膜|比|料)?', text)
            if scr == "reject":
                for t in tokens:
                    reject_tokens[t] += 1
            elif scr in ("star", "triangle"):
                for t in tokens:
                    positive_tokens[t] += 1

    # 却下のみに出る語を抽出
    candidates = []
    for tok, cnt in reject_tokens.most_common(50):
        if cnt < min_occurrences:
            continue
        if positive_tokens.get(tok, 0) > 0:
            continue
        candidates.append({"term": tok, "reject_count": cnt})

    return {"not_term_candidates": candidates[:20]}


def auto_run(case_ids, steps=None):
    """オートモード: 複数案件を順次自動処理（SSEジェネレータ）

    Flask に依存しない純粋なジェネレータ。SSE イベント文字列を yield する。
    Flask 側で Response(auto_run(...), mimetype="text/event-stream") として使う。

    Args:
        case_ids: 処理対象の案件IDリスト
        steps: 実行ステップのリスト（デフォルト: 全ステップ）
            追加サポート: "formula_search" を含めると、Stage 3 の検索式を
            Google Patents で実行して search_runs に保存する。
    """
    if steps is None:
        steps = ["keywords", "presearch", "download_citations", "compare", "excel"]

    for i, case_id in enumerate(case_ids):
        try:
            yield _sse_event("case_start", {
                "case_id": case_id,
                "index": i,
                "total": len(case_ids),
            })

            meta = load_case_meta(case_id)
            if not meta:
                yield _sse_event("case_error", {
                    "case_id": case_id,
                    "error": "案件が見つかりません",
                })
                continue

            case_dir = get_case_dir(case_id)

            hongan_path = case_dir / "hongan.json"
            segments_path = case_dir / "segments.json"
            if not hongan_path.exists() or not segments_path.exists():
                yield _sse_event("case_error", {
                    "case_id": case_id,
                    "error": "本願テキストまたは分節データがありません",
                })
                continue

            with open(hongan_path, "r", encoding="utf-8") as f:
                hongan = json.load(f)
            with open(segments_path, "r", encoding="utf-8") as f:
                segs = json.load(f)

            field = meta.get("field", "cosmetics")

            # --- Step: キーワード提案 + 予備検索（並列実行） ---
            # keywords と presearch は独立実行可能:
            # - presearch は keywords.json がなくても tech_analysis から検索式生成可能
            # - keywords が先に完了すれば presearch のClaude呼び出しで活用される
            run_kw = "keywords" in steps
            run_ps = "presearch" in steps

            if run_kw or run_ps:
                import queue

                sse_queue = queue.Queue()

                def _run_keywords():
                    sse_queue.put(_sse_event("step_start", {
                        "case_id": case_id, "step": "keywords",
                    }))
                    try:
                        kw_groups = _auto_suggest_keywords(
                            case_dir, segs, hongan, field
                        )
                        sse_queue.put(_sse_event("step_done", {
                            "case_id": case_id, "step": "keywords",
                            "detail": f"{len(kw_groups)}グループ生成",
                        }))
                        return kw_groups
                    except Exception as e:
                        sse_queue.put(_sse_event("step_error", {
                            "case_id": case_id, "step": "keywords",
                            "error": str(e),
                        }))
                        return None

                def _run_presearch():
                    sse_queue.put(_sse_event("step_start", {
                        "case_id": case_id, "step": "presearch",
                    }))
                    try:
                        cands = _auto_presearch(
                            case_dir, segs, hongan, field, meta
                        )
                        sse_queue.put(_sse_event("step_done", {
                            "case_id": case_id, "step": "presearch",
                            "detail": f"{len(cands)}件の候補",
                        }))
                        return cands
                    except Exception as e:
                        sse_queue.put(_sse_event("step_error", {
                            "case_id": case_id, "step": "presearch",
                            "error": str(e),
                        }))
                        return []

                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = {}
                    if run_kw:
                        futures[pool.submit(_run_keywords)] = "keywords"
                    if run_ps:
                        futures[pool.submit(_run_presearch)] = "presearch"

                    candidates = []
                    for future in as_completed(futures, timeout=900):
                        # キューに溜まったSSEイベントを送出
                        while not sse_queue.empty():
                            yield sse_queue.get_nowait()
                        if futures[future] == "presearch":
                            candidates = future.result() or []

                    # 残りのSSEイベントを送出
                    while not sse_queue.empty():
                        yield sse_queue.get_nowait()

            # --- Step: Stage 3 検索式で自動検索 (J-PlatPat/Google Patents) ---
            if "formula_search" in steps:
                yield _sse_event("step_start", {
                    "case_id": case_id, "step": "formula_search",
                })
                try:
                    fs_result = _auto_formula_search(
                        case_dir, case_id, meta,
                        source="google_patents",
                        levels=("narrow", "medium"),
                        max_results=30,
                        auto_dl_top_n=5,
                        score_threshold=70,
                    )
                    yield _sse_event("step_done", {
                        "case_id": case_id, "step": "formula_search",
                        "detail": f"{fs_result['runs']}ラン / 計{fs_result['total_hits']}件 / 自動DL{fs_result['downloaded']}件",
                    })
                except Exception as e:
                    yield _sse_event("step_error", {
                        "case_id": case_id, "step": "formula_search",
                        "error": str(e),
                    })

            # --- Step: 引用文献DL ---
            if "download_citations" in steps:
                yield _sse_event("step_start", {
                    "case_id": case_id, "step": "download_citations",
                })
                try:
                    dl_result = _auto_download_citations(
                        case_id, case_dir, meta
                    )
                    yield _sse_event("step_done", {
                        "case_id": case_id, "step": "download_citations",
                        "detail": f"{dl_result['downloaded']}/{dl_result['total']}件DL",
                    })
                except Exception as e:
                    yield _sse_event("step_error", {
                        "case_id": case_id, "step": "download_citations",
                        "error": str(e),
                    })

            # --- Step: 対比実行 ---
            if "compare" in steps:
                yield _sse_event("step_start", {
                    "case_id": case_id, "step": "compare",
                })
                try:
                    cmp_result = _auto_compare(
                        case_id, case_dir, segs, meta, field
                    )
                    yield _sse_event("step_done", {
                        "case_id": case_id, "step": "compare",
                        "detail": f"{cmp_result['num_docs']}件対比完了",
                    })
                except Exception as e:
                    yield _sse_event("step_error", {
                        "case_id": case_id, "step": "compare",
                        "error": str(e),
                    })

            # --- Step: 請求項1充足チェック → 追加サーチ ---
            if "compare" in steps:
                try:
                    cov = _check_claim1_coverage(case_dir, segs)
                    if not cov["covered"] and cov["uncovered_ids"]:
                        uncov_str = ", ".join(cov["uncovered_ids"])
                        yield _sse_event("step_warning", {
                            "case_id": case_id, "step": "coverage_check",
                            "detail": f"請求項1の分節 {uncov_str} がX文献で未充足（X:{cov['x_count']}件, Y:{cov['y_count']}件）",
                        })

                        # 追加サーチ実行
                        yield _sse_event("step_start", {
                            "case_id": case_id, "step": "additional_search",
                        })
                        try:
                            added = _auto_additional_search(
                                case_dir, segs, hongan, field, meta,
                                cov["uncovered_ids"], cov,
                            )
                            yield _sse_event("step_done", {
                                "case_id": case_id, "step": "additional_search",
                                "detail": f"{len(added)}件の追加候補（実在確認済）",
                            })

                            # 追加DL
                            if added:
                                yield _sse_event("step_start", {
                                    "case_id": case_id, "step": "additional_dl",
                                })
                                try:
                                    dl2 = _auto_download_citations(case_id, case_dir, meta)
                                    yield _sse_event("step_done", {
                                        "case_id": case_id, "step": "additional_dl",
                                        "detail": f"{dl2['downloaded']}/{dl2['total']}件DL",
                                    })
                                except Exception as e:
                                    yield _sse_event("step_error", {
                                        "case_id": case_id, "step": "additional_dl",
                                        "error": str(e),
                                    })

                                # 追加対比（新しい文献のみ、既存responseはスキップ済み）
                                yield _sse_event("step_start", {
                                    "case_id": case_id, "step": "additional_compare",
                                })
                                try:
                                    cmp2 = _auto_compare(case_id, case_dir, segs, meta, field)
                                    yield _sse_event("step_done", {
                                        "case_id": case_id, "step": "additional_compare",
                                        "detail": f"{cmp2['num_docs']}件追加対比完了",
                                    })
                                except Exception as e:
                                    yield _sse_event("step_error", {
                                        "case_id": case_id, "step": "additional_compare",
                                        "error": str(e),
                                    })
                        except Exception as e:
                            yield _sse_event("step_error", {
                                "case_id": case_id, "step": "additional_search",
                                "error": str(e),
                            })
                except Exception as e:
                    logger.warning("充足チェックエラー: %s — %s", case_id, e)

            # --- Step: Excel出力 ---
            if "excel" in steps:
                yield _sse_event("step_start", {
                    "case_id": case_id, "step": "excel",
                })
                try:
                    # meta再読込（追加サーチで更新されている可能性）
                    meta = load_case_meta(case_id) or meta
                    excel_path = _auto_export_excel(case_dir, segs, meta)
                    yield _sse_event("step_done", {
                        "case_id": case_id, "step": "excel",
                        "detail": excel_path.name,
                    })
                except Exception as e:
                    yield _sse_event("step_error", {
                        "case_id": case_id, "step": "excel",
                        "error": str(e),
                    })

            yield _sse_event("case_done", {
                "case_id": case_id, "index": i,
            })

        except Exception as e:
            yield _sse_event("case_error", {
                "case_id": case_id,
                "error": f"予期しないエラー: {str(e)}",
            })

    yield _sse_event("all_done", {"total": len(case_ids)})
