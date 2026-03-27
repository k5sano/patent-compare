#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
引用文献PDF注釈モジュール

対比結果に基づいて引用文献PDFに以下を追加:
- 段落番号の左に分節番号ラベル（○△×色分け）
- キーワードの透過ハイライト（グループ別色分け）
- ブックマーク（分節→該当ページジャンプ）
"""

import re
import fitz  # PyMuPDF

# 半角⇔全角変換テーブル
_HW2FW = str.maketrans("0123456789", "０１２３４５６７８９")
_FW2HW = str.maketrans("０１２３４５６７８９", "0123456789")

# 判定の色（RGB, ラベル背景用）
_JUDGMENT_COLORS = {
    "○": (0.13, 0.55, 0.13),   # 緑
    "△": (0.85, 0.55, 0.0),    # オレンジ
    "×": (0.85, 0.15, 0.15),   # 赤
}
_DEFAULT_COLOR = (0.5, 0.5, 0.5)

# キーワードグループのハイライト色（明るめ、透過して使用）
_GROUP_COLORS = [
    (1.0, 0.6, 0.6),     # 赤系
    (0.78, 0.65, 1.0),   # 紫系
    (1.0, 0.6, 0.85),    # マゼンタ系
    (0.6, 0.75, 1.0),    # 青系
    (0.6, 1.0, 0.7),     # 緑系
    (1.0, 0.82, 0.5),    # オレンジ系
    (0.5, 0.95, 0.85),   # ティール系
]


def annotate_citation_pdf(pdf_path, output_path, response, citation, keywords=None):
    """引用文献PDFに対比結果の注釈を追加

    Parameters:
        pdf_path: 元PDF (str or Path)
        output_path: 出力先PDF (str or Path)
        response: 対比結果 (responses/{doc_id}.json の内容)
        citation: 引用文献構造化データ (citations/{doc_id}.json の内容)
        keywords: キーワードグループ (keywords.json の内容, optional)

    Returns:
        dict: 処理結果
            labels: 追加した分節ラベル数
            highlights: キーワードハイライト数
            bookmarks: ブックマーク数
    """
    doc = fitz.open(str(pdf_path))

    para_page_map = _build_para_page_map(citation)
    claims_page = _find_claims_page(citation)

    # 1. キーワードハイライト（先に描画、下層にする）
    hl_count = 0
    if keywords:
        hl_count = _draw_keyword_highlights(doc, keywords)

    # 2. 段落横の分節ラベル
    label_count = _draw_segment_labels(doc, response, para_page_map, claims_page)

    # 3. ブックマーク
    toc = _build_toc(response, para_page_map, claims_page)
    doc.set_toc(toc)

    doc.save(str(output_path))
    doc.close()

    return {
        "labels": label_count,
        "highlights": hl_count,
        "bookmarks": len(toc),
    }


# ========== 内部関数 ==========

def _build_para_page_map(citation):
    """段落ID(半角)→ページ番号マップ"""
    mapping = {}
    for p in citation.get("paragraphs", []):
        pid_hw = p["id"].translate(_FW2HW)
        mapping[pid_hw] = p["page"]
    return mapping


def _find_claims_page(citation):
    """請求項セクションのページ番号"""
    for p in citation.get("paragraphs", []):
        if "請求" in p.get("section", ""):
            return p["page"]
    return 1


def _parse_cited_paragraphs(cited_location):
    """cited_locationから段落番号リストを抽出"""
    results = []
    for m in re.finditer(r"[【\[](\d{4})[】\]]", cited_location):
        results.append(m.group(1))
    if "請求項" in cited_location:
        results.append("__claims__")
    return results


def _find_rect_in_pdf(doc, search_text, hint_page, search_range=2):
    """PDF内でテキストを検索し、(page_num, rect)を返す"""
    start = max(0, hint_page - 1)
    end = min(doc.page_count, hint_page + search_range)
    for pn in range(start, end):
        rects = doc[pn].search_for(search_text)
        if rects:
            return pn, rects[0]
    # ヒントページ外も全ページ検索
    for pn in range(doc.page_count):
        if start <= pn < end:
            continue
        rects = doc[pn].search_for(search_text)
        if rects:
            return pn, rects[0]
    return None, None


def _draw_segment_labels(doc, response, para_page_map, claims_page):
    """段落番号の左に分節ラベルを描画"""
    # ページごとの注釈を収集
    page_annots = {}  # page_num -> [(rect, label, color)]

    for comp in response.get("comparisons", []):
        req_id = comp["requirement_id"]
        judgment = comp["judgment"]
        cited_loc = comp.get("cited_location", "")
        color = _JUDGMENT_COLORS.get(judgment, _DEFAULT_COLOR)
        paras = _parse_cited_paragraphs(cited_loc)

        for para_id in paras:
            pn, rect = _resolve_paragraph_location(
                doc, para_id, cited_loc, para_page_map, claims_page)
            if pn is not None and rect is not None:
                page_annots.setdefault(pn, []).append(
                    (rect, f"{req_id} {judgment}", color))

    # 従属請求項
    for sub in response.get("sub_claims", []):
        claim_num = sub["claim_number"]
        judgment = sub["judgment"]
        cited_loc = sub.get("cited_location", "")
        color = _JUDGMENT_COLORS.get(judgment, _DEFAULT_COLOR)
        paras = _parse_cited_paragraphs(cited_loc)
        label = f"Cl{claim_num} {judgment}"

        for para_id in paras:
            if para_id == "__claims__":
                continue
            pn, rect = _resolve_paragraph_location(
                doc, para_id, cited_loc, para_page_map, claims_page)
            if pn is not None and rect is not None:
                page_annots.setdefault(pn, []).append((rect, label, color))

    # 描画
    total = 0
    for page_num, annots in page_annots.items():
        page = doc[page_num]
        placed = {}
        for para_rect, label, color in annots:
            y_key = round(para_rect.y0, 0)
            offset = placed.get(y_key, 0)
            placed[y_key] = offset + 1

            x = 3
            y = para_rect.y0 + offset * 11
            label_width = len(label) * 5.2 + 6
            bg_rect = fitz.Rect(x, y - 1, x + label_width, y + 9.5)

            shape = page.new_shape()
            shape.draw_rect(bg_rect)
            shape.finish(fill=color, color=None, fill_opacity=0.9)
            shape.commit()

            page.insert_text(
                fitz.Point(x + 3, y + 7.5),
                label,
                fontsize=7,
                fontname="helv",
                color=(1, 1, 1),
            )
            total += 1

    return total


def _resolve_paragraph_location(doc, para_id, cited_loc, para_page_map, claims_page):
    """段落IDからPDF上の位置を解決"""
    if para_id == "__claims__":
        cm = re.search(r"請求項(\d+)", cited_loc)
        if not cm:
            return None, None
        variants = [
            "【請求項" + cm.group(1).translate(_HW2FW) + "】",
            "【請求項" + cm.group(1) + "】",
        ]
        for sv in variants:
            pn, rect = _find_rect_in_pdf(doc, sv, claims_page)
            if pn is not None:
                return pn, rect
        return None, None
    else:
        fw_id = "【" + para_id.translate(_HW2FW) + "】"
        hint_page = para_page_map.get(para_id, 1)
        return _find_rect_in_pdf(doc, fw_id, hint_page)


def _draw_keyword_highlights(doc, keywords):
    """キーワードを40%透過でハイライト"""
    count = 0
    for g in keywords:
        gid = g["group_id"]
        color = _GROUP_COLORS[(gid - 1) % len(_GROUP_COLORS)]

        for kw in g.get("keywords", []):
            term = kw["term"]
            if len(term) < 2:
                continue
            for page_num in range(doc.page_count):
                rects = doc[page_num].search_for(term)
                for rect in rects:
                    shape = doc[page_num].new_shape()
                    shape.draw_rect(rect)
                    shape.finish(fill=color, color=None, fill_opacity=0.4)
                    shape.commit()
                    count += 1
    return count


def _build_toc(response, para_page_map, claims_page):
    """ブックマーク（目次）を構築"""
    doc_id = response.get("document_id", "引用文献")
    toc = [[1, f"対比結果: {doc_id}", 1]]

    for comp in response.get("comparisons", []):
        req_id = comp["requirement_id"]
        judgment = comp["judgment"]
        cited_loc = comp.get("cited_location", "")
        paras = _parse_cited_paragraphs(cited_loc)

        if paras and paras[0] != "__claims__":
            page = para_page_map.get(paras[0], 1)
        elif "請求項" in cited_loc:
            page = claims_page
        else:
            page = 1
        toc.append([2, f"{req_id} {judgment} {cited_loc}", page])

    for sub in response.get("sub_claims", []):
        claim_num = sub["claim_number"]
        judgment = sub["judgment"]
        cited_loc = sub.get("cited_location", "")
        paras = _parse_cited_paragraphs(cited_loc)
        page = (para_page_map.get(paras[0], 1)
                if paras and paras[0] != "__claims__" else claims_page)
        toc.append([2, f"請求項{claim_num} {judgment} {cited_loc}", page])

    return toc
