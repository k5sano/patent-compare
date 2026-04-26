# -*- coding: utf-8 -*-
"""
PDF にアウトライン（しおり／ブックマーク）を付与する。
PyMuPDF (fitz) を使用。
"""

import fitz


def _view_to_point(view_str):
    """PDF 名前付き dest の view 文字列を (Point, zoom) に変換する。

    例: 'FitH,86' → (Point(0, 86), 0.0)
        'XYZ,12,34,1.5' → (Point(12, 34), 1.5)
        'Fit' → (Point(0, 0), 0.0)
    """
    if not view_str:
        return fitz.Point(0, 0), 0.0
    parts = str(view_str).split(",")
    typ = parts[0]
    args = []
    for x in parts[1:]:
        try:
            args.append(float(x))
        except ValueError:
            args.append(0.0)
    if typ == "XYZ":
        x = args[0] if len(args) > 0 else 0
        y = args[1] if len(args) > 1 else 0
        z = args[2] if len(args) > 2 else 0
        return fitz.Point(x, y), z
    if typ in ("FitH", "FitBH"):
        y = args[0] if len(args) > 0 else 0
        return fitz.Point(0, y), 0.0
    if typ in ("FitV", "FitBV"):
        x = args[0] if len(args) > 0 else 0
        return fitz.Point(x, 0), 0.0
    if typ == "FitR" and len(args) >= 4:
        return fitz.Point(args[0], args[3]), 0.0
    return fitz.Point(0, 0), 0.0


def _normalize_existing_dest(dest, page_1based):
    """get_toc(simple=False) の dest を set_toc が解釈できる形 (kind=1 GOTO) に正規化する。

    日本特許公報 PDF の TOC は kind=4 (LINK_NAMED) + view='FitH,N' になっており、
    そのまま set_toc に渡すと飛び先が失われ kind=0 page=-1 になってしまう。
    名前付き dest を解決して GOTO + Point に書き換えることでジャンプ先を保持する。
    """
    if not isinstance(dest, dict):
        return None
    kind = dest.get("kind", 1)
    if kind == 4:  # LINK_NAMED
        try:
            page_num = int(str(dest.get("page", page_1based)))
        except (TypeError, ValueError):
            page_num = max(0, page_1based - 1)
        pt, zoom = _view_to_point(dest.get("view", ""))
        return {"kind": 1, "page": page_num, "to": pt, "zoom": zoom}
    # 既に kind=1 (GOTO) 等の場合はそのまま使う
    return dest


def apply_toc(doc, bookmarks, *, preserve_existing=True, group_title="分節"):
    """開いている fitz.Document に TOC を適用する（in-place）。

    Args:
        doc: fitz.Document
        bookmarks: [{"title": "...", "page": 3}, ...]  page は 1-indexed
        preserve_existing: True なら既存 TOC を残し、新規 bookmarks を末尾に追加する。
                           その際、新規エントリは "group_title" 直下 (level=2) にぶら下げる。
        group_title: preserve_existing=True の時、新規追加分をまとめる level=1 ヘッダ名。

    Returns:
        新規に追加した TOC エントリ数 (level=1 ヘッダは含まない)
    """
    page_count = doc.page_count
    new_entries = []
    for bm in bookmarks:
        page = int(bm.get("page", 1))
        if page < 1:
            page = 1
        if page > page_count:
            page = page_count
        title = str(bm.get("title", ""))[:200] or "(no title)"
        new_entries.append([1, title, page])

    if preserve_existing:
        # 既存 TOC を simple=False で取得 (dest 情報が 4 番目に含まれる)
        # set_toc はリスト各要素を [level, title, page] または
        # [level, title, page, dest_dict] として受け付ける。
        # 元のジャンプ先 (位置・ズーム) を保持するため dest_dict を残して渡す。
        try:
            existing_full = doc.get_toc(simple=False) or []
        except Exception:
            existing_full = []
        # set_toc に渡す形式に正規化 (LINK_NAMED → LINK_GOTO 変換含む)
        existing_norm = []
        for e in existing_full:
            if not e or len(e) < 3:
                continue
            level, title, page = e[0], e[1], e[2]
            dest = e[3] if len(e) >= 4 else None
            dest = _normalize_existing_dest(dest, page) if dest else None
            entry = [level, title, page] + ([dest] if dest else [])
            existing_norm.append(entry)
        first_page = new_entries[0][2] if new_entries else 1
        # 新規 (level=2) エントリは page トップに飛ばす単純 dest で OK
        nested = [[2, e[1], e[2]] for e in new_entries]
        if new_entries:
            combined = existing_norm + [[1, group_title, first_page]] + nested
        else:
            combined = existing_norm
        doc.set_toc(combined)
    else:
        doc.set_toc(new_entries)
    return len(new_entries)


def add_bookmarks(src_pdf: str, out_pdf: str, bookmarks):
    """ファイル間で完結するブックマーク付与"""
    doc = fitz.open(src_pdf)
    n = apply_toc(doc, bookmarks)
    doc.save(out_pdf, garbage=3, deflate=True)
    doc.close()
    return n
