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

from modules.cited_ref_notation import (
    comment_of as _ref_comment_of,
    display_judgment as _display_judgment,
    expand as _expand_ref,
)


# --- スタイル定義 ---
FONT_TITLE = Font(name="游ゴシック", size=14, bold=True)
FONT_HEADER = Font(name="游ゴシック", size=10, bold=True)
FONT_NORMAL = Font(name="游ゴシック", size=10)
FONT_SMALL = Font(name="游ゴシック", size=10)
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
    """対比表Excelを生成 (1 シートのみ)

    Parameters:
        output_path: 出力ファイルパス
        case_meta: 案件メタデータ (case.yaml)
        segments: 請求項分節データ (segments.json)
        responses: {citation_id: response_data} のdict
        citations_meta: {citation_id: citation_json} のdict（任意）
    """
    wb = Workbook()
    _populate_comparison_sheets(wb, case_meta, segments, responses, citations_meta,
                                 main_sheet_title="対比表", drop_default=True)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)


def _populate_comparison_sheets(wb, case_meta, segments, responses, citations_meta=None,
                                 main_sheet_title="対比表", drop_default=False):
    """対比表 + ペースト用シートを wb に追加する (内部用)。

    drop_default=True なら wb.active が空のデフォルト Sheet (新規 Workbook 直後)
    の場合これを使い回す。False なら create_sheet で新シートを追加する。
    """
    if drop_default and len(wb.sheetnames) == 1 and wb.active.max_row == 1 and wb.active.max_column == 1:
        ws = wb.active
        ws.title = main_sheet_title
    else:
        ws = wb.create_sheet(main_sheet_title)

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

    # ===== シート2: ペースト用（既存対比表への貼付用、コンパクト記法） =====
    _write_paste_sheet(wb, segments, citation_order, responses, citations_meta, case_meta)


def _strip_comment_memo_from_loc(raw):
    """``cited_location`` から `"...` コメントと `//...` メモを除去し、参照記法のみを返す。

    JS 側 `_stripCommentMemoFromLoc` と同等のロジック。
    """
    if not raw:
        return ""
    out = []
    for tok in str(raw).split(";"):
        tok = tok.strip()
        if not tok or tok.startswith('"') or tok.startswith("//"):
            continue
        # トークン内の " や // 以降を切り捨て
        cuts = []
        i = tok.find('"')
        if i >= 0:
            cuts.append(i)
        i = tok.find("//")
        if i >= 0:
            cuts.append(i)
        if cuts:
            tok = tok[:min(cuts)].strip()
        if tok:
            out.append(tok)
    return ";".join(out)


def _short_reason(s, limit=None):
    """judgment_reason を最初の文 (「。」まで) で切る。途中切り捨て (…) はしない。

    LLM プロンプト側で「相違点を 1 文で簡潔に」と指示している前提なので、
    無理な文字数制限は掛けず、最初の句点までを返す。複数文ある場合のみ
    末尾「。」を 1 個分だけ落とす。
    """
    if not s:
        return ""
    import re
    t = re.sub(r"[\r\n]+", " ", str(s))
    t = re.sub(r"\s{2,}", " ", t).strip()
    m = re.search(r"[。．]", t)
    if m and m.start() > 0:
        t = t[: m.start()]
    return t


def _format_comp_for_paste(comp):
    """comparison/sub_claim 1 件分を「貼付用」セル内容に整形。

    JS 側 `_formatCompForPaste` と同等。
      - judgment ○ → prefix なし
      - judgment △ → "?"
      - judgment × → "x"
      - cited_location は raw 記法 (展開しない)。" コメントと // メモは除外。
      - 末尾: コメント (manual) があれば "/<comment>"。
        なければ judgment_reason (△/× のみ、短縮形) を "/" で付ける。
    """
    if not isinstance(comp, dict):
        return ""
    j = (comp.get("judgment") or "").strip()
    prefix = ""
    if j == "△":
        prefix = "?"
    elif j == "×":
        prefix = "!"  # 該当箇所なしは !

    raw = comp.get("cited_location") or ""
    loc_only = _strip_comment_memo_from_loc(raw)

    # manual comment ("...") 抽出
    from modules.cited_ref_notation import comment_of
    comment = (comment_of(raw) or "").strip()
    if not comment and j in ("△", "×") and comp.get("judgment_reason"):
        comment = _short_reason(comp.get("judgment_reason"))

    out = prefix + loc_only
    if comment:
        # タブ/改行は貼付時にセルが暴れるので空白に置換
        clean = comment.replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()
        out += "/" + clean
    return out


def _claim_full_text(claim):
    """請求項 dict から全文を組み立てる。"""
    if not claim:
        return ""
    if claim.get("full_text"):
        return claim["full_text"]
    parts = [s.get("text", "") for s in (claim.get("segments") or []) if s.get("text")]
    return "".join(parts)


def _write_paste_sheet(wb, segments, citation_order, responses, citations_meta, case_meta):
    """既存対比表への貼付専用シート。

    各引例ごとに 1 列。1 行 = 1 分節 (請求項1) または 1 従属請求項。
    請求項1 の分節は連続、請求項2 以降の各従属請求項の前に空行 1 つ。

    レイアウト:
      | 構成要件ID | 構成要件テキスト | 文献1 | 文献2 | ... |
      | 1a        | (claim1 seg text) | CL1,..| ...   | ... |
      | 1b        | ...               | ...   | ...   | ... |
      | (empty row before claim 2)                          |
      | 2a        | (claim 2 text)    | 13,51 | ...   | ... |
      | ...                                                  |
    """
    ws = wb.create_sheet("ペースト用")
    num_cit = len(citation_order)

    # 列幅
    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 40
    for i in range(num_cit):
        ws.column_dimensions[get_column_letter(3 + i)].width = 30

    # ヘッダ行
    row = 1
    _set_cell(ws, row, 1, "ID", font=FONT_WHITE, fill=FILL_HEADER,
              alignment=ALIGNMENT_CENTER, border=THIN_BORDER)
    _set_cell(ws, row, 2, "構成要件", font=FONT_WHITE, fill=FILL_HEADER,
              alignment=ALIGNMENT_CENTER, border=THIN_BORDER)
    for i, cit_id in enumerate(citation_order):
        cit_info = None
        for c in case_meta.get("citations", []):
            if c["id"] == cit_id:
                cit_info = c
                break
        label = cit_info["label"] if cit_info else cit_id
        _set_cell(ws, row, 3 + i, f"{label}\n({cit_id})",
                  font=FONT_WHITE, fill=FILL_HEADER,
                  alignment=ALIGNMENT_CENTER, border=THIN_BORDER)
    row += 1

    # 案内行
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2 + num_cit)
    _set_cell(ws, row, 1,
              "貼付用フォーマット: 各引例の列をコピーして既存対比表へ縦方向に貼付け。"
              "判定 ○ は prefix なし、△ は ?、× は x を冠。/ 以降はコメント。",
              font=FONT_SMALL, alignment=ALIGNMENT_LEFT,
              fill=FILL_HEADER_LIGHT, border=THIN_BORDER)
    row += 1

    # 請求項を分類
    claim1 = None
    sub_claims = []
    for claim in segments:
        if claim.get("claim_number") == 1:
            claim1 = claim
        elif claim.get("is_independent") and claim1 is None:
            claim1 = claim
        else:
            sub_claims.append(claim)
    if claim1 is None and segments:
        # フォールバック: 最初を独立クレームとみなす
        claim1 = segments[0]
        sub_claims = list(segments[1:])
    sub_claims.sort(key=lambda c: c.get("claim_number") or 0)

    # 請求項1 の分節 (空行なしで連続)
    if claim1:
        for seg in claim1.get("segments", []):
            seg_id = seg.get("id", "")
            seg_text = seg.get("text", "")
            _set_cell(ws, row, 1, seg_id, font=FONT_NORMAL,
                      alignment=ALIGNMENT_CENTER, border=THIN_BORDER)
            _set_cell(ws, row, 2, seg_text, font=FONT_NORMAL,
                      alignment=ALIGNMENT_LEFT, border=THIN_BORDER)
            for i, cit_id in enumerate(citation_order):
                resp = responses.get(cit_id, {})
                comp = _find_comparison(resp, seg_id)
                cell_val = _format_comp_for_paste(comp) if comp else ""
                fill = _get_judgment_fill(comp.get("judgment", "")) if comp else None
                _set_cell(ws, row, 3 + i, cell_val, font=FONT_NORMAL,
                          alignment=ALIGNMENT_LEFT, border=THIN_BORDER, fill=fill)
            row += 1

    # 従属請求項 (各々の前に空行 1)
    for claim in sub_claims:
        # 空行
        for col in range(1, 3 + num_cit):
            _set_cell(ws, row, col, "", border=THIN_BORDER)
        row += 1
        claim_num = claim.get("claim_number")
        # 通番ID: "{claim_num}a" 形 (1a/1b と並ぶ慣行に合わせる)
        # 元データに segments[0].id があればそれを優先 (既存形式へ追従)
        seg_id_default = f"{claim_num}a"
        first_seg_id = ""
        for s in (claim.get("segments") or []):
            if s.get("id"):
                first_seg_id = s["id"]
                break
        seg_id = first_seg_id or seg_id_default
        full_text = _claim_full_text(claim)

        _set_cell(ws, row, 1, seg_id, font=FONT_NORMAL,
                  alignment=ALIGNMENT_CENTER, border=THIN_BORDER)
        _set_cell(ws, row, 2, full_text, font=FONT_NORMAL,
                  alignment=ALIGNMENT_LEFT, border=THIN_BORDER)
        for i, cit_id in enumerate(citation_order):
            resp = responses.get(cit_id, {})
            sub = _find_sub_claim(resp, claim_num)
            cell_val = _format_comp_for_paste(sub) if sub else ""
            fill = _get_judgment_fill(sub.get("judgment", "")) if sub else None
            _set_cell(ws, row, 3 + i, cell_val, font=FONT_NORMAL,
                      alignment=ALIGNMENT_LEFT, border=THIN_BORDER, fill=fill)
        row += 1

    # 列幅自動調整は openpyxl 側で限定的なので固定値で運用


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
                judgment_raw = comp.get("judgment", "")
                fill = _get_judgment_fill(judgment_raw)
                # ○ は「先頭に何もつけない」慣行 → 表示は空、塗りは緑のまま
                judgment_disp = _display_judgment(judgment_raw)
                _set_cell(ws, row, 3 + i, judgment_disp,
                          font=FONT_JUDGMENT, fill=fill,
                          alignment=ALIGNMENT_CENTER, border=THIN_BORDER)
            else:
                _set_cell(ws, row, 3 + i, "-",
                          alignment=ALIGNMENT_CENTER, border=THIN_BORDER)
        row += 1

        # 理由行: cited_location は記法を展開して書き込む。
        #   コメント部分 ("...) は備考扱いとして判定理由の末尾に括弧書きで合成。
        #   メモ部分 (//...) は対外出力なので含めない。
        _set_cell(ws, row, 1, "", border=THIN_BORDER)
        _set_cell(ws, row, 2, "", border=THIN_BORDER)

        for i, cit_id in enumerate(citation_order):
            resp = responses.get(cit_id, {})
            comp = _find_comparison(resp, seg_id)
            if comp:
                reason_parts = []
                if comp.get("judgment_reason"):
                    reason_parts.append(comp["judgment_reason"])
                cited_loc_raw = comp.get("cited_location", "")
                if cited_loc_raw:
                    expanded = _expand_ref(cited_loc_raw, with_comment=False)
                    if expanded:
                        reason_parts.append(f"[{expanded}]")
                    cmt = _ref_comment_of(cited_loc_raw)
                    if cmt:
                        reason_parts.append(f"（備考: {cmt}）")
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
                    judgment_raw = sub_comp.get("judgment", "")
                    fill = _get_judgment_fill(judgment_raw)
                    judgment_disp = _display_judgment(judgment_raw)
                    parts = []
                    if judgment_disp:
                        parts.append(judgment_disp)
                    if sub_comp.get("judgment_reason"):
                        parts.append(sub_comp["judgment_reason"])
                    cited_loc_raw = sub_comp.get("cited_location", "")
                    if cited_loc_raw:
                        expanded = _expand_ref(cited_loc_raw, with_comment=False)
                        if expanded:
                            parts.append(f"[{expanded}]")
                        cmt = _ref_comment_of(cited_loc_raw)
                        if cmt:
                            parts.append(f"（備考: {cmt}）")
                    cell_text = "\n".join(parts)
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


# ============================================================
# 完成版対比表 (3 タブ統合) writer
# ============================================================

def write_full_report(output_path, case_meta, segments, responses,
                      citations_meta=None, hongan_analysis=None, inventive_step=None):
    """3 タブ統合 Excel を生成。

    タブ構成:
        - 本願解析結果   (hongan_analysis_v0.1.yaml の構造化結果)
        - 対比表         (write_comparison_table と同じ + ペースト用シート)
        - 進歩性判断     (inventive_step.json のパース結果)
    """
    wb = Workbook()
    # デフォルト Sheet を本願解析結果に転用 (空なら使い回し)
    _populate_hongan_analysis_sheet(wb, case_meta, hongan_analysis, drop_default=True)
    _populate_comparison_sheets(wb, case_meta, segments, responses, citations_meta,
                                 main_sheet_title="対比表", drop_default=False)
    _populate_inventive_step_sheet(wb, case_meta, inventive_step)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)


def _ensure_sheet(wb, title, drop_default=False):
    """新規シート作成 (drop_default なら 空のデフォルト Sheet を使い回す)。"""
    if drop_default and len(wb.sheetnames) == 1 and wb.active.max_row == 1 and wb.active.max_column == 1:
        ws = wb.active
        ws.title = title
        return ws
    return wb.create_sheet(title)


def _populate_hongan_analysis_sheet(wb, case_meta, hongan_analysis, drop_default=False):
    """本願解析結果 (analysis/hongan_analysis.json) をシートに展開。"""
    ws = _ensure_sheet(wb, "本願解析結果", drop_default=drop_default)
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 32
    ws.column_dimensions["C"].width = 90

    case_id = case_meta.get("case_id", "")
    title = case_meta.get("patent_title") or case_meta.get("title", "")
    pn = case_meta.get("patent_number", case_id)

    row = 1
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
    _set_cell(ws, row, 1, f"{pn}　{title}　本願解析結果",
              font=FONT_TITLE, alignment=ALIGNMENT_LEFT_CENTER)
    row += 1

    if not hongan_analysis or not hongan_analysis.get("sections"):
        _set_cell(ws, row, 1, "(本願分析が未実行です。Step 2 SUB 3 で実行してください)",
                  font=FONT_SMALL, alignment=ALIGNMENT_LEFT)
        return

    for sec in hongan_analysis.get("sections") or []:
        # セクション見出し
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
        _set_cell(ws, row, 1, f"{sec.get('id')} {sec.get('title', '')}",
                  font=FONT_HEADER, fill=FILL_HEADER_LIGHT, alignment=ALIGNMENT_LEFT_CENTER)
        row += 1

        for it in sec.get("items") or []:
            iid = it.get("id", "")
            label = it.get("label", "")
            value = it.get("value")
            value_text = _format_analysis_value(value)
            _set_cell(ws, row, 1, iid, font=FONT_NORMAL,
                      alignment=ALIGNMENT_CENTER, border=THIN_BORDER)
            _set_cell(ws, row, 2, label, font=FONT_NORMAL,
                      alignment=ALIGNMENT_LEFT, border=THIN_BORDER)
            _set_cell(ws, row, 3, value_text, font=FONT_NORMAL,
                      alignment=ALIGNMENT_LEFT, border=THIN_BORDER)
            row += 1
        row += 1


def _format_analysis_value(value):
    """本願分析の value (string / list / dict / None) を Excel 表示用に変換。

    マーカー <<HL>>...<</HL>> / <<UL>>...<</UL>> は素テキストにして残す
    (Excel ではフォント色付けが手間なのでそのままラベル代わり)。
    """
    if value is None or value == "":
        return "(未取得)"
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        if not value:
            return "(空)"
        if all(isinstance(x, str) for x in value):
            return "、".join(value)
        # オブジェクトリスト (例: 1.3 IPC/FI の {code, label} 形式)
        lines = []
        for x in value:
            if isinstance(x, dict):
                code = x.get("code", "")
                lab = x.get("label", "")
                lines.append(f"{code}（{lab}）" if lab else code)
            else:
                lines.append(str(x))
        return "\n".join(lines)
    if isinstance(value, dict):
        # 1.3 のような構造化分類
        parts = []
        for k, v in value.items():
            if isinstance(v, dict) and "items" in v:
                # F-term grouped: {theme: {theme_label, items: [{code, label}, ...]}}
                items = v.get("items") or []
                joined = "、".join(
                    f"{x.get('code', '')}（{x.get('label', '')}）" if x.get("label")
                    else x.get("code", "")
                    for x in items
                )
                tlab = v.get("theme_label", "")
                parts.append(f"{k}（{tlab}）: {joined}" if tlab else f"{k}: {joined}")
            elif isinstance(v, list):
                if all(isinstance(x, dict) for x in v):
                    formatted = "、".join(
                        f"{x.get('code', '')}（{x.get('label', '')}）" if x.get("label")
                        else x.get("code", "")
                        for x in v
                    )
                else:
                    formatted = "、".join(str(x) for x in v)
                parts.append(f"{k}: {formatted}")
            else:
                parts.append(f"{k}: {v}")
        return "\n".join(parts)
    return str(value)


def _populate_inventive_step_sheet(wb, case_meta, inventive_step):
    """進歩性判断 (inventive_step.json) をシートに展開。"""
    ws = _ensure_sheet(wb, "進歩性判断")
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 100

    case_id = case_meta.get("case_id", "")
    title = case_meta.get("patent_title") or case_meta.get("title", "")
    pn = case_meta.get("patent_number", case_id)

    row = 1
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
    _set_cell(ws, row, 1, f"{pn}　{title}　進歩性判断 (JPO 審査基準ベース)",
              font=FONT_TITLE, alignment=ALIGNMENT_LEFT_CENTER)
    row += 2

    if not inventive_step:
        _set_cell(ws, row, 1, "(進歩性判断が未実行です。Step 6 で実行してください)",
                  font=FONT_SMALL, alignment=ALIGNMENT_LEFT)
        return

    # 主要キー (順序を固定): main_reference / sub_references / motivation /
    # blocking_reasons / unexpected_effect / conclusion / 他
    key_labels = [
        ("main_reference", "主引例"),
        ("sub_references", "副引例"),
        ("common_features", "本願との一致点"),
        ("differences", "相違点"),
        ("motivation", "組合せの動機付け"),
        ("blocking_reasons", "阻害要因"),
        ("unexpected_effect", "顕著な効果"),
        ("design_choice", "設計事項該当性"),
        ("conclusion", "結論 (進歩性の有無)"),
        ("rationale", "論理付け"),
        ("notes", "備考"),
        ("citations", "引用文献"),
    ]
    seen_keys = set()
    for key, label in key_labels:
        if key not in inventive_step:
            continue
        seen_keys.add(key)
        _write_invstep_row(ws, row, label, inventive_step[key])
        row += 1

    # 残りのキー (上記以外も雑に出す)
    for k, v in inventive_step.items():
        if k in seen_keys:
            continue
        _write_invstep_row(ws, row, k, v)
        row += 1


def _write_invstep_row(ws, row, label, value):
    _set_cell(ws, row, 1, label, font=FONT_HEADER,
              fill=FILL_HEADER_LIGHT, alignment=ALIGNMENT_LEFT_CENTER, border=THIN_BORDER)
    _set_cell(ws, row, 2, _format_analysis_value(value), font=FONT_NORMAL,
              alignment=ALIGNMENT_LEFT, border=THIN_BORDER)
