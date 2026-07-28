from cmd_audit.core.models import ProbeCase
from cmd_audit.counterfactual.actions import PipelineAction
from cmd_audit.counterfactual.operators import OperatorSpec
from cmd_audit.repair.durable_store import DurableRepairStore
from cmd_audit.repair.failure_memory import _memory_fingerprint


def _case() -> ProbeCase:
    return ProbeCase.from_mapping_v1(
        {
            "case_id": "durable-1",
            "query": "Where is the blue key?",
            "raw_events": [
                {"event_id": "e1", "text": "The blue key is in Oslo."},
                {"event_id": "e2", "text": "The red key is in Rome."},
            ],
            "extracted_memory": [
                {
                    "memory_id": "m1",
                    "text": "A compressed key summary.",
                    "source_event_ids": ["e1", "e2"],
                },
                {
                    "memory_id": "m2",
                    "text": "The blue key is in Oslo.",
                    "source_event_ids": ["e1"],
                },
            ],
            "gold_evidence": [
                {
                    "evidence_id": "g1",
                    "text": "The blue key is in Oslo.",
                    "source_memory_id": "m2",
                }
            ],
            "gold_answer": "Oslo",
            "baseline_outputs": [
                {
                    "baseline_name": "base",
                    "answer": "unknown",
                    "retrieved_memory_ids": ["m1"],
                    "answer_score": 0.0,
                    "evidence_score": 0.0,
                    "injected_context": "A compressed key summary.",
                }
            ],
            "perturbation_label": "retrieval_error",
        }
    )


def test_retrieval_write_back_changes_later_materialized_recall() -> None:
    case = _case()
    recall = (case.extracted_memory[0],)
    store = DurableRepairStore()
    fingerprint = _memory_fingerprint(tuple(item.text for item in recall))
    store.write_back(
        fingerprint=fingerprint,
        operator=OperatorSpec.single(0, PipelineAction.RETRIEVAL_ERROR),
        source_family="family-1",
    )

    materialized = store.materialize(case, recall, base_context="baseline")

    assert [item.memory_id for item in materialized.items] == ["m1", "m2"]
    assert "The blue key is in Oslo." in materialized.context
    assert materialized.changed


def test_granularity_write_back_persists_atomic_raw_events() -> None:
    case = _case()
    recall = (case.extracted_memory[0],)
    store = DurableRepairStore()
    store.write_back(
        fingerprint=_memory_fingerprint(tuple(item.text for item in recall)),
        operator=OperatorSpec.single(0, PipelineAction.GRANULARITY_ERROR),
        source_family="family-1",
    )

    materialized = store.materialize(case, recall, base_context="baseline")

    assert {item.memory_id for item in materialized.items} == {
        "m1__e1",
        "m1__e2",
    }
    assert all(len(item.source_event_ids) == 1 for item in materialized.items)


def test_snapshot_restore_mutates_real_store_state() -> None:
    case = _case()
    recall = (case.extracted_memory[0],)
    store = DurableRepairStore()
    snapshot = store.snapshot()
    store.write_back(
        fingerprint=_memory_fingerprint(tuple(item.text for item in recall)),
        operator=OperatorSpec.single(0, PipelineAction.RETRIEVAL_ERROR),
        source_family="family-1",
    )
    assert not store.matches_snapshot(snapshot)

    store.restore(snapshot)

    assert store.matches_snapshot(snapshot)
    assert len(store) == 0


def test_unrelated_fingerprint_does_not_receive_write_back() -> None:
    case = _case()
    recall = (case.extracted_memory[0],)
    store = DurableRepairStore(similarity_threshold=0.8)
    store.write_back(
        fingerprint="completely unrelated memory terms",
        operator=OperatorSpec.single(0, PipelineAction.RETRIEVAL_ERROR),
        source_family="other",
    )

    materialized = store.materialize(case, recall, base_context="baseline")

    assert materialized.items == recall
    assert materialized.context == "baseline"
    assert not materialized.changed
