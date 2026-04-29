"""Step 4 Stage 1 の tech_analysis.json から Step 3 のキーワードグループを再構築する
`rebuild_keywords_from_tech_analysis` の単体テスト。

意図: tech_analysis.json の elements を真実の源として、Step 3 のグループを
要素単位 (E1/E2/...) に作り直す。手動 KW / F-term は segment_ids 重なりで自動移行する。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import case_service
from services.keyword_service import rebuild_keywords_from_tech_analysis


@pytest.fixture
def case_with_tech_analysis(tmp_path, monkeypatch):
    """tech_analysis.json と既存 keywords.json (手動 KW / F-term 入り) を持つ案件"""
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    case_id = "2030-rebuild"
    case_service.create_minimal_case(case_id, title="x", field="cosmetics")
    case_dir = cases_dir / case_id

    # tech_analysis.json (Step 4 Stage 1 の出力)
    search_dir = case_dir / "search"
    search_dir.mkdir(parents=True, exist_ok=True)
    ta = {
        "elements": {
            "A_form": {
                "label": "化粧料の形態",
                "segment_ids": ["1A", "1K"],
                "claim_terms": ["乳化化粧料", "クリーム"],
                "definition_terms": [
                    {"term": "O/W乳化", "type": "形態", "para": "0008"},
                ],
                "example_terms": [],
                "synonyms": {
                    "ja": ["乳化物"],
                    "en": ["emulsion"],
                },
            },
            "B_oil": {
                "label": "油性成分",
                "segment_ids": ["1B", "1C"],
                "claim_terms": ["油性成分"],
                "definition_terms": [],
                "example_terms": [
                    {"term": "スクワラン", "para": "0020"},
                ],
                "synonyms": {"ja": [], "en": ["oil"]},
            },
        }
    }
    with open(search_dir / "tech_analysis.json", "w", encoding="utf-8") as f:
        json.dump(ta, f, ensure_ascii=False)

    # 既存 keywords.json (手動追加 + F-term + 他の自動由来あり)
    existing_kw = [
        {
            "group_id": 99,  # 古い番号
            "label": "古いグループA",
            "segment_ids": ["1A"],  # → A_form と重なり最大
            "keywords": [
                {"term": "AI由来語", "type": "請求項由来", "source": "claim"},  # 自動由来は捨てられる
                {"term": "ユーザー追加A", "type": "手動追加", "source": "手動"},
                {"term": "4C083AA12", "type": "Fターム", "source": "fterm"},
            ],
        },
        {
            "group_id": 100,
            "label": "古いグループB",
            "segment_ids": ["1B"],  # → B_oil と重なり
            "keywords": [
                {"term": "ユーザー追加B", "type": "手動追加", "source": "手動"},
            ],
        },
    ]
    with open(case_dir / "keywords.json", "w", encoding="utf-8") as f:
        json.dump(existing_kw, f, ensure_ascii=False)

    return case_id, case_dir


class TestRebuild:
    def test_groups_match_elements(self, case_with_tech_analysis):
        case_id, _ = case_with_tech_analysis
        result, code = rebuild_keywords_from_tech_analysis(case_id)
        assert code == 200
        assert result["success"] is True
        assert result["num_groups"] == 2
        groups = result["groups"]

        # 1 グループ目 = A_form
        g1 = groups[0]
        assert g1["group_id"] == 1
        assert g1["label"] == "化粧料の形態"
        assert g1["segment_ids"] == ["1A", "1K"]
        terms = {kw["term"] for kw in g1["keywords"]}
        # claim/definition/synonyms から取り込み
        assert "乳化化粧料" in terms
        assert "クリーム" in terms
        assert "O/W乳化" in terms
        assert "乳化物" in terms
        assert "emulsion" in terms

        # 2 グループ目 = B_oil
        g2 = groups[1]
        assert g2["group_id"] == 2
        assert g2["label"] == "油性成分"
        assert g2["segment_ids"] == ["1B", "1C"]
        terms2 = {kw["term"] for kw in g2["keywords"]}
        assert "油性成分" in terms2
        assert "スクワラン" in terms2  # example_terms から
        assert "oil" in terms2

    def test_manual_keywords_migrate_by_overlap(self, case_with_tech_analysis):
        """手動追加 KW は segment_ids 重なり最大の新グループへ移動"""
        case_id, _ = case_with_tech_analysis
        result, _ = rebuild_keywords_from_tech_analysis(case_id)
        groups = result["groups"]

        # ユーザー追加A は 1A 由来 → A_form (groups[0]) へ
        terms_a = {kw["term"] for kw in groups[0]["keywords"]}
        assert "ユーザー追加A" in terms_a
        assert "ユーザー追加A" not in {kw["term"] for kw in groups[1]["keywords"]}

        # ユーザー追加B は 1B 由来 → B_oil (groups[1]) へ
        terms_b = {kw["term"] for kw in groups[1]["keywords"]}
        assert "ユーザー追加B" in terms_b

    def test_fterm_migrates(self, case_with_tech_analysis):
        case_id, _ = case_with_tech_analysis
        result, _ = rebuild_keywords_from_tech_analysis(case_id)
        groups = result["groups"]
        # F-term 4C083AA12 は 1A 由来 → A_form
        codes = {kw.get("term") for kw in groups[0]["keywords"]
                 if kw.get("type") == "Fターム"}
        assert "4C083AA12" in codes

    def test_auto_keywords_dropped(self, case_with_tech_analysis):
        """旧自動由来 KW (source != 手動) は新グループに引き継がれない (再生成のため)"""
        case_id, _ = case_with_tech_analysis
        result, _ = rebuild_keywords_from_tech_analysis(case_id)
        all_terms = set()
        for g in result["groups"]:
            for kw in g["keywords"]:
                all_terms.add(kw["term"])
        # 古い "AI由来語" は捨てられる (新しい claim_terms 等で再生成される想定)
        assert "AI由来語" not in all_terms

    def test_persisted_to_keywords_json(self, case_with_tech_analysis):
        case_id, case_dir = case_with_tech_analysis
        result, _ = rebuild_keywords_from_tech_analysis(case_id)
        with open(case_dir / "keywords.json", "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert len(saved) == 2
        assert saved[0]["group_id"] == 1
        assert saved[1]["group_id"] == 2


class TestErrors:
    def test_missing_case(self, tmp_path, monkeypatch):
        monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
        (tmp_path / "cases").mkdir()
        result, code = rebuild_keywords_from_tech_analysis("non-existent")
        assert code == 404

    def test_missing_tech_analysis(self, tmp_path, monkeypatch):
        monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
        (tmp_path / "cases").mkdir()
        case_service.create_minimal_case("2030-no-ta", title="x", field="cosmetics")
        result, code = rebuild_keywords_from_tech_analysis("2030-no-ta")
        assert code == 400
        assert "Stage 1" in result["error"] or "tech_analysis" in result["error"]

    def test_empty_elements(self, tmp_path, monkeypatch):
        monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
        (tmp_path / "cases").mkdir()
        case_service.create_minimal_case("2030-empty-elem", title="x", field="cosmetics")
        case_dir = tmp_path / "cases" / "2030-empty-elem"
        (case_dir / "search").mkdir(parents=True, exist_ok=True)
        with open(case_dir / "search" / "tech_analysis.json", "w", encoding="utf-8") as f:
            json.dump({"elements": {}}, f)
        result, code = rebuild_keywords_from_tech_analysis("2030-empty-elem")
        assert code == 400
        assert "elements" in result["error"]
