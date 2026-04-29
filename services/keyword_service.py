#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""キーワード管理サービス"""

import json
from pathlib import Path

from services.case_service import get_case_dir, load_case_meta


def _load_keywords(case_id):
    """keywords.json を読み込む"""
    kw_path = get_case_dir(case_id) / "keywords.json"
    if not kw_path.exists():
        return None, kw_path
    try:
        with open(kw_path, "r", encoding="utf-8") as f:
            text = f.read()
        text = text.replace('\x00', '')
        return json.loads(text, strict=False), kw_path
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, kw_path


def _save_keywords(kw_path, groups):
    """keywords.json を保存"""
    with open(kw_path, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)


def get_keywords(case_id):
    groups, _ = _load_keywords(case_id)
    if groups is None:
        return {"error": "キーワードデータがありません"}, 404
    return groups, 200


def add_keyword(case_id, group_id, term):
    groups, kw_path = _load_keywords(case_id)
    if groups is None:
        return {"error": "キーワードデータがありません"}, 404

    term = term.strip()
    if not term:
        return {"error": "キーワードを入力してください"}, 400

    for group in groups:
        if group["group_id"] == group_id:
            group["keywords"].append({
                "term": term,
                "source": "手動",
                "type": "手動追加",
            })
            break
    else:
        return {"error": f"グループ{group_id}が見つかりません"}, 404

    _save_keywords(kw_path, groups)
    return {"success": True}, 200


def delete_keyword(case_id, group_id, term):
    groups, kw_path = _load_keywords(case_id)
    if groups is None:
        return {"error": "キーワードデータがありません"}, 404

    for group in groups:
        if group["group_id"] == group_id:
            group["keywords"] = [
                kw for kw in group["keywords"] if kw["term"] != term
            ]
            break
    else:
        return {"error": f"グループ{group_id}が見つかりません"}, 404

    _save_keywords(kw_path, groups)
    return {"success": True}, 200


def edit_keyword(case_id, group_id, old_term, new_term):
    groups, kw_path = _load_keywords(case_id)
    if groups is None:
        return {"error": "キーワードデータがありません"}, 404

    old_term = (old_term or "").strip()
    new_term = (new_term or "").strip()
    if not old_term or not new_term:
        return {"error": "old_term と new_term は必須です"}, 400

    updated = False
    for group in groups:
        if group["group_id"] == group_id:
            for kw in group["keywords"]:
                if kw["term"] == old_term:
                    kw["term"] = new_term
                    updated = True
                    break
            break
    else:
        return {"error": f"グループ{group_id}が見つかりません"}, 404

    if not updated:
        return {"error": f"キーワード「{old_term}」が見つかりません"}, 404

    _save_keywords(kw_path, groups)
    return {"success": True}, 200


def rebuild_keywords_from_tech_analysis(case_id):
    """Step 4 Stage 1 の `tech_analysis.json` を真実の源として、
    Step 3 のキーワードグループを **要素単位 (E1/E2/...)** に再構築する。

    - element ごとに 1 グループ (group_id を 1 から順に振り直す)
    - label = element.label
    - segment_ids = element.segment_ids
    - keywords は element の各語彙ソース (claim/definition/example/synonyms) を統合
    - 既存 keywords.json に **手動追加された語句や F-term** は、
      segment_ids の重なりが最大の新グループへ自動移行 (重なりなしなら最初の要素 へ)。
    """
    from services.case_service import get_case_dir as _gcd
    case_dir = _gcd(case_id)
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404

    ta_path = case_dir / "search" / "tech_analysis.json"
    if not ta_path.exists():
        return {
            "error": "Step 4 Stage 1 の技術構造化が未実行です。先に Step 4 → Stage 1 を完了してください。",
        }, 400
    try:
        with open(ta_path, "r", encoding="utf-8") as f:
            ta = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return {"error": f"tech_analysis.json 読み込みエラー: {e}"}, 500

    elements = ta.get("elements") or {}
    if not elements:
        return {"error": "tech_analysis.json に elements がありません"}, 400

    # element key の順序維持 (Python 3.7+ で dict 挿入順保持)
    element_items = list(elements.items())

    # 1) 既存グループから「ユーザー手動追加分」と「F-term」を抽出 (再投入用)
    existing, kw_path = _load_keywords(case_id)
    existing = existing or []
    # キーワードをまとめて (term -> {kw, original_segs})
    extra_kws = []     # term/type/source/segments_of_origin
    extra_fterms = []  # F-term tags
    for og in existing:
        og_segs = set(og.get("segment_ids") or [])
        for kw in (og.get("keywords") or []):
            src = (kw.get("source") or "").strip()
            typ = (kw.get("type") or "").strip()
            # F-term は特別扱い
            if typ == "Fターム" or "fterm" in src.lower():
                extra_fterms.append({"kw": kw, "segs": og_segs})
                continue
            # 手動追加 / 手動ソースは保持
            if src == "手動" or typ == "手動追加":
                extra_kws.append({"kw": kw, "segs": og_segs})

    # 2) 新グループを elements から生成
    new_groups = []
    for idx, (key, elem) in enumerate(element_items, start=1):
        label = (elem.get("label") or key or f"要素{idx}").strip() or f"要素{idx}"
        seg_ids = list(elem.get("segment_ids") or [])

        keywords = []
        seen_terms = set()

        def _add_term(term, type_, source):
            t = (term or "").strip()
            if not t or t in seen_terms:
                return
            seen_terms.add(t)
            keywords.append({"term": t, "type": type_, "source": source})

        # claim_terms (str list)
        for t in (elem.get("claim_terms") or []):
            _add_term(t, "請求項由来", "claim")

        # definition_terms (dict list)
        for d in (elem.get("definition_terms") or []):
            if isinstance(d, dict):
                _add_term(d.get("term", ""), d.get("type") or "定義", f"定義 段落{d.get('para', '')}")
            elif isinstance(d, str):
                _add_term(d, "定義", "定義")

        # example_terms (dict list)
        for d in (elem.get("example_terms") or []):
            if isinstance(d, dict):
                _add_term(d.get("term", ""), "実施例由来", f"実施例 段落{d.get('para', '')}")
            elif isinstance(d, str):
                _add_term(d, "実施例由来", "実施例")

        # synonyms
        syn = elem.get("synonyms") or {}
        for t in (syn.get("ja") or []):
            _add_term(t, "同義語(和)", "synonym_ja")
        for t in (syn.get("en") or []):
            _add_term(t, "同義語(英)", "synonym_en")

        new_groups.append({
            "group_id": idx,
            "label": label,
            "segment_ids": seg_ids,
            "keywords": keywords,
            "search_codes": {},
            "_seen_terms": seen_terms,  # マージ重複判定用 (最後に削除)
        })

    if not new_groups:
        return {"error": "elements から有効なグループを生成できませんでした"}, 500

    # 3) 既存の手動 KW / Fターム を segment_ids の重なりが最大の新グループへ移行
    def _best_group_for(orig_segs):
        if not orig_segs:
            return new_groups[0]
        best = new_groups[0]
        best_overlap = -1
        for g in new_groups:
            overlap = len(set(g["segment_ids"]) & orig_segs)
            if overlap > best_overlap:
                best_overlap = overlap
                best = g
        return best

    for item in extra_kws:
        kw = item["kw"]
        term = (kw.get("term") or "").strip()
        if not term:
            continue
        target = _best_group_for(item["segs"])
        if term in target["_seen_terms"]:
            continue
        target["_seen_terms"].add(term)
        target["keywords"].append(kw)

    for item in extra_fterms:
        # F-term は keywords 配列ではなく専用フィールド (search_codes.fterm) に置かれている場合あり
        # 既存の置き場所を尊重して keywords に入れる (renderKwTag 側で type で判別)
        target = _best_group_for(item["segs"])
        kw = item["kw"]
        code = (kw.get("term") or kw.get("code") or "").strip()
        if not code:
            continue
        if code in target["_seen_terms"]:
            continue
        target["_seen_terms"].add(code)
        target["keywords"].append(kw)

    # 一時フィールドを削除
    for g in new_groups:
        g.pop("_seen_terms", None)

    # 4) 保存
    if kw_path is None:
        kw_path = case_dir / "keywords.json"
    _save_keywords(kw_path, new_groups)

    return {
        "success": True,
        "num_groups": len(new_groups),
        "groups": new_groups,
        "summary": [
            {"group_id": g["group_id"], "label": g["label"],
             "segment_ids": g["segment_ids"], "num_keywords": len(g["keywords"])}
            for g in new_groups
        ],
    }, 200


def add_keyword_group(case_id, label="新規グループ", segment_ids=None):
    case_dir = get_case_dir(case_id)
    kw_path = case_dir / "keywords.json"

    groups = []
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            groups = json.load(f)

    new_id = max((g["group_id"] for g in groups), default=0) + 1
    groups.append({
        "group_id": new_id,
        "label": label.strip() or "新規グループ",
        "segment_ids": segment_ids or [],
        "keywords": [],
        "search_codes": {},
    })

    _save_keywords(kw_path, groups)
    return {"success": True, "group_id": new_id}, 200


def delete_keyword_group(case_id, group_id):
    groups, kw_path = _load_keywords(case_id)
    if groups is None:
        return {"error": "キーワードデータがありません"}, 404

    groups = [g for g in groups if g["group_id"] != group_id]
    _save_keywords(kw_path, groups)
    return {"success": True}, 200


def update_keyword_group(case_id, group_id, data):
    groups, kw_path = _load_keywords(case_id)
    if groups is None:
        return {"error": "キーワードデータがありません"}, 404

    for group in groups:
        if group["group_id"] == group_id:
            if "label" in data:
                group["label"] = data["label"]
            if "segment_ids" in data:
                group["segment_ids"] = data["segment_ids"]
            break
    else:
        return {"error": f"グループ{group_id}が見つかりません"}, 404

    _save_keywords(kw_path, groups)
    return {"success": True}, 200


def _fterm_short_code(code: str) -> str:
    """フル F-term コード（例: 4C083AB13）から末尾のサフィックス（AB13）を取り出す。

    テーマ ID 直後の英字から末尾までを返す。マッチしない場合は元のコードを返す。
    """
    import re
    m = re.match(r"^\d{1,2}[A-Z]\d{3}([A-Z]{2}\d{2,3})$", code or "")
    return m.group(1) if m else (code or "")


def _fterm_lookup_node(code: str, dict_nodes: dict):
    """F-term コードを辞書から段階的に引く。

    試行順:
      1. 入力コードそのまま (`AA01` 等)
      2. テーマプレフィックスを剥がした短縮形 (`4C083AA01` → `AA01`)
      3. 短縮形からさらに付加コード (請求項=1 / 実施例=2 等の末尾 1 桁) を剥がす
         (`AA011` → `AA01`)

    見つかればノード dict、見つからなければ None。
    """
    import re
    if not code or not dict_nodes:
        return None
    if code in dict_nodes:
        return dict_nodes[code]
    short = _fterm_short_code(code)
    if short in dict_nodes:
        return dict_nodes[short]
    m = re.match(r"^([A-Z]{2}\d{2})\d$", short)
    if m and m.group(1) in dict_nodes:
        return dict_nodes[m.group(1)]
    return None


def enrich_fterm_groups(groups, field: str = "cosmetics"):
    """各キーワードグループの F-term の desc が空なら辞書から補完する (in-place)。

    keywords.json には保存しない。ビュー直前に表示用補完するための呼び出し用。
    """
    from modules.fterm_dict import get_nodes
    try:
        dict_nodes = get_nodes(field)
    except Exception:
        return groups
    if not dict_nodes:
        return groups
    for g in groups or []:
        fts = (g.get("search_codes") or {}).get("fterm") or []
        for ft in fts:
            if ft.get("desc"):
                continue
            node = _fterm_lookup_node(ft.get("code", ""), dict_nodes)
            if node and node.get("label"):
                ft["desc"] = node["label"]
    return groups


def fterm_candidates(case_id):
    """本願のFterm候補一覧を返す。

    各候補は以下を含む:
        code     : Ftermコード
        label    : 日本語説明
        source   : "本願分類" | "既存グループ" | "辞書"
        type     : 本願分類のみ (core/related/main/sub 等)
        note     : 本願分類のみ (補足コメント)
        examples : 辞書にある場合の例示語 (最大3件)
    """
    from modules.fterm_dict import get_nodes

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    field = meta.get("field", "cosmetics") if meta else "cosmetics"

    try:
        dict_nodes = get_nodes(field)
    except Exception:
        dict_nodes = {}

    def dict_examples(code: str) -> list:
        """フルコード or 短縮コードから辞書の examples を最大3件取得"""
        node = _fterm_lookup_node(code, dict_nodes)
        if not node:
            return []
        return (node.get("examples") or [])[:3]

    def dict_label(code: str) -> str:
        """辞書ラベル (説明) を返す。見つからなければ空文字。"""
        node = _fterm_lookup_node(code, dict_nodes)
        return (node.get("label") or "") if node else ""

    candidates = []
    seen = set()

    cls_path = case_dir / "search" / "classification.json"
    if cls_path.exists():
        with open(cls_path, "r", encoding="utf-8") as f:
            cls_data = json.load(f)
        for ft in cls_data.get("fterm", []):
            code = ft.get("code", "")
            if code and code not in seen:
                seen.add(code)
                candidates.append({
                    "code": code,
                    "label": ft.get("label", "") or dict_label(code),
                    "source": "本願分類",
                    "type": ft.get("type", ""),
                    "note": ft.get("note", ""),
                    "examples": dict_examples(code),
                })

    kw_path = case_dir / "keywords.json"
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            groups = json.load(f)
        for g in groups:
            for ft in g.get("search_codes", {}).get("fterm", []):
                code = ft.get("code", "")
                if code and code not in seen:
                    seen.add(code)
                    candidates.append({
                        "code": code,
                        "label": ft.get("desc", "") or dict_label(code),
                        "source": "既存グループ",
                        "examples": dict_examples(code),
                    })

    for code, node in dict_nodes.items():
        if node.get("depth", 0) >= 2 and code not in seen:
            seen.add(code)
            candidates.append({
                "code": code,
                "label": node.get("label", ""),
                "source": "辞書",
                "examples": (node.get("examples") or [])[:3],
            })

    return candidates, 200


def add_fterm(case_id, group_id, code, desc=""):
    groups, kw_path = _load_keywords(case_id)
    if groups is None:
        return {"error": "キーワードデータがありません"}, 404

    code = (code or "").strip()
    if not code:
        return {"error": "Ftermコードを入力してください"}, 400

    for group in groups:
        if group["group_id"] == group_id:
            if "search_codes" not in group:
                group["search_codes"] = {}
            if "fterm" not in group["search_codes"]:
                group["search_codes"]["fterm"] = []
            existing = [ft["code"] for ft in group["search_codes"]["fterm"]]
            if code in existing:
                return {"error": f"Fterm「{code}」は既に存在します"}, 400
            group["search_codes"]["fterm"].append({"code": code, "desc": desc})
            break
    else:
        return {"error": f"グループ{group_id}が見つかりません"}, 404

    _save_keywords(kw_path, groups)
    return {"success": True, "code": code, "desc": desc}, 200


def delete_fterm(case_id, group_id, code):
    groups, kw_path = _load_keywords(case_id)
    if groups is None:
        return {"error": "キーワードデータがありません"}, 404

    for group in groups:
        if group["group_id"] == group_id:
            if "search_codes" in group and "fterm" in group["search_codes"]:
                group["search_codes"]["fterm"] = [
                    ft for ft in group["search_codes"]["fterm"] if ft["code"] != code
                ]
            break
    else:
        return {"error": f"グループ{group_id}が見つかりません"}, 404

    _save_keywords(kw_path, groups)
    return {"success": True}, 200


def suggest_keywords(case_id):
    """AIキーワード提案"""
    from modules.keyword_recommender import recommend_by_tech_analysis
    from modules.keyword_suggester import build_keyword_groups_from_pipeline

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)

    hongan_path = case_dir / "hongan.json"
    segments_path = case_dir / "segments.json"
    if not hongan_path.exists() or not segments_path.exists():
        return {"error": "本願テキストまたは分節データがありません"}, 400

    with open(hongan_path, "r", encoding="utf-8") as f:
        hongan = json.load(f)
    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    field = meta.get("field", "cosmetics")

    # 既存の手動追加キーワードを保存
    kw_path = case_dir / "keywords.json"
    manual_by_seg = {}
    if kw_path.exists():
        try:
            with open(kw_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            for group in existing:
                for seg_id in group.get("segment_ids", []):
                    for kw in group.get("keywords", []):
                        if kw.get("type") == "手動追加" or kw.get("source") in ("manual", "手動"):
                            manual_by_seg.setdefault(seg_id, []).append(kw)
        except (json.JSONDecodeError, ValueError):
            pass

    tech_analysis, pipeline_result = recommend_by_tech_analysis(segs, hongan, field)

    if tech_analysis:
        with open(case_dir / "tech_analysis.json", "w", encoding="utf-8") as f:
            json.dump(tech_analysis, f, ensure_ascii=False, indent=2)

    with open(case_dir / "segment_keywords.json", "w", encoding="utf-8") as f:
        json.dump(pipeline_result, f, ensure_ascii=False, indent=2)

    ai_groups = build_keyword_groups_from_pipeline(pipeline_result, segs, field, hongan=hongan)

    # 手動追加キーワードを復元マージ
    for group in ai_groups:
        existing_terms = {kw["term"] for kw in group["keywords"]}
        for seg_id in group.get("segment_ids", []):
            for mkw in manual_by_seg.get(seg_id, []):
                if mkw["term"] not in existing_terms:
                    group["keywords"].append(mkw)
                    existing_terms.add(mkw["term"])

    with open(kw_path, "w", encoding="utf-8") as f:
        json.dump(ai_groups, f, ensure_ascii=False, indent=2)

    return ai_groups, 200


def suggest_keywords_by_segment(case_id):
    """分節別キーワード提案"""
    from modules.keyword_recommender import recommend_by_tech_analysis

    case_dir = get_case_dir(case_id)
    meta = load_case_meta(case_id)
    if not meta:
        return {"error": "案件が見つかりません"}, 404

    hongan_path = case_dir / "hongan.json"
    segments_path = case_dir / "segments.json"
    if not hongan_path.exists() or not segments_path.exists():
        return {"error": "本願テキストまたは分節データがありません"}, 400

    with open(hongan_path, "r", encoding="utf-8") as f:
        hongan = json.load(f)
    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    field = meta.get("field", "cosmetics")
    tech_analysis, result = recommend_by_tech_analysis(segs, hongan, field)

    if tech_analysis:
        with open(case_dir / "tech_analysis.json", "w", encoding="utf-8") as f:
            json.dump(tech_analysis, f, ensure_ascii=False, indent=2)

    with open(case_dir / "segment_keywords.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return result, 200


def sync_to_keyword_groups(case_dir, seg_keywords, field):
    """segment_keywords.json → keywords.json への同期変換"""
    COLOR_NAMES = {
        1: "赤", 2: "紫", 3: "マゼンタ", 4: "青",
        5: "緑", 6: "オレンジ", 7: "ティール",
    }

    groups = []
    for i, item in enumerate(seg_keywords):
        if not item.get("keywords"):
            continue
        group_id = i + 1
        groups.append({
            "group_id": group_id,
            "label": item["segment_text"][:20] if item.get("segment_text") else item["segment_id"],
            "color": COLOR_NAMES.get(group_id, "黒"),
            "segment_ids": [item["segment_id"]],
            "keywords": [
                {"term": kw["term"], "source": kw.get("source", ""), "type": kw.get("type", "")}
                for kw in item["keywords"]
            ],
            "search_codes": {},
        })

    kw_path = Path(case_dir) / "keywords.json"
    with open(kw_path, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)


def add_keyword_to_segment(case_id, segment_id, term):
    """テキスト選択からキーワードを分節に追加"""
    case_dir = get_case_dir(case_id)
    sk_path = case_dir / "segment_keywords.json"

    term = (term or "").strip()
    segment_id = (segment_id or "").strip()
    if not term or not segment_id:
        return {"error": "term と segment_id は必須です"}, 400

    seg_keywords = []
    if sk_path.exists():
        with open(sk_path, "r", encoding="utf-8") as f:
            seg_keywords = json.load(f)

    found = False
    for item in seg_keywords:
        if item["segment_id"] == segment_id:
            if not any(kw["term"] == term for kw in item["keywords"]):
                item["keywords"].append({
                    "term": term,
                    "source": "manual",
                    "type": "手動追加",
                })
            found = True
            break

    if not found:
        seg_keywords.append({
            "segment_id": segment_id,
            "segment_text": "",
            "keywords": [{"term": term, "source": "manual", "type": "手動追加"}],
        })

    with open(sk_path, "w", encoding="utf-8") as f:
        json.dump(seg_keywords, f, ensure_ascii=False, indent=2)

    meta = load_case_meta(case_id)
    field = meta.get("field", "cosmetics") if meta else "cosmetics"
    sync_to_keyword_groups(case_dir, seg_keywords, field)

    return {"success": True}, 200


def remove_keyword_from_segment(case_id, segment_id, term):
    """キーワードを分節から削除"""
    case_dir = get_case_dir(case_id)
    sk_path = case_dir / "segment_keywords.json"

    if not sk_path.exists():
        return {"error": "分節別キーワードがありません"}, 404

    term = (term or "").strip()
    segment_id = (segment_id or "").strip()
    if not term or not segment_id:
        return {"error": "term と segment_id は必須です"}, 400

    with open(sk_path, "r", encoding="utf-8") as f:
        seg_keywords = json.load(f)

    for item in seg_keywords:
        if item["segment_id"] == segment_id:
            item["keywords"] = [kw for kw in item["keywords"] if kw["term"] != term]
            break

    with open(sk_path, "w", encoding="utf-8") as f:
        json.dump(seg_keywords, f, ensure_ascii=False, indent=2)

    meta = load_case_meta(case_id)
    field = meta.get("field", "cosmetics") if meta else "cosmetics"
    sync_to_keyword_groups(case_dir, seg_keywords, field)

    return {"success": True}, 200


def update_segment_keyword(case_id, segment_id, old_term, new_term):
    """分節内キーワードのtermを修正"""
    case_dir = get_case_dir(case_id)
    sk_path = case_dir / "segment_keywords.json"

    if not sk_path.exists():
        return {"error": "分節別キーワードがありません"}, 404

    segment_id = (segment_id or "").strip()
    old_term = (old_term or "").strip()
    new_term = (new_term or "").strip()
    if not segment_id or not old_term or not new_term:
        return {"error": "segment_id, old_term, new_term は必須です"}, 400

    with open(sk_path, "r", encoding="utf-8") as f:
        seg_keywords = json.load(f)

    updated = False
    for item in seg_keywords:
        if item["segment_id"] == segment_id:
            for kw in item["keywords"]:
                if kw["term"] == old_term:
                    kw["term"] = new_term
                    updated = True
                    break
            break

    if not updated:
        return {"error": "該当キーワードが見つかりません"}, 404

    with open(sk_path, "w", encoding="utf-8") as f:
        json.dump(seg_keywords, f, ensure_ascii=False, indent=2)

    return {"success": True}, 200
