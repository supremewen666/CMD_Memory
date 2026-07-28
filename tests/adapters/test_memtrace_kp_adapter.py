"""Tests for the MemTrace-B protocol knowledge-point adapter.

Fixtures below are verbatim real records from HaluMem
``data/stage4_1_events2memories.jsonl`` (blob sha
``62566339d4b90678a63b0e53ac71a8aca1f936b0``), trimmed to the fields the adapter
reads. Note especially that ``is_update`` is the STRING ``"True"``/``"False"``.
"""

from __future__ import annotations

import json

from cmd_audit.adapters.memtrace_kp import (
    CONFLICT_SEPARATION_DAYS,
    MIN_STALE_SEPARATION_DAYS,
    checkpoint_event_indices,
    expand_memtrace_kp_probes,
    load_memtrace_kp_probe_cases,
    memtrace_kp_label,
    memtrace_kp_dimensions,
    memtrace_kp_record_to_probe_cases,
    parse_is_update,
    write_memtrace_kp_probe_cases,
    _knowledge_points,
)
from cmd_audit.data_io import load_probe_cases_v1


# ── Real HaluMem fixture ──────────────────────────────────────────────────────


def _event(index: int, date: str, name: str, event_type: str = "daily_routine") -> dict:
    return {
        "event_index": index,
        "event_type": event_type,
        "event_name": name,
        "event_time": date,
        "event_description": f"Description for {name}",
        "dialogue_info": {
            "start_time_point": "Sep 04, 2025, 18:42:18",
            "end_time_point": "Sep 04, 2025, 21:12:18",
            "dialogue_summary": f"Summary for {name}",
        },
    }


def _halumem_record() -> dict:
    """One HaluMem user, with real memory-point rows from user 1 of the source."""
    return {
        "uuid": "2f1f897e-d67f-dbc5-6a7b-b7634a9e294f",
        "event_list": [
            _event(0, "2025-09-04", "Initial Information - Fixed Profile", "init_information"),
            _event(1, "2025-09-05", "Initial Information - Preferences", "init_information"),
            _event(2, "2025-12-15", "Career milestone", "career_event"),
            _event(3, "2026-01-06", "Modification of Dog Preference"),
            _event(4, "2026-02-05", "Follow-up conversation"),
            _event(5, "2026-03-01", "Later session"),
        ],
        "profile": {
            "fixed": {"basic_info": {"name": "Martin Mark", "gender": "Male"}},
            "preferences": {
                "Pet Preference": {"init": {"memory_points": []}},
                "Sports Preference": {"init": {"memory_points": []}},
            },
        },
        "memory_points_all": [
            {
                "index": 1,
                "memory_content": "User's name is Martin Mark",
                "memory_type": "Persona Memory",
                "is_update": "False",
                "original_memories": [],
                "timestamp": "Sep 04, 2025, 21:12:18",
                "event_source": 0,
                "importance": 0.75,
            },
            {
                "index": 2,
                "memory_content": "Martin Mark's gender is Male",
                "memory_type": "Persona Memory",
                "is_update": "False",
                "original_memories": [],
                "timestamp": "Sep 04, 2025, 21:12:18",
                "event_source": 0,
                "importance": 0.75,
            },
            {
                "index": 3,
                "memory_content": "Martin Mark Pets I like: Dogs, especially Labradors",
                "memory_type": "Persona Memory",
                "is_update": "False",
                "original_memories": [],
                "timestamp": "Sep 05, 2025, 21:12:18",
                "event_source": 1,
                "importance": 0.75,
            },
            {
                "index": 1,
                "memory_content": (
                    "Martin has modified his pet preference from Labradors to "
                    "Golden Retrievers due to their gentle nature and adaptability."
                ),
                "memory_type": "Persona Memory",
                "is_update": "True",
                "original_memories": [
                    "Martin Mark Pets I like: Dogs, especially Labradors",
                    (
                        "Martin Mark Pets I like Dogs, especially Labradors reason: "
                        "I love Labradors because they are friendly, loyal, and great "
                        "companions for outdoor activities like jogging, which helps "
                        "me stay fit."
                    ),
                ],
                "importance": 0.5,
                "timestamp": "Jan 06, 2026, 20:12:29",
                "event_source": 3,
                "memory_source": "secondary",
            },
            {
                "index": 2,
                "memory_content": (
                    "Martin appreciates Golden Retrievers for their calm demeanor."
                ),
                "memory_type": "Persona Memory",
                "is_update": "False",
                "original_memories": [],
                "timestamp": "Jan 06, 2026, 20:12:29",
                "event_source": 3,
                "importance": 0.5,
            },
        ],
    }


def _dynamic_kp(record: dict):
    points = _knowledge_points(record, record["event_list"])
    dynamic = [point for point in points if point.supports_history]
    assert dynamic, "fixture must contain an is_update=True knowledge point"
    return dynamic[0]


# ── is_update string parsing ──────────────────────────────────────────────────


def test_is_update_is_parsed_as_a_string_not_truthiness() -> None:
    # bool("False") is True; the adapter must not fall for that.
    assert parse_is_update("False") is False
    assert parse_is_update("True") is True
    assert parse_is_update("true") is True
    assert parse_is_update(True) is True
    assert parse_is_update(None) is False
    assert parse_is_update("") is False


def test_knowledge_point_typing_reads_the_string_is_update_field() -> None:
    record = _halumem_record()
    points = _knowledge_points(record, record["event_list"])

    by_text = {point.text: point for point in points}
    static = by_text["User's name is Martin Mark"]
    assert static.is_update is False
    assert static.kp_type == "static"
    assert static.supports_history is False

    dynamic = by_text[
        "Martin has modified his pet preference from Labradors to Golden "
        "Retrievers due to their gentle nature and adaptability."
    ]
    assert dynamic.is_update is True
    assert dynamic.kp_type == "dynamic"
    assert dynamic.supports_history is True
    assert dynamic.prior_text == "Martin Mark Pets I like: Dogs, especially Labradors"


# ── Question-type dimension ───────────────────────────────────────────────────


def test_static_fact_gets_no_trajectory_or_historical_probe() -> None:
    record = _halumem_record()
    points = _knowledge_points(record, record["event_list"])
    static = next(point for point in points if not point.supports_history)

    specs = expand_memtrace_kp_probes(static, checkpoint_event_indices(6, 3))

    assert specs, "a static fact must still be probed"
    assert {spec.question_type for spec in specs} == {"current"}
    # A fact that never changed also cannot support a false-premise probe.
    assert {spec.evidence_condition for spec in specs} == {"present"}


def test_dynamic_fact_gets_all_three_question_types() -> None:
    record = _halumem_record()
    specs = expand_memtrace_kp_probes(
        _dynamic_kp(record), checkpoint_event_indices(6, 3)
    )

    assert {spec.question_type for spec in specs} == {
        "current",
        "historical",
        "trajectory",
    }


# ── Memory-age dimension ──────────────────────────────────────────────────────


def test_checkpoints_are_monotonic_and_end_at_the_last_event() -> None:
    indices = checkpoint_event_indices(65, 8)

    assert len(indices) == 8
    assert list(indices) == sorted(indices)
    assert all(indices[i] < indices[i + 1] for i in range(len(indices) - 1))
    assert indices[-1] == 64
    # Fewer events than checkpoints degrades to one checkpoint per event.
    assert checkpoint_event_indices(3, 8) == (0, 1, 2)
    assert checkpoint_event_indices(0, 8) == ()


def test_memory_age_is_monotonic_and_gated_after_first_appearance() -> None:
    record = _halumem_record()
    kp = _dynamic_kp(record)
    assert kp.first_event_index == 3

    specs = expand_memtrace_kp_probes(kp, checkpoint_event_indices(6, 6))

    # Nothing before the event that wrote the fact.
    assert all(spec.checkpoint_event_index >= 3 for spec in specs)
    assert all(spec.age_sessions >= 0 for spec in specs)
    # Age is exactly sessions elapsed since first appearance, and increases
    # monotonically with the checkpoint.
    for spec in specs:
        assert spec.age_sessions == spec.checkpoint_event_index - 3
    ages = [
        spec.age_sessions
        for spec in specs
        if spec.question_type == "current" and spec.evidence_condition == "present"
    ]
    assert ages == sorted(ages)
    assert ages == [0, 1, 2]


# ── Evidence-condition dimension ──────────────────────────────────────────────


def test_all_three_evidence_conditions_are_produced() -> None:
    cases = memtrace_kp_record_to_probe_cases(_halumem_record(), checkpoints=3)
    conditions = {case.case_id.rsplit("-", 1)[-1] for case in cases}

    assert conditions == {"present", "missing", "contradicted"}


def test_boundary_probes_target_categories_absent_from_the_profile() -> None:
    rows = memtrace_kp_dimensions(_halumem_record(), checkpoints=3)
    boundary = [row for row in rows if row["evidence_condition"] == "missing"]

    assert boundary
    assert all(row["probe_class"] == "boundary_distractor" for row in boundary)
    # The fixture profile records Pet and Sports, so neither may be probed as
    # "never mentioned"; the other eight universe categories may.
    ids = " ".join(row["case_id"] for row in boundary)
    assert "bdpetpreference" not in ids
    assert "bdsportspreference" not in ids
    assert "bdfoodpreference" in ids


# ── Label mapping ─────────────────────────────────────────────────────────────


def test_label_mapping_table() -> None:
    assert memtrace_kp_label("current", "contradicted", "dynamic") == "item_conflict"
    assert memtrace_kp_label("historical", "contradicted", "static") == "item_conflict"
    assert memtrace_kp_label("current", "missing", "boundary") == "safety_error"
    assert memtrace_kp_label("trajectory", "present", "dynamic") == "granularity_error"
    assert memtrace_kp_label("historical", "present", "dynamic") == "retrieval_error"
    assert memtrace_kp_label("current", "present", "dynamic") == "item_stale"
    # Present + current on a fact that never changed is the control condition.
    assert memtrace_kp_label("current", "present", "static") is None
    assert memtrace_kp_label("current", "present", "preference") is None


def test_generated_labels_stay_inside_the_legal_registry() -> None:
    from cmd_audit.core.labels import ITEM_LABELS, PIPELINE_LABELS

    cases = memtrace_kp_record_to_probe_cases(_halumem_record(), checkpoints=4)
    labels = {case.perturbation_label for case in cases}

    assert labels - {None} <= (set(PIPELINE_LABELS) | set(ITEM_LABELS))
    assert "item_conflict" in labels
    assert "safety_error" in labels
    assert "granularity_error" in labels
    assert "retrieval_error" in labels
    assert "item_stale" in labels


def test_mapped_labels_are_structurally_legal_counterfactual_actions() -> None:
    """A mapped label the operator layer cannot even attempt is worthless."""
    from cmd_audit.counterfactual.actions import (
        PipelineAction,
        apply_pipeline_action,
        get_legal_actions,
    )

    cases = memtrace_kp_record_to_probe_cases(_halumem_record(), checkpoints=4)
    seen: set[str] = set()
    for case in cases:
        label = case.perturbation_label
        if label is None or label in seen:
            continue
        seen.add(label)
        memory = {item.memory_id: item for item in case.extracted_memory}
        recall = tuple(
            memory[mid]
            for mid in case.primary_baseline.retrieved_memory_ids
            if mid in memory
        )
        config = {
            "candidate_items": case.extracted_memory,
            "raw_events": case.raw_events,
        }
        legal = {
            action.value
            for action in get_legal_actions(
                recall,
                0,
                include_gated_actions=True,
                include_item_actions=True,
                intervention_config=config,
            )
        }
        assert label in legal, f"{label} illegal on {case.case_id}"

        base = case.primary_baseline.injected_context
        repaired = apply_pipeline_action(
            PipelineAction(label), base, recall, 0, intervention_config=config
        )
        assert repaired != base, f"{label} is a no-op on {case.case_id}"

    assert len(seen) == 5


# ── Abstention gold for boundary probes ───────────────────────────────────────


def test_boundary_probe_gold_is_an_abstention_the_scorer_can_grade() -> None:
    from cmd_audit.scoring.phrase import answer_score, evidence_recall_from_text

    cases = memtrace_kp_record_to_probe_cases(_halumem_record(), checkpoints=2)
    boundary = [
        case for case in cases if case.case_id.endswith("-missing")
    ]
    assert boundary

    case = boundary[0]
    assert case.perturbation_label == "safety_error"
    assert "abstain" in case.gold_answer.casefold()
    assert "no stored memory records" in case.gold_answer.casefold()

    # The scorer must actually be able to grade the abstention both ways.
    assert answer_score(case.gold_answer, case.gold_answer) == 1.0
    assert answer_score(case.primary_baseline.answer, case.gold_answer) == 0.0
    # And the absence evidence must be recallable from the context text.
    assert (
        evidence_recall_from_text(
            case.gold_evidence, case.primary_baseline.injected_context
        )
        == 1.0
    )
    # The safety action is only legal because the scope item is flagged safe.
    assert any(item.passed_safety_filter for item in case.extracted_memory)


# ── Stale item timestamp ordering ─────────────────────────────────────────────


def test_stale_case_orders_both_states_with_parseable_store_timestamps() -> None:
    from cmd_audit.item_gate.collision import (
        _analyze_timestamp_direction,
        _classify_collision_type,
    )

    cases = memtrace_kp_record_to_probe_cases(_halumem_record(), checkpoints=3)
    stale = next(case for case in cases if case.perturbation_label == "item_stale")

    memory = {item.memory_id: item for item in stale.extracted_memory}
    # Both the old and the new state are present as separate items.
    assert "m_prior" in memory and "m_kp" in memory
    assert memory["m_prior"].text != memory["m_kp"].text
    # store is the ISO-8601 Z format _analyze_timestamp_direction can parse.
    assert memory["m_kp"].store.endswith("Z")
    assert memory["m_prior"].store.endswith("Z")
    assert memory["m_prior"].store < memory["m_kp"].store

    direction = _analyze_timestamp_direction(memory["m_kp"], memory["m_prior"], 7)
    assert direction == "a_newer"
    assert _classify_collision_type(direction) == "stale"
    # Both are recalled, which is what makes the stale action legal.
    assert {"m_kp", "m_prior"} <= set(stale.primary_baseline.retrieved_memory_ids)


def test_conflict_case_keeps_states_inside_the_gate_tolerance() -> None:
    from cmd_audit.item_gate.collision import (
        _analyze_timestamp_direction,
        _classify_collision_type,
    )

    cases = memtrace_kp_record_to_probe_cases(_halumem_record(), checkpoints=3)
    conflict = next(
        case for case in cases if case.perturbation_label == "item_conflict"
    )

    memory = {item.memory_id: item for item in conflict.extracted_memory}
    direction = _analyze_timestamp_direction(memory["m_kp"], memory["m_prior"], 7)
    # A conflict must NOT type as stale; the separation stays inside tolerance.
    assert direction == "same_period"
    assert _classify_collision_type(direction) == "conflict"
    assert CONFLICT_SEPARATION_DAYS < 7 < MIN_STALE_SEPARATION_DAYS


def test_historical_probe_withholds_the_prior_state_from_recall() -> None:
    """The pure-retrieval-miss signature retrieval_error needs."""
    cases = memtrace_kp_record_to_probe_cases(_halumem_record(), checkpoints=3)
    historical = next(
        case for case in cases if case.perturbation_label == "retrieval_error"
    )

    recalled = set(historical.primary_baseline.retrieved_memory_ids)
    memory_ids = {item.memory_id for item in historical.extracted_memory}
    # In the pool, absent from recall.
    assert "m_prior" in memory_ids
    assert "m_prior" not in recalled
    # And the gold answer is the prior state, so recovery requires surfacing it.
    assert historical.gold_evidence[0].source_memory_id == "m_prior"


# ── Determinism ───────────────────────────────────────────────────────────────


def test_case_ids_are_deterministic_unique_and_encode_the_grid() -> None:
    record = _halumem_record()

    first = memtrace_kp_record_to_probe_cases(record, checkpoints=4)
    second = memtrace_kp_record_to_probe_cases(_halumem_record(), checkpoints=4)

    ids = [case.case_id for case in first]
    assert ids == [case.case_id for case in second]
    assert len(set(ids)) == len(ids)
    assert all(case_id.startswith("memtraceb-2f1f897e-") for case_id in ids)

    sample = next(
        case.case_id for case in first if case.case_id.endswith("-current-present")
    )
    parts = sample.split("-")
    # memtraceb-<uuid8>-<kp slot>-a<age>c<ckpt>-<qtype>-<evidence cond>
    assert parts[0] == "memtraceb"
    assert parts[1] == "2f1f897e"
    assert parts[3].startswith("a") and "c" in parts[3]
    assert parts[-2:] == ["current", "present"]


def test_queries_never_leak_their_own_gold_answer() -> None:
    cases = memtrace_kp_record_to_probe_cases(_halumem_record(), checkpoints=3)

    for case in cases:
        if case.perturbation_label in ("granularity_error", "safety_error"):
            # Trajectory gold restates both states by construction; boundary
            # gold is an abstention instruction echoed in the prompt on purpose.
            continue
        assert case.gold_answer.casefold() not in case.query.casefold(), case.case_id


# ── Loader round-trip ─────────────────────────────────────────────────────────


def test_generated_cases_round_trip_through_load_probe_cases_v1(tmp_path) -> None:
    source = tmp_path / "halumem.jsonl"
    source.write_text(
        json.dumps(_halumem_record()) + "\n", encoding="utf-8"
    )
    out = tmp_path / "memtrace_kp_cases.json"

    write_memtrace_kp_probe_cases(source, out, checkpoints=4)

    reloaded = load_probe_cases_v1(out)
    direct = load_memtrace_kp_probe_cases(source, checkpoints=4)
    assert reloaded
    assert len(reloaded) == len(direct)
    assert [case.case_id for case in reloaded] == [case.case_id for case in direct]

    rows = json.loads(out.read_text(encoding="utf-8"))
    assert "_cmd_baseline_name" not in rows[0]


def test_loader_honours_users_and_limit(tmp_path) -> None:
    source = tmp_path / "halumem.jsonl"
    second = _halumem_record()
    second["uuid"] = "8ece194a-0000-0000-0000-000000000000"
    source.write_text(
        json.dumps(_halumem_record()) + "\n" + json.dumps(second) + "\n",
        encoding="utf-8",
    )

    both = load_memtrace_kp_probe_cases(source, checkpoints=3)
    one = load_memtrace_kp_probe_cases(source, users=1, checkpoints=3)

    assert len(both) == 2 * len(one)
    assert len(load_memtrace_kp_probe_cases(source, checkpoints=3, limit=5)) == 5


# ── Builder output-path guard ─────────────────────────────────────────────────


def test_builder_refuses_to_write_a_main_line_dataset() -> None:
    import pytest

    from experiments.build_memtrace_kp_cases import (
        UnsafeOutputPathError,
        assert_safe_output_path,
    )
    from pathlib import Path

    for protected in (
        "real_multihop_cases.json",
        "real_recurrent_cases.json",
        "real_item_layer_cases.json",
        "real_three_source_cases.json",
    ):
        with pytest.raises(UnsafeOutputPathError):
            assert_safe_output_path(Path("data/probe_cases") / protected)

    safe = Path("data/probe_cases/memtrace_kp_cases.json")
    assert assert_safe_output_path(safe) == safe
