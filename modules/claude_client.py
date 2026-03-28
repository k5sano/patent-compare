#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Claude Code CLI ラッパー

プロンプトを一時ファイルに書き出し、stdinリダイレクトで渡す。
長文プロンプト（100KB超）でも安定して動作する。
Claude Max (OAuth認証) 環境対応。
出力はバイナリモードで取得しUTF-8デコード（Windows cp932問題回避）。
"""

import os
import subprocess
import shutil
import logging
import tempfile

logger = logging.getLogger(__name__)

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


def is_claude_available():
    """claude CLI が PATH 上に存在するか確認"""
    return shutil.which("claude") is not None


def call_claude(prompt_text, timeout=DEFAULT_TIMEOUT):
    """Claude Code CLI にプロンプトを送信し、回答テキストを返す。

    Parameters:
        prompt_text: プロンプト文字列
        timeout: タイムアウト秒数（デフォルト600秒）

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

    logger.info("Claude CLI 呼び出し: prompt=%d文字, timeout=%d秒", len(prompt_text), timeout)

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
                ["claude", "-p"],
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
