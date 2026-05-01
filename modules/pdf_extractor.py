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
CLAIM_PATTERN_WO = re.compile(r'\[Claim\s+(\d+)\]', re.I)  # WO形式: [Claim 1]
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


def extract_text_from_pdf(pdf_path, ocr_threshold=200, ocr_lang='jpn', max_workers=None):
    """PDFからページごとのテキストを抽出（画像ページはOCRフォールバック）

    OCRが必要なページは ThreadPoolExecutor で並列実行する。
    Tesseract 自体は `OMP_THREAD_LIMIT=1` を与えてシングルスレッド化し、
    プロセス数の制御をPython側に集約する。

    Parameters:
        pdf_path: PDFファイルパス
        ocr_threshold: このバイト数以下のページはOCR対象とする
        ocr_lang: OCR言語 ('jpn' or 'eng' / 'jpn+eng')
        max_workers: OCR並列度。None ならOCR対象ページ数と cpu_count の小さい方
    """
    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed

    doc = fitz.open(pdf_path)
    pages = []
    ocr_targets = []  # [(page_num, png_bytes), ...]

    # 1st pass: 通常テキスト抽出 + OCR必要性の判定。
    # OCR対象ページはここで pixmap→PNG bytes 化してメインスレッドで確定させる
    # （doc を別スレッドで触らないため）。
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text")
        pages.append({"page": page_num + 1, "text": text})

        content_text = re.sub(r'\s+', '', text)
        has_table_label = bool(re.search(r'【表\d+】', text))
        if len(content_text) < ocr_threshold or has_table_label:
            pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5))
            ocr_targets.append((page_num, pix.tobytes("png")))

    doc.close()

    if ocr_targets:
        if max_workers is None:
            max_workers = min(len(ocr_targets), os.cpu_count() or 4)
        max_workers = max(1, int(max_workers))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(_ocr_pixmap, pn, png, ocr_lang): pn
                for pn, png in ocr_targets
            }
            for fut in as_completed(futures):
                page_num = futures[fut]
                try:
                    ocr_text = fut.result(timeout=180)
                except Exception:
                    ocr_text = None
                if ocr_text and len(ocr_text.strip()) > len(pages[page_num]["text"].strip()):
                    pages[page_num]["text"] = ocr_text
                    pages[page_num]["ocr"] = True

    return pages


def _ocr_pixmap(page_num, png_bytes, lang='jpn'):
    """PNG バイト列を Tesseract に通してOCR（スレッドセーフ版）"""
    import subprocess
    import tempfile
    import os
    import uuid

    uid = uuid.uuid4().hex[:8]
    img_path = os.path.join(tempfile.gettempdir(), f'patent_ocr_p{page_num}_{uid}.png')
    out_base = os.path.join(tempfile.gettempdir(), f'patent_ocr_out_p{page_num}_{uid}')
    out_file = out_base + '.txt'
    try:
        with open(img_path, 'wb') as f:
            f.write(png_bytes)
        env = os.environ.copy()
        env.setdefault('OMP_THREAD_LIMIT', '1')
        subprocess.run(
            ['tesseract', img_path, out_base, '-l', lang, '--psm', '6'],
            capture_output=True, timeout=120, env=env,
        )
        if os.path.exists(out_file):
            with open(out_file, 'r', encoding='utf-8') as f:
                return f.read()
        return ""
    except Exception:
        return None
    finally:
        for p in (img_path, out_file):
            try:
                if os.path.exists(p):
                    os.unlink(p)
            except OSError:
                pass


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
            return groups[0].replace(" ", "").replace("/", "")
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

    # WO形式フォールバック: [Claim 1], [Claim 2], ...
    if not claim_matches:
        claim_matches = list(CLAIM_PATTERN_WO.finditer(claim_area))

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


_TABLE_HEADER_RE = re.compile(
    r"(?:【\s*表\s*\d+|〔\s*表\s*\d+|\[\s*表\s*\d+|表\s*\d+\s*[】〕\]]"
    r"|Table\s*\d+|TABLE\s*\d+)",
    re.IGNORECASE,
)

_CJK_CHAR_RE = re.compile(r"[一-龯ぁ-んァ-ヶー]")


def _is_ocr_garbled_table(text):
    """画像ベース表を OCR したときに発生する「孤立 1 文字漢字/かなの羅列」を検出。

    本物のテキスト表は単語 (2 文字以上) が並ぶが、画像 OCR 失敗時は単一 CJK 文字が
    空白区切りで並ぶ。実データで本物 vs 化けを分離するしきい値:
        単一 CJK 文字 token 率 > 40% → 化け
    例: '回 責 男 画 画 画 男' → 化け / '成分A 実施例1 実施例2' → 本物
    """
    tokens = text.split()
    if not tokens:
        return False
    single_cjk = sum(
        1 for tok in tokens if len(tok) == 1 and _CJK_CHAR_RE.match(tok)
    )
    return single_cjk / len(tokens) > 0.40


def detect_tables(paragraphs):
    """段落データから「実際の表」だけを検出する。

    フィルタ条件:
      1. 50 文字超
      2. `【表N】` 等の表ヘッダーを含む (誤検出された段落本文を弾く)
      3. `_looks_like_table` (タブ/数値多めの行) を満たす
      4. 単一 CJK 文字 token 率 ≤ 40% (画像表 OCR 化けを弾く)
    """
    tables = []
    table_id = 0
    for para in paragraphs:
        text = para["text"] or ""
        if len(text) <= 50:
            continue
        if not _TABLE_HEADER_RE.search(text):
            continue
        if not _looks_like_table(text):
            continue
        if _is_ocr_garbled_table(text):
            continue
        table_id += 1
        tables.append({
            "id": f"表{table_id}",
            "page": para["page"],
            "section": para["section"],
            "paragraph_id": para["id"],
            "content": text,
        })
    return tables


def _guess_format_from_filename(pdf_path):
    """ファイル名から特許フォーマットを推定（OCR言語選択のヒント用）"""
    stem = Path(pdf_path).stem.upper()
    if re.match(r'WO\s*\d', stem):
        return "WO"
    if re.match(r'EP\s*\d', stem):
        return "WO"  # EP も英語段落形式
    if re.match(r'US\s*\d', stem):
        return "US"
    # JPA, JPB, 特開, 特願 etc.
    if re.match(r'JP', stem) or '特' in stem:
        return "JP"
    return None


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

    # ファイル名からフォーマットヒントを取得
    filename_hint = _guess_format_from_filename(pdf_path)

    # ファイル名がWO/US/EPなら最初から英語OCRで抽出
    if filename_hint and filename_hint != "JP":
        first_lang = 'eng'
    else:
        first_lang = 'jpn'

    # PDF全ページテキスト抽出（1st pass）
    pages = extract_text_from_pdf(str(pdf_path), ocr_lang=first_lang)
    full_text = "\n".join(p["text"] for p in pages)

    # テキストがほぼ空の場合（スキャンPDF + OCR失敗）、逆の言語で再試行
    content_chars = len(re.sub(r'\s+', '', full_text))
    if content_chars < 100:
        alt_lang = 'eng' if first_lang == 'jpn' else 'jpn'
        pages = extract_text_from_pdf(str(pdf_path), ocr_lang=alt_lang)
        full_text = "\n".join(p["text"] for p in pages)

    # フォーマット判定（テキスト内容から）
    fmt = detect_format(full_text)

    # テキスト判定がデフォルトJPだがファイル名は英語系の場合、ファイル名を優先
    if fmt == "JP" and filename_hint and filename_hint != "JP":
        fmt = filename_hint

    # フォーマットが英語系で、1st passがjpn OCRだった場合は英語OCRで再抽出
    if fmt != "JP" and first_lang == 'jpn':
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
