#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Claude Code CLI ラッパー

プロンプトを一時ファイルに書き出し、stdinリダイレクトで渡す。
長文プロンプト（100KB超）でも安定して動作する。
Claude Max (OAuth認証) 環境対応。
出力はバイナリモードで取得しUTF-8デコード（Windows cp932問題回避）。
MCP検索サーバー連携対応（--mcp-config）。
"""

import os
import json
import subprocess
import shutil
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DEFAULT_TIMEOUT = 600  # 10分


class ClaudeClientError(Exception):
    """Claude CLI エラーの基底クラス"""
    pass


class ClaudeNotFoundError(ClaudeClientError):
    """claude CLI が PATH 上に見つからない"""
    pass


class ClaudeTimeoutError(ClaudeClientError):
    """claude CLI がタイムアウト"""
    pass


class ClaudeExecutionError(ClaudeClientError):
    """claude CLI が非ゼロ終了コード"""
    pass


def _load_serpapi_key():
    """config.yamlからSerpAPIキーを読み込む"""
    config_path = PROJECT_ROOT / "config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            return cfg.get("serpapi_key", "")
        except Exception:
            pass
    return ""


def _build_mcp_config():
    """MCP検索サーバーの設定JSONを一時ファイルに書き出し、パスを返す。
    SerpAPIキーが未設定の場合はNoneを返す。
    """
    serpapi_key = _load_serpapi_key()
    if not serpapi_key:
        return None

    server_script = str(PROJECT_ROOT / "modules" / "mcp_search_server.py")
    config = {
        "mcpServers": {
            "patent-search": {
                "command": "python",
                "args": [server_script],
                "env": {
                    "SERPAPI_KEY": serpapi_key,
                },
            }
        }
    }

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", encoding="utf-8", delete=False
    )
    json.dump(config, tmp, ensure_ascii=False)
    tmp.close()
    return tmp.name


def is_claude_available():
    """claude CLI が PATH 上に存在するか確認"""
    return shutil.which("claude") is not None


def call_claude(prompt_text, timeout=DEFAULT_TIMEOUT, use_search=False):
    """Claude Code CLI にプロンプトを送信し、回答テキストを返す。

    Parameters:
        prompt_text: プロンプト文字列
        timeout: タイムアウト秒数（デフォルト600秒）
        use_search: True の場合、MCP検索サーバーを有効にする

    Returns:
        str: Claude の回答テキスト

    Raises:
        ClaudeNotFoundError: claude CLI が見つからない
        ClaudeTimeoutError: タイムアウト
        ClaudeExecutionError: 実行エラー
    """
    if not is_claude_available():
        raise ClaudeNotFoundError(
            "claude CLI が見つかりません。Claude Code がインストールされているか確認してください。"
        )

    # セッション固有の環境変数を除去し、OAuth認証を使わせる
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)        # ネストセッション防止
    env.pop("ANTHROPIC_API_KEY", None)  # セッションキー除去→OAuthフォールバック

    # コマンド構築
    cmd = ["claude", "-p"]

    # MCP検索サーバー設定
    mcp_config_path = None
    if use_search:
        mcp_config_path = _build_mcp_config()
        if mcp_config_path:
            cmd.extend(["--mcp-config", mcp_config_path])
            logger.info("MCP検索サーバー有効")

    logger.info("Claude CLI 呼び出し: prompt=%d文字, timeout=%d秒, search=%s",
                len(prompt_text), timeout, use_search)

    # プロンプトを一時ファイルに書き出し（UTF-8）
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", encoding="utf-8", delete=False
    )
    try:
        tmp.write(prompt_text)
        tmp.close()

        # stdinリダイレクト + バイナリモードで出力取得
        with open(tmp.name, "rb") as stdin_file:
            result = subprocess.run(
                cmd,
                stdin=stdin_file,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                env=env,
            )
    except subprocess.TimeoutExpired:
        raise ClaudeTimeoutError(
            f"Claude CLI がタイムアウトしました（{timeout}秒）。"
        )
    except FileNotFoundError:
        raise ClaudeNotFoundError(
            "claude CLI の実行に失敗しました。PATH を確認してください。"
        )
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        if mcp_config_path:
            try:
                os.unlink(mcp_config_path)
            except OSError:
                pass

    if result.returncode != 0:
        stderr_msg = result.stderr.decode("utf-8", errors="replace").strip()[:500]
        raise ClaudeExecutionError(
            f"Claude CLI がエラーコード {result.returncode} で終了: {stderr_msg}"
        )

    # stdout をUTF-8デコード（claude -p の出力はUTF-8）
    response_text = result.stdout.decode("utf-8", errors="replace")
    if not response_text.strip():
        raise ClaudeExecutionError("Claude CLI から空の応答が返されました。")

    logger.info("Claude CLI 応答: %d文字", len(response_text))
    return response_text
