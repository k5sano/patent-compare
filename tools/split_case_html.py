"""case.html から CSS / JS を static/ に抽出する一度限りのスクリプト。

実行後の成果物:
  - static/css/case.css        (案件画面のスタイル)
  - static/js/case.js          (案件画面のロジック; window.CASE_BOOTSTRAP を参照)

case.html 本体は別途手動で <style>/<script> タグを外部参照 + 小さな
bootstrap ブロックに書き換える (split_case_html.py では書き換えない)。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "templates" / "case.html"
CSS_OUT = ROOT / "static" / "css" / "case.css"
JS_OUT = ROOT / "static" / "js" / "case.js"

lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)


def slice_lines(start_exclusive: int, end_exclusive: int) -> str:
    # start_exclusive, end_exclusive は 1-indexed の行番号
    # タグ自身は含めない（start+1 から end-1 まで）
    return "".join(lines[start_exclusive:end_exclusive - 1])


def find_line(pattern: str, start: int = 0) -> int:
    # 1-indexed を返す
    rx = re.compile(pattern)
    for i in range(start, len(lines)):
        if rx.search(lines[i]):
            return i + 1
    raise ValueError(f"pattern not found: {pattern}")


style_open = find_line(r"^<style>\s*$")
style_close = find_line(r"^</style>\s*$", style_open)
script_open = find_line(r"^<script>\s*$", style_close)
script_close = find_line(r"^</script>\s*$", script_open)

print(f"<style>  : {style_open} - {style_close}  ({style_close - style_open + 1} lines)")
print(f"<script> : {script_open} - {script_close}  ({script_close - script_open + 1} lines)")

css_body = slice_lines(style_open, style_close)
CSS_OUT.write_text(css_body, encoding="utf-8")
print(f"[OK] wrote {CSS_OUT} ({len(css_body):,} bytes)")

js_body = slice_lines(script_open, script_close)

# ===== Jinja -> window.CASE_BOOTSTRAP 置換 =====
BOOTSTRAP = "window.CASE_BOOTSTRAP"

js_replacements: list[tuple[str, str]] = [
    # L994: CASE_ID
    (
        'const CASE_ID = "{{ case_id }}";',
        f'const CASE_ID = {BOOTSTRAP}.case_id;',
    ),
    # L1071: hasHongan (1 回目)
    (
        "    const hasHongan = {{ 'true' if hongan else 'false' }};",
        f'    const hasHongan = {BOOTSTRAP}.has_hongan;',
    ),
    # L1109: hasHongan (2 回目 / 先頭スペースなし)
    (
        "  const hasHongan = {{ 'true' if hongan else 'false' }};",
        f'  const hasHongan = {BOOTSTRAP}.has_hongan;',
    ),
    # L1898: origData
    (
        "    const origData = {{ segments|tojson if segments else '[]' }};",
        f'    const origData = {BOOTSTRAP}.segments || [];',
    ),
    # L1925: INITIAL_RELATED
    (
        "const INITIAL_RELATED = {{ related_paragraphs | tojson if related_paragraphs else '{}' }};",
        f'const INITIAL_RELATED = {BOOTSTRAP}.related_paragraphs || {{}};',
    ),
    # L2002: kwGroups
    (
        "let kwGroups = {{ keywords | tojson if keywords else '[]' }};",
        f'let kwGroups = {BOOTSTRAP}.keywords || [];',
    ),
]

for old, new in js_replacements:
    if old not in js_body:
        raise SystemExit(f"[FAIL] 置換対象が見つかりません:\n  {old!r}")
    js_body = js_body.replace(old, new, 1)

# ===== L1045-1049 初期表示パネル =====
initial_panel_block = (
    "// 初期表示: 最初の未完了ステップ\n"
    "{% if not hongan %}showPanel(0);\n"
    "{% elif not segments %}showPanel(1);\n"
    "{% elif not keywords %}showPanel(2);\n"
    "{% elif not citations %}showPanel(3);\n"
    "{% else %}showPanel(4);{% endif %}"
)
initial_panel_replacement = (
    "// 初期表示: 最初の未完了ステップ\n"
    "(function() {\n"
    f"  const b = {BOOTSTRAP};\n"
    "  if (!b.has_hongan) showPanel(0);\n"
    "  else if (!b.has_segments) showPanel(1);\n"
    "  else if (!b.has_keywords) showPanel(2);\n"
    "  else if (!b.has_citations) showPanel(3);\n"
    "  else showPanel(4);\n"
    "})();"
)
if initial_panel_block not in js_body:
    raise SystemExit("[FAIL] 初期表示パネル ブロックが見つかりません")
js_body = js_body.replace(initial_panel_block, initial_panel_replacement, 1)

# ===== L2015-2021 SEG_DATA 投入 =====
seg_data_block_pattern = re.compile(
    r"\{% if segments %\}\n"
    r"\{% for claim in segments %\}\n"
    r"\{% for seg in claim\.segments %\}\n"
    r"SEG_DATA\[[^\n]+\n"
    r"\{% endfor %\}\n"
    r"\{% endfor %\}\n"
    r"\{% endif %\}"
)
seg_data_replacement = (
    f"(function() {{\n"
    f"  const segs = {BOOTSTRAP}.segments || [];\n"
    f"  segs.forEach(claim => {{\n"
    f"    (claim.segments || []).forEach(seg => {{\n"
    f"      SEG_DATA[seg.id] = {{\n"
    f"        text: seg.text,\n"
    f"        claim: claim.claim_number,\n"
    f"        isIndep: !!claim.is_independent,\n"
    f"      }};\n"
    f"    }});\n"
    f"  }});\n"
    f"}})();"
)
if not seg_data_block_pattern.search(js_body):
    raise SystemExit("[FAIL] SEG_DATA ブロックが見つかりません")
js_body = seg_data_block_pattern.sub(seg_data_replacement, js_body, count=1)

# ===== L2831 citIds =====
cit_ids_re = re.compile(
    r'(?P<indent> *)const citIds = \[\{% for cit in citations %\}"\{\{ cit\.id \}\}",\{% endfor %\}\];'
)
cit_ids_matches = cit_ids_re.findall(js_body)
if not cit_ids_matches:
    raise SystemExit("[FAIL] citIds 行が見つかりません")
js_body = cit_ids_re.sub(
    lambda m: f"{m.group('indent')}const citIds = {BOOTSTRAP}.cit_ids || [];",
    js_body,
)
print(f"[OK] replaced {len(cit_ids_matches)} citIds occurrence(s)")

# Jinja 痕跡が残っていないか最終検査
leftover = re.findall(r"\{\{[^}]*\}\}|\{%[^%]*%\}", js_body)
if leftover:
    print("[WARN] 残存 Jinja 構文:")
    for item in leftover:
        print(f"  {item}")
    raise SystemExit(1)

JS_OUT.write_text(js_body, encoding="utf-8")
print(f"[OK] wrote {JS_OUT} ({len(js_body):,} bytes)")
print("\nすべての Jinja 構文を CASE_BOOTSTRAP 参照に置換しました。")
