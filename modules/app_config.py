#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""軽量 .env ローダー と 実行時設定ヘルパー。

外部依存（python-dotenv 等）を増やさないために最低限の機能のみ提供する。

使い方:
    from modules.app_config import load_env, get_app_config
    load_env()
    cfg = get_app_config()
    app.secret_key = cfg.secret_key
    app.run(host=cfg.host, port=cfg.port, debug=cfg.debug)
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
_SECRET_KEY_FILE = PROJECT_ROOT / ".secret_key"


def load_env(path: Path | str | None = None) -> None:
    """`.env` を読んで os.environ に反映する（既存の環境変数は上書きしない）。

    フォーマット:
        KEY=value         # コメント可
        KEY="value with spaces"
        # コメント行や空行は無視
    """
    env_path = Path(path) if path else _DEFAULT_ENV_PATH
    if not env_path.exists():
        return

    try:
        text = env_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = env_path.read_text(encoding="cp932")

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # 行末コメント除去（クォート外のみ）
        if not (value.startswith('"') or value.startswith("'")):
            for i, ch in enumerate(value):
                if ch == "#" and (i == 0 or value[i - 1].isspace()):
                    value = value[:i].rstrip()
                    break
        # クォート除去
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]

        if key and key not in os.environ:
            os.environ[key] = value


def _as_bool(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _load_or_create_secret_key() -> str:
    """.secret_key ファイルから読み込み、無ければ生成して保存する。"""
    if _SECRET_KEY_FILE.exists():
        key = _SECRET_KEY_FILE.read_text(encoding="utf-8").strip()
        if key:
            return key

    key = secrets.token_urlsafe(48)
    try:
        _SECRET_KEY_FILE.write_text(key, encoding="utf-8")
    except OSError:
        # 書き込みできなくてもアプリは動かす（メモリ上のキーで継続）
        pass
    return key


@dataclass(frozen=True)
class AppConfig:
    host: str
    port: int
    debug: bool
    secret_key: str


def get_app_config() -> AppConfig:
    """環境変数から Flask 起動設定を組み立てる。

    対応する環境変数:
        PATENT_COMPARE_HOST       既定: 127.0.0.1
        PATENT_COMPARE_PORT       既定: 5000
        PATENT_COMPARE_DEBUG      既定: 0 (false)
        PATENT_COMPARE_SECRET_KEY 既定: .secret_key ファイル or 自動生成
    """
    host = os.environ.get("PATENT_COMPARE_HOST", "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(os.environ.get("PATENT_COMPARE_PORT", "5000"))
    except ValueError:
        port = 5000

    debug = _as_bool(os.environ.get("PATENT_COMPARE_DEBUG"), default=False)

    secret_key = os.environ.get("PATENT_COMPARE_SECRET_KEY", "").strip()
    if not secret_key:
        secret_key = _load_or_create_secret_key()

    return AppConfig(host=host, port=port, debug=debug, secret_key=secret_key)
