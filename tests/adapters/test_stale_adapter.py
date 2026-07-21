from __future__ import annotations

import json

from cmd_audit.adapters.stale import (
    load_stale_probe_cases,
    stale_record_to_probe_cases,
    write_stale_probe_cases,
)


def test_stale_record_maps_real_schema_to_three_query_cases() -> None:
    record = {
        "scenario_id": "calendar-1",
        "type": "T1",
        "M_old": "Workshop venue is Berlin.",
        "M_new": "Workshop venue is Madrid.",
        "explanation": "The venue was updated after the old memory was written.",
        "haystack_session": [
            {"role": "user", "content": "Please remember the travel planning notes."},
            {"role": "assistant", "content": "Dinner remains at 7pm."},
        ],
        "probing_queries": {
            "dim1_query": "Where is the workshop?",
            "dim2_query": "Which city should I travel to for the workshop?",
            "dim3_query": "What is the current workshop venue?",
        },
    }

    cases = stale_record_to_probe_cases(record)

    assert len(cases) == 3
    assert [case.case_id for case in cases] == [
        "stale-calendar-1-dim1",
        "stale-calendar-1-dim2",
        "stale-calendar-1-dim3",
    ]
    assert [case.query for case in cases] == [
        "Where is the workshop?",
        "Which city should I travel to for the workshop?",
        "What is the current workshop venue?",
    ]
    first = cases[0]
    assert first.perturbation_label == "item_stale"
    assert first.gold_answer == "Workshop venue is Madrid."
    memory = {item.memory_id: item for item in first.extracted_memory}
    assert memory["m_stale"].text == "M_old: Workshop venue is Berlin."
    assert memory["m_current"].text == "M_new: Workshop venue is Madrid."
    assert "Dinner remains at 7pm" in memory["m_haystack"].text
    assert memory["m_stale"].source_event_ids == ("e_stale", "e_explanation")
    assert memory["m_current"].source_event_ids == ("e_current", "e_explanation")
    assert first.raw_events[3].text == "The venue was updated after the old memory was written."
    assert first.primary_baseline.retrieved_memory_ids == (
        "m_stale",
        "m_current",
        "m_haystack",
    )
    assert "M_old: Workshop venue is Berlin." in first.primary_baseline.injected_context


def test_stale_type_t2_maps_to_item_conflict() -> None:
    record = {
        "id": "prefs-2",
        "type": "T2",
        "M_old": "Mina chose blue.",
        "M_new": "Mina chose green.",
        "probing_queries": {
            "dim1_query": "Which color did Mina choose?",
            "dim2_query": "What is Mina's current color preference?",
            "dim3_query": "Which color should be used now?",
        },
    }

    case = stale_record_to_probe_cases(record)[0]

    assert case.perturbation_label == "item_conflict"
    stores = {item.memory_id: item.store for item in case.extracted_memory}
    assert stores["m_stale"] == "2026-01-01T00:00:00Z"
    assert stores["m_current"] == "2026-01-03T00:00:00Z"


def test_stale_loader_keeps_full_dataset_by_default(tmp_path) -> None:
    source = tmp_path / "stale.json"
    source.write_text(
        json.dumps(
            {
                "scenarios": [
                    _record("a", "T1"),
                    _record("b", "T2"),
                ]
            }
        ),
        encoding="utf-8",
    )

    cases = load_stale_probe_cases(source)

    assert len(cases) == 6
    assert [case.case_id for case in cases] == [
        "stale-a-dim1",
        "stale-a-dim2",
        "stale-a-dim3",
        "stale-b-dim1",
        "stale-b-dim2",
        "stale-b-dim3",
    ]
    assert len(load_stale_probe_cases(source, limit=2)) == 2


def test_write_stale_probe_cases_defaults_to_full_output(tmp_path) -> None:
    source = tmp_path / "stale.json"
    out = tmp_path / "probe_cases.json"
    source.write_text(json.dumps([_record("s0", "T1")]), encoding="utf-8")

    write_stale_probe_cases(source, out)

    rows = json.loads(out.read_text(encoding="utf-8"))
    assert len(rows) == 3
    assert rows[0]["case_id"] == "stale-s0-dim1"
    assert rows[0]["gold_answer"] == "new-s0"
    assert rows[0]["extracted_memory"][0]["text"] == "M_old: old-s0"
    assert rows[0]["extracted_memory"][1]["text"] == "M_new: new-s0"
    assert "_cmd_baseline_name" not in rows[0]


def _record(scenario_id: str, type_: str) -> dict:
    return {
        "scenario_id": scenario_id,
        "type": type_,
        "M_old": f"old-{scenario_id}",
        "M_new": f"new-{scenario_id}",
        "explanation": f"why-{scenario_id}",
        "haystack_session": f"noise-{scenario_id}",
        "probing_queries": {
            "dim1_query": f"q1-{scenario_id}",
            "dim2_query": f"q2-{scenario_id}",
            "dim3_query": f"q3-{scenario_id}",
        },
    }
