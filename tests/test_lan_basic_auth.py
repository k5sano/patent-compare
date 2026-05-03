"""LAN 越しアクセス時の Basic 認証 (loopback は素通し) の回帰防止テスト。"""
from __future__ import annotations

import base64

import pytest


@pytest.fixture
def app_with_password(monkeypatch):
    """PATENT_COMPARE_LAN_PASSWORD を設定した状態で web.py を再ロード"""
    monkeypatch.setenv("PATENT_COMPARE_LAN_PASSWORD", "testpw123")
    monkeypatch.setenv("PATENT_COMPARE_LAN_USERNAME", "patent")
    # web を再 import
    import importlib
    import web as web_mod
    importlib.reload(web_mod)
    web_mod.app.config["TESTING"] = True
    return web_mod.app


@pytest.fixture
def app_without_password(monkeypatch):
    monkeypatch.delenv("PATENT_COMPARE_LAN_PASSWORD", raising=False)
    import importlib
    import web as web_mod
    importlib.reload(web_mod)
    web_mod.app.config["TESTING"] = True
    return web_mod.app


def _basic_auth_header(user, pw):
    raw = f"{user}:{pw}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


class TestLoopbackBypass:
    def test_loopback_passes_without_credentials(self, app_with_password):
        client = app_with_password.test_client()
        # Flask test_client は remote_addr を 127.0.0.1 にする
        resp = client.get("/")
        # / が 200 を返すか、または別の正当応答 (302/404) — 401 でなければ OK
        assert resp.status_code != 401


class TestLanAuth:
    def test_lan_request_without_credentials_401(self, app_with_password):
        client = app_with_password.test_client()
        # LAN からのリクエストを擬装 (ヘッダ X-Forwarded-For は使わず environ で偽装)
        resp = client.get("/", environ_overrides={"REMOTE_ADDR": "192.168.1.10"})
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers
        assert resp.headers["WWW-Authenticate"].startswith("Basic")

    def test_lan_request_with_correct_credentials(self, app_with_password):
        client = app_with_password.test_client()
        resp = client.get("/", environ_overrides={"REMOTE_ADDR": "192.168.1.10"},
                          headers={"Authorization": _basic_auth_header("patent", "testpw123")})
        assert resp.status_code != 401

    def test_lan_request_with_wrong_password(self, app_with_password):
        client = app_with_password.test_client()
        resp = client.get("/", environ_overrides={"REMOTE_ADDR": "192.168.1.10"},
                          headers={"Authorization": _basic_auth_header("patent", "WRONG")})
        assert resp.status_code == 401

    def test_lan_request_with_wrong_user(self, app_with_password):
        client = app_with_password.test_client()
        resp = client.get("/", environ_overrides={"REMOTE_ADDR": "192.168.1.10"},
                          headers={"Authorization": _basic_auth_header("attacker", "testpw123")})
        assert resp.status_code == 401


class TestNoPasswordMode:
    def test_lan_request_without_password_set_passes(self, app_without_password):
        """PATENT_COMPARE_LAN_PASSWORD 未設定時は LAN からも 401 にしない (従来動作)"""
        client = app_without_password.test_client()
        resp = client.get("/", environ_overrides={"REMOTE_ADDR": "192.168.1.10"})
        assert resp.status_code != 401
