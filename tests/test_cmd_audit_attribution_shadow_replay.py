"""Behavior tests for route/retrieval shadow replay disambiguation."""

from __future__ import annotations

import unittest

from cmd_audit.attribution import (
    AttributionResult,
    _disambiguate_route_retrieval_shadow,
    assign_attribution_v1,
)
from cmd_audit.harness import _derive_store_sets
from cmd_audit.core.models import (
    BaselineOutput,
    GoldEvidence,
    MemoryItem,
    ProbeCase,
    RawEvent,
)
from cmd_audit.replays import ReplayResult


def _replay(
    replay_name: str,
    *,
    answer_score: float = 0.0,
    evidence_score: float = 0.0,
    recovery_gain: float = 0.0,
) -> ReplayResult:
    return ReplayResult(
        replay_name=replay_name,
        answer="",
        answer_score=answer_score,
        evidence_score=evidence_score,
        evidence_block="",
        recovery_gain=recovery_gain,
    )


# ── _disambiguate_route_retrieval_shadow ───────────────────────────────


class DisambiguateHelperTest(unittest.TestCase):
    def test_no_op_when_pair_is_not_route_and_retrieval(self) -> None:
        ranked = [
            _replay("oracle_compression", recovery_gain=0.6),
            _replay("oracle_write", recovery_gain=0.59),
        ]
        chosen, tag = _disambiguate_route_retrieval_shadow(
            ranked,
            gold_stores=frozenset({"episodic"}),
            queried_stores=frozenset({"episodic"}),
            default_store="episodic",
            shadow_noise_band=0.05,
        )
        self.assertIs(chosen, ranked[0])
        self.assertIsNone(tag)

    def test_no_op_when_gap_exceeds_noise_band(self) -> None:
        ranked = [
            _replay("oracle_route", recovery_gain=0.9),
            _replay("oracle_retrieval", recovery_gain=0.5),
        ]
        chosen, tag = _disambiguate_route_retrieval_shadow(
            ranked,
            gold_stores=frozenset({"episodic"}),
            queried_stores=frozenset({"episodic"}),
            default_store="episodic",
            shadow_noise_band=0.05,
        )
        self.assertEqual(chosen.replay_name, "oracle_route")
        self.assertIsNone(tag)

    def test_default_store_prefers_retrieval(self) -> None:
        # Sub-noise tie, gold lives in default store → retrieval wins.
        ranked = [
            _replay("oracle_route", recovery_gain=0.7500071),
            _replay("oracle_retrieval", recovery_gain=0.7500063),
        ]
        chosen, tag = _disambiguate_route_retrieval_shadow(
            ranked,
            gold_stores=frozenset({"episodic"}),
            queried_stores=frozenset({"episodic"}),
            default_store="episodic",
            shadow_noise_band=0.05,
        )
        self.assertEqual(chosen.replay_name, "oracle_retrieval")
        self.assertEqual(tag, "prefer_retrieval")

    def test_default_alias_also_prefers_retrieval(self) -> None:
        # default_store="default" alias path.
        ranked = [
            _replay("oracle_route", recovery_gain=0.6),
            _replay("oracle_retrieval", recovery_gain=0.59),
        ]
        chosen, tag = _disambiguate_route_retrieval_shadow(
            ranked,
            gold_stores=frozenset({"default"}),
            queried_stores=frozenset({"default"}),
            default_store="default",
            shadow_noise_band=0.05,
        )
        self.assertEqual(chosen.replay_name, "oracle_retrieval")
        self.assertEqual(tag, "prefer_retrieval")

    def test_outside_queried_and_default_prefers_route(self) -> None:
        # Gold lives only in a non-default store the baseline never queried.
        ranked = [
            _replay("oracle_retrieval", recovery_gain=0.6),
            _replay("oracle_route", recovery_gain=0.59),
        ]
        chosen, tag = _disambiguate_route_retrieval_shadow(
            ranked,
            gold_stores=frozenset({"semantic"}),
            queried_stores=frozenset({"episodic"}),
            default_store="episodic",
            shadow_noise_band=0.05,
        )
        self.assertEqual(chosen.replay_name, "oracle_route")
        self.assertEqual(tag, "prefer_route")

    def test_mixed_stores_marks_ambiguous_and_keeps_top(self) -> None:
        # Gold straddles default + non-default but not "outside everything".
        ranked = [
            _replay("oracle_route", recovery_gain=0.6),
            _replay("oracle_retrieval", recovery_gain=0.595),
        ]
        chosen, tag = _disambiguate_route_retrieval_shadow(
            ranked,
            gold_stores=frozenset({"episodic", "semantic"}),
            queried_stores=frozenset({"episodic", "semantic"}),
            default_store="episodic",
            shadow_noise_band=0.05,
        )
        # Default-store branch fires first because gold INTERSECTS default.
        self.assertEqual(chosen.replay_name, "oracle_retrieval")
        self.assertEqual(tag, "prefer_retrieval")

    def test_ambiguous_when_gold_in_queried_non_default_only(self) -> None:
        # Gold sits in a non-default store the baseline DID query — neither
        # rule fires cleanly. The helper keeps the rubric top1 and marks
        # ambiguous so downstream review can adjudicate.
        ranked = [
            _replay("oracle_route", recovery_gain=0.6),
            _replay("oracle_retrieval", recovery_gain=0.59),
        ]
        chosen, tag = _disambiguate_route_retrieval_shadow(
            ranked,
            gold_stores=frozenset({"semantic"}),
            queried_stores=frozenset({"episodic", "semantic"}),
            default_store="episodic",
            shadow_noise_band=0.05,
        )
        self.assertIs(chosen, ranked[0])
        self.assertEqual(tag, "ambiguous")

    def test_no_gold_stores_means_no_op(self) -> None:
        ranked = [
            _replay("oracle_route", recovery_gain=0.6),
            _replay("oracle_retrieval", recovery_gain=0.59),
        ]
        chosen, tag = _disambiguate_route_retrieval_shadow(
            ranked,
            gold_stores=None,
            queried_stores=None,
            default_store="episodic",
            shadow_noise_band=0.05,
        )
        self.assertIs(chosen, ranked[0])
        self.assertIsNone(tag)


# ── assign_attribution_v1 integration ──────────────────────────────────


def _full_replay_set(*, route_gain: float, retrieval_gain: float):
    """Minimal 10-replay portfolio with route/retrieval at the top."""
    return (
        _replay("oracle_write"),
        _replay("oracle_compression"),
        _replay("verbatim_event_oracle"),
        _replay("oracle_retrieval", recovery_gain=retrieval_gain),
        _replay("injection_oracle"),
        _replay("evidence_given_reasoning"),
        _replay("oracle_route", recovery_gain=route_gain),
        _replay("oracle_granularity"),
        _replay("graph_off"),
        _replay("safety_off"),
    )


class AssignAttributionV1ShadowTest(unittest.TestCase):
    def test_default_store_attribution_flips_to_retrieval(self) -> None:
        replays = _full_replay_set(route_gain=0.7500071, retrieval_gain=0.7500063)
        # Without store info — rubric tail wins, route_error.
        baseline = assign_attribution_v1(replays)
        self.assertEqual(baseline.predicted_label, "route_error")
        self.assertIsNone(baseline.shadow_replay_resolution)

        # With store info — retrieval wins.
        resolved = assign_attribution_v1(
            replays,
            gold_stores=frozenset({"episodic"}),
            queried_stores=frozenset({"episodic"}),
            default_store="episodic",
        )
        self.assertEqual(resolved.predicted_label, "retrieval_error")
        self.assertEqual(resolved.shadow_replay_resolution, "prefer_retrieval")
        # close_deltas head reflects the resolved choice.
        self.assertEqual(resolved.close_deltas[0][0], "retrieval_error")
        self.assertEqual(resolved.top_replay, "oracle_retrieval")

    def test_outside_default_attribution_flips_to_route(self) -> None:
        # retrieval slightly higher in rubric, but gold sits in non-default
        # non-queried store → route is the real intervention.
        replays = _full_replay_set(route_gain=0.74, retrieval_gain=0.7401)
        resolved = assign_attribution_v1(
            replays,
            gold_stores=frozenset({"semantic"}),
            queried_stores=frozenset({"episodic"}),
            default_store="episodic",
        )
        self.assertEqual(resolved.predicted_label, "route_error")
        self.assertEqual(resolved.shadow_replay_resolution, "prefer_route")
        self.assertEqual(resolved.top_replay, "oracle_route")

    def test_above_noise_band_no_disambiguation(self) -> None:
        # Real signal — route clearly wins, store-based rule should not flip.
        replays = _full_replay_set(route_gain=0.9, retrieval_gain=0.5)
        resolved = assign_attribution_v1(
            replays,
            gold_stores=frozenset({"episodic"}),
            queried_stores=frozenset({"episodic"}),
            default_store="episodic",
        )
        self.assertEqual(resolved.predicted_label, "route_error")
        self.assertIsNone(resolved.shadow_replay_resolution)

    def test_ambiguous_marker_preserves_rubric_top(self) -> None:
        replays = _full_replay_set(route_gain=0.6, retrieval_gain=0.59)
        resolved = assign_attribution_v1(
            replays,
            gold_stores=frozenset({"semantic"}),
            queried_stores=frozenset({"episodic", "semantic"}),
            default_store="episodic",
        )
        self.assertEqual(resolved.predicted_label, "route_error")
        self.assertEqual(resolved.shadow_replay_resolution, "ambiguous")

    def test_kwargs_default_to_legacy_behavior(self) -> None:
        replays = _full_replay_set(route_gain=0.7500071, retrieval_gain=0.7500063)
        legacy = assign_attribution_v1(replays)
        self.assertEqual(legacy.predicted_label, "route_error")
        self.assertIsNone(legacy.shadow_replay_resolution)


# ── _derive_store_sets ─────────────────────────────────────────────────


def _make_case(
    *,
    gold_in_stores: tuple[str, ...],
    queried_in_stores: tuple[str, ...],
    default_store: str = "episodic",
) -> ProbeCase:
    """Build a tiny ProbeCase with controlled gold/queried store membership."""
    memory_items = []
    gold_items = []
    retrieved_ids = []

    for i, store in enumerate(gold_in_stores):
        mid = f"g{i}"
        memory_items.append(MemoryItem(memory_id=mid, text=f"gold {i}", store=store))
        gold_items.append(
            GoldEvidence(
                evidence_id=f"ev{i}",
                text=f"gold {i}",
                source_memory_id=mid,
            )
        )

    for i, store in enumerate(queried_in_stores):
        mid = f"q{i}"
        memory_items.append(
            MemoryItem(memory_id=mid, text=f"retrieved {i}", store=store)
        )
        retrieved_ids.append(mid)

    return ProbeCase(
        case_id="store-derivation-case",
        query="q",
        raw_events=(),
        extracted_memory=tuple(memory_items),
        gold_evidence=tuple(gold_items),
        gold_answer="a",
        baseline_outputs=(
            BaselineOutput(
                baseline_name="primary",
                answer="a",
                retrieved_memory_ids=tuple(retrieved_ids),
                answer_score=0.0,
                evidence_score=0.0,
                injected_context="ctx",
            ),
        ),
        default_store=default_store,
    )


class DeriveStoreSetsTest(unittest.TestCase):
    def test_collects_gold_and_queried_stores(self) -> None:
        case = _make_case(
            gold_in_stores=("episodic", "semantic"),
            queried_in_stores=("episodic",),
        )
        gold, queried = _derive_store_sets(case)
        self.assertEqual(gold, frozenset({"episodic", "semantic"}))
        self.assertEqual(queried, frozenset({"episodic"}))

    def test_ignores_gold_without_source_memory(self) -> None:
        case = ProbeCase(
            case_id="c",
            query="q",
            raw_events=(),
            extracted_memory=(),
            gold_evidence=(
                GoldEvidence(evidence_id="ev0", text="t", source_memory_id=None),
            ),
            gold_answer="a",
            baseline_outputs=(
                BaselineOutput(
                    baseline_name="primary",
                    answer="a",
                    retrieved_memory_ids=(),
                    answer_score=0.0,
                    evidence_score=0.0,
                ),
            ),
        )
        gold, queried = _derive_store_sets(case)
        self.assertEqual(gold, frozenset())
        self.assertEqual(queried, frozenset())

    def test_returns_frozensets(self) -> None:
        case = _make_case(gold_in_stores=("a",), queried_in_stores=("b",))
        gold, queried = _derive_store_sets(case)
        self.assertIsInstance(gold, frozenset)
        self.assertIsInstance(queried, frozenset)


if __name__ == "__main__":
    unittest.main()
