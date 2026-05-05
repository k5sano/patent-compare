#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""J-PlatPat から日本特許公報の明細書 PDF を取得する。

J-PlatPat の画面操作で番号照会を実行し、内部の公報 PDF URL を取得して
ページ PDF を結合する。
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import fitz
import requests


JPLATPAT_NUMBER_INQUIRY_URL = "https://www.j-platpat.inpit.go.jp/p0000"
JPLATPAT_ORIGIN = "https://www.j-platpat.inpit.go.jp"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


@dataclass(frozen=True)
class JplatpatPatentNo:
    raw: str
    number: str
    display_number: str
    filename_stem: str
    doc_id: str
    inquiry_selectors: tuple[str, ...]
    kind: str
    fixed_url: str = ""


def normalize_jp_patent_number(raw: str) -> JplatpatPatentNo:
    """JP 特許番号を J-PlatPat 番号照会向けに正規化する。"""
    text = (raw or "").strip()
    if not text:
        raise ValueError("特許番号が空です")

    work = text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    work = re.sub(r"[－−―—ー]", "-", work)
    work = re.sub(r"(号公報|公報|明細書|号)", " ", work)
    work = re.sub(r"[()（）「」【】『』,，\s]+", " ", work).strip()

    pub_patterns = [
        r"特開\s*(\d{4})\s*[-/]\s*(\d{1,7})",
        r"JP\s*-?\s*(\d{4})\s*-?\s*(\d{3,7})\s*-?\s*A\d?",
    ]
    for pat in pub_patterns:
        m = re.search(pat, work, re.IGNORECASE)
        if m:
            year = m.group(1)
            serial = m.group(2).zfill(6)
            number = f"{year}-{serial}"
            return JplatpatPatentNo(
                raw=text,
                number=number,
                display_number=f"特開{number}",
                filename_stem=f"JP{year}{serial}A",
                doc_id=f"JP{year}{serial}A",
                inquiry_selectors=(
                    "#p00_srchCondtn_txtDocNoInputNo2",
                    "#p00_srchCondtn_txtDocNoInputNo1",
                    "#p00_srchCondtn_txtDocNoInputNo3",
                ),
                kind="publication",
                fixed_url=f"{JPLATPAT_ORIGIN}/c1801/PU/JP-{year}-{serial}/11/ja",
            )

    reg_patterns = [
        r"特許\s*(?:第)?\s*(\d{5,8})",
        r"JP\s*-?\s*(\d{5,8})\s*B\d?",
        r"^\s*(\d{5,8})\s*B\d?\s*$",
        r"^\s*(\d{5,8})\s*$",
    ]
    for pat in reg_patterns:
        m = re.search(pat, work, re.IGNORECASE)
        if m:
            number = m.group(1)
            return JplatpatPatentNo(
                raw=text,
                number=number,
                display_number=f"特許{number}",
                filename_stem=f"JP{number}B",
                doc_id=f"JP{number}B",
                inquiry_selectors=(
                    "#p00_srchCondtn_txtDocNoInputNo3",
                    "#p00_srchCondtn_txtDocNoInputNo2",
                    "#p00_srchCondtn_txtDocNoInputNo1",
                ),
                kind="registration",
                fixed_url=f"{JPLATPAT_ORIGIN}/c1801/PU/JP-{number}/15/ja",
            )

    raise ValueError("現在は JP 公開番号(A、例: 特開2024-123456 / JP2024123456A) と JP 登録番号(B、例: JP7250676B2 / 特許7250676)に対応しています")


def normalize_jp_registration_number(raw: str) -> JplatpatPatentNo:
    """後方互換用。登録番号だけでなく公開番号も受け付ける。"""
    return normalize_jp_patent_number(raw)


def download_jplatpat_pdf(
    patent_id: str,
    save_dir: str | Path,
    *,
    headless: bool = False,
    timeout_ms: int = 60000,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """J-PlatPat から公報 PDF をダウンロードして結合 PDF を保存する。"""
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "success": False,
            "error": "playwright が未インストールです: pip install playwright && playwright install chromium",
        }

    target = normalize_jp_patent_number(patent_id)
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    output_path = save_dir / f"{target.filename_stem}.pdf"

    def log(message: str) -> None:
        if on_progress:
            try:
                on_progress(message)
            except Exception:
                pass

    try:
        with sync_playwright() as p:
            browser = _launch_chromium(p, headless=headless)
            context = browser.new_context(locale="ja-JP", viewport={"width": 1280, "height": 900})
            page = context.new_page()
            try:
                if target.kind == "publication":
                    log(f"固定URLを開いています: {target.display_number}")
                    result = _open_fixed_url_and_pick_result(page, target, timeout_ms=timeout_ms)
                else:
                    log("J-PlatPat 番号照会を開いています")
                    page.goto(JPLATPAT_NUMBER_INQUIRY_URL, wait_until="domcontentloaded", timeout=timeout_ms)
                    _dismiss_dialogs(page)

                    log(f"番号照会: {target.display_number}")
                    _fill_inquiry_number(page, target, timeout_ms=timeout_ms)
                    with page.expect_response(
                        lambda r: "/web/patnumber/wsp0102" in r.url and r.request.method == "POST",
                        timeout=timeout_ms,
                    ) as response_info:
                        page.click("#p00_searchBtn_btnDocInquiry", timeout=timeout_ms)
                    search_payload = response_info.value.json()
                    result = _pick_search_result(search_payload, target)

                doc_page = _open_document_page(page, result, target, timeout_ms=timeout_ms)
                page_urls = _fetch_pdf_page_urls(doc_page, result, target, timeout_ms=timeout_ms)
                if not page_urls:
                    raise RuntimeError("J-PlatPat から PDF URL を取得できませんでした")

                log(f"PDF {len(page_urls)} ページを取得しています")
                pdf_paths = _download_page_pdfs(context, page_urls, timeout_ms=timeout_ms)
                _merge_pdfs(pdf_paths, output_path)
                return {
                    "success": True,
                    "path": str(output_path),
                    "patent_id": target.raw,
                    "doc_id": target.doc_id,
                    "jplatpat_doc_number": _result_doc_number(result, target),
                    "title": result.get("INVEN_NAME") or "",
                    "num_pages": len(pdf_paths),
                    "source": "jplatpat",
                }
            finally:
                context.close()
                browser.close()
    except PlaywrightTimeoutError as e:
        return {"success": False, "error": f"J-PlatPat 操作がタイムアウトしました: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _launch_chromium(playwright, *, headless: bool):
    kwargs = {"headless": headless, "args": ["--disable-blink-features=AutomationControlled"]}
    try:
        return playwright.chromium.launch(**kwargs)
    except Exception:
        chrome = Path(os.environ.get("CHROME_PATH", ""))
        candidates = [
            chrome if str(chrome) else None,
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        ]
        for candidate in candidates:
            if candidate and candidate.exists():
                return playwright.chromium.launch(executable_path=str(candidate), **kwargs)
        return playwright.chromium.launch(channel="chrome", **kwargs)


def _dismiss_dialogs(page) -> None:
    for selector in [
        'button:has-text("閉じる")',
        'button:has-text("OK")',
        'button:has-text("同意")',
        'button[aria-label*="閉じる"]',
    ]:
        try:
            loc = page.locator(selector)
            if loc.count():
                loc.first.click(timeout=1000)
                page.wait_for_timeout(300)
        except Exception:
            pass


def _fill_inquiry_number(page, target: JplatpatPatentNo, *, timeout_ms: int) -> None:
    last_error = None
    for selector in target.inquiry_selectors:
        try:
            loc = page.locator(selector)
            if not loc.count():
                continue
            loc.first.fill(target.number, timeout=timeout_ms)
            return
        except Exception as e:
            last_error = e
            continue
    if last_error:
        raise last_error
    raise RuntimeError("J-PlatPat 番号照会の入力欄が見つかりませんでした")


def _open_fixed_url_and_pick_result(page, target: JplatpatPatentNo, *, timeout_ms: int) -> dict:
    if not target.fixed_url:
        raise RuntimeError("固定URLを生成できませんでした")
    with page.expect_response(
        lambda r: "/web/patnumber/wsp0102" in r.url and r.request.method == "POST",
        timeout=timeout_ms,
    ) as response_info:
        page.goto(target.fixed_url, wait_until="load", timeout=timeout_ms)
    search_payload = response_info.value.json()
    _dismiss_dialogs(page)
    return _pick_search_result(search_payload, target)


def _result_doc_number(result: dict, target: JplatpatPatentNo) -> str:
    for key in (
        "PUBLI_NUM_DISP",
        "PUBL_NUM_DISP",
        "PUB_NUM_DISP",
        "OPN_NUM_DISP",
        "REG_NUM_DISP",
        "APP_NUM_DISP",
        "DOC_NUM_DISP",
    ):
        value = result.get(key)
        if value:
            return str(value)
    return target.display_number


def _pick_search_result(payload: dict, target: JplatpatPatentNo) -> dict:
    results = payload.get("SEARCH_RSLT_LIST") or []
    if not results:
        raise RuntimeError("番号照会で該当文献が見つかりませんでした")
    for item in results:
        haystack = " ".join(str(v) for v in item.values() if v is not None)
        compact = re.sub(r"[\s\-]", "", haystack)
        target_compact = re.sub(r"[\s\-]", "", target.number)
        if target.number in haystack or target_compact in compact:
            if not item.get("ISN"):
                raise RuntimeError("J-PlatPat 応答に ISN がありません")
            return item
    item = results[0]
    if not item.get("ISN"):
        raise RuntimeError("J-PlatPat 応答に ISN がありません")
    return item


def _open_document_page(page, result: dict, target: JplatpatPatentNo, *, timeout_ms: int):
    """検索結果リンクを開き、公報系 API を呼べるページを返す。失敗時は元ページで続行する。"""
    doc_num = _result_doc_number(result, target)
    try:
        try:
            page.check("#p0107_docDispScreenFormal_radio1-input", timeout=3000)
        except Exception:
            pass
        link = page.locator(f"text={doc_num}").first
        with page.expect_popup(timeout=timeout_ms) as popup_info:
            link.click(timeout=timeout_ms)
        popup = popup_info.value
        popup.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        return popup
    except Exception:
        return page


def _fetch_pdf_page_urls(page, result: dict, target: JplatpatPatentNo, *, timeout_ms: int) -> list[str]:
    first = _request_pdf_page_url(page, result, target, 1, timeout_ms=timeout_ms)
    total = _to_int(first.get("DOCU_ALL_PAGE_CNT")) or 1
    urls = [first["DOCU_URL"]]
    for page_num in range(2, total + 1):
        payload = _request_pdf_page_url(page, result, target, page_num, timeout_ms=timeout_ms)
        if payload.get("DOCU_URL"):
            urls.append(payload["DOCU_URL"])
    return urls


def _request_pdf_page_url(page, result: dict, target: JplatpatPatentNo, page_num: int, *, timeout_ms: int) -> dict:
    doc_num = _result_doc_number(result, target)
    body = {
        "DOCU_INFO_PART": {
            "DOCU_TYPE_FLG": "INTNL_PAGE_ENTIRE_PDF",
            "OTID": None,
            "ISN": str(result.get("ISN") or ""),
            "DOCU_ALL_PAGE_CNT": "",
            "DEMAND_PAGE_PDF_INFO_PART": {
                "DEMAND_DOCU_NUM": doc_num,
                "DEMAND_PAGE_NUM": str(page_num),
                "DEMAND_DOCU_PUBL_DATE": None,
                "DEMAND_LINK_CD": "BBL",
            },
            "MAIN_DOCU_PDF_INFO_PART": {
                "MAIN_DOCU_NUM": "",
                "MAIN_DOCU_START_PAGE": "",
                "MAIN_DOCU_ALL_PAGE_CNT": "",
                "EXAM_PAGE_CNT": "",
                "MAIN_DOCU_PUBL_DATE": None,
            },
            "FOLLOW_DOCU_PDF_INFO_PART": [
                {
                    "FOLLOW_DOCU_NUM": "",
                    "FOLLOW_DOCU_EVERY_ALL_PAGE_CNT": "",
                    "FOLLOW_DOCU_EVERY_START_PAGE": "",
                    "FOLLOW_DOCU_PUBL_DATE": None,
                }
            ],
            "LINK_INFO_PART": [{"LINK_CD": "", "LINK_PAGE_NUM": ""}],
        }
    }
    payload = page.evaluate(
        """async ({body}) => {
            const res = await fetch('/app/comdocu/wsp0701', {
                method: 'POST',
                headers: {'content-type': 'application/json;charset=UTF-8'},
                body: JSON.stringify(body),
                credentials: 'include'
            });
            if (!res.ok) throw new Error('wsp0701 status=' + res.status);
            return await res.json();
        }""",
        {"body": body},
    )
    info = payload.get("DOCU_INFO_PART") or {}
    if not info.get("DOCU_URL"):
        raise RuntimeError(f"PDF URL を取得できませんでした(page={page_num})")
    return info


def _download_page_pdfs(context, urls: Iterable[str], *, timeout_ms: int) -> list[Path]:
    temp_dir = Path(tempfile.mkdtemp(prefix="jplatpat_pdf_"))
    paths: list[Path] = []
    for i, url in enumerate(urls, start=1):
        full_url = url if str(url).startswith("http") else f"{JPLATPAT_ORIGIN}{url}"
        resp = context.request.get(full_url, headers=_HEADERS, timeout=timeout_ms)
        if not resp.ok:
            raise RuntimeError(f"PDF ページ取得失敗(page={i}, status={resp.status})")
        path = temp_dir / f"page_{i:04d}.pdf"
        path.write_bytes(resp.body())
        paths.append(path)
    return paths


def _merge_pdfs(pdf_paths: Iterable[Path], output_path: Path) -> None:
    pdf_paths = list(pdf_paths)
    merged = fitz.open()
    try:
        for pdf_path in pdf_paths:
            src = fitz.open(str(pdf_path))
            try:
                merged.insert_pdf(src)
            finally:
                src.close()
        merged.save(str(output_path))
    finally:
        merged.close()
        for pdf_path in pdf_paths[:1]:
            try:
                shutil.rmtree(str(pdf_path.parent), ignore_errors=True)
            except OSError:
                pass


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
