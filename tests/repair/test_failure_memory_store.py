"""Tests for production FailureMemoryStore step-level priors."""

from __future__ import annotations

import tempfile
from types import SimpleNamespace

from cmd_audit.core.labels import PIPELINE_STEP_ACTIONS
from cmd_audit.counterfactual import OperatorSpec, PipelineAction
from cmd_audit.repair.failure_memory import (
    FailureMemorySkillLoop,
    FailureMemoryStore,
    MarkdownFailureMemoryStore,
    StepLevelRecord,
    step_level_record_from_mcts_result,
)


def _record(
    *,
    query: str = "alpha project deadline",
    hop_index: int = 2,
    label: str = "retrieval_error",
    recovered: bool = True,
) -> StepLevelRecord:
    return StepLevelRecord.from_mcts_result(
        query=query,
        hop_index=hop_index,
        label=label,
        cause="cause",
        corrected_memory="corrected",
        repair_guidance="guidance",
        recovery_success=recovered,
        recovery_gain=1.0 if recovered else 0.0,
    )


def test_retrieve_label_is_bonus_not_hard_filter() -> None:
    store = FailureMemoryStore()
    retrieval = _record(label="retrieval_error")
    injection = _record(label="injection_error")
    store.add(injection).add(retrieval)

    records = store.retrieve(
        "alpha project deadline",
        hop_index=2,
        label="retrieval_error",
        top_k=2,
    )

    assert records[0].error_type == "retrieval_error"
    assert {record.error_type for record in records} == {
        "retrieval_error",
        "injection_error",
    }


def test_mcts_result_stores_one_based_hop_index() -> None:
    result = SimpleNamespace(
        main_culprit=(1, PipelineAction.INJECTION_ERROR, 0.7),
    )

    record = step_level_record_from_mcts_result("alpha project deadline", result)

    assert record is not None
    assert record.key.hop_index == 2
    assert record.error_type == "injection_error"
    assert record.operator_spec is not None
    assert record.operator_spec.format() == "gp1:injection_error"


def test_mcts_action_priors_return_complete_action_distribution() -> None:
    store = FailureMemoryStore()
    store.add(_record(label="injection_error", hop_index=2, recovered=True))

    priors = store.get_mcts_action_priors("alpha project deadline", hop_index=2)

    assert tuple(priors) == PIPELINE_STEP_ACTIONS
    assert priors["injection_error"] > priors["retrieval_error"]


def test_label_free_retrieve_honors_hop_index() -> None:
    store = FailureMemoryStore()
    store.add(_record(hop_index=1, label="retrieval_error"))
    store.add(_record(hop_index=3, label="injection_error"))

    records = store.retrieve("alpha project deadline", hop_index=3, top_k=2)

    assert isinstance(records[0], StepLevelRecord)
    assert records[0].key.hop_index == 3


def test_query_hop_priors_are_neutral_without_similar_records() -> None:
    store = FailureMemoryStore()
    store.add(_record(query="alpha project deadline", label="retrieval_error"))

    priors = store.get_mcts_action_priors("unrelated omega topic", hop_index=2)

    assert set(priors) == set(PIPELINE_STEP_ACTIONS)
    assert all(score == 0.5 for score in priors.values())


def test_add_mcts_result_only_persists_recovered_records() -> None:
    store = FailureMemoryStore()
    result = SimpleNamespace(
        main_culprit=(0, PipelineAction.RETRIEVAL_ERROR, 0.0),
    )

    record = store.add_mcts_result("alpha project deadline", result)

    assert record is None
    assert len(store) == 0


def test_store_retrieves_operator_specs_by_memory_fingerprint() -> None:
    store = FailureMemoryStore()
    operator = OperatorSpec.from_actions(
        (
            (0, PipelineAction.RETRIEVAL_ERROR),
            (1, PipelineAction.INJECTION_ERROR),
        ),
        item_signal_hints={"item_new": 1.0},
    )
    store.add(
        StepLevelRecord.from_mcts_result(
            query="alpha project deadline",
            hop_index=1,
            label="retrieval_error",
            cause="cause",
            corrected_memory="corrected",
            repair_guidance="guidance",
            recovery_success=True,
            recovery_gain=0.9,
            memory_texts=("alpha deadline bridge evidence",),
            operator_spec=operator,
        )
    )

    specs, source_count = store.retrieve_operator_specs(
        "unrelated wording",
        max_depth=3,
        memory_texts=("alpha deadline bridge evidence",),
    )

    assert source_count == 1
    assert [spec.format() for spec in specs] == [
        "gp0:retrieval_error+gp1:injection_error+hints[item_new=1]"
    ]


def test_skill_loop_writes_valid_pattern_and_returns_seed() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        loop = FailureMemorySkillLoop(
            MarkdownFailureMemoryStore(tmpdir),
            threshold=2,
        )
        assert loop.record_recovered_case(
            case_id="case_1",
            query="alpha project deadline",
            hop_index=2,
            label="retrieval_error",
            cause="cause",
            corrected_memory="corrected",
            repair_guidance="guidance",
            recovery_gain=0.7,
        ) is None
        pattern = loop.record_recovered_case(
            case_id="case_2",
            query="alpha project date",
            hop_index=2,
            label="retrieval_error",
            cause="cause",
            corrected_memory="corrected",
            repair_guidance="guidance",
            recovery_gain=0.8,
            operator_spec=OperatorSpec.single(1, PipelineAction.RETRIEVAL_ERROR),
        )

        pairs, source_count = loop.retrieve_seed_pairs(
            "alpha project deadline",
            max_depth=3,
        )
        specs, operator_source_count = loop.retrieve_operator_specs(
            "alpha project deadline",
            max_depth=3,
        )
        index = MarkdownFailureMemoryStore(tmpdir).read_index()

    assert pattern is not None
    assert pattern.valid
    assert pattern.operator_spec is not None
    assert pairs == [(1, "retrieval_error")]
    assert source_count == 1
    assert [spec.format() for spec in specs] == ["gp1:retrieval_error"]
    assert operator_source_count == 1
    # Patterns are now keyed by content cluster, so the id carries a cluster
    # suffix (one retrieval_error family here -> cluster 0).
    assert "patterns/pattern_retrieval_error_0.md" in index


def test_skill_loop_loads_operator_specs_from_markdown_store() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MarkdownFailureMemoryStore(tmpdir)
        pattern_markdown = FailureMemorySkillLoop(
            store,
            threshold=2,
        ).skill.format_pattern(
            ("# Case\n\n## Diagnosis\n- **Label**: retrieval_error\n",),
            trigger_fingerprint="alpha bridge evidence",
            source_case_ids=("case_1", "case_2"),
            operator_spec=OperatorSpec.from_actions(
                (
                    (0, PipelineAction.RETRIEVAL_ERROR),
                    (1, PipelineAction.INJECTION_ERROR),
                ),
                item_signal_hints={"item_new": 1.0},
            ),
            recovery_track={
                "recovered": 2,
                "total": 2,
                "avg_recovery_gain": 0.75,
            },
        )
        store.write_pattern(
            "pattern_retrieval_error_0",
            pattern_markdown,
            summary="retrieval_error, 2 source cases, valid=true",
        )

        cold_loop = FailureMemorySkillLoop(store)
        loaded = cold_loop.load_patterns_from_disk()
        specs, source_count = cold_loop.retrieve_operator_specs(
            "unrelated wording",
            max_depth=3,
            memory_texts=("alpha bridge evidence",),
        )

    assert loaded == 1
    assert source_count == 1
    assert [spec.format() for spec in specs] == [
        "gp0:retrieval_error+gp1:injection_error+hints[item_new=1]"
    ]


def test_markdown_store_skips_review_required_patterns() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MarkdownFailureMemoryStore(tmpdir)
        pattern_markdown = FailureMemorySkillLoop(store).skill.format_pattern(
            ("# Case\n\n## Diagnosis\n- **Label**: retrieval_error\n",),
            trigger_fingerprint="alpha bridge evidence",
            source_case_ids=("case_1",),
            operator_spec=OperatorSpec.single(0, PipelineAction.RETRIEVAL_ERROR),
            recovery_track={
                "recovered": 1,
                "total": 1,
                "avg_recovery_gain": 0.5,
            },
        )
        store.write_pattern(
            "pattern_retrieval_error_review",
            pattern_markdown + "\n## Review Required\n- needs human check\n",
            summary="retrieval_error, review required",
        )

        specs, source_count = store.retrieve_operator_specs(
            "unrelated wording",
            max_depth=2,
            memory_texts=("alpha bridge evidence",),
        )

    assert specs == []
    assert source_count == 0
