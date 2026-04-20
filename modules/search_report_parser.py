#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WO（PCT）国際調査報告（ISR=PCT/ISA/210）/書面意見（WO-ISA=PCT/ISA/237）パーサ

入力: ISR または書面意見のPDFパス
出力:
  {
    "form": "ISR" | "WOSA",
    "language": "en" | "ja",
    "intl_app_no": "PCT/JP2024/012345",
    "citations": [   # ISR/Box C のみ
        {"num": 1, "category": "X", "doc_label": "WO 2019/107497 A1",
         "doc_id": "WO2019107497A1", "claims": "1-3",
         "passages": "paragraphs [0021]-[0035]"}
    ],
    "box_v": "...全文...",  # WOSA/Box V のみ
    "raw_text": "...全文..."
  }
"""

import re
import unicodedata
from pathlib import Path

import fitz  # PyMuPDF


# ===== 庁・様式判定 =====

_RE_FORM_210 = re.compile(r'PCT/ISA/210', re.I)
_RE_FORM_237 = re.compile(r'PCT/ISA/237', re.I)
_RE_FORM_409 = re.compile(r'PCT/IPEA/409', re.I)  # IPER

_RE_TITLE_ISR_EN = re.compile(r'INTERNATIONAL\s+SEARCH\s+REPORT', re.I)
_RE_TITLE_ISR_JA = re.compile(r'国際調査報告')
_RE_TITLE_WO_EN = re.compile(r'WRITTEN\s+OPINION\s+OF\s+THE\s+INTERNATIONAL', re.I)
_RE_TITLE_WO_JA = re.compile(r'国際調査機関の見解書|国際調査機関による見解書')
_RE_TITLE_IPER_EN = re.compile(r'INTERNATIONAL\s+PRELIMINARY\s+(?:EXAMINATION\s+)?REPORT', re.I)


def detect_form(text):
    """フォーム種別を判定。'ISR' / 'WOSA' / 'IPER' / None"""
    if _RE_FORM_210.search(text) or _RE_TITLE_ISR_EN.search(text) or _RE_TITLE_ISR_JA.search(text):
        return 'ISR'
    if _RE_FORM_237.search(text) or _RE_TITLE_WO_EN.search(text) or _RE_TITLE_WO_JA.search(text):
        return 'WOSA'
    if _RE_FORM_409.search(text) or _RE_TITLE_IPER_EN.search(text):
        return 'IPER'
    return None


def detect_language(text):
    """日英判定（極めて簡素）"""
    # 日本語ひらがな・カタカナ・漢字の比率で判定
    ja = sum(1 for ch in text[:5000] if '\u3040' <= ch <= '\u30ff' or '\u4e00' <= ch <= '\u9fff')
    if ja > 50:
        return 'ja'
    return 'en'


# ===== 国際出願番号 =====

_RE_INTL_APP_NO = re.compile(
    r'PCT[/／]\s*([A-Z]{2})\s*[/／]\s*(\d{4})\s*[/／]\s*(\d{4,6})',
    re.I
)


def extract_intl_app_no(text):
    m = _RE_INTL_APP_NO.search(text)
    if not m:
        return ""
    return f"PCT/{m.group(1).upper()}/{m.group(2)}/{m.group(3)}"


# ===== Box C（引用文献表） =====

_BOX_C_START_PATTERNS = [
    re.compile(r'C\s*\.\s*DOCUMENTS\s+CONSIDERED\s+TO\s+BE\s+RELEVANT', re.I),
    re.compile(r'[ＣC]\s*[．.]\s*関連すると認められる文献'),
]

_BOX_C_END_PATTERNS = [
    re.compile(r'Form\s+PCT[/／]ISA[/／]210', re.I),
    re.compile(r'(?:^|\n)\s*[DＤ]\s*[．.]\s*'),  # 次のセクション
    re.compile(r'Further\s+documents\s+are\s+listed', re.I),
]

# カテゴリ列：X / Y / A / E / O / P / T / & / I / L 等。半角・全角。
# "&" は continuation (前行と同じ文献の追加カテゴリ)
_CATEGORY_CHARS = 'XYAEOPTILxyaeopti&ＸＹＡＥＯＰＴＩｘｙａｅｏｐｔｉ＆'
_RE_CAT_LINE = re.compile(
    r'^\s*([' + _CATEGORY_CHARS + r'])\s{1,4}(.+)$'
)
_RE_CLAIM_NOS = re.compile(r'(\d+(?:[\-–,]\s*\d+)*)\s*$')


def _slice_box_c(text):
    """Box C 領域を全文から切り出す"""
    start = -1
    for pat in _BOX_C_START_PATTERNS:
        m = pat.search(text)
        if m:
            start = m.end()
            break
    if start < 0:
        return ""

    end = len(text)
    for pat in _BOX_C_END_PATTERNS:
        m = pat.search(text, start)
        if m and m.start() < end:
            end = m.start()
    return text[start:end]


def _normalize_cat(ch):
    """カテゴリ文字を半角大文字に正規化"""
    return unicodedata.normalize('NFKC', ch).upper()


def _extract_doc_id(label):
    """文献ラベルから正規化された番号IDを抽出。
    例: 'WO 2019/107497 A1'       → 'WO2019107497A1'
        'JP 2020-082440 A'         → 'JP2020082440A'
        'US 2018/0353420 A1'       → 'US20180353420A1'
        '特開2020-082440'          → 'JP2020082440A'
        '特表2025-542551'          → 'JP2025542551A'
        '特許第7401673号'          → 'JP7401673B'
    """
    s = unicodedata.normalize('NFKC', label)

    # 日本語表記（特開/特表/特願/再表/再公表）→ JP 形式
    m = re.search(r'特(?:開|表)\s*(\d{4})\s*[\-ー－]\s*(\d+)', s)
    if m:
        return f"JP{m.group(1)}{m.group(2).zfill(6)}A"
    m = re.search(r'特願\s*(\d{4})\s*[\-ー－]\s*(\d+)', s)
    if m:
        return f"JP{m.group(1)}{m.group(2).zfill(6)}"
    m = re.search(r'再(?:公)?表\s*(\d{4})\s*[\-ー－]\s*(\d+)', s)
    if m:
        return f"JP{m.group(1)}{m.group(2).zfill(6)}A"
    m = re.search(r'特許(?:第)?\s*(\d+)\s*(?:号)?', s)
    if m and not re.search(r'(特許請求|特許庁)', s):
        return f"JP{m.group(1)}B"

    # 英字表記（国コード）: スペース・ハイフン・スラッシュ混在の番号も許容
    # 例: "EP 3 719 056 A1" / "FR 3 088 205 A1" / "US 2018/0353420 A1"
    s_upper = s.upper()
    # OCR誤認補正: "A1" → "Al" / "AI" / "A|" 等を元に戻す（末尾種別のみ）
    s_upper = re.sub(r'([AB])[LI|!](?=\b|\d)', r'\g<1>1', s_upper)
    m = re.search(
        r'\b(WO|JP|US|EP|CN|KR|DE|GB|FR|CA|AU|TW)'
        r'[\s\-/]*'
        r'(\d[\d\s\-/]{2,25})'
        r'\s*([AB]\d?)?',
        s_upper
    )
    if not m:
        return ""
    cc, num_raw, kind = m.groups()
    digits = re.sub(r'[^\d]', '', num_raw)
    if not digits:
        return ""
    parts = [cc, digits]
    if kind:
        parts.append(kind)
    return "".join(parts)


def parse_box_c(box_text):
    """Box C テキスト → 引用リスト

    入力は Box C を切り出した生テキスト。
    出力: [{num, category, doc_label, doc_id, claims, passages}, ...]
    """
    citations = []
    if not box_text.strip():
        return citations

    lines = [ln.rstrip() for ln in box_text.splitlines()]
    current = None
    cit_num = 0

    for ln in lines:
        if not ln.strip():
            if current:
                citations.append(current)
                current = None
            continue

        m = _RE_CAT_LINE.match(ln)
        if m:
            cat = _normalize_cat(m.group(1))
            rest = m.group(2).strip()

            # クレーム番号（行末の数値群）を抽出
            claims = ""
            mc = _RE_CLAIM_NOS.search(rest)
            if mc:
                claims = mc.group(1).strip()
                rest = rest[:mc.start()].strip()

            if current:
                citations.append(current)
            cit_num += 1
            current = {
                "num": cit_num,
                "category": cat,
                "doc_label": rest,
                "doc_id": _extract_doc_id(rest),
                "claims": claims,
                "passages": "",
            }
        else:
            # 継続行：passages（引用箇所）として蓄積
            if current is not None:
                if current["passages"]:
                    current["passages"] += " " + ln.strip()
                else:
                    current["passages"] = ln.strip()

    if current:
        citations.append(current)

    return citations


_RE_COUNTRY_LINE = re.compile(
    r'^\s*(?:WO|JP|US|EP|CN|KR|DE|GB|FR|CA|AU|TW)\b[\s\-/]*\d'
)
_RE_DATE_HINT = re.compile(
    r'\(\d{4}-\d{2}-\d{2}\)|\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)',
    re.I
)


def parse_box_c_loose(box_text):
    """カテゴリ列がOCRで消えたISR Box C 用の緩いパーサ。

    各引用の先頭行を「国コードで始まる行」で識別し、継続行は日付/引用箇所として
    doc_label または passages に振り分ける。
    """
    citations = []
    if not box_text.strip():
        return citations
    lines = [ln.rstrip() for ln in box_text.splitlines()]
    current = None
    cit_num = 0

    for ln in lines:
        s = ln.strip()
        if not s:
            if current:
                citations.append(current)
                current = None
            continue

        if _RE_COUNTRY_LINE.match(s):
            # 新しい文献の先頭行
            claims = ""
            rest = s
            mc = _RE_CLAIM_NOS.search(s)
            if mc:
                claims = mc.group(1).strip()
                rest = s[:mc.start()].strip()
            if current:
                citations.append(current)
            cit_num += 1
            current = {
                "num": cit_num,
                "category": "",
                "doc_label": rest,
                "doc_id": _extract_doc_id(rest),
                "claims": claims,
                "passages": "",
            }
        else:
            if current is None:
                continue
            # 継続行：クレーム番号末尾なら取り込み、残りを passages へ
            content = s
            mc = _RE_CLAIM_NOS.search(s)
            if mc:
                if not current["claims"]:
                    current["claims"] = mc.group(1).strip()
                content = s[:mc.start()].strip()
            if not content:
                continue
            if _RE_DATE_HINT.search(content):
                # 出願人・日付行 → doc_label に結合
                current["doc_label"] += " " + content
                if not current["doc_id"]:
                    current["doc_id"] = _extract_doc_id(current["doc_label"])
            else:
                # 引用箇所
                current["passages"] = (current["passages"] + " " + content).strip() if current["passages"] else content

    if current:
        citations.append(current)
    return citations


# ===== Dn形式の引用文献リスト（IPER/書面意見でよく見る） =====

# 例: "D1 EP 3 719 056 A1 (SHINETSU CHEMICAL CO [JP]) 7 October 2020"
# 「Dn」の直後に必ず国コード+数字が来ることを要求（Box V本文の "D3 relates to..." を除外）
_COUNTRY_CC = r'(?:WO|JP|US|EP|CN|KR|DE|GB|FR|CA|AU|TW)'
_RE_D_LINE = re.compile(
    r'^\s*D\s*(\d+)\s*[:.]?\s+(' + _COUNTRY_CC + r'[\s\-/]*\d.+)$'
)


def parse_d_list(text):
    """'D1 ... / D2 ...' 形式の引用文献リストを抽出

    カテゴリ情報は文脈依存なので空のまま返す（後段の要約で判定する）。
    """
    citations = []
    if not text:
        return citations
    lines = text.splitlines()
    current = None

    for ln in lines:
        m = _RE_D_LINE.match(ln)
        if m:
            if current:
                citations.append(current)
            d_num = int(m.group(1))
            rest = m.group(2).strip()
            current = {
                "num": d_num,
                "category": "",
                "doc_label": rest,
                "doc_id": _extract_doc_id(rest),
                "claims": "",
                "passages": "",
            }
        elif current is not None and ln.strip():
            # 継続行（日付 `(2020-10-07)` など）をラベルに結合
            current["doc_label"] += " " + ln.strip()
            if not current["doc_id"]:
                current["doc_id"] = _extract_doc_id(current["doc_label"])

    if current:
        citations.append(current)
    return citations


_RE_REF_SECTION_START = re.compile(
    r'Reference\s+is\s+made\s+to\s+the\s+following\s+documents?\s*[:：]',
    re.I,
)
_RE_REF_SECTION_END_PATTERNS = [
    re.compile(r'\n\s*\n\s*\d+(?:\.\d+)?\s+[A-Z][a-z]'),  # "1 Independent claims" 等
    re.compile(r'\n\s*\n\s*(?:V|VI|VII|VIII)\.\s', re.I),
    re.compile(r'Form\s+PCT[/／]', re.I),
]


def _slice_reference_section(text):
    """`Reference is made to the following documents:` セクションを切り出す"""
    m = _RE_REF_SECTION_START.search(text)
    if not m:
        return ""
    start = m.end()
    end = len(text)
    for pat in _RE_REF_SECTION_END_PATTERNS:
        mm = pat.search(text, start + 5)
        if mm and mm.start() < end:
            end = mm.start()
    return text[start:end]


def _merge_citations(*lists):
    """複数の引用リストを doc_id でユニーク化してマージ"""
    seen = {}
    order = []
    next_num = 1
    for lst in lists:
        for c in lst:
            key = c.get("doc_id") or f"_label_{c.get('doc_label','')[:30]}"
            if key in seen:
                # カテゴリや引用箇所を補完
                existing = seen[key]
                if not existing.get("category") and c.get("category"):
                    existing["category"] = c["category"]
                if not existing.get("claims") and c.get("claims"):
                    existing["claims"] = c["claims"]
                if not existing.get("passages") and c.get("passages"):
                    existing["passages"] = c["passages"]
                continue
            merged = dict(c)
            merged["num"] = next_num
            next_num += 1
            seen[key] = merged
            order.append(key)
    return [seen[k] for k in order]


# ===== Box V（書面意見の理由付き陳述） =====

_BOX_V_START_PATTERNS = [
    re.compile(r'(?:Box\s*No\.?\s*)?V\s*\.\s*Reasoned\s+statement', re.I),
    re.compile(r'[ＶV]\s*[．.]\s*規則43の?2[．.]?1\(?a?\)?\(?i?\)?'),
    re.compile(r'[ＶV]\s*[．.]\s*.*?新規性.*?進歩性'),  # ゆるめ
]

_BOX_V_END_PATTERNS = [
    re.compile(r'(?:Box\s*No\.?\s*)?VI\s*\.\s*', re.I),
    re.compile(r'[ＶV][ＩI]\s*[．.]\s*'),
    re.compile(r'Form\s+PCT[/／]ISA[/／]237', re.I),
]


def extract_box_v(text):
    """Box V（書面意見の理由付き陳述）本文を抽出"""
    start = -1
    for pat in _BOX_V_START_PATTERNS:
        m = pat.search(text)
        if m:
            start = m.start()
            break
    if start < 0:
        return ""

    end = len(text)
    for pat in _BOX_V_END_PATTERNS:
        m = pat.search(text, start + 5)
        if m and m.start() < end:
            end = m.start()
    return text[start:end].strip()


# ===== Box C カテゴリ列のbbox抽出（画像PDF用） =====

def _extract_categories_by_bbox(pdf_path):
    """画像PDFのBox CページからカテゴリX/Y/Aをy昇順で抽出。

    Tesseractはテーブル構造を崩すが、bbox付きOCR (image_to_data) で
    「DOCUMENTS CONSIDERED 行より下、左端 x<Category列右端」にある
    単一文字トークン (X/Y/A/E/O/P/T/I/&) を拾うことでカテゴリを復元できる。
    """
    try:
        import pytesseract
        from pytesseract import Output
        from PIL import Image
    except ImportError:
        return []

    categories = []
    doc = fitz.open(str(pdf_path))
    try:
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(3.0, 3.0))
            img = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
            try:
                df = pytesseract.image_to_data(
                    img, lang='eng', config='--psm 6',
                    output_type=Output.DATAFRAME,
                )
            except Exception:
                continue
            df = df[df['text'].notna()].copy()
            if df.empty:
                continue
            df['text_s'] = df['text'].astype(str).str.strip()
            df = df[df['text_s'] != '']

            # DOCUMENTS CONSIDERED ヘッダ位置
            hdr = df[df['text_s'].str.contains(
                r'DOCUMENT|CONSIDERED', case=False, na=False, regex=True
            )]
            if hdr.empty:
                continue
            hdr_top = int(hdr.iloc[0]['top'])
            hdr_bottom = hdr_top + int(hdr.iloc[0]['height'])

            # Category ヘッダで列x範囲を決定
            cat_hdr = df[df['text_s'].str.contains('Category', case=False, na=False)]
            if not cat_hdr.empty:
                cl = int(cat_hdr.iloc[0]['left'])
                cw = int(cat_hdr.iloc[0]['width'])
                cat_left = max(0, cl - 30)
                cat_right = cl + cw + 30
                crop_top = int(cat_hdr.iloc[0]['top']) + int(cat_hdr.iloc[0]['height']) + 10
            else:
                cat_left, cat_right = 140, 320
                crop_top = hdr_bottom + 40

            # Category 列だけクロップしてOCR（ホワイトリストでカテゴリ文字に限定）
            crop = img.crop((cat_left, crop_top, cat_right, img.size[1] - 40))
            try:
                df_cat = pytesseract.image_to_data(
                    crop, lang='eng',
                    config='--psm 6 -c tessedit_char_whitelist=XYAEOPTI&xyaeopti',
                    output_type=Output.DATAFRAME,
                )
            except Exception:
                continue
            df_cat = df_cat[df_cat['text'].notna()].copy()
            if df_cat.empty:
                continue
            df_cat['text_s'] = df_cat['text'].astype(str).str.strip()
            df_cat = df_cat[df_cat['text_s'] != '']
            cat_tokens = df_cat[
                df_cat['text_s'].str.fullmatch(r'[XYAEOPTIxyaeopti&]', na=False) &
                (df_cat['conf'].astype(float) > 30)
            ]
            for _, t in cat_tokens.sort_values('top').iterrows():
                ch = unicodedata.normalize('NFKC', t['text_s']).upper()
                categories.append({
                    'category': ch,
                    'top': int(t['top']) + crop_top,  # 絶対座標
                    'conf': float(t['conf']),
                })
    finally:
        doc.close()
    return categories


def _apply_categories_to_citations(citations, categories):
    """カテゴリリストを引用リストに順番で割当（カテゴリ未設定の引用にのみ）"""
    if not citations or not categories:
        return citations
    # 重複した連続X (OCRノイズ) を1件とみなすため、前のtopから大きく離れたもののみ採用
    dedup = []
    prev_top = -999
    for c in categories:
        if c['top'] - prev_top < 20:  # 同一行の重複トークン
            continue
        dedup.append(c)
        prev_top = c['top']

    for cit, cat in zip(citations, dedup):
        if not cit.get('category'):
            cit['category'] = cat['category']
    return citations


# ===== トップレベル =====

def extract_text(pdf_path, ocr_lang='jpn+eng'):
    """PDFテキストを全ページ結合して返す。画像PDFは自動でOCRフォールバック。

    J-PlatPat のISR/IPERは画像PDFで提供されるためOCRが必須。
    """
    from modules.pdf_extractor import extract_text_from_pdf
    pages = extract_text_from_pdf(
        str(pdf_path), ocr_threshold=200, ocr_lang=ocr_lang
    )
    return "\n".join(p.get("text", "") for p in pages)


def parse_search_report(pdf_path):
    """ISR/書面意見PDFをパース"""
    text = extract_text(pdf_path)
    form = detect_form(text)
    lang = detect_language(text)
    intl_no = extract_intl_app_no(text)

    result = {
        "filename": Path(pdf_path).name,
        "form": form,
        "language": lang,
        "intl_app_no": intl_no,
        "citations": [],
        "box_v": "",
        "raw_text": text,
    }

    # Box C (X/Y/A形式) と D番号リストの両方を試してマージ
    box_c_text = _slice_box_c(text) if form else ""
    box_c_cits = parse_box_c(box_c_text)
    # Box Cで0件ならルーズパーサ（カテゴリ列OCR欠落のISR用）→ bboxで復元試行
    if not box_c_cits and box_c_text:
        box_c_cits = parse_box_c_loose(box_c_text)
        if box_c_cits and form == 'ISR':
            try:
                cats = _extract_categories_by_bbox(pdf_path)
                _apply_categories_to_citations(box_c_cits, cats)
            except Exception:
                pass
    # 「Reference is made to the following documents:」セクションを優先、
    # 無ければ全文からDn行を拾う（フォールバック）
    ref_text = _slice_reference_section(text) if form else ""
    d_cits = parse_d_list(ref_text) if ref_text else (parse_d_list(text) if form else [])
    result["citations"] = _merge_citations(box_c_cits, d_cits)

    if form in ('WOSA', 'IPER'):
        result["box_v"] = extract_box_v(text)

    return result


if __name__ == "__main__":
    # 簡易動作確認: python -m modules.search_report_parser <pdf>
    import sys
    import json as _json
    if len(sys.argv) < 2:
        print("usage: python search_report_parser.py <pdf>")
        sys.exit(1)
    res = parse_search_report(sys.argv[1])
    res.pop("raw_text", None)  # 出力簡略化
    print(_json.dumps(res, ensure_ascii=False, indent=2))
