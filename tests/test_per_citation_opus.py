import json
import re

import yaml

from services.comparison import execute


def _add_second_citation(case_dir):
    src = case_dir / "citations" / "JP2030000002A.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    data["patent_number"] = "JP2030000003A"
    data["patent_title"] = "引用テスト組成物2"
    data["paragraphs"] = [
        {"id": "0020", "text": "別の実施例の組成物は成分Aを含む。"},
        {"id": "0021", "text": "別の実施例の組成物は成分Bを含む。"},
    ]
    (case_dir / "citations" / "JP2030000003A.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    meta_path = case_dir / "case.yaml"
    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    meta.setdefault("citations", []).append(
        {"id": "JP2030000003A", "role": "副引例", "label": "JP2030000003A"}
    )
    meta_path.write_text(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _add_extra_citations(case_dir, doc_ids):
    src = case_dir / "citations" / "JP2030000002A.json"
    base = json.loads(src.read_text(encoding="utf-8"))
    meta_path = case_dir / "case.yaml"
    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    for doc_id in doc_ids:
        data = dict(base)
        data["patent_number"] = doc_id
        data["patent_title"] = f"引用テスト組成物 {doc_id}"
        data["paragraphs"] = [
            {"id": "0030", "text": f"{doc_id} の組成物は成分Aを含む。"},
            {"id": "0031", "text": f"{doc_id} の組成物は成分Bを含む。"},
        ]
        (case_dir / "citations" / f"{doc_id}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        meta.setdefault("citations", []).append(
            {"id": doc_id, "role": "副引例", "label": doc_id}
        )
    meta_path.write_text(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _comparison_response(doc_id):
    return json.dumps(
        {
            "document_id": doc_id,
            "comparisons": [
                {
                    "requirement_id": "1A",
                    "judgment": "○",
                    "cited_location": "0010",
                    "judgment_reason": "成分Aが記載されている。",
                },
                {
                    "requirement_id": "1B",
                    "judgment": "○",
                    "cited_location": "0011",
                    "judgment_reason": "成分Bが記載されている。",
                },
            ],
            "overall_summary": "近い。",
            "category_suggestion": "X",
        },
        ensure_ascii=False,
    )


def test_compare_per_citation_env_enables_opus_serial(copy_case_fixture, monkeypatch):
    case_dir = copy_case_fixture("smoke")
    _add_second_citation(case_dir)
    monkeypatch.delenv("COMPARE_MODE", raising=False)
    monkeypatch.setenv("COMPARE_PER_CITATION", "1")
    monkeypatch.delenv("COMPARE_PARALLEL", raising=False)

    calls = []

    def fake_call(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        doc_id = "JP2030000003A" if "JP2030000003A" in prompt else "JP2030000002A"
        return _comparison_response(doc_id)

    monkeypatch.setattr("modules.claude_client.call_claude", fake_call)

    result, status = execute.compare_execute(
        "smoke",
        ["JP2030000002A", "JP2030000003A"],
        model="opus",
    )

    assert status == 200
    assert result["execution_mode"] == "per_citation"
    assert result["parallel"] == 1
    assert result["mode_used"] == "requirement_first"
    assert set(result["saved_docs"]) == {"JP2030000002A", "JP2030000003A"}
    assert len(calls) == 2
    assert all(call["model"] == "opus" for call in calls)
    assert all(call["timeout"] == 600 for call in calls)


def test_compare_opus_without_env_keeps_integrated_mode(copy_case_fixture, monkeypatch):
    case_dir = copy_case_fixture("smoke")
    _add_second_citation(case_dir)
    monkeypatch.delenv("COMPARE_MODE", raising=False)
    monkeypatch.delenv("COMPARE_PER_CITATION", raising=False)
    monkeypatch.delenv("COMPARE_PARALLEL", raising=False)

    calls = []

    def fake_call(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        return json.dumps(
            {
                "results": [
                    json.loads(_comparison_response("JP2030000002A")),
                    json.loads(_comparison_response("JP2030000003A")),
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("modules.claude_client.call_claude", fake_call)

    result, status = execute.compare_execute(
        "smoke",
        ["JP2030000002A", "JP2030000003A"],
        model="opus",
    )

    assert status == 200
    assert result["execution_mode"] == "integrated"
    assert set(result["saved_docs"]) == {"JP2030000002A", "JP2030000003A"}
    assert len(calls) == 1
    assert calls[0]["model"] == "opus"


def test_compare_parallel_lightweight_still_uses_per_citation(copy_case_fixture, monkeypatch):
    case_dir = copy_case_fixture("smoke")
    _add_second_citation(case_dir)
    monkeypatch.delenv("COMPARE_MODE", raising=False)
    monkeypatch.delenv("COMPARE_PER_CITATION", raising=False)
    monkeypatch.setenv("COMPARE_PARALLEL", "9")

    calls = []

    def fake_call(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        doc_id = "JP2030000003A" if "JP2030000003A" in prompt else "JP2030000002A"
        return _comparison_response(doc_id)

    monkeypatch.setattr("modules.claude_client.call_claude", fake_call)

    result, status = execute.compare_execute(
        "smoke",
        ["JP2030000002A", "JP2030000003A"],
        model="sonnet",
    )

    assert status == 200
    assert result["execution_mode"] == "per_citation"
    assert result["parallel"] == 2
    assert set(result["saved_docs"]) == {"JP2030000002A", "JP2030000003A"}
    assert len(calls) == 2


def test_compare_per_citation_opus_five_docs_has_no_global_timeout(
    copy_case_fixture, monkeypatch
):
    import concurrent.futures as futures_mod

    case_dir = copy_case_fixture("smoke")
    _add_extra_citations(
        case_dir,
        ["JP2030000003A", "JP2030000004A", "JP2030000005A", "JP2030000006A"],
    )
    citation_ids = [
        "JP2030000002A",
        "JP2030000003A",
        "JP2030000004A",
        "JP2030000005A",
        "JP2030000006A",
    ]
    monkeypatch.delenv("COMPARE_MODE", raising=False)
    monkeypatch.setenv("COMPARE_PER_CITATION", "1")
    monkeypatch.delenv("COMPARE_PARALLEL", raising=False)

    original_as_completed = futures_mod.as_completed
    seen = {}

    def fake_as_completed(fs, timeout=None):
        seen["timeout"] = timeout
        return original_as_completed(fs, timeout=timeout)

    monkeypatch.setattr(futures_mod, "as_completed", fake_as_completed)

    calls = []

    def fake_call(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        match = re.search(r"JP203000000[2-6]A", prompt)
        return _comparison_response(match.group(0) if match else "JP2030000002A")

    monkeypatch.setattr("modules.claude_client.call_claude", fake_call)

    result, status = execute.compare_execute("smoke", citation_ids, model="opus")

    assert status == 200
    assert result["execution_mode"] == "per_citation"
    assert result["parallel"] == 1
    assert seen["timeout"] is None
    assert set(result["saved_docs"]) == set(citation_ids)
    assert len(calls) == 5
