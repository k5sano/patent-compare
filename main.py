#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PatentCompare — 特許審査拒絶理由構成支援システム
CLI エントリーポイント (薄いラッパー)

ビジネスロジックは services/ 層に集約されており、
このファイルは Click コマンド → service 呼び出しの結線のみを行う。
"""

import json
import click
import pyperclip
import yaml
from pathlib import Path
from rich.console import Console
from rich.table import Table

from services import case_service, comparison_service, keyword_service

console = Console()
PROJECT_ROOT = Path(__file__).parent.resolve()


# ===== CLI 固有: アクティブ案件の管理 =====

def _active_marker_path() -> Path:
    return PROJECT_ROOT / "cases" / ".active"


def get_active_case() -> str | None:
    marker = _active_marker_path()
    if marker.exists():
        return marker.read_text(encoding="utf-8").strip()
    return None


def set_active_case(case_id: str) -> None:
    _active_marker_path().write_text(case_id, encoding="utf-8")


def resolve_case_id(case_id: str | None) -> str:
    """引数省略時はアクティブ案件を返す。無ければ ClickException。"""
    if case_id:
        return case_id
    active = get_active_case()
    if active is None:
        raise click.ClickException("アクティブ案件がありません。'use' コマンドで案件を選択してください。")
    return active


def _ensure_case_exists(case_id: str) -> Path:
    case_dir = case_service.get_case_dir(case_id)
    if not case_dir.exists():
        raise click.ClickException(f"案件 '{case_id}' が見つかりません。")
    return case_dir


def _unwrap(result):
    """サービスが返す (data, code) または data を data に正規化。失敗なら例外を投げる。"""
    if isinstance(result, tuple):
        data, code = result
        if code >= 400 and isinstance(data, dict) and "error" in data:
            raise click.ClickException(data["error"])
        return data
    return result


# ===== メインCLIグループ =====
@click.group()
@click.version_option(version="0.2.0", prog_name="PatentCompare")
def cli():
    """PatentCompare - 特許審査拒絶理由構成支援システム"""
    pass


# ===== 案件管理 =====
@cli.command()
@click.argument("case_id")
@click.option("--title", "-t", required=True, help="案件タイトル（発明の名称）")
@click.option("--field", "-f", type=click.Choice(["cosmetics", "laminate"]),
              default="cosmetics", help="技術分野")
def new(case_id, title, field):
    """新規案件を作成"""
    data = _unwrap(case_service.create_minimal_case(case_id, title=title, field=field))
    set_active_case(case_id)
    console.print(f"[bold green]案件 '{case_id}' を作成しました。[/bold green]")
    console.print(f"  タイトル: {title}")
    console.print(f"  分野: {field}")
    console.print(f"  パス: {data['path']}")


@cli.command("list")
def list_cases():
    """案件一覧を表示"""
    cases = case_service.list_all_cases()
    if not cases:
        console.print("[yellow]案件がありません。[/yellow]")
        return

    active = get_active_case()
    table = Table(title="案件一覧")
    table.add_column("", width=3)
    table.add_column("案件番号")
    table.add_column("タイトル")
    table.add_column("分野")
    table.add_column("引用", justify="right")
    table.add_column("回答", justify="right")

    for meta in cases:
        marker = "▶" if meta.get("case_id") == active else ""
        table.add_row(
            marker,
            meta.get("case_id", ""),
            meta.get("title", "") or meta.get("patent_title", ""),
            meta.get("field", ""),
            str(meta.get("_num_citations", 0)),
            str(meta.get("_num_responses", 0)),
        )

    console.print(table)


@cli.command()
@click.argument("case_id")
def use(case_id):
    """アクティブ案件を切替"""
    _ensure_case_exists(case_id)
    set_active_case(case_id)
    console.print(f"[bold green]アクティブ案件を '{case_id}' に切り替えました。[/bold green]")


# ===== PDF抽出 =====
@cli.command()
@click.argument("doc_type", type=click.Choice(["hongan", "citation"]))
@click.argument("pdf_path", type=click.Path(exists=True))
@click.option("--role", type=click.Choice(["主引例", "副引例", "技術常識"]),
              default="主引例", help="文献の役割（citationのみ）")
@click.option("--label", "-l", default=None, help="文献ラベル（例: 文献1）")
@click.option("--case", "case_id", default=None, help="案件番号（省略時はアクティブ案件）")
def extract(doc_type, pdf_path, role, label, case_id):
    """PDFからテキスト抽出・構造解析"""
    case_id = resolve_case_id(case_id)
    _ensure_case_exists(case_id)

    if doc_type == "hongan":
        data = _unwrap(case_service.upload_hongan(case_id, pdf_path))
        console.print(f"[bold green]本願テキスト抽出完了[/bold green]")
        console.print(f"  特許番号: {data.get('patent_number', '')}")
        console.print(f"  請求項数: {data.get('num_claims', 0)}")
        console.print(f"  段落数:   {data.get('num_paragraphs', 0)}")
    else:
        data = _unwrap(case_service.upload_citation(
            case_id, pdf_path, role=role, label=label or ""))
        console.print(f"[bold green]引用文献テキスト抽出完了[/bold green]")
        console.print(f"  文献ID:   {data['doc_id']}")
        console.print(f"  役割:     {role}")
        console.print(f"  請求項数: {data.get('num_claims', 0)}")
        console.print(f"  段落数:   {data.get('num_paragraphs', 0)}")


# ===== 請求項分節 =====
@cli.group()
def segments():
    """請求項分節の管理"""
    pass


@segments.command("show")
@click.option("--case", "case_id", default=None)
def segments_show(case_id):
    """請求項分節を計算・表示・保存"""
    case_id = resolve_case_id(case_id)
    _ensure_case_exists(case_id)

    data = _unwrap(case_service.compute_segments(case_id))

    for claim in data["segments"]:
        kind = "独立" if claim.get("is_independent") else "従属"
        console.print(f"\n[bold]請求項{claim['claim_number']}[/bold] ({kind})")
        for seg in claim["segments"]:
            console.print(f"  [cyan]{seg['id']}[/cyan]: {seg['text']}")

    console.print(f"\n[dim]保存先: {data['path']}[/dim]")
    console.print("[yellow]修正が必要な場合は 'segments edit' で編集してください。[/yellow]")


@segments.command("edit")
@click.option("--case", "case_id", default=None)
def segments_edit(case_id):
    """エディタで分節を修正"""
    case_id = resolve_case_id(case_id)
    case_dir = _ensure_case_exists(case_id)
    segments_path = case_dir / "segments.json"
    if not segments_path.exists():
        raise click.ClickException("分節データがありません。先に 'segments show' を実行してください。")
    click.edit(filename=str(segments_path))
    console.print("[bold green]分節データを更新しました。[/bold green]")


# ===== キーワード =====
@cli.group()
def keywords():
    """キーワードグループ管理"""
    pass


@keywords.command("suggest")
@click.option("--case", "case_id", default=None)
def keywords_suggest(case_id):
    """キーワード＋Fterm自動提案"""
    case_id = resolve_case_id(case_id)
    _ensure_case_exists(case_id)

    _unwrap(keyword_service.suggest_keywords(case_id))
    data = _unwrap(keyword_service.get_keywords(case_id))

    for group in data:
        color_name = group.get("color", "white")
        segs = ", ".join(group.get("segment_ids", []))
        console.print(f"\n[bold]グループ{group['group_id']}: {group['label']}[/bold] "
                       f"(色: {color_name}, 分節: {segs})")
        for kw in group.get("keywords", []):
            console.print(f"  [{kw.get('type', '')}] {kw.get('term', '')} "
                           f"(出典: {kw.get('source', '')})")
        for ft in group.get("search_codes", {}).get("fterm", []):
            console.print(f"  Fterm: {ft.get('code')}: {ft.get('desc')}")


@keywords.command("show")
@click.option("--case", "case_id", default=None)
def keywords_show(case_id):
    """現在のキーワードグループを表示"""
    case_id = resolve_case_id(case_id)
    _ensure_case_exists(case_id)

    groups = _unwrap(keyword_service.get_keywords(case_id))
    if not groups:
        raise click.ClickException("キーワードデータがありません。先に 'keywords suggest' を実行してください。")

    for group in groups:
        console.print(f"\n[bold]グループ{group['group_id']}: {group['label']}[/bold]")
        for kw in group.get("keywords", []):
            console.print(f"  {kw.get('term', '')}")


@keywords.command("add")
@click.argument("group_id", type=int)
@click.argument("term")
@click.option("--case", "case_id", default=None)
def keywords_add(group_id, term, case_id):
    """キーワードをグループに追加"""
    case_id = resolve_case_id(case_id)
    _ensure_case_exists(case_id)

    _unwrap(keyword_service.add_keyword(case_id, group_id, term))
    console.print(f"[bold green]グループ{group_id}に '{term}' を追加しました。[/bold green]")


# ===== 対比プロンプト生成 =====
@cli.command()
@click.argument("citation_id")
@click.option("--file", "to_file", is_flag=True, help="ファイルに出力（default: クリップボード）")
@click.option("--case", "case_id", default=None)
def prompt(citation_id, to_file, case_id):
    """対比プロンプト生成→クリップボードにコピー"""
    case_id = resolve_case_id(case_id)
    case_dir = _ensure_case_exists(case_id)

    data = _unwrap(comparison_service.generate_prompt_single(case_id, citation_id))
    prompt_text = data["prompt"]

    if to_file:
        output_path = case_dir / "prompts" / f"{citation_id}_prompt.txt"
        console.print(f"[bold green]プロンプトをファイルに出力しました[/bold green] → {output_path}")
    else:
        pyperclip.copy(prompt_text)
        console.print(f"[bold green]プロンプトをクリップボードにコピーしました[/bold green]")

    console.print(f"  文字数: {data['char_count']:,}")
    console.print("\n[yellow]Claudeチャットに貼り付けて実行してください。[/yellow]")


# ===== Claude回答取り込み =====
@cli.command()
@click.argument("citation_id")
@click.option("--file", "from_file", type=click.Path(exists=True), default=None,
              help="ファイルから読み込み（default: クリップボードから）")
@click.option("--case", "case_id", default=None)
def response(citation_id, from_file, case_id):
    """Claude回答を取り込み・パース"""
    case_id = resolve_case_id(case_id)
    _ensure_case_exists(case_id)

    if from_file:
        with open(from_file, "r", encoding="utf-8") as f:
            raw_text = f.read()
    else:
        raw_text = pyperclip.paste()
        if not raw_text.strip():
            raise click.ClickException("クリップボードが空です。")

    data = _unwrap(comparison_service.save_response_single(case_id, citation_id, raw_text))

    if data.get("errors"):
        console.print("[bold red]パースエラー:[/bold red]")
        for err in data["errors"]:
            console.print(f"  - {err}")
    else:
        console.print("[bold green]回答パース成功[/bold green]")

    if data.get("success"):
        case_dir = case_service.get_case_dir(case_id)
        console.print(f"  保存先: {case_dir / 'responses' / f'{citation_id}.json'}")


# ===== 成果物出力 =====
@cli.group()
def export():
    """成果物出力"""
    pass


@export.command("table")
@click.option("--case", "case_id", default=None)
def export_table(case_id):
    """対比表Excelを出力"""
    case_id = resolve_case_id(case_id)
    _ensure_case_exists(case_id)

    data = _unwrap(comparison_service.export_excel(case_id))
    console.print(f"[bold green]対比表Excelを出力しました[/bold green] → {data.get('path', '')}")


@export.command("all")
@click.option("--case", "case_id", default=None)
@click.pass_context
def export_all(ctx, case_id):
    """全成果物を出力（現状は対比表Excelのみ）"""
    ctx.invoke(export_table, case_id=case_id)
    console.print("[bold green]全成果物の出力が完了しました。[/bold green]")


if __name__ == "__main__":
    cli()
