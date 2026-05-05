"""J-PlatPat PDF ダウンローダの pure-function 部分の単体テスト。

実 J-PlatPat 通信を伴うテストは含まない (ネットワーク + Playwright + Chromium が必要)。
正規化ロジック (normalize_jp_patent_number) と playwright 未インストール時のエラー応答だけ
ネット非依存で検証する。実環境動作は scripts/jplatpat_pdf_smoke.py で確認。
"""
from __future__ import annotations

import sys

import pytest

from modules.jplatpat_pdf_downloader import (
    download_jplatpat_pdf,
    normalize_jp_patent_number,
    normalize_jp_registration_number,
)


class TestNormalizePublicationNumber:
    """特開 / JPyyyy-nnnnnnA — 公開番号"""

    @pytest.mark.parametrize("raw,exp_number,exp_doc_id", [
        ("特開2024-123456", "2024-123456", "JP2024123456A"),
        ("特開 2024-123456", "2024-123456", "JP2024123456A"),
        ("特開2024-12345", "2024-012345", "JP2024012345A"),  # zfill 6 桁
        ("JP2024-123456A", "2024-123456", "JP2024123456A"),
        ("jp2024-123456a", "2024-123456", "JP2024123456A"),  # case insensitive
        ("JP2024-123456A1", "2024-123456", "JP2024123456A"),  # kind suffix 許容
        ("特開2024-123456号公報", "2024-123456", "JP2024123456A"),
        ("特開　2024-123456", "2024-123456", "JP2024123456A"),  # 全角スペース
        ("特開2024－123456", "2024-123456", "JP2024123456A"),  # 全角ハイフン
        ("特開２０２４-１２３４５６", "2024-123456", "JP2024123456A"),  # 全角数字
    ])
    def test_publication_variants(self, raw, exp_number, exp_doc_id):
        target = normalize_jp_patent_number(raw)
        assert target.number == exp_number
        assert target.doc_id == exp_doc_id
        assert target.kind == "publication"
        assert target.fixed_url.endswith(f"/PU/JP-{exp_number}/11/ja")
        assert target.display_number == f"特開{exp_number}"
        assert target.filename_stem == exp_doc_id


class TestNormalizeRegistrationNumber:
    """特許 / JPnnnnnnnB — 登録番号"""

    @pytest.mark.parametrize("raw,exp_number,exp_doc_id", [
        ("特許7250676", "7250676", "JP7250676B"),
        ("特許第7250676号", "7250676", "JP7250676B"),
        ("JP7250676B", "7250676", "JP7250676B"),
        ("JP7250676B2", "7250676", "JP7250676B"),
        ("7250676", "7250676", "JP7250676B"),
        ("7250676B2", "7250676", "JP7250676B"),
        ("７２５０６７６", "7250676", "JP7250676B"),  # 全角数字
        ("特許　7250676", "7250676", "JP7250676B"),  # 全角スペース混入
    ])
    def test_registration_variants(self, raw, exp_number, exp_doc_id):
        target = normalize_jp_patent_number(raw)
        assert target.number == exp_number
        assert target.doc_id == exp_doc_id
        assert target.kind == "registration"
        assert target.fixed_url.endswith(f"/PU/JP-{exp_number}/15/ja")
        assert target.display_number == f"特許{exp_number}"

    def test_alias_function_kept_for_back_compat(self):
        """normalize_jp_registration_number は normalize_jp_patent_number への薄いラッパ"""
        a = normalize_jp_registration_number("特許7250676")
        b = normalize_jp_patent_number("特許7250676")
        assert a == b


class TestNormalizeRejects:
    @pytest.mark.parametrize("raw", [
        "",
        "   ",
        None,  # None は ValueError(空) で弾かれる
    ])
    def test_empty_inputs(self, raw):
        with pytest.raises(ValueError):
            normalize_jp_patent_number(raw)

    @pytest.mark.parametrize("raw", [
        "abc",
        "WO2024/123456",  # WO/再表は今回未対応
        "再表2012-029514",
        "US7250676",      # US は対象外
    ])
    def test_unknown_format_raises(self, raw):
        with pytest.raises(ValueError):
            normalize_jp_patent_number(raw)


class TestSelectorOrder:
    """番号照会画面では公開番号と登録番号で input 欄が違うため、selector の優先順が逆になる"""

    def test_publication_uses_no2_first(self):
        target = normalize_jp_patent_number("特開2024-123456")
        # No2 (公開番号) が先頭、No3 (登録番号) はもっと後ろ
        assert target.inquiry_selectors[0].endswith("InputNo2")

    def test_registration_uses_no3_first(self):
        target = normalize_jp_patent_number("特許7250676")
        assert target.inquiry_selectors[0].endswith("InputNo3")


class TestDownloadGracefulWhenPlaywrightMissing:
    """playwright 未インストール環境で download_jplatpat_pdf が success=False を返すことを検証。

    sys.modules を差し替えて import 失敗を再現する。実環境を壊さないよう finally で復元。
    """

    def test_returns_install_hint_error(self, tmp_path, monkeypatch):
        # 既に import 済みの可能性があるので強制的に未インストール状態を再現
        for mod_name in [
            "playwright.sync_api",
            "playwright",
        ]:
            monkeypatch.setitem(sys.modules, mod_name, None)

        result = download_jplatpat_pdf("特開2024-123456", tmp_path)
        assert result["success"] is False
        # メッセージに pip install hint が含まれる
        assert "playwright" in result["error"].lower()
