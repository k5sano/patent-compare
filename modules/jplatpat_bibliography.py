#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""J-PlatPat の内部 API から本願書誌情報を取得する。

番号照会 `wsp0102` で対象文献の ISN 等を解決し、公報詳細の
`wsp1201` が返す書誌欄 TEXT_DATA を解析する。ブラウザは使わず、
`requests.Session` で直接呼び出す。
"""

from __future__ import annotations

import datetime as _dt
import html
import re
from dataclasses import asdict
from typing import Any

import requests

from modules.jplatpat_client import parse_classifications_from_raw
from modules.jplatpat_pdf_downloader import (
    JPLATPAT_ORIGIN,
    normalize_jp_patent_number,
)


JPLATPAT_NUMBER_INQUIRY_URL = f"{JPLATPAT_ORIGIN}/p0000"
WSP0102_URL = f"{JPLATPAT_ORIGIN}/web/patnumber/wsp0102"
WSP1101_URL = f"{JPLATPAT_ORIGIN}/app/comdocu/wsp1101"
WSP1201_URL = f"{JPLATPAT_ORIGIN}/app/comdocu/wsp1201"
AUTH_URL = f"{JPLATPAT_ORIGIN}/app/auth/wsc0401"

DEFAULT_TIMEOUT = 30

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": JPLATPAT_ORIGIN,
    "Content-Type": "application/json;charset=UTF-8",
}

_FW2HW = str.maketrans({
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
    "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
    "Ａ": "A", "Ｂ": "B", "Ｃ": "C", "Ｄ": "D", "Ｅ": "E", "Ｆ": "F",
    "Ｇ": "G", "Ｈ": "H", "Ｉ": "I", "Ｊ": "J", "Ｋ": "K", "Ｌ": "L",
    "Ｍ": "M", "Ｎ": "N", "Ｏ": "O", "Ｐ": "P", "Ｑ": "Q", "Ｒ": "R",
    "Ｓ": "S", "Ｔ": "T", "Ｕ": "U", "Ｖ": "V", "Ｗ": "W", "Ｘ": "X",
    "Ｙ": "Y", "Ｚ": "Z", "／": "/", "－": "-", "　": " ",
})


class JplatpatBibliographyError(RuntimeError):
    """J-PlatPat 書誌情報取得に失敗した。"""


def fetch_jplatpat_bibliography(
    patent_id: str,
    *,
    session: requests.Session | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """特開/特許番号から書誌情報 dict を返す。

    Returns:
        {
          "patent_number": "特開2024-108988",
          "application_number": "特願2023-022940",
          "application_date": "2023-01-31",
          "priority_date": "",
          "applicants": [...],
          "inventors": [...],
          "ipc": [...], "fi": [...], "fterm": [...],
          "theme_code": [...], "theme_codes": [...],
          ...
        }
    """
    target = normalize_jp_patent_number(patent_id)
    sess = session or requests.Session()
    headers = dict(_HEADERS)
    headers["Referer"] = target.fixed_url or JPLATPAT_NUMBER_INQUIRY_URL

    # Cookie / 初期セッション確立。失敗しても後段で判定する。
    try:
        sess.get(headers["Referer"], headers={"User-Agent": _HEADERS["User-Agent"]}, timeout=timeout)
    except requests.RequestException:
        pass
    try:
        sess.post(AUTH_URL, headers=headers, json={}, timeout=timeout)
    except requests.RequestException:
        pass

    search_payload = _post_json(sess, WSP0102_URL, _build_wsp0102_body(target), headers, timeout)
    result = _pick_search_result(search_payload, target.number)
    doc_key = _document_key(result, target.display_number, target.kind)
    isn = str(result.get("ISN") or "")
    if not isn:
        raise JplatpatBibliographyError("J-PlatPat 応答に ISN がありません")

    detail_headers = dict(headers)
    detail_headers["Referer"] = f"{JPLATPAT_ORIGIN}/p0200"
    _post_json(
        sess,
        WSP1101_URL,
        {"DOCU_KEY": doc_key, "JPN_TEXT_ENG_TEXT_FLG": 0, "ISN": isn, "LANG": "ja", "OTID": None},
        detail_headers,
        timeout,
    )
    biblio_payload = _post_json(
        sess,
        WSP1201_URL,
        {
            "DOCU_KEY": doc_key,
            "ACQUISITION_MODE": "0",
            "SPC_NUM": 1,
            "TOTAL_PAGE_CNT": 0,
            "USE_OF_LANG": "ja",
            "WABUN_EIBUN": "0",
            "BLOCK_NUM": 0,
            "ISN": isn,
            "OTID": None,
        },
        detail_headers,
        timeout,
    )
    text_data = ((biblio_payload.get("DOCU_DATA") or {}).get("TEXT_DATA") or "")
    if not text_data:
        raise JplatpatBibliographyError("J-PlatPat 書誌欄 TEXT_DATA が空です")

    parsed = parse_bibliography_text(text_data)
    cls = parse_classifications_from_raw(parsed.get("raw_text") or "")
    applicants = parsed.get("applicants") or _as_list(result.get("APPN_RIGHT_HOLDER"))

    out: dict[str, Any] = {
        "source": "jplatpat",
        "fetched_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "input": patent_id,
        "target": asdict(target),
        "isn": isn,
        "patent_number": parsed.get("patent_number") or _display_number(result, target.display_number, target.kind),
        "patent_title": parsed.get("patent_title") or result.get("INVEN_NAME") or "",
        "application_number": parsed.get("application_number") or result.get("APP_NUM_DISP") or "",
        "application_date": parsed.get("application_date") or _normalize_date(result.get("APP_DATE")),
        "publication_date": parsed.get("publication_date") or _normalize_date(result.get("KNOWN_DATE")),
        "priority_date": parsed.get("priority_date") or "",
        "priority_dates": parsed.get("priority_dates") or [],
        "applicants": applicants,
        "applicant": applicants[0] if applicants else "",
        "inventors": parsed.get("inventors") or [],
        "ipc": cls.get("ipc") or [],
        "fi": cls.get("fi") or _as_list(result.get("FI")),
        "fterm": cls.get("fterm") or [],
        "theme_code": cls.get("theme_codes") or [],
        "theme_codes": cls.get("theme_codes") or [],
        "raw_text": parsed.get("raw_text") or "",
    }
    return out


def parse_bibliography_text(text_data: str) -> dict[str, Any]:
    """`wsp1201.DOCU_DATA.TEXT_DATA` から書誌事項を抽出する。"""
    text = _clean_text_data(text_data)
    patent_number = _normalize_jp_doc_number(_strip_paren_suffix(
        _extract_value_after_label(text, "公開番号") or _extract_value_after_label(text, "特許番号")
    ))
    title = _extract_value_after_label(text, "発明の名称")
    application_number = _normalize_jp_doc_number(
        _strip_paren_suffix(_extract_value_after_label(text, "出願番号"))
    )
    application_date = _extract_date_after_label(text, "出願日")
    publication_date = _extract_date_after_label(text, "公開日") or _extract_date_after_label(text, "公表日")
    priority_dates = _extract_dates_after_label(text, "優先日")
    applicants = _extract_named_blocks(text, "出願人", ("氏名又は名称", "名称"))
    inventors = _extract_named_blocks(text, "発明者", ("氏名", "氏名又は名称"))

    return {
        "patent_number": patent_number,
        "patent_title": title,
        "application_number": application_number,
        "application_date": application_date,
        "publication_date": publication_date,
        "priority_date": priority_dates[0] if priority_dates else "",
        "priority_dates": priority_dates,
        "applicants": applicants,
        "applicant": applicants[0] if applicants else "",
        "inventors": inventors,
        "raw_text": text,
    }


def _post_json(session: requests.Session, url: str, body: dict, headers: dict, timeout: int) -> dict:
    try:
        resp = session.post(url, headers=headers, json=body, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as e:
        raise JplatpatBibliographyError(f"{url} request failed: {e}") from e
    except ValueError as e:
        raise JplatpatBibliographyError(f"{url} JSON decode failed") from e
    return payload


def _build_wsp0102_body(target) -> dict:
    num_type = (
        "PUBLI_NUM_PUB_NUM_A"
        if target.kind == "publication"
        else "PATENT_NUM_B_PATENT_INVENT_DESCRIPT_NUM_C"
    )
    return {
        "DISP_ID": "P0000",
        "SEARCH_TYPE": "1",
        "NUM_INQRY_DISP": {
            "RESEARCH_FLG": 1,
            "INPUT_TYPE": {"NUM_INPUT": 1, "NUM_RNG_INPUT": 0, "DOCDB_FORMAL_INPUT": 0},
            "NUM_INFO": [
                {
                    "PUBL_DATE_PUBL_INSTT": "JPN_JP",
                    "NUM_TYPE": num_type,
                    "NUM": target.number,
                }
            ],
            "NUM_RNG_INFO": {"PUBL_DATE_PUBL_INSTT": None, "NUM_TYPE": None, "START": None, "END": None},
            "DOCDB_FORMAL_INFO": {"APP_NUM": [], "DOCU_NUM": []},
            "SEARCH_OPTN": {
                "FILTER_INFO": {
                    "FILTER_MAXIMUM_CNT_KNOWN_Y_BY": 10,
                    "FILTER_MAXIMUM_CNT_PER_FI": 10,
                    "ITEM": None,
                    "COND": None,
                    "COMPAR_COND_SIGN": None,
                }
            },
        },
        "SEARCH_RSLT_MAX_CNT": 3000,
        "SORT_INFO": {"ITEM": None, "SORT_ORDER": 0},
    }


def _pick_search_result(payload: dict, normalized_number: str) -> dict:
    results = payload.get("SEARCH_RSLT_LIST") or []
    if not results:
        raise JplatpatBibliographyError("番号照会で該当文献が見つかりませんでした")
    compact_target = re.sub(r"[\s\-]", "", normalized_number)
    for item in results:
        haystack = " ".join(str(v) for v in item.values() if v is not None)
        compact = re.sub(r"[\s\-]", "", haystack)
        if normalized_number in haystack or compact_target in compact:
            return item
    return results[0]


def _document_key(result: dict, fallback: str, kind: str = "") -> str:
    keys = (
        ("REG_NUM_DISP", "PUBLI_NUM_DISP", "EXAM_PUB_NUM_DISP", "APP_NUM_DISP", "DOC_NUM_DISP")
        if kind == "registration"
        else ("PUBLI_NUM_DISP", "REG_NUM_DISP", "EXAM_PUB_NUM_DISP", "APP_NUM_DISP", "DOC_NUM_DISP")
    )
    for key in keys:
        value = result.get(key)
        if value:
            return str(value)
    return fallback


def _display_number(result: dict, fallback: str, kind: str = "") -> str:
    keys = (
        ("REG_NUM_DISP", "PUBLI_NUM_DISP", "EXAM_PUB_REG_PRIORITY_DOCU_NUM_DISP")
        if kind == "registration"
        else ("PUBLI_NUM_DISP", "REG_NUM_DISP", "EXAM_PUB_REG_PRIORITY_DOCU_NUM_DISP")
    )
    for key in keys:
        value = result.get(key)
        if value:
            return str(value)
    return fallback


def _clean_text_data(text_data: str) -> str:
    s = html.unescape(text_data or "")
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</?(?:SDO|DP|RTI|TXF|B)\b[^>]*>", "", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("\r", "\n")
    s = re.sub(r"[ \t\xa0　]+", " ", s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()


def _extract_value_after_label(text: str, label: str) -> str:
    m = re.search(rf"【{re.escape(label)}】\s*([^\n【]+)", text)
    return (m.group(1).strip() if m else "")


def _strip_paren_suffix(value: str) -> str:
    return re.sub(r"\s*[\(（][^)）]+[\)）]\s*$", "", value or "").strip()


def _normalize_jp_doc_number(value: str) -> str:
    s = value or ""
    m = re.match(r"^(特願|特開)\s*(\d{4})\s*[-－]\s*(\d{1,6})$", s)
    if m:
        return f"{m.group(1)}{m.group(2)}-{m.group(3).zfill(6)}"
    return s


def _extract_date_after_label(text: str, label: str) -> str:
    m = re.search(rf"【{re.escape(label)}】\s*([^\n【]+)", text)
    return _normalize_date(m.group(1) if m else "")


def _extract_dates_after_label(text: str, label: str) -> list[str]:
    values = []
    for m in re.finditer(rf"【{re.escape(label)}】\s*([^\n【]+)", text):
        d = _normalize_date(m.group(1))
        if d and d not in values:
            values.append(d)
    return values


def _normalize_date(value: Any) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    m = re.search(r"(\d{4})[./年/-](\d{1,2})[./月/-](\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return s


def _extract_named_blocks(text: str, block_label: str, name_labels: tuple[str, ...]) -> list[str]:
    names: list[str] = []
    block_re = re.compile(
        rf"\(\d{{2}}\)【{re.escape(block_label)}】(?P<body>.*?)(?=\n\(\d{{2}}\)【|\n【テーマコード|\n【Fターム|\n【Ｆターム|\Z)",
        re.DOTALL,
    )
    for block in block_re.finditer(text):
        body = block.group("body")
        name = ""
        for label in name_labels:
            name = _extract_value_after_label(body, label)
            if name:
                break
        if not name:
            lines = [
                ln.strip() for ln in body.splitlines()
                if ln.strip()
                and not re.fullmatch(r"【[^】]+】.*", ln.strip())
                and not re.fullmatch(r"\d{6,}", ln.strip())
            ]
            if lines:
                name = lines[0]
        if name and name not in names:
            names.append(name)
    return names


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    s = str(value).strip()
    return [s] if s else []
