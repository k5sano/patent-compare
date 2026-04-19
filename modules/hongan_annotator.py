# -*- coding: utf-8 -*-
"""
本願PDF上に分節ID（例: "1A"）ラベルを描画する。
- 請求項部の該当分節テキスト先頭に赤ラベル
- 関連段落（【XXXX】）に青ラベル
"""

import re
import fitz


_FW_DIGITS = str.maketrans("0123456789", "０１２３４５６７８９")

CLAIM_COLOR = (0.84, 0.16, 0.16)   # 赤系
PARA_COLOR = (0.13, 0.50, 0.81)    # 青系


def _to_fullwidth_digits(s: str) -> str:
    return s.translate(_FW_DIGITS)


def _search_claim_head(doc, seg_text, min_len=10, max_len=24):
    """分節テキストの先頭を使い、全ページ走査で最初の一致を返す"""
    if not seg_text:
        return None, None
    head = re.sub(r"\s+", "", seg_text)[:max_len]
    if len(head) < min_len:
        return None, None
    for n in range(len(head), min_len - 1, -2):
        for pi in range(doc.page_count):
            rects = doc[pi].search_for(head[:n])
            if rects:
                return pi, rects[0]
    return None, None


def _search_para_marker(page, para_id):
    if not para_id:
        return None
    for form in (f"【{para_id}】", _to_fullwidth_digits(f"【{para_id}】")):
        rects = page.search_for(form)
        if rects:
            return rects[0]
    return None


def _draw_label(page, anchor_rect, label, rgb):
    fs = 9
    w = max(22.0, 5.5 * len(label) + 8)
    h = 13.0
    x0 = anchor_rect.x0 - w - 2
    if x0 < 5:
        x0 = min(anchor_rect.x1 + 2, page.rect.width - w - 2)
    y0 = max(5, anchor_rect.y0)
    box = fitz.Rect(x0, y0, x0 + w, y0 + h)
    page.draw_rect(box, color=rgb, fill=rgb, width=0.5, overlay=True)
    page.insert_text(
        fitz.Point(box.x0 + 3, box.y1 - 3),
        label, fontsize=fs, color=(1, 1, 1),
        fontname="helv", overlay=True,
    )


def apply_hongan_annotations(doc, claim_items, para_items):
    """本願PDF に分節IDラベルを描画する（in-place）。

    Args:
        doc: fitz.Document
        claim_items: [{"seg_id": "1A", "seg_text": "..."}, ...]
        para_items:  [{"seg_id": "1A", "para_id": "0012", "page": 3}, ...]

    Returns:
        描画したラベル総数
    """
    n = 0

    # 請求項側: 同じ seg_text を重複検索しないようキャッシュ
    claim_cache = {}
    for item in claim_items:
        stext = item.get("seg_text", "")
        key = stext[:40]
        if key not in claim_cache:
            claim_cache[key] = _search_claim_head(doc, stext)
        page_idx, rect = claim_cache[key]
        if page_idx is None:
            continue
        _draw_label(doc[page_idx], rect, item["seg_id"], CLAIM_COLOR)
        n += 1

    # 関連段落側
    fallback_slots = {}
    for item in para_items:
        page_num = int(item.get("page") or 1)
        if not (1 <= page_num <= doc.page_count):
            continue
        page = doc[page_num - 1]
        rect = _search_para_marker(page, item.get("para_id", ""))
        if rect is None:
            slot = fallback_slots.get(page_num, 0)
            y = 18 + slot * 15
            rect = fitz.Rect(10, y, 40, y + 12)
            fallback_slots[page_num] = slot + 1
        _draw_label(page, rect, item["seg_id"], PARA_COLOR)
        n += 1
    return n
