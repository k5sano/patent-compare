#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""J-PlatPat OPD の半自動ドシエ収集サービス。"""
from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
import hashlib
import shutil
from urllib.parse import urljoin, urlparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from services.case_service import get_case_dir, load_case_meta, load_json_file

logger = logging.getLogger(__name__)

JPLATPAT_ORIGIN = "https://www.j-platpat.inpit.go.jp"
_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

_DISMISS_SELECTORS = [
    'button:has-text("閉じる")',
    'button:has-text("OK")',
    'button:has-text("同意")',
    'button:has-text("はい")',
    'button[aria-label*="close" i]',
    'button[aria-label*="閉じる"]',
]

_OPD_SELECTORS = [
    'button:has-text("OPD")',
    'a:has-text("OPD")',
    'button:has-text("ワン")',
    'a:has-text("ワン")',
    'button:has-text("One Portal Dossier")',
    'a:has-text("One Portal Dossier")',
]

_EXPAND_ALL_SELECTORS = [
    'text="書類情報をすべて開く"',
    'text="書類情報を全て開く"',
    'button:has-text("書類情報をすべて開く")',
    'button:has-text("書類情報を全て開く")',
    'a:has-text("書類情報をすべて開く")',
    'a:has-text("書類情報を全て開く")',
    '[role="button"]:has-text("書類情報をすべて開く")',
    '[role="button"]:has-text("書類情報を全て開く")',
    'button:has-text("すべて開く")',
    'button:has-text("全て開く")',
    'a:has-text("すべて開く")',
    'a:has-text("全て開く")',
    '[role="button"]:has-text("すべて開く")',
    '[role="button"]:has-text("全て開く")',
    'button:has-text("Open all")',
    'a:has-text("Open all")',
    '[role="button"]:has-text("Open all")',
]
_EXPAND_ALL_LABELS = [
    "書類情報を全て開く",
    "書類情報をすべて開く",
    "全て開く",
    "すべて開く",
    "Open all",
]
_SHOW_ALL_CITATION_SELECTORS = [
    'text="全ての分類･引用情報を表示"',
    'text="全ての分類・引用情報を表示"',
    'text="すべての分類･引用情報を表示"',
    'text="すべての分類・引用情報を表示"',
    'button:has-text("分類")',
    'button:has-text("引用情報")',
    'a:has-text("分類")',
    'a:has-text("引用情報")',
    '[role="button"]:has-text("分類")',
    '[role="button"]:has-text("引用情報")',
]
_SHOW_ALL_CITATION_LABELS = [
    "全ての分類･引用情報を表示",
    "全ての分類・引用情報を表示",
    "すべての分類･引用情報を表示",
    "すべての分類・引用情報を表示",
    "Show all classification/citation information",
]

_SCRAPE_SELECTORS = [
    "table tbody tr",
    "mat-row, .mat-row",
    "li",
    "a",
    "button",
    '[role="button"]',
]

_TARGET_PATTERNS = [
    ("ISR", 100, re.compile(r"国際調査報告|International\s+Search\s+Report|\bISR\b", re.I)),
    ("IPER", 95, re.compile(r"国際予備審査報告|International\s+Preliminary\s+(?:Examination\s+)?Report|International\s+Preliminary\s+Report\s+on\s+Patentability|\bIPER\b", re.I)),
    ("CN拒絶理由", 85, re.compile(r"\bCN\b|中国|China", re.I), re.compile(r"拒絶理由|Office\s+Action|Notification\s+of\s+Office\s+Action|审查意见|審查意見", re.I)),
    ("US Non Final Rejection", 90, re.compile(r"Non[-\s]?Final\s+Rejection|Non[-\s]?Final\s+Office\s+Action", re.I)),
    ("US Final Rejection", 90, re.compile(r"Final\s+Rejection|Final\s+Office\s+Action", re.I)),
]

_NON_TARGET_PAT = re.compile(
    r"Notification\s+of\s+transmittal|Notification\s+of\s+Transmittal|送付通知|発送通知",
    re.I,
)

_CONTAINER_PAT = re.compile(r"書類情報|PDFダウンロード|PDFを最大|提出日.*書類名")

_ISR_BODY_PAT = re.compile(
    r"^(\d{4}[-/]\d{2}[-/]\d{2}\s+)?(?:Copy\s+of\s+the\s+)?(?:国際調査報告|International\s+Search\s+Report)\b",
    re.I,
)

_IPER_BODY_PAT = re.compile(
    r"^(\d{4}[-/]\d{2}[-/]\d{2}\s+)?(?:Copy\s+of\s+the\s+)?(?:特許性に関する国際予備報告|International\s+Preliminary\s+(?:Report\s+on\s+Patentability|Examination\s+Report))",
    re.I,
)

_DATE_PAT = re.compile(r"(\d{4}[-/]\d{2}[-/]\d{2})")
_ATTACHED_PAT = re.compile(r"添付書類|Attached\s+Document", re.I)
_REJECTION_KINDS = {"IPER", "US Non Final Rejection", "US Final Rejection", "CN拒絶理由"}
_OPD_DOWNLOAD_CLICK_SELECTORS = [
    'a:has-text("添付書類")',
    'button:has-text("添付書類")',
    'a:has-text("原文")',
    'button:has-text("原文")',
    'a:has-text("PDF")',
    'button:has-text("PDF")',
    '[role="button"]:has-text("原文")',
    '[role="button"]:has-text("PDF")',
]
_OPD_ATTACHMENT_DOWNLOAD_SELECTORS = [
    'a:has-text("原文")',
    'button:has-text("原文")',
    '[role="button"]:has-text("原文")',
]
_OPD_ATTACHMENT_EXCLUDE_PAT = re.compile(
    r"国内書面|National\s+Entry|明細書|Description|請求の範囲|Claims|要約|Abstract|図面|Drawings|出願書類|分類情報|一括|最大10|PDFダウンロード",
    re.I,
)
_PATENT_CITATION_RE = re.compile(
    r"\b(WO|JP|US|EP|CN|KR|DE|GB|FR|CA|AU|TW)"
    r"[\s\-/]*"
    r"(\d[\d\s\-/]{2,25})"
    r"\s*([ABC](?:\d|[LI|!])?|T\d?)?",
    re.I,
)


def _hongan_patent_number(case_id: str) -> str:
    hongan = load_json_file(case_id, "hongan.json") or {}
    meta = load_case_meta(case_id) or {}
    return (hongan.get("patent_number") or meta.get("patent_number") or case_id or "").strip()


def _build_jplatpat_url(patent_number: str) -> str:
    from modules.jplatpat_client import build_jplatpat_fixed_url

    url = build_jplatpat_fixed_url(patent_number)
    if url:
        return url
    m = re.search(r"^(\d{4})\s*[-]\s*(\d{3,6})$", patent_number or "")
    if m:
        return f"https://www.j-platpat.inpit.go.jp/c1801/PU/JP-{m.group(1)}-{m.group(2).zfill(6)}/11/ja"
    return ""


def _classify_opd_document(text: str) -> Optional[dict]:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return None
    if len(compact) > 450 or _CONTAINER_PAT.search(compact) or _NON_TARGET_PAT.search(compact):
        return None
    for item in _TARGET_PATTERNS:
        if len(item) == 3:
            kind, priority, pattern = item
            if not pattern.search(compact):
                continue
            if kind == "ISR" and not _ISR_BODY_PAT.search(compact):
                continue
            if kind == "IPER" and not _IPER_BODY_PAT.search(compact):
                continue
        else:
            kind, priority, country_pat, doc_pat = item
            if not country_pat.search(compact) or not doc_pat.search(compact):
                continue
        note = ""
        if kind in ("ISR", "IPER"):
            note = "英訳リンクは表紙のみの場合があります。展開後の添付書類PDFを優先してください。"
        return {
            "kind": kind,
            "priority": priority,
            "note": note,
        }
    return None


def _normalize_doc_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _extract_doc_date(text: str) -> str:
    m = _DATE_PAT.search(text or "")
    return m.group(1).replace("/", "-") if m else ""


def _canonical_patent_id(raw: str) -> str:
    s = re.sub(r"([AB])[LI|!](?=\b|\d)", r"\g<1>1", (raw or "").upper())
    return re.sub(r"[^A-Z0-9]", "", s)


def _normalize_patent_citation(cc: str, num_raw: str, kind_raw: str = "") -> str:
    cc = (cc or "").upper()
    digits = re.sub(r"[^\d]", "", num_raw or "")
    if not digits:
        return ""
    kind = (kind_raw or "").upper()
    kind = re.sub(r"([AB])[LI|!]", r"\g<1>1", kind)
    return f"{cc}{digits}{kind}"


def _iter_patent_citations(text: str):
    normalized = re.sub(r"\s+", " ", text or "")
    for m in _PATENT_CITATION_RE.finditer(normalized):
        pid = _normalize_patent_citation(*m.groups())
        if not pid:
            continue
        start, end = m.span()
        snippet = normalized[max(0, start - 80):min(len(normalized), end + 120)].strip()
        yield pid, snippet


def _link_attached_documents(documents: list[dict]) -> list[dict]:
    """ISR/IPER本体行の直後に出る「添付書類」行を関連づける。"""
    targets: list[dict] = []
    last_target: Optional[dict] = None
    for doc in documents:
        text = doc.get("text") or doc.get("label") or ""
        if doc.get("target") and doc.get("kind") in ("ISR", "IPER"):
            doc.setdefault("date", _extract_doc_date(text))
            doc.setdefault("attachment_labels", [])
            doc.setdefault("preferred_source", "attachment_if_available")
            last_target = doc
            targets.append(doc)
            continue
        if last_target and _ATTACHED_PAT.search(text):
            doc["target"] = False
            doc["attachment_for"] = last_target.get("kind")
            doc["attachment_for_date"] = last_target.get("date", "")
            label = _normalize_doc_text(text)[:180]
            last_target.setdefault("attachment_labels", [])
            if label not in last_target["attachment_labels"]:
                last_target["attachment_labels"].append(label)
    return targets


def _has_downloadable_attachment(target: dict) -> bool:
    for label in target.get("attachment_labels") or []:
        if _ATTACHED_PAT.search(label or "") and not _OPD_ATTACHMENT_EXCLUDE_PAT.search(label or ""):
            return True
    return False


def _downloadable_rejection_targets(targets: list[dict]) -> tuple[list[dict], list[dict]]:
    downloadable = []
    skipped = []
    for target in targets:
        if target.get("kind") not in _REJECTION_KINDS:
            continue
        if _has_downloadable_attachment(target):
            downloadable.append(target)
        else:
            skipped.append({
                "kind": target.get("kind") or "",
                "label": target.get("label") or target.get("text") or "",
                "date": target.get("date") or _extract_doc_date(target.get("label") or target.get("text") or ""),
                "success": False,
                "skipped": True,
                "error": "OPD上で安全に取得できる添付書類行を検出できませんでした。原文/英訳リンクは表紙や別書類の場合があるため、手動取込を使ってください。",
            })
    return downloadable, skipped


def save_opd_index(case_id: str, payload: dict) -> Path:
    case_dir = get_case_dir(case_id)
    dossier_dir = case_dir / "dossier"
    dossier_dir.mkdir(parents=True, exist_ok=True)
    path = dossier_dir / "opd_index.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def load_opd_index(case_id: str) -> tuple[dict, int]:
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404
    path = get_case_dir(case_id) / "dossier" / "opd_index.json"
    if not path.exists():
        data = {
            "case_id": case_id,
            "patent_number": _hongan_patent_number(case_id),
            "documents": [],
            "targets": [],
            "exists": False,
            "ocr_reports": _load_or_build_ocr_reports(case_id),
        }
        data["citation_candidates"] = _extract_citation_candidates_from_index(data)
        data["rejection_documents"] = _build_rejection_documents(case_id, data)
        return data, 200
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        _refresh_targets_from_documents(data, case_id=case_id)
        data["opd_pdf_reports"] = _load_opd_pdf_reports(case_id)
        data["rejection_documents"] = _build_rejection_documents(case_id, data)
        data["exists"] = True
        return data, 200
    except (OSError, json.JSONDecodeError) as e:
        return {"error": f"OPDインデックス読込失敗: {e}"}, 500


def _refresh_targets_from_documents(data: dict, case_id: str | None = None) -> dict:
    if not data.get("documents"):
        if case_id:
            data["ocr_reports"] = _load_or_build_ocr_reports(case_id)
        data["citation_candidates"] = _extract_citation_candidates_from_index(data)
        return data
    docs = []
    for raw in data.get("documents") or []:
        text = raw.get("text") or raw.get("label") or ""
        classified = _classify_opd_document(text)
        if classified:
            doc = dict(raw)
            doc.update(classified)
            doc["target"] = True
            docs.append(doc)
        elif _ATTACHED_PAT.search(text):
            doc = dict(raw)
            doc["kind"] = "添付書類"
            doc["target"] = False
            doc.setdefault("priority", 0)
            docs.append(doc)
    targets = _link_attached_documents(docs)
    targets.sort(key=lambda d: (-int(d.get("priority") or 0), d.get("kind", ""), d.get("date", ""), d.get("label", "")))
    data["documents"] = docs
    data["targets"] = targets
    if case_id:
        data["ocr_reports"] = _load_or_build_ocr_reports(case_id)
        data["opd_pdf_reports"] = _load_opd_pdf_reports(case_id)
    data["citation_candidates"] = _extract_citation_candidates_from_index(data)
    return data


def _rejection_summary_path(case_id: str) -> Path:
    return get_case_dir(case_id) / "dossier" / "opd_rejection_summaries.json"


def _opd_download_signals_path(case_id: str) -> Path:
    return get_case_dir(case_id) / "dossier" / "opd_download_signals.json"


def _opd_pdf_dir(case_id: str) -> Path:
    d = get_case_dir(case_id) / "dossier" / "opd_pdfs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _opd_pdf_reports_path(case_id: str) -> Path:
    return get_case_dir(case_id) / "dossier" / "opd_pdf_reports.json"


def _load_opd_pdf_reports(case_id: str) -> list[dict]:
    path = _opd_pdf_reports_path(case_id)
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data.get("reports") if isinstance(data.get("reports"), list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_opd_pdf_reports(case_id: str, reports: list[dict]) -> None:
    path = _opd_pdf_reports_path(case_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump({
            "case_id": case_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "reports": reports,
        }, f, ensure_ascii=False, indent=2)


def _parse_opd_pdf_report(case_id: str, pdf_path: Path, meta: dict) -> dict:
    from modules.search_report_parser import parse_search_report

    parsed = parse_search_report(str(pdf_path))
    raw_text = parsed.get("raw_text") or ""
    return {
        "kind": parsed.get("form") or meta.get("kind") or "OPD PDF",
        "label": meta.get("label") or pdf_path.name,
        "source": "opd_attached_pdf",
        "filename": pdf_path.name,
        "path": str(pdf_path),
        "date": meta.get("date", ""),
        "citations": parsed.get("citations") or [],
        "box_v": parsed.get("box_v") or "",
        "raw_text": raw_text[:20000],
        "raw_text_length": len(raw_text),
        "language": parsed.get("language", ""),
        "intl_app_no": parsed.get("intl_app_no", ""),
    }


def _load_rejection_summary_cache(case_id: str) -> dict:
    path = _rejection_summary_path(case_id)
    if not path.exists():
        return {"items": {}}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data.get("items"), dict) else {"items": {}}
    except (OSError, json.JSONDecodeError):
        return {"items": {}}


def _save_rejection_summary_cache(case_id: str, cache: dict) -> None:
    path = _rejection_summary_path(case_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _rejection_doc_id(kind: str, label: str, source: str) -> str:
    src = f"{kind}|{source}|{label}"
    return hashlib.sha1(src.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _rejection_cover_key(label: str, date: str = "") -> str:
    norm = re.sub(r"\s+", " ", label or "").strip().lower()
    norm = re.sub(r"\s+", "", norm)
    return f"{date or ''}|{norm}"


def _rejection_cover_keys(kind: str, label: str, date: str = "") -> set[str]:
    keys = {_rejection_cover_key(label, date)}
    kind_norm = re.sub(r"\s+", "", kind or "").lower()
    if date and kind_norm:
        keys.add(f"{date}|kind:{kind_norm}")
    return keys


def _build_rejection_documents(case_id: str, data: dict) -> list[dict]:
    cache = _load_rejection_summary_cache(case_id)
    cached_items = cache.get("items") or {}
    items: list[dict] = []
    seen: set[str] = set()
    covered_opd_document_keys: set[str] = set()

    for report in data.get("opd_pdf_reports") or []:
        text = report.get("box_v") or report.get("raw_text") or ""
        if not text.strip():
            continue
        kind = report.get("kind") or ""
        label = report.get("label") or report.get("filename") or kind
        if kind in ("IPER", "WOSA") or kind in _REJECTION_KINDS or re.search(r"IPER|Written Opinion|書面意見|予備報告|Rejection|Office Action|拒絶理由", label, re.I):
            cover_kind = "IPER" if kind == "WOSA" and re.search(r"International Preliminary Report|予備報告|IPER", label, re.I) else kind
            covered_opd_document_keys.update(_rejection_cover_keys(cover_kind, label, report.get("date", "")))

    def add_item(kind: str, label: str, *, source: str, text: str = "", date: str = "", note: str = "") -> None:
        label = re.sub(r"\s+", " ", label or "").strip()
        if not label:
            return
        item_id = _rejection_doc_id(kind, label, source)
        if item_id in seen:
            return
        seen.add(item_id)
        cached = cached_items.get(item_id) or {}
        text = text or ""
        status = "summarized" if cached.get("ja_summary") else ("ready" if text.strip() else "needs_pdf_ocr")
        if status == "needs_pdf_ocr" and not note:
            note = "OPD上の書類候補のみ取得済みです。本願内ISR OCRでは処理されません。OPDの添付PDFを保存/OCRすると翻訳・要約できます。"
        item = {
            "id": item_id,
            "kind": kind,
            "label": label,
            "source": source,
            "date": date,
            "has_text": bool(text.strip()),
            "text_preview": text.strip()[:800],
            "source_text": text.strip()[:20000],
            "status": status,
            "note": note,
            "ja_summary": cached.get("ja_summary", ""),
            "summarized_at": cached.get("summarized_at", ""),
            "model": cached.get("model", ""),
        }
        items.append(item)

    for doc in data.get("documents") or []:
        kind = doc.get("kind") or ""
        if kind not in _REJECTION_KINDS:
            continue
        text = doc.get("text") or doc.get("label") or ""
        date = doc.get("date") or _extract_doc_date(text)
        if _rejection_cover_keys(kind, text, date) & covered_opd_document_keys:
            continue
        add_item(kind, text, source="opd_document", date=date)

    for report in data.get("ocr_reports") or []:
        kind = report.get("kind") or ""
        label = report.get("label") or report.get("filename") or kind
        text = report.get("box_v") or report.get("raw_text") or ""
        if not text.strip():
            continue
        if (
            kind in ("ISR", "IPER", "WOSA")
            or report.get("source") == "hongan_pdf_embedded_isr"
            or re.search(r"ISR|International Search Report|IPER|Written Opinion|書面意見|予備報告|国際調査報告", label, re.I)
        ):
            note = "引用抽出に使用した保存済みOCR本文です。この本文をそのまま翻訳・要約に使います。"
            add_item(kind or "OCR", label, source=report.get("source") or "opd_ocr", text=text, note=note)

    for report in data.get("opd_pdf_reports") or []:
        kind = report.get("kind") or ""
        label = report.get("label") or report.get("filename") or kind
        text = report.get("box_v") or report.get("raw_text") or ""
        if kind in ("IPER", "WOSA") or kind in _REJECTION_KINDS or re.search(r"IPER|Written Opinion|書面意見|予備報告|Rejection|Office Action|拒絶理由", label, re.I):
            add_item(kind or "OPD PDF", label, source="opd_attached_pdf", text=text, date=report.get("date", ""))

    try:
        from services.search_report_service import load_reports
        reports = load_reports(case_id).get("reports") or []
    except Exception:
        reports = []
    for report in reports:
        form = report.get("form") or ""
        if form not in ("IPER", "WOSA"):
            continue
        label = report.get("filename") or form
        text = report.get("box_v") or ""
        add_item(form, label, source="search_report", text=text)
        if report.get("box_v_summary"):
            item_id = _rejection_doc_id(form, label, "search_report")
            cached_items.setdefault(item_id, {})
            cached_items[item_id].setdefault("ja_summary", report.get("box_v_summary"))

    items.sort(key=lambda x: (0 if x["status"] == "summarized" else 1 if x["has_text"] else 2, x.get("kind", ""), x.get("date", ""), x.get("label", "")))
    return items


def get_rejection_documents(case_id: str) -> tuple[dict, int]:
    data, code = load_opd_index(case_id)
    if code != 200:
        return data, code
    items = data.get("rejection_documents") or []
    return {"documents": items, "count": len(items)}, 200


def summarize_rejection_documents(case_id: str, model: str | None = None, force: bool = False) -> tuple[dict, int]:
    from modules.claude_client import call_claude, ClaudeClientError

    data, code = load_opd_index(case_id)
    if code != 200:
        return data, code
    docs = data.get("rejection_documents") or []
    cache = _load_rejection_summary_cache(case_id)
    cache.setdefault("items", {})
    results = []
    for doc in docs:
        base_result = {
            "id": doc.get("id"),
            "kind": doc.get("kind", ""),
            "label": doc.get("label", ""),
            "has_text": bool(doc.get("has_text")),
        }
        if not doc.get("has_text"):
            results.append({**base_result, "status": "needs_pdf_ocr"})
            continue
        if doc.get("ja_summary") and not force:
            results.append({**base_result, "status": "cached"})
            continue
        prompt = _build_rejection_summary_prompt(doc)
        try:
            ja_summary = call_claude(prompt, timeout=300, model=model)
        except ClaudeClientError as e:
            results.append({**base_result, "status": "error", "error": str(e)})
            continue
        cache["items"][doc["id"]] = {
            "ja_summary": ja_summary,
            "summarized_at": datetime.now(timezone.utc).isoformat(),
            "model": model or "",
        }
        results.append({**base_result, "status": "summarized"})
    _save_rejection_summary_cache(case_id, cache)
    refreshed, _ = load_opd_index(case_id)
    return {
        "success": True,
        "results": results,
        "documents": refreshed.get("rejection_documents") or [],
    }, 200


def ingest_opd_pdf_file(case_id: str, src_path: str | Path, *, label: str = "", kind: str = "") -> tuple[dict, int]:
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404
    src = Path(src_path)
    if not src.exists():
        return {"error": f"PDFが見つかりません: {src}"}, 404
    dest_dir = _opd_pdf_dir(case_id)
    safe = re.sub(r'[<>:"/\\|?*]', "_", src.name).strip() or "opd.pdf"
    dest = dest_dir / safe
    if src.resolve() != dest.resolve():
        shutil.copy2(str(src), str(dest))
    meta = {"label": label or safe, "kind": kind or "", "date": ""}
    try:
        report = _parse_opd_pdf_report(case_id, dest, meta)
    except Exception as e:
        return {"error": f"OPD添付PDFのOCR/解析に失敗: {e}", "filename": dest.name}, 400
    reports = [r for r in _load_opd_pdf_reports(case_id) if r.get("filename") != report.get("filename")]
    reports.append(report)
    _save_opd_pdf_reports(case_id, reports)
    data, code = load_opd_index(case_id)
    if code != 200:
        return data, code
    return {
        "success": True,
        "report": {k: v for k, v in report.items() if k != "raw_text"},
        "documents": data.get("rejection_documents") or [],
    }, 200


def _build_rejection_summary_prompt(doc: dict) -> str:
    text = _focus_rejection_summary_text(doc.get("source_text") or doc.get("text_preview") or "")
    return f"""以下は特許ドシエ中の拒絶理由・見解系書類です。

書類種別: {doc.get('kind')}
書類名: {doc.get('label')}

## 原文
{text[:16000]}

## 指示
日本語で、実務者がすぐ判断できるように整理してください。
1. 主要な拒絶・否定的見解の結論
2. 問題になっている請求項
3. 引用文献と使われ方
4. 新規性・進歩性・サポート/明確性などの論点別まとめ
5. 本願対応で確認すべきポイント

原文にない推測は避け、不明な点は不明と書いてください。"""


def _focus_rejection_summary_text(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    patterns = [
        r"Box\s+No\.?\s*V.*?Reasoned\s+statement",
        r"Reasoned\s+statement\s+with\s+regard\s+to\s+novelty",
        r"Re\s+Item\s+V",
        r"Statement\s+Novelty",
        r"1\.\s*Statement\s+Novelty",
    ]
    starts = []
    for pat in patterns:
        m = re.search(pat, text, re.I | re.S)
        if m:
            starts.append(max(0, m.start() - 500))
    if starts:
        focused = text[min(starts):]
        if len(focused) >= 3000:
            return focused[:12000]
    return text[:12000]


def _extract_citation_candidates_from_index(data: dict) -> list[dict]:
    own = data.get("patent_number") or data.get("case_id") or ""
    own_keys = {_canonical_patent_id(own)}
    m = re.search(r"(\d{4})[-\s]?(\d{3,6})", own)
    if m:
        own_keys.add(_canonical_patent_id(f"JP{m.group(1)}{m.group(2).zfill(6)}A"))

    texts: list[tuple[str, str]] = []
    for idx, text in enumerate(data.get("citation_info_texts") or [], start=1):
        texts.append((f"引用情報{idx}", text))
    for doc in data.get("documents") or []:
        if doc.get("kind") in ("引用情報", "citation_info"):
            label = doc.get("kind") or "引用情報"
            texts.append((label, doc.get("text") or doc.get("label") or ""))

    seen = set()
    candidates = []
    for source_label, text in texts:
        for pid, snippet in _iter_patent_citations(text):
            key = _canonical_patent_id(pid)
            if not key or key in own_keys or key in seen:
                continue
            seen.add(key)
            candidates.append({
                "patent_id": pid,
                "label": f"ドシエ引用{len(candidates) + 1}",
                "source": "opd_dossier",
                "source_label": source_label,
                "raw_text": snippet[:180],
            })
    for report in data.get("ocr_reports") or []:
        for cit in report.get("citations") or []:
            pid = cit.get("doc_id") or ""
            key = _canonical_patent_id(pid)
            if not key or key in own_keys or key in seen:
                continue
            seen.add(key)
            raw_parts = [
                cit.get("category", ""),
                cit.get("doc_label", ""),
                f"claims {cit.get('claims', '')}" if cit.get("claims") else "",
                cit.get("passages", ""),
            ]
            candidates.append({
                "patent_id": pid,
                "label": f"ドシエ引用{len(candidates) + 1}",
                "source": "opd_dossier_ocr",
                "source_label": report.get("label") or report.get("kind") or "OCR",
                "raw_text": " / ".join(part for part in raw_parts if part)[:180],
                "category": cit.get("category", ""),
            })
        for cit in report.get("family_citations") or []:
            pid = cit.get("doc_id") or ""
            key = _canonical_patent_id(pid)
            if not key or key in own_keys or key in seen:
                continue
            seen.add(key)
            candidates.append({
                "patent_id": pid,
                "label": cit.get("label") or f"ドシエ引用{len(candidates) + 1}",
                "source": "opd_dossier_ocr_family",
                "source_label": report.get("label") or report.get("kind") or "OCR",
                "raw_text": (cit.get("raw_text") or "")[:180],
                "category": cit.get("category", ""),
                "family_of": cit.get("family_of", ""),
            })
    for report in data.get("opd_pdf_reports") or []:
        for cit in report.get("citations") or []:
            pid = cit.get("doc_id") or ""
            key = _canonical_patent_id(pid)
            if not key or key in own_keys or key in seen:
                continue
            seen.add(key)
            raw_parts = [
                cit.get("category", ""),
                cit.get("doc_label", ""),
                f"claims {cit.get('claims', '')}" if cit.get("claims") else "",
                cit.get("passages", ""),
            ]
            candidates.append({
                "patent_id": pid,
                "label": f"ドシエ引用{len(candidates) + 1}",
                "source": "opd_attached_pdf_ocr",
                "source_label": report.get("label") or report.get("kind") or "OPD添付PDF OCR",
                "raw_text": " / ".join(part for part in raw_parts if part)[:180],
                "category": cit.get("category", ""),
            })
    return candidates


def extract_citation_candidates(case_id: str) -> tuple[dict, int]:
    data, code = load_opd_index(case_id)
    if code != 200:
        return data, code
    candidates = _decorate_citation_candidates(case_id, data.get("citation_candidates") or [])
    return {"candidates": candidates, "count": len(candidates)}, 200


def _decorate_citation_candidates(case_id: str, candidates: list[dict]) -> list[dict]:
    loaded_keys = _loaded_citation_keys(case_id)
    try:
        from modules.patent_downloader import build_google_patents_url, build_jplatpat_url
    except Exception:
        build_google_patents_url = lambda _pid: ""
        build_jplatpat_url = lambda _pid: ""

    out = []
    for cand in candidates:
        item = dict(cand)
        pid = item.get("patent_id") or ""
        key = _canonical_patent_id(pid)
        item["loaded"] = bool(key and key in loaded_keys)
        item["google_patents_url"] = build_google_patents_url(pid) if pid else ""
        item["jplatpat_url"] = build_jplatpat_url(pid) if pid else ""
        out.append(item)
    return out


def _loaded_citation_keys(case_id: str) -> set[str]:
    keys: set[str] = set()

    def add(value: str) -> None:
        key = _canonical_patent_id(value or "")
        if key:
            keys.add(key)

    meta = load_case_meta(case_id) or {}
    for cit in meta.get("citations") or []:
        add(cit.get("id", ""))
        add(cit.get("label", ""))

    case_dir = get_case_dir(case_id)
    citations_dir = case_dir / "citations"
    if citations_dir.exists():
        for path in citations_dir.glob("*.json"):
            add(path.stem)
            try:
                with path.open(encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            add(data.get("patent_number", ""))
            add(data.get("label", ""))
            add(Path(data.get("source_pdf", "")).stem)

    input_dir = case_dir / "input"
    if input_dir.exists():
        for path in input_dir.glob("*.pdf"):
            add(path.stem)
    return keys


def rebuild_ocr_reports(case_id: str) -> tuple[dict, int]:
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404
    cache_path = get_case_dir(case_id) / "dossier" / "opd_ocr_reports.json"
    reports = _build_ocr_reports(case_id)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("w", encoding="utf-8") as f:
        json.dump({
            "case_id": case_id,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "reports": reports,
        }, f, ensure_ascii=False, indent=2)

    data, code = load_opd_index(case_id)
    if code != 200:
        return data, code
    data["case_id"] = case_id
    data["patent_number"] = _hongan_patent_number(case_id)
    data["ocr_reports"] = reports
    data["citation_candidates"] = _extract_citation_candidates_from_index(data)
    data["ocr_scope"] = "hongan_embedded_isr"
    data["ocr_note"] = "本願PDF内のISRだけをOCRしました。OPD添付のIPER/拒絶理由PDFは未取得のため、別途保存/OCRが必要です。"
    return data, 200


def _load_or_build_ocr_reports(case_id: str) -> list[dict]:
    cache_path = get_case_dir(case_id) / "dossier" / "opd_ocr_reports.json"
    if cache_path.exists():
        try:
            with cache_path.open(encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data.get("reports"), list):
                return data["reports"]
        except (OSError, json.JSONDecodeError):
            pass

    reports = _build_ocr_reports(case_id)
    if reports:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("w", encoding="utf-8") as f:
            json.dump({
                "case_id": case_id,
                "built_at": datetime.now(timezone.utc).isoformat(),
                "reports": reports,
            }, f, ensure_ascii=False, indent=2)
    return reports


def _build_ocr_reports(case_id: str) -> list[dict]:
    """OPD表示用にISR/IPER等のOCR解析結果を返す。

    現段階では、OPD添付PDFの保存が未実装のため、本願PDF内に含まれるISRを
    既存OCRパーサで解析してドシエ表示へ合流させる。
    """
    try:
        from services import case_service as cs
        pdf_path = cs._resolve_hongan_pdf_for_isr_scan(case_id)
        parsed = cs._parse_embedded_isr_search_report(pdf_path)
        jp_family_by_num = cs._extract_jp_family_members_by_isr_num(parsed or {})
    except Exception as e:
        logger.info("OPD OCR report build skipped (%s): %s", case_id, e)
        return []

    if not parsed or not parsed.get("citations"):
        return []
    raw_text = parsed.get("raw_text") or ""
    family_citations = []
    for cit in parsed.get("citations") or []:
        family_num = cit.get("num")
        try:
            family_jps = jp_family_by_num.get(int(family_num or 0), [])
        except (TypeError, ValueError):
            family_jps = []
        for idx, jp_patent_id in enumerate(family_jps, start=1):
            label = f"本ISRD{family_num}易読"
            if len(family_jps) > 1:
                label = f"{label}{idx}"
            family_citations.append({
                "doc_id": jp_patent_id,
                "label": label,
                "raw_text": f"{cit.get('doc_id') or cit.get('doc_label') or ''} のJPファミリー",
                "family_of": cit.get("doc_id", ""),
                "isr_num": family_num,
                "category": cit.get("category", ""),
                "claims": cit.get("claims", ""),
            })
    return [{
        "kind": parsed.get("form") or "ISR",
        "label": "本願PDF内ISR OCR",
        "source": "hongan_pdf_embedded_isr",
        "filename": parsed.get("filename", ""),
        "language": parsed.get("language", ""),
        "intl_app_no": parsed.get("intl_app_no", ""),
        "citations": parsed.get("citations") or [],
        "family_citations": family_citations,
        "raw_text": raw_text[:20000],
        "raw_text_length": len(raw_text),
    }]


class _Command:
    __slots__ = ("op", "kwargs", "result_q")

    def __init__(self, op: str, kwargs: Dict[str, Any]):
        self.op = op
        self.kwargs = kwargs
        self.result_q: queue.Queue = queue.Queue(maxsize=1)


class OpdDossierSession:
    def __init__(self):
        self._cmd_q: queue.Queue[_Command] = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._ready_event = threading.Event()
        self._launch_error: Optional[str] = None
        self._started_at = 0.0

    def is_alive(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def open(self, case_id: str, *, timeout: int = 60) -> dict:
        if not self.is_alive():
            self._start_worker()
        if not self._ready_event.wait(timeout=min(timeout, 35)):
            return {"ok": False, "error": "ブラウザ起動がタイムアウトしました"}
        if self._launch_error:
            return {"ok": False, "error": self._launch_error}
        patent_number = _hongan_patent_number(case_id)
        url = _build_jplatpat_url(patent_number)
        if not url:
            return {"ok": False, "error": f"本願固定URLを生成できませんでした: {patent_number}"}
        return self._submit("open_opd", {"url": url, "patent_number": patent_number}, timeout=timeout)

    def collect(self, case_id: str, *, timeout: int = 45) -> dict:
        if not self.is_alive():
            return {"ok": False, "error": "OPDセッションが開かれていません。先に OPD を開いてください。"}
        result = self._submit("collect", {}, timeout=timeout)
        if result.get("ok"):
            payload = {
                "case_id": case_id,
                "patent_number": _hongan_patent_number(case_id),
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "page_url": result.get("url", ""),
                "expanded": bool(result.get("expanded")),
                "citation_info_expanded": bool(result.get("citation_info_expanded")),
                "page_text": result.get("page_text", ""),
                "citation_info_texts": result.get("citation_info_texts", []),
                "documents": result.get("documents", []),
                "targets": result.get("targets", []),
                "citation_candidates": result.get("citation_candidates", []),
                "ocr_reports": _load_or_build_ocr_reports(case_id),
                "warnings": result.get("warnings", []),
            }
            payload["citation_candidates"] = _extract_citation_candidates_from_index(payload)
            save_opd_index(case_id, payload)
            result.update(payload)
        return result

    def download_rejection_pdfs(self, case_id: str, *, target_indices: list[int] | None = None, timeout: int = 120) -> dict:
        if not self.is_alive():
            return {"ok": False, "error": "OPDセッションが開かれていません。先に OPD を開いてください。"}
        data, code = load_opd_index(case_id)
        if code != 200:
            return {"ok": False, "error": data.get("error", "OPDインデックス読込失敗")}
        all_targets = data.get("targets") or data.get("documents") or []
        if target_indices is not None:
            wanted = set()
            for idx in target_indices:
                try:
                    wanted.add(int(idx))
                except (TypeError, ValueError):
                    continue
            raw_targets = [
                t for i, t in enumerate(all_targets)
                if i in wanted and t.get("kind") in _REJECTION_KINDS
            ]
        else:
            raw_targets = [t for t in all_targets if t.get("kind") in _REJECTION_KINDS]
        targets, skipped = _downloadable_rejection_targets(raw_targets)
        if not targets:
            return {
                "ok": False,
                "error": "収集結果の中に安全に自動保存できるOPD添付書類がありません。対象行に添付がない場合は、OPDで対象PDFを開いて保存し、PDF手動取込を使ってください。",
                "downloads": skipped,
            }
        result = self._submit("download_rejection_pdfs", {
            "case_id": case_id,
            "targets": targets,
        }, timeout=timeout)
        if skipped:
            result.setdefault("downloads", [])
            result["downloads"].extend(skipped)
        if result.get("ok"):
            reports = _load_opd_pdf_reports(case_id)
            for item in result.get("downloads") or []:
                if not item.get("success") or not item.get("path"):
                    continue
                try:
                    report = _parse_opd_pdf_report(case_id, Path(item["path"]), item)
                except Exception as e:
                    item["success"] = False
                    item["error"] = f"OCR/解析失敗: {e}"
                    continue
                if not (report.get("raw_text") or "").strip() and not (report.get("box_v") or "").strip():
                    item["success"] = False
                    item["error"] = "PDFは保存できましたが、OCRテキストが0字でした。表紙/ビューアHTML/空PDFの可能性があります。"
                    continue
                reports = [r for r in reports if r.get("filename") != report.get("filename")]
                reports.append(report)
                item["report_kind"] = report.get("kind", "")
                item["has_box_v"] = bool(report.get("box_v"))
            _save_opd_pdf_reports(case_id, reports)
            refreshed, _ = load_opd_index(case_id)
            result["rejection_documents"] = refreshed.get("rejection_documents") or []
        return result

    def status(self) -> dict:
        if not self.is_alive():
            return {"alive": False}
        r = self._submit("status", {}, timeout=5)
        r["alive"] = True
        return r

    def close(self) -> dict:
        if not self.is_alive():
            return {"ok": True, "note": "not running"}
        try:
            self._submit("stop", {}, timeout=5)
        except Exception:
            pass
        self._running = False
        return {"ok": True}

    def _start_worker(self):
        self._running = True
        self._ready_event = threading.Event()
        self._launch_error = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="opd-dossier-worker")
        self._thread.start()
        self._started_at = time.time()

    def _submit(self, op: str, kwargs: dict, *, timeout: int) -> dict:
        cmd = _Command(op, kwargs)
        self._cmd_q.put(cmd)
        try:
            return cmd.result_q.get(timeout=timeout)
        except queue.Empty:
            return {"ok": False, "error": f"タイムアウト ({timeout}s) op={op}"}

    def _run(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._launch_error = "playwright が未インストールです"
            self._ready_event.set()
            self._running = False
            return

        self._p = self._browser = self._ctx = self._page = None
        try:
            self._p = sync_playwright().start()
            self._browser = self._p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            self._ctx = self._browser.new_context(
                locale="ja-JP",
                viewport={"width": 1280, "height": 900},
                accept_downloads=True,
            )
            self._page = self._ctx.new_page()
            self._ready_event.set()

            while self._running:
                try:
                    cmd = self._cmd_q.get(timeout=1.0)
                except queue.Empty:
                    if self._browser is not None and not self._browser.is_connected():
                        break
                    continue
                if cmd.op == "stop":
                    cmd.result_q.put({"ok": True})
                    break
                try:
                    cmd.result_q.put(self._handle(cmd.op, cmd.kwargs))
                except Exception as e:
                    logger.exception("opd op error: %s", cmd.op)
                    cmd.result_q.put({"ok": False, "error": f"{type(e).__name__}: {e}"})
        except Exception as e:
            self._launch_error = f"ブラウザ起動失敗: {e}"
            self._ready_event.set()
        finally:
            self._cleanup()
            self._running = False

    def _cleanup(self):
        for obj in (getattr(self, "_ctx", None), getattr(self, "_browser", None)):
            try:
                if obj:
                    obj.close()
            except Exception:
                pass
        try:
            if self._p:
                self._p.stop()
        except Exception:
            pass

    def _handle(self, op: str, kw: dict) -> dict:
        if op == "open_opd":
            return self._op_open_opd(kw["url"], kw.get("patent_number", ""))
        if op == "collect":
            return self._op_collect()
        if op == "download_rejection_pdfs":
            return self._op_download_rejection_pdfs(kw["case_id"], kw.get("targets") or [])
        if op == "status":
            return {"ok": True, "url": self._page.url if self._page else ""}
        return {"ok": False, "error": f"unknown op: {op}"}

    def _op_open_opd(self, url: str, patent_number: str) -> dict:
        page = self._page
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1200)
        _dismiss_modals(page)
        clicked = _click_first_visible(page, _OPD_SELECTORS, timeout=1200)
        if clicked:
            try:
                page.wait_for_timeout(2500)
                if self._ctx.pages:
                    self._page = self._ctx.pages[-1]
                    page = self._page
            except Exception:
                pass
            _dismiss_modals(page)
        return {
            "ok": True,
            "url": page.url,
            "patent_number": patent_number,
            "opd_clicked": bool(clicked),
            "hint": "" if clicked else "OPDボタンの自動クリックに失敗しました。ブラウザ側でOPDを開いてから「OPD書類を収集」を押してください。",
        }

    def _op_collect(self) -> dict:
        page = self._page
        _dismiss_modals(page)
        expanded = _click_expand_all_documents(page)
        if expanded:
            page.wait_for_timeout(2500)
        citation_expanded = _click_show_all_citation_info(page)
        if citation_expanded:
            page.wait_for_timeout(2000)
        documents = _scrape_documents(page)
        page_text = _scrape_page_text(page)
        citation_info_texts = _scrape_citation_info_texts(page)
        targets = [d for d in documents if d.get("target")]
        targets.sort(key=lambda d: (-int(d.get("priority") or 0), d.get("kind", ""), d.get("label", "")))
        warnings = []
        if not expanded and not _documents_look_expanded(documents):
            warnings.append("「書類情報を全て開く」ボタンを自動クリックできませんでした。未展開なら手動で開いて再収集してください。")
        if not targets:
            warnings.append("対象書類候補が見つかりませんでした。OPD画面が開かれているか確認してください。")
        targets = _link_attached_documents(documents)
        targets.sort(key=lambda d: (-int(d.get("priority") or 0), d.get("kind", ""), d.get("date", ""), d.get("label", "")))
        citation_candidates = _extract_citation_candidates_from_index({
            "documents": documents,
            "citation_info_texts": citation_info_texts,
        })
        return {
            "ok": True,
            "url": page.url,
            "expanded": bool(expanded),
            "citation_info_expanded": bool(citation_expanded),
            "page_text": page_text,
            "citation_info_texts": citation_info_texts,
            "documents": documents,
            "targets": targets,
            "citation_candidates": citation_candidates,
            "warnings": warnings,
        }

    def _op_download_rejection_pdfs(self, case_id: str, targets: list[dict]) -> dict:
        page = self._page
        _dismiss_modals(page)
        expanded = _click_expand_all_documents(page)
        if expanded:
            page.wait_for_timeout(2500)
            _dismiss_modals(page)
        downloads = []
        for target in targets:
            label = target.get("label") or target.get("text") or ""
            kind = target.get("kind") or ""
            date = target.get("date") or _extract_doc_date(label)
            item = {"kind": kind, "label": label, "date": date, "success": False}
            try:
                direct_ok, direct_path = _try_direct_opd_recipe_download(page, case_id, target)
                if direct_ok:
                    item["success"] = True
                    item["path"] = direct_path
                    item["resolved_by"] = "saved_wsh0901_recipe"
                    downloads.append(item)
                    continue
                row = _find_opd_attachment_row_for_target(page, target)
                if row is None:
                    item["error"] = "OPD画面上で対象の添付書類行を見つけられませんでした"
                    downloads.append(item)
                    continue
                clicked, path = _click_row_pdf_and_capture_download(page, row, case_id, target)
                if not clicked:
                    item["error"] = "添付書類行の原文ボタンをクリックできませんでした"
                    downloads.append(item)
                    continue
                item["success"] = True
                item["path"] = path
                downloads.append(item)
            except Exception as e:
                item["error"] = f"{type(e).__name__}: {e}"
                downloads.append(item)
        return {"ok": True, "downloads": downloads}


def _dismiss_modals(page) -> None:
    for sel in _DISMISS_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=250):
                loc.click(timeout=800)
                page.wait_for_timeout(300)
        except Exception:
            continue


def _click_first_visible(page, selectors: list[str], *, timeout: int) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=timeout):
                loc.click(timeout=2000)
                return True
        except Exception:
            continue
    return False


def _click_expand_all_documents(page) -> bool:
    return _click_opd_toolbar_button(page, _EXPAND_ALL_LABELS, _EXPAND_ALL_SELECTORS)


def _click_show_all_citation_info(page) -> bool:
    return _click_opd_toolbar_button(page, _SHOW_ALL_CITATION_LABELS, _SHOW_ALL_CITATION_SELECTORS)


def _documents_look_expanded(documents: list[dict]) -> bool:
    if not documents:
        return False
    has_target = any(d.get("target") for d in documents)
    has_attachment = any(_ATTACHED_PAT.search(d.get("text") or d.get("label") or "") for d in documents)
    has_original = any(re.search(r"原文|Original", d.get("text") or d.get("label") or "", re.I) for d in documents)
    return bool(has_target and (has_attachment or has_original))


def _click_opd_toolbar_button(page, labels: list[str], selectors: list[str]) -> bool:
    """Click OPD toolbar buttons whose visible label may be split across nested nodes."""
    if _click_first_visible(page, selectors, timeout=1200):
        return True
    try:
        return bool(page.evaluate(
            """({labels}) => {
                const normalizedLabels = labels.map(label => String(label || '').replace(/[\\s　]+/g, ''));
                const clickableSelector = 'button,a,[role="button"],input[type="button"],input[type="submit"]';
                const isVisible = (el) => {
                  const r = el.getBoundingClientRect();
                  const s = window.getComputedStyle(el);
                  return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                };
                const normText = (el) => {
                  const value = el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '';
                  return ((el.innerText || el.textContent || '') + ' ' + value).replace(/[\\s　]+/g, '');
                };
                const clickableAncestor = (el) => {
                  let cur = el;
                  for (let i = 0; cur && i < 5; i += 1, cur = cur.parentElement) {
                    if (cur.matches && cur.matches(clickableSelector)) return cur;
                    if (cur.onclick || cur.tabIndex >= 0) return cur;
                  }
                  return el;
                };
                const score = (el, text) => {
                  const tag = (el.tagName || '').toLowerCase();
                  let v = 0;
                  if (tag === 'button' || tag === 'a') v += 40;
                  if (el.getAttribute('role') === 'button') v += 30;
                  if (el.onclick) v += 15;
                  if (el.tabIndex >= 0) v += 5;
                  if (normalizedLabels.some(label => text === label)) v += 20;
                  if (normalizedLabels.some(label => text.startsWith(label))) v += 8;
                  return v;
                };
                const candidates = [];
                const nodes = document.querySelectorAll('button,a,[role="button"],input[type="button"],input[type="submit"],span,div,li');
                for (const el of nodes) {
                  if (!isVisible(el)) continue;
                  const text = normText(el);
                  if (!text) continue;
                  if (!normalizedLabels.some(label => text === label || text.includes(label))) continue;
                  const target = clickableAncestor(el);
                  if (!target || !isVisible(target)) continue;
                  const targetText = normText(target) || text;
                  candidates.push({target, score: score(target, targetText), area: target.getBoundingClientRect().width * target.getBoundingClientRect().height});
                }
                candidates.sort((a, b) => b.score - a.score || a.area - b.area);
                const found = candidates[0] && candidates[0].target;
                if (!found) return false;
                found.scrollIntoView({block: 'center', inline: 'center'});
                for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  found.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
                }
                return true;
            }""",
            {"labels": labels},
        ))
    except Exception:
        return False


def _target_needles(target: dict) -> list[str]:
    text = _normalize_doc_text(target.get("text") or target.get("label") or "")
    kind = target.get("kind") or ""
    date = target.get("date") or _extract_doc_date(text)
    needles = []
    if date:
        needles.append(date)
    if kind == "IPER":
        needles.append("preliminary")
    elif "Final Rejection" in kind:
        needles.append("final")
    elif "Non Final" in kind:
        needles.append("non")
    elif "CN" in kind:
        needles.append("office")
    for token in re.findall(r"[A-Za-z]{5,}|[\u4e00-\u9fff\u3040-\u30ff]{2,}", text)[:8]:
        if token.lower() not in ("original", "english", "classification", "citation"):
            needles.append(token)
    return needles[:5]


def _find_opd_row_for_target(page, target: dict):
    needles = [n.lower() for n in _target_needles(target) if n]
    selectors = ["table tbody tr", "mat-row, .mat-row", "li", "div"]
    for sel in selectors:
        try:
            locs = page.locator(sel).all()
        except Exception:
            continue
        for loc in locs[:1000]:
            try:
                text = _normalize_doc_text(loc.inner_text(timeout=250))
            except Exception:
                continue
            low = text.lower()
            if len(text) > 1800:
                continue
            if needles and sum(1 for n in needles if n.lower() in low) >= min(2, len(needles)):
                return loc
    return None


def _find_opd_attachment_row_for_target(page, target: dict):
    date = target.get("date") or _extract_doc_date(target.get("label") or target.get("text") or "")
    labels = [
        _normalize_doc_text(label)
        for label in (target.get("attachment_labels") or [])
        if _ATTACHED_PAT.search(label or "") and not _OPD_ATTACHMENT_EXCLUDE_PAT.search(label or "")
    ]
    selectors = ["table tbody tr", "mat-row, .mat-row", "li", "div"]
    for sel in selectors:
        try:
            locs = page.locator(sel).all()
        except Exception:
            continue
        for loc in locs[:1000]:
            try:
                text = _normalize_doc_text(loc.inner_text(timeout=250))
            except Exception:
                continue
            if len(text) > 1200:
                continue
            if not _ATTACHED_PAT.search(text):
                continue
            if _OPD_ATTACHMENT_EXCLUDE_PAT.search(text):
                continue
            if date and date not in text:
                continue
            if labels:
                low = text.lower()
                if not any(label[:80].lower() in low or low[:120] in label.lower() for label in labels):
                    continue
            return loc
    return None


def _safe_opd_pdf_name(target: dict, suggested: str = "") -> str:
    base = suggested or "_".join(part for part in [
        target.get("date") or _extract_doc_date(target.get("label") or ""),
        target.get("kind") or "OPD",
        hashlib.sha1((target.get("label") or "").encode("utf-8", errors="ignore")).hexdigest()[:8],
    ] if part)
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    return re.sub(r'[<>:"/\\|?*]', "_", base)


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for i in range(2, 1000):
        candidate = path.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{stem}_{int(time.time())}{suffix}")


def _is_pdf_bytes(body: bytes | None, content_type: str = "") -> bool:
    if not body:
        return False
    # J-PlatPat/Chrome can report application/pdf while Playwright exposes the
    # Chrome PDF viewer HTML shell. Only the PDF magic is reliable here.
    return body[:5] == b"%PDF-"


def _filename_from_url(url: str) -> str:
    try:
        name = Path(urlparse(url).path).name
    except Exception:
        return ""
    return name if name.lower().endswith(".pdf") else ""


def _walk_json_values(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k, v
            yield from _walk_json_values(v)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_json_values(item)


def _iter_pdf_url_candidates(payload) -> list[str]:
    urls = []
    seen = set()
    for key, value in _walk_json_values(payload):
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text:
            continue
        key_text = str(key or "").lower()
        looks_like = (
            ".pdf" in text.lower()
            or key_text in {"docu_url", "pdf_url", "url"}
            or key_text.endswith("url")
        )
        if not looks_like:
            continue
        if text in seen:
            continue
        seen.add(text)
        urls.append(text)
    return urls


def _response_signal_entry(response) -> dict:
    req = response.request
    post_data = ""
    try:
        post_data = req.post_data or ""
    except Exception:
        pass
    return {
        "url": response.url,
        "status": getattr(response, "status", 0),
        "method": getattr(req, "method", ""),
        "content_type": (response.headers or {}).get("content-type", ""),
        "post_data": post_data[:5000] if isinstance(post_data, str) else "",
    }


def _save_opd_download_signal(case_id: str, target: dict, entries: list[dict], *, saved_path: str = "", error: str = "") -> None:
    path = _opd_download_signals_path(case_id)
    try:
        if path.exists():
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {"case_id": case_id, "signals": []}
    except (OSError, json.JSONDecodeError):
        data = {"case_id": case_id, "signals": []}
    data.setdefault("signals", []).append({
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "kind": target.get("kind", ""),
        "label": target.get("label") or target.get("text") or "",
        "date": target.get("date") or _extract_doc_date(target.get("label") or target.get("text") or ""),
        "saved_path": saved_path,
        "error": error,
        "entries": entries[-40:],
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_opd_download_signals(case_id: str) -> list[dict]:
    path = _opd_download_signals_path(case_id)
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data.get("signals") if isinstance(data.get("signals"), list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _find_opd_download_recipe(case_id: str, target: dict) -> dict | None:
    target_label = target.get("label") or target.get("text") or ""
    target_date = target.get("date") or _extract_doc_date(target_label)
    target_kind = target.get("kind") or ""
    for signal in reversed(_load_opd_download_signals(case_id)):
        label = signal.get("label") or ""
        date = signal.get("date") or _extract_doc_date(label)
        kind = signal.get("kind") or ""
        if target_date and date and target_date != date:
            continue
        if target_kind and kind and target_kind != kind:
            continue
        if target_label and label and _rejection_cover_key(label, date) != _rejection_cover_key(target_label, target_date):
            continue
        for entry in signal.get("entries") or []:
            if not str(entry.get("url", "")).endswith("/app/opdgw/wsh0901"):
                continue
            post_data = entry.get("post_data") or ""
            try:
                body = json.loads(post_data)
            except json.JSONDecodeError:
                continue
            if body.get("DOC_ID") and body.get("FILE_TYPE") == "PDF":
                return {"endpoint": entry.get("url"), "body": body, "source_signal": signal.get("captured_at", "")}
    return None


def _try_direct_opd_recipe_download(page, case_id: str, target: dict) -> tuple[bool, str]:
    recipe = _find_opd_download_recipe(case_id, target)
    if not recipe:
        return False, ""
    entry = {
        "signal_type": "direct_recipe",
        "url": recipe["endpoint"],
        "method": "POST",
        "post_data": json.dumps(recipe["body"], ensure_ascii=False),
        "source_signal": recipe.get("source_signal", ""),
    }
    try:
        payload = page.evaluate(
            """async ({url, body}) => {
                const u = new URL(url);
                const path = u.pathname;
                const res = await fetch(path, {
                    method: 'POST',
                    headers: {'content-type': 'application/json;charset=UTF-8'},
                    body: JSON.stringify(body),
                    credentials: 'include'
                });
                return {ok: res.ok, status: res.status, json: await res.json()};
            }""",
            {"url": recipe["endpoint"], "body": recipe["body"]},
        )
        entry["status"] = payload.get("status")
        entry["ok"] = payload.get("ok")
        candidates = _iter_pdf_url_candidates(payload.get("json") or {})
        entry["pdf_url_candidates"] = candidates[:20]
        dest_dir = _opd_pdf_dir(case_id)
        for url in candidates:
            full_url = url if re.match(r"^https?://", url, re.I) else urljoin(JPLATPAT_ORIGIN, url)
            ok, path, err = _fetch_pdf_url_with_requests(page, full_url, target, dest_dir)
            if ok:
                entry["resolved_pdf_url"] = full_url
                entry["resolved_by"] = "direct_recipe_requests_cookie"
                entry["saved_as"] = path
                _save_opd_download_signal(case_id, target, [entry], saved_path=path)
                return True, path
            entry.setdefault("resolve_errors", []).append(f"{full_url}: {err}")
        _save_opd_download_signal(case_id, target, [entry], error="direct recipe did not resolve a real PDF")
    except Exception as e:
        entry["error"] = f"{type(e).__name__}: {e}"
        _save_opd_download_signal(case_id, target, [entry], error=entry["error"])
    return False, ""


def _save_pdf_body_from_signal(dest_dir: Path, target: dict, body: bytes, *, suggested: str = "") -> Path:
    dest = _unique_path(dest_dir / _safe_opd_pdf_name(target, suggested))
    dest.write_bytes(body)
    return dest


def _cookie_header_from_context(page, url: str) -> str:
    try:
        cookies = page.context.cookies(url)
    except Exception:
        cookies = []
    return "; ".join(f"{c.get('name')}={c.get('value')}" for c in cookies if c.get("name"))


def _requests_get_with_tls_fallback(url: str, *, headers: dict, timeout: int = 30):
    import os
    import requests
    import urllib3

    kwargs = {"headers": headers, "timeout": timeout}
    try:
        import certifi
        kwargs["verify"] = certifi.where()
    except Exception:
        pass
    try:
        return requests.get(url, **kwargs)
    except requests.exceptions.SSLError:
        if os.environ.get("PATENT_COMPARE_INSECURE_SSL_FALLBACK", "1") == "0":
            raise
        kwargs["verify"] = False
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        return requests.get(url, **kwargs)


def _fetch_pdf_url_with_requests(page, url: str, target: dict, dest_dir: Path) -> tuple[bool, str, str]:
    headers = dict(_HTTP_HEADERS)
    cookie = _cookie_header_from_context(page, url)
    if cookie:
        headers["Cookie"] = cookie
    headers["Referer"] = JPLATPAT_ORIGIN + "/h0400"
    resp = _requests_get_with_tls_fallback(url, headers=headers, timeout=30)
    body = resp.content or b""
    if resp.status_code == 200 and _is_pdf_bytes(body, resp.headers.get("content-type", "")):
        dest = _save_pdf_body_from_signal(dest_dir, target, body, suggested=_filename_from_url(url))
        return True, str(dest), ""
    sample = body[:80].decode("utf-8", errors="ignore").replace("\n", " ")
    return False, "", f"requests status={resp.status_code}, content-type={resp.headers.get('content-type','')}, head={sample}"


def _try_save_pdf_from_response(page, response, case_id: str, target: dict) -> tuple[bool, str, dict]:
    dest_dir = _opd_pdf_dir(case_id)
    entry = _response_signal_entry(response)
    content_type = entry.get("content_type", "")
    url_low = (response.url or "").lower()
    likely = (
        "pdf" in content_type.lower()
        or ".pdf" in url_low
        or "/wsp" in url_low
        or "json" in content_type.lower()
        or "download" in url_low
        or "docu" in url_low
        or "opd" in url_low
    )
    if not likely:
        return False, "", entry
    try:
        body = response.body()
    except Exception:
        body = b""
    if _is_pdf_bytes(body, content_type):
        suggested = _filename_from_url(response.url)
        dest = _save_pdf_body_from_signal(dest_dir, target, body, suggested=suggested)
        entry["saved_as"] = str(dest)
        entry["signal_type"] = "pdf_response"
        return True, str(dest), entry

    payload = None
    if "json" in content_type.lower() or url_low.endswith(("wsp0701", "wsp1101", "wsp1201")) or "/wsp" in url_low:
        try:
            payload = response.json()
        except Exception:
            try:
                payload = json.loads(response.text())
            except Exception:
                payload = None
    if payload is None:
        return False, "", entry

    candidates = _iter_pdf_url_candidates(payload)
    entry["signal_type"] = "json_response"
    entry["pdf_url_candidates"] = candidates[:20]
    for url in candidates:
        full_url = url if re.match(r"^https?://", url, re.I) else urljoin(JPLATPAT_ORIGIN, url)
        try:
            resp = page.context.request.get(full_url, timeout=30000)
            ct = (resp.headers or {}).get("content-type", "")
            pdf_body = resp.body()
            if not resp.ok or not _is_pdf_bytes(pdf_body, ct):
                ok, path, err = _fetch_pdf_url_with_requests(page, full_url, target, dest_dir)
                if ok:
                    entry["saved_as"] = path
                    entry["resolved_pdf_url"] = full_url
                    entry["resolved_by"] = "requests_cookie"
                    return True, path, entry
                entry.setdefault("resolve_errors", []).append(f"{full_url}: not a real PDF via browser/request ({err})")
                continue
            suggested = _filename_from_url(full_url)
            dest = _save_pdf_body_from_signal(dest_dir, target, pdf_body, suggested=suggested)
            entry["saved_as"] = str(dest)
            entry["resolved_pdf_url"] = full_url
            entry["resolved_by"] = "playwright_request"
            return True, str(dest), entry
        except Exception as e:
            try:
                ok, path, err = _fetch_pdf_url_with_requests(page, full_url, target, dest_dir)
                if ok:
                    entry["saved_as"] = path
                    entry["resolved_pdf_url"] = full_url
                    entry["resolved_by"] = "requests_cookie_after_playwright_error"
                    return True, path, entry
                entry.setdefault("resolve_errors", []).append(f"{full_url}: {type(e).__name__}: {e}; requests fallback: {err}")
            except Exception as e2:
                entry.setdefault("resolve_errors", []).append(f"{full_url}: {type(e).__name__}: {e}; requests fallback {type(e2).__name__}: {e2}")
            continue
    return False, "", entry


def _click_row_pdf_and_capture_download(page, row, case_id: str, target: dict) -> tuple[bool, str]:
    dest_dir = _opd_pdf_dir(case_id)
    clickers = []
    for sel in _OPD_ATTACHMENT_DOWNLOAD_SELECTORS:
        try:
            count = min(row.locator(sel).count(), 3)
        except Exception:
            continue
        for idx in range(count):
            loc = row.locator(sel).nth(idx)
            try:
                text = _normalize_doc_text(loc.inner_text(timeout=250))
            except Exception:
                text = ""
            if re.search(r"英訳|English|一括|PDFダウンロード", text, re.I):
                continue
            clickers.append(loc)
    if not clickers:
        return False, ""

    for loc in clickers:
        responses = []
        downloads = []

        def on_response(response):
            try:
                responses.append(response)
            except Exception:
                pass

        def on_download(download):
            try:
                downloads.append(download)
            except Exception:
                pass

        try:
            page.context.on("response", on_response)
            page.on("download", on_download)
            before_pages = list(page.context.pages)
            loc.click(timeout=2500)
            page.wait_for_timeout(6500)

            for download in downloads:
                try:
                    name = _safe_opd_pdf_name(target, download.suggested_filename)
                    dest = _unique_path(dest_dir / name)
                    download.save_as(str(dest))
                    _save_opd_download_signal(case_id, target, [{
                        "signal_type": "browser_download",
                        "suggested_filename": download.suggested_filename,
                        "saved_as": str(dest),
                    }], saved_path=str(dest))
                    return True, str(dest)
                except Exception:
                    continue

            signal_entries = []
            for response in responses:
                try:
                    ok, path, entry = _try_save_pdf_from_response(page, response, case_id, target)
                    signal_entries.append(entry)
                    if ok:
                        _save_opd_download_signal(case_id, target, signal_entries, saved_path=path)
                        return True, path
                except Exception as e:
                    try:
                        entry = _response_signal_entry(response)
                    except Exception:
                        entry = {"url": getattr(response, "url", ""), "error": f"{type(e).__name__}: {e}"}
                    entry["error"] = f"{type(e).__name__}: {e}"
                    signal_entries.append(entry)

            after_pages = list(page.context.pages)
            if len(after_pages) > len(before_pages):
                for new_page in after_pages[len(before_pages):]:
                    try:
                        new_page.wait_for_load_state("domcontentloaded", timeout=6000)
                    except Exception:
                        pass
                    url = new_page.url or ""
                    signal_entries.append({"signal_type": "popup", "url": url})
                    if ".pdf" in url.lower():
                        try:
                            resp = page.context.request.get(url, timeout=30000)
                            body = resp.body()
                            ct = (resp.headers or {}).get("content-type", "")
                            if resp.ok and _is_pdf_bytes(body, ct):
                                dest = _save_pdf_body_from_signal(dest_dir, target, body, suggested=_filename_from_url(url))
                                signal_entries[-1]["saved_as"] = str(dest)
                                _save_opd_download_signal(case_id, target, signal_entries, saved_path=str(dest))
                                try:
                                    new_page.close()
                                except Exception:
                                    pass
                                return True, str(dest)
                            ok, path, err = _fetch_pdf_url_with_requests(page, url, target, dest_dir)
                            if ok:
                                signal_entries[-1]["saved_as"] = path
                                signal_entries[-1]["resolved_by"] = "requests_cookie"
                                _save_opd_download_signal(case_id, target, signal_entries, saved_path=path)
                                try:
                                    new_page.close()
                                except Exception:
                                    pass
                                return True, path
                            signal_entries[-1]["error"] = err
                        except Exception as e:
                            try:
                                ok, path, err = _fetch_pdf_url_with_requests(page, url, target, dest_dir)
                                if ok:
                                    signal_entries[-1]["saved_as"] = path
                                    signal_entries[-1]["resolved_by"] = "requests_cookie_after_playwright_error"
                                    _save_opd_download_signal(case_id, target, signal_entries, saved_path=path)
                                    try:
                                        new_page.close()
                                    except Exception:
                                        pass
                                    return True, path
                                signal_entries[-1]["error"] = f"{type(e).__name__}: {e}; requests fallback: {err}"
                            except Exception as e2:
                                signal_entries[-1]["error"] = f"{type(e).__name__}: {e}; requests fallback {type(e2).__name__}: {e2}"
                    try:
                        new_page.close()
                    except Exception:
                        pass
            _save_opd_download_signal(case_id, target, signal_entries, error="PDFレスポンスを特定できませんでした")
        except Exception as e:
            _save_opd_download_signal(case_id, target, [], error=f"{type(e).__name__}: {e}")
            # Some OPD actions open a new page instead of a download.
            try:
                before_pages = list(page.context.pages)
                loc.click(timeout=2000)
                page.wait_for_timeout(2000)
                after_pages = list(page.context.pages)
                if len(after_pages) > len(before_pages):
                    new_page = after_pages[-1]
                    try:
                        new_page.wait_for_load_state("domcontentloaded", timeout=6000)
                    except Exception:
                        pass
                    url = new_page.url or ""
                    if ".pdf" in url.lower():
                        resp = page.context.request.get(url, timeout=30000)
                        if resp.ok:
                            dest = dest_dir / _safe_opd_pdf_name(target)
                            dest.write_bytes(resp.body())
                            try:
                                new_page.close()
                            except Exception:
                                pass
                            return True, str(dest)
                    try:
                        new_page.close()
                    except Exception:
                        pass
            except Exception:
                continue
        finally:
            try:
                page.context.remove_listener("response", on_response)
            except Exception:
                pass
            try:
                page.remove_listener("download", on_download)
            except Exception:
                pass
    return False, ""


def _scrape_documents(page) -> list[dict]:
    docs: list[dict] = []
    seen = set()
    for sel in _SCRAPE_SELECTORS:
        try:
            locs = page.locator(sel).all()
        except Exception:
            continue
        for loc in locs[:800]:
            try:
                text = _normalize_doc_text(loc.inner_text(timeout=600))
            except Exception:
                continue
            if len(text) < 4 or len(text) > 1200:
                continue
            classified = _classify_opd_document(text)
            is_attached = bool(_ATTACHED_PAT.search(text))
            if not classified and not is_attached:
                continue
            href = ""
            try:
                href = loc.get_attribute("href", timeout=300) or ""
            except Exception:
                pass
            kind = classified["kind"] if classified else "添付書類"
            key = (kind, text[:180], href)
            if key in seen:
                continue
            seen.add(key)
            docs.append({
                "label": text[:180],
                "text": text,
                "kind": kind,
                "priority": classified["priority"] if classified else 0,
                "target": bool(classified),
                "note": classified.get("note", "") if classified else "",
                "href": href,
            })
    return docs


def _scrape_page_text(page) -> str:
    try:
        text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""
    return _normalize_doc_text(text)[:120000]


def _scrape_citation_info_texts(page) -> list[str]:
    texts: list[str] = []
    seen = set()
    selectors = [
        'a:has-text("引用情報")',
        'button:has-text("引用情報")',
        '[role="button"]:has-text("引用情報")',
        'text="引用情報"',
    ]
    for sel in selectors:
        try:
            count = min(page.locator(sel).count(), 20)
        except Exception:
            continue
        for idx in range(count):
            try:
                loc = page.locator(sel).nth(idx)
                if not loc.is_visible(timeout=500):
                    continue
                before_pages = list(page.context.pages)
                loc.click(timeout=1500)
                page.wait_for_timeout(1200)
                active = page
                after_pages = list(page.context.pages)
                if len(after_pages) > len(before_pages):
                    active = after_pages[-1]
                    try:
                        active.wait_for_load_state("domcontentloaded", timeout=5000)
                    except Exception:
                        pass
                text = _scrape_page_text(active)
                if text and text not in seen:
                    seen.add(text)
                    texts.append(text)
                if active is not page:
                    try:
                        active.close()
                    except Exception:
                        pass
                else:
                    _dismiss_modals(page)
                    try:
                        page.keyboard.press("Escape")
                    except Exception:
                        pass
            except Exception:
                continue
    return texts


_SESSION: Optional[OpdDossierSession] = None
_LOCK = threading.Lock()


def get_session() -> OpdDossierSession:
    global _SESSION
    with _LOCK:
        if _SESSION is None:
            _SESSION = OpdDossierSession()
        return _SESSION


def reset_session() -> None:
    global _SESSION
    with _LOCK:
        if _SESSION is not None:
            try:
                _SESSION.close()
                if _SESSION._thread is not None:
                    _SESSION._thread.join(timeout=3)
            except Exception:
                pass
        _SESSION = None
