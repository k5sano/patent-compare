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

from modules import google_patents_throttle

# Google Patents ページ取得時のヘッダー
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}


_ERA_TO_LETTER = {"明": "M", "大": "T", "昭": "S", "平": "H", "令": "R"}


def _gp(stub, lang):
    return f"https://patents.google.com/patent/{stub}/{lang}"


def build_google_patents_url_candidates(patent_id):
    """特許番号から Google Patents URL の候補リストを優先順に返す。

    日本語表記 (特開/特表/特許/再表/実登 等) を ASCII Kind-code 付き形式に変換してから
    候補 URL を組み立てる。複数の表記揺れを試行できるようリストで返す。

    例:
        特開2020-169128       → ['.../JP2020169128A/ja']
        特開平5-12345        → ['.../JPH0512345A/ja']
        特開昭60-12345       → ['.../JPS6012345A/ja']
        特表2020-500001      → ['.../JP2020500001A/ja']
        特許6789012          → ['.../JP6789012B2/ja', '.../JP6789012B1/ja']
        再表2012-029514      → ['.../WO2012029514A1/en', '.../JPWO2012029514A1/ja']
        WO2022/030405        → ['.../WO2022030405A1/en']
        JP2020169128A        → そのまま
        US20130040869A1      → そのまま (en)
    """
    pid = (patent_id or "").strip()
    if not pid:
        return []
    cands = []

    # --- 日本語表記 (西暦) 特開/特表 ---
    m = re.match(r"特(?:開|表)\s*(\d{4})\s*[-ー－]\s*(\d+)\s*$", pid)
    if m:
        y, n = m.group(1), m.group(2)
        # 主要形 (no zero-pad), 念のため 6 桁 zfill 版も
        cands.append(_gp(f"JP{y}{n}A", "ja"))
        if len(n) < 6:
            cands.append(_gp(f"JP{y}{n.zfill(6)}A", "ja"))
        return cands

    # --- 日本語表記 (元号) 特開昭/平/令... ---
    m = re.match(r"特(?:開|表)\s*([明大昭平令])\s*(\d{1,2})\s*[-ー－]\s*(\d+)\s*$", pid)
    if m:
        era = _ERA_TO_LETTER[m.group(1)]
        y = m.group(2).zfill(2)
        n = m.group(3)
        cands.append(_gp(f"JP{era}{y}{n}A", "ja"))
        if len(n) < 6:
            cands.append(_gp(f"JP{era}{y}{n.zfill(6)}A", "ja"))
        return cands

    # --- 特許 (登録番号) ---
    m = re.match(r"特許\s*(?:第)?\s*(\d+)\s*(?:号)?\s*$", pid)
    if m:
        n = m.group(1)
        cands.append(_gp(f"JP{n}B2", "ja"))
        cands.append(_gp(f"JP{n}B1", "ja"))
        return cands

    # --- 再表 / 再公表 (国際公開の和訳系) ---
    m = re.match(r"再(?:公)?表\s*(\d{4})\s*[-ー－/／]\s*(\d+)\s*$", pid)
    if m:
        y, n = m.group(1), m.group(2)
        cands.append(_gp(f"WO{y}{n.zfill(6)}A1", "en"))
        cands.append(_gp(f"JPWO{y}{n.zfill(6)}A1", "ja"))
        return cands

    # --- 実用新案登録 ---
    m = re.match(r"(?:実登|登録実用新案|実用新案登録)(?:第)?\s*(\d+)\s*(?:号)?\s*$", pid)
    if m:
        n = m.group(1)
        cands.append(_gp(f"JPU{n}", "ja"))
        cands.append(_gp(f"JP{n}U", "ja"))
        return cands

    # --- 特願 (出願番号、Google Patents は通常未収録だが念のため) ---
    m = re.match(r"特願\s*(\d{4})\s*[-ー－]\s*(\d+)\s*$", pid)
    if m:
        y, n = m.group(1), m.group(2)
        cands.append(_gp(f"JP{y}{n}A", "ja"))
        return cands

    # --- ASCII 国コード付き ---
    cleaned = re.sub(r"[\s\-/／]", "", pid)
    upper = cleaned.upper()

    # WO 表記正規化
    m = re.match(r"WO(\d{4})(\d+)([A-Z]\d?)?$", upper)
    if m:
        y, n = m.group(1), m.group(2)
        kc = m.group(3) or "A1"
        cands.append(_gp(f"WO{y}{n.zfill(6)}{kc}", "en"))
        return cands

    # JP / US / EP / CN / KR その他は cleaned をそのまま使う
    if upper.startswith("JP"):
        cands.append(_gp(cleaned, "ja"))
        cands.append(_gp(cleaned, "en"))
    elif upper[:2].isalpha():
        cands.append(_gp(cleaned, "en"))
    else:
        # 完全な未識別形式: en で試す
        cands.append(_gp(cleaned, "en"))
    return cands


def build_google_patents_url(patent_id):
    """後方互換: 最優先の候補 URL を返す。"""
    cands = build_google_patents_url_candidates(patent_id)
    if cands:
        return cands[0]
    cleaned = re.sub(r"[\s\-/／]", "", patent_id or "")
    return _gp(cleaned, "en")


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
    """特許PDFを Google Patents 経由でダウンロード。

    日本語表記 (特開/特表/特許/再表/実登) を ASCII Kind-code 付き形式に変換した
    候補 URL を順次試行し、最初に PDF が取得できたものを採用。

    Parameters:
        patent_id: 特許番号 (例: "US5286475", "JP2020082440A", "特開2020-169128",
                              "特許6789012", "再表2012-029514")
        save_dir: 保存先ディレクトリ (Path or str)
        timeout: タイムアウト秒数

    Returns:
        dict:
            success: bool
            path: ファイルパス (成功時)
            error: エラーメッセージ (失敗時)
            google_patents_url: 最後に試した Google Patents URL
    """
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    # ファイル名用にサニタイズ (日本語の特開等は \w に含まれないので英数字 ID へ変換)
    safe_name = re.sub(r"[^\w\-]", "", patent_id)
    if not safe_name:
        safe_name = "unknown"

    candidates = build_google_patents_url_candidates(patent_id)
    if not candidates:
        return {
            "success": False,
            "error": "Google Patents URL を生成できませんでした",
            "google_patents_url": "",
        }

    last_error = None
    last_url = candidates[-1]

    for google_url in candidates:
        last_url = google_url
        # Step 1: Google Patents ページを取得して PDF URL を抽出
        try:
            google_patents_throttle.wait()
            page_resp = requests.get(google_url, headers=_HEADERS, timeout=timeout,
                                     allow_redirects=True)
            if page_resp.status_code != 200:
                last_error = f"Google Patents ページ取得失敗 (status={page_resp.status_code})"
                continue
            pdf_url = _extract_pdf_url_from_html(page_resp.text)
            if not pdf_url:
                last_error = "Google Patents ページから PDF リンクを検出できませんでした"
                continue
        except requests.Timeout:
            last_error = "Google Patents ページ取得がタイムアウトしました"
            continue
        except requests.RequestException as e:
            last_error = f"Google Patents ページ取得エラー: {e}"
            continue

        # Step 2: PDF をダウンロード（patentimages も同じドメイン群なので throttle 対象）
        try:
            google_patents_throttle.wait()
            pdf_resp = requests.get(pdf_url, headers=_HEADERS, timeout=timeout,
                                    allow_redirects=True)
            if (pdf_resp.status_code == 200 and
                    "pdf" in pdf_resp.headers.get("content-type", "").lower()):
                path = save_dir / f"{safe_name}.pdf"
                path.write_bytes(pdf_resp.content)
                return {
                    "success": True,
                    "path": str(path),
                    "google_patents_url": google_url,
                }
            last_error = f"PDF ダウンロード失敗 (status={pdf_resp.status_code})"
        except requests.Timeout:
            last_error = "PDF ダウンロードがタイムアウトしました"
        except requests.RequestException as e:
            last_error = f"PDF ダウンロードエラー: {e}"

    return {
        "success": False,
        "error": last_error or "全候補 URL で PDF を取得できませんでした",
        "google_patents_url": last_url,
    }


# ----------------------------------------------------------------
# 統合ダウンローダ: JP は J-PlatPat、それ以外は Google Patents
# ----------------------------------------------------------------

def is_jp_patent_id(patent_id):
    """patent_id が J-PlatPat 番号照会で扱える日本特許番号かを判定。

    特開/特表/特許 + 西暦・元号、JP-yyyy-nnnnnnA、JP-nnnnnnnB 等を JP として扱い、
    WO/US/EP/再表/再公表 や正規化不能な文字列は False を返す
    (jplatpat_pdf_downloader.normalize_jp_patent_number に委譲)。
    """
    if not (patent_id or "").strip():
        return False
    try:
        from modules.jplatpat_pdf_downloader import normalize_jp_patent_number
    except ImportError:
        return False
    try:
        normalize_jp_patent_number(patent_id)
        return True
    except ValueError:
        return False


def download_patent_pdf_smart(patent_id, save_dir, *, timeout=30,
                              prefer_jplatpat=True, headless=True,
                              on_progress=None):
    """JP 番号は J-PlatPat、それ以外は Google Patents。失敗時クロスフォールバック。

    - JP 番号: J-PlatPat 番号照会 → 公報 PDF 内部 API → ページ結合
        失敗したら Google Patents に切替
    - 非 JP / JP 判定不能: Google Patents 直行
    - 両方失敗: Google Patents の失敗 dict を返す (エラーには J-PlatPat 側のメッセージも添付)

    既存の download_patent_pdf と互換のレスポンス dict (success/path/error/google_patents_url)
    に加え、成功時は ``source`` フィールドで取得経路を示す:
        "jplatpat" — J-PlatPat 経由 (num_pages, title も付与)
        "google_patents" — Google Patents 経由

    Parameters:
        prefer_jplatpat: False にすると JP 番号でも Google Patents に直行 (既存挙動互換)
        headless: J-PlatPat 取得時の Playwright モード (バッチ実行は True 推奨)
        on_progress: J-PlatPat 進捗ログ用コールバック
    """
    pid = (patent_id or "").strip()
    if not pid:
        return {"success": False, "error": "patent_id が空です",
                "google_patents_url": ""}

    if prefer_jplatpat and is_jp_patent_id(pid):
        jp_error = ""
        try:
            from modules.jplatpat_pdf_downloader import download_jplatpat_pdf
            jp_result = download_jplatpat_pdf(
                pid, save_dir, headless=headless, on_progress=on_progress,
            )
            if jp_result.get("success"):
                return {
                    "success": True,
                    "path": jp_result["path"],
                    "source": "jplatpat",
                    "num_pages": jp_result.get("num_pages"),
                    "title": jp_result.get("title", ""),
                    "google_patents_url": build_google_patents_url(pid),
                }
            jp_error = jp_result.get("error", "")
        except Exception as e:
            jp_error = f"J-PlatPat 経路で例外: {e}"

        # フォールバック: Google Patents
        gp_result = download_patent_pdf(pid, save_dir, timeout=timeout)
        gp_result["jplatpat_error"] = jp_error
        if gp_result.get("success"):
            gp_result["source"] = "google_patents"
            gp_result["fallback"] = True
        return gp_result

    # 非 JP は Google Patents 直行
    gp_result = download_patent_pdf(pid, save_dir, timeout=timeout)
    if gp_result.get("success"):
        gp_result["source"] = "google_patents"
    return gp_result
