"""case.html を外部 CSS/JS 参照に書き換える。

split_case_html.py で static/ に抽出済み前提。
このスクリプトは case.html の
  - <style>...</style> ブロック全体 → <link ...>
  - <script>...2921行...</script> ブロック全体 → inline bootstrap + 外部 src
に置換する。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "templates" / "case.html"

text = SRC.read_text(encoding="utf-8")

style_block_re = re.compile(r"<style>\n.*?\n</style>\n", re.DOTALL)
style_replacement = (
    '<link rel="stylesheet" '
    'href="{{ url_for(\'static\', filename=\'css/case.css\') }}">\n'
)
if not style_block_re.search(text):
    raise SystemExit("[FAIL] <style> ブロックが見つかりません")
text, n_style = style_block_re.subn(style_replacement, text, count=1)
print(f"[OK] replaced {n_style} <style> block(s)")

script_block_re = re.compile(r"<script>\n.*?\n</script>\n", re.DOTALL)
bootstrap_and_external = """<script>
window.CASE_BOOTSTRAP = {
  case_id: {{ case_id | tojson }},
  has_hongan: {{ 'true' if hongan else 'false' }},
  has_segments: {{ 'true' if segments else 'false' }},
  has_keywords: {{ 'true' if keywords else 'false' }},
  has_citations: {{ 'true' if citations else 'false' }},
  segments: {{ segments | tojson if segments else '[]' }},
  keywords: {{ keywords | tojson if keywords else '[]' }},
  related_paragraphs: {{ related_paragraphs | tojson if related_paragraphs else '{}' }},
  cit_ids: [{% for cit in citations %}{{ cit.id | tojson }}{% if not loop.last %},{% endif %}{% endfor %}]
};
</script>
<script src="{{ url_for('static', filename='js/case.js') }}"></script>
"""
if not script_block_re.search(text):
    raise SystemExit("[FAIL] <script> ブロックが見つかりません")
text, n_script = script_block_re.subn(bootstrap_and_external, text, count=1)
print(f"[OK] replaced {n_script} <script> block(s)")

SRC.write_text(text, encoding="utf-8")
lines_after = text.count("\n")
print(f"\n[OK] wrote {SRC}")
print(f"   行数: {lines_after}")
print(f"   サイズ: {len(text.encode('utf-8')):,} bytes")
