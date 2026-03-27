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
