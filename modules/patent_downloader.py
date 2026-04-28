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
    """特許番号から J-PlatPat 固定 URL を生成。

    形式 (I. Sawaki 「J-PlatPat における公報固定URLについて」 2024/05/01 版):
        https://www.j-platpat.inpit.go.jp/c1801/PU/{β}/{γ}/ja
      β = 国記号-公報番号 (JP-yyyy-nnnnnn / JP-nnnnnnn / WO-A-... / US-A-... / US-B-... 等)
      γ = 公報種別コード (10/11/12/15/19/20/21/22/23/25/30/35/40/45/50)

    対応:
      特開yyyy-nnnnnn / 特表yyyy-nnnnnn → JP-yyyy-nnnnnn/11
      特願yyyy-nnnnnn                  → JP-yyyy-nnnnnn/10
      再表yyyy-nnnnnn                  → JP-yyyy-nnnnnn/19
      特許nnnnnnn / 特許第n号 / JPnB    → JP-nnnnnnn/15
      実登nnnnnnn / JPnUny            → JP-nnnnnnn/25
      JPyyyynnnnnnnA / JP-yyyy-nnnnnnA → JP-yyyy-nnnnnn/11
      WOyyyy/nnnnnn                   → WO-A-yyyy-nnnnnn/50
      USyyyynnnnnnnA / US yyyy/nnnnnnn → US-A-yyyy-nnnnnnn/50
      USnnnnnnnB / USnnnnnnnnB        → US-B-nnnnnnn/50
      EPnnnnnnnA(1) / EP-A-...        → EP-A-nnnnnnn/50
      EPnnnnnnnB(1) / EP-B-...        → EP-B-nnnnnnn/50
      CN…A / CN…B / CN…C / CN…U / CN…Y → CN-{X}-nnn/50
      KR…A / KR…B                     → KR-{X}-nnn/50

    Returns:
        str: J-PlatPat 固定 URL、または生成不能なら空文字列
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

    # 特表yyyy-nnnnnn (国際公開の和文公表)
    m = re.match(r'特表\s*(\d{4})\s*[-ー]\s*(\d+)', pid)
    if m:
        return f"{BASE}/JP-{m.group(1)}-{m.group(2).zfill(6)}/11/ja"

    # 再表yyyy-nnnnnn / 再公表yyyy-nnnnnn
    m = re.match(r'再(?:公)?表\s*(\d{4})\s*[-ー]\s*(\d+)', pid)
    if m:
        return f"{BASE}/JP-{m.group(1)}-{m.group(2).zfill(6)}/19/ja"

    # 実登nnnnnnn / 登録実用新案nnnnnnn
    m = re.match(r'(?:実登|登録実用新案|実用新案登録)(?:第)?\s*(\d+)(?:号)?', pid)
    if m:
        return f"{BASE}/JP-{m.group(1)}/25/ja"

    # 実願yyyy-nnnnnn (実用新案出願)
    m = re.match(r'実願\s*(\d{4})\s*[-ー]\s*(\d+)', pid)
    if m:
        return f"{BASE}/JP-{m.group(1)}-{m.group(2).zfill(6)}/20/ja"

    # 実開yyyy-nnnnnn (実用新案公開)
    m = re.match(r'実開\s*(\d{4})\s*[-ー]\s*(\d+)', pid)
    if m:
        return f"{BASE}/JP-{m.group(1)}-{m.group(2).zfill(6)}/21/ja"

    # 特許nnnnnnn / 特許第n号 (登録番号)
    m = re.match(r'特許(?:第)?\s*(\d+)(?:号)?', pid)
    if m:
        return f"{BASE}/JP-{m.group(1)}/15/ja"

    # --- JP 番号（英字表記） ---
    # JP-yyyy-nnnnnnA / JPyyyynnnnnnnA (公開公報)
    # 注: 出願年 4 桁 + 連番 (一部の表記揺れに対応するため数字数を緩和)
    m = re.match(r'JP[-\s]?(\d{4})[-\s]?(\d{3,7})\s*A\d?\s*$', pid, re.IGNORECASE)
    if m:
        return f"{BASE}/JP-{m.group(1)}-{m.group(2).zfill(6)}/11/ja"

    # JPnnnnnnnB / JPnnnnnnnB2 (登録)
    m = re.match(r'JP[-\s]?(\d{5,8})\s*B\d?\s*$', pid, re.IGNORECASE)
    if m:
        return f"{BASE}/JP-{m.group(1)}/15/ja"

    # JPnnnnnnnU / JPnnnnnnnY (実用新案登録)
    m = re.match(r'JP[-\s]?(\d{5,8})\s*[UY]\d?\s*$', pid, re.IGNORECASE)
    if m:
        return f"{BASE}/JP-{m.group(1)}/25/ja"

    # --- WO 国際公開 ---
    # WOyyyy/nnnnnn / WO-yyyy-nnnnnn / WO yyyy nnnnnn
    m = re.match(r'WO[-\s/]*(\d{4})[-\s/]*(\d{1,6})\s*A?\d?\s*$', pid, re.IGNORECASE)
    if m:
        return f"{BASE}/WO-A-{m.group(1)}-{m.group(2).zfill(6)}/50/ja"

    # --- US ---
    # 公開: USyyyynnnnnnn / US-yyyy-nnnnnnn / US yyyy/nnnnnnn (末尾 A1/A2/A 任意)
    # 例: US20130040869, US2005/0048102A1
    m = re.match(r'US[-\s]?(\d{4})[-\s/]*(\d{4,7})\s*A?\d?\s*$', pid, re.IGNORECASE)
    if m:
        return f"{BASE}/US-A-{m.group(1)}-{m.group(2).zfill(7)}/50/ja"

    # 登録: USnnnnnnn(B) / USnnnnnnnnB / US 6,316,011 B1 (8 or 9 桁、カンマ可)
    m = re.match(r'US[-\s]?([\d,]+)\s*B\d?\s*$', pid, re.IGNORECASE)
    if m:
        num = m.group(1).replace(",", "")
        if len(num) >= 7:
            return f"{BASE}/US-B-{num}/50/ja"

    # --- EP ---
    # 公開: EPnnnnnnnA(1/2)
    m = re.match(r'EP[-\s]?(\d{6,9})\s*A\d?\s*$', pid, re.IGNORECASE)
    if m:
        return f"{BASE}/EP-A-{m.group(1)}/50/ja"
    # 登録: EPnnnnnnnB(1/2)
    m = re.match(r'EP[-\s]?(\d{6,9})\s*B\d?\s*$', pid, re.IGNORECASE)
    if m:
        return f"{BASE}/EP-B-{m.group(1)}/50/ja"
    # 種別不明 → A と仮置き
    m = re.match(r'EP[-\s]?(\d{6,9})\s*$', pid, re.IGNORECASE)
    if m:
        return f"{BASE}/EP-A-{m.group(1)}/50/ja"

    # --- CN ---
    # 公開 CN…A / 登録(発明) CN…B / 登録(別系統) CN…C / 実案 CN…U / CN…Y
    for suffix, kind in (("A", "A"), ("B", "B"), ("C", "C"), ("U", "U"), ("Y", "Y")):
        m = re.match(rf'CN[-\s]?(\d{{6,12}})\s*{suffix}\d?\s*$', pid, re.IGNORECASE)
        if m:
            return f"{BASE}/CN-{kind}-{m.group(1)}/50/ja"
    # 種別不明
    m = re.match(r'CN[-\s]?(\d{6,12})\s*$', pid, re.IGNORECASE)
    if m:
        return f"{BASE}/CN-A-{m.group(1)}/50/ja"

    # --- KR ---
    # 公開 KR…A / 登録 KR…B
    m = re.match(r'KR[-\s]?(\d{6,13})\s*A\d?\s*$', pid, re.IGNORECASE)
    if m:
        return f"{BASE}/KR-A-{m.group(1)}/50/ja"
    m = re.match(r'KR[-\s]?(\d{6,13})\s*B\d?\s*$', pid, re.IGNORECASE)
    if m:
        return f"{BASE}/KR-B-{m.group(1)}/50/ja"
    # 種別不明
    m = re.match(r'KR[-\s]?(\d{6,13})\s*$', pid, re.IGNORECASE)
    if m:
        return f"{BASE}/KR-A-{m.group(1)}/50/ja"

    return ""

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
