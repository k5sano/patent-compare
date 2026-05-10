import json

import yaml

import web
from modules.pdf_annotation_meta import write_annotation_meta
from services import case_service


def test_view_citation_pdf_serves_registered_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    case_id = "2030-workspace"
    case_dir = tmp_path / "cases" / case_id
    input_dir = case_dir / "input"
    input_dir.mkdir(parents=True)
    (case_dir / "citations").mkdir()
    (case_dir / "output").mkdir()

    with (case_dir / "case.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            {
                "case_id": case_id,
                "title": "workspace",
                "field": "cosmetics",
                "citations": [{"id": "JP2030123456A", "label": "文献1", "role": "主引例"}],
            },
            f,
            allow_unicode=True,
        )

    (input_dir / "JP2030123456A.pdf").write_bytes(b"%PDF-1.3\n% test\n")

    client = web.app.test_client()
    resp = client.get(f"/case/{case_id}/citation/JP2030123456A/pdf")

    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert resp.data.startswith(b"%PDF-")


def test_view_hongan_pdf_prefers_annotated_bookmarked_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    case_id = "2030-hongan"
    case_dir = tmp_path / "cases" / case_id
    input_dir = case_dir / "input"
    output_dir = case_dir / "output"
    input_dir.mkdir(parents=True)
    output_dir.mkdir()

    with (case_dir / "case.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump({"case_id": case_id, "patent_number": "JP2030000001A"}, f)
    with (case_dir / "hongan.json").open("w", encoding="utf-8") as f:
        json.dump({"patent_number": "JP2030000001A", "source_pdf": "JP2030000001A.pdf"}, f)

    (input_dir / "JP2030000001A.pdf").write_bytes(b"%PDF-1.3\n% original hongan\n")
    (output_dir / f"{case_id}_本願_bookmarked.pdf").write_bytes(b"%PDF-1.3\n% annotated hongan\n")
    (output_dir / f"{case_id}_本願_bookmarked_20300102_030405_BU.pdf").write_bytes(
        b"%PDF-1.3\n% backup hongan\n"
    )

    client = web.app.test_client()
    resp = client.get(f"/case/{case_id}/hongan/pdf")

    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert b"annotated hongan" in resp.data
    assert b"original hongan" not in resp.data
    assert b"backup hongan" not in resp.data


def test_hongan_annotated_status_reports_missing_and_existing(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    case_id = "2030-hongan-status"
    case_dir = tmp_path / "cases" / case_id
    output_dir = case_dir / "output"
    output_dir.mkdir(parents=True)
    with (case_dir / "case.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump({"case_id": case_id}, f)

    client = web.app.test_client()
    missing = client.get(f"/case/{case_id}/hongan/annotated/status")
    assert missing.status_code == 200
    assert missing.get_json()["exists"] is False
    assert missing.get_json()["state"] == "missing"

    (output_dir / f"{case_id}_本願_bookmarked.pdf").write_bytes(b"%PDF-1.3\n")
    existing = client.get(f"/case/{case_id}/hongan/annotated/status")
    assert existing.status_code == 200
    assert existing.get_json()["exists"] is True
    assert existing.get_json()["filename"] == f"{case_id}_本願_bookmarked.pdf"
    assert existing.get_json()["state"] == "unknown"


def test_hongan_annotated_status_detects_stale_segments(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    case_id = "2030-hongan-stale"
    case_dir = tmp_path / "cases" / case_id
    output_dir = case_dir / "output"
    output_dir.mkdir(parents=True)
    (case_dir / "segments.json").write_text('[{"claim":1}]', encoding="utf-8")
    with (case_dir / "case.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump({"case_id": case_id}, f)
    pdf = output_dir / f"{case_id}_本願_bookmarked.pdf"
    pdf.write_bytes(b"%PDF-1.3\n")
    write_annotation_meta(pdf, case_id=case_id, kind="hongan", case_dir=case_dir)
    (case_dir / "segments.json").write_text('[{"claim":2}]', encoding="utf-8")

    client = web.app.test_client()
    resp = client.get(f"/case/{case_id}/hongan/annotated/status")

    assert resp.status_code == 200
    assert resp.get_json()["state"] == "stale"
    assert "segments" in resp.get_json()["reasons"]


def test_view_citation_pdf_prefers_annotated_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    case_id = "2030-workspace-annotated"
    case_dir = tmp_path / "cases" / case_id
    input_dir = case_dir / "input"
    output_dir = case_dir / "output"
    input_dir.mkdir(parents=True)
    output_dir.mkdir()

    with (case_dir / "case.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            {
                "case_id": case_id,
                "title": "workspace",
                "field": "cosmetics",
                "citations": [{"id": "JP2030123456A", "label": "文献1"}],
            },
            f,
            allow_unicode=True,
        )

    (input_dir / "JP2030123456A.pdf").write_bytes(b"%PDF-1.3\n% original\n")
    (output_dir / "JP2030123456A_annotated.pdf").write_bytes(b"%PDF-1.3\n% annotated\n")
    (output_dir / "JP2030123456A_annotated_20300102_030405_BU.pdf").write_bytes(
        b"%PDF-1.3\n% backup\n"
    )

    client = web.app.test_client()
    resp = client.get(f"/case/{case_id}/citation/JP2030123456A/pdf")

    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert b"annotated" in resp.data
    assert b"original" not in resp.data
    assert b"backup" not in resp.data


def test_citation_annotated_status_reports_create_state(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    case_id = "2030-citation-status"
    case_dir = tmp_path / "cases" / case_id
    output_dir = case_dir / "output"
    responses_dir = case_dir / "responses"
    output_dir.mkdir(parents=True)
    responses_dir.mkdir()
    with (case_dir / "case.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            {"case_id": case_id, "citations": [{"id": "JP2030123456A", "label": "文献1"}]},
            f,
            allow_unicode=True,
        )

    client = web.app.test_client()
    missing = client.get(f"/case/{case_id}/citation/JP2030123456A/annotated/status")
    assert missing.status_code == 200
    assert missing.get_json()["exists"] is False
    assert missing.get_json()["can_create"] is False

    (responses_dir / "JP2030123456A.json").write_text("{}", encoding="utf-8")
    creatable = client.get(f"/case/{case_id}/citation/JP2030123456A/annotated/status")
    assert creatable.status_code == 200
    assert creatable.get_json()["exists"] is False
    assert creatable.get_json()["can_create"] is True

    (output_dir / "JP2030123456A_annotated.pdf").write_bytes(b"%PDF-1.3\n")
    existing = client.get(f"/case/{case_id}/citation/JP2030123456A/annotated/status")
    assert existing.status_code == 200
    assert existing.get_json()["exists"] is True
    assert existing.get_json()["filename"] == "JP2030123456A_annotated.pdf"
    assert existing.get_json()["state"] == "unknown"


def test_citation_annotated_status_detects_stale_response(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    case_id = "2030-citation-stale"
    case_dir = tmp_path / "cases" / case_id
    output_dir = case_dir / "output"
    responses_dir = case_dir / "responses"
    citations_dir = case_dir / "citations"
    output_dir.mkdir(parents=True)
    responses_dir.mkdir()
    citations_dir.mkdir()
    with (case_dir / "case.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            {"case_id": case_id, "citations": [{"id": "JP2030123456A", "label": "文献1"}]},
            f,
            allow_unicode=True,
        )
    (case_dir / "segments.json").write_text("[]", encoding="utf-8")
    (responses_dir / "JP2030123456A.json").write_text('{"v":1}', encoding="utf-8")
    (citations_dir / "JP2030123456A.json").write_text("{}", encoding="utf-8")
    pdf = output_dir / "JP2030123456A_annotated.pdf"
    pdf.write_bytes(b"%PDF-1.3\n")
    write_annotation_meta(
        pdf,
        case_id=case_id,
        kind="citation",
        case_dir=case_dir,
        citation_id="JP2030123456A",
    )
    (responses_dir / "JP2030123456A.json").write_text('{"v":2}', encoding="utf-8")

    client = web.app.test_client()
    resp = client.get(f"/case/{case_id}/citation/JP2030123456A/annotated/status")

    assert resp.status_code == 200
    assert resp.get_json()["state"] == "stale"
    assert "response" in resp.get_json()["reasons"]


def test_citation_annotated_backups_are_listed_and_viewable(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    case_id = "2030-workspace-backups"
    case_dir = tmp_path / "cases" / case_id
    output_dir = case_dir / "output"
    output_dir.mkdir(parents=True)

    with (case_dir / "case.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            {
                "case_id": case_id,
                "title": "workspace",
                "field": "cosmetics",
                "citations": [{"id": "JP2030123456A", "label": "文献1"}],
            },
            f,
            allow_unicode=True,
        )

    filename = "JP2030123456A_annotated_20300102_030405_BU.pdf"
    (output_dir / filename).write_bytes(b"%PDF-1.3\n% backup\n")

    client = web.app.test_client()
    resp = client.get(f"/case/{case_id}/citation/JP2030123456A/annotated/backups")

    assert resp.status_code == 200
    assert resp.get_json()["backups"][0]["filename"] == filename

    pdf_resp = client.get(
        f"/case/{case_id}/citation/JP2030123456A/annotated/backup/{filename}"
    )

    assert pdf_resp.status_code == 200
    assert pdf_resp.mimetype == "application/pdf"
    assert b"backup" in pdf_resp.data


def test_view_citation_pdf_returns_404_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    case_id = "2030-workspace-missing"
    case_dir = tmp_path / "cases" / case_id
    (case_dir / "input").mkdir(parents=True)
    with (case_dir / "case.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump({"case_id": case_id, "citations": []}, f, allow_unicode=True)

    client = web.app.test_client()
    resp = client.get(f"/case/{case_id}/citation/UNKNOWN/pdf")

    assert resp.status_code == 404
    assert resp.get_json()["error"].startswith("引用文献PDFが見つかりません")


def test_case_detail_compact_workspace_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    case_id = "2030-compact"
    case_dir = tmp_path / "cases" / case_id
    case_dir.mkdir(parents=True)
    with (case_dir / "case.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            {"case_id": case_id, "title": "compact", "field": "cosmetics", "citations": []},
            f,
            allow_unicode=True,
        )

    client = web.app.test_client()
    resp = client.get(f"/case/{case_id}?compact=1&panel=2")

    assert resp.status_code == 200
    assert b'body class="compact-ref"' in resp.data
    assert b"compact_panel: 2" in resp.data


def test_hit_view_workspace_nav_uses_dropdown_order(tmp_path, monkeypatch):
    monkeypatch.setattr(case_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    case_id = "2030-hit-nav"
    case_dir = tmp_path / "cases" / case_id
    hit_dir = case_dir / "search_runs" / "_hit_text"
    hit_dir.mkdir(parents=True)
    with (case_dir / "case.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            {
                "case_id": case_id,
                "title": "hit nav",
                "field": "cosmetics",
                "citations": [
                    {"id": "JP2030000001A", "label": "文献1"},
                    {"id": "JP2030000002A", "label": "文献2"},
                    {"id": "JP2030000003A", "label": "文献3"},
                ],
            },
            f,
            allow_unicode=True,
        )
    with (hit_dir / "JP2030000002A.json").open("w", encoding="utf-8") as f:
        json.dump(
            {
                "patent_id": "JP2030000002A",
                "title": "middle",
                "source": "google",
                "abstract": "middle abstract",
                "claims": ["claim one"],
                "description": "【0001】middle description",
            },
            f,
            ensure_ascii=False,
        )

    client = web.app.test_client()
    resp = client.get(
        f"/case/{case_id}/search-run/hit/JP2030000002A/view"
        "?nav=workspace"
        "&nav_id=JP2030000001A"
        "&nav_id=JP2030000002A"
        "&nav_id=JP2030000003A"
    )
    html = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert '<span class="nav-pos">2 / 3</span>' in html
    assert 'id="nav-prev-doc" class=""' in html
    assert 'id="nav-next-doc" class=""' in html
    assert "/search-run/hit/JP2030000001A/view" in html
    assert "/search-run/hit/JP2030000003A/view" in html
