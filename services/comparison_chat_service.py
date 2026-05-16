#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""対比セル単位の壁打ちチャットと判定上書き。"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from services.case_service import get_case_dir, load_case_meta
from services.evidence_index_service import search_evidence_index
from modules.claude_client import call_claude, ClaudeClientError


_PARA_RE = re.compile(
    r"【([０-９0-9]{1,6})】"
    r"|\[([０-９0-9]{2,6})\]"
    r"|(?:paragraphs?|paras?|para\.?)\s*\[?([０-９0-9]{1,6})\]?"
    r"|([０-９0-9]{1,6})\s*段落",
    re.IGNORECASE,
)

_CHEM_HINTS = {
    "ステアリン酸": "ステアリン酸は C18 の飽和脂肪酸です。",
    "ステアリン酸グリセリル": "ステアリン酸グリセリルはステアリン酸(C18)とグリセリンのエステルです。",
    "パルミチン酸": "パルミチン酸は C16 の飽和脂肪酸です。",
    "ミリスチン酸": "ミリスチン酸は C14 の飽和脂肪酸です。",
    "ラウリン酸": "ラウリン酸は C12 の飽和脂肪酸です。",
    "カプリン酸": "カプリン酸は C10 の飽和脂肪酸です。",
    "カプリル酸": "カプリル酸は C8 の飽和脂肪酸です。",
    "エステル": "エステルは酸とアルコール由来の -COO- 結合を含む化合物群です。",
}

_FW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_TERM_STOPWORDS = {
    "について", "として", "する", "した", "して", "いる", "ある", "これ", "それ",
    "本願", "引例", "引用", "文献", "発明", "構成", "判断", "認定", "記載",
    "請求項", "段落", "比較例", "実施例", "表",
}


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _safe_name(s):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(s or "")).strip("_") or "cell"


def _read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _append_jsonl(path, item):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def _load_jsonl(path):
    rows = []
    if not path.exists():
        return rows
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows


def _load_citation(case_id, citation_id):
    case_dir = get_case_dir(case_id)
    citation = _read_json(case_dir / "citations" / f"{citation_id}.json", {}) or {}
    try:
        from services.comparison.common import (
            _enrich_citation_with_extracted_tables,
            _enrich_citation_with_hit_text,
        )
        citation = _enrich_citation_with_hit_text(case_id, citation_id, citation)
        citation = _enrich_citation_with_extracted_tables(case_id, citation_id, citation)
    except Exception:
        pass
    return citation


def _compact_text(value):
    s = str(value or "").translate(_FW_DIGITS)
    s = s.replace("−", "-").replace("－", "-").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", "", s)


def _add_term(out, seen, term):
    term = re.sub(r"\s+", "", str(term or "").translate(_FW_DIGITS))
    term = term.strip("「」『』()（）[]【】,，.。:：;；")
    if len(term) < 2 or term in _TERM_STOPWORDS:
        return
    key = _compact_text(term).lower()
    if not key or key in seen:
        return
    seen.add(key)
    out.append(term)


def _query_terms(*texts, max_terms=48):
    joined = "\n".join(str(t or "") for t in texts)
    out = []
    seen = set()

    # 実務上の指摘でよく出る参照語は、空白や全角数字を吸収して優先採用する。
    ref_pats = [
        r"比較\s*例\s*[0-9０-９]+",
        r"実施\s*例\s*[0-9０-９]+",
        r"表\s*[0-9０-９]+",
        r"請求\s*項\s*[0-9０-９]+",
        r"[A-Z]{1,4}\s*[-_/]?\s*[0-9０-９]{2,}",
        r"[0-9０-９]+(?:\.[0-9０-９]+)?\s*(?:質量|重量)?\s*[％%]",
    ]
    for pat in ref_pats:
        for m in re.finditer(pat, joined, re.IGNORECASE):
            _add_term(out, seen, m.group(0))

    token_pat = re.compile(
        r"[A-Za-z][A-Za-z0-9_.%/\-]{1,}|"
        r"[一-龥ぁ-んァ-ヶーα-ωΑ-Ω][一-龥ぁ-んァ-ヶーα-ωΑ-Ω0-9０-９％%℃μµ・ー\-]{1,}"
    )
    for m in token_pat.finditer(joined):
        _add_term(out, seen, m.group(0))
        if len(out) >= max_terms:
            break
    return out[:max_terms]


def _find_segment(segs, segment_id):
    sid = str(segment_id)
    for claim in segs or []:
        for seg in claim.get("segments") or []:
            if str(seg.get("id")) == sid:
                return {
                    "id": sid,
                    "text": seg.get("text", ""),
                    "claim_number": claim.get("claim_number"),
                    "claim_text": claim.get("full_text", ""),
                }
    return {"id": sid, "text": "", "claim_number": None, "claim_text": ""}


def _find_claim(segs, claim_number):
    try:
        cn = int(claim_number)
    except (TypeError, ValueError):
        return {"id": str(claim_number), "text": "", "claim_number": None, "claim_text": ""}
    for claim in segs or []:
        if int(claim.get("claim_number") or -1) == cn:
            text = claim.get("full_text") or "".join(
                (seg.get("text") or "") for seg in (claim.get("segments") or [])
            )
            return {
                "id": f"請求項{cn}",
                "text": text,
                "claim_number": cn,
                "claim_text": text,
                "is_independent": bool(claim.get("is_independent")),
                "dependencies": claim.get("dependencies") or [],
            }
    return {"id": f"請求項{cn}", "text": "", "claim_number": cn, "claim_text": ""}


def _find_comparison(response, segment_id):
    sid = str(segment_id)
    for comp in response.get("comparisons") or []:
        if str(comp.get("requirement_id")) == sid:
            return comp
    return None


def _find_sub_claim_response(response, claim_number):
    try:
        cn = int(claim_number)
    except (TypeError, ValueError):
        return None
    for comp in response.get("sub_claims") or []:
        if int(comp.get("claim_number") or -1) == cn:
            return comp
    return None


def _find_target(segs, response, target_kind, target_key):
    if target_kind == "sub_claim":
        return _find_claim(segs, target_key), (_find_sub_claim_response(response, target_key) or {})
    return _find_segment(segs, target_key), (_find_comparison(response, target_key) or {})


def _paragraph_id_set(*texts):
    ids = set()
    for text in texts:
        for m in _PARA_RE.finditer(str(text or "")):
            raw = next((g for g in m.groups() if g), "")
            if not raw:
                continue
            s = raw.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
            s = s.lstrip("0") or "0"
            # 1桁の単独数字は請求項番号や数量に当たりやすいので捨てる。
            if len(s) == 1:
                continue
            ids.add(s)
    return ids


def _para_key(para):
    raw = str(para.get("id") or para.get("number") or "")
    raw = raw.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    return raw.strip().lstrip("0") or "0"


def _context_paragraphs(citation, wanted_ids, limit=8):
    paras = citation.get("paragraphs") or []
    if not paras:
        return []
    out = []
    seen = set()
    for i, para in enumerate(paras):
        if _para_key(para) not in wanted_ids:
            continue
        for j in range(max(0, i - 1), min(len(paras), i + 2)):
            p = paras[j]
            key = _para_key(p)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "para_no": str(p.get("id") or p.get("number") or key),
                "page": p.get("page"),
                "section": p.get("section"),
                "text": p.get("text", ""),
            })
            if len(out) >= limit:
                return out
    return out


def _score_text(text, terms):
    compact = _compact_text(text).lower()
    if not compact:
        return 0, []
    score = 0
    matched = []
    for term in terms or []:
        key = _compact_text(term).lower()
        if len(key) < 2 or key not in compact:
            continue
        weight = 1
        if re.match(r"^(比較例|実施例|表|請求項)\d+", key):
            weight = 6
        elif re.search(r"\d", key):
            weight = 3
        elif len(key) >= 6:
            weight = 2
        score += weight
        matched.append(term)
    return score, matched[:8]


def _merge_paragraph_context(existing, citation, terms, wanted_ids, limit=10):
    paras = citation.get("paragraphs") or []
    out = list(existing or [])
    seen = {
        str(p.get("para_no") or p.get("id") or p.get("number") or "")
        for p in out
    }
    scored = []
    for i, para in enumerate(paras):
        score, matched = _score_text(para.get("text", ""), terms)
        key = _para_key(para)
        if key in wanted_ids:
            score += 100
        if score <= 0:
            continue
        scored.append((score, i, matched))
    for _score, i, matched in sorted(scored, key=lambda x: (-x[0], x[1])):
        for j in range(max(0, i - 1), min(len(paras), i + 2)):
            p = paras[j]
            para_no = str(p.get("id") or p.get("number") or _para_key(p))
            if para_no in seen:
                continue
            seen.add(para_no)
            out.append({
                "para_no": para_no,
                "page": p.get("page"),
                "section": p.get("section"),
                "text": p.get("text", ""),
                "matched_terms": matched if j == i else [],
            })
            if len(out) >= limit:
                return out
    return out


def _table_to_text(table, max_chars=2200):
    parts = []
    for key in ("caption_label", "caption", "title", "section"):
        val = table.get(key)
        if val:
            parts.append(str(val))
    headers = table.get("headers") or []
    if headers:
        parts.append("\t".join(str(x) for x in headers))
    rows = table.get("rows") or table.get("data") or []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                cells = row.get("cells") or row.get("values") or []
                parts.append("\t".join(str(x) for x in cells))
            elif isinstance(row, list):
                parts.append("\t".join(str(x) for x in row))
            else:
                parts.append(str(row))
    content = table.get("content") or table.get("text") or ""
    if content:
        parts.append(str(content))
    text = "\n".join(p for p in parts if p)
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def _context_tables(citation, terms, limit=4):
    out = []
    for i, table in enumerate(citation.get("tables") or [], 1):
        text = _table_to_text(table)
        score, matched = _score_text(text, terms)
        label = table.get("caption_label") or table.get("caption") or table.get("title") or f"表{i}"
        label_score, label_matched = _score_text(label, terms)
        score += label_score * 2
        if label_matched:
            matched = list(dict.fromkeys((matched or []) + label_matched))
        if score <= 0:
            continue
        out.append((score, {
            "index": i,
            "label": label,
            "page": table.get("page") or table.get("page_num") or table.get("page_number"),
            "source": table.get("source"),
            "matched_terms": matched[:8],
            "text": text,
        }))
    return [t for _score, t in sorted(out, key=lambda x: -x[0])[:limit]]


def _merge_paragraph_lists(*groups, limit=10):
    out = []
    seen = set()
    for group in groups:
        for para in group or []:
            para_no = str(para.get("para_no") or para.get("id") or para.get("number") or "")
            key = para_no.translate(_FW_DIGITS).lstrip("0") or para_no
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(para)
            if len(out) >= limit:
                return out
    return out


def _merge_table_lists(*groups, limit=4):
    out = []
    seen = set()
    for group in groups:
        for table in group or []:
            key = str(table.get("label") or table.get("index") or str(table.get("text") or "")[:40])
            if key in seen:
                continue
            seen.add(key)
            out.append(table)
            if len(out) >= limit:
                return out
    return out


def _chem_hints(*texts):
    joined = "\n".join(str(t or "") for t in texts)
    return [
        {"term": term, "hint": hint}
        for term, hint in _CHEM_HINTS.items()
        if term in joined
    ]


def _citation_meta(case_id, citation_id):
    meta = load_case_meta(case_id) or {}
    for c in meta.get("citations") or []:
        if str(c.get("id")) == str(citation_id):
            return c
    return {"id": citation_id, "label": citation_id, "role": ""}


def build_cell_context(case_id, citation_id, segment_id, message="", target_kind="comparison"):
    case_dir = get_case_dir(case_id)
    segs = _read_json(case_dir / "segments.json", [])
    citation = _load_citation(case_id, citation_id)
    response = _read_json(case_dir / "responses" / f"{citation_id}.json", {})

    target_kind = "sub_claim" if target_kind == "sub_claim" else "comparison"
    segment, comp = _find_target(segs, response, target_kind, segment_id)
    terms = _query_terms(
        message,
        segment.get("text"),
        segment.get("claim_text"),
        comp.get("cited_location"),
        comp.get("cited_text"),
        comp.get("judgment_reason"),
    )
    wanted = _paragraph_id_set(
        message,
        comp.get("cited_location"),
        comp.get("cited_text"),
        comp.get("judgment_reason"),
    )
    query_text = "\n".join(str(t or "") for t in [
        message,
        segment.get("text"),
        segment.get("claim_text"),
        comp.get("cited_location"),
        comp.get("cited_text"),
        comp.get("judgment_reason"),
    ])
    evidence = {}
    try:
        evidence = search_evidence_index(
            case_id,
            citation_id,
            query_text=query_text,
            terms=terms,
            citation=citation,
            limit=10,
        )
    except Exception:
        evidence = {}

    explicit_paras = _context_paragraphs(citation, wanted)
    scored_paras = _merge_paragraph_context([], citation, terms, wanted)
    paras = _merge_paragraph_lists(
        explicit_paras,
        evidence.get("paragraphs") if evidence else [],
        scored_paras,
    )
    fallback_tables = _context_tables(citation, terms)
    tables = _merge_table_lists(
        evidence.get("tables") if evidence else [],
        fallback_tables,
    )
    hints = _chem_hints(
        message,
        segment.get("text"),
        comp.get("cited_text"),
        comp.get("judgment_reason"),
        *(p.get("text") for p in paras),
        *(t.get("text") for t in tables),
    )

    return {
        "case_id": case_id,
        "target_kind": target_kind,
        "segment": segment,
        "citation": {
            "id": citation_id,
            "label": citation.get("label") or citation.get("patent_number") or citation_id,
            "title": citation.get("patent_title", ""),
            "role": (_citation_meta(case_id, citation_id) or {}).get("role", ""),
        },
        "current_judgment": {
            "category": comp.get("judgment", ""),
            "reason": comp.get("judgment_reason", ""),
            "cited_location": comp.get("cited_location", ""),
            "cited_text": comp.get("cited_text", ""),
            "section_type": comp.get("section_type", ""),
            "note": comp.get("note", ""),
            "edited_at": comp.get("_edited_at", ""),
        },
        "relevant_paragraphs": paras,
        "relevant_tables": tables,
        "chemistry_hints": hints,
        "query_terms": terms[:24],
        "message_paragraph_ids": sorted(wanted),
        "evidence_index": evidence.get("index", {}) if evidence else {},
    }


def _chat_path(case_id, citation_id, segment_id, target_kind="comparison"):
    case_dir = get_case_dir(case_id)
    prefix = "subclaim" if target_kind == "sub_claim" else "comparison"
    return case_dir / "chat" / prefix / f"{_safe_name(citation_id)}_{_safe_name(segment_id)}.jsonl"


def get_cell_chat_history(case_id, citation_id, segment_id, target_kind="comparison"):
    return {
        "messages": _load_jsonl(_chat_path(case_id, citation_id, segment_id, target_kind)),
        "context": build_cell_context(case_id, citation_id, segment_id, target_kind=target_kind),
    }, 200


def _build_prompt(context, message):
    paras = context.get("relevant_paragraphs") or []
    tables = context.get("relevant_tables") or []
    hints = context.get("chemistry_hints") or []
    return (
        "あなたは日本特許庁の審査官水準で、特許対比表のセル単位レビューを支援するAIです。\n"
        "目的は、既存のLLM判定を鵜呑みにせず、ユーザーの指摘を検討し、"
        "判定を変えるべきかを短く実務的に整理することです。\n"
        "形式的な文言一致だけでなく、発明の技術的意義、引例の実施例・比較例、"
        "表中の成分・配合比、内在的物性、特許法29条1項/2項の使い分けを踏まえてください。\n"
        "根拠は段落番号・表番号・該当セル等を明記し、根拠がない場合は推測として区別してください。\n"
        "cited_location は厳密なコンパクト記法で出力してください。段落番号は数字のみ、請求項は CL、"
        "図は F、表は T、化は K、式は E、数は S、ページは P、コラムは C、行は G です。"
        "同種は ,、種類が変わる時だけ ;、コメントは /、防備録メモは // を使います。"
        "英字と数字を混同してはいけません。CL を 0L、T を 1、S を 8、G を 6、C を 0 にしないでください。"
        "T4/備考 を T4;/備考 にしないでください。不明な文字は [判読不明] のまま残してください。\n"
        "出力は日本語で、(1)結論 (2)根拠 (3)既存判定を修正すべき点 (4)推奨上書き文案 の順に簡潔に答えてください。\n\n"
        "最後に必ず、次のJSONだけを ```json fenced block``` で付けてください。\n"
        "判定を更新すべきときは apply=true とし、judgment/reason/location/text を上書き文案として具体的に埋めてください。\n"
        "既存判定を維持すべきときは apply=false にしてください。\n"
        "{\n"
        "  \"suggested_override\": {\n"
        "    \"apply\": true,\n"
        "    \"judgment\": \"○|△|×|\",\n"
        "    \"judgment_reason\": \"対比表セルに保存する理由文\",\n"
        "    \"cited_location\": \"コンパクト記法 例: 20;F2;CL3;T4/備考 / P1A2-4 / C4G12 / F1,1a,5C\",\n"
        "    \"cited_text\": \"根拠となる引用本文\",\n"
        "    \"user_note\": \"壁打ちでの変更理由\"\n"
        "  }\n"
        "}\n\n"
        f"[構成要件]\n{json.dumps(context.get('segment'), ensure_ascii=False)}\n\n"
        f"[引用文献]\n{json.dumps(context.get('citation'), ensure_ascii=False)}\n\n"
        f"[現在の判定]\n{json.dumps(context.get('current_judgment'), ensure_ascii=False)}\n\n"
        f"[関連段落]\n{json.dumps(paras, ensure_ascii=False)}\n\n"
        f"[関連表]\n{json.dumps(tables, ensure_ascii=False)}\n\n"
        f"[化学メモ]\n{json.dumps(hints, ensure_ascii=False)}\n\n"
        f"[検索語]\n{json.dumps(context.get('query_terms') or [], ensure_ascii=False)}\n\n"
        f"[ユーザーの指摘]\n{message}\n"
    )


def _extract_json_candidates(text):
    text = str(text or "")
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    for block in blocks:
        yield block
    start = text.find("{")
    while start >= 0:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    yield text[start:i + 1]
                    break
        start = text.find("{", start + 1)


def _suggested_override_from_reply(reply, context):
    current = context.get("current_judgment") or {}
    def build_suggestion(sug, *, from_fallback=False):
        judgment = str(sug.get("judgment") or "").strip()
        if judgment and judgment not in {"○", "△", "×"}:
            return None
        fields = {
            "judgment": judgment,
            "judgment_reason": str(sug.get("judgment_reason") or sug.get("reason") or "").strip(),
            "cited_location": str(sug.get("cited_location") or sug.get("location") or "").strip(),
            "cited_text": str(sug.get("cited_text") or "").strip(),
        }
        if fields["cited_location"]:
            try:
                from modules.cited_ref_notation import normalize as _norm
                fields["cited_location"] = _norm(fields["cited_location"])
            except Exception:
                pass
        current_key = {
            "judgment": "category",
            "judgment_reason": "reason",
            "cited_location": "cited_location",
            "cited_text": "cited_text",
        }
        changed = any(str(current.get(current_key[k]) or "").strip() != v
                      for k, v in fields.items() if v)
        judgment_changed = bool(judgment and judgment != str(current.get("category") or "").strip())
        apply_flag = bool(sug.get("apply"))
        if from_fallback:
            apply_flag = bool(judgment_changed and fields["judgment_reason"])
        return {
            "apply": apply_flag and bool(fields["judgment_reason"] or fields["cited_location"] or fields["cited_text"]),
            "changed": changed,
            "judgment_changed": judgment_changed,
            "from_judgment": str(current.get("category") or ""),
            "to_judgment": judgment,
            "fields": fields,
            "user_note": str(sug.get("user_note") or "壁打ち上書き文案").strip(),
        }

    for raw in _extract_json_candidates(reply):
        try:
            data = json.loads(raw)
        except Exception:
            continue
        sug = data.get("suggested_override") if isinstance(data, dict) else None
        if not isinstance(sug, dict):
            sug = data if isinstance(data, dict) else None
        if not isinstance(sug, dict):
            continue
        built = build_suggestion(sug)
        if built:
            return built
    return _suggested_override_from_natural_reply(reply, context, build_suggestion)


def _extract_labeled_block(text, labels):
    label_pat = "|".join(re.escape(x) for x in labels)
    next_pat = (
        r"結論|判定|判断|理由|根拠|該当箇所|引用本文|引用テキスト|cited_location|"
        r"cited_text|judgment_reason|推奨上書き文案|上書き文案|上書きメモ|user_note"
    )
    m = re.search(
        rf"(?:^|\n)\s*(?:[-*・]\s*)?(?:{label_pat})\s*[:：]\s*(.+?)"
        rf"(?=\n\s*(?:[-*・]\s*)?(?:{next_pat})\s*[:：]|\Z)",
        str(text or ""),
        flags=re.DOTALL | re.IGNORECASE,
    )
    return (m.group(1).strip() if m else "")


def _strip_json_blocks(text):
    text = re.sub(r"```(?:json)?\s*\{.*?\}\s*```", "", str(text or ""), flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def _locations_from_text(text):
    locs = []
    pats = [
        r"【\s*[0-9０-９]{2,6}\s*】",
        r"段落\s*[0-9０-９]{2,6}",
        r"比較\s*例\s*[0-9０-９]+",
        r"実施\s*例\s*[0-9０-９]+",
        r"表\s*[0-9０-９]+",
    ]
    for pat in pats:
        for m in re.finditer(pat, str(text or "")):
            s = re.sub(r"\s+", "", m.group(0)).translate(_FW_DIGITS)
            try:
                from modules.cited_ref_notation import normalize as _norm
                s = _norm(s)
            except Exception:
                pass
            if s not in locs:
                locs.append(s)
    return ";".join(locs[:8])


def _suggested_override_from_natural_reply(reply, context, build_suggestion):
    text = str(reply or "")
    clean = _strip_json_blocks(text)
    judgment = ""
    m = re.search(r"([○△×])\s*(?:→|⇒|=>|->)\s*([○△×])", clean)
    if m:
        judgment = m.group(2)
    if not judgment:
        m = re.search(r"(?:結論|判定|判断)\s*[:：]?[^\n○△×]{0,80}([○△×])", clean)
        if m:
            judgment = m.group(1)
    if not judgment and re.search(r"(充足|一致|開示).{0,20}(認定|判断|相当|できる)", clean):
        if not re.search(r"(不充足|不一致|部分一致|一部|△|×)", clean[:500]):
            judgment = "○"

    reason = _extract_labeled_block(clean, ["judgment_reason", "理由", "根拠", "推奨上書き文案", "上書き文案"])
    if not reason:
        reason = clean
    reason = re.sub(r"\n{3,}", "\n\n", reason).strip()
    if len(reason) > 1600:
        reason = reason[:1600].rstrip() + "..."

    cited_location = (
        _extract_labeled_block(clean, ["cited_location", "該当箇所"])
        or _locations_from_text(clean)
    )
    cited_text = _extract_labeled_block(clean, ["cited_text", "引用本文", "引用テキスト"])
    if not cited_text:
        paras = context.get("relevant_paragraphs") or []
        cited_text = "\n".join(
            f"【{p.get('para_no', '')}】{p.get('text', '')}"
            for p in paras[:2]
            if p.get("text")
        )[:1400]

    if not (judgment or reason or cited_location or cited_text):
        return None
    return build_suggestion({
        "apply": True,
        "judgment": judgment,
        "judgment_reason": reason,
        "cited_location": cited_location,
        "cited_text": cited_text,
        "user_note": "壁打ち自然文から文案反映",
    }, from_fallback=True)


def chat_cell(case_id, citation_id, segment_id, message, model=None, target_kind="comparison"):
    segment_id = str(segment_id or "").strip()
    if not segment_id:
        return {"error": "segment_id は必須です"}, 400
    message = (message or "").strip()
    if not message:
        return {"error": "message は必須です"}, 400
    target_kind = "sub_claim" if target_kind == "sub_claim" else "comparison"
    context = build_cell_context(case_id, citation_id, segment_id, message=message, target_kind=target_kind)
    path = _chat_path(case_id, citation_id, segment_id, target_kind)
    user_row = {"role": "user", "content": message, "created_at": _now()}
    _append_jsonl(path, user_row)
    try:
        reply = call_claude(_build_prompt(context, message), timeout=300, model=model or "sonnet")
    except ClaudeClientError as e:
        err = {"role": "assistant", "content": f"LLM呼び出しエラー: {e}", "created_at": _now(), "error": True}
        _append_jsonl(path, err)
        return {"error": str(e), "messages": _load_jsonl(path), "context": context}, 502
    suggested = _suggested_override_from_reply(reply, context)
    asst_row = {"role": "assistant", "content": reply, "created_at": _now()}
    if suggested:
        asst_row["suggested_override"] = suggested
    _append_jsonl(path, asst_row)
    return {
        "reply": reply,
        "messages": _load_jsonl(path),
        "context": context,
        "suggested_override": suggested,
    }, 200


def apply_judgment_override(case_id, citation_id, segment_id, fields, user_note="", chat_ref=None, target_kind="comparison"):
    segment_id = str(segment_id or "").strip()
    if not segment_id:
        return {"error": "segment_id は必須です"}, 400
    case_dir = get_case_dir(case_id)
    resp_path = case_dir / "responses" / f"{citation_id}.json"
    data = _read_json(resp_path, None)
    if data is None:
        return {"error": f"回答データがありません: {citation_id}"}, 404
    target_kind = "sub_claim" if target_kind == "sub_claim" else "comparison"
    comp = _find_sub_claim_response(data, segment_id) if target_kind == "sub_claim" else _find_comparison(data, segment_id)
    if comp is None:
        return {"error": f"対象セルが見つかりません: {segment_id}"}, 404

    original = {k: comp.get(k, "") for k in ("judgment", "judgment_reason", "cited_location", "cited_text")}
    allowed = ("judgment", "judgment_reason", "cited_location", "cited_text")
    updated = {}
    for key in allowed:
        if key in fields and fields[key] is not None:
            comp[key] = str(fields[key])
            if key == "cited_location" and comp[key]:
                try:
                    from modules.cited_ref_notation import normalize as _norm
                    comp[key] = _norm(comp[key])
                except Exception:
                    pass
            updated[key] = comp[key]

    ts = _now()
    prefix = "subclaim" if target_kind == "sub_claim" else "comparison"
    rel_chat = chat_ref or str(Path("chat") / prefix / f"{_safe_name(citation_id)}_{_safe_name(segment_id)}.jsonl")
    override_key = f"sub_claim:{segment_id}" if target_kind == "sub_claim" else str(segment_id)
    data.setdefault("overrides", {})
    data["overrides"][override_key] = {
        "target_kind": target_kind,
        "segment_id": str(segment_id),
        "original": original,
        "updated": updated,
        "user_note": user_note or "",
        "chat_ref": rel_chat,
        "timestamp": ts,
    }
    comp["_edited_at"] = ts
    comp["_edited_by"] = "user"
    comp["_override_note"] = user_note or ""
    _write_json(resp_path, data)
    from services.comparison.response import _decorate_comparison_with_notation
    return {
        "success": True,
        "updated": updated,
        "override": data["overrides"][override_key],
        "doc": _decorate_comparison_with_notation(data),
    }, 200


def list_unmet_cells(case_id, citation_ids=None):
    case_dir = get_case_dir(case_id)
    segs = _read_json(case_dir / "segments.json", [])
    seg_text = {}
    for claim in segs or []:
        for seg in claim.get("segments") or []:
            seg_text[str(seg.get("id"))] = seg.get("text", "")
    rows = []
    resp_dir = case_dir / "responses"
    if not resp_dir.exists():
        return {"cells": rows}, 200
    wanted = None
    if citation_ids is not None:
        wanted = {str(x) for x in (citation_ids or []) if str(x).strip()}
    for path in sorted(resp_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        if wanted is not None and path.stem not in wanted:
            continue
        data = _read_json(path, {})
        overrides = data.get("overrides") or {}
        for comp in data.get("comparisons") or []:
            judgment = str(comp.get("judgment") or "").strip()
            if judgment == "○":
                continue
            sid = str(comp.get("requirement_id") or "")
            ov = overrides.get(sid) or {}
            reviewed_at = comp.get("_edited_at") or ov.get("timestamp") or ""
            rows.append({
                "target_kind": "comparison",
                "citation_id": path.stem,
                "segment_id": sid,
                "segment_text": seg_text.get(sid, ""),
                "judgment": judgment,
                "reason": comp.get("judgment_reason", ""),
                "cited_location": comp.get("cited_location", ""),
                "edited_at": comp.get("_edited_at", ""),
                "reviewed": bool(reviewed_at),
                "reviewed_at": reviewed_at,
                "override_note": comp.get("_override_note") or ov.get("user_note", ""),
            })
        for comp in data.get("sub_claims") or []:
            judgment = str(comp.get("judgment") or "").strip()
            if judgment == "○":
                continue
            cn = comp.get("claim_number")
            ov = overrides.get(f"sub_claim:{cn}") or {}
            reviewed_at = comp.get("_edited_at") or ov.get("timestamp") or ""
            rows.append({
                "target_kind": "sub_claim",
                "citation_id": path.stem,
                "segment_id": str(cn or ""),
                "segment_text": f"請求項{cn}",
                "judgment": judgment,
                "reason": comp.get("judgment_reason", ""),
                "cited_location": comp.get("cited_location", ""),
                "edited_at": comp.get("_edited_at", ""),
                "reviewed": bool(reviewed_at),
                "reviewed_at": reviewed_at,
                "override_note": comp.get("_override_note") or ov.get("user_note", ""),
            })
    rows.sort(key=lambda r: (1 if r.get("reviewed") else 0, r.get("citation_id", ""), r.get("segment_id", "")))
    return {"cells": rows, "count": len(rows), "citation_ids": sorted(wanted) if wanted is not None else None}, 200
