# -*- coding: utf-8 -*-
"""
本願 / 引用文献 PDF に埋め込まれた表画像から成分・配合量等の構造化データを抽出する。

実装方式 = LLM CLI のサブプロセス呼び出し。
- Claude モデルは Claude Code CLI (`claude -p`) で呼ぶ
- Codex/GPT モデルは ChatGPT ログイン済みの Codex CLI (`codex exec`) で呼ぶ
- GLM は画像入力対象外
- Phase 0 検証時の実測: Sonnet 4.6 / 22 秒 / 約 $0.12 相当 (Max クォータ消費)

抽出対象は「成分名 / 配合量」を含む実施例表が中心だが、汎用的に「表っぽい画像」
すべてを試行する (表でない場合は LLM 側が is_table:false を返す)。
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Optional

import fitz


# ---------- 画像候補抽出 ----------

@dataclass
class ImageCandidate:
    page_num: int           # 1-indexed
    xref: int
    width: int
    height: int
    image_path: Path        # 抽出後 PNG の保存先
    caption: Optional[str] = None         # 画像近傍で見つけた最近接の関連テキスト (生)
    caption_label: Optional[str] = None   # "表1" / "Table 2" / "化1" 等の正規化済みラベル
    is_table_caption: bool = False        # 表系キャプション → OCR 対象
    is_figure_caption: bool = False       # 化学式/図系キャプション → スキップ推奨

    def to_dict(self):
        d = asdict(self)
        d["image_path"] = str(self.image_path)
        return d


def _is_likely_table_image(width: int, height: int) -> bool:
    """表候補のヒューリスティック。明らかに小さい / 細長い画像は除外する。

    閾値は J-PlatPat / Google Patents の典型的な表画像から決め打ち。
    判定ミスは LLM 側で `is_table: false` を返してもらい吸収。
    """
    if width < 200 or height < 100:
        return False
    if width > 6 * height or height > 6 * width:
        return False
    if width * height < 30_000:
        return False
    return True


# 表のキャプション (positive): 【表1】 / 表1 / Table 1 / 実施例の表 等
_TABLE_CAPTION_PAT = re.compile(
    r"(【\s*表\s*[0-9０-９]+(?:\s*[\-－―]\s*[0-9０-９]+)?\s*】"
    r"|表\s*[0-9０-９]+(?:\s*[\-－―]\s*[0-9０-９]+)?(?!\s*[にをはのとが])"
    r"|[Tt]able\s+[0-9]+)"
)
# 図/化学式のキャプション (negative): 【化1】【図1】Figure 1 等 → 表ではないので OCR スキップ
_FIGURE_CAPTION_PAT = re.compile(
    r"(【\s*(?:化|図)\s*[0-9０-９]+\s*】"
    r"|(?:^|\s)(?:Fig\.?|Figure)\s+[0-9]+)"
)


def _find_image_caption(page, image_rect, *, max_distance: float = 60.0):
    """画像 rect の上下近傍にあるテキストブロックから caption を見つけて返す。

    Returns:
        (caption_text, caption_label, is_table, is_figure) のタプル。
        いずれもマッチしなければ caption_text は近接テキストの 1 行 (デバッグ用)、
        is_table=is_figure=False。
    """
    blocks = page.get_text("blocks")
    above = []  # (distance, text)
    below = []
    for b in blocks:
        if len(b) < 5:
            continue
        x0, y0, x1, y1, text = b[0], b[1], b[2], b[3], b[4]
        text = (text or "").strip()
        if not text:
            continue
        if y1 < image_rect.y0 and (image_rect.y0 - y1) < max_distance:
            above.append((image_rect.y0 - y1, text))
        elif y0 > image_rect.y1 and (y0 - image_rect.y1) < max_distance:
            below.append((y0 - image_rect.y1, text))
    above.sort()
    below.sort()

    # 上を優先 (表のキャプションは上にあることが多い)
    for source in (above, below):
        for _dist, text in source:
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                tm = _TABLE_CAPTION_PAT.search(line)
                if tm:
                    return line, tm.group(0), True, False
                fm = _FIGURE_CAPTION_PAT.search(line)
                if fm:
                    return line, fm.group(0), False, True
    # マッチなし: 上の最も近い 1 行をデバッグ用に返す
    if above:
        return above[0][1].splitlines()[0][:80], None, False, False
    if below:
        return below[0][1].splitlines()[0][:80], None, False, False
    return None, None, False, False


def extract_image_candidates(pdf_path: Path, out_dir: Path) -> list[ImageCandidate]:
    """PDF から表候補画像を抽出して PNG として保存。各画像の近傍 caption も検出する。

    Args:
        pdf_path: 入力 PDF
        out_dir: 抽出 PNG の保存先 (未存在なら作成)

    Returns:
        ImageCandidate のリスト
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[ImageCandidate] = []
    seen_xrefs: set[int] = set()

    with fitz.open(str(pdf_path)) as doc:
        for pi in range(doc.page_count):
            page = doc[pi]
            for img in page.get_images(full=True):
                xref = img[0]
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                w, h = img[2], img[3]
                if not _is_likely_table_image(w, h):
                    continue
                try:
                    pix = fitz.Pixmap(doc, xref)
                    if pix.alpha or pix.colorspace.name != "DeviceRGB":
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    img_name = f"{pdf_path.stem}_p{pi+1}_x{xref}.png"
                    img_path = out_dir / img_name
                    pix.save(str(img_path))
                    pix = None
                except Exception:
                    continue
                # 画像のページ上の rect を取得して近傍 caption を探す
                caption_text = caption_label = None
                is_table = is_figure = False
                try:
                    rects = page.get_image_rects(xref)
                    if rects:
                        caption_text, caption_label, is_table, is_figure = (
                            _find_image_caption(page, rects[0])
                        )
                except Exception:
                    pass
                candidates.append(ImageCandidate(
                    page_num=pi + 1, xref=xref, width=w, height=h,
                    image_path=img_path,
                    caption=caption_text, caption_label=caption_label,
                    is_table_caption=is_table, is_figure_caption=is_figure,
                ))
    return candidates


def find_table_references_in_text(pdf_path: Path) -> list[str]:
    """本文テキストから 表N / Table N / 実施例N 等の参照を抽出 (重複あり)。

    Phase 1 で「期待される表数」と「実際に検出した caption 数」を照合する用。
    """
    pat = re.compile(
        r"(【\s*表\s*[0-9０-９]+\s*】|表\s*[0-9０-９]+|"
        r"[Tt]able\s+[0-9]+|【\s*実施例\s*[0-9０-９]+\s*】|"
        r"実施例\s*[0-9０-９]+|[Ee]xample\s+[0-9]+)"
    )
    refs = []
    with fitz.open(str(pdf_path)) as doc:
        for pi in range(doc.page_count):
            txt = doc[pi].get_text() or ""
            for m in pat.finditer(txt):
                refs.append(m.group(0))
    return refs


# ---------- LLM 呼び出し ----------

# Phase 0 で実測: Sonnet が日本語化学名・全角数字に十分な精度。Haiku は誤読多発で不可。
DEFAULT_MODEL = "sonnet"

# プロンプトは表のセル構造を素直な JSON で返させる。実施例表のような「成分×実施例」
# レイアウトもこの構造で表現できる (headers が ["成分", "実施例1", "実施例2", ...]、
# 各 row.cells が ["ヘパリン類似物質", "0.3", "0.5", ...] のように)。
_EXTRACT_PROMPT_TEMPLATE = """画像 `{image_path}` を Read ツールで読み取り、表の内容を以下の JSON スキーマで出力してください。応答は **コードブロックで囲まれた JSON のみ**、解説や前置きは不要。表でない場合は `{{"is_table": false}}` のみを返してください。
{caption_hint}
スキーマ:
{{
  "is_table": true,
  "title": "表のタイトル / 見出し (なければ null)",
  "headers": ["列1", "列2", ...],
  "rows": [
    {{"cells": ["値1", "値2", ...]}}
  ]
}}

注意:
- セル結合 (rowspan / colspan) は値を複製して各セルを埋める
- **複数行のヘッダ**（例: 上段「実施例 / 比較例」、下段「1, 2, 3, ...」）は連結して 1 行にまとめる
  例: ヘッダ上段に "実施例" がコル 1〜9 を覆い、下段が "1 2 3 ... 9"、上段に "比較例" がコル 10〜13 を覆い、下段が "1 2 3 4" の場合
  → headers は ["実施例1","実施例2",...,"実施例9","比較例1","比較例2","比較例3","比較例4"]
- 全角数字・記号・括弧はそのまま保持 (０．３％ → "０．３％" のまま)
- 化合物名・成分名・商品名は表記そのまま忠実に
- セル内改行は半角スペースで連結
- 単位行 (「(質量%)」等) があれば title に含める
- **JSON は厳密に出力**: trailing comma 禁止、コメント禁止
"""


@dataclass
class ExtractedTable:
    is_table: bool = False
    page_num: int = 0
    image_path: str = ""
    title: Optional[str] = None
    headers: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    # メタ情報
    duration_ms: int = 0
    cost_usd_equivalent: float = 0.0
    model: str = ""
    error: Optional[str] = None


def _parse_claude_result(raw_stdout: str) -> tuple[Optional[dict], dict]:
    """`claude -p --output-format json` の stdout から (extracted_payload, meta) を返す。

    meta に duration_ms / total_cost_usd / model / num_turns 等を入れる。
    extracted_payload は LLM が返した本文 JSON (`{"is_table": ..., ...}`) を parse したもの。
    パースに失敗すれば None。
    """
    lines = [l for l in raw_stdout.splitlines() if l.strip()]
    if not lines:
        return None, {"error": "empty stdout"}

    # 最後の行が --output-format json の最終 result オブジェクト
    try:
        envelope = json.loads(lines[-1])
    except json.JSONDecodeError as e:
        return None, {"error": f"envelope parse error: {e}"}

    # API 呼び出しでは Claude CLI の envelope ではなく、表JSON本体だけが
    # 返ることがある。
    if isinstance(envelope, dict) and "is_table" in envelope:
        return envelope, {}

    meta = {
        "duration_ms": envelope.get("duration_ms"),
        "total_cost_usd": envelope.get("total_cost_usd"),
        "num_turns": envelope.get("num_turns"),
        "is_error": envelope.get("is_error"),
        "model_usage": envelope.get("modelUsage"),
        "session_id": envelope.get("session_id"),
    }
    if envelope.get("is_error"):
        meta["error"] = envelope.get("result") or "claude returned is_error"
        return None, meta

    result_text = envelope.get("result", "") or ""
    # ```json ... ``` で囲まれている前提だが、素のオブジェクトもケア
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", result_text, re.DOTALL)
    if m:
        body = m.group(1)
    else:
        m2 = re.search(r"(\{.*\})", result_text, re.DOTALL)
        body = m2.group(1) if m2 else None
    if not body:
        meta["error"] = "no JSON object in result"
        return None, meta
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        # LLM が稀に trailing comma 付きで返すので strip して再試行
        body_clean = re.sub(r",(\s*[\]\}])", r"\1", body)
        try:
            payload = json.loads(body_clean)
        except json.JSONDecodeError as e2:
            meta["error"] = f"body parse error: {e2}"
            return None, meta
    return payload, meta


def extract_table_via_claude(image_path: Path, *, model: str = DEFAULT_MODEL,
                              timeout: int = 600,
                              caption_hint: Optional[str] = None,
                              effort: str = "low") -> ExtractedTable:
    """1 枚の画像を LLM に投げて表内容を JSON 抽出する。

    Args:
        image_path: 抽出対象画像 (PNG/JPG)。絶対パス推奨 (Read ツールが解決する)
        model: モデルエイリアス (sonnet/codex-sonnet等) または完全モデル名
        timeout: subprocess の timeout (秒)
        caption_hint: PDF 本文から検出した画像近傍のキャプション (例: "【表１】")。
                      LLM のコンテキストとして与えると title 推定の精度・速度が上がる。

    Returns:
        ExtractedTable。失敗時は is_table=False, error 設定
    """
    image_path = Path(image_path).resolve()
    if caption_hint:
        cap_block = f"\nこの画像は PDF 本文中で「{caption_hint}」として参照されています。title フィールドにはこの参照ラベルを含めてください。\n"
    else:
        cap_block = ""
    prompt = _EXTRACT_PROMPT_TEMPLATE.format(
        image_path=str(image_path), caption_hint=cap_block,
    )

    from modules.claude_client import (
        ClaudeClientError,
        call_llm_with_image,
        model_provider,
    )
    provider = model_provider(model)
    if provider == "codex":
        api_prompt = prompt.replace(
            f"画像 `{image_path}` を Read ツールで読み取り",
            "添付画像を読み取り",
        )
        t0 = time.monotonic()
        try:
            raw = call_llm_with_image(
                api_prompt, image_path, timeout=timeout,
                model=model, effort=effort,
            )
        except ClaudeClientError as e:
            return ExtractedTable(
                is_table=False, image_path=str(image_path),
                duration_ms=int((time.monotonic() - t0) * 1000),
                model=model, error=str(e),
            )
        payload, meta = _parse_claude_result(raw)
        elapsed_ms = meta.get("duration_ms") or int((time.monotonic() - t0) * 1000)
        if payload is None:
            return ExtractedTable(
                is_table=False, image_path=str(image_path),
                duration_ms=elapsed_ms, model=model,
                error=meta.get("error") or "parse failure",
            )
        return ExtractedTable(
            is_table=bool(payload.get("is_table")),
            image_path=str(image_path),
            title=payload.get("title"),
            headers=list(payload.get("headers") or []),
            rows=list(payload.get("rows") or []),
            duration_ms=elapsed_ms,
            cost_usd_equivalent=0.0,
            model=model,
        )
    if provider != "claude":
        return ExtractedTable(
            is_table=False, image_path=str(image_path), model=model,
            error=f"{model} は画像入力による表抽出に未対応です",
        )

    cmd = [
        "claude", "-p", prompt,
        "--model", model,
        "--output-format", "json",
        "--allowedTools", "Read",
        "--effort", effort,
    ]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ExtractedTable(
            is_table=False, image_path=str(image_path),
            duration_ms=int((time.monotonic() - t0) * 1000),
            model=model, error=f"timeout after {timeout}s",
        )
    raw = proc.stdout.decode("utf-8", errors="replace")
    payload, meta = _parse_claude_result(raw)

    elapsed_ms = meta.get("duration_ms") or int((time.monotonic() - t0) * 1000)
    cost = meta.get("total_cost_usd") or 0.0
    err = meta.get("error")

    if payload is None:
        return ExtractedTable(
            is_table=False, image_path=str(image_path),
            duration_ms=elapsed_ms, cost_usd_equivalent=cost,
            model=model, error=err or "parse failure",
        )
    return ExtractedTable(
        is_table=bool(payload.get("is_table")),
        image_path=str(image_path),
        title=payload.get("title"),
        headers=list(payload.get("headers") or []),
        rows=list(payload.get("rows") or []),
        duration_ms=elapsed_ms,
        cost_usd_equivalent=cost,
        model=model,
    )


# ---------- オーケストレーション ----------

def extract_tables_from_pdf(pdf_path: Path, out_dir: Path, *,
                             model: str = DEFAULT_MODEL,
                             max_images: Optional[int] = None,
                             include_uncaptioned: bool = False,
                             effort: str = "low",
                             progress=None) -> dict:
    """1 つの PDF から表候補画像を抽出して、各画像を claude に投げて JSON 化する。

    キャプション (【表N】等) が見つかった画像のみ Claude に投げるのがデフォルト。
    figure キャプション (【化N】【図N】) は表ではないので明示的にスキップ。

    Args:
        pdf_path: 入力 PDF
        out_dir: 抽出 PNG / 結果 JSON の保存先 (`{out_dir}/images/`, `{out_dir}/tables.json`)
        model: claude --model に渡すエイリアス
        max_images: デバッグ用に最初の N 枚だけ処理 (フィルタ後の枚数で数える)
        include_uncaptioned: True の場合、表キャプションが見つからない画像も Claude に投げて
                              判定させる (Phase 0 の挙動)。デフォルト False で確実に表のみ。
        progress: callable(stage, current, total, info) — 進捗通知用

    Returns:
        {"doc_id": str, "tables": [...], "candidates_total": int, ...}
    """
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    img_dir = out_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)

    if progress:
        progress("scan", 0, 0, str(pdf_path.name))
    all_candidates = extract_image_candidates(pdf_path, img_dir)
    body_refs = find_table_references_in_text(pdf_path)

    # 表キャプション付き画像のみ抽出対象。figure キャプション (化/図) は明示スキップ。
    if include_uncaptioned:
        targets = [c for c in all_candidates if not c.is_figure_caption]
    else:
        targets = [c for c in all_candidates if c.is_table_caption]
    skipped = [c for c in all_candidates if c not in targets]

    if max_images is not None:
        targets = targets[:max_images]
    total = len(targets)

    tables = []
    total_cost = 0.0
    total_duration = 0
    n_table = 0
    n_nontable = 0
    n_error = 0

    for i, cand in enumerate(targets, 1):
        if progress:
            progress("extract", i, total,
                     f"{cand.image_path.name} cap={cand.caption_label or '-'}")
        et = extract_table_via_claude(
            cand.image_path, model=model,
            caption_hint=cand.caption_label,
            effort=effort,
        )
        total_cost += et.cost_usd_equivalent or 0.0
        total_duration += et.duration_ms or 0
        rec = asdict(et)
        rec.update({
            "page_num": cand.page_num,
            "image_xref": cand.xref,
            "image_width": cand.width,
            "image_height": cand.height,
            "caption": cand.caption,
            "caption_label": cand.caption_label,
        })
        tables.append(rec)
        if et.error:
            n_error += 1
        elif et.is_table:
            n_table += 1
        else:
            n_nontable += 1

    summary = {
        "doc_id": pdf_path.stem,
        "pdf_path": str(pdf_path),
        "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": model,
        "candidates_total": len(all_candidates),
        "candidates_targeted": len(targets),
        "candidates_skipped": len(skipped),
        "skipped_reasons": [
            {
                "page": c.page_num, "xref": c.xref,
                "caption": c.caption, "caption_label": c.caption_label,
                "reason": ("figure_caption" if c.is_figure_caption
                           else "no_table_caption"),
            }
            for c in skipped
        ],
        "body_table_references": body_refs,
        "n_table": n_table,
        "n_nontable": n_nontable,
        "n_error": n_error,
        "total_duration_ms": total_duration,
        "total_cost_usd_equivalent": round(total_cost, 4),
        "tables": tables,
    }
    out_json = out_dir / "tables.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    summary["output_json"] = str(out_json)
    return summary


# ---------- 画像 records ベースの抽出 (PDF を持たない引用文献向け) ----------

def _normalize_caption_for_filter(label: str) -> tuple[bool, bool]:
    """画像 record の label / context から (is_table, is_figure) を判定する。"""
    if not label:
        return False, False
    if _TABLE_CAPTION_PAT.search(label):
        return True, False
    if _FIGURE_CAPTION_PAT.search(label):
        return False, True
    return False, False


def extract_tables_from_image_records(
    image_records: list[dict], out_dir: Path, doc_id: str, *,
    model: str = DEFAULT_MODEL, effort: str = "low",
    max_images: Optional[int] = None,
    include_uncaptioned: bool = False,
    progress=None,
) -> dict:
    """Google Patents 等から取得済みの image records から表抽出する。

    PDF を持たない (まだ DL されていない) 引用文献向け。

    Args:
        image_records: [{"src": "https://...", "label": "表1", "context": "...",
                         "width": ..., "height": ...}, ...]
                       (modules.google_patents_scraper.fetch_patent_full_text の result["images"])
        out_dir: 結果保存先 (ここに images/ と tables.json が出来る)
        doc_id: 出力ファイル名のベース (patent_id 等)
    """
    import urllib.request
    out_dir = Path(out_dir)
    img_dir = out_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    # doc_id をファイル名安全に正規化 ("再表2012/029514" の "/" 等を _ に)
    safe_doc_id = re.sub(r'[\\/:*?"<>|\s]', "_", doc_id) or "doc"

    if progress:
        progress("scan", 0, 0, f"{doc_id}: {len(image_records)} 画像候補")

    # キャプションフィルタを適用 (label or context をチェック)
    candidates: list[dict] = []
    skipped: list[dict] = []
    for i, rec in enumerate(image_records):
        label = rec.get("label") or ""
        context = rec.get("context") or ""
        is_table, is_figure = _normalize_caption_for_filter(label)
        if not (is_table or is_figure):
            # label が空: context からも判定試みる
            is_table, is_figure = _normalize_caption_for_filter(context)
        target = include_uncaptioned or is_table
        cand = {
            "index": i, "src": rec.get("src"),
            "label": label, "context": context,
            "width": rec.get("width", 0), "height": rec.get("height", 0),
            "is_table_caption": is_table, "is_figure_caption": is_figure,
        }
        if target and not is_figure:
            candidates.append(cand)
        else:
            cand["skip_reason"] = "figure_caption" if is_figure else "no_table_caption"
            skipped.append(cand)

    if max_images is not None:
        candidates = candidates[:max_images]

    tables = []
    total_cost = 0.0
    total_duration = 0
    n_table = n_nontable = n_error = 0

    for i, cand in enumerate(candidates, 1):
        url = cand.get("src")
        if not url:
            continue
        # ダウンロード → 一時 PNG (パス安全な doc_id を使う)
        img_name = f"{safe_doc_id}_img{cand['index']:03d}.png"
        img_path = img_dir / img_name
        if not img_path.exists():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    img_path.write_bytes(r.read())
            except Exception as e:
                tables.append({
                    "index": cand["index"], "src": url,
                    "is_table": False, "error": f"image download failed: {e}",
                    "label": cand["label"], "caption": cand["label"] or cand["context"],
                })
                n_error += 1
                continue
        if progress:
            progress("extract", i, len(candidates),
                     f"{img_name} cap={cand['label'] or '-'}")
        et = extract_table_via_claude(
            img_path, model=model, effort=effort,
            caption_hint=cand["label"] or None,
        )
        rec_out = asdict(et)
        rec_out.update({
            "index": cand["index"],
            "src": url,
            "image_width": cand["width"],
            "image_height": cand["height"],
            "caption": cand["label"] or cand["context"],
            "caption_label": cand["label"],
        })
        tables.append(rec_out)
        total_cost += et.cost_usd_equivalent or 0.0
        total_duration += et.duration_ms or 0
        if et.error:
            n_error += 1
        elif et.is_table:
            n_table += 1
        else:
            n_nontable += 1

    summary = {
        "doc_id": doc_id,
        "source_kind": "image_records",
        "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": model,
        "candidates_total": len(image_records),
        "candidates_targeted": len(candidates),
        "candidates_skipped": len(skipped),
        "skipped_reasons": [
            {"index": s["index"], "src": s["src"],
             "label": s["label"], "reason": s["skip_reason"]}
            for s in skipped
        ],
        "n_table": n_table,
        "n_nontable": n_nontable,
        "n_error": n_error,
        "total_duration_ms": total_duration,
        "total_cost_usd_equivalent": round(total_cost, 4),
        "tables": tables,
    }
    out_json = out_dir / "tables.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    summary["output_json"] = str(out_json)
    return summary
