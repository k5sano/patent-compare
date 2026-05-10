"""comparison_service の軽量スモークテスト。"""
from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from services.comparison_service import export_excel, generate_prompt_single


def test_generate_prompt_single_contains_required_sections(copy_case_fixture):
    case_dir = copy_case_fixture("smoke")

    result, code = generate_prompt_single("smoke", "JP2030000002A")

    assert code == 200
    assert result["char_count"] > 1000
    prompt = result["prompt"]
    assert "## タスク" in prompt
    assert "## 本願の請求項 構成要件" in prompt
    assert "JP2030000002A" in prompt
    assert (case_dir / "prompts" / "JP2030000002A_prompt.txt").exists()


def test_export_excel_writes_workbook(copy_case_fixture):
    case_dir = copy_case_fixture("smoke")

    result, code = export_excel("smoke")

    assert code == 200
    assert result["success"] is True
    out = Path(result["path"])
    assert out.exists()
    assert out.parent == case_dir / "output"
    wb = load_workbook(out)
    assert "対比表" in wb.sheetnames
    ws = wb["対比表"]
    values = [cell.value for row in ws.iter_rows(max_row=12, max_col=4) for cell in row]
    assert "JP2030000002A" in " ".join(str(v or "") for v in values)
