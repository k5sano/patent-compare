"""壁打ち chat サービス (services/chat_service.py) の単体テスト。

LLM 呼び出しは monkeypatch でモックする。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import case_service, chat_service as cs


@pytest.fixture
def case_with_data(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    (tmp_path / "cases").mkdir()
    case_id = "2030-chat"
    case_service.create_minimal_case(case_id, title="x", field="cosmetics")
    case_dir = tmp_path / "cases" / case_id

    # 最低限の hongan / segments を入れて prompt 生成が機能するように
    with open(case_dir / "hongan.json", "w", encoding="utf-8") as f:
        json.dump({
            "patent_number": "JP2030-X", "patent_title": "テスト発明",
            "total_pages": 5,
            "claims": [{"number": 1, "text": "Aを含む組成物"}],
            "paragraphs": [{"id": "0001", "text": "本文", "section": "実施例"}],
            "tables": [],
        }, f, ensure_ascii=False)
    with open(case_dir / "segments.json", "w", encoding="utf-8") as f:
        json.dump([{"claim_number": 1, "is_independent": True, "dependencies": [],
                    "segments": [{"id": "1A", "text": "成分A"}]}], f, ensure_ascii=False)
    return case_id, case_dir


# ============================================================
# CRUD
# ============================================================

class TestCRUD:
    def test_create_and_load(self, case_with_data):
        case_id, _ = case_with_data
        result, code = cs.create_thread(case_id, topic="hongan", title="テストスレ")
        assert code == 200
        tid = result["thread"]["id"]
        assert tid.startswith("hongan_")

        loaded, code2 = cs.load_thread(case_id, tid)
        assert code2 == 200
        assert loaded["thread"]["title"] == "テストスレ"
        assert loaded["thread"]["topic"] == "hongan"

    def test_list_filters_by_topic(self, case_with_data):
        case_id, _ = case_with_data
        cs.create_thread(case_id, topic="hongan", title="A")
        cs.create_thread(case_id, topic="hongan", title="B")
        cs.create_thread(case_id, topic="search", title="C")

        all_, _ = cs.list_threads(case_id)
        assert len(all_["threads"]) == 3

        only_hongan, _ = cs.list_threads(case_id, topic="hongan")
        assert len(only_hongan["threads"]) == 2
        titles = sorted(t["title"] for t in only_hongan["threads"])
        assert titles == ["A", "B"]

    def test_invalid_topic_rejected(self, case_with_data):
        case_id, _ = case_with_data
        result, code = cs.create_thread(case_id, topic="bogus", title="x")
        assert code == 400

    def test_delete_removes_file(self, case_with_data):
        case_id, case_dir = case_with_data
        result, _ = cs.create_thread(case_id, topic="hongan")
        tid = result["thread"]["id"]
        p = case_dir / "analysis" / "chat" / f"{tid}.json"
        assert p.exists()
        del_result, code = cs.delete_thread(case_id, tid)
        assert code == 200
        assert not p.exists()

    def test_delete_unknown_404(self, case_with_data):
        case_id, _ = case_with_data
        result, code = cs.delete_thread(case_id, "nonexistent_thread")
        assert code == 404

    def test_missing_case(self, tmp_path, monkeypatch):
        monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
        (tmp_path / "cases").mkdir()
        result, code = cs.list_threads("non-existent")
        assert code == 404


# ============================================================
# メッセージ送受信 (LLM はモック)
# ============================================================

class TestMessage:
    def test_append_message_and_reply_basic(self, case_with_data, monkeypatch):
        case_id, _ = case_with_data
        result, _ = cs.create_thread(case_id, topic="hongan", title="x")
        tid = result["thread"]["id"]
        monkeypatch.setattr(
            "modules.claude_client.call_claude",
            lambda prompt, timeout=300, model=None: "回答テキストです。",
        )
        out, code = cs.append_message_and_reply(case_id, tid, "実施例について教えて")
        assert code == 200
        thread = out["thread"]
        assert len(thread["messages"]) == 2
        assert thread["messages"][0]["role"] == "user"
        assert thread["messages"][0]["content"] == "実施例について教えて"
        assert thread["messages"][1]["role"] == "assistant"
        assert thread["messages"][1]["content"] == "回答テキストです。"
        assert thread["messages"][1]["suggestions"] == []

    def test_suggestions_parsed_from_response(self, case_with_data, monkeypatch):
        case_id, _ = case_with_data
        result, _ = cs.create_thread(case_id, topic="hongan", title="x")
        tid = result["thread"]["id"]
        fake = (
            "発明の本質はこうです。\n"
            "[[suggest kind=update_analysis_item target=1.1 value=\"Aを含む新規組成物\"]]\n"
            "また、メモも追記してください:\n"
            "[[suggest kind=append_understanding_note target=実施例 value=\"成分Aは10〜20%が好適\"]]"
        )
        monkeypatch.setattr(
            "modules.claude_client.call_claude",
            lambda prompt, timeout=300, model=None: fake,
        )
        out, _ = cs.append_message_and_reply(case_id, tid, "提案して")
        suggs = out["thread"]["messages"][-1]["suggestions"]
        assert len(suggs) == 2
        kinds = {s["kind"] for s in suggs}
        assert kinds == {"update_analysis_item", "append_understanding_note"}
        # value のクォート除去
        item_sugg = next(s for s in suggs if s["kind"] == "update_analysis_item")
        assert item_sugg["target"] == "1.1"
        assert item_sugg["value"] == "Aを含む新規組成物"
        assert item_sugg["applied"] is False

    def test_unknown_suggest_kind_skipped(self, case_with_data, monkeypatch):
        case_id, _ = case_with_data
        result, _ = cs.create_thread(case_id, topic="hongan", title="x")
        tid = result["thread"]["id"]
        # update_segment は Phase 1 で未対応なので無視されるはず
        fake = '[[suggest kind=update_segment target=1A value="新分節"]]'
        monkeypatch.setattr(
            "modules.claude_client.call_claude",
            lambda prompt, timeout=300, model=None: fake,
        )
        out, _ = cs.append_message_and_reply(case_id, tid, "x")
        assert out["thread"]["messages"][-1]["suggestions"] == []

    def test_llm_failure_records_error_message(self, case_with_data, monkeypatch):
        from modules.claude_client import ClaudeClientError
        case_id, _ = case_with_data
        result, _ = cs.create_thread(case_id, topic="hongan", title="x")
        tid = result["thread"]["id"]

        def boom(prompt, timeout=300, model=None):
            raise ClaudeClientError("CLI not found")
        monkeypatch.setattr("modules.claude_client.call_claude", boom)
        out, code = cs.append_message_and_reply(case_id, tid, "hello")
        assert code == 502
        thread = out["thread"]
        # ユーザーメッセージとエラーアシスタントメッセージが両方入る
        assert len(thread["messages"]) == 2
        assert thread["messages"][1].get("_error") is True

    def test_empty_message_400(self, case_with_data):
        case_id, _ = case_with_data
        result, _ = cs.create_thread(case_id, topic="hongan", title="x")
        tid = result["thread"]["id"]
        out, code = cs.append_message_and_reply(case_id, tid, "  ")
        assert code == 400


# ============================================================
# Suggestion 適用
# ============================================================

class TestApply:
    def _setup_with_suggestion(self, case_id, monkeypatch, kind="update_analysis_item",
                                target="1.1", value="新しい値"):
        result, _ = cs.create_thread(case_id, topic="hongan", title="x")
        tid = result["thread"]["id"]
        fake = f'[[suggest kind={kind} target={target} value="{value}"]]'
        monkeypatch.setattr(
            "modules.claude_client.call_claude",
            lambda prompt, timeout=300, model=None: fake,
        )
        out, _ = cs.append_message_and_reply(case_id, tid, "x")
        sugg_id = out["thread"]["messages"][-1]["suggestions"][0]["id"]
        return tid, sugg_id

    def test_apply_update_analysis_item(self, case_with_data, monkeypatch):
        case_id, case_dir = case_with_data
        # 先に分析結果を作っておく必要がある (update_item_value が読み込む先)
        from services.hongan_analysis_service import run_analysis
        # テンプレ YAML を用意
        tdir = case_dir.parent.parent / "templates"
        tdir.mkdir(exist_ok=True)
        (tdir / "hongan_analysis_v0.1.yaml").write_text(
            "template_id: hongan_v0.1\nversion: '0.1'\nsections:\n  - id: 1\n    title: x\n    items:\n      - id: '1.1'\n        label: 要約\n        type: llm\n",
            encoding="utf-8",
        )
        run_analysis(case_id, skip_llm=True)

        tid, sid = self._setup_with_suggestion(case_id, monkeypatch)
        out, code = cs.apply_suggestion(case_id, tid, sid)
        assert code == 200
        # 永続化された分析 JSON を確認
        with (case_dir / "analysis" / "hongan_analysis.json").open(encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["sections"][0]["items"][0]["value"] == "新しい値"
        # スレッドにも適用済フラグ + イベント
        thread = out["thread"]
        applied_sugg = thread["messages"][-1]["suggestions"][0]
        assert applied_sugg["applied"] is True
        assert len(thread["applied_events"]) == 1

    def test_apply_unknown_suggestion_404(self, case_with_data):
        case_id, _ = case_with_data
        result, _ = cs.create_thread(case_id, topic="hongan", title="x")
        tid = result["thread"]["id"]
        out, code = cs.apply_suggestion(case_id, tid, "nonexistent")
        assert code == 404

    def test_apply_twice_returns_409(self, case_with_data, monkeypatch):
        case_id, case_dir = case_with_data
        # テンプレ + run_analysis 一度
        tdir = case_dir.parent.parent / "templates"
        tdir.mkdir(exist_ok=True)
        (tdir / "hongan_analysis_v0.1.yaml").write_text(
            "template_id: x\nversion: '0.1'\nsections:\n  - id: 1\n    title: x\n    items:\n      - id: '1.1'\n        label: l\n        type: llm\n",
            encoding="utf-8",
        )
        from services.hongan_analysis_service import run_analysis
        run_analysis(case_id, skip_llm=True)

        tid, sid = self._setup_with_suggestion(case_id, monkeypatch)
        cs.apply_suggestion(case_id, tid, sid)
        out, code = cs.apply_suggestion(case_id, tid, sid)
        assert code == 409


# ============================================================
# Suggestion パース単体
# ============================================================

class TestSuggestParse:
    def test_no_suggestions(self):
        assert cs._parse_suggestions("普通のテキストだけ") == []

    def test_value_with_escaped_quote(self):
        text = '[[suggest kind=update_analysis_item target=1.1 value="言葉に\\"引用\\"を含む"]]'
        suggs = cs._parse_suggestions(text)
        assert len(suggs) == 1
        assert suggs[0]["value"] == '言葉に"引用"を含む'

    def test_multiple_suggestions(self):
        text = (
            '[[suggest kind=update_analysis_item target=1.1 value="A"]]\n'
            '[[suggest kind=append_understanding_note target=実施例 value="B"]]\n'
            '[[suggest kind=update_analysis_item target=3.5 value="C"]]'
        )
        suggs = cs._parse_suggestions(text)
        assert len(suggs) == 3
        assert [s["target"] for s in suggs] == ["1.1", "実施例", "3.5"]
