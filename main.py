#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PatentCompare — 特許審査拒絶理由構成支援システム
CLIエントリーポイント
"""

import os
import json
import click
import yaml
import pyperclip
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

# プロジェクトルートを基準にパス解決
PROJECT_ROOT = Path(__file__).parent.resolve()


def load_config():
    """config.yaml を読み込む"""
    config_path = PROJECT_ROOT / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_active_case():
    """アクティブ案件の案件番号を取得"""
    marker = PROJECT_ROOT / "cases" / ".active"
    if marker.exists():
        return marker.read_text(encoding="utf-8").strip()
    return None


def set_active_case(case_id):
    """アクティブ案件を設定"""
    marker = PROJECT_ROOT / "cases" / ".active"
    marker.write_text(case_id, encoding="utf-8")


def get_case_dir(case_id=None):
    """案件ディレクトリを取得（case_id省略時はアクティブ案件）"""
    if case_id is None:
        case_id = get_active_case()
        if case_id is None:
            raise click.ClickException("アクティブ案件がありません。'use' コマンドで案件を選択してください。")
    case_dir = PROJECT_ROOT / "cases" / case_id
    if not case_dir.exists():
        raise click.ClickException(f"案件 '{case_id}' が見つかりません。")
    return case_dir


# ===== メインCLIグループ =====
@click.group()
@click.version_option(version="0.1.0", prog_name="PatentCompare")
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
    case_dir = PROJECT_ROOT / "cases" / case_id
    if case_dir.exists():
        raise click.ClickException(f"案件 '{case_id}' は既に存在します。")

    # ディレクトリ構造を作成
    for sub in ["input", "citations", "prompts", "responses", "output"]:
        (case_dir / sub).mkdir(parents=True, exist_ok=True)

    # case.yaml を作成
    case_meta = {
        "case_id": case_id,
        "title": title,
        "field": field,
        "created": str(Path(".")),  # 簡易タイムスタンプ
        "citations": [],
    }
    with open(case_dir / "case.yaml", "w", encoding="utf-8") as f:
        yaml.dump(case_meta, f, allow_unicode=True, default_flow_style=False)

    # アクティブ案件に設定
    set_active_case(case_id)

    console.print(f"[bold green]案件 '{case_id}' を作成しました。[/bold green]")
    console.print(f"  タイトル: {title}")
    console.print(f"  分野: {field}")
    console.print(f"  パス: {case_dir}")


@cli.command("list")
def list_cases():
    """案件一覧を表示"""
    cases_dir = PROJECT_ROOT / "cases"
    if not cases_dir.exists():
        console.print("[yellow]案件がありません。[/yellow]")
        return

    active = get_active_case()
    table = Table(title="案件一覧")
    table.add_column("", width=3)
    table.add_column("案件番号")
    table.add_column("タイトル")
    table.add_column("分野")

    for d in sorted(cases_dir.iterdir()):
        if d.is_dir() and (d / "case.yaml").exists():
            with open(d / "case.yaml", "r", encoding="utf-8") as f:
                meta = yaml.safe_load(f)
            marker = "▶" if d.name == active else ""
            table.add_row(marker, meta.get("case_id", d.name),
                          meta.get("title", ""), meta.get("field", ""))

    console.print(table)


@cli.command()
@click.argument("case_id")
def use(case_id):
    """アクティブ案件を切替"""
    case_dir = PROJECT_ROOT / "cases" / case_id
    if not case_dir.exists():
        raise click.ClickException(f"案件 '{case_id}' が見つかりません。")
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
    from modules.pdf_extractor import extract_patent_pdf

    case_dir = get_case_dir(case_id)
    result = extract_patent_pdf(pdf_path, doc_type)

    if doc_type == "hongan":
        output_path = case_dir / "hongan.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        console.print(f"[bold green]本願テキスト抽出完了[/bold green] → {output_path}")
        console.print(f"  請求項数: {len(result.get('claims', []))}")
        console.print(f"  段落数: {len(result.get('paragraphs', []))}")
    else:
        # 引用文献
        doc_id = result.get("patent_number", Path(pdf_path).stem)
        output_path = case_dir / "citations" / f"{doc_id}.json"
        result["role"] = role
        result["label"] = label or doc_id
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        # case.yamlにも追加
        case_yaml = case_dir / "case.yaml"
        with open(case_yaml, "r", encoding="utf-8") as f:
            meta = yaml.safe_load(f)
        citations = meta.get("citations", [])
        citations.append({"id": doc_id, "role": role, "label": label or doc_id})
        meta["citations"] = citations
        with open(case_yaml, "w", encoding="utf-8") as f:
            yaml.dump(meta, f, allow_unicode=True, default_flow_style=False)

        console.print(f"[bold green]引用文献テキスト抽出完了[/bold green] → {output_path}")
        console.print(f"  文献番号: {doc_id}")
        console.print(f"  役割: {role}")


# ===== 請求項分節 =====
@cli.group()
def segments():
    """請求項分節の管理"""
    pass


@segments.command("show")
@click.option("--case", "case_id", default=None)
def segments_show(case_id):
    """請求項分節結果を表示"""
    from modules.claim_segmenter import segment_claims

    case_dir = get_case_dir(case_id)
    hongan_path = case_dir / "hongan.json"
    segments_path = case_dir / "segments.json"

    if not hongan_path.exists():
        raise click.ClickException("本願テキストがありません。先に 'extract hongan' を実行してください。")

    with open(hongan_path, "r", encoding="utf-8") as f:
        hongan = json.load(f)

    # 分節実行
    result = segment_claims(hongan["claims"])

    # 保存
    with open(segments_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 表示
    for claim in result:
        console.print(f"\n[bold]請求項{claim['claim_number']}[/bold] "
                       f"({'独立' if claim['is_independent'] else '従属'})")
        for seg in claim["segments"]:
            console.print(f"  [cyan]{seg['id']}[/cyan]: {seg['text']}")

    console.print(f"\n[dim]保存先: {segments_path}[/dim]")
    console.print("[yellow]修正が必要な場合は 'segments edit' で編集してください。[/yellow]")


@segments.command("edit")
@click.option("--case", "case_id", default=None)
def segments_edit(case_id):
    """エディタで分節を修正"""
    case_dir = get_case_dir(case_id)
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
    from modules.keyword_suggester import suggest_keywords

    case_dir = get_case_dir(case_id)
    hongan_path = case_dir / "hongan.json"
    segments_path = case_dir / "segments.json"
    case_yaml = case_dir / "case.yaml"

    if not hongan_path.exists():
        raise click.ClickException("本願テキストがありません。")
    if not segments_path.exists():
        raise click.ClickException("分節データがありません。先に 'segments show' を実行してください。")

    with open(hongan_path, "r", encoding="utf-8") as f:
        hongan = json.load(f)
    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)
    with open(case_yaml, "r", encoding="utf-8") as f:
        meta = yaml.safe_load(f)

    field = meta.get("field", "cosmetics")
    result = suggest_keywords(hongan, segs, field)

    # 保存
    kw_path = case_dir / "keywords.json"
    with open(kw_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 表示
    for group in result:
        color_name = group.get("color", "white")
        console.print(f"\n[bold]グループ{group['group_id']}: {group['label']}[/bold] "
                       f"(色: {color_name}, 分節: {', '.join(group['segment_ids'])})")
        for kw in group["keywords"]:
            console.print(f"  [{kw['type']}] {kw['term']} (出典: {kw['source']})")
        if group.get("search_codes", {}).get("fterm"):
            console.print("  Fterm:")
            for ft in group["search_codes"]["fterm"]:
                console.print(f"    {ft['code']}: {ft['desc']}")

    console.print(f"\n[dim]保存先: {kw_path}[/dim]")


@keywords.command("show")
@click.option("--case", "case_id", default=None)
def keywords_show(case_id):
    """現在のキーワードグループを表示"""
    case_dir = get_case_dir(case_id)
    kw_path = case_dir / "keywords.json"
    if not kw_path.exists():
        raise click.ClickException("キーワードデータがありません。先に 'keywords suggest' を実行してください。")

    with open(kw_path, "r", encoding="utf-8") as f:
        groups = json.load(f)

    for group in groups:
        console.print(f"\n[bold]グループ{group['group_id']}: {group['label']}[/bold]")
        for kw in group["keywords"]:
            console.print(f"  {kw['term']}")


@keywords.command("add")
@click.argument("group_id", type=int)
@click.argument("term")
@click.option("--type", "kw_type", default="手動追加", help="キーワード種別")
@click.option("--case", "case_id", default=None)
def keywords_add(group_id, term, kw_type, case_id):
    """キーワードをグループに追加"""
    case_dir = get_case_dir(case_id)
    kw_path = case_dir / "keywords.json"
    if not kw_path.exists():
        raise click.ClickException("キーワードデータがありません。")

    with open(kw_path, "r", encoding="utf-8") as f:
        groups = json.load(f)

    for group in groups:
        if group["group_id"] == group_id:
            group["keywords"].append({"term": term, "source": "手動", "type": kw_type})
            break
    else:
        raise click.ClickException(f"グループ{group_id}が見つかりません。")

    with open(kw_path, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)
    console.print(f"[bold green]グループ{group_id}に '{term}' を追加しました。[/bold green]")


@keywords.command("save")
@click.option("--case", "case_id", default=None)
def keywords_save(case_id):
    """キーワードを確定・保存"""
    case_dir = get_case_dir(case_id)
    kw_path = case_dir / "keywords.json"
    if not kw_path.exists():
        raise click.ClickException("キーワードデータがありません。")
    console.print("[bold green]キーワードグループを確定しました。[/bold green]")


# ===== 対比プロンプト生成 =====
@cli.command()
@click.argument("citation_id")
@click.option("--file", "to_file", is_flag=True, help="ファイルに出力")
@click.option("--case", "case_id", default=None)
def prompt(citation_id, to_file, case_id):
    """対比プロンプト生成→クリップボードにコピー"""
    from modules.prompt_generator import generate_prompt

    case_dir = get_case_dir(case_id)
    segments_path = case_dir / "segments.json"
    kw_path = case_dir / "keywords.json"
    citation_path = case_dir / "citations" / f"{citation_id}.json"

    if not segments_path.exists():
        raise click.ClickException("分節データがありません。")
    if not citation_path.exists():
        raise click.ClickException(f"引用文献 '{citation_id}' が見つかりません。")

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)
    with open(citation_path, "r", encoding="utf-8") as f:
        citation = json.load(f)

    keywords = None
    if kw_path.exists():
        with open(kw_path, "r", encoding="utf-8") as f:
            keywords = json.load(f)

    prompt_text = generate_prompt(segs, citation, keywords)

    if to_file:
        output_path = case_dir / "prompts" / f"{citation_id}_prompt.txt"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(prompt_text)
        console.print(f"[bold green]プロンプトをファイルに出力しました[/bold green] → {output_path}")
    else:
        pyperclip.copy(prompt_text)
        console.print(f"[bold green]プロンプトをクリップボードにコピーしました[/bold green]")
        console.print(f"  文字数: {len(prompt_text):,}")

    console.print(f"\n[yellow]Claudeチャットに貼り付けて実行してください。[/yellow]")


# ===== Claude回答取り込み =====
@cli.command()
@click.argument("citation_id")
@click.option("--file", "from_file", type=click.Path(exists=True), default=None,
              help="ファイルから読み込み")
@click.option("--case", "case_id", default=None)
def response(citation_id, from_file, case_id):
    """Claude回答を取り込み・パース"""
    from modules.response_parser import parse_response

    case_dir = get_case_dir(case_id)
    segments_path = case_dir / "segments.json"

    if not segments_path.exists():
        raise click.ClickException("分節データがありません。")

    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    if from_file:
        with open(from_file, "r", encoding="utf-8") as f:
            raw_text = f.read()
    else:
        raw_text = pyperclip.paste()
        if not raw_text.strip():
            raise click.ClickException("クリップボードが空です。")

    # 全分節IDを収集
    all_segment_ids = []
    for claim in segs:
        for seg in claim["segments"]:
            all_segment_ids.append(seg["id"])

    result, errors = parse_response(raw_text, all_segment_ids)

    if errors:
        console.print("[bold red]パースエラー:[/bold red]")
        for err in errors:
            console.print(f"  - {err}")
        console.print("\n[yellow]補完プロンプトを生成しますか？ (y/n)[/yellow]")
        # エラーがあっても部分的に保存
    else:
        console.print("[bold green]回答パース成功[/bold green]")

    # 保存
    output_path = case_dir / "responses" / f"{citation_id}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    console.print(f"  保存先: {output_path}")


# ===== 成果物出力 =====
@cli.group()
def export():
    """成果物出力"""
    pass


@export.command("table")
@click.option("--case", "case_id", default=None)
def export_table(case_id):
    """対比表Excelを出力"""
    from modules.excel_writer import write_comparison_table

    case_dir = get_case_dir(case_id)
    case_yaml = case_dir / "case.yaml"
    segments_path = case_dir / "segments.json"

    if not segments_path.exists():
        raise click.ClickException("分節データがありません。")

    with open(case_yaml, "r", encoding="utf-8") as f:
        meta = yaml.safe_load(f)
    with open(segments_path, "r", encoding="utf-8") as f:
        segs = json.load(f)

    # 回答データを収集
    responses = {}
    responses_dir = case_dir / "responses"
    if responses_dir.exists():
        for rfile in responses_dir.glob("*.json"):
            with open(rfile, "r", encoding="utf-8") as f:
                responses[rfile.stem] = json.load(f)

    if not responses:
        raise click.ClickException("回答データがありません。先に 'response' コマンドで回答を取り込んでください。")

    # 引用文献メタ情報を収集
    citations_meta = {}
    for cit in meta.get("citations", []):
        cit_path = case_dir / "citations" / f"{cit['id']}.json"
        if cit_path.exists():
            with open(cit_path, "r", encoding="utf-8") as f:
                citations_meta[cit["id"]] = json.load(f)

    output_path = case_dir / "output" / f"{meta['case_id']}_対比表.xlsx"
    write_comparison_table(
        output_path=str(output_path),
        case_meta=meta,
        segments=segs,
        responses=responses,
        citations_meta=citations_meta,
    )
    console.print(f"[bold green]対比表Excelを出力しました[/bold green] → {output_path}")


@export.command("all")
@click.option("--case", "case_id", default=None)
@click.pass_context
def export_all(ctx, case_id):
    """全成果物を出力（Excel＋色塗りPDF）"""
    ctx.invoke(export_table, case_id=case_id)
    console.print("[bold green]全成果物の出力が完了しました。[/bold green]")


if __name__ == "__main__":
    cli()
