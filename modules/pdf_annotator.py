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

_CLAIM_MARKER_PREFIX = "__claim__:"


def _claim_marker(claim_number):
    try:
        n = int(str(claim_number).translate(_FW2HW))
    except (TypeError, ValueError):
        return "__claims__"
    return f"{_CLAIM_MARKER_PREFIX}{n}"


def _claim_number_from_marker(marker):
    text = str(marker or "")
    if not text.startswith(_CLAIM_MARKER_PREFIX):
        return None
    try:
        return int(text[len(_CLAIM_MARKER_PREFIX):])
    except ValueError:
        return None


def annotate_citation_pdf(
    pdf_path,
    output_path,
    response,
    citation,
    keywords=None,
    migrate_bookmarks_from=None,
    segments=None,
):
    """引用文献PDFに対比結果の注釈を追加

    Parameters:
        pdf_path: 元PDF (str or Path)
        output_path: 出力先PDF (str or Path)
        response: 対比結果 (responses/{doc_id}.json の内容)
        citation: 引用文献構造化データ (citations/{doc_id}.json の内容)
        keywords: キーワードグループ (keywords.json の内容, optional)
        migrate_bookmarks_from: 旧注釈PDF。ユーザー作成ブックマークだけを
            新PDFの「旧版から移行」配下へ移す (optional)
        segments: segments.json の内容。指定時はブックマークを対比表と同じ
            請求項順・分節順に並べる (optional)

    Returns:
        dict: 処理結果
            labels: 追加した分節ラベル数
            highlights: キーワードハイライト数
            bookmarks: ブックマーク数
    """
    migrated_bookmarks = _extract_user_bookmarks_for_migration(migrate_bookmarks_from)
    doc = fitz.open(str(pdf_path))

    para_page_map = _build_para_page_map(citation)
    claims_page = _find_claims_page(citation)

    # 1. キーワードハイライト（先に描画、下層にする）
    hl_count = 0
    if keywords:
        hl_count = _draw_keyword_highlights(doc, keywords)

    # 2. 段落横の分節ラベル
    label_count = _draw_segment_labels(doc, response, para_page_map, claims_page)

    # 3. ブックマーク: 段落番号の y 座標まで飛ぶ詳細 dest を作る。
    #    サマリーページは末尾に追加するので、元 PDF 側のページ番号はそのまま使う。
    toc_entries = _build_toc_entries(response, doc, para_page_map, claims_page, segments=segments)

    # 4. 末尾に注釈サマリーを追加
    _insert_summary_page(doc, response, citation)

    # 5. TOC。元 PDF 側はページ番号そのまま、サマリーは最終ページ。
    toc = _build_toc(response, toc_entries, summary_page=doc.page_count)
    if migrated_bookmarks:
        _append_migrated_bookmarks(toc, migrated_bookmarks, doc.page_count)
    doc.set_toc(toc)

    doc.save(str(output_path))
    doc.close()

    return {
        "labels": label_count,
        "highlights": hl_count,
        "bookmarks": len(toc),
        "migrated_bookmarks": len(migrated_bookmarks),
    }


# ========== 内部関数 ==========

def _category_label(category):
    c = (category or "").strip().upper()[:1]
    labels = {
        "X": "X: 単独で拒絶理由を構成し得る文献",
        "Y": "Y: 他文献との組合せで拒絶理由を構成し得る文献",
        "A": "A: 参考文献",
    }
    return labels.get(c, c or "未分類")


def _wrap_text(text, max_chars):
    """PDF サマリー用の簡易折り返し。日本語も概ね max_chars で折る。"""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return []
    lines = []
    cur = ""
    for token in re.split(r"([、。・,.;:])", text):
        if not token:
            continue
        if len(cur) + len(token) <= max_chars:
            cur += token
            continue
        if cur:
            lines.append(cur)
        while len(token) > max_chars:
            lines.append(token[:max_chars])
            token = token[max_chars:]
        cur = token
    if cur:
        lines.append(cur)
    return lines


def _draw_textbox(page, rect, text, *, fontsize=10, color=(0.08, 0.1, 0.14),
                  align=fitz.TEXT_ALIGN_LEFT, bold=False):
    """日本語フォントで text box を描く。環境差があるので helv にフォールバック。"""
    fontnames = ("japan", "japan-s", "helv")
    for fontname in fontnames:
        try:
            rc = page.insert_textbox(
                rect,
                text,
                fontsize=fontsize,
                fontname=fontname,
                color=color,
                align=align,
            )
            if rc >= 0:
                return rc
        except Exception:
            continue
    return -1


def _draw_line_text(page, point, text, *, fontsize=10, color=(0.08, 0.1, 0.14)):
    for fontname in ("japan", "japan-s", "helv"):
        try:
            page.insert_text(point, text, fontsize=fontsize, fontname=fontname, color=color)
            return True
        except Exception:
            continue
    return False


def _summary_missing_items(response):
    items = []
    for comp in response.get("comparisons", []) or []:
        judgment = str(comp.get("judgment") or "").strip()
        if judgment not in ("×", "△"):
            continue
        rid = comp.get("requirement_id") or "?"
        reason = comp.get("judgment_reason") or comp.get("reason") or ""
        loc = comp.get("cited_location") or ""
        text = f"{rid} {judgment}"
        if loc:
            text += f" / {loc}"
        if reason:
            text += f": {reason}"
        items.append(text)
    return items


def _insert_summary_page(doc, response, citation):
    """注釈 PDF の最終ページに文献概要・カテゴリ・未充足構成を追加。"""
    first = doc[0] if doc.page_count else None
    width = first.rect.width if first else 595
    height = first.rect.height if first else 842
    page = doc.new_page(width=width, height=height)

    margin = 42
    y = 42
    title_color = (0.1, 0.18, 0.32)
    muted = (0.35, 0.39, 0.46)
    line_color = (0.78, 0.82, 0.88)

    doc_id = response.get("document_id") or citation.get("patent_number") or citation.get("id") or "引用文献"
    title = citation.get("patent_title") or citation.get("title") or citation.get("invention_title") or ""
    applicant = citation.get("applicant") or citation.get("assignee") or ""
    category = _category_label(response.get("category_suggestion", ""))
    summary = response.get("overall_summary") or citation.get("abstract") or ""
    relevance = response.get("rejection_relevance") or ""
    missing = _summary_missing_items(response)

    _draw_line_text(page, fitz.Point(margin, y + 18),
                    "注釈PDFサマリー", fontsize=18, color=title_color)
    y += 34
    _draw_textbox(page, fitz.Rect(margin, y, width - margin, y + 22),
                  str(doc_id), fontsize=12, color=muted)
    y += 28
    page.draw_line(fitz.Point(margin, y), fitz.Point(width - margin, y), color=line_color, width=0.8)
    y += 18

    rows = [
        ("カテゴリ", category),
        ("文献名", title),
        ("出願人", applicant),
    ]
    for label, value in rows:
        if not value:
            continue
        _draw_textbox(page, fitz.Rect(margin, y, margin + 80, y + 18),
                      label, fontsize=9, color=muted)
        _draw_textbox(page, fitz.Rect(margin + 84, y, width - margin, y + 22),
                      str(value), fontsize=10, color=(0.08, 0.1, 0.14))
        y += 24

    y += 6
    _draw_line_text(page, fitz.Point(margin, y + 13),
                    "この文献の概要", fontsize=12, color=title_color)
    y += 20
    summary_lines = _wrap_text(summary or "概要情報がありません。", 44)
    if relevance:
        summary_lines += ["", "拒絶理由との関連性:"] + _wrap_text(relevance, 44)
    for line in summary_lines[:12]:
        h = 14 if line else 8
        _draw_textbox(page, fitz.Rect(margin, y, width - margin, y + h + 4),
                      line, fontsize=9.5, color=(0.1, 0.12, 0.16))
        y += h

    y += 14
    _draw_line_text(page, fitz.Point(margin, y + 13),
                    "本願請求項で埋まっていない構成", fontsize=12, color=title_color)
    y += 20
    if missing:
        for item in missing[:14]:
            for i, line in enumerate(_wrap_text(item, 47)[:3]):
                prefix = "・" if i == 0 else "  "
                _draw_textbox(page, fitz.Rect(margin, y, width - margin, y + 14),
                              prefix + line, fontsize=8.8, color=(0.12, 0.14, 0.18))
                y += 13
            if y > height - 70:
                remaining = len(missing) - missing.index(item) - 1
                if remaining > 0:
                    _draw_textbox(page, fitz.Rect(margin, y, width - margin, y + 16),
                                  f"・ほか {remaining} 件", fontsize=8.8, color=muted)
                break
    else:
        _draw_textbox(page, fitz.Rect(margin, y, width - margin, y + 16),
                      "× または △ の構成要件はありません。", fontsize=9, color=muted)

    footer = "前ページまでが元PDFと注釈です。ブックマークから該当段落番号の位置へ移動できます。"
    _draw_textbox(page, fitz.Rect(margin, height - 42, width - margin, height - 24),
                  footer, fontsize=8, color=muted)
    return page

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
    """cited_location から段落番号 (4 桁文字列) と請求項マーカーのリストを抽出。

    対応する記法:
      - 旧: `【0012】` (4 桁ブラケット)
      - 新コンパクト記法 (modules.cited_ref_notation): `12;CL1`, `67;CL1:CL3`, `63,67-72;F2` など
        - kind=para → ゼロパディング 4 桁化して追加
        - kind=claim → "__claim__:N" を追加 (請求項Nへ飛ばす)
        - 他の kind (page/column/figure/table/...) は段落ジャンプ対象外なのでスキップ
    """
    if not cited_location:
        return []
    results = []
    seen = set()

    def _add(item):
        if item not in seen:
            seen.add(item)
            results.append(item)

    # 旧 ブラケット記法
    for m in re.finditer(r"[【\[](\d{2,5})[】\]]", cited_location):
        _add(f"{int(m.group(1)):04d}")

    # 新コンパクト記法
    try:
        from modules.cited_ref_notation import parse as _parse_notation
        notation = _parse_notation(cited_location)
        for ref in notation.refs:
            if ref.kind == "para":
                for v in (ref.values or []):
                    try:
                        _add(f"{int(v):04d}")
                    except (TypeError, ValueError):
                        pass
            elif ref.kind == "claim":
                values = ref.values or []
                if values:
                    for v in values:
                        _add(_claim_marker(v))
                else:
                    _add("__claims__")
            # page/column/figure/table/chem/formula/eq は段落の直接ジャンプ対象外
    except Exception:
        # parser 失敗時は旧来のブラケット結果のみで継続
        pass

    # 自然文表記の後方互換: "請求項7" / "請求項７"
    for m in re.finditer(r"請求\s*項\s*([0-9０-９]+)", cited_location):
        _add(_claim_marker(m.group(1)))

    # parser 失敗時の保険: CL7 / CL6,7 / CL1-3
    if not any(str(x).startswith(_CLAIM_MARKER_PREFIX) or x == "__claims__" for x in results):
        for m in re.finditer(r"\bCL([0-9,\-]+)", cited_location, flags=re.IGNORECASE):
            for chunk in m.group(1).split(","):
                chunk = chunk.strip()
                if not chunk:
                    continue
                if "-" in chunk:
                    try:
                        a, b = [int(x) for x in chunk.split("-", 1)]
                    except ValueError:
                        continue
                    if a <= b:
                        for n in range(a, b + 1):
                            _add(_claim_marker(n))
                    else:
                        _add(_claim_marker(a))
                        _add(_claim_marker(b))
                elif chunk.isdigit():
                    _add(_claim_marker(chunk))

    if "請求項" in cited_location and not any(str(x).startswith(_CLAIM_MARKER_PREFIX) for x in results):
        _add("__claims__")

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
        req_id = str(sub.get("requirement_id") or "").strip()
        label = f"{req_id or f'CL{claim_num}'} {judgment}"

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
    claim_num = _claim_number_from_marker(para_id)
    if para_id == "__claims__" or claim_num is not None:
        if claim_num is None:
            cm = re.search(r"(?:請求\s*項|CL)\s*([0-9０-９]+)", cited_loc, flags=re.IGNORECASE)
            if cm:
                try:
                    claim_num = int(cm.group(1).translate(_FW2HW))
                except ValueError:
                    claim_num = None
        if claim_num is None:
            return None, None
        claim_s = str(claim_num)
        variants = [
            "【請求項" + claim_s.translate(_HW2FW) + "】",
            "【請求項" + claim_s + "】",
            "請求項" + claim_s.translate(_HW2FW),
            "請求項" + claim_s,
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


def _toc_dest(page_1based, y=0):
    page_1based = max(1, int(page_1based or 1))
    y = max(0, float(y or 0) - 18)
    return {
        "kind": fitz.LINK_GOTO,
        "page": page_1based - 1,
        "to": fitz.Point(0, y),
        "zoom": 0,
    }


def _is_generated_bookmark_title(title):
    t = str(title or "").strip()
    if not t:
        return True
    if t.startswith("注釈サマリー:") or t.startswith("対比結果:"):
        return True
    # アプリ生成の対比ブックマーク: "1A ○ 【0001】", "請求項2 × ..." 等。
    if re.match(r"^(?:請求項\s*\d+|[A-Za-z0-9][A-Za-z0-9_.-]*)\s*[○△×]", t):
        return True
    return False


def _extract_user_bookmarks_for_migration(pdf_path):
    """旧注釈PDFからユーザー作成らしいブックマークだけを取り出す。"""
    if not pdf_path:
        return []
    try:
        old = fitz.open(str(pdf_path))
    except Exception:
        return []
    try:
        toc = old.get_toc(simple=False)
    finally:
        old.close()

    user_entries = []
    for entry in toc or []:
        if len(entry) < 3:
            continue
        level, title, page = entry[:3]
        if _is_generated_bookmark_title(title):
            continue
        dest = entry[3] if len(entry) > 3 and isinstance(entry[3], dict) else None
        user_entries.append({
            "level": max(1, int(level or 1)),
            "title": str(title or "")[:200],
            "page": max(1, int(page or 1)),
            "dest": dest,
        })
    return user_entries


def _append_migrated_bookmarks(toc, migrated_bookmarks, page_count):
    if not migrated_bookmarks:
        return
    toc.append([1, "旧版から移行", 1, _toc_dest(1, 0)])
    min_level = min(int(e.get("level") or 1) for e in migrated_bookmarks)
    max_page = max(1, int(page_count or 1))
    for entry in migrated_bookmarks:
        page = min(max(1, int(entry.get("page") or 1)), max_page)
        title = str(entry.get("title") or "")[:200]
        dest = entry.get("dest")
        if isinstance(dest, dict):
            dest = dict(dest)
            dest["page"] = min(max(0, int(dest.get("page", page - 1))), max_page - 1)
        else:
            dest = _toc_dest(page, 0)
        toc.append([
            int(entry.get("level") or 1) - min_level + 2,
            title,
            page,
            dest,
        ])


def _iter_response_items_in_display_order(response, segments=None):
    """対比表と同じ請求項順・分節順で response 項目を返す。

    segments が無い場合は従来互換で comparisons → sub_claims の順。
    """
    comparisons = response.get("comparisons", []) or []
    sub_claims = response.get("sub_claims", []) or []

    if not segments:
        for comp in comparisons:
            rid = str(comp.get("requirement_id") or "").strip()
            yield {
                "label": rid or "?",
                "judgment": comp.get("judgment", ""),
                "cited_location": comp.get("cited_location", ""),
                "source": comp,
            }
        for sub in sub_claims:
            label = str(sub.get("requirement_id") or "").strip()
            if not label:
                label = f"請求項{sub.get('claim_number', '?')}"
            yield {
                "label": label,
                "judgment": sub.get("judgment", ""),
                "cited_location": sub.get("cited_location", ""),
                "source": sub,
            }
        return

    comp_by_req = {
        str(comp.get("requirement_id") or "").strip(): comp
        for comp in comparisons
        if str(comp.get("requirement_id") or "").strip()
    }
    sub_by_claim = {}
    sub_by_req = {}
    for sub in sub_claims:
        req = str(sub.get("requirement_id") or "").strip()
        if req:
            sub_by_req[req] = sub
        try:
            sub_by_claim[int(sub.get("claim_number"))] = sub
        except (TypeError, ValueError):
            pass

    seen_comp = set()
    seen_sub = set()

    for claim in segments or []:
        try:
            claim_number = int(claim.get("claim_number"))
        except (TypeError, ValueError):
            claim_number = None
        is_independent = bool(claim.get("is_independent")) or claim_number == 1
        if is_independent:
            for seg in claim.get("segments") or []:
                rid = str(seg.get("id") or "").strip()
                if not rid:
                    continue
                comp = comp_by_req.get(rid)
                if not comp:
                    continue
                seen_comp.add(rid)
                yield {
                    "label": rid,
                    "judgment": comp.get("judgment", ""),
                    "cited_location": comp.get("cited_location", ""),
                    "source": comp,
                }
        else:
            sub = None
            # 従属請求項も 5A のような requirement_id を持つ場合があるので優先的に引く。
            for seg in claim.get("segments") or []:
                rid = str(seg.get("id") or "").strip()
                if rid and rid in sub_by_req:
                    sub = sub_by_req[rid]
                    break
            if sub is None and claim_number is not None:
                sub = sub_by_claim.get(claim_number)
            if not sub:
                continue
            req = str(sub.get("requirement_id") or "").strip()
            label = req or f"請求項{sub.get('claim_number', claim_number or '?')}"
            seen_sub.add(id(sub))
            yield {
                "label": label,
                "judgment": sub.get("judgment", ""),
                "cited_location": sub.get("cited_location", ""),
                "source": sub,
            }

    # segments.json と応答がずれている場合も、残りを落とさない。
    for comp in comparisons:
        rid = str(comp.get("requirement_id") or "").strip()
        if rid and rid in seen_comp:
            continue
        yield {
            "label": rid or "?",
            "judgment": comp.get("judgment", ""),
            "cited_location": comp.get("cited_location", ""),
            "source": comp,
        }
    for sub in sub_claims:
        if id(sub) in seen_sub:
            continue
        label = str(sub.get("requirement_id") or "").strip()
        if not label:
            label = f"請求項{sub.get('claim_number', '?')}"
        yield {
            "label": label,
            "judgment": sub.get("judgment", ""),
            "cited_location": sub.get("cited_location", ""),
            "source": sub,
        }


def _build_toc_entries(response, doc, para_page_map, claims_page, segments=None):
    """ブックマーク対象を構築。ページだけでなく段落番号の y 座標も保持する。

    タイトル形式: `<requirement_id> <judgment> 【<para>】 (p.<page>)` または
                  `<requirement_id> <judgment> 請求項`
    """
    entries = []

    def _emit(label_prefix, judgment, cited_loc):
        paras = _parse_cited_paragraphs(cited_loc)
        emitted = False
        for para_id in paras:
            pn = rect = None
            claim_num = _claim_number_from_marker(para_id)
            if para_id == "__claims__" or claim_num is not None:
                pn, rect = _resolve_paragraph_location(
                    doc, para_id, cited_loc, para_page_map, claims_page)
                page = (pn + 1) if pn is not None else claims_page
                claim_label = f"請求項{claim_num}" if claim_num is not None else "請求項"
                title = f"{label_prefix} {judgment} {claim_label} (p.{page})"
            else:
                pn, rect = _resolve_paragraph_location(
                    doc, para_id, cited_loc, para_page_map, claims_page)
                page = (pn + 1) if pn is not None else para_page_map.get(para_id, 1)
                title = f"{label_prefix} {judgment} 【{para_id}】 (p.{page})"
            y = rect.y0 if rect is not None else 0
            entries.append({"level": 2, "title": title, "page": page, "y": y})
            emitted = True
        if not emitted:
            # cited_location があれば生のままタイトルに残し、ページは 1 にフォールバック
            label = cited_loc.strip() if cited_loc else "(不明)"
            entries.append({"level": 2, "title": f"{label_prefix} {judgment} {label}", "page": 1, "y": 0})

    for item in _iter_response_items_in_display_order(response, segments):
        _emit(item["label"], item["judgment"], item.get("cited_location", ""))

    return entries


def _build_toc(response, toc_entries, *, summary_page=None, page_offset=0):
    doc_id = response.get("document_id", "引用文献")
    summary_page = int(summary_page or 1)
    toc = [[1, f"対比結果: {doc_id}", 1, _toc_dest(1, 0)]]
    for entry in toc_entries:
        page = int(entry.get("page") or 1) + page_offset
        toc.append([
            int(entry.get("level") or 2),
            str(entry.get("title") or "")[:200],
            page,
            _toc_dest(page, entry.get("y") or 0),
        ])
    toc.append([1, f"注釈サマリー: {doc_id}", summary_page, _toc_dest(summary_page, 0)])
    return toc


# set_toc の detailed form で /XYZ の `to` を段落番号の y 座標にする。
# PDF-XChange 等ではページ先頭だけでなく、この座標が表示冒頭付近になる。
