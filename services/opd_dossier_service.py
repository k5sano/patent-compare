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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from services.case_service import get_case_dir, load_case_meta, load_json_file

logger = logging.getLogger(__name__)

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
    'button:has-text("書類情報をすべて開く")',
    'a:has-text("書類情報をすべて開く")',
    '[role="button"]:has-text("書類情報をすべて開く")',
    'button:has-text("すべて開く")',
    'a:has-text("すべて開く")',
    '[role="button"]:has-text("すべて開く")',
    'button:has-text("Open all")',
    'a:has-text("Open all")',
    '[role="button"]:has-text("Open all")',
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
            last_target.setdefault("attachment_labels", []).append(_normalize_doc_text(text)[:180])
    return targets


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
        return {"documents": [], "targets": [], "exists": False}, 200
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        _refresh_targets_from_documents(data, case_id=case_id)
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
    data["citation_candidates"] = _extract_citation_candidates_from_index(data)
    return data


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
    return candidates


def extract_citation_candidates(case_id: str) -> tuple[dict, int]:
    data, code = load_opd_index(case_id)
    if code != 200:
        return data, code
    candidates = data.get("citation_candidates") or []
    return {"candidates": candidates, "count": len(candidates)}, 200


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
    except Exception as e:
        logger.info("OPD OCR report build skipped (%s): %s", case_id, e)
        return []

    if not parsed or not parsed.get("citations"):
        return []
    raw_text = parsed.get("raw_text") or ""
    return [{
        "kind": parsed.get("form") or "ISR",
        "label": "本願PDF内ISR OCR",
        "source": "hongan_pdf_embedded_isr",
        "filename": parsed.get("filename", ""),
        "language": parsed.get("language", ""),
        "intl_app_no": parsed.get("intl_app_no", ""),
        "citations": parsed.get("citations") or [],
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
                "page_text": result.get("page_text", ""),
                "citation_info_texts": result.get("citation_info_texts", []),
                "documents": result.get("documents", []),
                "targets": result.get("targets", []),
                "citation_candidates": result.get("citation_candidates", []),
                "warnings": result.get("warnings", []),
            }
            save_opd_index(case_id, payload)
            result.update(payload)
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
            self._ctx = self._browser.new_context(locale="ja-JP", viewport={"width": 1280, "height": 900})
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
        expanded = _click_first_visible(page, _EXPAND_ALL_SELECTORS, timeout=1500)
        if expanded:
            page.wait_for_timeout(2500)
        documents = _scrape_documents(page)
        page_text = _scrape_page_text(page)
        citation_info_texts = _scrape_citation_info_texts(page)
        targets = [d for d in documents if d.get("target")]
        targets.sort(key=lambda d: (-int(d.get("priority") or 0), d.get("kind", ""), d.get("label", "")))
        warnings = []
        if not expanded:
            warnings.append("「書類情報をすべて開く」ボタンを自動クリックできませんでした。未展開なら手動で開いて再収集してください。")
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
            "page_text": page_text,
            "citation_info_texts": citation_info_texts,
            "documents": documents,
            "targets": targets,
            "citation_candidates": citation_candidates,
            "warnings": warnings,
        }


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
