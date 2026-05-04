#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""壁打ち chat サービス — Step 2 (本願理解) と Step 4.5 (検索) で
ユーザーが LLM と対話的に往復しながら知見を貯めるための機能。

スレッド永続化先: cases/<id>/analysis/chat/<thread_id>.json
LLM 呼び出し: modules.claude_client.call_claude (model=claude-opus-4-6)
LLM が応答内に [[suggest kind=... target=... value=...]] マーカーを入れた場合は
パースして UI 側で「適用」ボタン → apply_suggestion で既存 service を呼び出す。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from services import case_service
from services.case_service import get_case_dir, load_case_meta

logger = logging.getLogger(__name__)

_CLAUDE_MODEL = "claude-sonnet-4-6"  # chat は応答速度優先で Sonnet
_VALID_TOPICS = ("hongan", "search", "free")


# ============================================================
# パス・ファイル I/O
# ============================================================

def _chat_dir(case_id: str) -> Path:
    return get_case_dir(case_id) / "analysis" / "chat"


def _thread_path(case_id: str, thread_id: str) -> Path:
    return _chat_dir(case_id) / f"{thread_id}.json"


def _load_thread_file(case_id: str, thread_id: str) -> dict | None:
    p = _thread_path(case_id, thread_id)
    if not p.exists():
        return None
    try:
        with p.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("chat thread 読込失敗 %s: %s", p, e)
        return None


def _save_thread_file(case_id: str, thread: dict) -> Path:
    d = _chat_dir(case_id)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{thread['id']}.json"
    with p.open("w", encoding="utf-8") as f:
        json.dump(thread, f, ensure_ascii=False, indent=2)
    return p


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _now_compact() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


# ============================================================
# CRUD
# ============================================================

def list_threads(case_id: str, topic: str | None = None):
    """指定案件のスレッド一覧を更新時刻降順で返す。"""
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404
    d = _chat_dir(case_id)
    out = []
    if d.exists():
        for p in d.glob("*.json"):
            try:
                with p.open(encoding="utf-8") as f:
                    t = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            if topic and t.get("topic") != topic:
                continue
            out.append({
                "id": t.get("id"),
                "topic": t.get("topic"),
                "title": t.get("title", ""),
                "created_at": t.get("created_at", ""),
                "updated_at": t.get("updated_at", ""),
                "message_count": len(t.get("messages") or []),
            })
    out.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return {"threads": out}, 200


def create_thread(case_id: str, topic: str, title: str = ""):
    """新規スレッドを作成して保存。"""
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404
    if topic not in _VALID_TOPICS:
        return {"error": f"topic は {_VALID_TOPICS} のいずれか"}, 400

    thread_id = f"{topic}_{_now_compact()}_{uuid.uuid4().hex[:6]}"
    title = (title or "").strip() or "(無題)"
    now = _now_iso()
    thread = {
        "id": thread_id,
        "topic": topic,
        "title": title,
        "created_at": now,
        "updated_at": now,
        "messages": [],
        "applied_events": [],
    }
    _save_thread_file(case_id, thread)
    return {"thread": thread}, 200


def load_thread(case_id: str, thread_id: str):
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404
    t = _load_thread_file(case_id, thread_id)
    if not t:
        return {"error": "スレッドが見つかりません"}, 404
    return {"thread": t}, 200


def delete_thread(case_id: str, thread_id: str):
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404
    p = _thread_path(case_id, thread_id)
    if not p.exists():
        return {"error": "スレッドが見つかりません"}, 404
    try:
        p.unlink()
    except OSError as e:
        return {"error": f"削除失敗: {e}"}, 500
    return {"success": True}, 200


# ============================================================
# Suggestion マーカーパース
# ============================================================

# [[suggest kind=update_analysis_item target=1.1 value="..."]]
# value は ダブルクォートで囲む。エスケープ "" で内部のクォートを表現。
_SUGGEST_RE = re.compile(
    r"\[\[\s*suggest"
    r"\s+kind=([A-Za-z_]+)"
    r"\s+target=([^\s\]]+)"
    r"\s+value=\"((?:[^\"\\]|\\.)*)\""
    r"\s*\]\]"
)

_VALID_SUGGEST_KINDS = (
    "update_analysis_item",
    "append_understanding_note",
    "add_citation",
)


def _parse_suggestions(text: str) -> list[dict]:
    """応答テキストから suggestion マーカーを抽出。

    Returns: [{id, kind, target, value, applied: False}]
    不正な kind は除外。
    """
    out = []
    for m in _SUGGEST_RE.finditer(text or ""):
        kind = m.group(1)
        if kind not in _VALID_SUGGEST_KINDS:
            continue
        target = m.group(2).strip()
        value = m.group(3).replace('\\"', '"').replace("\\\\", "\\")
        out.append({
            "id": uuid.uuid4().hex[:8],
            "kind": kind,
            "target": target,
            "value": value,
            "applied": False,
        })
    return out


# ============================================================
# プロンプト構成
# ============================================================

def _read_json(case_id: str, *parts) -> dict | None:
    p = get_case_dir(case_id).joinpath(*parts)
    if not p.exists():
        return None
    try:
        with p.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _read_text(case_id: str, *parts) -> str:
    p = get_case_dir(case_id).joinpath(*parts)
    if not p.exists():
        return ""
    try:
        with p.open(encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _hongan_excerpt(hongan: dict | None, budget: int = 15000) -> str:
    """本願メタ + 請求項全文 + 段落抜粋 (budget 文字まで)。残りは Read ツールで
    必要時にロードしてもらう前提。"""
    if not hongan:
        return "(本願データなし)"
    lines = [
        f"## 本願メタ",
        f"公開番号: {hongan.get('patent_number', '')}",
        f"発明の名称: {hongan.get('patent_title', '')}",
        f"総ページ数: {hongan.get('total_pages', '?')}",
        "",
    ]
    claims = hongan.get("claims") or []
    if claims:
        lines.append("## 請求項 (全件)")
        for c in claims:
            lines.append(f"請求項{c.get('number', '?')}: {c.get('text', '')}")
        lines.append("")
    used = sum(len(s) for s in lines)
    paragraphs = hongan.get("paragraphs") or []
    if paragraphs:
        lines.append("## 明細書本文 (抜粋。全段落は Read ツールで hongan.json を参照)")
        for p in paragraphs:
            entry = f"【{p.get('id', '')}】({p.get('section', '')}) {p.get('text', '')}"
            if used + len(entry) > budget:
                lines.append(f"... (以降 {len(paragraphs) - paragraphs.index(p)} 段落省略 - 必要なら Read で hongan.json を参照)")
                break
            lines.append(entry)
            used += len(entry)
    n_tables = len(hongan.get("tables") or [])
    if n_tables:
        lines.append(f"\n## 表 ({n_tables} 件あり)")
        lines.append(f"hongan.json の \"tables\" フィールドに格納。Read で取得して下さい。")
    return "\n".join(lines)


def _citations_index(case_id: str) -> str:
    """全引例のラベル + メタ + ファイルパスを一覧化 (本文は inline しない)。
    LLM はこの index を見て、必要な引例を Read ツールで取得する。"""
    cit_dir = get_case_dir(case_id) / "citations"
    if not cit_dir.exists():
        return "## 引例\n(引例データなし)"
    files = sorted([p for p in cit_dir.iterdir() if p.suffix == ".json"])
    if not files:
        return "## 引例\n(引例データなし)"

    lines = [
        f"## 引例 ({len(files)} 件) — Read ツールで必要な JSON を取得",
        "",
        "下記表からラベル / 公開番号 / 役割を確認し、必要な引例をフルパスで Read してください。",
        "各 JSON は claims (請求項全件) / paragraphs (全段落 [{id, section, text}]) / tables (表全件) を持つ。",
        "",
        "| ラベル | 公開番号 | 役割 | タイトル | ファイルパス |",
        "|---|---|---|---|---|",
    ]
    for fp in files:
        try:
            with fp.open(encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        label = d.get("label") or fp.stem
        pn = d.get("patent_number", "")
        role = d.get("role", "")
        title = (d.get("patent_title") or "").replace("|", "/")[:40]
        lines.append(f"| {label} | {pn} | {role} | {title} | `{fp.resolve().as_posix()}` |")
    return "\n".join(lines)


def _file_access_guide(case_id: str) -> str:
    """LLM に対してファイルアクセスのガイドを示す。Claude Code CLI の Read/Grep
    ツールが使えるので、必要に応じてロードできる。"""
    case_dir = get_case_dir(case_id).resolve().as_posix()
    return f"""## ファイル参照ガイド (重要)

あなたは Claude Code CLI 内で動いており、**Read / Grep / Glob ツール** が使えます。
案件のすべてのデータは以下のパス配下にあります:

```
{case_dir}/
├── hongan.json                       # 本願 (claims, paragraphs[全段落], tables)
├── segments.json                     # Step 2 分節 (構成要件)
├── analysis/
│   ├── hongan_analysis.json          # Step 2 SUB 3 本願分析 (8 セクション 33 項目)
│   ├── hongan_understanding.md       # 予備調査メモ
│   └── chat/<thread_id>.json         # 本 chat スレッド自身
├── citations/<label>.json            # 引例 (上の表参照、claims/paragraphs/tables)
├── responses/<label>.json            # 引例ごとの対比結果 (Step 5 出力)
├── keywords.json                     # Step 3 キーワード辞書
├── search/
│   ├── tech_analysis.json            # Step 4 Stage 1 構造化
│   ├── classification.json           # 分類コード (FI/F-term/IPC)
│   └── presearch_candidates.json     # 予備検索候補
└── output/
    ├── *.xlsx                        # 対比表 Excel
    └── *_annotated.pdf               # 引例注釈 PDF
```

**プロンプトには軽量な抜粋しか入れていない**。質問に答えるのに本文・表が要るときは、
迷わず Read ツールで該当ファイルをフルパスで取得してください (相対パス禁止)。

引例 paragraphs から特定段落だけ欲しい時は Grep で `{{"id": "0023"` のように
パターン検索すると効率的です。
"""


def _segments_excerpt(segments: list | None) -> str:
    if not segments:
        return "(分節なし)"
    lines = ["## 構成要件 (Step 2 分節)"]
    for c in segments:
        lines.append(f"請求項{c.get('claim_number')} ({'独立' if c.get('is_independent') else '従属'}):")
        for s in c.get("segments") or []:
            lines.append(f"  {s.get('id')}: {s.get('text', '')}")
    return "\n".join(lines)


def _analysis_excerpt(analysis: dict | None) -> str:
    if not analysis:
        return ""
    lines = ["## 本願分析 (SUB 3 既存出力)"]
    for sec in analysis.get("sections") or []:
        lines.append(f"### {sec.get('id')} {sec.get('title', '')}")
        for it in sec.get("items") or []:
            v = it.get("value")
            if v is None or v == "":
                continue
            v_str = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
            v_str = v_str[:600]
            lines.append(f"- {it.get('id')} {it.get('label', '')}: {v_str}")
    return "\n".join(lines)


_SYSTEM_TEMPLATE = """あなたは熟練した特許サーチャーの相棒です。{topic_role}

== 応答スタイル (重要) ==
- **直接的・断定的に答える**。本願コンテキストに書いてある事実は確認なしで使う。
- **聞き返さずに、まず自分で答える**。本当に曖昧で答えられない時だけ、回答の最後に
  1 行で問い直す (「念のため: Aですか?Bですか?」)。
- ユーザーが何かを依頼したら **「やりますか?」と聞かない**。やる前提で、必要な前提を
  自分で埋めて結論を出す。確認が必要なのは破壊的操作だけ。
- 簡潔に。1 メッセージあたり 200〜500 字目安。但し論理展開が必要な時は伸ばしてよい。
- 文献名・段落番号・数値範囲は本願/引例コンテキストに書いてあるならそのまま引用。
- 引例参照時は「引例 <ラベル> 段落【0023】」のように出典を明記。本文・表・請求項のどこからの引用か区別する。
- 本文や表の詳細が要るときは **Read / Grep ツールで該当 JSON を取得してから答える**。
  prompt 内には軽量サマリしかないので、推測で答えず実データを取りに行くこと。

== Web 検索の利用 ==
- 案件データに無い情報 (原料の業界一般情報, 競合他社の動向, 規格・規制, 公知技術の補強)
  が必要なときは **WebSearch / WebFetch ツールを使ってよい**。
- Web 由来の情報を回答に使うときは **必ず出典を明示**:
  - 文末または該当文の直後に `(出典: <ページタイトル> https://...)` 形式で URL を貼る
  - 複数ソースを組み合わせる時は箇条書きで全 URL を列挙
  - 推測や記憶ベースで補わず、Web 取得した事実のみを引用元として記載する
- 特許公報そのものを Web から取得した場合 (J-PlatPat / Google Patents / Espacenet 等) も
  同様に URL 明示。本案件に既に登録された引例 (citations/) と区別するため
  「(Web 取得・未登録)」と注記する。
- 提案・推論を出すときは「〜と思われる」より「〜です」を優先 (誤りはユーザーが訂正する)。
- 「確認させてください」「念のため」「もしよろしければ」は避ける。テンポを上げる。

== データ修正の提案 (任意機能) ==
本願分析項目や予備調査メモを書き換えると有用な時のみ、回答末尾に下記マーカーを入れる:

  [[suggest kind=update_analysis_item target=<項目ID> value="<新しい値>"]]
  [[suggest kind=append_understanding_note target=<セクション名> value="<追記内容>"]]
  [[suggest kind=add_citation target=<公報番号> value="<役割: 主引例|副引例|参考 等>"]]

ユーザーが UI で「適用」ボタンを押すまでデータは書き換わらない。
1 メッセージあたり最大 3 件。提案不要なら入れなくてよい (むしろ入れない方が良い)。

**add_citation について**:
- Web 検索で見つけた有効文献や、調査中に判明した関連特許を引用文献リストに登録する用途
- target は Google Patents で DL 可能な公報番号 (例 WO2022/044362, 特開2020-132594, JP6960743B2)
- 提案前に「この文献はなぜ有効か」を本文で 1〜2 文で説明 (請求項対応箇所、新規性/進歩性論点等)
- 適用クリックで自動 DL → citations/ に登録される (DL 失敗時は J-PlatPat URL が表示されるので
  ユーザーが手動 DL する流れ)

{file_access_guide}

== 案件コンテキスト (軽量サマリ) ==
{hongan_block}

{segments_block}

{analysis_block}

{understanding_block}

{citations_index}
"""


_TOPIC_ROLES = {
    "hongan": "本願 (請求項・実施例・先行技術) の理解を深めるための対話を行います。",
    "search": "先行文献検索の戦略 (検索式・キーワード・分類・ノイズ除去) を一緒に練ります。",
    "free": "案件全般の質問・議論に応じます。",
}


def _build_chat_prompt(case_id: str, topic: str, history: list[dict],
                       new_user_msg: str) -> str:
    """LLM への入力テキストを組み立てる (call_claude は単一 prompt 受取なので
    system + history + new を 1 つにまとめる)。
    """
    hongan = _read_json(case_id, "hongan.json")
    segments = _read_json(case_id, "segments.json")
    analysis = _read_json(case_id, "analysis", "hongan_analysis.json")
    understanding = _read_text(case_id, "analysis", "hongan_understanding.md")

    system_part = _SYSTEM_TEMPLATE.format(
        topic_role=_TOPIC_ROLES.get(topic, _TOPIC_ROLES["free"]),
        file_access_guide=_file_access_guide(case_id),
        hongan_block=_hongan_excerpt(hongan),
        segments_block=_segments_excerpt(segments),
        analysis_block=_analysis_excerpt(analysis),
        understanding_block=("## 予備調査メモ\n" + understanding) if understanding else "",
        citations_index=_citations_index(case_id),
    )

    history_lines = []
    for m in history or []:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "user":
            history_lines.append(f"\n[ユーザー]\n{content}")
        else:
            history_lines.append(f"\n[アシスタント]\n{content}")

    return (
        system_part
        + "\n\n== これまでの会話 ==\n"
        + ("\n".join(history_lines) if history_lines else "(まだありません)")
        + f"\n\n[ユーザー (新規)]\n{new_user_msg}\n\n[アシスタント]\n"
    )


# ============================================================
# メッセージ送受信
# ============================================================

def append_message_and_reply(case_id: str, thread_id: str, user_msg: str,
                             claude_timeout: int = 900):
    """ユーザーメッセージを追記し、LLM 応答を生成して同じスレッドに追記する。

    タイムアウト 15 分 (900 秒): chat は Read/Grep ツールで案件全データ
    に自律アクセスする設計のため、長文回答だと数分〜10分かかる。
    短くするとブラウザ側 'Failed to fetch' が早すぎて UX 崩壊する。
    """
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404
    user_msg = (user_msg or "").strip()
    if not user_msg:
        return {"error": "メッセージが空です"}, 400

    thread = _load_thread_file(case_id, thread_id)
    if not thread:
        return {"error": "スレッドが見つかりません"}, 404

    # ユーザーメッセージを追記
    now = _now_iso()
    thread["messages"].append({
        "role": "user", "content": user_msg, "timestamp": now,
    })

    # LLM 呼び出し
    try:
        from modules.claude_client import call_claude, ClaudeClientError
    except ImportError:
        return {"error": "claude_client が利用できません"}, 500

    # 直近メッセージを除いた履歴を渡す (新規メッセージは prompt 末尾に別途付ける)
    history_for_prompt = thread["messages"][:-1]
    prompt = _build_chat_prompt(case_id, thread.get("topic", "free"),
                                history_for_prompt, user_msg)
    try:
        raw = call_claude(prompt, timeout=claude_timeout, model=_CLAUDE_MODEL)
    except ClaudeClientError as e:
        # 失敗したらユーザーメッセージは追記済みでもエラー追記
        thread["messages"].append({
            "role": "assistant",
            "content": f"(LLM 呼び出しに失敗しました: {e})",
            "timestamp": _now_iso(),
            "suggestions": [],
            "_error": True,
        })
        thread["updated_at"] = _now_iso()
        _save_thread_file(case_id, thread)
        return {"error": str(e), "thread": thread}, 502

    suggestions = _parse_suggestions(raw)
    thread["messages"].append({
        "role": "assistant",
        "content": raw,
        "timestamp": _now_iso(),
        "suggestions": suggestions,
    })
    thread["updated_at"] = _now_iso()
    _save_thread_file(case_id, thread)
    return {"thread": thread}, 200


# ============================================================
# Suggestion 適用
# ============================================================

def apply_suggestion(case_id: str, thread_id: str, suggestion_id: str):
    """LLM 提案を既存 service 経由で実行し、適用イベントを thread に記録。"""
    if not load_case_meta(case_id):
        return {"error": "案件が見つかりません"}, 404

    thread = _load_thread_file(case_id, thread_id)
    if not thread:
        return {"error": "スレッドが見つかりません"}, 404

    # suggestion を探す
    target_sugg = None
    target_msg_idx = None
    for i, msg in enumerate(thread.get("messages") or []):
        for sugg in msg.get("suggestions") or []:
            if sugg.get("id") == suggestion_id:
                target_sugg = sugg
                target_msg_idx = i
                break
        if target_sugg:
            break
    if not target_sugg:
        return {"error": "suggestion が見つかりません"}, 404
    if target_sugg.get("applied"):
        return {"error": "既に適用済みです"}, 409

    kind = target_sugg.get("kind")
    target = target_sugg.get("target")
    value = target_sugg.get("value")

    # 適用先 service を呼ぶ
    apply_result = None
    apply_error = None
    if kind == "update_analysis_item":
        from services.hongan_analysis_service import update_item_value
        result, code = update_item_value(case_id, target, value)
        if code != 200:
            apply_error = result.get("error", f"HTTP {code}")
        else:
            apply_result = "ok"
    elif kind == "append_understanding_note":
        from services.preliminary_research_service import save_note
        result = save_note(case_id, component=target, note=value,
                           urls_opened=[], queries=[], field=None)
        if isinstance(result, dict) and result.get("error"):
            apply_error = result["error"]
        else:
            apply_result = "ok"
    elif kind == "add_citation":
        # target = patent_id (例 "WO2022/044362"), value = role (例 "主引例")
        from services.case_service import register_citation_by_patent_id
        role = value.strip() or "参考"
        result, code = register_citation_by_patent_id(case_id, target, role=role)
        if code != 200:
            apply_error = result.get("error", f"HTTP {code}")
            # DL 失敗時の手動 DL ヒントを呼び出し元に伝える
            if result.get("jplatpat_url") or result.get("google_patents_url"):
                target_sugg["dl_hint"] = {
                    "jplatpat_url": result.get("jplatpat_url"),
                    "google_patents_url": result.get("google_patents_url"),
                    "hint": result.get("hint"),
                }
        else:
            apply_result = f"ok (doc_id={result.get('doc_id')})"
    else:
        return {"error": f"未対応の kind: {kind}"}, 400

    if apply_error:
        return {"error": apply_error}, 500

    # 適用済みフラグ + イベント記録
    target_sugg["applied"] = True
    target_sugg["applied_at"] = _now_iso()
    thread.setdefault("applied_events", []).append({
        "timestamp": _now_iso(),
        "suggestion_id": suggestion_id,
        "kind": kind,
        "target": target,
        "result": apply_result,
    })
    thread["updated_at"] = _now_iso()
    _save_thread_file(case_id, thread)

    return {"success": True, "suggestion": target_sugg, "thread": thread}, 200
