"""patent_downloader.build_google_patents_url_candidates の単体テスト。

日本語表記 (特開/特表/特許/再表/実登/特願) を ASCII Kind-code 付き形式に変換できる
ことを検証する。download_patent_pdf の実 HTTP は呼ばない。
"""
from __future__ import annotations

import pytest

from modules.patent_downloader import (
    build_google_patents_url,
    build_google_patents_url_candidates,
)


GP = "https://patents.google.com/patent"


class TestKokai:
    """特開/特表 (公開公報・公表公報)"""

    def test_kokai_seireki(self):
        cands = build_google_patents_url_candidates("特開2020-169128")
        assert cands[0] == f"{GP}/JP2020169128A/ja"

    def test_kokai_seireki_zero_pad_fallback(self):
        # 短い番号は 6 桁 zfill 版もフォールバックに含む
        cands = build_google_patents_url_candidates("特開2020-12345")
        assert cands[0] == f"{GP}/JP202012345A/ja"
        assert f"{GP}/JP2020012345A/ja" in cands

    def test_kokai_heisei(self):
        cands = build_google_patents_url_candidates("特開平5-12345")
        assert cands[0] == f"{GP}/JPH0512345A/ja"

    def test_kokai_showa(self):
        cands = build_google_patents_url_candidates("特開昭60-12345")
        assert cands[0] == f"{GP}/JPS6012345A/ja"

    def test_kokai_reiwa(self):
        cands = build_google_patents_url_candidates("特開令3-1234")
        assert cands[0] == f"{GP}/JPR031234A/ja"

    def test_kohyo(self):
        cands = build_google_patents_url_candidates("特表2020-500001")
        assert cands[0] == f"{GP}/JP2020500001A/ja"


class TestTokkyo:
    """特許 (登録番号)"""

    def test_tokkyo_with_dai_go(self):
        cands = build_google_patents_url_candidates("特許第6789012号")
        # B2 を最優先、B1 もフォールバックで含む
        assert cands[0] == f"{GP}/JP6789012B2/ja"
        assert f"{GP}/JP6789012B1/ja" in cands

    def test_tokkyo_bare(self):
        cands = build_google_patents_url_candidates("特許6789012")
        assert cands[0] == f"{GP}/JP6789012B2/ja"


class TestSaihyo:
    """再表 / 再公表"""

    def test_saihyo(self):
        cands = build_google_patents_url_candidates("再表2012-029514")
        # WO 形式が最優先
        assert cands[0] == f"{GP}/WO2012029514A1/en"
        # JPWO もフォールバック (J-PlatPat 経由の和訳が欲しいケース)
        assert f"{GP}/JPWO2012029514A1/ja" in cands

    def test_saikohyo(self):
        cands = build_google_patents_url_candidates("再公表2012-029514")
        assert cands[0] == f"{GP}/WO2012029514A1/en"


class TestUtility:
    """実用新案登録"""

    def test_jitsuto(self):
        cands = build_google_patents_url_candidates("実登第3123456号")
        assert any("3123456" in u for u in cands)


class TestAsciiCodes:
    """ASCII 国コード付き表記"""

    def test_jp_passthrough(self):
        cands = build_google_patents_url_candidates("JP2020169128A")
        assert cands[0] == f"{GP}/JP2020169128A/ja"

    def test_us_passthrough(self):
        cands = build_google_patents_url_candidates("US20130040869A1")
        assert cands[0] == f"{GP}/US20130040869A1/en"

    def test_wo_with_slash(self):
        cands = build_google_patents_url_candidates("WO2022/030405")
        assert cands[0] == f"{GP}/WO2022030405A1/en"

    def test_ep(self):
        cands = build_google_patents_url_candidates("EP3719056A1")
        assert cands[0] == f"{GP}/EP3719056A1/en"


class TestEdgeCases:
    def test_empty_string(self):
        assert build_google_patents_url_candidates("") == []

    def test_none(self):
        assert build_google_patents_url_candidates(None) == []

    def test_whitespace_handled(self):
        cands = build_google_patents_url_candidates("  特開2020-169128  ")
        assert cands[0] == f"{GP}/JP2020169128A/ja"


class TestBackwardCompat:
    """build_google_patents_url が最優先候補を返す (後方互換)"""

    def test_returns_first_candidate(self):
        url = build_google_patents_url("特開2020-169128")
        assert url == f"{GP}/JP2020169128A/ja"

    def test_unknown_returns_en_fallback(self):
        # 完全に未識別な文字列は en にフォールバック
        url = build_google_patents_url("XYZ-9999")
        assert url.endswith("/en")
