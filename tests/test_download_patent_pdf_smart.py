"""patent_downloader.download_patent_pdf_smart の単体テスト。

実 J-PlatPat / Google Patents には接続せず、両ダウンローダ関数を monkeypatch で
入れ替えて分岐ロジックだけ検証する。
"""
from __future__ import annotations

import pytest

from modules import patent_downloader


# ---- is_jp_patent_id -------------------------------------------------------

class TestIsJpPatentId:
    @pytest.mark.parametrize("pid", [
        "特開2024-123456",
        "特開2024-12345",  # zfill
        "JP2024-123456A",
        "JP2024123456A",
        "特許7250676",
        "特許第7250676号",
        "JP7250676B2",
        "7250676B2",
    ])
    def test_jp_numbers_recognized(self, pid):
        assert patent_downloader.is_jp_patent_id(pid) is True

    @pytest.mark.parametrize("pid", [
        "WO2022/044362",
        "WO2024123456A1",
        "US20130040869A1",
        "US7250676",
        "EP3719056A1",
        "再表2012-029514",
        "",
        "   ",
        None,
        "abc",
    ])
    def test_non_jp_returns_false(self, pid):
        assert patent_downloader.is_jp_patent_id(pid) is False


# ---- download_patent_pdf_smart routing ------------------------------------

class TestRouting:
    def _stub_jplatpat(self, monkeypatch, *, success, **extra):
        """download_jplatpat_pdf を fake で差し替え。呼ばれた回数を記録。"""
        calls = []
        def fake(patent_id, save_dir, *, headless=True, on_progress=None, timeout_ms=60000):
            calls.append({"patent_id": patent_id, "save_dir": str(save_dir),
                          "headless": headless})
            if success:
                return {"success": True, "path": f"{save_dir}/dummy.pdf",
                        "num_pages": 5, "title": "test", **extra}
            return {"success": False, "error": "JP fail", **extra}
        monkeypatch.setattr(
            "modules.jplatpat_pdf_downloader.download_jplatpat_pdf", fake
        )
        return calls

    def _stub_gp(self, monkeypatch, *, success):
        """download_patent_pdf を fake で差し替え。"""
        calls = []
        def fake(patent_id, save_dir, timeout=30):
            calls.append({"patent_id": patent_id, "save_dir": str(save_dir)})
            if success:
                return {"success": True, "path": f"{save_dir}/gp.pdf",
                        "google_patents_url": f"https://patents.google.com/patent/{patent_id}/ja"}
            return {"success": False, "error": "GP fail",
                    "google_patents_url": f"https://patents.google.com/patent/{patent_id}/ja"}
        monkeypatch.setattr(
            "modules.patent_downloader.download_patent_pdf", fake
        )
        return calls

    def test_jp_uses_jplatpat_first(self, tmp_path, monkeypatch):
        jp_calls = self._stub_jplatpat(monkeypatch, success=True)
        gp_calls = self._stub_gp(monkeypatch, success=True)
        result = patent_downloader.download_patent_pdf_smart(
            "特開2024-123456", tmp_path
        )
        assert result["success"] is True
        assert result["source"] == "jplatpat"
        assert result["num_pages"] == 5
        assert result["title"] == "test"
        # JP 経路が呼ばれ、GP は呼ばれない
        assert len(jp_calls) == 1
        assert len(gp_calls) == 0

    def test_jp_falls_back_to_gp_on_failure(self, tmp_path, monkeypatch):
        jp_calls = self._stub_jplatpat(monkeypatch, success=False)
        gp_calls = self._stub_gp(monkeypatch, success=True)
        result = patent_downloader.download_patent_pdf_smart(
            "特開2024-123456", tmp_path
        )
        assert result["success"] is True
        assert result["source"] == "google_patents"
        assert result["fallback"] is True
        assert result["jplatpat_error"] == "JP fail"
        assert len(jp_calls) == 1
        assert len(gp_calls) == 1

    def test_both_fail_returns_gp_error_with_jp_attached(self, tmp_path, monkeypatch):
        self._stub_jplatpat(monkeypatch, success=False)
        gp_calls = self._stub_gp(monkeypatch, success=False)
        result = patent_downloader.download_patent_pdf_smart(
            "特開2024-123456", tmp_path
        )
        assert result["success"] is False
        assert result["error"] == "GP fail"
        assert result["jplatpat_error"] == "JP fail"
        assert len(gp_calls) == 1

    def test_non_jp_skips_jplatpat(self, tmp_path, monkeypatch):
        jp_calls = self._stub_jplatpat(monkeypatch, success=True)
        gp_calls = self._stub_gp(monkeypatch, success=True)
        result = patent_downloader.download_patent_pdf_smart(
            "WO2022/044362", tmp_path
        )
        assert result["success"] is True
        assert result["source"] == "google_patents"
        # JP 経路は呼ばれない
        assert len(jp_calls) == 0
        assert len(gp_calls) == 1

    def test_prefer_jplatpat_false_skips_jp(self, tmp_path, monkeypatch):
        """既存挙動 (Google Patents 直行) に明示的にフォールバックする経路"""
        jp_calls = self._stub_jplatpat(monkeypatch, success=True)
        gp_calls = self._stub_gp(monkeypatch, success=True)
        result = patent_downloader.download_patent_pdf_smart(
            "特開2024-123456", tmp_path, prefer_jplatpat=False
        )
        assert result["success"] is True
        assert result["source"] == "google_patents"
        assert len(jp_calls) == 0
        assert len(gp_calls) == 1

    def test_jp_exception_falls_back_gracefully(self, tmp_path, monkeypatch):
        """J-PlatPat 経路で例外が飛んでも Google Patents で復帰できる"""
        def raising(patent_id, save_dir, *, headless=True, on_progress=None, timeout_ms=60000):
            raise RuntimeError("playwright crashed")
        monkeypatch.setattr(
            "modules.jplatpat_pdf_downloader.download_jplatpat_pdf", raising
        )
        gp_calls = self._stub_gp(monkeypatch, success=True)
        result = patent_downloader.download_patent_pdf_smart(
            "特開2024-123456", tmp_path
        )
        assert result["success"] is True
        assert result["source"] == "google_patents"
        assert "playwright crashed" in result.get("jplatpat_error", "")
        assert len(gp_calls) == 1

    def test_empty_input_returns_error(self, tmp_path):
        result = patent_downloader.download_patent_pdf_smart("", tmp_path)
        assert result["success"] is False
        assert "空" in result["error"]

    def test_passes_on_progress_to_jplatpat(self, tmp_path, monkeypatch):
        captured = {}
        def fake(patent_id, save_dir, *, headless=True, on_progress=None, timeout_ms=60000):
            captured["headless"] = headless
            captured["on_progress"] = on_progress
            return {"success": True, "path": f"{save_dir}/dummy.pdf"}
        monkeypatch.setattr(
            "modules.jplatpat_pdf_downloader.download_jplatpat_pdf", fake
        )
        cb = lambda msg: None
        patent_downloader.download_patent_pdf_smart(
            "特開2024-123456", tmp_path, headless=False, on_progress=cb,
        )
        assert captured["headless"] is False
        assert captured["on_progress"] is cb
