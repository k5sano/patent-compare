#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PDF→構造化テキスト抽出モジュール

対応フォーマット:
- 日本特許公報（段落番号【XXXX】形式）
- 米国特許（Col.X ln.Y形式）
- WO/EP公報（Paragraph [XXXX]形式）
"""

import re
import json
import fitz  # PyMuPDF
from pathlib import Path


# --- 段落番号の正規表現 ---
PARA_PATTERNS = {
    "JP": re.compile(r'【(\d{4})】'),
    "US": re.compile(r'Col\.?\s*(\d+)\s*,?\s*(?:ln|line)\.?\s*(\d+)'),
    "WO": re.compile(r'\[(\d{4})\]'),
}

# --- セクション判定キーワード（日本語） ---
SECTION_KEYWORDS = [
    (re.compile(r'技術分野'), "技術分野"),
    (re.compile(r'背景技術|従来の技術'), "背景技術"),
    (re.compile(r'発明が解決|課題'), "課題"),
    (re.compile(r'課題を解決|手段'), "手段"),
    (re.compile(r'発明の効果'), "効果"),
    (re.compile(r'実施の形態|実施形態|発明の詳細'), "実施形態"),
    (re.compile(r'実施例'), "実施例"),
    (re.compile(r'比較例'), "比較例"),
]

# --- セクション判定キーワード（英語 → 日本語名に統一） ---
SECTION_KEYWORDS_EN = [
    (re.compile(r'TECHNICAL FIELD|FIELD OF THE INVENTION', re.I), "技術分野"),
    (re.compile(r'BACKGROUND|PRIOR ART|RELATED ART', re.I), "背景技術"),
    (re.compile(r'SUMMARY OF THE INVENTION|SUMMARY', re.I), "手段"),
    (re.compile(r'BRIEF DESCRIPTION OF.*DRAWING', re.I), "図面"),
    (re.compile(r'DETAILED DESCRIPTION', re.I), "実施形態"),
    (re.compile(r'EXAMPLE(?:S)?(?!\s*\d)', re.I), "実施例"),
    (re.compile(r'COMPARATIVE EXAMPLE', re.I), "比較例"),
    (re.compile(r'CLAIMS', re.I), "請求項"),
]

# --- 請求項パターン ---
CLAIM_PATTERN_JP = re.compile(r'【請求項(\d+)】')
CLAIM_SECTION_START = re.compile(r'【特許請求の範囲】|【書類名】\s*特許請求の範囲')
CLAIM_SECTION_END = re.compile(r'【発明の詳細な説明】|【書類名】\s*明細書')

# --- 従属請求項の引用パターン ---
DEPENDENCY_PATTERN = re.compile(
    r'請求項(\d+)(?:から請求項(\d+))?(?:の(?:いずれか(?:一項)?)?に)?記載の'
    r'|請求項(\d+)(?:又は|または|もしくは)(?:請求項)?(\d+)に記載の'
    r'|請求項(\d+)に記載の'
)

# --- 英語請求項パターン ---
CLAIM_PATTERN_EN = re.compile(r'\n\s*(\d+)\.\s+')
CLAIM_SECTION_START_EN = re.compile(
    r'(?:What is claimed is|CLAIMS|The Claims|I claim|We claim)\s*:?\s*\n', re.I
)
DEPENDENCY_PATTERN_EN = re.compile(
    r'claim\s+(\d+)'
    r'|claims?\s+(\d+)\s*[-–to]+\s*(\d+)'
    r'|any\s+(?:one\s+)?of\s+claims\s+(\d+)',
    re.I,
)

# --- 表の検出パターン ---
TABLE_PATTERN = re.compile(r'【(表\d+)】|【(Table\s*\d+)】|\[(表\d+)\]|\[(Table\s*\d+)\]')


def detect_format(text):
    """特許公報のフォーマットを判定"""
    # 日本語段落番号 【XXXX】 があれば JP
    if re.search(r'【\d{4}】', text):
        return "JP"
    # US 特許: Col.X 形式 or CLAIMS セクション + "1. A/An" 形式
    if re.search(r'Col\.?\s*\d+', text):
        return "US"
    # WO/EP: [XXXX] 段落番号
    if re.search(r'\[\d{4}\]', text):
        return "WO"
    # 英語特許の一般検出: CLAIMS/ABSTRACT/DESCRIPTION 等のヘッダー
    if re.search(r'\b(?:CLAIMS|ABSTRACT|DETAILED DESCRIPTION|FIELD OF THE INVENTION)\b', text):
        return "US"
    return "JP"  # デフォルト


def extract_text_from_pdf(pdf_path, ocr_threshold=200, ocr_lang='jpn'):
    """PDFからページごとのテキストを抽出（画像ページはOCRフォールバック）

    Parameters:
        pdf_path: PDFファイルパス
        ocr_threshold: このバイト数以下のページはOCR対象とする
        ocr_lang: OCR言語 ('jpn' or 'eng')
    """
    import subprocess
    import tempfile
    import os

    doc = fitz.open(pdf_path)
    pages = []
    ocr_pages = []  # OCRが必要なページ番号リスト

    # 1st pass: 通常テキスト抽出 + OCR必要性の判定
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text")
        pages.append({"page": page_num + 1, "text": text})

        # OCR必要性判定:
        # - テキストが少ないページ（画像がメインの可能性）
        # - 【表X】ラベルがあるページ（表が画像化されている可能性が高い）
        content_text = re.sub(r'\s+', '', text)  # 空白除去後の文字数
        has_table_label = bool(re.search(r'【表\d+】', text))
        if len(content_text) < ocr_threshold or has_table_label:
            ocr_pages.append(page_num)

    # 2nd pass: 必要なページだけOCR
    if ocr_pages:
        for page_num in ocr_pages:
            ocr_text = _ocr_page(doc[page_num], page_num, lang=ocr_lang)
            if ocr_text and len(ocr_text.strip()) > len(pages[page_num]["text"].strip()):
                # OCR結果のほうが情報量が多ければ置換
                pages[page_num]["text"] = ocr_text
                pages[page_num]["ocr"] = True

    doc.close()
    return pages


def _ocr_page(page, page_num, lang='jpn'):
    """1ページをOCRしてテキストを返す

    Parameters:
        page: fitz.Page
        page_num: ページ番号（0-indexed）
        lang: Tesseract言語コード ('jpn' or 'eng')
    """
    import subprocess
    import tempfile
    import os

    try:
        mat = fitz.Matrix(2.5, 2.5)  # 高解像度で精度向上
        pix = page.get_pixmap(matrix=mat)
        img_path = os.path.join(tempfile.gettempdir(), f'patent_ocr_p{page_num}.png')
        pix.save(img_path)

        out_base = os.path.join(tempfile.gettempdir(), f'patent_ocr_out_p{page_num}')
        subprocess.run(
            ['tesseract', img_path, out_base, '-l', lang, '--psm', '6'],
            capture_output=True, timeout=60
        )
        out_file = out_base + '.txt'
        text = ""
        if os.path.exists(out_file):
            with open(out_file, 'r', encoding='utf-8') as f:
                text = f.read()
            os.unlink(out_file)
        if os.path.exists(img_path):
            os.unlink(img_path)
        return text
    except Exception:
        return None


def detect_patent_number(pages_text):
    """特許番号を検出"""
    full_text = "\n".join(p["text"] for p in pages_text[:3])

    patterns = [
        # J-PlatPatヘッダー形式: "JP  2024-32096  A  2024.3.12"
        re.compile(r'JP\s+(\d{4})\s*[-−]\s*(\d+)\s+(A|B\d?)\b'),
        # 日本語表記
        re.compile(r'(特開\d{4}[-−]\d{1,6})'),
        re.compile(r'(特許第\d+号)'),
        re.compile(r'(特願\d{4}[-−]\d{1,6})'),
        re.compile(r'(再公表WO\d{4}/\d{6})'),
        # 国際・外国
        re.compile(r'(WO\s*\d{4}/\d{6})'),
        re.compile(r'(US\s*\d{7,}[AB]?\d?)'),
        re.compile(r'(EP\s*\d{7,}[AB]?\d?)'),
        # ファイル名に含まれる形式: JP2024032096A
        re.compile(r'(JP\d{10,}[AB]?\d?)'),
    ]
    for pat in patterns:
        m = pat.search(full_text)
        if m:
            groups = m.groups()
            if len(groups) == 3 and groups[2] in ("A", "B1", "B2"):
                # J-PlatPatヘッダーの場合: "JP 2024-32096 A" → "特開2024-032096"
                year, num, kind = groups
                num_padded = num.zfill(6)
                if kind == "A":
                    return f"特開{year}-{num_padded}"
                elif kind.startswith("B"):
                    return f"特許{year}-{num_padded}"
            return groups[0].replace(" ", "")
    return None


def detect_patent_title(pages_text, pdf_path=None, fmt="JP"):
    """発明の名称を検出（テキスト抽出 → OCRフォールバック）"""
    full_text = "\n".join(p["text"] for p in pages_text[:5])

    # 日本語パターン（JP および翻訳文付き英語公報）
    title = _find_title_in_text(full_text)
    if title:
        return title

    # 英語パターン
    if fmt != "JP":
        title = _find_title_in_text_en(full_text)
        if title:
            return title

    # テキストで見つからない場合: OCRで1ページ目を読み取り
    if pdf_path:
        ocr_text = _ocr_first_pages(pdf_path, max_pages=1)
        if ocr_text:
            title = _find_title_in_text(ocr_text)
            if title:
                return title
            if fmt != "JP":
                title = _find_title_in_text_en(ocr_text)
                if title:
                    return title

    return None


def _clean_ocr_title(title):
    """OCR結果のタイトルからスペースを適切に除去"""
    # OCRでは日本語文字間にスペースが入ることがある
    # 日本語文字同士の間のスペースを除去
    title = re.sub(r'(?<=[\u3000-\u9fff\uff00-\uffef])\s+(?=[\u3000-\u9fff\uff00-\uffef])', '', title)
    # 全角括弧周辺のスペースも除去
    title = re.sub(r'\s*([（）])\s*', r'\1', title)
    return title.strip()


def _find_title_in_text(text):
    """テキストから発明の名称を検索"""
    # パターン1: 【発明の名称】の後（スペース許容版: OCR対応）
    m = re.search(r'[【\[]?\s*発\s*明\s*の\s*名\s*称\s*[】\]]?\s*(.+?)(?:\n[【\(（]|\n\()', text, re.DOTALL)
    if m:
        title = m.group(1).strip().replace('\n', ' ')
        title = _clean_ocr_title(title)
        if len(title) > 1:
            return title

    # パターン2: (54) の後（J-PlatPat形式）
    m = re.search(r'\(54\)\s*[【]?\s*発\s*明\s*の\s*名\s*称\s*[】]?\s*(.+?)(?:\n\(|\n【|\n\()', text, re.DOTALL)
    if m:
        title = m.group(1).strip().replace('\n', ' ')
        title = _clean_ocr_title(title)
        if len(title) > 1:
            return title

    # パターン3: 「発明の名称」の直後（柔軟マッチ）
    m = re.search(r'発明の名称[】\]\s]*\n?\s*(.+?)(?:\n|$)', text)
    if m:
        title = m.group(1).strip()
        title = _clean_ocr_title(title)
        if len(title) > 1 and not re.match(r'^[\(\(【]', title):
            return title

    return None


def _find_title_in_text_en(text):
    """英語テキストからタイトルを検索"""
    # パターン1: (54) の後の行（英語公報共通）
    m = re.search(r'\(54\)\s*(.+?)(?:\n\(|\n\n)', text, re.DOTALL)
    if m:
        title = m.group(1).strip().replace('\n', ' ')
        title = re.sub(r'\s+', ' ', title)
        if len(title) > 3:
            return title

    # パターン2: "Title:" の後
    m = re.search(r'Title\s*:\s*(.+?)(?:\n\n|\n[A-Z])', text, re.DOTALL)
    if m:
        title = m.group(1).strip().replace('\n', ' ')
        title = re.sub(r'\s+', ' ', title)
        if len(title) > 3:
            return title

    # パターン3: ABSTRACT 直前の大文字行
    m = re.search(r'\n\s*([A-Z][A-Z\s,\-]+[A-Z])\s*\n\s*ABSTRACT', text)
    if m:
        title = m.group(1).strip()
        # セクションヘッダーを除外
        if title not in ('CLAIMS', 'DESCRIPTION', 'DRAWINGS', 'ABSTRACT'):
            return title

    return None


def _ocr_first_pages(pdf_path, max_pages=1):
    """PDFの冒頭ページをOCRしてテキストを返す"""
    try:
        doc = fitz.open(str(pdf_path))
        ocr_texts = []
        for page_num in range(min(max_pages, len(doc))):
            text = _ocr_page(doc[page_num], page_num)
            if text:
                ocr_texts.append(text)
        doc.close()
        return "\n".join(ocr_texts)
    except Exception:
        return None


def classify_section(text, current_section, fmt="JP"):
    """段落テキストからセクション名を判定

    Parameters:
        text: 段落テキスト
        current_section: 現在のセクション名
        fmt: フォーマット ("JP" / "US" / "WO")
    """
    check_text = text[:80] if len(text) > 80 else text
    keywords = SECTION_KEYWORDS if fmt == "JP" else SECTION_KEYWORDS_EN
    for pattern, section_name in keywords:
        if pattern.search(check_text):
            return section_name
    # 英語フォーマットでも日本語キーワードが含まれる場合がある（翻訳文等）
    if fmt != "JP":
        for pattern, section_name in SECTION_KEYWORDS:
            if pattern.search(check_text):
                return section_name
    return current_section


def parse_claims_jp(full_text):
    """日本特許の請求項を抽出"""
    claims = []

    # 請求項セクションを特定
    claim_start = CLAIM_SECTION_START.search(full_text)
    claim_end = CLAIM_SECTION_END.search(full_text)

    if claim_start:
        claim_area = full_text[claim_start.end():]
        if claim_end and claim_end.start() > claim_start.start():
            claim_area = full_text[claim_start.end():claim_end.start()]
    else:
        claim_area = full_text

    # 個別の請求項を抽出
    claim_matches = list(CLAIM_PATTERN_JP.finditer(claim_area))
    for i, match in enumerate(claim_matches):
        claim_num = int(match.group(1))
        start = match.end()
        end = claim_matches[i + 1].start() if i + 1 < len(claim_matches) else len(claim_area)
        claim_text = claim_area[start:end].strip()
        # 改行を整理
        claim_text = re.sub(r'\s+', ' ', claim_text).strip()

        # 従属先を検出
        dependencies = _detect_dependencies(claim_text)
        is_independent = len(dependencies) == 0

        claims.append({
            "number": claim_num,
            "text": claim_text,
            "dependencies": dependencies,
            "is_independent": is_independent,
        })

    return claims


def parse_claims_en(full_text):
    """英語特許の請求項を抽出"""
    claims = []

    # CLAIMSセクションを特定
    claim_start = CLAIM_SECTION_START_EN.search(full_text)
    if claim_start:
        claim_area = full_text[claim_start.end():]
    else:
        # フォールバック: 末尾に "1. " パターンがあれば使う
        claim_area = full_text

    # 個別の請求項を抽出: "1. A method..." 形式
    claim_matches = list(CLAIM_PATTERN_EN.finditer(claim_area))
    if not claim_matches:
        return claims

    for i, match in enumerate(claim_matches):
        claim_num = int(match.group(1))
        start = match.end()
        end = claim_matches[i + 1].start() if i + 1 < len(claim_matches) else len(claim_area)
        claim_text = claim_area[start:end].strip()
        # 改行を整理
        claim_text = re.sub(r'\s+', ' ', claim_text).strip()

        # 従属先を検出
        dependencies = _detect_dependencies_en(claim_text)
        is_independent = len(dependencies) == 0

        claims.append({
            "number": claim_num,
            "text": claim_text,
            "dependencies": dependencies,
            "is_independent": is_independent,
        })

    return claims


def _detect_dependencies(claim_text):
    """請求項テキストから従属先の請求項番号を検出（日本語）"""
    deps = set()
    for m in DEPENDENCY_PATTERN.finditer(claim_text):
        groups = m.groups()
        for g in groups:
            if g is not None:
                deps.add(int(g))
    return sorted(deps)


def _detect_dependencies_en(claim_text):
    """英語請求項テキストから従属先の請求項番号を検出"""
    deps = set()
    for m in DEPENDENCY_PATTERN_EN.finditer(claim_text):
        groups = m.groups()
        for g in groups:
            if g is not None:
                try:
                    deps.add(int(g))
                except ValueError:
                    pass
    return sorted(deps)


def parse_paragraphs_jp(full_text, pages):
    """日本特許の段落を抽出"""
    paragraphs = []
    current_section = "実施形態"

    # ページごとの位置マッピング
    page_offsets = []
    offset = 0
    for p in pages:
        page_offsets.append({"page": p["page"], "start": offset, "end": offset + len(p["text"])})
        offset += len(p["text"]) + 1  # +1 for join \n

    # 段落番号で分割
    para_pattern = PARA_PATTERNS["JP"]
    matches = list(para_pattern.finditer(full_text))

    for i, match in enumerate(matches):
        para_id = match.group(1)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        para_text = full_text[start:end].strip()

        # セクション判定
        current_section = classify_section(para_text, current_section)

        # ページ特定
        page_num = 1
        for po in page_offsets:
            if po["start"] <= match.start() < po["end"]:
                page_num = po["page"]
                break

        # 改行を適度に整理（表形式は維持）
        if not _looks_like_table(para_text):
            para_text = re.sub(r'(?<!\n)\n(?!\n)', ' ', para_text)

        paragraphs.append({
            "id": para_id,
            "page": page_num,
            "section": current_section,
            "text": para_text.strip(),
        })

    return paragraphs


def parse_paragraphs_en(full_text, pages, fmt="US"):
    """英語特許の段落を抽出

    Parameters:
        full_text: 全ページ結合テキスト
        pages: ページデータリスト
        fmt: "US" or "WO"
    """
    paragraphs = []
    current_section = "実施形態"

    # ページごとの位置マッピング
    page_offsets = []
    offset = 0
    for p in pages:
        page_offsets.append({"page": p["page"], "start": offset, "end": offset + len(p["text"])})
        offset += len(p["text"]) + 1

    # US/WO: [XXXX] 段落番号パターン
    para_pattern = re.compile(r'\[(\d{4})\]')
    matches = list(para_pattern.finditer(full_text))

    if matches:
        # 段落番号あり
        for i, match in enumerate(matches):
            para_id = match.group(1)
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
            para_text = full_text[start:end].strip()

            # セクション判定（英語キーワード使用）
            current_section = classify_section(para_text, current_section, fmt=fmt)

            # ページ特定
            page_num = 1
            for po in page_offsets:
                if po["start"] <= match.start() < po["end"]:
                    page_num = po["page"]
                    break

            if not _looks_like_table(para_text):
                para_text = re.sub(r'(?<!\n)\n(?!\n)', ' ', para_text)

            paragraphs.append({
                "id": para_id,
                "page": page_num,
                "section": current_section,
                "text": para_text.strip(),
            })
    else:
        # 段落番号なし: セクションヘッダーで分割し、空行で段落分割して連番を振る
        # まずセクションヘッダーで大きく分割
        section_pattern = re.compile(
            r'\n\s*(TECHNICAL FIELD|FIELD OF THE INVENTION|BACKGROUND|PRIOR ART'
            r'|SUMMARY OF THE INVENTION|SUMMARY|BRIEF DESCRIPTION OF.*?DRAWINGS?'
            r'|DETAILED DESCRIPTION|EXAMPLES?|COMPARATIVE EXAMPLE|CLAIMS'
            r'|ABSTRACT)\s*\n',
            re.I,
        )
        para_num = 1
        # 空行2つ以上で段落分割
        raw_paras = re.split(r'\n\s*\n', full_text)
        for raw_para in raw_paras:
            text = raw_para.strip()
            if not text or len(text) < 5:
                continue

            # セクションヘッダー検出
            current_section = classify_section(text, current_section, fmt=fmt)

            # ヘッダーだけの段落はスキップ
            if section_pattern.fullmatch('\n' + text + '\n'):
                continue

            # ページ特定
            pos = full_text.find(raw_para)
            page_num = 1
            if pos >= 0:
                for po in page_offsets:
                    if po["start"] <= pos < po["end"]:
                        page_num = po["page"]
                        break

            if not _looks_like_table(text):
                text = re.sub(r'(?<!\n)\n(?!\n)', ' ', text)

            para_id = f"{para_num:04d}"
            paragraphs.append({
                "id": para_id,
                "page": page_num,
                "section": current_section,
                "text": text.strip(),
            })
            para_num += 1

    return paragraphs


def _looks_like_table(text):
    """テキストが表形式かどうかを簡易判定"""
    lines = text.split('\n')
    if len(lines) < 3:
        return False
    # タブ区切りやスペース区切りのパターン
    tab_lines = sum(1 for l in lines if '\t' in l)
    if tab_lines > len(lines) * 0.5:
        return True
    # 数値が多い行
    num_lines = sum(1 for l in lines if re.search(r'\d+\.\d+', l))
    if num_lines > len(lines) * 0.3:
        return True
    return False


def detect_tables(paragraphs):
    """段落データからテーブルを検出・抽出"""
    tables = []
    table_id = 0
    for para in paragraphs:
        if _looks_like_table(para["text"]) and len(para["text"]) > 50:
            table_id += 1
            tables.append({
                "id": f"表{table_id}",
                "page": para["page"],
                "section": para["section"],
                "paragraph_id": para["id"],
                "content": para["text"],
            })
    return tables


def extract_patent_pdf(pdf_path, doc_type="hongan"):
    """
    メインのPDF抽出関数

    Parameters:
        pdf_path: PDFファイルパス
        doc_type: "hongan" | "citation"

    Returns:
        構造化テキストのdict
    """
    pdf_path = Path(pdf_path)

    # PDF全ページテキスト抽出（1st pass: フォーマット判定用にデフォルト言語で）
    pages = extract_text_from_pdf(str(pdf_path))
    full_text = "\n".join(p["text"] for p in pages)

    # フォーマット判定
    fmt = detect_format(full_text)

    # 英語フォーマットの場合、OCR言語を切り替えて再抽出
    if fmt != "JP":
        pages = extract_text_from_pdf(str(pdf_path), ocr_lang='eng')
        full_text = "\n".join(p["text"] for p in pages)

    # 特許番号検出
    patent_number = detect_patent_number(pages)
    if patent_number is None:
        patent_number = pdf_path.stem

    # 発明の名称検出（テキストで見つからなければOCRにフォールバック）
    patent_title = detect_patent_title(pages, pdf_path=pdf_path, fmt=fmt)

    # 請求項・段落抽出（フォーマット別分岐）
    if fmt == "JP":
        claims = parse_claims_jp(full_text)
        paragraphs = parse_paragraphs_jp(full_text, pages)
    else:
        claims = parse_claims_en(full_text)
        paragraphs = parse_paragraphs_en(full_text, pages, fmt=fmt)

    # テーブル検出
    tables = detect_tables(paragraphs)

    result = {
        "file_name": pdf_path.stem,
        "file_type": doc_type,
        "patent_number": patent_number,
        "patent_title": patent_title,
        "format": fmt,
        "total_pages": len(pages),
        "claims": claims,
        "paragraphs": paragraphs,
        "tables": tables,
    }

    return result
