"""予備調査サービス (services/preliminary_research_service.py) の単体テスト。

仕様書 §9 のテスト方針:
  - レシピ読込 (存在する分野 / 未登録分野→generic フォールバック)
  - URL 生成 (単一クエリ / 複数クエリ / 日本語エンコード)
  - メモ保存 (新規作成 / 追記)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from services import case_service, preliminary_research_service as prs


@pytest.fixture
def isolated_root(tmp_path, monkeypatch):
    """PROJECT_ROOT を tmp_path に差し替え、recipe ディレクトリも仮設"""
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    (tmp_path / "cases").mkdir()
    rdir = tmp_path / "templates" / "preliminary_research"
    rdir.mkdir(parents=True)

    cosmetics = {
        "field": "cosmetics",
        "display_name": "化粧品",
        "sources": [
            {
                "id": "cosmetic_info_jp",
                "name": "Cosmetic-Info.jp",
                "search_url_template": "https://cosmetic-info.jp/prod/result.php?keyword={query}&search_type=1",
                "encoding": "utf-8",
                "priority": 1,
            },
            {
                "id": "cosmetic_ingredients_online",
                "name": "化粧品成分オンライン",
                "search_url_template": "https://cosmetic-ingredients.org/?s={query}",
                "encoding": "utf-8",
                "priority": 2,
            },
        ],
        "synonym_expansion": {"enabled": True, "prompt_hint": "化粧品成分の表記揺れ"},
    }
    (rdir / "cosmetics.yaml").write_text(
        yaml.safe_dump(cosmetics, allow_unicode=True), encoding="utf-8"
    )

    generic = {
        "field": "generic",
        "display_name": "汎用",
        "sources": [
            {
                "id": "wikipedia_ja",
                "name": "Wikipedia",
                "search_url_template": "https://ja.wikipedia.org/w/index.php?search={query}",
                "encoding": "utf-8",
                "priority": 1,
            },
        ],
        "synonym_expansion": {"enabled": True, "prompt_hint": "汎用"},
    }
    (rdir / "generic.yaml").write_text(
        yaml.safe_dump(generic, allow_unicode=True), encoding="utf-8"
    )
    return tmp_path


class TestListAvailableFields:
    def test_lists_yaml_stems(self, isolated_root):
        fields = prs.list_available_fields()
        assert set(fields) == {"cosmetics", "generic"}

    def test_empty_when_no_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
        # templates/preliminary_research が存在しない
        assert prs.list_available_fields() == []


class TestLoadRecipe:
    def test_loads_known_field(self, isolated_root):
        r = prs.load_recipe("cosmetics")
        assert r["field"] == "cosmetics"
        assert any(s["id"] == "cosmetic_ingredients_online" for s in r["sources"])

    def test_unknown_field_falls_back_to_generic(self, isolated_root):
        r = prs.load_recipe("layered_materials")
        assert r["field"] == "generic"

    def test_completely_missing_returns_skeleton(self, tmp_path, monkeypatch):
        monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
        # ディレクトリも YAML も無い
        r = prs.load_recipe("anything")
        assert r["field"] == "generic"
        assert r["sources"] == []
        assert r["synonym_expansion"]["enabled"] is False


class TestGenerateSearchUrls:
    def test_single_query(self, isolated_root):
        recipe = prs.load_recipe("cosmetics")
        urls = prs.generate_search_urls(recipe, ["サッカリン"])
        assert len(urls) == 2  # 2 sources × 1 query
        # 並びは priority 順
        assert urls[0]["source_id"] == "cosmetic_info_jp"
        assert urls[1]["source_id"] == "cosmetic_ingredients_online"
        # URL に encoded 日本語が入っている
        assert "%E3%82%B5" in urls[0]["url"]  # サ の UTF-8 先頭
        assert urls[1]["url"].startswith("https://cosmetic-ingredients.org/?s=")

    def test_multiple_queries_keeps_order(self, isolated_root):
        recipe = prs.load_recipe("cosmetics")
        urls = prs.generate_search_urls(recipe, ["A", "B"])
        assert len(urls) == 4
        # priority 1 (cosmetic_info_jp) → A, B、その後 priority 2 → A, B
        assert urls[0]["query"] == "A" and urls[0]["source_id"] == "cosmetic_info_jp"
        assert urls[1]["query"] == "B" and urls[1]["source_id"] == "cosmetic_info_jp"
        assert urls[2]["query"] == "A" and urls[2]["source_id"] == "cosmetic_ingredients_online"
        assert urls[3]["query"] == "B" and urls[3]["source_id"] == "cosmetic_ingredients_online"

    def test_japanese_url_encoding(self, isolated_root):
        recipe = prs.load_recipe("generic")
        urls = prs.generate_search_urls(recipe, ["カプリル酸"])
        assert len(urls) == 1
        # スペースや日本語が % エンコードされている (URL safe)
        assert " " not in urls[0]["url"]
        assert "カプリル酸" not in urls[0]["url"]

    def test_empty_queries(self, isolated_root):
        recipe = prs.load_recipe("cosmetics")
        assert prs.generate_search_urls(recipe, []) == []
        assert prs.generate_search_urls(recipe, ["", "  ", None]) == []

    def test_skips_source_without_template(self, isolated_root):
        recipe = {
            "sources": [
                {"id": "broken", "name": "x", "priority": 1},  # no search_url_template
                {"id": "ok", "name": "y", "priority": 2,
                 "search_url_template": "https://example.com/?q={query}"},
            ]
        }
        urls = prs.generate_search_urls(recipe, ["test"])
        assert len(urls) == 1
        assert urls[0]["source_id"] == "ok"


class TestSaveNote:
    def _setup_case(self, isolated_root, case_id="2030-prelim"):
        case_service.create_minimal_case(case_id, title="x", field="cosmetics")
        return case_id

    def test_creates_new_md_file(self, isolated_root):
        cid = self._setup_case(isolated_root)
        result = prs.save_note(
            cid, component="サッカリン", note="甘味料用途",
            urls_opened=["https://example.com/a"],
            queries=["サッカリン", "saccharin"],
            field="cosmetics",
        )
        assert result.get("success") is True
        md = isolated_root / "cases" / cid / "analysis" / "hongan_understanding.md"
        assert md.exists()
        body = md.read_text(encoding="utf-8")
        assert "## 予備調査: サッカリン" in body
        assert "甘味料用途" in body
        assert "https://example.com/a" in body
        assert "saccharin" in body

    def test_appends_without_overwriting(self, isolated_root):
        cid = self._setup_case(isolated_root)
        md = isolated_root / "cases" / cid / "analysis" / "hongan_understanding.md"
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text("# 既存メモ\n\n冒頭の内容\n", encoding="utf-8")

        prs.save_note(cid, component="新規", note="追記される内容")
        body = md.read_text(encoding="utf-8")
        # 既存内容は維持
        assert body.startswith("# 既存メモ")
        assert "冒頭の内容" in body
        # 追記もされる
        assert "## 予備調査: 新規" in body
        assert "追記される内容" in body

    def test_returns_404_for_missing_case(self, isolated_root):
        result = prs.save_note("non-existent", component="x", note="y")
        assert result.get("_status") == 404

    def test_strips_newlines_from_component(self, isolated_root):
        cid = self._setup_case(isolated_root)
        prs.save_note(cid, component="サ\nッ\rカリン", note="テスト")
        body = (isolated_root / "cases" / cid / "analysis" / "hongan_understanding.md").read_text(encoding="utf-8")
        assert "## 予備調査: サ ッ カリン" in body


class TestAnalysisDirCreated:
    """create_minimal_case が analysis/ も作るようになったことの回帰防止"""

    def test_analysis_subdir_created(self, isolated_root):
        case_service.create_minimal_case("2030-newcase", title="x", field="cosmetics")
        adir = isolated_root / "cases" / "2030-newcase" / "analysis"
        assert adir.is_dir()
