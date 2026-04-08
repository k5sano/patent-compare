#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
特許PDFダウンロードモジュール

Google Patents ページからPDFリンクを抽出してダウンロード。
失敗時は Google Patents / J-PlatPat のリンクを返して手動DLを案内。
"""

import re
from pathlib import Path

import requests

# Google Patents ページ取得時のヘッダー
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}


def build_google_patents_url(patent_id):
    """特許番号からGoogle PatentsページURLを構築"""
    cleaned = re.sub(r'[\s\-/]', '', patent_id)
    if cleaned.upper().startswith("JP"):
        return f"https://patents.google.com/patent/{cleaned}/ja"
    return f"https://patents.google.com/patent/{cleaned}/en"


def build_jplatpat_url(patent_id):
    """特許番号からJ-PlatPat固定URLを生成。

    対応形式:
      特開2023-123456 / JP2023123456A → 公開(11)
      特願2021-012345                 → 出願(10)
      特許6789012 / JP6789012B2       → 登録(15)
      再表2018-012345                 → 再表(19)
      WO2022/030405                   → 外国(50)
      US20070292359A1 / EP...         → 外国(50)

    Returns:
        str: J-PlatPat固定URL、または生成不能なら空文字列
    """
    pid = patent_id.strip()
    if not pid:
        return ""

    BASE = "https://www.j-platpat.inpit.go.jp/c1801/PU"

    # --- 日本語表記 ---
    # 特開yyyy-nnnnnn
    m = re.match(r'特開\s*(\d{4})\s*[-ー]\s*(\d+)', pid)
    if m:
        return f"{BASE}/JP-{m.group(1)}-{m.group(2).zfill(6)}/11/ja"

    # 特願yyyy-nnnnnn
    m = re.match(r'特願\s*(\d{4})\s*[-ー]\s*(\d+)', pid)
    if m:
        return f"{BASE}/JP-{m.group(1)}-{m.group(2).zfill(6)}/10/ja"

    # 特表yyyy-nnnnnn
    m = re.match(r'特表\s*(\d{4})\s*[-ー]\s*(\d+)', pid)
    if m:
        return f"{BASE}/JP-{m.group(1)}-{m.group(2).zfill(6)}/11/ja"

    # 再表yyyy-nnnnnn / 再公表yyyy-nnnnnn
    m = re.match(r'再(?:公)?表\s*(\d{4})\s*[-ー]\s*(\d+)', pid)
    if m:
        return f"{BASE}/JP-{m.group(1)}-{m.group(2).zfill(6)}/19/ja"

    # 特許nnnnnnn（登録番号）
    m = re.match(r'特許(?:第)?\s*(\d+)(?:号)?', pid)
    if m:
        return f"{BASE}/JP-{m.group(1)}/15/ja"

    # --- JP番号（英字表記） ---
    # JP2023-123456A / JP2023123456A（公開）
    m = re.match(r'JP\s*(\d{4})\s*[-]?\s*(\d{3,6})\s*A', pid, re.IGNORECASE)
    if m:
        return f"{BASE}/JP-{m.group(1)}-{m.group(2).zfill(6)}/11/ja"

    # JPnnnnnnnB / JPnnnnnnnB2（登録）
    m = re.match(r'JP\s*(\d{5,8})\s*B\d?', pid, re.IGNORECASE)
    if m:
        return f"{BASE}/JP-{m.group(1)}/15/ja"

    # --- WO ---
    m = re.match(r'WO\s*(\d{4})\s*[/]?\s*(\d+)', pid, re.IGNORECASE)
    if m:
        return f"{BASE}/WO-A-{m.group(1)}-{m.group(2).zfill(6)}/50/ja"

    # --- US ---
    # US20070292359A1 / US2005/0048102A1
    m = re.match(r'US\s*(\d{4})\s*[/]?\s*(\d+)\s*A\d?', pid, re.IGNORECASE)
    if m:
        num = m.group(1) + m.group(2)
        return f"{BASE}/US-{num}/50/ja"

    # US6316011B1（登録）
    m = re.match(r'US\s*([\d,]+)\s*B\d?', pid, re.IGNORECASE)
    if m:
        num = m.group(1).replace(",", "")
        return f"{BASE}/US-{num}/50/ja"

    # --- EP ---
    m = re.match(r'EP\s*(\d+)', pid, re.IGNORECASE)
    if m:
        return f"{BASE}/EP-{m.group(1)}/50/ja"

    # --- CN ---
    m = re.match(r'CN\s*(\d+)', pid, re.IGNORECASE)
    if m:
        return f"{BASE}/CN-{m.group(1)}/50/ja"

    # --- KR ---
    m = re.match(r'KR\s*(\d+)', pid, re.IGNORECASE)
    if m:
        return f"{BASE}/KR-{m.group(1)}/50/ja"

    return ""


def _extract_pdf_url_from_html(html):
    """Google PatentsページのHTMLからPDF直リンクを抽出

    ページ内に以下のようなリンクがある:
    https://patentimages.storage.googleapis.com/XX/XX/XX/XXXXXXXX/PATENT.pdf
    """
    # パターン: patentimages.storage.googleapis.com の PDF URL
    pattern = re.compile(
        r'https?://patentimages\.storage\.googleapis\.com/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]+/[^\s"\'<>]+\.pdf',
        re.IGNORECASE
    )
    matches = pattern.findall(html)
    if matches:
        return matches[0]
    return None


def download_patent_pdf(patent_id, save_dir, timeout=30):
    """特許PDFをダウンロード

    1. Google Patents ページを取得
    2. HTMLからPDF直リンク (patentimages.storage.googleapis.com/...) を抽出
    3. PDFをダウンロード

    Parameters:
        patent_id: 特許番号 (例: "US5286475", "JP2020082440A")
        save_dir: 保存先ディレクトリ (Path or str)
        timeout: タイムアウト秒数

    Returns:
        dict:
            success: bool
            path: ファイルパス (成功時)
            error: エラーメッセージ (失敗時)
            google_patents_url: Google PatentsのURL (常に含む)
    """
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    # ファイル名用にサニタイズ
    safe_name = re.sub(r'[^\w\-]', '', patent_id)
    if not safe_name:
        safe_name = "unknown"

    google_url = build_google_patents_url(patent_id)

    # Step 1: Google Patents ページを取得してPDF URLを抽出
    try:
        page_resp = requests.get(google_url, headers=_HEADERS, timeout=timeout,
                                 allow_redirects=True)

        if page_resp.status_code != 200:
            return {
                "success": False,
                "error": f"Google Patentsページ取得失敗 (status={page_resp.status_code})",
                "google_patents_url": google_url,
            }

        pdf_url = _extract_pdf_url_from_html(page_resp.text)

        if not pdf_url:
            return {
                "success": False,
                "error": "Google PatentsページからPDFリンクを検出できませんでした",
                "google_patents_url": google_url,
            }

    except requests.Timeout:
        return {
            "success": False,
            "error": "Google Patentsページ取得がタイムアウトしました",
            "google_patents_url": google_url,
        }
    except requests.RequestException as e:
        return {
            "success": False,
            "error": f"Google Patentsページ取得エラー: {e}",
            "google_patents_url": google_url,
        }

    # Step 2: PDFをダウンロード
    try:
        pdf_resp = requests.get(pdf_url, headers=_HEADERS, timeout=timeout,
                                allow_redirects=True)

        if (pdf_resp.status_code == 200 and
                'pdf' in pdf_resp.headers.get('content-type', '').lower()):
            path = save_dir / f"{safe_name}.pdf"
            path.write_bytes(pdf_resp.content)
            return {
                "success": True,
                "path": str(path),
                "google_patents_url": google_url,
            }

        return {
            "success": False,
            "error": f"PDFダウンロード失敗 (status={pdf_resp.status_code})",
            "google_patents_url": google_url,
        }

    except requests.Timeout:
        return {
            "success": False,
            "error": "PDFダウンロードがタイムアウトしました",
            "google_patents_url": google_url,
        }
    except requests.RequestException as e:
        return {
            "success": False,
            "error": f"PDFダウンロードエラー: {e}",
            "google_patents_url": google_url,
        }
