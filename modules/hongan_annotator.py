# -*- coding: utf-8 -*-
"""
本願PDF上に分節ID（例: "1A"）ラベルを描画する。
- 請求項部の該当分節テキスト先頭に赤ラベル
- 関連段落（【XXXX】）に青ラベル
"""

import re
import fitz


_FW_DIGITS = str.maketrans("0123456789", "０１２３４５６７８９")

CLAIM_COLOR = (0.84, 0.16, 0.16)   # 赤系 (請求項側ラベル: FreeText 注釈で移動可能)
PARA_COLOR = (0.13, 0.50, 0.81)    # 青系 (段落側ラベル: 塗り+白文字、ベタ描画)


def _to_fullwidth_digits(s: str) -> str:
    return s.translate(_FW_DIGITS)


_CLAIM_SECTION_START_MARKERS = (
    "【特許請求の範囲】", "特許請求の範囲",
)
# 請求の範囲セクションの「終わり」を示す次セクション見出し
_CLAIM_SECTION_END_MARKERS = (
    "【発明の詳細な説明】", "発明の詳細な説明",
    "【発明の概要】", "発明の概要",
    "【技術分野】", "【背景技術】",
)


def _find_claim_section_start(doc):
    """『特許請求の範囲』のセクション先頭ページ番号 (0-indexed) を返す。"""
    for pi in range(doc.page_count):
        for marker in _CLAIM_SECTION_START_MARKERS:
            try:
                if doc[pi].search_for(marker):
                    return pi
            except Exception:
                continue
    return 0


def _find_claim_section_range(doc):
    """『特許請求の範囲』〜『発明の詳細な説明』直前 までのページ範囲 (start, end) を返す。
    見つからなければ全ページ。範囲は inclusive。"""
    start = _find_claim_section_start(doc)
    end = doc.page_count - 1
    for pi in range(start, doc.page_count):
        for marker in _CLAIM_SECTION_END_MARKERS:
            try:
                rects = doc[pi].search_for(marker)
            except Exception:
                rects = []
            if rects:
                # マーカーが claim 開始ページ自身にある場合 (= 同ページに発明の詳細...)
                # でも end=start で OK (claim 開始マーカーの方が上にあるはず)
                return (start, pi)
    return (start, end)


def _normalize_for_match(s):
    """マッチ用正規化: 空白・タブ・改行除去 + 全角 ASCII (英数記号) → 半角

    全角範囲 U+FF01〜U+FF5E (! 〜 ~) を一括で半角に変換。
    対象: 数字 (０-９) / アルファベット (Ａ-Ｚ ａ-ｚ) / 記号 (．，／－＋％ 等)
    """
    if not s:
        return ""
    t = re.sub(r"\s+", "", s)
    out = []
    for ch in t:
        cp = ord(ch)
        if 0xFF01 <= cp <= 0xFF5E:
            out.append(chr(cp - 0xFF01 + 0x21))
        else:
            out.append(ch)
    return "".join(out)


def _search_claim_head_near(doc, seg_text, *, claim_start_page=0, claim_end_page=None,
                              near_page=0, near_y=0.0,
                              min_len=5, max_len=28):
    """ページ単語を抽出して空白・全半角差を吸収した上でマッチを探す。

    日本特許公報は本文中に各文字間スペースが入ることがあり、`page.search_for`
    だけでは「HLB値」と「H L B 値」の差を吸収できないため、
    `page.get_text("words")` で語単位に取り、結合した文字列を正規化して
    部分文字列検索する。各文字 → 該当語の rect の対応を保持し、ヒット位置を rect で返す。
    """
    if not seg_text:
        return None, None
    target = _normalize_for_match(seg_text)[:max_len]
    if len(target) < min_len:
        return None, None
    if claim_end_page is None:
        claim_end_page = doc.page_count - 1
    pages = list(range(claim_start_page, claim_end_page + 1))
    if not pages:
        pages = [0]

    # 各ページの単語列・正規化テキスト・char→word マッピングを構築 (1 度だけ)
    page_corpus = []  # list of (pi, full_norm_text, char_to_word_idx, words_list)
    for pi in pages:
        try:
            words = doc[pi].get_text("words")
        except Exception:
            continue
        full = ""
        c2w = []
        for wi, w in enumerate(words):
            wt = _normalize_for_match(w[4])
            for _ in wt:
                c2w.append(wi)
            full += wt
        page_corpus.append((pi, full, c2w, words))

    # 部分文字列マッチ。head を 1 文字ずつ縮めながら最初にヒットする長さを採用。
    candidates = []
    for n in range(len(target), min_len - 1, -1):
        token = target[:n]
        if not token:
            continue
        for pi, full, c2w, words in page_corpus:
            start = 0
            while True:
                idx = full.find(token, start)
                if idx < 0:
                    break
                wi = c2w[idx] if idx < len(c2w) else None
                if wi is not None:
                    w = words[wi]
                    candidates.append((pi, fitz.Rect(w[0], w[1], w[2], w[3])))
                start = idx + 1
        if candidates:
            break
    if not candidates:
        return None, None

    def _cost(c):
        pi, r = c
        if pi < near_page:
            return (10_000_000 + (near_page - pi) * 1000 + r.y0, 0)
        if pi == near_page:
            if r.y0 >= near_y - 0.5:  # 同行上を許容するため微小な許容
                return (r.y0 - near_y, 0)
            return (1_000_000 + (near_y - r.y0), 0)
        return ((pi - near_page) * 10_000 + r.y0, 0)

    return min(candidates, key=_cost)


def _search_claim_head(doc, seg_text, min_len=10, max_len=24, claim_start_page=0):
    """分節テキストの先頭を使い、claim_start_page 以降のページで最初の一致を返す。

    要約 (1ページ目) 等に同じフレーズが出ても、claim 本体側を優先するため、
    検索開始ページを claim_start_page にずらす。
    """
    if not seg_text:
        return None, None
    head = re.sub(r"\s+", "", seg_text)[:max_len]
    if len(head) < min_len:
        return None, None
    page_order = list(range(claim_start_page, doc.page_count)) + list(range(0, claim_start_page))
    for n in range(len(head), min_len - 1, -2):
        for pi in page_order:
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


def _add_movable_label_annot(page, anchor_rect, label, rgb):
    """PDF-XChange 等で移動・編集できる FreeText 注釈としてラベルを追加する。

    請求項本文上に重ねたい場合、`page.draw_rect` + `insert_text` だと
    PDF のページ自体に焼き付けられて移動できないので、annotation として登録する。
    """
    fs = 9
    w = max(22.0, 5.5 * len(label) + 8)
    h = 13.0
    x0 = anchor_rect.x0 - w - 2
    if x0 < 5:
        x0 = min(anchor_rect.x1 + 2, page.rect.width - w - 2)
    y0 = max(5, anchor_rect.y0)
    box = fitz.Rect(x0, y0, x0 + w, y0 + h)

    try:
        # 表示テキストは label そのもの (例: "1A")。/Contents に入る。
        annot = page.add_freetext_annot(
            box, label,
            fontsize=fs, fontname="helv",
            align=0,
        )
        # PDF-XChange のサイドバー識別用 (ここで content を上書きしないこと —
        # /Contents = 表示文字列 なので "分節 1A" を入れると表示が分節に変わる)
        try:
            annot.set_info(title="patent-compare", subject=f"分節 {label}")
        except Exception:
            pass
        try:
            annot.set_border(width=0.5)
        except Exception:
            pass
        # PyMuPDF 1.26 では FreeText に対する set_colors() が ValueError を投げる。
        # 色は update() に明示で渡さないと appearance stream が黒文字＋背景なし
        # で生成され、ページ上に何も見えなくなる。
        annot.update(
            fontsize=fs,
            text_color=(1, 1, 1),
            fill_color=rgb,
            opacity=1.0,
        )
        # update() は /AP (appearance stream) と /C (border color) を書くが
        # /IC (Interior Color = 塗りつぶし色) を annotation dict に書かない。
        # PDF-XChange Editor のプロパティダイアログは /IC を読むので、書かないと
        # 「塗りつぶし=白」と表示される (ユーザーが手動で赤に変更すると正常になる)。
        # /IC を直接 xref に書き込んで appearance と整合させる。
        try:
            ic_str = f"[{rgb[0]} {rgb[1]} {rgb[2]}]"
            page.parent.xref_set_key(annot.xref, "IC", ic_str)
        except Exception:
            pass
    except Exception:
        # FreeText 注釈生成失敗時は従来のベタ描画にフォールバック
        page.draw_rect(box, color=rgb, fill=rgb, width=0.5, overlay=True)
        page.insert_text(
            fitz.Point(box.x0 + 3, box.y1 - 3),
            label, fontsize=fs, color=(1, 1, 1),
            fontname="helv", overlay=True,
        )


def _draw_label(page, anchor_rect, label, rgb, *, style="filled",
                  text_alpha=1.0, stroke_alpha=1.0, slot=0):
    """ラベルを描画する。

    Args:
        style: "filled" (塗りつぶし＋白抜き文字) — 段落側のデフォルト。
               "outline" (枠線のみ＋同色文字) — 請求項側、本文と重なる場合用。
        text_alpha: 文字の不透明度 (style='outline' の場合に効く)。
        stroke_alpha: 枠線の不透明度。
        slot: 同一 anchor に複数ラベルを並べる時の通し番号 (0,1,2,...)。
              優先して左方向に積み、左マージンが足りなくなったら下方向にずらす。
    """
    fs = 9
    w = max(22.0, 5.5 * len(label) + 8)
    h = 13.0
    gap = 2
    base_x0 = anchor_rect.x0 - w - gap
    x0 = base_x0 - slot * (w + gap)
    y0 = max(5, anchor_rect.y0)
    if x0 < 5:
        # 左マージンを使い切ったら、slot=0 と同じ x に戻して下方向に積む
        x0 = base_x0
        if x0 < 5:
            x0 = min(anchor_rect.x1 + gap, page.rect.width - w - 2)
        y0 = max(5, anchor_rect.y0) + slot * (h + gap)
    box = fitz.Rect(x0, y0, x0 + w, y0 + h)

    if style == "outline":
        # 枠線のみ (塗りつぶしなし) + 文字色は同色 (text_alpha 適用)
        page.draw_rect(
            box, color=rgb, fill=None, width=0.7, overlay=True,
            stroke_opacity=stroke_alpha,
        )
        page.insert_text(
            fitz.Point(box.x0 + 3, box.y1 - 3),
            label, fontsize=fs, color=rgb,
            fontname="helv", overlay=True,
            fill_opacity=text_alpha,
        )
    else:
        # 塗りつぶし + 白抜き文字
        page.draw_rect(
            box, color=rgb, fill=rgb, width=0.5, overlay=True,
        )
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

    # 請求項側: 「【特許請求の範囲】」〜「【発明の詳細な説明】」直前 までに範囲を限定
    claim_start_page, claim_end_page = _find_claim_section_range(doc)

    # 直前のヒット位置を覚えて、順次性を保つことで「飛び散り」を防ぐ。
    # near_page/near_y が呼び出しごとに変わるため cache はせず毎回検索する。
    last_page = claim_start_page
    last_y = 0.0
    for item in claim_items:
        stext = item.get("seg_text", "")
        result = _search_claim_head_near(
            doc, stext,
            claim_start_page=claim_start_page,
            claim_end_page=claim_end_page,
            near_page=last_page, near_y=last_y,
        )
        page_idx, rect = result if result else (None, None)
        if page_idx is None:
            continue
        # 請求項側は移動可能な FreeText 注釈として追加 (PDF-XChange 等で
        # ドラッグ移動・編集・削除ができる)。色は赤、塗りつぶしなし、枠線あり。
        _add_movable_label_annot(doc[page_idx], rect, item["seg_id"], CLAIM_COLOR)
        n += 1
        # 次の分節は通常この近傍に来るのでカーソルを進める
        last_page = page_idx
        last_y = rect.y0

    # 関連段落側
    # 同じ (page, para_id) に複数の分節 ID が紐づくと、_search_para_marker が
    # 同じ rect を返してラベルが上書きされる。para_slots で配置済みの個数を
    # 数えて _draw_label に slot 番号を渡し、左方向 (足りなければ下方向) に
    # ずらして積む。
    fallback_slots = {}
    para_slots = {}
    for item in para_items:
        page_num = int(item.get("page") or 1)
        if not (1 <= page_num <= doc.page_count):
            continue
        page = doc[page_num - 1]
        para_id = item.get("para_id", "")
        rect = _search_para_marker(page, para_id)
        slot = 0
        if rect is None:
            fb = fallback_slots.get(page_num, 0)
            y = 18 + fb * 15
            rect = fitz.Rect(10, y, 40, y + 12)
            fallback_slots[page_num] = fb + 1
        else:
            slot = para_slots.get((page_num, para_id), 0)
            para_slots[(page_num, para_id)] = slot + 1
        _draw_label(page, rect, item["seg_id"], PARA_COLOR, slot=slot)
        n += 1
    return n
