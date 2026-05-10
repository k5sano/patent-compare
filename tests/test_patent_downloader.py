"""patent_downloader の純粋分岐テスト。"""
from __future__ import annotations

import builtins

import pytest

from modules import patent_downloader


class TestIsJpPatentIdBranches:
    @pytest.mark.parametrize("pid", [
        "特開2024-123456",
        "特開２０２４－１２３４５６",
        "JP2024123456A",
        "JP2024-123456A",
        "特許第7250676号",
        "JP7250676B2",
        "7250676B2",
    ])
    def test_accepts_jplatpat_publication_and_grant_forms(self, pid):
        assert patent_downloader.is_jp_patent_id(pid) is True

    @pytest.mark.parametrize("pid", [
        "WO2020/112595A1",
        "US2016175445A1",
        "EP3719056A1",
        "再表2012-029514",
        "再公表WO2019/180364",
        "JP2020",
        "not a patent",
        "",
        "   ",
        None,
    ])
    def test_rejects_non_jplatpat_or_invalid_forms(self, pid):
        assert patent_downloader.is_jp_patent_id(pid) is False

    def test_returns_false_when_jplatpat_normalizer_unavailable(self, monkeypatch):
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "modules.jplatpat_pdf_downloader":
                raise ImportError("missing optional dependency")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        assert patent_downloader.is_jp_patent_id("特開2024-123456") is False

