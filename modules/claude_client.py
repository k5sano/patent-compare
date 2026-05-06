#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LLM 呼び出しラッパー。

Claude は Claude Code CLI、Codex は ChatGPT ログイン済みの Codex CLI、
GLM は API で呼び出す。既存コードとの互換のため関数名 call_claude は残す。
"""

import os
import re
import json
import subprocess
import shutil
import logging
import tempfile
from pathlib import Path

import requests

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


def _load_config_value(*keys):
    """環境変数優先で config.yaml の値も読む。キー名は大文字/小文字を許容。"""
    for key in keys:
        val = os.environ.get(key, "")
        if val:
            return val
    config_path = PROJECT_ROOT / "config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            for key in keys:
                for cand in (key, key.lower(), key.lower().replace("_api_key", "_key")):
                    val = cfg.get(cand, "")
                    if val:
                        return val
        except Exception:
            pass
    return ""


def _load_serpapi_key():
    """SerpAPIキーを読み込む（環境変数優先、config.yamlフォールバック）"""
    return _load_config_value("SERPAPI_KEY")


def _load_zai_api_key():
    return _load_config_value("ZAI_API_KEY", "GLM_API_KEY", "BIGMODEL_API_KEY")


def _load_zai_base_url():
    return (
        _load_config_value("ZAI_BASE_URL", "GLM_BASE_URL", "BIGMODEL_BASE_URL")
        or "https://api.z.ai/api/paas/v4"
    )


def _build_mcp_config():
    """MCP検索サーバーの設定JSONを一時ファイルに書き出し、パスを返す。
    Playwright直接検索はSerpAPIキー不要で動作する。
    SerpAPIキーがあればGoogle Scholar検索も利用可能。
    """
    server_script = str(PROJECT_ROOT / "modules" / "mcp_search_server.py")
    env = {}
    serpapi_key = _load_serpapi_key()
    if serpapi_key:
        env["SERPAPI_KEY"] = serpapi_key

    config = {
        "mcpServers": {
            "patent-search": {
                "command": "python",
                "args": [server_script],
                "env": env,
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


def is_codex_available():
    return shutil.which("codex") is not None


def is_glm_available():
    return bool(_load_zai_api_key())


# UI / API パラメータで受け取るエイリアスをフルモデル ID に解決する。
# CLI が直接エイリアス受け付けに対応する版もあるが、ここでは安全のため明示的にマップ。
MODEL_ALIASES = {
    # Claude
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    "claude-opus": "claude-opus-4-6",
    "claude-sonnet": "claude-sonnet-4-6",
    "claude-haiku": "claude-haiku-4-5-20251001",
    # Codex CLI (ChatGPT ログイン): Claude の用途名に寄せた別名
    "codex-opus": "gpt-5.5",
    "codex-opus-pro": "gpt-5.5-pro",
    "codex-sonnet": "gpt-5.4",
    "codex-sonnet-fast": "gpt-5.4-mini",
    "codex-haiku": "gpt-5.4-nano",
    # 旧UI保存値との互換。OpenAI APIではなくCodex CLIへ流す。
    "openai-opus": "gpt-5.5",
    "openai-opus-pro": "gpt-5.5-pro",
    "openai-sonnet": "gpt-5.4",
    "openai-sonnet-fast": "gpt-5.4-mini",
    "openai-haiku": "gpt-5.4-nano",
    # GLM
    "glm-opus": "glm-5.1",
    "glm-sonnet": "glm-5-turbo",
    "glm-turbo": "glm-5-turbo",
    "glm-fast": "glm-4.7",
    "glm-haiku": "glm-4.5-air",
    "glm-air": "glm-4.5-air",
}


def resolve_model(name):
    """エイリアス ('opus'/'sonnet'/'haiku') もフル ID も同じく受け付け、
    フル ID を返す。空・未知文字列はそのまま返す（CLI 側に解釈を任せる）。
    None なら None（CLI既定）。"""
    if not name:
        return None
    if ":" in name:
        provider, raw_model = name.split(":", 1)
        if provider.lower() in ("claude", "anthropic", "codex", "openai", "glm", "zai", "z.ai"):
            name = raw_model
    return MODEL_ALIASES.get(name, name)


def model_provider(name):
    """モデル指定から provider を推定する。未知は Claude CLI 互換として扱う。"""
    if not name:
        return "claude"
    raw = str(name).strip().lower()
    if ":" in raw:
        provider, _raw_model = raw.split(":", 1)
        if provider in ("claude", "anthropic"):
            return "claude"
        if provider in ("codex", "openai"):
            return "codex"
        if provider in ("glm", "zai", "z.ai"):
            return "glm"
    resolved = (resolve_model(name) or "").lower()
    if raw.startswith(("codex-", "openai-")):
        return "codex"
    if resolved.startswith(("gpt-", "o1", "o3", "o4")):
        return "codex"
    if resolved.startswith("glm-"):
        return "glm"
    return "claude"


def is_llm_available(model=None):
    provider = model_provider(model)
    if provider == "codex":
        return is_codex_available()
    if provider == "glm":
        return is_glm_available()
    return is_claude_available()


def llm_status():
    return {
        "available": is_claude_available() or is_codex_available() or is_glm_available(),
        "claude_available": is_claude_available(),
        "codex_available": is_codex_available(),
        # 旧フロント互換: OpenAI系は Codex CLI 経由
        "openai_available": is_codex_available(),
        "glm_available": is_glm_available(),
        "search_available": is_claude_available() or is_codex_available() or bool(_load_serpapi_key()),
    }


# --effort のデフォルト値。ユーザ設定 (settings.json) で xhigh/max になっている
# 場合があるが、本プロジェクトの対比/検索/キーワードでは high で十分。
# レートリミット消費を抑える目的で明示的に指定する。
DEFAULT_EFFORT = "high"


def _call_codex_exec(prompt_text, timeout, use_search, model, effort,
                     image_path: Path | None = None):
    if not is_codex_available():
        raise ClaudeNotFoundError(
            "codex CLI が見つかりません。ChatGPT/Codex 拡張または Codex CLI を確認してください。"
        )

    out_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", encoding="utf-8", delete=False
    )
    out_path = Path(out_file.name)
    out_file.close()

    cmd = ["codex"]
    if use_search:
        cmd.append("--search")
    cmd.extend([
        "exec",
        "--model", model,
        "--sandbox", "read-only",
        "--cd", str(PROJECT_ROOT),
        "--skip-git-repo-check",
        "--output-last-message", str(out_path),
    ])
    if image_path is not None:
        cmd.extend(["--image", str(Path(image_path).resolve())])

    logger.info("Codex CLI 呼び出し: model=%s prompt=%d文字 search=%s image=%s",
                model, len(prompt_text), use_search, bool(image_path))
    try:
        result = subprocess.run(
            cmd,
            input=prompt_text.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        out_path.unlink(missing_ok=True)
        raise ClaudeTimeoutError(f"Codex CLI がタイムアウトしました（{timeout}秒）。") from e
    except FileNotFoundError as e:
        out_path.unlink(missing_ok=True)
        raise ClaudeNotFoundError("codex CLI の実行に失敗しました。PATH を確認してください。") from e

    try:
        if result.returncode != 0:
            stderr_msg = result.stderr.decode("utf-8", errors="replace").strip()[:500]
            raise ClaudeExecutionError(
                f"Codex CLI がエラーコード {result.returncode} で終了: {stderr_msg}"
            )
        response_text = out_path.read_text(encoding="utf-8", errors="replace")
        if not response_text.strip():
            fallback = result.stdout.decode("utf-8", errors="replace")
            response_text = fallback
        if not response_text.strip():
            raise ClaudeExecutionError("Codex CLI から空の応答が返されました。")
        return response_text
    finally:
        try:
            out_path.unlink(missing_ok=True)
        except Exception:
            logger.debug("Codex CLI 一時出力ファイルの削除に失敗: %s", out_path, exc_info=True)


def _glm_thinking(effort):
    e = (effort or "").lower()
    if e in ("medium", "high", "xhigh", "max"):
        return {"type": "enabled"}
    return None


def _call_glm_chat(prompt_text, timeout, use_search, model, effort):
    api_key = _load_zai_api_key()
    if not api_key:
        raise ClaudeNotFoundError("ZAI_API_KEY / GLM_API_KEY / BIGMODEL_API_KEY が設定されていません。")
    if use_search:
        logger.warning("GLM では patent-compare の検索ツール連携を行わず、通常のLLM呼び出しとして実行します。")

    base_url = _load_zai_base_url().rstrip("/")
    url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt_text}],
        "max_tokens": 32768,
        "temperature": 0.2,
    }
    thinking = _glm_thinking(effort)
    if thinking:
        payload["thinking"] = thinking
    logger.info("GLM 呼び出し: model=%s prompt=%d文字", model, len(prompt_text))
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
    except requests.Timeout as e:
        raise ClaudeTimeoutError(f"GLM API がタイムアウトしました（{timeout}秒）。") from e
    except requests.RequestException as e:
        raise ClaudeExecutionError(f"GLM API 呼び出しに失敗しました: {e}") from e
    if resp.status_code >= 400:
        raise ClaudeExecutionError(f"GLM API エラー {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    try:
        response_text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise ClaudeExecutionError(f"GLM API 応答形式を解釈できません: {str(data)[:500]}") from e
    if not (response_text or "").strip():
        raise ClaudeExecutionError("GLM API から空の応答が返されました。")
    return response_text


def call_claude(prompt_text, timeout=DEFAULT_TIMEOUT, use_search=False, model=None,
                effort=DEFAULT_EFFORT):
    """選択モデルに応じて Claude CLI / Codex CLI / GLM API を呼び出す。

    Parameters:
        prompt_text: プロンプト文字列
        timeout: タイムアウト秒数（デフォルト600秒）
        use_search: True の場合、MCP検索サーバーを有効にする
        model: モデル指定。'opus'/'codex-sonnet'/'glm-opus' 等のエイリアス
               またはフル ID。None の場合 Claude CLI 既定。
        effort: Effort レベル ('low'/'medium'/'high'/'xhigh'/'max')。
                None なら CLI 既定 (= ユーザ settings.json) を使う。
                デフォルトは 'high' (リミット消費を抑える)。

    Returns:
        str: LLM の回答テキスト

    Raises:
        ClaudeNotFoundError: CLI や API キーが見つからない
        ClaudeTimeoutError: タイムアウト
        ClaudeExecutionError: 実行エラー
    """
    provider = model_provider(model)
    resolved_model = resolve_model(model)
    if provider == "codex":
        return _call_codex_exec(
            prompt_text, timeout, use_search, resolved_model, effort,
        )
    if provider == "glm":
        return _call_glm_chat(prompt_text, timeout, use_search, resolved_model, effort)

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
    if resolved_model:
        cmd.extend(["--model", resolved_model])
    if effort:
        cmd.extend(["--effort", effort])

    # MCP検索サーバー設定
    mcp_config_path = None
    if use_search:
        mcp_config_path = _build_mcp_config()
        if mcp_config_path:
            cmd.extend(["--mcp-config", mcp_config_path])
            # MCPツールの実行を事前許可（-p モードではインタラクティブ許可不可）
            cmd.extend(["--allowedTools", "mcp__patent-search__*"])
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


def call_llm_with_image(prompt_text, image_path, timeout=DEFAULT_TIMEOUT, model=None,
                        effort=DEFAULT_EFFORT):
    """画像入力つきLLM呼び出し。現在は Codex CLI を対象にする。"""
    provider = model_provider(model)
    resolved_model = resolve_model(model)
    if provider == "codex":
        return _call_codex_exec(
            prompt_text, timeout, False, resolved_model, effort,
            image_path=Path(image_path),
        )
    raise ClaudeExecutionError(f"画像入力はこのモデルでは未対応です: {model or 'default'}")


# 特許番号検出用の正規表現（半角・全角対応）
_PATENT_RE = re.compile(
    r'(?:特開|特願|特表|再公表|JP|US|WO|EP|CN|KR)'
    r'[\s\u3000]*'
    r'[\d０-９]{4}'
    r'[\s\u3000]*[-/−ー]?[\s\u3000]*'
    r'[\d０-９]{3,7}'
)


def call_claude_stream(prompt_text, timeout=DEFAULT_TIMEOUT, use_search=False, model=None,
                        effort=DEFAULT_EFFORT):
    """Claude Code CLI にプロンプトを送信し、進捗イベントを yield するジェネレータ。

    --output-format stream-json を使用してストリーミング出力を取得し、
    ツール呼び出し（検索クエリ）や特許番号の出現をリアルタイムで検出する。

    Yields:
        dict: 進捗イベント
            {"type": "search", "query": "..."}       — MCP検索ツール呼び出し検出
            {"type": "candidate", "number": "...", "count": N} — 特許番号検出
            {"type": "status", "message": "..."}     — 状態メッセージ
            {"type": "done", "response": "..."}      — 完了（最終テキスト）
            {"type": "error", "message": "..."}      — エラー
    """
    provider = model_provider(model)
    if provider != "claude":
        try:
            response = call_claude(
                prompt_text, timeout=timeout, use_search=use_search,
                model=model, effort=effort,
            )
            yield {"type": "done", "response": response}
        except ClaudeClientError as e:
            yield {"type": "error", "message": str(e)}
        return

    if not is_claude_available():
        yield {"type": "error", "message": "claude CLI が見つかりません"}
        return

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("ANTHROPIC_API_KEY", None)

    cmd = ["claude", "-p", "--verbose", "--output-format", "stream-json"]
    resolved_model = resolve_model(model)
    if resolved_model:
        cmd.extend(["--model", resolved_model])
    if effort:
        cmd.extend(["--effort", effort])

    mcp_config_path = None
    if use_search:
        mcp_config_path = _build_mcp_config()
        if mcp_config_path:
            cmd.extend(["--mcp-config", mcp_config_path])
            cmd.extend(["--allowedTools", "mcp__patent-search__*"])

    logger.info("Claude CLI ストリーム呼び出し: prompt=%d文字, search=%s, model=%s",
                len(prompt_text), use_search, resolved_model or "default")

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", encoding="utf-8", delete=False
    )
    tmp.write(prompt_text)
    tmp.close()

    proc = None
    try:
        stdin_file = open(tmp.name, "rb")
        proc = subprocess.Popen(
            cmd,
            stdin=stdin_file,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        stdin_file.close()

        yield {"type": "status", "message": "Claude CLI 起動中..."}

        full_result = ""
        candidate_count = 0
        seen_patents = set()
        tool_input_buffers = {}  # index -> {"name": str, "json": str}

        for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue

            evt_type = evt.get("type", "")

            # ---- 最終結果 ----
            if evt_type == "result":
                result_text = evt.get("result", "")
                if result_text:
                    full_result = result_text
                break

            # ---- content_block_start: ツール呼び出し開始 ----
            if evt_type == "content_block_start":
                block = evt.get("content_block", {})
                if block.get("type") == "tool_use":
                    idx = evt.get("index", 0)
                    tool_input_buffers[idx] = {
                        "name": block.get("name", ""),
                        "json": "",
                    }

            # ---- content_block_delta: テキスト or ツール入力の断片 ----
            if evt_type == "content_block_delta":
                delta = evt.get("delta", {})
                idx = evt.get("index", 0)

                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    full_result += text
                    for m in _PATENT_RE.finditer(text):
                        pat = m.group(0).replace("\u3000", "").replace(" ", "")
                        if pat not in seen_patents:
                            seen_patents.add(pat)
                            candidate_count += 1
                            yield {"type": "candidate", "number": pat, "count": candidate_count}

                if delta.get("type") == "input_json_delta" and idx in tool_input_buffers:
                    tool_input_buffers[idx]["json"] += delta.get("partial_json", "")

            # ---- content_block_stop: ツール入力完成 → クエリ抽出 ----
            if evt_type == "content_block_stop":
                idx = evt.get("index", 0)
                if idx in tool_input_buffers:
                    tool_info = tool_input_buffers.pop(idx)
                    try:
                        input_obj = json.loads(tool_info["json"])
                        query = input_obj.get("query", "") or input_obj.get("q", "")
                        if query:
                            yield {"type": "search", "query": query}
                    except (json.JSONDecodeError, TypeError):
                        pass

            # ---- 高レベル assistant イベント（フォーマット違い対応） ----
            if evt_type == "assistant":
                msg = evt.get("message", {})
                if isinstance(msg, dict):
                    for block in msg.get("content", []):
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_use":
                            inp = block.get("input", {})
                            query = inp.get("query", "") or inp.get("q", "")
                            if query:
                                yield {"type": "search", "query": query}
                        elif block.get("type") == "text":
                            text = block.get("text", "")
                            full_result += text
                            for m in _PATENT_RE.finditer(text):
                                pat = m.group(0).replace("\u3000", "").replace(" ", "")
                                if pat not in seen_patents:
                                    seen_patents.add(pat)
                                    candidate_count += 1
                                    yield {"type": "candidate", "number": pat, "count": candidate_count}

        proc.wait(timeout=timeout)

        if proc.returncode != 0 and not full_result:
            stderr_msg = proc.stderr.read().decode("utf-8", errors="replace").strip()[:500]
            yield {"type": "error", "message": f"Claude CLI エラー: {stderr_msg}"}
            return

        if not full_result.strip():
            yield {"type": "error", "message": "Claude CLI から空の応答"}
            return

        yield {"type": "done", "response": full_result}

    except FileNotFoundError:
        yield {"type": "error", "message": "claude CLI の実行に失敗しました"}
    except Exception as e:
        logger.exception("call_claude_stream 例外")
        yield {"type": "error", "message": str(e)}
    finally:
        if proc and proc.poll() is None:
            proc.kill()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        if mcp_config_path:
            try:
                os.unlink(mcp_config_path)
            except OSError:
                pass
