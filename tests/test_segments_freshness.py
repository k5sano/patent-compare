"""segments.json と responses/*.json の整合性検出 (silent stale 防止) のテスト。

ユーザー報告: 「分節編集が対比に反映されない」silent stale バグの検出機構。
- responses 側に存在するが現分節に無い ID = orphan
- 現分節に存在するが responses 側に無い ID = missing
- segments.json mtime > 最古 response mtime = stale_by_mtime
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from services import case_service
from services.comparison_service import check_segments_freshness


@pytest.fixture
def case_with_segments(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    (tmp_path / "cases").mkdir()
    case_id = "2030-fresh"
    case_service.create_minimal_case(case_id, title="x", field="cosmetics")
    case_dir = tmp_path / "cases" / case_id

    segs = [
        {"claim_number": 1, "is_independent": True, "dependencies": [],
         "segments": [
             {"id": "1A", "text": "成分A"},
             {"id": "1B", "text": "成分B"},
             {"id": "1C", "text": "成分C"},
         ]},
    ]
    with open(case_dir / "segments.json", "w", encoding="utf-8") as f:
        json.dump(segs, f, ensure_ascii=False)
    return case_id, case_dir


def _make_response(case_dir, doc_id, requirement_ids):
    rdir = case_dir / "responses"
    rdir.mkdir(parents=True, exist_ok=True)
    data = {
        "document_id": doc_id,
        "comparisons": [
            {"requirement_id": rid, "judgment": "○", "judgment_reason": ""}
            for rid in requirement_ids
        ],
    }
    p = rdir / f"{doc_id}.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return p


class TestNoResponses:
    def test_no_responses_dir(self, case_with_segments):
        case_id, _ = case_with_segments
        out, code = check_segments_freshness(case_id)
        assert code == 200
        assert out["has_responses"] is False
        assert out["needs_recompare"] is False

    def test_empty_responses_dir(self, case_with_segments):
        case_id, case_dir = case_with_segments
        (case_dir / "responses").mkdir(parents=True, exist_ok=True)
        out, _ = check_segments_freshness(case_id)
        assert out["has_responses"] is False
        assert out["response_count"] == 0


class TestMatched:
    def test_all_segment_ids_present(self, case_with_segments):
        case_id, case_dir = case_with_segments
        _make_response(case_dir, "DOC1", ["1A", "1B", "1C"])
        out, _ = check_segments_freshness(case_id)
        assert out["has_responses"] is True
        assert out["response_count"] == 1
        assert out["missing_in_responses"] == []
        assert out["orphans_in_responses"] == {}
        # mtime は同時生成 ≈ 同じ → stale_by_mtime False
        assert out["needs_recompare"] is False

    def test_underscore_files_excluded(self, case_with_segments):
        """_raw_*.txt や _last_raw_response.txt 等の作業ファイルはカウントしない"""
        case_id, case_dir = case_with_segments
        _make_response(case_dir, "DOC1", ["1A", "1B", "1C"])
        # _raw 系のゴミファイル
        (case_dir / "responses" / "_last_raw_response.txt").write_text("garbage")
        (case_dir / "responses" / "_raw_DOC1.txt").write_text("garbage")
        out, _ = check_segments_freshness(case_id)
        # response_count は 1 のまま (DOC1.json のみ)
        assert out["response_count"] == 1


class TestMissing:
    def test_segments_added_after_compare(self, case_with_segments):
        """response に無い分節 (新規追加分) を missing として検出"""
        case_id, case_dir = case_with_segments
        # 旧対比は 1A だけだった
        _make_response(case_dir, "DOC1", ["1A"])
        out, _ = check_segments_freshness(case_id)
        assert out["needs_recompare"] is True
        assert set(out["missing_in_responses"]) == {"1B", "1C"}
        assert out["orphans_in_responses"] == {}


class TestOrphans:
    def test_segment_renamed_creates_orphan(self, case_with_segments):
        """分節 ID 変更後、response 側に古い ID が残る"""
        case_id, case_dir = case_with_segments
        # response は 1A/1B/1X (1X は現分節に存在しない)
        _make_response(case_dir, "DOC1", ["1A", "1B", "1X"])
        out, _ = check_segments_freshness(case_id)
        assert out["needs_recompare"] is True
        assert out["missing_in_responses"] == ["1C"]  # 現分節 1C は判定なし
        assert "DOC1" in out["orphans_in_responses"]
        assert out["orphans_in_responses"]["DOC1"] == ["1X"]

    def test_orphans_grouped_by_document(self, case_with_segments):
        case_id, case_dir = case_with_segments
        _make_response(case_dir, "DOC1", ["1A", "1Z"])
        _make_response(case_dir, "DOC2", ["1B", "1Y"])
        out, _ = check_segments_freshness(case_id)
        assert "DOC1" in out["orphans_in_responses"]
        assert "DOC2" in out["orphans_in_responses"]
        assert out["orphans_in_responses"]["DOC1"] == ["1Z"]
        assert out["orphans_in_responses"]["DOC2"] == ["1Y"]


class TestStaleByMtime:
    def test_segments_newer_than_response(self, case_with_segments):
        """segments.json の mtime > response mtime なら stale"""
        case_id, case_dir = case_with_segments
        _make_response(case_dir, "DOC1", ["1A", "1B", "1C"])
        # response の mtime を過去に巻き戻す
        old = time.time() - 3600
        os.utime(case_dir / "responses" / "DOC1.json", (old, old))
        # segments.json は今 (現在時刻のまま)
        out, _ = check_segments_freshness(case_id)
        assert out["stale_by_mtime"] is True
        # ただし ID 集合は一致しているので missing/orphan は無し
        assert out["missing_in_responses"] == []
        assert out["orphans_in_responses"] == {}
        assert out["needs_recompare"] is True  # mtime 不一致だけでも要再対比


class TestCaseNotFound:
    def test_404_for_unknown_case(self, tmp_path, monkeypatch):
        monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
        (tmp_path / "cases").mkdir()
        out, code = check_segments_freshness("non-existent")
        assert code == 404


class TestRenderedBanner:
    """case.html が freshness を Step5/Step6 のバナーとして埋め込み、
    かつ bootstrap data (window.CASE_BOOTSTRAP.freshness) にも露出することを確認。"""

    def _setup(self, tmp_path, monkeypatch, case_id="2030-banner"):
        import web
        monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
        (tmp_path / "cases").mkdir()
        case_service.create_minimal_case(case_id, title="x", field="cosmetics")
        case_dir = tmp_path / "cases" / case_id
        # 最低限の hongan + segments を入れて render が成功するように
        with open(case_dir / "hongan.json", "w", encoding="utf-8") as f:
            json.dump({"patent_number": "JP", "patent_title": "T", "total_pages": 1,
                       "claims": [{"number": 1, "text": "x"}],
                       "paragraphs": [{"id": "0001", "text": "x", "section": "実施例"}],
                       "tables": []}, f, ensure_ascii=False)
        with open(case_dir / "segments.json", "w", encoding="utf-8") as f:
            json.dump([{"claim_number": 1, "is_independent": True, "dependencies": [],
                        "segments": [{"id": "1A", "text": "成分A"},
                                     {"id": "1B", "text": "成分B"}]}], f, ensure_ascii=False)
        web.app.config["TESTING"] = True
        return case_id, case_dir, web.app

    def test_no_banner_when_no_responses(self, tmp_path, monkeypatch):
        case_id, _, app = self._setup(tmp_path, monkeypatch)
        client = app.test_client()
        body = client.get(f"/case/{case_id}").get_data(as_text=True)
        assert "freshness-banner" not in body
        # bootstrap には has_responses: false が入る
        assert '"has_responses": false' in body

    def test_banner_when_mismatch_exists(self, tmp_path, monkeypatch):
        case_id, case_dir, app = self._setup(tmp_path, monkeypatch)
        # 旧分節 1X だけ含む response (現分節 1A/1B に該当無し → orphan + missing)
        _make_response(case_dir, "DOC1", ["1X"])
        client = app.test_client()
        body = client.get(f"/case/{case_id}").get_data(as_text=True)
        assert "freshness-banner" in body
        assert "分節と対比結果に不整合があります" in body
        # bootstrap に needs_recompare: true
        assert '"needs_recompare": true' in body

    def test_no_banner_when_matched(self, tmp_path, monkeypatch):
        case_id, case_dir, app = self._setup(tmp_path, monkeypatch)
        _make_response(case_dir, "DOC1", ["1A", "1B"])  # 完全一致
        client = app.test_client()
        body = client.get(f"/case/{case_id}").get_data(as_text=True)
        assert "freshness-banner" not in body
