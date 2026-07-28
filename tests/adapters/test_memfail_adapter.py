from __future__ import annotations

import csv
import json

import pytest

from cmd_audit.adapters.memfail import (
    MemFailSchemaError,
    load_memfail_probe_cases,
    memfail_record_to_probe_cases,
    write_memfail_probe_cases,
)
from cmd_audit.core.labels import ITEM_LABELS, PIPELINE_LABELS
from cmd_audit.data_io import load_probe_cases_v1
from experiments.build_memfail_cases import build_all

# ── Real sample rows from the shipped MemFail CSVs (verified headers) ──────────

LONG_HOP_ROW = {
    "id": "longhop-1hop-001",
    "hop_count": "1",
    "fact_1": "Early yoga gives me a calm mind.",
    "fact_2": "A calm mind leads me to finish journaling.",
    "fact_3": "",
    "fact_4": "",
    "chain_1": "early yoga",
    "chain_2": "calm mind",
    "chain_3": "finish journaling",
    "chain_4": "",
    "chain_5": "",
    "graded_question": (
        "When I do early yoga, what do I end up doing?\n\nOptions:\n"
        "A. check email\nB. fold laundry\nC. finish journaling\n"
        "D. water the plants\nE. take out the trash"
    ),
    "ground_truth_answer": "finish journaling",
    "choice_a": "check email",
    "choice_b": "fold laundry",
    "choice_c": "finish journaling",
    "choice_d": "water the plants",
    "choice_e": "take out the trash",
    "correct_choice": "C",
}

COEXISTING_ROW = {
    "preference_category": "hat styles",
    "preferences": '["fedora", "beanie", "bucket hat"]',
    "preference_facts": json.dumps(
        [
            "I often wear a fedora when I want to add a classic touch to my outfit.",
            "Beanies are my go-to for staying warm and casual during chilly days.",
            "A bucket hat is what I reach for on sunny, laid-back weekends.",
        ]
    ),
    "question": (
        "I'm packing for a week-long trip with mixed weather — which hats "
        "should I bring to cover all occasions?"
    ),
    "ground_truth_answer": "fedora, beanie, bucket hat",
}

CONDITIONAL_ROW = {
    "entity": "Selene",
    "entity_category": "character",
    "behavior": "composes cryptic messages in a hidden journal",
    "condition_type": "mood",
    "condition": "when feeling nostalgic",
    "entity_facts": json.dumps(
        [
            "Selene has always been a bit of an enigma among her friends. "
            "She enjoys quiet moments alone, often retreating to a cozy nook. "
            "Her hidden journal is a secret treasure she guards closely. "
            "Selene writes cryptic messages in her hidden journal only when "
            "she's feeling nostalgic."
        ]
    ),
    "question": (
        "Selene just found an old photo album from her childhood. Would she "
        "start composing cryptic messages in her journal now?"
    ),
    "question_context": "just found an old photo album",
    "condition_met": "yes",
    "ground_truth_answer": (
        "Yes — finding the album triggers her nostalgia, so she composes "
        "cryptic messages then."
    ),
}

PERSONA_ROW = {
    "entity": "Yuki Tanaka",
    "entity_facts": json.dumps(
        [
            "Yuki Tanaka is a cartographer who chases old shipwrecks. "
            "She wakes at dawn and inks maps by hand with salmon-colored ink. "
            "Yuki refuses to use GPS when plotting historic wreck sites. "
            "She is allergic to shellfish."
        ]
    ),
    "questions": json.dumps(
        [
            {
                "text": (
                    "Do you know which dive site Noah Brooks should visit to "
                    "see interesting wreck patinas?"
                ),
                "is_misleading": True,
                "distractor": "Noah Brooks",
                "ground_truth_answer": "I don't have information about Noah Brooks.",
            },
            {
                "text": "What field equipment should Ava Thompson bring for a mapping outing?",
                "is_misleading": True,
                "distractor": "Ava Thompson",
                "ground_truth_answer": "I don't have information about Ava Thompson.",
            },
            {
                "text": "What should I bring when taking Yuki Tanaka out on a coastal survey?",
                "is_misleading": False,
                "distractor": None,
                "ground_truth_answer": (
                    "Bring a brass sextant, her handmade field notebook, and "
                    "avoid shellfish because she is allergic to shellfish."
                ),
            },
        ]
    ),
}


# ── Label mapping ─────────────────────────────────────────────────────────────


def test_long_hop_maps_to_retrieval_error() -> None:
    cases = memfail_record_to_probe_cases(LONG_HOP_ROW, task="long_hop")

    assert len(cases) == 1
    case = cases[0]
    assert case.perturbation_label == "retrieval_error"
    assert case.perturbation_label in PIPELINE_LABELS


def test_coexisting_maps_to_item_conflict() -> None:
    case = memfail_record_to_probe_cases(COEXISTING_ROW, task="coexisting")[0]

    assert case.perturbation_label == "item_conflict"
    assert case.perturbation_label in ITEM_LABELS


@pytest.mark.parametrize("task", ["conditional_easy", "conditional_hard"])
def test_conditional_maps_to_granularity_error(task: str) -> None:
    case = memfail_record_to_probe_cases(CONDITIONAL_ROW, task=task)[0]

    assert case.perturbation_label == "granularity_error"


def test_persona_misleading_is_safety_error_and_direct_is_retrieval_error() -> None:
    cases = memfail_record_to_probe_cases(PERSONA_ROW, task="persona")

    assert [case.perturbation_label for case in cases] == [
        "safety_error",
        "safety_error",
        "retrieval_error",
    ]


def test_unknown_task_is_rejected() -> None:
    with pytest.raises(MemFailSchemaError):
        memfail_record_to_probe_cases(LONG_HOP_ROW, task="not_a_memfail_task")


# ── One case per graded query ─────────────────────────────────────────────────


def test_persona_row_yields_one_case_per_graded_query() -> None:
    cases = memfail_record_to_probe_cases(PERSONA_ROW, task="persona")

    assert len(cases) == 3
    assert [case.query for case in cases] == [
        "Do you know which dive site Noah Brooks should visit to see interesting wreck patinas?",
        "What field equipment should Ava Thompson bring for a mapping outing?",
        "What should I bring when taking Yuki Tanaka out on a coastal survey?",
    ]


def test_single_query_tasks_yield_exactly_one_case() -> None:
    assert len(memfail_record_to_probe_cases(LONG_HOP_ROW, task="long_hop")) == 1
    assert len(memfail_record_to_probe_cases(COEXISTING_ROW, task="coexisting")) == 1
    assert (
        len(memfail_record_to_probe_cases(CONDITIONAL_ROW, task="conditional_easy")) == 1
    )


# ── JSON-list cell parsing ────────────────────────────────────────────────────


def test_coexisting_parses_json_list_cells_into_separate_events_and_items() -> None:
    case = memfail_record_to_probe_cases(COEXISTING_ROW, task="coexisting")[0]

    # Each of the 3 preference statements is its own raw event and its own item;
    # a correct answer needs all 3 to have survived storage.
    pref_events = [e for e in case.raw_events if e.event_id.startswith("e_pref")]
    pref_items = [m for m in case.extracted_memory if m.memory_id.startswith("m_pref")]
    assert len(pref_events) == 3
    assert len(pref_items) == 3
    assert "fedora" in pref_items[0].text
    assert "Beanies" in pref_items[1].text
    assert "bucket hat" in pref_items[2].text
    # One gold evidence per preference: all N must be recoverable.
    assert len(case.gold_evidence) == 3
    assert case.gold_answer == "fedora, beanie, bucket hat"


def test_malformed_json_list_cell_raises() -> None:
    broken = dict(COEXISTING_ROW, preferences="[not json")

    with pytest.raises(MemFailSchemaError):
        memfail_record_to_probe_cases(broken, task="coexisting")


def test_long_hop_keeps_each_fact_as_a_separate_storage_event() -> None:
    case = memfail_record_to_probe_cases(LONG_HOP_ROW, task="long_hop")[0]

    fact_events = [e for e in case.raw_events if e.event_id.startswith("e_fact")]
    fact_items = [m for m in case.extracted_memory if m.memory_id.startswith("m_fact")]
    assert len(fact_events) == 2
    assert len(fact_items) == 2
    # Facts are never concatenated: that separation is the benchmark's point.
    assert fact_items[0].text == "Early yoga gives me a calm mind."
    assert fact_items[1].text == "A calm mind leads me to finish journaling."
    # Options block is stripped from the query and kept as a distractor item.
    assert case.query == "When I do early yoga, what do I end up doing?"
    assert "Options:" not in case.query
    distractor = next(m for m in case.extracted_memory if m.memory_id == "m_distractor")
    assert "check email" in distractor.text
    assert "finish journaling" not in distractor.text


def test_long_hop_gold_is_answer_text_not_choice_letter() -> None:
    case = memfail_record_to_probe_cases(LONG_HOP_ROW, task="long_hop")[0]

    # Gold is the answer TEXT so CMD's casefolded phrase/exact-match scorer can
    # match generated prose; a bare "C" would not.
    assert case.gold_answer == "finish journaling"
    assert case.gold_answer != "C"
    assert case.gold_evidence[0].required_phrases == ("finish journaling",)


# ── Item <-> event provenance integrity ───────────────────────────────────────


@pytest.mark.parametrize(
    "row,task",
    [
        (LONG_HOP_ROW, "long_hop"),
        (COEXISTING_ROW, "coexisting"),
        (CONDITIONAL_ROW, "conditional_easy"),
        (CONDITIONAL_ROW, "conditional_hard"),
        (PERSONA_ROW, "persona"),
    ],
)
def test_item_to_event_provenance_is_traversable(row: dict, task: str) -> None:
    for case in memfail_record_to_probe_cases(row, task=task):
        event_ids = {event.event_id for event in case.raw_events}
        memory_ids = {item.memory_id for item in case.extracted_memory}
        for item in case.extracted_memory:
            assert item.source_event_ids, f"{case.case_id}:{item.memory_id} has no provenance"
            for event_id in item.source_event_ids:
                assert event_id in event_ids
        for baseline in case.baseline_outputs:
            for memory_id in baseline.retrieved_memory_ids:
                assert memory_id in memory_ids


def test_conditional_stored_item_is_condition_stripped_while_raw_keeps_the_rule() -> None:
    case = memfail_record_to_probe_cases(CONDITIONAL_ROW, task="conditional_easy")[0]

    summary = next(
        m for m in case.extracted_memory if m.memory_id == "m_session_summary"
    )
    # The injected fault: the stored summary asserts the behavior with no qualifier.
    assert "when feeling nostalgic" not in summary.text
    assert "composes cryptic messages in a hidden journal" in summary.text
    # The raw event still carries the full rule, so returning to raw provenance
    # is what makes the case repairable.
    rule_event = next(e for e in case.raw_events if e.event_id == "e_rule")
    assert "when feeling nostalgic" in rule_event.text
    # The coarse summary spans >1 raw event: the structural signature the
    # granularity operator selects on.
    assert len(summary.source_event_ids) > 1
    assert "e_rule" in summary.source_event_ids
    assert case.gold_evidence[0].granularity_level == "event"
    assert case.gold_evidence[0].required_phrases == ("when feeling nostalgic",)


# ── Misleading persona queries abstain ────────────────────────────────────────


def test_misleading_persona_query_gold_answer_is_an_abstention() -> None:
    misleading, _second, direct = memfail_record_to_probe_cases(
        PERSONA_ROW, task="persona"
    )

    assert misleading.gold_answer == "I don't have information about Noah Brooks."
    assert misleading.perturbation_label == "safety_error"
    assert misleading.safety_filter_blocked is True
    # The abstention evidence is a first-class item, flagged safe, so the
    # restore-redacted operator can surface it.
    no_record = next(
        m for m in misleading.extracted_memory if m.memory_id == "m_no_record"
    )
    assert "Noah Brooks" in no_record.text
    assert no_record.passed_safety_filter is True
    # The safety_error signature: the abstention item WAS retrieved but the
    # safety layer redacted it from the injected context, so the answer fell
    # back to the unrelated profile.
    assert "m_no_record" in misleading.primary_baseline.retrieved_memory_ids
    assert "m_no_record" not in misleading.primary_baseline.injected_context
    assert no_record.text not in misleading.primary_baseline.injected_context
    assert misleading.primary_baseline.answer_score == 0.0

    # A direct query is scored on real profile content, not abstention.
    assert "don't have information" not in direct.gold_answer
    assert direct.perturbation_label == "retrieval_error"


def test_misleading_persona_query_without_ground_truth_still_abstains() -> None:
    questions = json.loads(PERSONA_ROW["questions"])
    del questions[0]["ground_truth_answer"]
    row = dict(PERSONA_ROW, questions=json.dumps(questions))

    case = memfail_record_to_probe_cases(row, task="persona")[0]

    assert case.gold_answer == "I don't have information about Noah Brooks."


# ── Deterministic case ids ────────────────────────────────────────────────────


def test_case_ids_are_stable_and_deterministic() -> None:
    first = memfail_record_to_probe_cases(PERSONA_ROW, task="persona", row_index=7)
    second = memfail_record_to_probe_cases(PERSONA_ROW, task="persona", row_index=7)

    assert [case.case_id for case in first] == [case.case_id for case in second]
    assert [case.case_id for case in first] == [
        "memfail-persona-yuki-tanaka-0007-q0",
        "memfail-persona-yuki-tanaka-0007-q1",
        "memfail-persona-yuki-tanaka-0007-q2",
    ]


def test_case_id_follows_task_row_query_template() -> None:
    long_hop = memfail_record_to_probe_cases(LONG_HOP_ROW, task="long_hop")[0]
    coexisting = memfail_record_to_probe_cases(COEXISTING_ROW, task="coexisting")[0]
    conditional = memfail_record_to_probe_cases(
        CONDITIONAL_ROW, task="conditional_hard", row_index=3
    )[0]

    assert long_hop.case_id == "memfail-long_hop-longhop-1hop-001-q0"
    assert coexisting.case_id == "memfail-coexisting-hat-styles-q0"
    assert conditional.case_id == "memfail-conditional_hard-selene-0003-q0"


def test_case_ids_do_not_depend_on_mutable_content() -> None:
    # Rewriting the answer text must not change the id (no content hashing).
    mutated = dict(LONG_HOP_ROW, ground_truth_answer="finish journaling now")
    original = memfail_record_to_probe_cases(LONG_HOP_ROW, task="long_hop")[0]
    changed = memfail_record_to_probe_cases(mutated, task="long_hop")[0]

    assert original.case_id == changed.case_id


# ── Loader round trip ─────────────────────────────────────────────────────────


def test_generated_cases_load_through_load_probe_cases_v1(tmp_path) -> None:
    source = tmp_path / "long_hop_chains.csv"
    _write_csv(source, [LONG_HOP_ROW])
    out = tmp_path / "memfail_long_hop_cases.json"

    write_memfail_probe_cases(source, out, task="long_hop")

    loaded = load_probe_cases_v1(out)
    assert len(loaded) == 1
    assert loaded[0].case_id == "memfail-long_hop-longhop-1hop-001-q0"
    assert loaded[0].perturbation_label == "retrieval_error"
    rows = json.loads(out.read_text(encoding="utf-8"))
    assert "_cmd_baseline_name" not in rows[0]


def test_loader_keeps_full_dataset_by_default_and_honours_limit(tmp_path) -> None:
    source = tmp_path / "persona_dataset.csv"
    _write_csv(source, [PERSONA_ROW, PERSONA_ROW])

    cases = load_memfail_probe_cases(source, task="persona")

    assert len(cases) == 6
    # Row ordinal disambiguates the repeated entity name.
    assert [case.case_id for case in cases] == [
        "memfail-persona-yuki-tanaka-0000-q0",
        "memfail-persona-yuki-tanaka-0000-q1",
        "memfail-persona-yuki-tanaka-0000-q2",
        "memfail-persona-yuki-tanaka-0001-q0",
        "memfail-persona-yuki-tanaka-0001-q1",
        "memfail-persona-yuki-tanaka-0001-q2",
    ]
    assert len(load_memfail_probe_cases(source, task="persona", limit=2)) == 2


# ── Cases are actually repairable by the labeled action ───────────────────────


@pytest.mark.parametrize(
    "row,task,action_name",
    [
        (LONG_HOP_ROW, "long_hop", "retrieval_error"),
        (COEXISTING_ROW, "coexisting", "item_conflict"),
        (CONDITIONAL_ROW, "conditional_easy", "granularity_error"),
        (CONDITIONAL_ROW, "conditional_hard", "granularity_error"),
    ],
)
def test_labeled_action_raises_evidence_recall(row: dict, task: str, action_name: str) -> None:
    """A case no operator can repair is worthless for a repair-efficacy harness."""
    for case in memfail_record_to_probe_cases(row, task=task):
        assert _labeled_action_recovers(case, action_name)


def test_both_persona_query_kinds_are_repairable() -> None:
    misleading, _second, direct = memfail_record_to_probe_cases(
        PERSONA_ROW, task="persona"
    )

    assert _labeled_action_recovers(misleading, "safety_error")
    assert _labeled_action_recovers(direct, "retrieval_error")


def test_coexisting_reconciled_claim_has_weaker_provenance_than_real_statements() -> None:
    """The deconfliction operator no-ops when every item's provenance ties."""
    case = memfail_record_to_probe_cases(COEXISTING_ROW, task="coexisting")[0]
    by_id = {m.memory_id: m for m in case.extracted_memory}

    # The reconciled single-winner claim traces only to the query turn; the real
    # preference statements trace to their own storage events.
    assert by_id["m_reconciled"].source_event_ids == ("e_query",)
    assert by_id["m_pref1"].source_event_ids == ("e_pref1",)
    # All items are retrieved; only the reconciled claim reached the context.
    assert set(case.primary_baseline.retrieved_memory_ids) == set(by_id)
    assert case.primary_baseline.injected_context == by_id["m_reconciled"].text


def test_direct_persona_required_phrases_are_grounded_in_stored_memory() -> None:
    """Phrases sliced from MemFail's synthesized answer match nothing in memory."""
    _m, _s, direct = memfail_record_to_probe_cases(PERSONA_ROW, task="persona")

    stored = "\n".join(item.text for item in direct.extracted_memory)
    for phrase in direct.gold_evidence[0].required_phrases:
        assert phrase.casefold() in stored.casefold()
    # The gold item must be the profile group the answer actually draws on.
    gold_id = direct.gold_evidence[0].source_memory_id
    assert gold_id not in direct.primary_baseline.retrieved_memory_ids


def _labeled_action_recovers(case, action_name: str) -> bool:
    """True if the case's own labeled action raises gold-evidence recall."""
    from cmd_audit.counterfactual.actions import PipelineAction, apply_pipeline_action
    from cmd_audit.scoring import evidence_recall_from_text

    by_id = {item.memory_id: item for item in case.extracted_memory}
    recall = tuple(
        by_id[mid] for mid in case.primary_baseline.retrieved_memory_ids if mid in by_id
    )
    context = case.primary_baseline.injected_context
    config = {
        "candidate_items": list(case.extracted_memory),
        "raw_events": list(case.raw_events),
    }
    before = evidence_recall_from_text(case.gold_evidence, context)
    after = evidence_recall_from_text(
        case.gold_evidence,
        apply_pipeline_action(
            PipelineAction(action_name), context, recall, 0, intervention_config=config
        ),
    )
    return after > before


def test_every_case_carries_a_failing_primary_baseline(tmp_path) -> None:
    source = tmp_path / "coexisting_facts_dataset.csv"
    _write_csv(source, [COEXISTING_ROW])

    case = load_memfail_probe_cases(source, task="coexisting")[0]

    baseline = case.primary_baseline
    assert baseline.baseline_name == "vector_memory"
    assert baseline.answer_score == 0.0
    assert baseline.evidence_score == 0.0
    # The failing answer reflects the failure mode: one surviving preference.
    assert baseline.answer == "bucket hat"
    assert baseline.injected_context


def test_builder_writes_combined_runner_input(tmp_path) -> None:
    csv_dir = tmp_path / "csv"
    output_dir = tmp_path / "probe"
    csv_dir.mkdir()
    _write_csv(csv_dir / "long_hop_chains.csv", [LONG_HOP_ROW])
    _write_csv(csv_dir / "coexisting_facts_dataset.csv", [COEXISTING_ROW])
    _write_csv(
        csv_dir / "conditional_facts_dataset_easy.csv",
        [CONDITIONAL_ROW],
    )
    _write_csv(
        csv_dir / "conditional_facts_dataset_hard.csv",
        [CONDITIONAL_ROW],
    )
    _write_csv(csv_dir / "persona_dataset.csv", [PERSONA_ROW])

    summary = build_all(csv_dir=csv_dir, output_dir=output_dir)

    combined = output_dir / "memfail_cases.json"
    assert summary["combined_output"] == str(combined)
    assert summary["total_cases"] == 7
    assert len(load_probe_cases_v1(combined)) == 7


def _write_csv(path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
