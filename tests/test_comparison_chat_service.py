from __future__ import annotations

import json
import importlib
from pathlib import Path

import yaml

from services import comparison_chat_service as svc


def _write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _make_case(root: Path):
    case = root / "cases" / "C1"
    (case / "citations").mkdir(parents=True)
    (case / "responses").mkdir()
    with open(case / "case.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump({
            "case_id": "C1",
            "citations": [{"id": "D1", "label": "D1公報", "role": "主引例"}],
        }, f, allow_unicode=True)
    _write(case / "segments.json", [{
        "claim_number": 1,
        "full_text": "C5-C10脂肪酸エステルを含む組成物。",
        "segments": [{"id": "1A", "text": "C5-C10脂肪酸エステルを含む"}],
    }, {
        "claim_number": 2,
        "is_independent": False,
        "dependencies": [1],
        "full_text": "前記脂肪酸エステルがステアリン酸グリセリルである請求項1の組成物。",
        "segments": [{"id": "2A", "text": "脂肪酸エステルがステアリン酸グリセリルである"}],
    }])
    _write(case / "citations" / "D1.json", {
        "label": "D1公報",
        "patent_title": "試験文献",
        "paragraphs": [
            {"id": "0130", "text": "前段落。"},
            {"id": "0131", "text": "ステアリン酸グリセリルを含有する。"},
            {"id": "0132", "text": "後段落。"},
        ],
    })
    _write(case / "responses" / "D1.json", {
        "document_id": "D1",
        "comparisons": [{
            "requirement_id": "1A",
            "judgment": "×",
            "judgment_reason": "エステルの記載がない。",
            "cited_location": "該当箇所なし",
            "cited_text": "該当記載なし",
        }],
        "sub_claims": [{
            "claim_number": 2,
            "judgment": "△",
            "judgment_reason": "ステアリン酸グリセリルの記載はあるが、従属項として未確認。",
            "cited_location": "0131",
            "cited_text": "ステアリン酸グリセリルを含有する。",
        }],
    })
    return case


def test_build_cell_context_extracts_paragraph_and_chem_hint(isolated_project_root):
    _make_case(isolated_project_root)

    ctx = svc.build_cell_context("C1", "D1", "1A", message="131段落にステアリン酸グリセリルあり")

    assert ctx["segment"]["text"] == "C5-C10脂肪酸エステルを含む"
    assert ctx["current_judgment"]["category"] == "×"
    assert [p["para_no"] for p in ctx["relevant_paragraphs"]] == ["0130", "0131", "0132"]
    hints = {h["term"]: h["hint"] for h in ctx["chemistry_hints"]}
    assert "ステアリン酸グリセリル" in hints


def test_build_cell_context_finds_comparative_example_and_table(isolated_project_root):
    case = _make_case(isolated_project_root)
    _write(case / "citations" / "D1.json", {
        "label": "D1公報",
        "patent_title": "シーラントフィルム",
        "paragraphs": [
            {"id": "0105", "text": "比較例7の説明。"},
            {
                "id": "0106",
                "text": "（比較 例 8）ヒートシール層用にブテン含有率19wt%のプロピレンーブテン共重合体100重量部に対し、エチレン含有率4wt%のブテンーエチレン共重合体を調製した。",
            },
            {"id": "0107", "text": "上記結果を表1、表2に示す。"},
        ],
        "tables": [{
            "caption_label": "表2",
            "page_num": 43,
            "headers": ["項目", "比較例8"],
            "rows": [
                {"cells": ["総厚み", "25μm"]},
                {"cells": ["ヒートシール層", "3μm"]},
            ],
        }],
    })

    ctx = svc.build_cell_context(
        "C1", "D1", "1A",
        message="比較例8と表2を見て。請求項6の融解熱量比は内在物性として進歩性なしでは？",
    )

    assert "0106" in [p["para_no"] for p in ctx["relevant_paragraphs"]]
    assert ctx["relevant_tables"][0]["label"] == "表2"
    assert "比較例8" in ctx["relevant_tables"][0]["text"]
    assert ctx["evidence_index"]["table_count"] == 1
    prompt = svc._build_prompt(ctx, "比較例8を踏まえて修正")
    assert "[関連表]" in prompt
    assert "内在的物性" in prompt


def test_chat_cell_saves_history(monkeypatch, isolated_project_root):
    _make_case(isolated_project_root)
    monkeypatch.setattr(svc, "call_claude", lambda prompt, timeout=300, model=None: "結論: 再検討余地あり")

    out, status = svc.chat_cell("C1", "D1", "1A", "131段落を見て", model="local-ai")

    assert status == 200
    assert out["reply"] == "結論: 再検討余地あり"
    assert [m["role"] for m in out["messages"]] == ["user", "assistant"]


def test_chat_cell_extracts_natural_override(monkeypatch, isolated_project_root):
    _make_case(isolated_project_root)

    def fake_call(prompt, timeout=300, model=None):
        return (
            "結論: △→○に修正するのが相当です。\n"
            "理由: 段落【0131】にはステアリン酸グリセリルが記載され、"
            "これは脂肪酸エステルに該当するため、構成要件を充足します。\n"
            "該当箇所: 【0131】\n"
            "引用本文: ステアリン酸グリセリルを含有する。"
        )

    monkeypatch.setattr(svc, "call_claude", fake_call)

    out, status = svc.chat_cell("C1", "D1", "1A", "これは○では？", model="sonnet")

    assert status == 200
    sug = out["suggested_override"]
    assert sug["apply"] is True
    assert sug["judgment_changed"] is True
    assert sug["to_judgment"] == "○"
    assert sug["fields"]["judgment"] == "○"
    assert "ステアリン酸グリセリル" in sug["fields"]["judgment_reason"]
    assert "【0131】" in sug["fields"]["cited_location"]


def test_sub_claim_chat_context_and_override(monkeypatch, isolated_project_root):
    case = _make_case(isolated_project_root)
    monkeypatch.setattr(svc, "call_claude", lambda prompt, timeout=300, model=None: (
        "結論: △→○に修正するのが相当です。\n"
        "理由: 請求項2の追加限定であるステアリン酸グリセリルは段落【0131】に記載されています。\n"
        "該当箇所: 【0131】\n"
        "引用本文: ステアリン酸グリセリルを含有する。"
    ))

    hist, status = svc.get_cell_chat_history("C1", "D1", "2", target_kind="sub_claim")
    assert status == 200
    assert hist["context"]["target_kind"] == "sub_claim"
    assert hist["context"]["segment"]["id"] == "請求項2"

    out, status = svc.chat_cell("C1", "D1", "2", "これは○では？", target_kind="sub_claim")
    assert status == 200
    assert out["suggested_override"]["to_judgment"] == "○"

    saved, status = svc.apply_judgment_override(
        "C1", "D1", "2",
        out["suggested_override"]["fields"],
        target_kind="sub_claim",
        user_note="従属項壁打ちで更新",
    )
    assert status == 200
    data = json.loads((case / "responses" / "D1.json").read_text(encoding="utf-8"))
    assert data["sub_claims"][0]["judgment"] == "○"
    assert data["overrides"]["sub_claim:2"]["target_kind"] == "sub_claim"


def test_chat_cell_requires_segment_id(isolated_project_root):
    _make_case(isolated_project_root)

    out, status = svc.chat_cell("C1", "D1", "", "131段落を見て")

    assert status == 400
    assert "segment_id" in out["error"]


def test_apply_judgment_override_preserves_original(isolated_project_root):
    case = _make_case(isolated_project_root)

    out, status = svc.apply_judgment_override(
        "C1", "D1", "1A",
        {"judgment": "△", "judgment_reason": "エステル構造はあるがC5-C10外。"},
        user_note="壁打ちで部分充足に変更",
    )

    assert status == 200
    data = json.loads((case / "responses" / "D1.json").read_text(encoding="utf-8"))
    comp = data["comparisons"][0]
    assert comp["judgment"] == "△"
    assert data["overrides"]["1A"]["original"]["judgment"] == "×"
    assert data["overrides"]["1A"]["user_note"] == "壁打ちで部分充足に変更"
    assert out["doc"]["comparisons"][0]["judgment_display"] == "△"


def test_list_unmet_cells(isolated_project_root):
    _make_case(isolated_project_root)

    out, status = svc.list_unmet_cells("C1")

    assert status == 200
    assert out["count"] == 2
    assert out["cells"][0]["citation_id"] == "D1"
    assert out["cells"][0]["segment_id"] == "1A"
    assert out["cells"][1]["target_kind"] == "sub_claim"
    assert out["cells"][1]["segment_id"] == "2"


def test_list_unmet_cells_filters_citations(isolated_project_root):
    case = _make_case(isolated_project_root)
    _write(case / "responses" / "D2.json", {
        "document_id": "D2",
        "comparisons": [{
            "requirement_id": "1A",
            "judgment": "×",
            "judgment_reason": "D2の未充足",
        }],
    })

    out, status = svc.list_unmet_cells("C1", citation_ids=["D2"])

    assert status == 200
    assert out["count"] == 1
    assert out["cells"][0]["citation_id"] == "D2"
    assert out["citation_ids"] == ["D2"]


def test_comparison_chat_routes(monkeypatch, isolated_project_root):
    _make_case(isolated_project_root)
    monkeypatch.setattr(svc, "call_claude", lambda prompt, timeout=300, model=None: "結論: ルート応答")

    import web

    importlib.reload(web)
    web.app.config["TESTING"] = True
    client = web.app.test_client()

    hist = client.get("/case/C1/comparison/D1/chat?segment_id=1A")
    assert hist.status_code == 200
    assert hist.get_json()["context"]["segment"]["id"] == "1A"

    posted = client.post(
        "/case/C1/comparison/D1/chat",
        json={"segment_id": "1A", "message": "131段落を確認して", "model": "local-ai"},
    )
    assert posted.status_code == 200
    assert posted.get_json()["reply"] == "結論: ルート応答"

    override = client.post(
        "/case/C1/comparison/D1/judgment/override",
        json={
            "segment_id": "1A",
            "fields": {"judgment": "△", "judgment_reason": "ルートから上書き。"},
            "user_note": "route test",
        },
    )
    assert override.status_code == 200
    assert override.get_json()["doc"]["comparisons"][0]["judgment"] == "△"

    unmet = client.get("/case/C1/comparison/unmet-cells")
    assert unmet.status_code == 200
    assert unmet.get_json()["count"] == 2
