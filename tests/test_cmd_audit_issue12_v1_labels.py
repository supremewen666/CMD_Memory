"""Behavior-level tests for issue 0012: granularity_error + graph_error + safety_error labels."""

from __future__ import annotations

from pathlib import Path
import unittest

from cmd_audit import (
    LabelValidationError,
    V0_PIPELINE_LABELS,
    V1_PIPELINE_LABELS,
    V1_PIPELINE_LABEL_ORDER,
    V1_REPLAY_TO_LABEL,
    assign_attribution_v1,
    load_probe_cases,
    load_probe_cases_v1,
    run_case_v1,
    run_cases_v1,
    run_graph_off,
    run_oracle_granularity,
    run_safety_off,
    run_v1_replay_portfolio,
    validate_v0_label,
    validate_v1_label,
)
from cmd_audit.labels import (
    DEFERRED_PIPELINE_LABELS,
    OUT_OF_SCOPE_ITEM_LABELS,
    REPLAY_TO_LABEL,
)

V0_SMOKE = Path("data/probe_cases/v0_issue3_cases.json")
GRANULARITY_FIXTURE = Path("data/probe_cases/v1_granularity_error_case.json")
GRAPH_FIXTURE = Path("data/probe_cases/v1_graph_error_case.json")
SAFETY_FIXTURE = Path("data/probe_cases/v1_safety_error_case.json")
INGESTION_FIXTURE = Path("data/probe_cases/v1_ingestion_error_case.json")
ROUTE_FIXTURE = Path("data/probe_cases/v1_route_error_case.json")


# ── V1 Label Validation (11 labels) ──────────────────────────────────────


class V1LabelValidationTest(unittest.TestCase):
    """validate_v1_label accepts all 11 labels; validate_v0_label rejects new V1 labels."""

    def test_v1_label_order_has_eleven_labels(self) -> None:
        self.assertEqual(len(V1_PIPELINE_LABEL_ORDER), 11)

    def test_v1_labels_are_superset_of_v0(self) -> None:
        self.assertTrue(V0_PIPELINE_LABELS.issubset(V1_PIPELINE_LABELS))

    def test_validate_v1_label_accepts_all_eleven_labels(self) -> None:
        for label in V1_PIPELINE_LABEL_ORDER:
            with self.subTest(label=label):
                self.assertEqual(validate_v1_label(label), label)

    def test_validate_v1_label_accepts_v0_labels(self) -> None:
        for label in V0_PIPELINE_LABELS:
            with self.subTest(label=label):
                self.assertEqual(validate_v1_label(label), label)

    def test_validate_v1_label_rejects_bad_item_labels(self) -> None:
        for label in OUT_OF_SCOPE_ITEM_LABELS:
            with self.subTest(label=label):
                with self.assertRaises(LabelValidationError):
                    validate_v1_label(label)

    def test_validate_v1_label_rejects_deferred_labels(self) -> None:
        self.assertEqual(
            len(DEFERRED_PIPELINE_LABELS),
            0,
            "DEFERRED_PIPELINE_LABELS should be empty after issue 0012",
        )

    def test_validate_v0_label_rejects_granularity_error(self) -> None:
        with self.assertRaises(LabelValidationError):
            validate_v0_label("granularity_error")

    def test_validate_v0_label_rejects_graph_error(self) -> None:
        with self.assertRaises(LabelValidationError):
            validate_v0_label("graph_error")

    def test_validate_v0_label_rejects_safety_error(self) -> None:
        with self.assertRaises(LabelValidationError):
            validate_v0_label("safety_error")

    def test_validate_v0_label_rejects_bad_item_labels(self) -> None:
        for label in OUT_OF_SCOPE_ITEM_LABELS:
            with self.subTest(label=label):
                with self.assertRaises(LabelValidationError):
                    validate_v0_label(label)

    def test_new_labels_not_in_deferred(self) -> None:
        self.assertNotIn("granularity_error", DEFERRED_PIPELINE_LABELS)
        self.assertNotIn("graph_error", DEFERRED_PIPELINE_LABELS)
        self.assertNotIn("safety_error", DEFERRED_PIPELINE_LABELS)

    def test_new_labels_in_v1(self) -> None:
        self.assertIn("granularity_error", V1_PIPELINE_LABELS)
        self.assertIn("graph_error", V1_PIPELINE_LABELS)
        self.assertIn("safety_error", V1_PIPELINE_LABELS)

    def test_v1_replay_to_label_includes_new_replays(self) -> None:
        self.assertIn("oracle_granularity", V1_REPLAY_TO_LABEL)
        self.assertEqual(V1_REPLAY_TO_LABEL["oracle_granularity"], "granularity_error")
        self.assertIn("graph_off", V1_REPLAY_TO_LABEL)
        self.assertEqual(V1_REPLAY_TO_LABEL["graph_off"], "graph_error")
        self.assertIn("safety_off", V1_REPLAY_TO_LABEL)
        self.assertEqual(V1_REPLAY_TO_LABEL["safety_off"], "safety_error")

    def test_v0_replay_to_label_unchanged(self) -> None:
        self.assertEqual(REPLAY_TO_LABEL["oracle_write"], "write_error")
        self.assertNotIn("oracle_granularity", REPLAY_TO_LABEL)
        self.assertNotIn("graph_off", REPLAY_TO_LABEL)
        self.assertNotIn("safety_off", REPLAY_TO_LABEL)


# ── V1 Probe Case Loading ──────────────────────────────────────────────────


class V1ProbeCaseLoadingTest(unittest.TestCase):
    """load_probe_cases_v1 accepts new labels; load_probe_cases rejects them."""

    def test_load_v1_granularity_case(self) -> None:
        cases = load_probe_cases_v1(GRANULARITY_FIXTURE)
        self.assertEqual(len(cases), 1)
        case = cases[0]
        self.assertEqual(case.perturbation_label, "granularity_error")
        self.assertEqual(case.current_granularity, "session")
        self.assertIn("event", case.granularity_levels)

    def test_load_v1_graph_case(self) -> None:
        cases = load_probe_cases_v1(GRAPH_FIXTURE)
        self.assertEqual(len(cases), 1)
        case = cases[0]
        self.assertEqual(case.perturbation_label, "graph_error")
        has_expanded = any(item.is_graph_expanded for item in case.extracted_memory)
        self.assertTrue(has_expanded)

    def test_load_v1_safety_case(self) -> None:
        cases = load_probe_cases_v1(SAFETY_FIXTURE)
        self.assertEqual(len(cases), 1)
        case = cases[0]
        self.assertEqual(case.perturbation_label, "safety_error")
        self.assertTrue(case.safety_filter_blocked)

    def test_load_v0_cases_rejects_granularity_error(self) -> None:
        with self.assertRaises(LabelValidationError):
            load_probe_cases(GRANULARITY_FIXTURE)

    def test_load_v0_cases_rejects_graph_error(self) -> None:
        with self.assertRaises(LabelValidationError):
            load_probe_cases(GRAPH_FIXTURE)

    def test_load_v0_cases_rejects_safety_error(self) -> None:
        with self.assertRaises(LabelValidationError):
            load_probe_cases(SAFETY_FIXTURE)

    def test_gold_evidence_granularity_level_parsed(self) -> None:
        cases = load_probe_cases_v1(GRANULARITY_FIXTURE)
        evidence = cases[0].gold_evidence[0]
        self.assertEqual(evidence.granularity_level, "event")

    def test_memory_item_is_graph_expanded_parsed(self) -> None:
        cases = load_probe_cases_v1(GRAPH_FIXTURE)
        items = cases[0].extracted_memory
        expanded = [item for item in items if item.is_graph_expanded]
        self.assertGreater(len(expanded), 0)


# ── Oracle Granularity Replay ──────────────────────────────────────────────


class OracleGranularityReplayTest(unittest.TestCase):
    """Oracle Granularity replay enumerates levels and recovers from best level."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.granularity_case = load_probe_cases_v1(GRANULARITY_FIXTURE)[0]
        cls.granularity_result = run_oracle_granularity(cls.granularity_case)

    def test_oracle_granularity_has_correct_name(self) -> None:
        self.assertEqual(self.granularity_result.replay_name, "oracle_granularity")

    def test_oracle_granularity_recovers_evidence(self) -> None:
        self.assertGreater(self.granularity_result.evidence_score, 0.0)

    def test_oracle_granularity_positive_recovery_gain(self) -> None:
        self.assertGreater(self.granularity_result.recovery_gain, 0.0)

    def test_oracle_granularity_evidence_block_contains_city(self) -> None:
        self.assertIn("Barcelona", self.granularity_result.evidence_block)

    def test_oracle_granularity_on_v0_case_has_zero_recovery_gain(self) -> None:
        v0_cases = load_probe_cases(V0_SMOKE)
        write_case = [c for c in v0_cases if c.perturbation_label == "write_error"][0]
        result = run_oracle_granularity(write_case)
        self.assertEqual(result.recovery_gain, 0.0)

    def test_oracle_granularity_on_route_case_has_zero_recovery_gain(self) -> None:
        route_case = load_probe_cases_v1(ROUTE_FIXTURE)[0]
        result = run_oracle_granularity(route_case)
        self.assertEqual(result.recovery_gain, 0.0)

    def test_oracle_granularity_no_gain_when_all_levels_equal(self) -> None:
        """When all levels produce the same evidence, gain must be zero."""
        safety_case = load_probe_cases_v1(SAFETY_FIXTURE)[0]
        result = run_oracle_granularity(safety_case)
        self.assertEqual(result.recovery_gain, 0.0)


# ── Graph-Off Replay ───────────────────────────────────────────────────────


class GraphOffReplayTest(unittest.TestCase):
    """Graph-Off replay disables graph expansion and recovers from direct items."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.graph_case = load_probe_cases_v1(GRAPH_FIXTURE)[0]
        cls.graph_result = run_graph_off(cls.graph_case)

    def test_graph_off_has_correct_name(self) -> None:
        self.assertEqual(self.graph_result.replay_name, "graph_off")

    def test_graph_off_recovers_evidence(self) -> None:
        self.assertGreater(self.graph_result.evidence_score, 0.0)

    def test_graph_off_positive_recovery_gain(self) -> None:
        self.assertGreater(self.graph_result.recovery_gain, 0.0)

    def test_graph_off_evidence_block_from_direct_item(self) -> None:
        self.assertIn("Oakwood Construction", self.graph_result.evidence_block)

    def test_graph_off_on_v0_case_has_zero_recovery_gain(self) -> None:
        v0_cases = load_probe_cases(V0_SMOKE)
        write_case = [c for c in v0_cases if c.perturbation_label == "write_error"][0]
        result = run_graph_off(write_case)
        self.assertEqual(result.recovery_gain, 0.0)

    def test_graph_off_zero_gain_when_no_expanded_items(self) -> None:
        """When no items are graph-expanded, gain must be zero."""
        granularity_case = load_probe_cases_v1(GRANULARITY_FIXTURE)[0]
        result = run_graph_off(granularity_case)
        self.assertEqual(result.recovery_gain, 0.0)

    def test_graph_off_zero_gain_for_safety_case(self) -> None:
        safety_case = load_probe_cases_v1(SAFETY_FIXTURE)[0]
        result = run_graph_off(safety_case)
        self.assertEqual(result.recovery_gain, 0.0)


# ── Safety-Off Replay ──────────────────────────────────────────────────────


class SafetyOffReplayTest(unittest.TestCase):
    """Safety-Off replay bypasses safety filter and recovers blocked evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.safety_case = load_probe_cases_v1(SAFETY_FIXTURE)[0]
        cls.safety_result = run_safety_off(cls.safety_case)

    def test_safety_off_has_correct_name(self) -> None:
        self.assertEqual(self.safety_result.replay_name, "safety_off")

    def test_safety_off_recovers_evidence(self) -> None:
        self.assertGreater(self.safety_result.evidence_score, 0.0)

    def test_safety_off_positive_recovery_gain(self) -> None:
        self.assertGreater(self.safety_result.recovery_gain, 0.0)

    def test_safety_off_evidence_block_contains_blocked_content(self) -> None:
        self.assertIn("SQL injection", self.safety_result.evidence_block)
        self.assertIn("payment module", self.safety_result.evidence_block)

    def test_safety_off_on_v0_case_has_zero_recovery_gain(self) -> None:
        v0_cases = load_probe_cases(V0_SMOKE)
        write_case = [c for c in v0_cases if c.perturbation_label == "write_error"][0]
        result = run_safety_off(write_case)
        self.assertEqual(result.recovery_gain, 0.0)

    def test_safety_off_zero_gain_when_not_blocked(self) -> None:
        """When safety_filter_blocked is False, gain must be zero."""
        graph_case = load_probe_cases_v1(GRAPH_FIXTURE)[0]
        result = run_safety_off(graph_case)
        self.assertEqual(result.recovery_gain, 0.0)

    def test_safety_off_zero_gain_for_granularity_case(self) -> None:
        granularity_case = load_probe_cases_v1(GRANULARITY_FIXTURE)[0]
        result = run_safety_off(granularity_case)
        self.assertEqual(result.recovery_gain, 0.0)


# ── V1 Replay Portfolio (10 replays) ───────────────────────────────────────


class V1ReplayPortfolioTest(unittest.TestCase):
    """V1 replay portfolio includes 10 replays (V0 6 + oracle_route + 3 new)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.v0_cases = load_probe_cases(V0_SMOKE)

    def test_portfolio_has_ten_replays(self) -> None:
        replays = run_v1_replay_portfolio(self.v0_cases[0])
        self.assertEqual(len(replays), 10)

    def test_portfolio_includes_new_replays(self) -> None:
        replays = run_v1_replay_portfolio(self.v0_cases[0])
        names = {r.replay_name for r in replays}
        self.assertIn("oracle_granularity", names)
        self.assertIn("graph_off", names)
        self.assertIn("safety_off", names)

    def test_portfolio_includes_oracle_route(self) -> None:
        replays = run_v1_replay_portfolio(self.v0_cases[0])
        names = {r.replay_name for r in replays}
        self.assertIn("oracle_route", names)

    def test_portfolio_includes_all_v0_replays(self) -> None:
        replays = run_v1_replay_portfolio(self.v0_cases[0])
        names = {r.replay_name for r in replays}
        for v0_name in REPLAY_TO_LABEL:
            self.assertIn(v0_name, names, f"V1 portfolio missing V0 replay {v0_name}")

    def test_every_replay_in_portfolio_has_label_mapping(self) -> None:
        replays = run_v1_replay_portfolio(self.v0_cases[0])
        for replay in replays:
            with self.subTest(replay_name=replay.replay_name):
                self.assertIn(replay.replay_name, V1_REPLAY_TO_LABEL)

    def test_new_replays_have_valid_labels(self) -> None:
        self.assertEqual(V1_REPLAY_TO_LABEL["oracle_granularity"], "granularity_error")
        self.assertEqual(V1_REPLAY_TO_LABEL["graph_off"], "graph_error")
        self.assertEqual(V1_REPLAY_TO_LABEL["safety_off"], "safety_error")
        for label in ("granularity_error", "graph_error", "safety_error"):
            self.assertIn(label, V1_PIPELINE_LABELS)


# ── New Label Attribution ──────────────────────────────────────────────────


class GranularityErrorAttributionTest(unittest.TestCase):
    """granularity_error label maps from oracle_granularity replay."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.granularity_case = load_probe_cases_v1(GRANULARITY_FIXTURE)[0]
        cls.replays = run_v1_replay_portfolio(cls.granularity_case)

    def test_oracle_granularity_has_positive_recovery_gain(self) -> None:
        for replay in self.replays:
            if replay.replay_name == "oracle_granularity":
                self.assertGreater(replay.recovery_gain, 0.0)
                return
        self.fail("oracle_granularity replay not found")

    def test_oracle_granularity_produces_valid_attribution(self) -> None:
        attribution = assign_attribution_v1(
            self.replays, has_ingestion_trace=self.granularity_case.has_ingestion_trace
        )
        self.assertIn(attribution.predicted_label, V1_PIPELINE_LABELS)
        self.assertGreater(attribution.recovery_gain, 0.0)

    def test_granularity_case_current_granularity_is_session(self) -> None:
        self.assertEqual(self.granularity_case.current_granularity, "session")

    def test_granularity_case_has_event_level_evidence(self) -> None:
        evidence = self.granularity_case.gold_evidence[0]
        self.assertEqual(evidence.granularity_level, "event")

    def test_no_v0_case_gets_granularity_error(self) -> None:
        v0_cases = load_probe_cases(V0_SMOKE)
        for case in v0_cases:
            with self.subTest(case_id=case.case_id):
                replays = run_v1_replay_portfolio(case)
                attribution = assign_attribution_v1(
                    replays, has_ingestion_trace=case.has_ingestion_trace
                )
                self.assertNotEqual(
                    attribution.predicted_label,
                    "granularity_error",
                    f"{case.case_id}: should not get granularity_error",
                )


class GraphErrorAttributionTest(unittest.TestCase):
    """graph_error label maps from graph_off replay."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.graph_case = load_probe_cases_v1(GRAPH_FIXTURE)[0]
        cls.replays = run_v1_replay_portfolio(cls.graph_case)

    def test_graph_off_has_positive_recovery_gain(self) -> None:
        for replay in self.replays:
            if replay.replay_name == "graph_off":
                self.assertGreater(replay.recovery_gain, 0.0)
                return
        self.fail("graph_off replay not found")

    def test_graph_off_produces_valid_attribution(self) -> None:
        attribution = assign_attribution_v1(
            self.replays, has_ingestion_trace=self.graph_case.has_ingestion_trace
        )
        self.assertIn(attribution.predicted_label, V1_PIPELINE_LABELS)
        self.assertGreater(attribution.recovery_gain, 0.0)

    def test_graph_case_has_expanded_items(self) -> None:
        has_expanded = any(
            item.is_graph_expanded for item in self.graph_case.extracted_memory
        )
        self.assertTrue(has_expanded)

    def test_graph_case_has_direct_item_with_evidence(self) -> None:
        direct = [
            item
            for item in self.graph_case.extracted_memory
            if not item.is_graph_expanded
        ]
        self.assertGreater(len(direct), 0)

    def test_no_v0_case_gets_graph_error(self) -> None:
        v0_cases = load_probe_cases(V0_SMOKE)
        for case in v0_cases:
            with self.subTest(case_id=case.case_id):
                replays = run_v1_replay_portfolio(case)
                attribution = assign_attribution_v1(
                    replays, has_ingestion_trace=case.has_ingestion_trace
                )
                self.assertNotEqual(
                    attribution.predicted_label,
                    "graph_error",
                    f"{case.case_id}: should not get graph_error",
                )


class SafetyErrorAttributionTest(unittest.TestCase):
    """safety_error label is valid and safety_off replay recovers evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.safety_case = load_probe_cases_v1(SAFETY_FIXTURE)[0]

    def test_safety_case_has_blocked_flag(self) -> None:
        self.assertTrue(self.safety_case.safety_filter_blocked)

    def test_safety_off_produces_positive_gain_for_safety_case(self) -> None:
        result = run_safety_off(self.safety_case)
        self.assertGreater(result.recovery_gain, 0.0)

    def test_safety_error_is_in_v1_labels(self) -> None:
        self.assertIn("safety_error", V1_PIPELINE_LABELS)

    def test_no_v0_case_gets_safety_error(self) -> None:
        v0_cases = load_probe_cases(V0_SMOKE)
        for case in v0_cases:
            with self.subTest(case_id=case.case_id):
                replays = run_v1_replay_portfolio(case)
                attribution = assign_attribution_v1(
                    replays, has_ingestion_trace=case.has_ingestion_trace
                )
                self.assertNotEqual(
                    attribution.predicted_label,
                    "safety_error",
                    f"{case.case_id}: should not get safety_error",
                )


# ── V0+0011 Non-Regression through V1 Pipeline ───────────────────────────


class V1NonRegressionTest(unittest.TestCase):
    """V0 6-label + V1 2-label (0011) smoke suite: labels unchanged through V1 pipeline."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.v0_cases = load_probe_cases(V0_SMOKE)
        cls.ingestion_case = load_probe_cases_v1(INGESTION_FIXTURE)[0]

    def test_all_v0_labels_match_through_v1_pipeline(self) -> None:
        for case in self.v0_cases:
            with self.subTest(case_id=case.case_id):
                result = run_case_v1(case, tie_margin=0.05)
                self.assertEqual(
                    result.attribution.predicted_label,
                    case.perturbation_label,
                    f"{case.case_id}: V0 label {case.perturbation_label!r} "
                    f"flipped to {result.attribution.predicted_label!r} in V1 pipeline",
                )

    def test_no_v0_case_gets_granularity_error(self) -> None:
        for case in self.v0_cases:
            with self.subTest(case_id=case.case_id):
                result = run_case_v1(case, tie_margin=0.05)
                self.assertNotEqual(
                    result.attribution.predicted_label,
                    "granularity_error",
                )

    def test_no_v0_case_gets_graph_error(self) -> None:
        for case in self.v0_cases:
            with self.subTest(case_id=case.case_id):
                result = run_case_v1(case, tie_margin=0.05)
                self.assertNotEqual(
                    result.attribution.predicted_label,
                    "graph_error",
                )

    def test_no_v0_case_gets_safety_error(self) -> None:
        for case in self.v0_cases:
            with self.subTest(case_id=case.case_id):
                result = run_case_v1(case, tie_margin=0.05)
                self.assertNotEqual(
                    result.attribution.predicted_label,
                    "safety_error",
                )

    def test_ingestion_case_still_ingestion_error(self) -> None:
        result = run_case_v1(self.ingestion_case)
        self.assertEqual(result.attribution.predicted_label, "ingestion_error")

    def test_v1_pipeline_runs_all_six_and_returns_results(self) -> None:
        results = run_cases_v1(list(self.v0_cases), tie_margin=0.05)
        self.assertEqual(len(results), 6)
        labels = {r.attribution.predicted_label for r in results}
        self.assertEqual(labels, set(V0_PIPELINE_LABELS))


# ── V1 Replay-to-Label Mapping ────────────────────────────────────────────


class V1ReplayToLabelMappingTest(unittest.TestCase):
    """V1_REPLAY_TO_LABEL maps every V1 replay to a valid V1 label."""

    def test_all_mappings_are_valid_v1_labels(self) -> None:
        for replay_name, label in V1_REPLAY_TO_LABEL.items():
            with self.subTest(replay_name=replay_name):
                self.assertIn(label, V1_PIPELINE_LABELS)

    def test_v1_mapping_is_superset_of_v0(self) -> None:
        for replay_name, label in REPLAY_TO_LABEL.items():
            with self.subTest(replay_name=replay_name):
                self.assertEqual(V1_REPLAY_TO_LABEL[replay_name], label)

    def test_new_replays_only_in_v1(self) -> None:
        self.assertNotIn("oracle_granularity", REPLAY_TO_LABEL)
        self.assertIn("oracle_granularity", V1_REPLAY_TO_LABEL)
        self.assertNotIn("graph_off", REPLAY_TO_LABEL)
        self.assertIn("graph_off", V1_REPLAY_TO_LABEL)
        self.assertNotIn("safety_off", REPLAY_TO_LABEL)
        self.assertIn("safety_off", V1_REPLAY_TO_LABEL)

    def test_v1_mapping_has_ten_entries(self) -> None:
        self.assertEqual(len(V1_REPLAY_TO_LABEL), 10)


# ── ECS Compatibility ──────────────────────────────────────────────────────


class V1ECSCompatibilityTest(unittest.TestCase):
    """New V1 labels produce valid ECS drafts through the full pipeline."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.granularity_case = load_probe_cases_v1(GRANULARITY_FIXTURE)[0]
        cls.graph_case = load_probe_cases_v1(GRAPH_FIXTURE)[0]
        cls.safety_case = load_probe_cases_v1(SAFETY_FIXTURE)[0]

    def test_run_case_full_v1_for_granularity_case(self) -> None:
        from cmd_audit import run_case_full_v1

        result = run_case_full_v1(self.granularity_case)
        self.assertIn(result.audit.attribution.predicted_label, V1_PIPELINE_LABELS)
        self.assertTrue(result.ecs_draft.cause)
        self.assertTrue(result.ecs_draft.corrected_memory)

    def test_run_case_full_v1_for_graph_case(self) -> None:
        from cmd_audit import run_case_full_v1

        result = run_case_full_v1(self.graph_case)
        self.assertIn(result.audit.attribution.predicted_label, V1_PIPELINE_LABELS)
        self.assertTrue(result.ecs_draft.cause)
        self.assertTrue(result.ecs_draft.corrected_memory)

    def test_run_case_full_v1_for_safety_case(self) -> None:
        from cmd_audit import run_case_full_v1

        result = run_case_full_v1(self.safety_case)
        self.assertIsNotNone(result.audit.attribution.predicted_label)
        self.assertTrue(result.ecs_draft.cause)
        self.assertIn(
            result.audit.attribution.predicted_label,
            V1_PIPELINE_LABELS,
        )

    def test_new_repair_actions_exist_for_all_new_labels(self) -> None:
        from cmd_audit.repairs import REPAIR_ACTION_BY_LABEL

        for label in ("granularity_error", "graph_error", "safety_error"):
            with self.subTest(label=label):
                self.assertIn(label, REPAIR_ACTION_BY_LABEL)
                action = REPAIR_ACTION_BY_LABEL[label]
                self.assertTrue(action.cause)
                self.assertTrue(action.repair_guidance)


# ── New ProbeCase Fields ──────────────────────────────────────────────────


class NewProbeCaseFieldsTest(unittest.TestCase):
    """New ProbeCase fields have correct defaults and are parsed from JSON."""

    def test_granularity_levels_default(self) -> None:
        v0_cases = load_probe_cases(V0_SMOKE)
        case = v0_cases[0]
        self.assertIsInstance(case.granularity_levels, tuple)
        self.assertGreater(len(case.granularity_levels), 0)

    def test_current_granularity_default(self) -> None:
        v0_cases = load_probe_cases(V0_SMOKE)
        case = v0_cases[0]
        self.assertEqual(case.current_granularity, "session")

    def test_safety_filter_blocked_default(self) -> None:
        v0_cases = load_probe_cases(V0_SMOKE)
        case = v0_cases[0]
        self.assertFalse(case.safety_filter_blocked)

    def test_is_graph_expanded_default(self) -> None:
        v0_cases = load_probe_cases(V0_SMOKE)
        case = v0_cases[0]
        for item in case.extracted_memory:
            self.assertFalse(item.is_graph_expanded)

    def test_granularity_level_on_evidence_default(self) -> None:
        v0_cases = load_probe_cases(V0_SMOKE)
        case = v0_cases[0]
        for evidence in case.gold_evidence:
            self.assertIsNone(evidence.granularity_level)

    def test_granularity_case_levels_configured(self) -> None:
        case = load_probe_cases_v1(GRANULARITY_FIXTURE)[0]
        self.assertEqual(case.current_granularity, "session")
        self.assertIn("event", case.granularity_levels)
        self.assertIn("session", case.granularity_levels)
