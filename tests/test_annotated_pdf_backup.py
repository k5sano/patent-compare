import re

from services.comparison_service import _backup_existing_annotated_pdf


def test_backup_existing_annotated_pdf_uses_date_bu_name(tmp_path):
    target = tmp_path / "JP2030123456A_annotated.pdf"
    target.write_bytes(b"%PDF-1.3\n% current\n")

    backup = _backup_existing_annotated_pdf(target)

    assert backup is not None
    assert backup.exists()
    assert backup.read_bytes() == target.read_bytes()
    assert re.match(r"JP2030123456A_annotated_\d{8}_\d{6}_BU\.pdf", backup.name)
