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

    # 3. ブックマーク (set_toc は /XYZ で書くので、後段で /FitH に書き換える)
    toc = _build_toc(doc, response, para_page_map, claims_page)
    doc.set_toc(toc)
    _convert_outlines_to_fith(doc)

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


# 公開エイリアス: 本願 PDF (modules.hongan_annotator) からも同じ色で
# キーワードハイライトをかけられるように共有する。色定義 _GROUP_COLORS と
# 描画関数を同モジュール経由で再利用すれば、引用文献と本願で同一配色になる。
def paint_keyword_highlights(doc, keywords):
    """`_draw_keyword_highlights` の公開ラッパ。"""
    return _draw_keyword_highlights(doc, keywords)


def _draw_keyword_highlights(doc, keywords):
    """キーワードを半透明の蛍光ペン風ハイライト注釈で塗る。

    PDFネイティブの highlight annotation を使うので、どのビューワでも
    文字がきちんと透けて見える（draw_rect+fill_opacity はビューワ依存で
    完全に塗りつぶされてしまう問題の回避）。

    実装メモ: ページごとにまとめて処理し、同じページに対する annot の
    更新中にページ参照が無効化されないようにする。
    """
    count = 0
    # page → list[(rect, color)] に先に集約
    per_page = {i: [] for i in range(doc.page_count)}
    # 重複除外用: (page, x0, y0, x1, y1) の集合 (本願 PDF で 1 語が多数ヒットして
    # 7000+ annot になる現象を抑える)
    seen = set()
    for g in keywords:
        gid = g["group_id"]
        color = _GROUP_COLORS[(gid - 1) % len(_GROUP_COLORS)]
        for kw in g.get("keywords", []):
            term = (kw.get("term") or "").strip()
            # 短すぎる語は誤マッチが多いので除外 (英数字 3 文字 / 日本語 2 文字以上)
            if not term or len(term) < 2:
                continue
            if term.isascii() and len(term) < 3:
                continue
            for pn in range(doc.page_count):
                page = doc[pn]
                for rect in page.search_for(term):
                    key = (pn, round(rect.x0, 1), round(rect.y0, 1),
                           round(rect.x1, 1), round(rect.y1, 1))
                    if key in seen:
                        continue
                    seen.add(key)
                    per_page[pn].append((rect, color))

    # ページ単位で注釈付与
    for pn, items in per_page.items():
        if not items:
            continue
        page = doc[pn]
        for rect, color in items:
            annot = page.add_highlight_annot(rect)
            annot.set_colors(stroke=color)
            annot.set_opacity(0.4)
            annot.update()
            count += 1
    return count


def _build_toc(doc, response, para_page_map, claims_page):
    """ブックマーク（目次）を構築。

    引用段落ごとに個別エントリを作り、**段落の実位置** (PDF 上で 【NNNN】 が
    実際に出現する rect) にジャンプさせる。テキスト検索で見つからない場合だけ
    ページ先頭にフォールバック。

    Dest 形式: 4 要素 TOC `[level, title, page_1based, {kind, to, zoom}]`。
    set_toc は to.y をそのまま PDF-y として /XYZ に書き出すので、
    呼び側で `pdf_y = page_height - rect.y0` を計算してから渡す。
    後段の _convert_outlines_to_fith が /XYZ を /FitH に変換する。
    """
    doc_id = response.get("document_id", "引用文献")
    toc = [[1, f"対比結果: {doc_id}", 1]]

    def _make_dest(pn, rect):
        page_height = doc[pn].mediabox.height
        # rect.y0 は screen-coords (top-down) の上端 → PDF-coords (bottom-up) に変換
        # 少し上にマージンを取って、段落の冒頭が画面上部に来るように +20
        pdf_y = page_height - max(0.0, rect.y0 - 20)
        return {"kind": 1, "to": fitz.Point(0, pdf_y), "zoom": 0}

    def _emit(label_prefix, judgment, cited_loc):
        paras = _parse_cited_paragraphs(cited_loc)
        emitted = False
        for para_id in paras:
            label_loc = "請求項" if para_id == "__claims__" else f"【{para_id}】"
            label = f"{label_prefix} {judgment} {label_loc}"
            # 段落の実位置を PDF 内テキスト検索で解決
            pn, rect = _resolve_paragraph_location(
                doc, para_id, cited_loc, para_page_map, claims_page)
            if pn is not None and rect is not None:
                toc.append([2, label, pn + 1, _make_dest(pn, rect)])
            else:
                # 検索失敗: 段落 ID から推定したページの先頭にフォールバック
                fallback_page = (claims_page if para_id == "__claims__"
                                 else para_page_map.get(para_id, 1))
                toc.append([2, label, fallback_page])
            emitted = True
        if not emitted:
            toc.append([2, f"{label_prefix} {judgment} {cited_loc or '(不明)'}", 1])

    for comp in response.get("comparisons", []):
        _emit(comp["requirement_id"], comp["judgment"], comp.get("cited_location", ""))

    for sub in response.get("sub_claims", []):
        _emit(f"請求項{sub['claim_number']}", sub["judgment"], sub.get("cited_location", ""))

    return toc


def _convert_outlines_to_fith(doc, top_offset_from_top=36):
    """set_toc で生成された /XYZ ベースのブックマーク dest を /FitH 形式に書き換える。

    PDF-XChange Editor 等で /XYZ x y 0 (zoom=0) のブックマークを押してもページ移動が
    起きない報告があり、原 PDF が使っている /FitH 形式に揃えると動作する。

    /XYZ に既に y が指定されている場合 (TOC を 4 要素形式で精密位置指定した場合)
    はその y をそのまま /FitH に流用。デフォルト top の場合 (3 要素形式) のみ
    `top_offset_from_top` を使ってフォールバック。
    """
    # outline ツリーをトラバースして /A /GoTo /D を /FitH に書き換える
    catalog_outlines_xref = None
    try:
        ol = doc.xref_get_key(doc.pdf_catalog(), "Outlines")
        if ol and ol[0] == "xref":
            catalog_outlines_xref = int(ol[1].split()[0])
    except Exception:
        return 0
    if not catalog_outlines_xref:
        return 0

    pages_height_cache = {}
    def _page_height_for_xref(page_xref):
        if page_xref in pages_height_cache:
            return pages_height_cache[page_xref]
        for i in range(doc.page_count):
            if doc[i].xref == page_xref:
                h = doc[i].mediabox.height
                pages_height_cache[page_xref] = h
                return h
        pages_height_cache[page_xref] = None
        return None

    converted = 0
    visited = set()
    stack = [catalog_outlines_xref]
    while stack:
        xref = stack.pop()
        if xref in visited:
            continue
        visited.add(xref)
        try:
            obj = doc.xref_object(xref)
        except Exception:
            continue
        # 子要素を辿る
        for key in ("First", "Last", "Next", "Prev"):
            try:
                v = doc.xref_get_key(xref, key)
                if v and v[0] == "xref":
                    stack.append(int(v[1].split()[0]))
            except Exception:
                pass
        # /A /D を解析して /FitH に書き換え
        m = re.search(
            r'/A\s*<<\s*/S\s*/GoTo\s*/D\s*\[\s*(\d+)\s+0\s+R\s*/XYZ\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s*\]\s*>>',
            obj,
        )
        if not m:
            continue
        page_xref = int(m.group(1))
        ph = _page_height_for_xref(page_xref)
        if ph is None:
            continue
        existing_y = float(m.group(3))
        # 既に y が page_height - 36 ぐらい (set_toc が simple form で生成したデフォルト)
        # と異なる場合はそれを精密位置とみなして流用。それ以外は top_offset を適用。
        default_y = ph - float(top_offset_from_top)
        if abs(existing_y - default_y) > 1.0:
            # 4 要素形式で精密位置を渡されている → そのまま /FitH に転記
            fith_y = existing_y
        else:
            # 3 要素形式 (デフォルト top of page) → 同じ位置を /FitH で
            fith_y = default_y
        new_a = f"<</S/GoTo/D[{page_xref} 0 R/FitH {fith_y:.4f}]>>"
        try:
            doc.xref_set_key(xref, "A", new_a)
            converted += 1
        except Exception:
            pass
    return converted
