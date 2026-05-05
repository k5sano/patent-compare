#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Google Patents ブラウザスクレイパー

Playwright (ヘッドレス Chromium) を使って Google Patents を直接検索する。
APIキー不要。

使い方:
    from modules.google_patents_scraper import search_google_patents
    hits = search_google_patents("エアゾール 化粧料 油状泡沫", max_results=10)
    hits_jp = search_google_patents("エアゾール 化粧料", country="JP", max_results=5)
"""

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import quote_plus

from modules import google_patents_throttle

logger = logging.getLogger(__name__)


@dataclass
class PatentHit:
    """検索結果1件"""
    patent_id: str
    title: str = ""
    assignee: str = ""
    priority_date: str = ""
    snippet: str = ""
    source: str = "google_patents"
    url: str = ""
    pdf_url: str = ""
    is_patent: bool = True


def search_google_patents(
    query: str,
    country: str = "",
    max_results: int = 10,
    language: str = "ja",
    timeout_ms: int = 20000,
) -> List[PatentHit]:
    """Google Patents をヘッドレスブラウザで検索する。

    Args:
        query: 検索クエリ (自然言語 or Google Patents 構文)
        country: 国コード制限 (例: "JP") → query に "country:JP" を付加
        max_results: 最大取得件数
        language: 言語設定 (デフォルト: "ja")
        timeout_ms: ページ読み込みタイムアウト (ms)

    Returns:
        PatentHit のリスト
    """
    if country:
        query = f"{query} country:{country}"

    encoded_q = quote_plus(query)
    url = (
        f"https://patents.google.com/?q={encoded_q}"
        f"&oq={encoded_q}&hl={language}&num={max_results}"
    )

    logger.info("Google Patents ブラウザ検索: %s", query)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error(
            "playwright 未インストール: "
            "pip install playwright && playwright install chromium"
        )
        return []

    hits: List[PatentHit] = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                locale=language,
            )
            page = context.new_page()
            google_patents_throttle.wait()
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)

            hits = _extract_results(page, max_results)
            browser.close()

    except Exception as e:
        logger.warning("Google Patents ブラウザ検索エラー: %s", e)
        return []

    logger.info("Google Patents 検索結果: %d件", len(hits))
    return hits


def _extract_results(page, max_results: int) -> List[PatentHit]:
    """ページから検索結果を抽出する"""
    hits: List[PatentHit] = []

    # Google Patents: <search-result-item> > <article> 内に結果がある。
    # 特許IDは <state-modifier data-result="patent/JP2024037328A/ja"> に格納。
    items = page.query_selector_all("search-result-item")

    if not items:
        # フォールバック: HTMLテキストから正規表現抽出
        logger.info("search-result-item なし — HTML正規表現で抽出")
        return _extract_from_html(page.content(), max_results)

    for item in items[:max_results]:
        try:
            hit = _parse_result_item(item)
            if hit:
                hits.append(hit)
        except Exception as e:
            logger.debug("結果アイテムのパースエラー: %s", e)

    return hits


def _parse_result_item(item) -> Optional[PatentHit]:
    """1件の <search-result-item> をパース"""

    # --- 特許ID ---
    # state-modifier[data-result] の値: "patent/JP2024037328A/ja"
    sm = item.query_selector("state-modifier[data-result]")
    if not sm:
        return None

    data_result = sm.get_attribute("data-result") or ""
    m = re.search(r"patent/([^/]+)", data_result)
    if not m:
        return None

    patent_id = m.group(1)
    url = f"https://patents.google.com/patent/{patent_id}"

    # --- タイトル ---
    title = ""
    h3 = item.query_selector("h3")
    if h3:
        title = (h3.inner_text() or "").strip()

    # --- メタデータ（出願人・日付） ---
    # span.style-scope.search-result-item にメタデータが格納される:
    #   [国コード, patent_id, patent_id, 発明者, 発明者, ...]
    # .metadata にまとめて: "JP JP2024037328A Inventor Name Organization"
    assignee = ""
    priority_date = ""

    # .metadata テキストから出願人を抽出
    meta_el = item.query_selector(".metadata")
    if meta_el:
        meta_text = (meta_el.inner_text() or "").strip()
        # patent_id より後の部分を取得（国コード・IDを除去）
        parts = meta_text.split()
        # 国コード(2文字)とpatent_id文字列を除外して残りを出願人とする
        assignee_parts = []
        for part in parts:
            if len(part) <= 3 and part.isalpha():
                continue  # 国コード (JP, US, EP...)
            if patent_id in part:
                continue  # patent_id
            assignee_parts.append(part)
        assignee = " ".join(assignee_parts)

    # abstract div からPriority日付を抽出
    abstract_div = item.query_selector("div.abstract, .abstract")
    if abstract_div:
        full_text = (abstract_div.inner_text() or "").strip()
        date_m = re.search(r"Priority\s+(\d{4}-\d{2}-\d{2})", full_text)
        if date_m:
            priority_date = date_m.group(1)

    return PatentHit(
        patent_id=patent_id,
        title=title or patent_id,
        assignee=assignee,
        priority_date=priority_date,
        source="google_patents",
        url=url,
    )


def _extract_from_html(html: str, max_results: int) -> List[PatentHit]:
    """HTMLテキストから正規表現で特許IDを抽出（フォールバック）"""
    hits = []
    seen = set()

    for m in re.finditer(r'patent/([A-Z]{2}\d{4,}[A-Z]?\d?)', html):
        if len(hits) >= max_results:
            break

        patent_id = m.group(1)
        if patent_id in seen:
            continue
        seen.add(patent_id)

        hits.append(PatentHit(
            patent_id=patent_id,
            title=patent_id,
            source="google_patents",
            url=f"https://patents.google.com/patent/{patent_id}",
        ))

    return hits


# --- 詳細ページ取得 ---

def fetch_patent_detail(
    patent_id: str,
    *,
    language: str = "ja",
    timeout_ms: int = 20000,
) -> dict:
    """Google Patents の特許詳細ページから要約と請求項1を取得する。

    Returns:
        {"abstract": "...", "claim1": "...", "title": "...", "assignee": "...", "url": "..."}
        取得失敗時は空文字列でフィールドが埋められる。
    """
    cleaned = normalize_for_google_patents(patent_id)
    url = f"https://patents.google.com/patent/{cleaned}/{language}"

    result = {
        "patent_id": patent_id,
        "url": url,
        "title": "",
        "abstract": "",
        "claim1": "",
        "assignee": "",
    }

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return result

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(locale=language)
                page = context.new_page()
                google_patents_throttle.wait()
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                page.wait_for_timeout(2500)

                # タイトル
                try:
                    el = page.locator("h1#title, h1").first
                    if el.count() > 0:
                        result["title"] = (el.inner_text() or "").strip()[:200]
                except Exception:
                    pass

                # 出願人
                try:
                    el = page.locator('dd[itemprop="assigneeCurrent"], dd[itemprop="assigneeOriginal"]').first
                    if el.count() > 0:
                        result["assignee"] = (el.inner_text() or "").strip()
                except Exception:
                    pass

                # Abstract
                try:
                    el = page.locator('abstract, section[itemprop="abstract"]').first
                    if el.count() > 0:
                        txt = (el.inner_text() or "").strip()
                        result["abstract"] = txt[:1500]
                except Exception:
                    pass

                # Claim 1
                try:
                    el = page.locator('div.claim[num="00001"], div[num="00001"]').first
                    if el.count() == 0:
                        el = page.locator('.claim-text, .claim').first
                    if el.count() > 0:
                        txt = (el.inner_text() or "").strip()
                        result["claim1"] = txt[:1500]
                except Exception:
                    pass
            finally:
                browser.close()
    except Exception as e:
        logger.warning("fetch_patent_detail error (%s): %s", patent_id, e)

    return result


def normalize_for_google_patents(patent_id: str) -> str:
    """Google Patents の URL に使える ID 形式へ正規化する。

    例:
      再表2012/029514 → WO2012029514A1
      特開2024-073024 → JP2024073024A
      特許第6719258号 → JP6719258B2
      WO2012/029514  → WO2012029514A1
    解釈できないものは余計な空白/ハイフン/スラッシュだけ落として返す。
    """
    s = (patent_id or "").strip()
    if not s:
        return ""
    # 全角→半角・装飾除去
    s = s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    s = (s.replace("－", "-").replace("ー", "-").replace("−", "-")
           .replace("―", "-").replace("—", "-").replace("／", "/"))
    s = re.sub(r'(号公報|号|公報|明細書)', '', s)
    s = re.sub(r'[（(][^)）]*[)）]', '', s)
    s = s.strip()

    # 再公表/再表 yyyy[-/]nnnnnn → WOyyyynnnnnnA1
    m = re.search(r'再(?:公)?表\s*(\d{4})\s*[-/]\s*(\d+)', s)
    if m:
        return f"WO{m.group(1)}{m.group(2).zfill(6)}A1"
    # 特開 yyyy-nnnnnn → JPyyyynnnnnnA
    m = re.search(r'特開\s*(\d{4})\s*-\s*(\d+)', s)
    if m:
        return f"JP{m.group(1)}{m.group(2).zfill(6)}A"
    # 特表 yyyy-nnnnnn → JPyyyynnnnnnA
    m = re.search(r'特表\s*(\d{4})\s*-\s*(\d+)', s)
    if m:
        return f"JP{m.group(1)}{m.group(2).zfill(6)}A"
    # 特願 yyyy-nnnnnn → JPyyyynnnnnnA (Google Patents は出願番号→公開公報をある程度紐付ける)
    m = re.search(r'特願\s*(\d{4})\s*-\s*(\d+)', s)
    if m:
        return f"JP{m.group(1)}{m.group(2).zfill(6)}A"
    # 特許第nnn号 → JPnnnB2
    m = re.search(r'特許(?:第)?\s*(\d+)', s)
    if m:
        return f"JP{m.group(1)}B2"
    # WO yyyy[-/]nnnnnn → WOyyyynnnnnnA1 (空白/ハイフン/スラッシュ自由)
    m = re.search(r'WO\s*(\d{4})\s*[-/]?\s*(\d+)', s, re.I)
    if m:
        return f"WO{m.group(1)}{m.group(2).zfill(6)}A1"
    # JP yyyy-nnnnnn A → JPyyyynnnnnnA
    m = re.search(r'JP\s*(\d{4})\s*-?\s*(\d{3,6})\s*A\d?', s, re.I)
    if m:
        return f"JP{m.group(1)}{m.group(2).zfill(6)}A"
    # JPnnn B → JPnnnB2 (B1/B2 数字が無ければ B2 と仮定)
    m = re.search(r'JP\s*(\d{5,8})\s*B(\d?)', s, re.I)
    if m:
        b = m.group(2) or "2"
        return f"JP{m.group(1)}B{b}"
    # US 8,123,456 B2 系: カンマ/空白/ハイフン/スラッシュを落とす (US8123456B2)
    m = re.search(r'US\s*([\d,]+)\s*([A-Z]\d?)?', s, re.I)
    if m:
        num = m.group(1).replace(',', '')
        suffix = m.group(2) or ''
        return f"US{num}{suffix}".upper().replace(' ', '')
    # それ以外 (EP, CN, KR 等) は空白/ハイフン/スラッシュ/カンマを落とす
    return re.sub(r'[\s\-/,]', '', s)


def fetch_patent_full_text(
    patent_id: str,
    *,
    language: str = "ja",
    timeout_ms: int = 30000,
) -> dict:
    """Google Patents から公報の全文（claims + description）を取得する。

    Returns:
        {"patent_id", "url", "title", "abstract", "claims": [text...], "description": "text", "fetched_at": iso_dt}
        取得失敗時は claims=[], description="" で返る。
    """
    import datetime
    cleaned = normalize_for_google_patents(patent_id)
    url = f"https://patents.google.com/patent/{cleaned}/{language}"

    result = {
        "patent_id": patent_id,
        "url": url,
        "title": "",
        "abstract": "",
        "claims": [],
        "description": "",
        "images": [],
        "fetched_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return result

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage", "--disable-gpu",
                      "--disable-blink-features=AutomationControlled"],
            )
            try:
                context = browser.new_context(locale=language,
                                               viewport={"width": 1024, "height": 768})
                # 重いリソース (画像/フォント/動画/スタイルシート) をブロックして高速化
                def _block(route):
                    rt = route.request.resource_type
                    if rt in ("image", "font", "media", "stylesheet"):
                        route.abort()
                    else:
                        route.continue_()
                try:
                    context.route("**/*", _block)
                except Exception:
                    pass
                page = context.new_page()
                google_patents_throttle.wait()
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                # 必須要素 (タイトル) → 本文 (description) の順に待つ
                try:
                    page.wait_for_selector("h1#title", timeout=8000)
                except Exception:
                    pass
                try:
                    page.wait_for_selector("#description", timeout=10000)
                except Exception:
                    try:
                        page.wait_for_selector("div.claim", timeout=3000)
                    except Exception:
                        page.wait_for_timeout(1500)
                page.wait_for_timeout(400)

                # タイトル
                try:
                    el = page.locator("h1#title").first
                    if el.count() > 0:
                        result["title"] = (el.inner_text() or "").strip()[:300]
                except Exception:
                    pass

                # Abstract
                try:
                    el = page.locator('abstract, section[itemprop="abstract"], #text > abstract').first
                    if el.count() > 0:
                        result["abstract"] = (el.inner_text() or "").strip()
                except Exception:
                    pass

                # Claims (各クレームを個別に取得)
                try:
                    items = page.locator('div.claim').all()
                    if items:
                        for it in items:
                            try:
                                txt = (it.inner_text() or "").strip()
                                if txt:
                                    result["claims"].append(txt)
                            except Exception:
                                continue
                    if not result["claims"]:
                        # フォールバック: #claims セクション全体
                        cl_block = page.locator("#claims").first
                        if cl_block.count() > 0:
                            t = (cl_block.inner_text() or "").strip()
                            if t:
                                result["claims"] = [t]
                except Exception:
                    pass

                # Description (#description が現在の構造)
                try:
                    desc = page.locator("#description").first
                    if desc.count() > 0:
                        d = (desc.inner_text() or "").strip()
                        # 先頭の "Description" ラベルだけ除去
                        if d.startswith("Description\n"):
                            d = d[len("Description\n"):]
                        elif d.startswith("Description"):
                            d = d[len("Description"):].lstrip()
                        result["description"] = d
                except Exception:
                    pass

                # 実施例の表 (画像) を抽出。Google Patents は description 内の表を
                # patentimages.storage.googleapis.com の <img> として埋め込む。
                try:
                    images_data = page.evaluate("""() => {
                      const desc = document.querySelector('#description');
                      if (!desc) return [];
                      const imgs = Array.from(desc.querySelectorAll('img'));
                      return imgs.map(im => {
                        // 直近の「文字塊」を caption として拾う (前方への遡り)
                        let cur = im;
                        let context = '';
                        for (let step = 0; step < 8; step++) {
                          cur = cur.previousElementSibling || (cur.parentElement);
                          if (!cur) break;
                          const t = (cur.textContent || '').trim();
                          if (t.length >= 10 && t.length < 600) {
                            context = t.slice(0, 300);
                            break;
                          }
                        }
                        return {
                          src: im.src,
                          alt: im.alt || '',
                          width: im.naturalWidth || im.width || 0,
                          height: im.naturalHeight || im.height || 0,
                          context: context,
                        };
                      }).filter(o => o.src);
                    }""")
                    if images_data:
                        # caption から「表N」「Table N」を抽出して label に
                        for im in images_data:
                            ctx = im.get("context", "")
                            mlabel = re.search(r'(表\s*\d+|Table\s*\d+|Fig(?:ure)?\.?\s*\d+)', ctx, re.I)
                            im["label"] = mlabel.group(1) if mlabel else ""
                        result["images"] = images_data
                except Exception:
                    pass
            finally:
                browser.close()
    except Exception as e:
        logger.warning("fetch_patent_full_text error (%s): %s", patent_id, e)

    return result


# --- 便利関数 ---

def search_patents_with_keywords(
    keywords: List[str],
    country: str = "",
    max_results: int = 10,
) -> List[PatentHit]:
    """キーワードリストで検索（スペース結合）"""
    query = " ".join(keywords[:8])
    return search_google_patents(query, country=country, max_results=max_results)
