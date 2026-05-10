"""Flask 主要ルートと Basic 認証のスモークテスト。"""
from __future__ import annotations

import base64
import importlib


def _auth(user="patent", pw="testpw123"):
    raw = f"{user}:{pw}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def test_index_and_case_detail_return_200(copy_case_fixture):
    copy_case_fixture("smoke")
    import web

    web.app.config["TESTING"] = True
    client = web.app.test_client()

    assert client.get("/").status_code == 200
    resp = client.get("/case/smoke")
    assert resp.status_code == 200
    assert "Step 6".encode("utf-8") in resp.data


def test_basic_auth_loopback_bypasses_lan_password(monkeypatch):
    monkeypatch.setenv("PATENT_COMPARE_LAN_PASSWORD", "testpw123")
    monkeypatch.setenv("PATENT_COMPARE_LAN_USERNAME", "patent")
    import web

    importlib.reload(web)
    web.app.config["TESTING"] = True
    resp = web.app.test_client().get("/", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})

    assert resp.status_code != 401


def test_basic_auth_lan_requires_credentials(monkeypatch):
    monkeypatch.setenv("PATENT_COMPARE_LAN_PASSWORD", "testpw123")
    monkeypatch.setenv("PATENT_COMPARE_LAN_USERNAME", "patent")
    import web

    importlib.reload(web)
    web.app.config["TESTING"] = True
    client = web.app.test_client()

    assert client.get("/", environ_overrides={"REMOTE_ADDR": "192.168.1.10"}).status_code == 401
    ok = client.get(
        "/",
        environ_overrides={"REMOTE_ADDR": "192.168.1.10"},
        headers={"Authorization": _auth()},
    )
    assert ok.status_code != 401

