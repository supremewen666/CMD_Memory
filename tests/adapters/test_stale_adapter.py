from __future__ import annotations

import json

from cmd_audit.adapters.stale import (
    load_stale_probe_cases,
    stale_record_to_probe_cases,
    write_stale_probe_cases,
)


def test_stale_record_to_probe_cases_builds_item_stale_case() -> None:
    record = {
        "scenario_id": "calendar-1",
        "type": "stale",
        "context": "Kai moved the venue after the first reminder.",
        "old_answer": "Berlin",
        "current_answer": "Madrid",
        "queries": [{"query": "Where is Kai's workshop?"}],
    }

    cases = stale_record_to_probe_cases(record)

    assert len(cases) == 1
    case = cases[0]
    assert case.case_id == "stale-calendar-1-q000"
    assert case.perturbation_label == "item_stale"
    assert case.primary_baseline.retrieved_memory_ids == ("m_stale", "m_current")
    assert case.primary_baseline.injected_context.startswith("Earlier remembered")
    assert case.gold_answer == "Madrid"
    assert case.gold_evidence[0].source_memory_id == "m_current"
    assert case.raw_events[-1].text == "Current memory state: Madrid"


def test_stale_record_defaults_to_item_conflict() -> None:
    record = {
        "id": "prefs-2",
        "question": "Which color did Mina choose?",
        "conflicting_answer": "blue",
        "answer": "green",
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
                    {
                        "scenario_id": "a",
                        "type": "stale",
                        "old_answer": "old-a",
                        "current_answer": "new-a",
                        "queries": [
                            {"query": "qa1"},
                            {"query": "qa2"},
                        ],
                    },
                    {
                        "scenario_id": "b",
                        "type": "conflict",
                        "conflicting_answer": "old-b",
                        "answer": "new-b",
                        "queries": [
                            {"query": "qb1"},
                            {"query": "qb2"},
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    cases = load_stale_probe_cases(source)

    assert len(cases) == 4
    assert [case.case_id for case in cases] == [
        "stale-a-q000",
        "stale-a-q001",
        "stale-b-q000",
        "stale-b-q001",
    ]
    assert len(load_stale_probe_cases(source, limit=2)) == 2


def test_write_stale_probe_cases_defaults_to_full_output(tmp_path) -> None:
    source = tmp_path / "stale.json"
    out = tmp_path / "probe_cases.json"
    source.write_text(
        json.dumps(
            [
                {
                    "id": "s0",
                    "old_answer": "old-0",
                    "current_answer": "new-0",
                    "queries": [{"query": "q0"}, {"query": "q1"}],
                }
            ]
        ),
        encoding="utf-8",
    )

    write_stale_probe_cases(source, out)

    rows = json.loads(out.read_text(encoding="utf-8"))
    assert len(rows) == 2
    assert rows[0]["case_id"] == "stale-s0-q000"
    assert "_cmd_baseline_name" not in rows[0]
