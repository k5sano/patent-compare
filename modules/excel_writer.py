#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
対比表Excel出力モジュール

テンプレートに準拠した以下のセクションを含むExcelを生成:
1. ヘッダ情報（目的文、案件番号、凡例）
2. 請求項1の対比表
3. 従属請求項の対比表
4. 文献リスト
5. 拒絶理由構成の方針
"""

from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import (
    Font, PatternFill, Alignment, Border, Side,
    numbers
)
from openpyxl.utils import get_column_letter


# --- スタイル定義 ---
FONT_TITLE = Font(name="游ゴシック", size=14, bold=True)
FONT_HEADER = Font(name="游ゴシック", size=10, bold=True)
FONT_NORMAL = Font(name="游ゴシック", size=9)
FONT_SMALL = Font(name="游ゴシック", size=8)
FONT_JUDGMENT = Font(name="游ゴシック", size=12, bold=True)

FILL_GREEN = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")   # ○
FILL_YELLOW = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")  # △
FILL_RED = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")     # ×
FILL_HEADER = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
FILL_HEADER_LIGHT = PatternFill(start_color="D9E2F3", end_color="D9E2F3", fill_type="solid")
FILL_SECTION = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")

FONT_WHITE = Font(name="游ゴシック", size=10, bold=True, color="FFFFFF")

ALIGNMENT_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
ALIGNMENT_LEFT = Alignment(horizontal="left", vertical="top", wrap_text=True)
ALIGNMENT_LEFT_CENTER = Alignment(horizontal="left", vertical="center", wrap_text=True)

THIN_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)


def _get_judgment_fill(judgment):
    """判定に応じた背景色を返す"""
    if judgment == "○":
        return FILL_GREEN
    elif judgment == "△":
        return FILL_YELLOW
    elif judgment == "×":
        return FILL_RED
    return None


def _set_cell(ws, row, col, value, font=None, fill=None, alignment=None, border=None):
    """セルに値とスタイルを設定"""
    cell = ws.cell(row=row, column=col, value=value)
    if font:
        cell.font = font
    if fill:
        cell.fill = fill
    if alignment:
        cell.alignment = alignment
    if border:
        cell.border = border
    return cell


def write_comparison_table(output_path, case_meta, segments, responses, citations_meta=None):
    """対比表Excelを生成

    Parameters:
        output_path: 出力ファイルパス
        case_meta: 案件メタデータ (case.yaml)
        segments: 請求項分節データ (segments.json)
        responses: {citation_id: response_data} のdict
        citations_meta: {citation_id: citation_json} のdict（任意）
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "対比表"

    citations_meta = citations_meta or {}
    case_id = case_meta.get("case_id", "")
    patent_number = case_meta.get("patent_number", case_id)
    patent_title = case_meta.get("patent_title", case_meta.get("title", ""))

    # 引用文献の順序（case.yamlのcitations順）
    citation_order = []
    for cit in case_meta.get("citations", []):
        cid = cit["id"]
        if cid in responses:
            citation_order.append(cid)

    num_citations = len(citation_order)
    if num_citations == 0:
        # 回答がない場合は空の対比表
        citation_order = list(responses.keys())
        num_citations = len(citation_order)

    # 列幅設定
    ws.column_dimensions["A"].width = 6    # 構成要件ID
    ws.column_dimensions["B"].width = 45   # 構成要件テキスト
    for i in range(num_citations):
        col_letter = get_column_letter(3 + i)
        ws.column_dimensions[col_letter].width = 40

    row = 1

    # ===== セクション1: ヘッダ情報 =====
    # 目的文
    ws.merge_cells(start_row=row, start_column=1,
                   end_row=row, end_column=2 + num_citations)
    _set_cell(ws, row, 1,
              "本書は、日本特許法第29条に基づく拒絶理由の構成を目的とした "
              "先行技術文献との対比表です。出願日以前の公知文献のみを対象とします。",
              font=FONT_SMALL, alignment=ALIGNMENT_LEFT_CENTER)
    row += 1

    # 案件情報
    ws.merge_cells(start_row=row, start_column=1,
                   end_row=row, end_column=2 + num_citations)
    _set_cell(ws, row, 1, f"{patent_number}　{patent_title}　請求項分節・対比表",
              font=FONT_TITLE, alignment=ALIGNMENT_LEFT_CENTER)
    row += 2

    # 凡例
    _set_cell(ws, row, 1, "凡例:", font=FONT_HEADER)
    _set_cell(ws, row, 2, "○＝一致（同一又は実質同一の構成が開示）", font=FONT_NORMAL,
              fill=FILL_GREEN)
    row += 1
    _set_cell(ws, row, 2, "△＝部分一致（上位概念での開示、数値範囲の一部重複等）",
              font=FONT_NORMAL, fill=FILL_YELLOW)
    row += 1
    _set_cell(ws, row, 2, "×＝不一致（対応する記載なし）", font=FONT_NORMAL,
              fill=FILL_RED)
    row += 2

    # ===== セクション2: 請求項1の対比表 =====
    claim1 = None
    sub_claims = []
    for claim in segments:
        if claim["claim_number"] == 1:
            claim1 = claim
        else:
            sub_claims.append(claim)

    if claim1 is None:
        # 請求項1がない場合は最初の独立請求項を使用
        for claim in segments:
            if claim.get("is_independent"):
                claim1 = claim
                break

    if claim1:
        row = _write_claim_comparison(ws, row, claim1, citation_order,
                                       responses, citations_meta, case_meta)
        row += 2

    # ===== セクション3: 従属請求項の対比表 =====
    if sub_claims:
        ws.merge_cells(start_row=row, start_column=1,
                       end_row=row, end_column=2 + num_citations)
        _set_cell(ws, row, 1, "従属請求項", font=FONT_TITLE, fill=FILL_SECTION)
        row += 1

        row = _write_sub_claims_table(ws, row, sub_claims, citation_order,
                                       responses, citations_meta)
        row += 2

    # ===== セクション4: 文献リスト =====
    ws.merge_cells(start_row=row, start_column=1,
                   end_row=row, end_column=2 + num_citations)
    _set_cell(ws, row, 1, "拒絶理由構成のための先行技術文献調査結果",
              font=FONT_TITLE, fill=FILL_SECTION)
    row += 1

    row = _write_document_list(ws, row, citation_order, responses,
                                citations_meta, case_meta, num_citations)
    row += 2

    # ===== セクション5: 拒絶理由構成の方針 =====
    ws.merge_cells(start_row=row, start_column=1,
                   end_row=row, end_column=2 + num_citations)
    _set_cell(ws, row, 1, "拒絶理由構成の方針（分析コメント）",
              font=FONT_TITLE, fill=FILL_SECTION)
    row += 1

    row = _write_rejection_strategy(ws, row, citation_order, responses, num_citations)

    # 出力ディレクトリを作成
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # 保存
    wb.save(output_path)


def _write_claim_comparison(ws, row, claim, citation_order, responses, citations_meta, case_meta):
    """請求項の対比表を書き込む"""
    num_citations = len(citation_order)

    # ヘッダ行: 構成要件 | 請求項X | 文献1 | 文献2 | ...
    _set_cell(ws, row, 1, "ID", font=FONT_WHITE, fill=FILL_HEADER,
              alignment=ALIGNMENT_CENTER, border=THIN_BORDER)
    _set_cell(ws, row, 2, f"請求項{claim['claim_number']} 構成要件",
              font=FONT_WHITE, fill=FILL_HEADER,
              alignment=ALIGNMENT_CENTER, border=THIN_BORDER)

    for i, cit_id in enumerate(citation_order):
        cit_meta = citations_meta.get(cit_id, {})
        cit_info = None
        for c in case_meta.get("citations", []):
            if c["id"] == cit_id:
                cit_info = c
                break
        label = cit_info["label"] if cit_info else cit_id
        role = cit_info.get("role", "") if cit_info else ""
        header_text = f"{label}\n({cit_id})\n{role}"
        _set_cell(ws, row, 3 + i, header_text,
                  font=FONT_WHITE, fill=FILL_HEADER,
                  alignment=ALIGNMENT_CENTER, border=THIN_BORDER)
    row += 1

    # 各構成要件
    for seg in claim["segments"]:
        seg_id = seg["id"]
        seg_text = seg["text"]

        # 判定行
        _set_cell(ws, row, 1, seg_id, font=FONT_HEADER,
                  alignment=ALIGNMENT_CENTER, border=THIN_BORDER,
                  fill=FILL_HEADER_LIGHT)
        _set_cell(ws, row, 2, seg_text, font=FONT_NORMAL,
                  alignment=ALIGNMENT_LEFT, border=THIN_BORDER)

        for i, cit_id in enumerate(citation_order):
            resp = responses.get(cit_id, {})
            comp = _find_comparison(resp, seg_id)
            if comp:
                judgment = comp.get("judgment", "")
                fill = _get_judgment_fill(judgment)
                _set_cell(ws, row, 3 + i, judgment,
                          font=FONT_JUDGMENT, fill=fill,
                          alignment=ALIGNMENT_CENTER, border=THIN_BORDER)
            else:
                _set_cell(ws, row, 3 + i, "-",
                          alignment=ALIGNMENT_CENTER, border=THIN_BORDER)
        row += 1

        # 理由行
        _set_cell(ws, row, 1, "", border=THIN_BORDER)
        _set_cell(ws, row, 2, "", border=THIN_BORDER)

        for i, cit_id in enumerate(citation_order):
            resp = responses.get(cit_id, {})
            comp = _find_comparison(resp, seg_id)
            if comp:
                reason_parts = []
                if comp.get("judgment_reason"):
                    reason_parts.append(comp["judgment_reason"])
                if comp.get("cited_location"):
                    reason_parts.append(f"[{comp['cited_location']}]")
                if comp.get("cited_text"):
                    reason_parts.append(f"「{comp['cited_text'][:100]}」")
                reason_text = "\n".join(reason_parts)
                _set_cell(ws, row, 3 + i, reason_text,
                          font=FONT_SMALL, alignment=ALIGNMENT_LEFT,
                          border=THIN_BORDER)
            else:
                _set_cell(ws, row, 3 + i, "", border=THIN_BORDER)
        row += 1

    return row


def _write_sub_claims_table(ws, row, sub_claims, citation_order, responses, citations_meta):
    """従属請求項の対比表"""
    num_citations = len(citation_order)

    # ヘッダ
    _set_cell(ws, row, 1, "請求項", font=FONT_WHITE, fill=FILL_HEADER,
              alignment=ALIGNMENT_CENTER, border=THIN_BORDER)
    _set_cell(ws, row, 2, "追加限定事項", font=FONT_WHITE, fill=FILL_HEADER,
              alignment=ALIGNMENT_CENTER, border=THIN_BORDER)
    for i, cit_id in enumerate(citation_order):
        _set_cell(ws, row, 3 + i, cit_id, font=FONT_WHITE, fill=FILL_HEADER,
                  alignment=ALIGNMENT_CENTER, border=THIN_BORDER)
    row += 1

    for claim in sub_claims:
        claim_num = claim["claim_number"]

        # 各分節を表示
        for seg in claim["segments"]:
            _set_cell(ws, row, 1, f"請求項{claim_num}\n{seg['id']}",
                      font=FONT_HEADER, alignment=ALIGNMENT_CENTER,
                      border=THIN_BORDER, fill=FILL_HEADER_LIGHT)
            _set_cell(ws, row, 2, seg["text"], font=FONT_NORMAL,
                      alignment=ALIGNMENT_LEFT, border=THIN_BORDER)

            for i, cit_id in enumerate(citation_order):
                resp = responses.get(cit_id, {})
                # sub_claimsから検索
                sub_comp = _find_sub_claim(resp, claim_num)
                if sub_comp:
                    judgment = sub_comp.get("judgment", "")
                    fill = _get_judgment_fill(judgment)
                    cell_text = f"{judgment}\n{sub_comp.get('judgment_reason', '')}"
                    _set_cell(ws, row, 3 + i, cell_text,
                              font=FONT_NORMAL, fill=fill,
                              alignment=ALIGNMENT_LEFT, border=THIN_BORDER)
                else:
                    _set_cell(ws, row, 3 + i, "-",
                              alignment=ALIGNMENT_CENTER, border=THIN_BORDER)
            row += 1

    return row


def _write_document_list(ws, row, citation_order, responses, citations_meta,
                          case_meta, num_citations):
    """文献リストセクション"""
    # ヘッダ
    headers = ["No", "文献名・タイトル", "概要・拒絶理由との関連性"]
    cols = min(len(headers), 2 + num_citations)

    _set_cell(ws, row, 1, "No", font=FONT_WHITE, fill=FILL_HEADER,
              alignment=ALIGNMENT_CENTER, border=THIN_BORDER)
    ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=2)
    _set_cell(ws, row, 2, "文献名・タイトル", font=FONT_WHITE, fill=FILL_HEADER,
              alignment=ALIGNMENT_CENTER, border=THIN_BORDER)
    if num_citations > 0:
        ws.merge_cells(start_row=row, start_column=3,
                       end_row=row, end_column=2 + num_citations)
        _set_cell(ws, row, 3, "概要・拒絶理由との関連性",
                  font=FONT_WHITE, fill=FILL_HEADER,
                  alignment=ALIGNMENT_CENTER, border=THIN_BORDER)
    row += 1

    for idx, cit_id in enumerate(citation_order, 1):
        resp = responses.get(cit_id, {})
        cit_info = None
        for c in case_meta.get("citations", []):
            if c["id"] == cit_id:
                cit_info = c
                break

        label = cit_info["label"] if cit_info else f"文献{idx}"
        role = cit_info.get("role", "") if cit_info else ""
        category = resp.get("category_suggestion", "")
        summary = resp.get("overall_summary", "")
        relevance = resp.get("rejection_relevance", "")

        _set_cell(ws, row, 1, idx, font=FONT_NORMAL,
                  alignment=ALIGNMENT_CENTER, border=THIN_BORDER)
        _set_cell(ws, row, 2, f"{label}\n({cit_id})\n役割: {role}\nカテゴリ: {category}",
                  font=FONT_NORMAL, alignment=ALIGNMENT_LEFT, border=THIN_BORDER)
        if num_citations > 0:
            ws.merge_cells(start_row=row, start_column=3,
                           end_row=row, end_column=2 + num_citations)
            _set_cell(ws, row, 3, f"{summary}\n\n{relevance}",
                      font=FONT_NORMAL, alignment=ALIGNMENT_LEFT, border=THIN_BORDER)
        ws.row_dimensions[row].height = 60
        row += 1

    return row


def _write_rejection_strategy(ws, row, citation_order, responses, num_citations):
    """拒絶理由構成の方針セクション"""
    total_cols = 2 + num_citations

    # 自動的に方針案を生成
    strategies = []

    # 各文献のカテゴリを確認
    x_docs = []  # 単独拒絶候補
    y_docs = []  # 組合せ拒絶候補
    for cit_id in citation_order:
        resp = responses.get(cit_id, {})
        cat = resp.get("category_suggestion", "")
        if cat == "X":
            x_docs.append(cit_id)
        elif cat == "Y":
            y_docs.append(cit_id)

    strategy_num = 1
    if x_docs:
        ws.merge_cells(start_row=row, start_column=1,
                       end_row=row, end_column=total_cols)
        _set_cell(ws, row, 1,
                  f"方針{strategy_num}: 29条2項（進歩性）— "
                  f"{', '.join(x_docs)} を主引例として単独で拒絶理由を構成",
                  font=FONT_NORMAL, alignment=ALIGNMENT_LEFT, border=THIN_BORDER)
        row += 1
        strategy_num += 1

    if len(y_docs) >= 2:
        ws.merge_cells(start_row=row, start_column=1,
                       end_row=row, end_column=total_cols)
        _set_cell(ws, row, 1,
                  f"方針{strategy_num}: 29条2項（進歩性）— "
                  f"{', '.join(y_docs)} の組合せで拒絶理由を構成",
                  font=FONT_NORMAL, alignment=ALIGNMENT_LEFT, border=THIN_BORDER)
        row += 1
        strategy_num += 1

    if not x_docs and not y_docs:
        ws.merge_cells(start_row=row, start_column=1,
                       end_row=row, end_column=total_cols)
        _set_cell(ws, row, 1,
                  "（自動分析結果: 現在の文献では拒絶理由の構成が困難な可能性があります。"
                  "追加の文献調査を検討してください。）",
                  font=FONT_NORMAL, alignment=ALIGNMENT_LEFT, border=THIN_BORDER)
        row += 1

    # 留意点
    row += 1
    ws.merge_cells(start_row=row, start_column=1,
                   end_row=row, end_column=total_cols)
    _set_cell(ws, row, 1, "留意点:", font=FONT_HEADER, alignment=ALIGNMENT_LEFT)
    row += 1
    ws.merge_cells(start_row=row, start_column=1,
                   end_row=row, end_column=total_cols)
    _set_cell(ws, row, 1,
              "- △判定の構成要件は、審査官の判断により○に引き上げられる可能性があります\n"
              "- ×判定の構成要件については、副引例や技術常識で補う必要があります\n"
              "- 上記は自動分析結果です。最終判断はサーチャー・審査官が行ってください",
              font=FONT_SMALL, alignment=ALIGNMENT_LEFT)
    row += 1

    return row


def _find_comparison(resp, segment_id):
    """回答データから指定された構成要件の比較結果を検索"""
    for comp in resp.get("comparisons", []):
        if comp.get("requirement_id") == segment_id:
            return comp
    return None


def _find_sub_claim(resp, claim_number):
    """回答データから従属請求項の判定を検索"""
    for sc in resp.get("sub_claims", []):
        if sc.get("claim_number") == claim_number:
            return sc
    return None
