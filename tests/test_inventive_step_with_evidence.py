import json

from modules.inventive_step_analyzer import (
    _build_citation_evidence,
    _build_hongan_essence,
    _extract_para_ids,
    _extract_table_ids,
    generate_inventive_step_prompt,
    parse_inventive_step_response,
)


def test_extract_location_ids():
    assert _extract_para_ids("23,24;T1;CL3") == ["23", "24"]
    assert _extract_para_ids("41-45;T2,3") == ["41", "42", "43", "44", "45"]
    assert _extract_table_ids("23,24;T1;CL3") == ["1"]
    assert _extract_table_ids("41-45;T2,3") == ["2", "3"]


def test_build_hongan_essence_empty_and_with_effects():
    assert _build_hongan_essence(None) == ""

    hongan = {
        "paragraphs": [
            {"id": "0001", "section": "課題", "text": "液だれを防止することが課題である。"},
            {"id": "0002", "section": "効果", "text": "洗浄力と泡安定性に優れる。"},
            {"id": "0003", "section": "実施例", "text": "実施例1では良好な泡を得た。"},
        ],
        "tables": [
            {"id": "1", "content": "実施例 | 泡安定性\n1 | ◎\n比較例1 | ×"},
        ],
    }
    text = _build_hongan_essence(hongan)
    assert "## 本願の技術思想" in text
    assert "液だれを防止" in text
    assert "泡安定性" in text
    assert "比較例1" in text


def test_build_citation_evidence_includes_partial_and_negative_evidence():
    responses = {
        "D1": {
            "comparisons": [
                {"requirement_id": "1A", "judgment": "○", "cited_location": "10"},
                {"requirement_id": "1B", "judgment": "△", "cited_location": "23,24;T1"},
                {"requirement_id": "1C", "judgment": "×", "cited_location": "30"},
            ]
        }
    }
    citations_meta = {
        "D1": {
            "paragraphs": [
                {"id": "0023", "section": "実施例", "text": "短繊維長は2mmである。"},
                {"id": "0024", "section": "効果", "text": "泡安定性が向上する。"},
                {"id": "0030", "section": "比較例", "text": "長繊維では効果が不足する。"},
            ],
            "tables": [{"id": "1", "content": "表1\n繊維長 | 評価\n2mm | A"}],
        }
    }
    text = _build_citation_evidence(responses, citations_meta)
    assert "引例の課題・効果" in text
    assert "1B ←【0023】" in text
    assert "短繊維長は2mm" in text
    assert "1C ←【0030】" in text
    assert "表1" in text


def test_generate_prompt_uses_hongan_and_citation_evidence():
    segments = [
        {
            "claim_number": 1,
            "segments": [
                {"id": "1A", "text": "成分Aを含む"},
                {"id": "1B", "text": "繊維長が1〜3mmである"},
            ],
        }
    ]
    responses = {
        "D1": {
            "document_role": "主引例",
            "category_suggestion": "X",
            "overall_summary": "1Aは開示、1Bは一部相違。",
            "comparisons": [
                {"requirement_id": "1A", "judgment": "○", "cited_location": "10", "judgment_reason": "成分Aあり"},
                {"requirement_id": "1B", "judgment": "△", "cited_location": "23;T1", "judgment_reason": "範囲が異なる"},
            ],
        }
    }
    citations_meta = {
        "D1": {
            "paragraphs": [{"id": "0023", "section": "実施例", "text": "繊維長4mmを用いた。"}],
            "tables": [{"id": "1", "content": "表1\n繊維長 | 評価\n4mm | B"}],
        }
    }
    hongan = {
        "paragraphs": [
            {"id": "0003", "section": "課題", "text": "泡安定性を改善する。"},
            {"id": "0004", "section": "効果", "text": "繊維長1〜3mmで顕著な効果がある。"},
        ],
        "tables": [{"id": "1", "content": "本願表1\n1mm | A\n4mm | C"}],
    }

    without_hongan = generate_inventive_step_prompt(segments, responses, citations_meta)
    with_hongan = generate_inventive_step_prompt(
        segments, responses, citations_meta, hongan=hongan
    )

    assert len(with_hongan) > len(without_hongan)
    assert "本願の技術思想" in with_hongan
    assert "泡安定性を改善" in with_hongan
    assert "引用文献の証拠" in with_hongan
    assert "繊維長4mm" in with_hongan
    assert '"deliberation"' in with_hongan
    assert "effect_classification" in with_hongan


def test_parse_inventive_step_response_accepts_new_fields_and_backfill_old_fields():
    raw = json.dumps(
        {
            "overall_assessment": {"inventive_step": "なし"},
            "advantageous_effects": {"claimed_effects": "効果あり"},
        },
        ensure_ascii=False,
    )
    data, errors = parse_inventive_step_response(raw)
    assert errors == []
    assert data["deliberation"] is None
    assert data["advantageous_effects"]["effect_classification"] is None
