#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
EPO Open Patent Services (OPS) クライアント

EPO OPS REST API v3.2 を使って特許検索・書誌データ取得を行う。
OAuth2 (client_credentials) 認証。

登録: https://developers.epo.org/
ドキュメント: https://www.epo.org/en/searching-for-patents/data/web-services/ops

使い方:
    client = EpoOpsClient(consumer_key="...", consumer_secret="...")
    hits = client.search_patents("ta=エアゾール and ta=化粧料", max_results=10)
"""

import base64
import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

OPS_AUTH_URL = "https://ops.epo.org/3.2/auth/accesstoken"
OPS_SEARCH_URL = "https://ops.epo.org/3.2/rest-services/published-data/search/biblio"
OPS_BIBLIO_URL = "https://ops.epo.org/3.2/rest-services/published-data/publication/epodoc"

# XML namespaces used in OPS responses
NS = {
    "ops": "http://ops.epo.org",
    "xchange": "http://www.epo.org/exchange",
    "reg": "http://www.epo.org/register",
}


@dataclass
class PatentHit:
    """検索結果1件"""
    patent_id: str          # e.g. "JP2024037328A"
    title: str = ""
    title_en: str = ""
    applicant: str = ""
    publication_date: str = ""  # YYYYMMDD
    ipc_codes: List[str] = field(default_factory=list)
    abstract: str = ""
    source: str = "epo_ops"
    url: str = ""


class EpoOpsClient:
    """EPO OPS REST API クライアント"""

    def __init__(self, consumer_key: str, consumer_secret: str):
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self._token: Optional[str] = None
        self._token_expires: float = 0

    # ------------------------------------------------------------------
    # OAuth2 認証
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        """OAuth2 アクセストークンを取得（キャッシュ付き）"""
        now = time.time()
        if self._token and now < self._token_expires - 30:
            return self._token

        credentials = f"{self.consumer_key}:{self.consumer_secret}"
        b64 = base64.b64encode(credentials.encode()).decode()

        try:
            resp = requests.post(
                OPS_AUTH_URL,
                headers={
                    "Authorization": f"Basic {b64}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data="grant_type=client_credentials",
                timeout=15,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("EPO OPS 認証失敗: %s", e)
            raise RuntimeError(f"EPO OPS 認証失敗: {e}") from e

        data = resp.json()
        self._token = data["access_token"]
        self._token_expires = now + int(data.get("expires_in", 1200))
        logger.info("EPO OPS トークン取得成功 (expires_in=%s)", data.get("expires_in"))
        return self._token

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._get_token()}"}

    # ------------------------------------------------------------------
    # 検索
    # ------------------------------------------------------------------

    def search_patents(
        self,
        cql_query: str,
        max_results: int = 25,
    ) -> List[PatentHit]:
        """CQL クエリで特許を検索し、書誌データ付きの結果を返す。

        Args:
            cql_query: CQL クエリ文字列
                例: 'ta="aerosol cosmetic" and ipc="A61K8"'
                例: 'ta="エアゾール" and ta="化粧料"'
            max_results: 最大取得件数 (1-100)

        Returns:
            PatentHit のリスト
        """
        max_results = min(max(max_results, 1), 100)
        logger.info("EPO OPS 検索: q=%s, max=%d", cql_query, max_results)

        try:
            resp = requests.get(
                OPS_SEARCH_URL,
                params={"q": cql_query},
                headers={
                    **self._auth_headers(),
                    "Accept": "application/xml",
                    "Range": f"1-{max_results}",
                },
                timeout=30,
            )
        except requests.RequestException as e:
            logger.warning("EPO OPS 検索リクエスト失敗: %s", e)
            return []

        if resp.status_code == 404:
            # 結果0件
            logger.info("EPO OPS 検索結果: 0件")
            return []

        if resp.status_code != 200:
            logger.warning(
                "EPO OPS 検索失敗: status=%d, body=%s",
                resp.status_code, resp.text[:300],
            )
            return []

        return self._parse_search_response(resp.text)

    def search_keywords(
        self,
        keywords: List[str],
        country: str = "",
        ipc: str = "",
        max_results: int = 15,
    ) -> List[PatentHit]:
        """キーワードリストからCQLクエリを組み立てて検索する。

        Args:
            keywords: 検索キーワード（日本語/英語混在可）
            country: 国コード制限 (例: "JP")
            ipc: IPC分類制限 (例: "A61K8")
            max_results: 最大件数

        Returns:
            PatentHit のリスト
        """
        if not keywords:
            return []

        # キーワードを ta= (title/abstract) でAND結合
        # 長すぎるとエラーになるので最大5語
        terms = keywords[:5]
        ta_parts = [f'ta="{kw}"' for kw in terms]
        cql = " and ".join(ta_parts)

        if country:
            cql += f' and pn="{country}"'
        if ipc:
            cql += f' and ipc="{ipc}"'

        return self.search_patents(cql, max_results=max_results)

    # ------------------------------------------------------------------
    # XML パース
    # ------------------------------------------------------------------

    def _parse_search_response(self, xml_text: str) -> List[PatentHit]:
        """OPS 検索結果XMLをパースして PatentHit リストを返す"""
        hits: List[PatentHit] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning("EPO OPS XML パースエラー: %s", e)
            return []

        # 検索結果件数
        biblio_search = root.find(".//ops:biblio-search", NS)
        if biblio_search is not None:
            total = biblio_search.get("total-result-count", "?")
            logger.info("EPO OPS 検索結果: total=%s", total)

        # exchange-document を走査
        for doc in root.iter("{http://www.epo.org/exchange}exchange-document"):
            hit = self._parse_exchange_document(doc)
            if hit:
                hits.append(hit)

        return hits

    def _parse_exchange_document(self, doc) -> Optional[PatentHit]:
        """1件の exchange-document をパース"""
        country = doc.get("country", "")
        doc_number = doc.get("doc-number", "")
        kind = doc.get("kind", "")

        if not doc_number:
            return None

        patent_id = f"{country}{doc_number}{kind}"

        # タイトル (日本語優先、なければ英語)
        title_ja = ""
        title_en = ""
        for title_elem in doc.iter("{http://www.epo.org/exchange}invention-title"):
            lang = title_elem.get("lang", "")
            text = (title_elem.text or "").strip()
            if lang == "ja":
                title_ja = text
            elif lang == "en":
                title_en = text

        # 出願人
        applicant = ""
        for app_elem in doc.iter("{http://www.epo.org/exchange}applicant"):
            data_format = app_elem.get("data-format", "")
            if data_format == "original" or not applicant:
                name_elem = app_elem.find(
                    "{http://www.epo.org/exchange}applicant-name/"
                    "{http://www.epo.org/exchange}name"
                )
                if name_elem is not None and name_elem.text:
                    applicant = name_elem.text.strip()
                    if data_format == "original":
                        break

        # 公開日
        pub_date = ""
        pub_ref = doc.find(
            ".//{http://www.epo.org/exchange}publication-reference"
            "/{http://www.epo.org/exchange}document-id"
        )
        if pub_ref is not None:
            date_elem = pub_ref.find("{http://www.epo.org/exchange}date")
            if date_elem is not None and date_elem.text:
                pub_date = date_elem.text.strip()

        # IPC分類
        ipc_codes = []
        for ipc_elem in doc.iter("{http://www.epo.org/exchange}classification-ipc"):
            main = ipc_elem.find("{http://www.epo.org/exchange}main-classification")
            if main is not None and main.text:
                ipc_codes.append(main.text.strip())
            for further in ipc_elem.findall(
                "{http://www.epo.org/exchange}further-classification"
            ):
                if further.text:
                    ipc_codes.append(further.text.strip())

        # 要約（日本語優先）
        abstract = ""
        for abs_elem in doc.iter("{http://www.epo.org/exchange}abstract"):
            lang = abs_elem.get("lang", "")
            text_parts = []
            for p in abs_elem.iter("{http://www.epo.org/exchange}p"):
                if p.text:
                    text_parts.append(p.text.strip())
            full_text = " ".join(text_parts)
            if lang == "ja" or not abstract:
                abstract = full_text[:300]
                if lang == "ja":
                    break

        # Google Patents URL
        url = f"https://patents.google.com/patent/{patent_id}"

        return PatentHit(
            patent_id=patent_id,
            title=title_ja or title_en,
            title_en=title_en,
            applicant=applicant,
            publication_date=pub_date,
            ipc_codes=ipc_codes[:5],
            abstract=abstract,
            source="epo_ops",
            url=url,
        )

    # ------------------------------------------------------------------
    # ユーティリティ
    # ------------------------------------------------------------------

    def test_connection(self) -> bool:
        """接続テスト。認証が通るかどうかを確認。"""
        try:
            self._get_token()
            return True
        except Exception as e:
            logger.error("EPO OPS 接続テスト失敗: %s", e)
            return False
