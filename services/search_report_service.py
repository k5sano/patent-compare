#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ISR/書面意見の取り込み・要約・引用文献取得サービス"""

import json
import shutil
from datetime import datetime
from pathlib import Path

from services.case_service import (
    get_case_dir, load_case_meta, upload_citation, get_case_lock,
)


REPORTS_FILE = "search_reports.json"


def _reports_dir(case_id):
    d = get_case_dir(case_id) / "search_reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _reports_json_path(case_id):
    return _reports_dir(case_id) / REPORTS_FILE


def load_reports(case_id):
    """search_reports.json を読み込む"""
    p = _reports_json_path(case_id)
    if not p.exists():
        return {"reports": []}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_reports(case_id, data):
    p = _reports_json_path(case_id)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_reports(case_id):
    """UI用の軽量レスポンス（raw_textを除く）"""
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404
    data = load_reports(case_id)
    out = []
    for r in data.get("reports", []):
        rr = {k: v for k, v in r.items() if k != "raw_text"}
        out.append(rr)
    return {"reports": out}, 200


def upload_report(case_id, src_path, original_filename):
    """ISR/書面意見PDFをアップロード→保存→パース"""
    from modules.search_report_parser import parse_search_report

    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404

    # 保存先
    dest_dir = _reports_dir(case_id)
    dest = dest_dir / original_filename
    if Path(src_path) != dest:
        shutil.copy2(str(src_path), str(dest))

    # パース
    try:
        parsed = parse_search_report(str(dest))
    except Exception as e:
        return {"error": f"パース失敗: {e}"}, 400

    if parsed.get("form") is None:
        return {
            "error": "ISR/書面意見/IPER として認識できませんでした",
            "filename": original_filename,
        }, 400

    parsed["uploaded_at"] = datetime.now().isoformat(timespec="seconds")
    parsed["box_v_summary"] = ""
    parsed.pop("raw_text", None)  # 永続化からは外す（容量節約）

    # 同一案件への並列アップロードで search_reports.json の lost-update を防ぐ
    with get_case_lock(case_id):
        data = load_reports(case_id)
        data.setdefault("reports", [])
        # 同名は上書き
        data["reports"] = [r for r in data["reports"] if r.get("filename") != original_filename]
        data["reports"].append(parsed)
        save_reports(case_id, data)

    return {"success": True, "report": parsed}, 200


def delete_report(case_id, filename):
    """ISR/書面意見を削除"""
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404

    dest_dir = _reports_dir(case_id)
    pdf_path = dest_dir / filename
    if pdf_path.exists():
        pdf_path.unlink()

    data = load_reports(case_id)
    data["reports"] = [r for r in data.get("reports", []) if r.get("filename") != filename]
    save_reports(case_id, data)
    return {"success": True}, 200


def summarize_box_v(case_id, filename):
    """Box V本文をClaudeで要約。結果を search_reports.json に保存"""
    from modules.claude_client import call_claude, ClaudeClientError

    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404

    data = load_reports(case_id)
    target = next((r for r in data.get("reports", []) if r.get("filename") == filename), None)
    if not target:
        return {"error": "対象の報告書が見つかりません"}, 404

    box_v = target.get("box_v") or ""
    if not box_v.strip():
        return {"error": "Box V 本文が抽出されていません"}, 400

    citations = target.get("citations", [])
    cit_lines = []
    for c in citations:
        cit_lines.append(
            f"- D{c.get('num')}: {c.get('doc_label','')} "
            f"[{c.get('category','')}] claims: {c.get('claims','')}"
        )
    cit_block = "\n".join(cit_lines) if cit_lines else "(引用文献の抽出なし)"

    form_label = "国際調査機関の書面意見 (PCT/ISA/237)"
    if target.get("form") == "IPER":
        form_label = "国際予備審査報告 (PCT/IPEA/409)"

    prompt = f"""以下は PCT国際出願の {form_label} における「Box V（新規性・進歩性・産業上の利用可能性についての理由付き陳述）」の本文です。
本文の言語は混在する可能性がありますが、必ず**日本語で**要約してください。

## 引用文献リスト
{cit_block}

## Box V 本文
{box_v}

## 出力フォーマット（マークダウン）

### 1. 全体結論
- 新規性: Yes/No（簡潔な理由）
- 進歩性: Yes/No
- 産業上の利用可能性: Yes/No

### 2. クレーム別の判断
クレーム番号ごとに、どの引用文献を根拠にどう判断されているかを箇条書き。

### 3. 引用文献ごとの言及まとめ
各文献（D1, D2, ...）について、Box V内でどう使われているか（主引例/副引例/参考、関連クレーム、引用箇所の段落番号など）を1〜3行で。

要約は事実に忠実に。本文に書かれていない推測は含めないこと。
"""

    try:
        response = call_claude(prompt, timeout=300)
    except ClaudeClientError as e:
        return {"error": f"Claude呼び出し失敗: {e}"}, 500

    target["box_v_summary"] = response
    save_reports(case_id, data)
    return {"success": True, "summary": response}, 200


def _category_to_role(cat):
    """X→主引例 / Y→副引例 / その他→参考"""
    c = (cat or "").upper()
    if c == "X":
        return "主引例"
    if c == "Y":
        return "副引例"
    return "参考"


GOOGLE_PATENTS_DL_INTERVAL = 2.0  # 秒。Google Patents への連続DL間の最小間隔（ロボット判定回避）


def fetch_cited_documents(case_id, filename, citation_nums):
    """指定された引用文献のPDFをGoogle Patentsから取得し、既存citationsに統合。

    Google Patents への連続アクセスは GOOGLE_PATENTS_DL_INTERVAL 秒の間隔を空ける
    （ロボット判定回避）。並列化しないこと。

    Parameters:
        citation_nums: 取得対象の num のリスト（Box C内の通し番号）
    """
    import time
    from modules.patent_downloader import download_patent_pdf

    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404

    data = load_reports(case_id)
    target = next((r for r in data.get("reports", []) if r.get("filename") == filename), None)
    if not target:
        return {"error": "対象の報告書が見つかりません"}, 404

    case_dir = get_case_dir(case_id)
    input_dir = case_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    nums_set = set(citation_nums or [])
    citations = target.get("citations", [])
    results = []
    dl_count = 0  # 実際にDLを試みた件数（レート制御の基準）

    for cit in citations:
        if nums_set and cit.get("num") not in nums_set:
            continue
        doc_id = cit.get("doc_id") or ""
        label = cit.get("doc_label") or doc_id
        if not doc_id:
            results.append({
                "num": cit.get("num"), "doc_id": "", "label": label,
                "success": False, "error": "文献番号を抽出できなかったため取得不可",
            })
            cit["fetch_status"] = "no_id"
            continue

        # 2回目以降のDL前にスリープ（Google Patents 連続アクセス対策）
        if dl_count > 0:
            time.sleep(GOOGLE_PATENTS_DL_INTERVAL)
        dl_count += 1

        dl = download_patent_pdf(doc_id, input_dir, timeout=60)
        if not dl.get("success"):
            results.append({
                "num": cit.get("num"), "doc_id": doc_id, "label": label,
                "success": False,
                "error": dl.get("error", "PDF取得失敗"),
                "google_patents_url": dl.get("google_patents_url", ""),
            })
            cit["fetch_status"] = "failed"
            cit["google_patents_url"] = dl.get("google_patents_url", "")
            continue

        role = _category_to_role(cit.get("category", ""))
        try:
            up_result, code = upload_citation(
                case_id, dl["path"], role=role, label=label,
            )
        except Exception as e:
            results.append({
                "num": cit.get("num"), "doc_id": doc_id, "label": label,
                "success": False, "error": f"取込失敗: {e}",
            })
            cit["fetch_status"] = "extract_failed"
            continue

        if code != 200:
            results.append({
                "num": cit.get("num"), "doc_id": doc_id, "label": label,
                "success": False, "error": up_result.get("error", "取込失敗"),
            })
            cit["fetch_status"] = "extract_failed"
            continue

        cit["fetch_status"] = "ok"
        cit["fetched_doc_id"] = up_result.get("doc_id", doc_id)
        results.append({
            "num": cit.get("num"),
            "doc_id": up_result.get("doc_id", doc_id),
            "label": label,
            "role": role,
            "success": True,
            "num_claims": up_result.get("num_claims", 0),
            "num_paragraphs": up_result.get("num_paragraphs", 0),
        })

    save_reports(case_id, data)
    return {"success": True, "results": results}, 200
