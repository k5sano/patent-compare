"""app_config モジュールのテスト"""

import os
from pathlib import Path

import pytest

from modules import app_config


@pytest.fixture
def clean_env(monkeypatch):
    """PATENT_COMPARE_* 環境変数をクリアした状態にする"""
    for key in list(os.environ):
        if key.startswith("PATENT_COMPARE_"):
            monkeypatch.delenv(key, raising=False)


class TestLoadEnv:
    def test_basic_key_value(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\nBAZ=qux\n", encoding="utf-8")

        monkeypatch.delenv("FOO", raising=False)
        monkeypatch.delenv("BAZ", raising=False)
        app_config.load_env(env_file)

        assert os.environ.get("FOO") == "bar"
        assert os.environ.get("BAZ") == "qux"

    def test_quoted_values(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text('KEY_A="hello world"\nKEY_B=\'single\'\n', encoding="utf-8")

        monkeypatch.delenv("KEY_A", raising=False)
        monkeypatch.delenv("KEY_B", raising=False)
        app_config.load_env(env_file)

        assert os.environ["KEY_A"] == "hello world"
        assert os.environ["KEY_B"] == "single"

    def test_comments_and_blank_lines(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# これはコメント\n\nKEY=value  # inline comment\n",
            encoding="utf-8",
        )

        monkeypatch.delenv("KEY", raising=False)
        app_config.load_env(env_file)
        assert os.environ["KEY"] == "value"

    def test_does_not_overwrite_existing(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("MYKEY=from_file\n", encoding="utf-8")

        monkeypatch.setenv("MYKEY", "from_shell")
        app_config.load_env(env_file)
        assert os.environ["MYKEY"] == "from_shell"

    def test_missing_file_is_noop(self, tmp_path):
        app_config.load_env(tmp_path / "does_not_exist.env")


class TestGetAppConfig:
    def test_defaults(self, clean_env):
        cfg = app_config.get_app_config()
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 5000
        assert cfg.debug is False
        assert cfg.secret_key  # 何らかのキーが返る

    def test_env_overrides(self, clean_env, monkeypatch):
        monkeypatch.setenv("PATENT_COMPARE_HOST", "0.0.0.0")
        monkeypatch.setenv("PATENT_COMPARE_PORT", "8080")
        monkeypatch.setenv("PATENT_COMPARE_DEBUG", "true")
        monkeypatch.setenv("PATENT_COMPARE_SECRET_KEY", "my-explicit-key")

        cfg = app_config.get_app_config()
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 8080
        assert cfg.debug is True
        assert cfg.secret_key == "my-explicit-key"

    @pytest.mark.parametrize("val,expected", [
        ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
        ("0", False), ("false", False), ("no", False), ("", False),
    ])
    def test_debug_boolean_parsing(self, clean_env, monkeypatch, val, expected):
        monkeypatch.setenv("PATENT_COMPARE_DEBUG", val)
        assert app_config.get_app_config().debug is expected

    def test_invalid_port_falls_back(self, clean_env, monkeypatch):
        monkeypatch.setenv("PATENT_COMPARE_PORT", "abc")
        assert app_config.get_app_config().port == 5000
