#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PatentCompare Flask application factory."""
from __future__ import annotations

import hmac as _hmac
from base64 import b64decode as _b64decode

from flask import Flask, jsonify, request

from modules.app_config import load_env, get_app_config
from blueprints._helpers import PROJECT_ROOT, _is_loopback


def create_app():
    load_env()
    app_cfg = get_app_config()

    app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))
    app.secret_key = app_cfg.secret_key
    app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
    app.json.ensure_ascii = False
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True

    @app.after_request
    def _no_cache_for_html(resp):
        ct = resp.headers.get("Content-Type", "")
        if ct.startswith("text/html"):
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
        return resp

    @app.before_request
    def _require_lan_basic_auth():
        if not app_cfg.lan_password:
            return None
        remote = (request.remote_addr or "").strip()
        if _is_loopback(remote):
            return None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Basic "):
            try:
                decoded = _b64decode(auth_header[6:]).decode("utf-8", errors="replace")
                user, _, pw = decoded.partition(":")
            except Exception:
                user, pw = "", ""
            if (_hmac.compare_digest(user, app_cfg.lan_username)
                    and _hmac.compare_digest(pw, app_cfg.lan_password)):
                return None
        return ("認証が必要です", 401,
                {"WWW-Authenticate": 'Basic realm="patent-compare"'})

    @app.errorhandler(413)
    def _payload_too_large(_error):
        return jsonify({"error": "アップロードできるファイルサイズを超えています"}), 413

    @app.errorhandler(500)
    def _internal_error(error):
        app.logger.exception("Unhandled server error: %s", error)
        return jsonify({"error": "サーバーエラーが発生しました"}), 500

    from blueprints.auto_bp import bp as auto_bp
    from blueprints.cases_bp import bp as cases_bp
    from blueprints.chat_analysis_bp import bp as chat_analysis_bp
    from blueprints.comparison_bp import bp as comparison_bp
    from blueprints.downloads_bp import bp as downloads_bp
    from blueprints.extract_bp import bp as extract_bp
    from blueprints.keywords_bp import bp as keywords_bp
    from blueprints.search_bp import bp as search_bp
    from blueprints.search_fulltext_bp import bp as search_fulltext_bp
    from blueprints.search_runs_bp import bp as search_runs_bp
    from blueprints.segments_bp import bp as segments_bp

    for bp in (
        cases_bp, extract_bp, segments_bp, keywords_bp, comparison_bp,
        downloads_bp, search_bp, search_runs_bp, search_fulltext_bp,
        chat_analysis_bp, auto_bp,
    ):
        app.register_blueprint(bp)

    return app


app = create_app()
_app_cfg = get_app_config()


if __name__ == "__main__":
    (PROJECT_ROOT / "templates").mkdir(exist_ok=True)
    print("PatentCompare Web GUI")
    print(f"http://{_app_cfg.host}:{_app_cfg.port}  (debug={_app_cfg.debug})")
    try:
        from modules.claude_client import llm_status as _llm_status
        _st = _llm_status()
        print(
            "LLM status: "
            f"claude={_st.get('claude_available')} "
            f"codex={_st.get('codex_available')} "
            f"glm={_st.get('glm_available')} "
            f"local={_st.get('local_available')} "
            f"local_model={_st.get('local_model')}"
        )
    except Exception as e:
        print(f"LLM status: unavailable ({e})")
    if _app_cfg.host == "0.0.0.0":
        print(f"   LAN access: 他端末からは http://<このPCのLAN IP>:{_app_cfg.port}")
        if _app_cfg.lan_password:
            print(f"   Basic Auth: user='{_app_cfg.lan_username}' / "
                  f"password set ({len(_app_cfg.lan_password)} chars)")
        else:
            print("   !!! WARNING: PATENT_COMPARE_LAN_PASSWORD 未設定 - LAN から無認証アクセス可能")
    if _app_cfg.debug and _app_cfg.host == "0.0.0.0":
        print("!!! CRITICAL: debug=True + host=0.0.0.0 は LAN からコード実行可能。")
        print("!!!           PATENT_COMPARE_DEBUG=0 にしてください。")
    app.run(debug=_app_cfg.debug, host=_app_cfg.host, port=_app_cfg.port)
