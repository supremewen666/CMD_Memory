"""Harness integration for Failure Memory operator-skill execution."""

import tempfile
from unittest.mock import patch

from cmd_audit.core.models import BaselineOutput, GoldEvidence, MemoryItem, ProbeCase
from cmd_audit.counterfactual import OperatorSpec, PipelineAction
from cmd_audit.harness import run_case
from cmd_audit.repair.failure_memory import (
    FailureMemorySkillLoop,
    FailureMemoryStore,
    MarkdownFailureMemoryStore,
    StepLevelRecord,
)


def _case_with_missed_second_hop() -> ProbeCase:
    bridge = MemoryItem("m_hop1_bridge", "bridge key K is active", ("e_bridge",))
    distractor = MemoryItem(
        "m_hop2_distractor",
        "Bridge key K resolves to: BERLIN",
        ("e_distractor",),
    )
    gold = MemoryItem("m_hop2_gold", "Bridge key K resolves to: PARIS", ("e_gold",))
    vector_baseline = BaselineOutput(
        baseline_name="vector_memory",
        answer="BERLIN",
        retrieved_memory_ids=("m_hop1_bridge", "m_hop2_distractor"),
        answer_score=0.0,
        evidence_score=0.0,
        injected_context="bridge key K is active\nBridge key K resolves to: BERLIN",
    )
    fixed_baseline = BaselineOutput(
        baseline_name="fixed_summary",
        answer="BERLIN",
        retrieved_memory_ids=("m_hop1_bridge", "m_hop2_distractor"),
        answer_score=0.0,
        evidence_score=0.0,
        injected_context="bridge key K is active\nBridge key K resolves to: BERLIN",
    )
    return ProbeCase(
        case_id="skill-operator-case",
        query="bridge key K resolves to city",
        raw_events=(),
        extracted_memory=(bridge, distractor, gold),
        gold_evidence=(
            GoldEvidence("ev_gold", "Bridge key K resolves to: PARIS", "m_hop2_gold"),
        ),
        gold_answer="PARIS",
        baseline_outputs=(vector_baseline, fixed_baseline),
        perturbation_label="retrieval_error",
    )


def _agent_generate(_query: str, context: str) -> str:
    if "Corrected retrieval candidates" in context and "PARIS" in context:
        return "PARIS"
    return "BERLIN"


def _answer_verifier(answer: str, gold_answer: str) -> float:
    return 1.0 if gold_answer in answer else 0.0


def _store_with_retrieval_operator(case: ProbeCase) -> FailureMemoryStore:
    recall_texts = tuple(
        item.text
        for item in case.extracted_memory
        if item.memory_id in case.primary_baseline.retrieved_memory_ids
    )
    store = FailureMemoryStore()
    store.add(
        StepLevelRecord.from_mcts_result(
            query="prior wording does not matter",
            hop_index=2,
            label="retrieval_error",
            cause="prior recovered by adding the missed second-hop item",
            corrected_memory="Bridge key K resolves to: PARIS",
            repair_guidance="execute the retrieval operator at hop 2",
            recovery_success=True,
            recovery_gain=1.0,
            memory_texts=recall_texts,
            operator_spec=OperatorSpec.single(1, PipelineAction.RETRIEVAL_ERROR),
        )
    )
    return store


def test_run_case_tries_failure_memory_operator_before_single_point_search() -> None:
    case = _case_with_missed_second_hop()
    store = _store_with_retrieval_operator(case)

    with patch("cmd_audit.harness.attribute_single_point") as fallback:
        result = run_case(
            case,
            hook=True,
            agent_generate=_agent_generate,
            answer_verifier=_answer_verifier,
            failure_memory_store=store,
        )

    fallback.assert_not_called()
    assert result.attribution is not None
    assert result.attribution.predicted_label == "retrieval_error"
    assert result.attribution.recovery_gain == 1.0


def test_post_repair_does_not_inject_failure_memory_guidance() -> None:
    case = _case_with_missed_second_hop()
    store = _store_with_retrieval_operator(case)
    captured_contexts: list[str] = []

    def agent_generate(_query: str, context: str) -> str:
        captured_contexts.append(context)
        return _agent_generate(_query, context)

    with patch.object(
        store,
        "get_repair_guidance",
        return_value="GUIDANCE_SENTINEL",
    ):
        result = run_case(
            case,
            post_repair=True,
            agent_generate=agent_generate,
            answer_verifier=_answer_verifier,
            failure_memory_store=store,
        )

    assert result.ecs_draft is not None
    assert result.ecs_draft.repair_guidance == "GUIDANCE_SENTINEL"
    assert captured_contexts
    assert all("GUIDANCE_SENTINEL" not in context for context in captured_contexts)


def test_live_post_repair_fallback_does_not_fabricate_gold_evidence() -> None:
    case = _case_with_missed_second_hop()
    captured_repaired_contexts = []

    def fake_post_repair(_case, repaired_context, **_kwargs):
        captured_repaired_contexts.append(repaired_context)
        from cmd_audit.repair.post_repair import PostRepairResult

        return PostRepairResult(
            case_id=_case.case_id,
            repair_assessment="failed",
            post_repair_answer_score=0.0,
            post_repair_evidence_score=0.0,
            token_cost=0.0,
            regression_risk=0.0,
            had_repair_regression=False,
        )

    with patch("cmd_audit.harness.run_post_repair_context_replay", fake_post_repair):
        result = run_case(
            case,
            post_repair=True,
            agent_generate=lambda _query, _context: "BERLIN",
            answer_verifier=_answer_verifier,
        )

    assert result.ecs_draft is not None
    assert "PARIS" not in result.ecs_draft.corrected_memory
    assert captured_repaired_contexts
    assert all("PARIS" not in ctx.corrected_memory for ctx in captured_repaired_contexts)


def test_run_case_uses_operator_specs_reloaded_from_markdown_skill_library() -> None:
    case = _case_with_missed_second_hop()
    recall_texts = tuple(
        item.text
        for item in case.extracted_memory
        if item.memory_id in case.primary_baseline.retrieved_memory_ids
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        writer_loop = FailureMemorySkillLoop(
            MarkdownFailureMemoryStore(tmpdir),
            threshold=1,
        )
        writer_loop.record_recovered_case(
            case_id="prior_case",
            query="prior wording",
            hop_index=2,
            label="retrieval_error",
            cause="retrieval recovered",
            corrected_memory="Bridge key K resolves to: PARIS",
            repair_guidance="operator documentation",
            retrieved_items=recall_texts,
            memory_texts=recall_texts,
            recovery_gain=1.0,
            operator_spec=OperatorSpec.single(1, PipelineAction.RETRIEVAL_ERROR),
        )
        cold_loop = FailureMemorySkillLoop(MarkdownFailureMemoryStore(tmpdir))
        assert cold_loop.load_patterns_from_disk() == 1

        with patch("cmd_audit.harness.attribute_single_point") as fallback:
            result = run_case(
                case,
                hook=True,
                agent_generate=_agent_generate,
                answer_verifier=_answer_verifier,
                failure_memory_store=cold_loop,
            )

    fallback.assert_not_called()
    assert result.attribution is not None
    assert result.attribution.predicted_label == "retrieval_error"
    assert result.attribution.recovery_gain == 1.0
