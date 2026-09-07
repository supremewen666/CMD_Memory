"""Route A E-1 tests: runtime/gold separation boundary (BUILD SPEC §3.1, §14.1)."""

import json
import unittest

from cmd_audit.core.models import (
    BaselineOutput,
    GoldEvidence,
    MemoryItem,
    ProbeCase,
    RawEvent,
)
from cmd_audit.eval.state_intent import (
    RuntimeSeparationError,
    runtime_case_from_probe_case,
    runtime_case_to_mapping,
)


def probe_case_with_gold() -> ProbeCase:
    """A probe case whose gold/label values are distinctive sentinels.

    Every sentinel below is a value the BUILD SPEC §3.1 forbidden list says
    must not reach synthesized code.
    """
    return ProbeCase(
        case_id="case_sentinel_1",
        query="Which city does the user live in now?",
        raw_events=(
            RawEvent(event_id="e1", text="The user moved to Lisbon in March."),
            RawEvent(event_id="e2", text="Earlier the user lived in Porto."),
        ),
        extracted_memory=(
            MemoryItem(
                memory_id="m_current",
                text="The user lives in Lisbon.",
                source_event_ids=("e1",),
                store="episodic",
                passed_safety_filter=True,
            ),
            MemoryItem(
                memory_id="m_stale",
                text="M_new: The user lives in Porto.",
                source_event_ids=("e2",),
                store="episodic",
            ),
        ),
        gold_evidence=(
            GoldEvidence(
                evidence_id="ev_gold_sentinel",
                text="The user lives in Lisbon.",
                source_memory_id="m_current",
                source_event_id="e1",
                required_phrases=("REQUIRED_PHRASE_SENTINEL",),
                granularity_level="event",
            ),
        ),
        gold_answer="GOLD_ANSWER_SENTINEL",
        baseline_outputs=(
            BaselineOutput(
                baseline_name="primary",
                answer="Porto",
                retrieved_memory_ids=("m_stale",),
                answer_score=0.0,
                evidence_score=0.0,
            ),
        ),
        perturbation_label="retrieval_error",
    )


class RuntimeCaseSeparationTest(unittest.TestCase):
    def test_runtime_case_serialization_contains_no_gold_or_label(self):
        runtime = runtime_case_from_probe_case(
            probe_case_with_gold(), token_budget=512
        )
        blob = json.dumps(runtime_case_to_mapping(runtime), sort_keys=True)
        for forbidden in (
            "GOLD_ANSWER_SENTINEL",
            "REQUIRED_PHRASE_SENTINEL",
            "ev_gold_sentinel",
            "retrieval_error",
            "gold_evidence",
            "gold_answer",
            "perturbation_label",
            "passed_safety_filter",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, blob)

    def test_runtime_case_keeps_runtime_observable_fields(self):
        runtime = runtime_case_from_probe_case(
            probe_case_with_gold(), token_budget=512
        )
        self.assertEqual(runtime.case_id, "case_sentinel_1")
        self.assertEqual(
            tuple(item.item_id for item in runtime.items), ("m_current", "m_stale")
        )
        self.assertEqual(
            tuple(event.event_id for event in runtime.raw_events), ("e1", "e2")
        )
        self.assertEqual(runtime.token_budget, 512)

    def test_runtime_case_records_which_items_the_pipeline_recalled(self):
        """The recorded run retrieved only m_stale; m_current stayed in the pool.

        Without this, a state built from the pool has every item visible, so a
        retrieval miss cannot be expressed and gold is free-present.
        """
        runtime = runtime_case_from_probe_case(
            probe_case_with_gold(), token_budget=512
        )
        retrieved = {item.item_id: item.retrieved for item in runtime.items}
        self.assertEqual(retrieved, {"m_stale": True, "m_current": False})

    def test_template_hint_in_item_text_is_rejected(self):
        """BUILD SPEC §5.3: injector template hints may not reach runtime."""
        with self.assertRaises(RuntimeSeparationError):
            runtime_case_from_probe_case(
                probe_case_with_gold(), token_budget=512, reject_template_hints=True
            )


if __name__ == "__main__":
    unittest.main()
