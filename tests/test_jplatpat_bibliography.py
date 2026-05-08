from __future__ import annotations

import json

from modules import jplatpat_bibliography as jb
from modules.jplatpat_client import parse_classifications_from_raw


SAMPLE_TEXT_DATA = """<SDO BIJ><DP><RTI>
(11)【公開番号】特開2024-108988(P2024-108988A)<br>
(43)【公開日】令和6年8月13日(2024.8.13)<br>
(54)【発明の名称】毛髪変形化粧料<br>
(51)【国際特許分類】<br>
   Ａ６１Ｋ   8/898    (2006.01)<br>
   Ａ６１Ｑ   5/04     (2006.01)<br>
【ＦＩ】<br>
   Ａ６１Ｋ   8/898<br>
   Ａ６１Ｑ   5/04<br>
(21)【出願番号】特願2023-22940(P2023-22940)<br>
(22)【出願日】令和5年1月31日(2023.1.31)<br>
(71)【出願人】<br>
【識別番号】518035145<br>
【氏名又は名称】株式会社イングラボ<br>
(71)【出願人】<br>
【識別番号】518035156<br>
【氏名又は名称】株式会社ＣＵＴＩＣＵＬＡ<br>
(72)【発明者】<br>
【氏名】中谷  靖章<br>
(72)【発明者】<br>
【氏名】一木  登紀男<br>
【テーマコード（参考）】<br>
４Ｃ０８３<br>
【Ｆターム（参考）】<br>
4C083AB082<br>
4C083AC302<br>
</RTI></SDO>"""


class _Resp:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload or {}
        self.status_code = status_code
        self.text = text or json.dumps(self._payload, ensure_ascii=False)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self._payload


class _Session:
    def __init__(self):
        self.posts = []

    def get(self, *args, **kwargs):
        return _Resp()

    def post(self, url, headers=None, json=None, timeout=None):
        self.posts.append((url, headers, json, timeout))
        if url.endswith("/web/patnumber/wsp0102"):
            return _Resp({
                "SEARCH_RSLT_LIST": [{
                    "ISN": "22850252",
                    "PUBLI_NUM_DISP": "特開2024-108988",
                    "APP_NUM_DISP": "特願2023-022940",
                    "APP_DATE": "2023/01/31",
                    "KNOWN_DATE": "2024/08/13",
                    "INVEN_NAME": "毛髪変形化粧料",
                    "APPN_RIGHT_HOLDER": ["株式会社ＣＵＴＩＣＵＬＡ"],
                    "FI": ["A61K8/898"],
                }]
            })
        if url.endswith("/app/comdocu/wsp1101"):
            return _Resp({"RSLT_INFO": {"STATUS_CD_A": 0}})
        if url.endswith("/app/comdocu/wsp1201"):
            return _Resp({"DOCU_DATA": {"TEXT_DATA": SAMPLE_TEXT_DATA}})
        return _Resp({"RESULT_CD": 0})


def test_parse_bibliography_text_extracts_names_dates_and_classifications():
    parsed = jb.parse_bibliography_text(SAMPLE_TEXT_DATA)
    assert parsed["patent_number"] == "特開2024-108988"
    assert parsed["application_number"] == "特願2023-022940"
    assert parsed["application_date"] == "2023-01-31"
    assert parsed["publication_date"] == "2024-08-13"
    assert parsed["applicants"] == ["株式会社イングラボ", "株式会社ＣＵＴＩＣＵＬＡ"]
    assert parsed["inventors"] == ["中谷 靖章", "一木 登紀男"]


def test_fetch_jplatpat_bibliography_uses_internal_api_and_returns_dict():
    sess = _Session()
    out = jb.fetch_jplatpat_bibliography("特開2024-108988", session=sess, timeout=7)

    assert out["patent_number"] == "特開2024-108988"
    assert out["application_date"] == "2023-01-31"
    assert out["applicants"] == ["株式会社イングラボ", "株式会社ＣＵＴＩＣＵＬＡ"]
    assert out["inventors"] == ["中谷 靖章", "一木 登紀男"]
    assert out["ipc"] == ["A61K 8/898", "A61Q 5/04"]
    assert out["fi"] == ["A61K 8/898", "A61Q 5/04"]
    assert out["theme_code"] == ["4C083"]
    assert out["fterm"] == ["4C083AB082", "4C083AC302"]

    urls = [u for u, _h, _j, _t in sess.posts]
    assert jb.WSP0102_URL in urls
    assert jb.WSP1101_URL in urls
    assert jb.WSP1201_URL in urls
    wsp0102_body = next(body for url, _h, body, _t in sess.posts if url == jb.WSP0102_URL)
    assert wsp0102_body["NUM_INQRY_DISP"]["NUM_INFO"][0]["NUM_TYPE"] == "PUBLI_NUM_PUB_NUM_A"


def test_parse_classifications_preserves_laminate_fterm_layer_suffix():
    raw = """【テーマコード（参考）】
4F100
【Ｆターム（参考）】
4F100AK01B4F100AK01C
"""
    out = parse_classifications_from_raw(raw)
    assert out["fterm"] == ["4F100AK01B", "4F100AK01C"]
    assert out["theme_codes"] == ["4F100"]


def test_registration_number_uses_registration_num_type():
    body = jb._build_wsp0102_body(
        type("T", (), {"kind": "registration", "number": "7250676"})()
    )
    assert body["NUM_INQRY_DISP"]["NUM_INFO"][0]["NUM_TYPE"] == "PATENT_NUM_B_PATENT_INVENT_DESCRIPT_NUM_C"
