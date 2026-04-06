#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""オートモード（バッチ自動処理）サービス"""

import json
import logging
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from services.case_service import (
    get_case_dir, load_case_meta, save_case_meta,
    load_search_data, save_search_data,
)

logger = logging.getLogger(__name__)


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
    from modules.claude_client import call_claude

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

    # MCP検索サーバーも有効化
    raw_response = call_claude(prompt, timeout=600, use_search=True)

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

    logger.info("候補検証開始: %d件を並列検証", len(to_verify))
    with ThreadPoolExecutor(max_workers=min(len(to_verify), 6)) as executor:
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
    from modules.patent_downloader import download_patent_pdf
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

    # 既にダウンロード済みのものを除外
    to_download = []
    already_done = 0
    for c in targets:
        patent_id = c.get("patent_id", c.get("doc_number", ""))
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
        dl_result = download_patent_pdf(patent_id, case_dir / "input", timeout=60)
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
    logger.info("引例DL開始: %d件を並列ダウンロード（%d件はキャッシュ済み）",
                len(to_download), already_done)

    with ThreadPoolExecutor(max_workers=min(len(to_download), 6)) as executor:
        futures = {
            executor.submit(_download_one, c, pid, did): pid
            for c, pid, did in to_download
        }
        for future in as_completed(futures, timeout=300):
            pid = futures[future]
            try:
                result, error = future.result()
                if error:
                    errors.append(error)
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
                logger.warning("引例DLエラー: %s — %s", pid, e)

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
    from modules.claude_client import call_claude

    citations_map = {}  # doc_id -> citation_data
    for cit in meta.get("citations", []):
        cit_path = case_dir / "citations" / f"{cit['id']}.json"
        if cit_path.exists():
            with open(cit_path, "r", encoding="utf-8") as f:
                citations_map[cit["id"]] = json.load(f)

    if not citations_map:
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

        raw_response = call_claude(prompt_text, timeout=600)

        with open(responses_dir / f"_raw_{doc_id}.txt", "w", encoding="utf-8") as f:
            f.write(raw_response)

        result, errors = parse_response(raw_response, all_segment_ids)
        return doc_id, result, errors

    saved_docs = []
    all_errors = []
    logger.info("対比実行開始: %d件を並列処理", len(citations_map))

    with ThreadPoolExecutor(max_workers=min(len(citations_map), 6)) as executor:
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
    return {"num_docs": len(saved_docs), "errors": all_errors}


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
# メインエントリポイント（SSEジェネレータ）
# ------------------------------------------------------------------

def auto_run(case_ids, steps=None):
    """オートモード: 複数案件を順次自動処理（SSEジェネレータ）

    Flask に依存しない純粋なジェネレータ。SSE イベント文字列を yield する。
    Flask 側で Response(auto_run(...), mimetype="text/event-stream") として使う。

    Args:
        case_ids: 処理対象の案件IDリスト
        steps: 実行ステップのリスト（デフォルト: 全ステップ）
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

            # --- Step: Excel出力 ---
            if "excel" in steps:
                yield _sse_event("step_start", {
                    "case_id": case_id, "step": "excel",
                })
                try:
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
