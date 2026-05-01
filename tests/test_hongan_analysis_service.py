"""hongan_analysis_service の単体テスト。

- load_template: YAML 読込
- auto 項目解決: meta / classification / claim_segmenter / paragraph_matcher
- LLM 項目: call_claude を monkeypatch して JSON 応答 → result 反映を検証
- skip_llm=True で LLM 呼び出しをスキップ
- 出力ファイル (cases/<id>/analysis/hongan_analysis.json) への保存
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from services import case_service, hongan_analysis_service as has


@pytest.fixture
def case_with_data(tmp_path, monkeypatch):
    """templates/hongan_analysis_v0.1.yaml と最小限の hongan/segments を持つ案件を作る。"""
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    (tmp_path / "cases").mkdir()
    case_id = "2030-han"
    case_service.create_minimal_case(case_id, title="x", field="cosmetics")
    case_dir = tmp_path / "cases" / case_id

    # テンプレート YAML をコピー (実テンプレートを最小化したもの)
    tdir = tmp_path / "templates"
    tdir.mkdir()
    template = {
        "template_id": "hongan_v0.1",
        "version": "0.1",
        "sections": [
            {
                "id": 1, "title": "発明の本質",
                "items": [
                    {"id": "1.1", "label": "発明の一言要約", "type": "llm"},
                    {"id": "1.3", "label": "技術分野", "type": "auto",
                     "source": "jplatpat_classification"},
                ],
            },
            {
                "id": 2, "title": "書誌情報",
                "items": [
                    {"id": "2.1", "label": "出願番号等", "type": "auto", "source": "meta"},
                    {"id": "2.4", "label": "優先権", "type": "manual"},
                ],
            },
            {
                "id": 4, "title": "請求項",
                "items": [
                    {"id": "4.1", "label": "独立請求項", "type": "auto",
                     "source": "claim_segmenter"},
                    {"id": "4.2", "label": "従属関係", "type": "auto",
                     "source": "claim_segmenter"},
                    {"id": "4.3", "label": "構成要素", "type": "auto",
                     "source": "claim_segmenter"},
                ],
            },
        ],
    }
    (tdir / "hongan_analysis_v0.1.yaml").write_text(
        yaml.safe_dump(template, allow_unicode=True), encoding="utf-8"
    )

    # hongan.json (最小)
    hongan = {
        "patent_number": "JP2024-001",
        "patent_title": "テスト発明",
        "format": "JP",
        "total_pages": 5,
        "claims": [{"number": 1, "text": "Aを含む組成物"}],
        "paragraphs": [
            {"id": "0001", "text": "背景の説明", "section": "背景技術"},
            {"id": "0010", "text": "課題の説明", "section": "発明が解決しようとする課題"},
        ],
        "tables": [],
    }
    with open(case_dir / "hongan.json", "w", encoding="utf-8") as f:
        json.dump(hongan, f, ensure_ascii=False)

    # segments.json
    segments = [
        {"claim_number": 1, "is_independent": True, "dependencies": [],
         "category": "物",
         "segments": [
             {"id": "1A", "text": "成分A"},
             {"id": "1B", "text": "成分B"},
         ]},
        {"claim_number": 2, "is_independent": False, "dependencies": [1],
         "segments": [{"id": "2A", "text": "請求項1において..."}]},
    ]
    with open(case_dir / "segments.json", "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False)

    # search/classification.json
    sdir = case_dir / "search"
    sdir.mkdir(parents=True, exist_ok=True)
    with open(sdir / "classification.json", "w", encoding="utf-8") as f:
        json.dump({
            "ipc": ["A61K 8/00"],
            "fi": ["A61K 8/22"],
            "fterm": ["4C083AA12"],
            "theme_codes": ["4C083"],
        }, f, ensure_ascii=False)

    return case_id, case_dir


class TestLoadTemplate:
    def test_loads_v0_1(self, case_with_data):
        t = has.load_template("v0.1")
        assert t["template_id"] == "hongan_v0.1"
        assert any(s["id"] == 1 for s in t["sections"])

    def test_missing_version_raises(self, case_with_data):
        with pytest.raises(FileNotFoundError):
            has.load_template("v9.9")


class TestAutoResolution:
    def test_skip_llm_fills_only_auto(self, case_with_data, monkeypatch):
        case_id, case_dir = case_with_data
        # call_claude が呼ばれないことを確認
        monkeypatch.setattr(
            "modules.claude_client.call_claude",
            lambda *a, **kw: pytest.fail("call_claude should not be invoked when skip_llm=True"),
        )
        result, code = has.run_analysis(case_id, skip_llm=True)
        assert code == 200
        assert result["success"] is True
        sections = result["data"]["sections"]

        # 1.3 (jplatpat_classification) — enrich された構造
        item_1_3 = sections[0]["items"][1]
        assert item_1_3["id"] == "1.3"
        v = item_1_3["value"]
        ipc_codes = [x["code"] for x in v["IPC"]]
        assert "A61K 8/00" in ipc_codes
        fi_codes = [x["code"] for x in v["FI"]]
        assert "A61K 8/22" in fi_codes
        # Fターム は theme でグルーピング
        grouped = v["Fターム_grouped"]
        assert "4C083" in grouped
        suffixes = [x["code"] for x in grouped["4C083"]["items"]]
        assert "AA12" in suffixes
        assert "4C083" in v["テーマコード"]

        # 2.1 (meta)
        item_2_1 = sections[1]["items"][0]
        assert item_2_1["id"] == "2.1"
        assert item_2_1["value"]["公開番号"] == "JP2024-001"

        # 4.1 (independent claims)
        item_4_1 = sections[2]["items"][0]
        assert item_4_1["id"] == "4.1"
        assert len(item_4_1["value"]) == 1
        assert item_4_1["value"][0]["claim_number"] == 1
        assert item_4_1["value"][0]["category"] == "物"

        # 4.2 (tree)
        item_4_2 = sections[2]["items"][1]
        assert item_4_2["value"] == {"1": [2]}

        # 4.3 (segments)
        item_4_3 = sections[2]["items"][2]
        assert len(item_4_3["value"]) == 2
        assert item_4_3["value"][0]["claim_number"] == 1
        assert len(item_4_3["value"][0]["segments"]) == 2

        # manual はそのまま None
        item_2_4 = sections[1]["items"][1]
        assert item_2_4["type"] == "manual"
        assert item_2_4["value"] is None

        # 1.1 (LLM) は skip_llm なので None
        item_1_1 = sections[0]["items"][0]
        assert item_1_1["type"] == "llm"
        assert item_1_1["value"] is None

    def test_output_persisted(self, case_with_data):
        case_id, case_dir = case_with_data
        has.run_analysis(case_id, skip_llm=True)
        out = case_dir / "analysis" / "hongan_analysis.json"
        assert out.exists()
        with out.open(encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["template_id"] == "hongan_v0.1"


class TestLlmResolution:
    def test_llm_results_merged(self, case_with_data, monkeypatch):
        case_id, _ = case_with_data
        # Claude 応答をモック (有効な JSON)
        fake_response = '```json\n{"1.1": "Aを含む組成物による特性向上"}\n```'
        monkeypatch.setattr(
            "modules.claude_client.call_claude",
            lambda *a, **kw: fake_response,
        )
        result, code = has.run_analysis(case_id, skip_llm=False)
        assert code == 200
        assert result["llm_item_count"] == 1
        assert result["llm_filled_count"] == 1
        sections = result["data"]["sections"]
        item_1_1 = sections[0]["items"][0]
        assert item_1_1["value"] == "Aを含む組成物による特性向上"

    def test_llm_failure_recorded_but_auto_still_filled(self, case_with_data, monkeypatch):
        case_id, _ = case_with_data
        from modules.claude_client import ClaudeClientError
        def boom(*a, **kw):
            raise ClaudeClientError("CLI not found")
        monkeypatch.setattr("modules.claude_client.call_claude", boom)
        result, code = has.run_analysis(case_id, skip_llm=False)
        assert code == 200
        assert "llm_error" in result
        # auto 項目は埋まる (LLM 失敗でも skeleton は返す)
        sections = result["data"]["sections"]
        item_1_3 = sections[0]["items"][1]
        ipc_codes = [x["code"] for x in item_1_3["value"]["IPC"]]
        assert "A61K 8/00" in ipc_codes
        # LLM 項目は None のまま
        item_1_1 = sections[0]["items"][0]
        assert item_1_1["value"] is None

    def test_llm_invalid_json_response(self, case_with_data, monkeypatch):
        case_id, _ = case_with_data
        monkeypatch.setattr(
            "modules.claude_client.call_claude",
            lambda *a, **kw: "申し訳ありませんが回答できません",
        )
        result, code = has.run_analysis(case_id, skip_llm=False)
        assert code == 200
        assert "llm_error" in result
        assert "JSON" in result["llm_error"]


class TestErrors:
    def test_missing_case(self, tmp_path, monkeypatch):
        monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
        (tmp_path / "cases").mkdir()
        result, code = has.run_analysis("non-existent")
        assert code == 404

    def test_missing_hongan(self, tmp_path, monkeypatch):
        monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
        (tmp_path / "cases").mkdir()
        case_service.create_minimal_case("2030-no-hongan", title="x", field="cosmetics")
        # template だけ用意
        tdir = tmp_path / "templates"
        tdir.mkdir()
        (tdir / "hongan_analysis_v0.1.yaml").write_text(
            "template_id: x\nversion: '0.1'\nsections: []\n", encoding="utf-8"
        )
        result, code = has.run_analysis("2030-no-hongan")
        assert code == 400
        assert "hongan" in result["error"]


class TestLoadExisting:
    def test_returns_exists_false_when_no_file(self, case_with_data):
        case_id, _ = case_with_data
        result, code = has.load_existing_analysis(case_id)
        assert code == 200
        assert result["exists"] is False

    def test_returns_data_after_run(self, case_with_data):
        case_id, _ = case_with_data
        has.run_analysis(case_id, skip_llm=True)
        result, code = has.load_existing_analysis(case_id)
        assert code == 200
        assert result["exists"] is True
        assert result["data"]["template_id"] == "hongan_v0.1"


class TestExtractJson:
    def test_plain_json(self):
        assert has._extract_json_from_response('{"a": 1}') == {"a": 1}

    def test_codefenced_json(self):
        assert has._extract_json_from_response('```json\n{"a": 1}\n```') == {"a": 1}

    def test_with_preamble(self):
        text = "以下が結果です:\n```\n{\"a\": 2}\n```\n以上"
        assert has._extract_json_from_response(text) == {"a": 2}

    def test_invalid_returns_empty(self):
        assert has._extract_json_from_response("not json") == {}
        assert has._extract_json_from_response("") == {}
