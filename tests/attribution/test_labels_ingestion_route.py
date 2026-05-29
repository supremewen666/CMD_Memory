"""Behavior-level tests for issue 0011: ingestion_error + route_error labels."""

from __future__ import annotations

from pathlib import Path
import unittest

from cmd_audit import (
    LabelValidationError,
    PIPELINE_LABELS_BASE,
    PIPELINE_LABELS,
    PIPELINE_LABEL_ORDER,
    REPLAY_TO_LABEL,
    assign_attribution,
    load_probe_cases,
    load_probe_cases_v1,
    run_case,
    run_cases,
    validate_label_base,
    validate_label,
)
from cmd_audit.core.labels import (
    DEFERRED_PIPELINE_LABELS,
    OUT_OF_SCOPE_ITEM_LABELS,
    REPLAY_TO_LABEL_BASE,
)
from cmd_audit.replays import run_oracle_route, run_replay_portfolio

V0_SMOKE = Path("data/probe_cases/v0_issue3_cases.json")
INGESTION_FIXTURE = Path("data/probe_cases/v1_ingestion_error_case.json")
ROUTE_FIXTURE = Path("data/probe_cases/v1_route_error_case.json")


# ── V1 Label Validation ────────────────────────────────────────────────


class V1LabelValidationTest(unittest.TestCase):
    """validate_label accepts V0+V1 labels; validate_label_base rejects V1 labels."""

    def test_v1_label_order_has_eleven_labels(self) -> None:
        self.assertEqual(len(PIPELINE_LABEL_ORDER), 11)

    def test_v1_labels_are_superset_of_v0(self) -> None:
        self.assertTrue(PIPELINE_LABELS_BASE.issubset(PIPELINE_LABELS))

    def test_validate_label_accepts_all_eight_labels(self) -> None:
        for label in PIPELINE_LABEL_ORDER:
            with self.subTest(label=label):
                self.assertEqual(validate_label(label), label)

    def test_validate_label_accepts_v0_labels(self) -> None:
        for label in PIPELINE_LABELS_BASE:
            with self.subTest(label=label):
                self.assertEqual(validate_label(label), label)

    def test_validate_label_rejects_bad_item_labels(self) -> None:
        for label in OUT_OF_SCOPE_ITEM_LABELS:
            with self.subTest(label=label):
                with self.assertRaises(LabelValidationError):
                    validate_label(label)

    def test_validate_label_rejects_deferred_labels(self) -> None:
        for label in DEFERRED_PIPELINE_LABELS:
            with self.subTest(label=label):
                with self.assertRaises(LabelValidationError):
                    validate_label(label)

    def test_validate_label_base_rejects_ingestion_error(self) -> None:
        with self.assertRaises(LabelValidationError):
            validate_label_base("ingestion_error")

    def test_validate_label_base_rejects_route_error(self) -> None:
        with self.assertRaises(LabelValidationError):
            validate_label_base("route_error")

    def test_validate_label_base_rejects_bad_item_labels(self) -> None:
        for label in OUT_OF_SCOPE_ITEM_LABELS:
            with self.subTest(label=label):
                with self.assertRaises(LabelValidationError):
                    validate_label_base(label)

    def test_ingestion_error_not_in_deferred(self) -> None:
        self.assertNotIn("ingestion_error", DEFERRED_PIPELINE_LABELS)

    def test_route_error_not_in_deferred(self) -> None:
        self.assertNotIn("route_error", DEFERRED_PIPELINE_LABELS)

    def test_granularity_graph_safety_now_active(self) -> None:
        self.assertNotIn("granularity_error", DEFERRED_PIPELINE_LABELS)
        self.assertNotIn("graph_error", DEFERRED_PIPELINE_LABELS)
        self.assertNotIn("safety_error", DEFERRED_PIPELINE_LABELS)
        self.assertIn("granularity_error", PIPELINE_LABELS)
        self.assertIn("graph_error", PIPELINE_LABELS)
        self.assertIn("safety_error", PIPELINE_LABELS)

    def test_v1_replay_to_label_includes_oracle_route(self) -> None:
        self.assertIn("oracle_route", REPLAY_TO_LABEL)
        self.assertEqual(REPLAY_TO_LABEL["oracle_route"], "route_error")

    def test_v0_replay_to_label_unchanged(self) -> None:
        self.assertEqual(REPLAY_TO_LABEL_BASE["oracle_write"], "write_error")
        self.assertNotIn("oracle_route", REPLAY_TO_LABEL_BASE)


# ── V1 Probe Case Loading ──────────────────────────────────────────────


class V1ProbeCaseLoadingTest(unittest.TestCase):
    """load_probe_cases_v1 accepts V1 labels; load_probe_cases still rejects them."""

    def test_load_v1_ingestion_case(self) -> None:
        cases = load_probe_cases_v1(INGESTION_FIXTURE)
        self.assertEqual(len(cases), 1)
        case = cases[0]
        self.assertEqual(case.perturbation_label, "ingestion_error")
        self.assertFalse(case.has_ingestion_trace)

    def test_load_v1_route_case(self) -> None:
        cases = load_probe_cases_v1(ROUTE_FIXTURE)
        self.assertEqual(len(cases), 1)
        case = cases[0]
        self.assertEqual(case.perturbation_label, "route_error")
        self.assertEqual(case.default_store, "episodic")

    def test_load_v1_case_has_stores_on_memory_items(self) -> None:
        cases = load_probe_cases_v1(ROUTE_FIXTURE)
        stores = {item.store for item in cases[0].extracted_memory}
        self.assertIn("episodic", stores)
        self.assertIn("semantic", stores)

    def test_load_v0_cases_still_rejects_ingestion_error(self) -> None:
        with self.assertRaises(LabelValidationError):
            load_probe_cases(INGESTION_FIXTURE)

    def test_load_v0_cases_still_rejects_route_error(self) -> None:
        with self.assertRaises(LabelValidationError):
            load_probe_cases(ROUTE_FIXTURE)


# ── Ingestion Error Attribution ────────────────────────────────────────


class IngestionErrorAttributionTest(unittest.TestCase):
    """ingestion_error/write_error boundary: has_ingestion_trace controls the split."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.ingestion_case = load_probe_cases_v1(INGESTION_FIXTURE)[0]
        cls.v0_cases = load_probe_cases(V0_SMOKE)

    def test_ingestion_case_has_no_trace(self) -> None:
        self.assertFalse(self.ingestion_case.has_ingestion_trace)

    def test_v0_write_case_has_trace_default(self) -> None:
        write_case = [
            c for c in self.v0_cases if c.perturbation_label == "write_error"
        ][0]
        self.assertTrue(write_case.has_ingestion_trace)

    def test_ingestion_case_attributed_as_ingestion_error(self) -> None:
        replays = run_replay_portfolio(self.ingestion_case)
        attribution = assign_attribution(
            replays, has_ingestion_trace=self.ingestion_case.has_ingestion_trace
        )
        self.assertEqual(attribution.predicted_label, "ingestion_error")
        self.assertEqual(attribution.top_replay, "oracle_write")
        self.assertGreater(attribution.recovery_gain, 0.0)

    def test_write_case_through_v1_pipeline_still_write_error(self) -> None:
        write_case = [
            c for c in self.v0_cases if c.perturbation_label == "write_error"
        ][0]
        replays = run_replay_portfolio(write_case)
        attribution = assign_attribution(
            replays, has_ingestion_trace=write_case.has_ingestion_trace
        )
        self.assertEqual(attribution.predicted_label, "write_error")

    def test_v0_cases_all_have_ingestion_trace_true(self) -> None:
        for case in self.v0_cases:
            with self.subTest(case_id=case.case_id):
                self.assertTrue(
                    case.has_ingestion_trace,
                    f"{case.case_id}: has_ingestion_trace should default to True",
                )

    def test_no_v0_case_gets_ingestion_error(self) -> None:
        for case in self.v0_cases:
            with self.subTest(case_id=case.case_id):
                replays = run_replay_portfolio(case)
                attribution = assign_attribution(
                    replays, has_ingestion_trace=case.has_ingestion_trace
                )
                self.assertNotEqual(
                    attribution.predicted_label,
                    "ingestion_error",
                    f"{case.case_id}: should not get ingestion_error",
                )


# ── Oracle Route Replay ────────────────────────────────────────────────


class OracleRouteReplayTest(unittest.TestCase):
    """Oracle Route replay enumerates stores and recovers from best store."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.route_case = load_probe_cases_v1(ROUTE_FIXTURE)[0]
        cls.route_result = run_oracle_route(cls.route_case)

    def test_oracle_route_has_correct_name(self) -> None:
        self.assertEqual(self.route_result.replay_name, "oracle_route")

    def test_oracle_route_recovers_evidence(self) -> None:
        self.assertGreater(self.route_result.evidence_score, 0.0)

    def test_oracle_route_positive_recovery_gain(self) -> None:
        self.assertGreater(self.route_result.recovery_gain, 0.0)

    def test_oracle_route_evidence_block_from_semantic_store(self) -> None:
        self.assertIn("Stockholm", self.route_result.evidence_block)
        self.assertIn("design workshop", self.route_result.evidence_block)

    def test_oracle_route_on_v0_case_has_zero_recovery_gain_for_write(self) -> None:
        v0_cases = load_probe_cases(V0_SMOKE)
        write_case = [c for c in v0_cases if c.perturbation_label == "write_error"][0]
        result = run_oracle_route(write_case)
        self.assertEqual(result.recovery_gain, 0.0)


# ── V1 Replay Portfolio ────────────────────────────────────────────────


class V1ReplayPortfolioTest(unittest.TestCase):
    """V1 replay portfolio includes 8 replays (V0 6 + oracle_route)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.v0_cases = load_probe_cases(V0_SMOKE)

    def test_portfolio_has_ten_replays(self) -> None:
        replays = run_replay_portfolio(self.v0_cases[0])
        self.assertEqual(len(replays), 10)

    def test_portfolio_includes_oracle_route(self) -> None:
        replays = run_replay_portfolio(self.v0_cases[0])
        names = {r.replay_name for r in replays}
        self.assertIn("oracle_route", names)

    def test_portfolio_includes_all_v0_replays(self) -> None:
        replays = run_replay_portfolio(self.v0_cases[0])
        names = {r.replay_name for r in replays}
        for v0_name in REPLAY_TO_LABEL:
            self.assertIn(v0_name, names, f"V1 portfolio missing V0 replay {v0_name}")

    def test_every_replay_in_portfolio_has_label_mapping(self) -> None:
        replays = run_replay_portfolio(self.v0_cases[0])
        for replay in replays:
            with self.subTest(replay_name=replay.replay_name):
                self.assertIn(replay.replay_name, REPLAY_TO_LABEL)

    def test_oracle_route_has_valid_label(self) -> None:
        label = REPLAY_TO_LABEL["oracle_route"]
        self.assertEqual(label, "route_error")
        self.assertIn(label, PIPELINE_LABELS)


# ── V0 Non-Regression through V1 Pipeline ──────────────────────────────


class V1NonRegressionTest(unittest.TestCase):
    """V0 6-label smoke suite produces identical predicted labels through V1 pipeline."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.v0_cases = load_probe_cases(V0_SMOKE)

    def test_all_v0_labels_match_through_v1_pipeline(self) -> None:
        for case in self.v0_cases:
            with self.subTest(case_id=case.case_id):
                result = run_case(case, tie_margin=0.05)
                self.assertEqual(
                    result.attribution.predicted_label,
                    case.perturbation_label,
                    f"{case.case_id}: V0 label {case.perturbation_label!r} "
                    f"flipped to {result.attribution.predicted_label!r} in V1 pipeline",
                )

    def test_no_v0_case_gets_route_error(self) -> None:
        for case in self.v0_cases:
            with self.subTest(case_id=case.case_id):
                result = run_case(case, tie_margin=0.05)
                self.assertNotEqual(
                    result.attribution.predicted_label,
                    "route_error",
                    f"{case.case_id}: should not get route_error",
                )

    def test_no_v0_case_gets_ingestion_error(self) -> None:
        for case in self.v0_cases:
            with self.subTest(case_id=case.case_id):
                result = run_case(case, tie_margin=0.05)
                self.assertNotEqual(
                    result.attribution.predicted_label,
                    "ingestion_error",
                    f"{case.case_id}: should not get ingestion_error",
                )

    def test_v1_pipeline_runs_all_six_and_returns_results(self) -> None:
        results = run_cases(list(self.v0_cases), tie_margin=0.05)
        self.assertEqual(len(results), 6)
        labels = {r.attribution.predicted_label for r in results}
        self.assertEqual(labels, set(PIPELINE_LABELS_BASE))


# ── V1 Replay-to-Label Mapping ────────────────────────────────────────


class V1ReplayToLabelMappingTest(unittest.TestCase):
    """REPLAY_TO_LABEL maps every V1 replay to a valid V1 label."""

    def test_all_mappings_are_valid_v1_labels(self) -> None:
        for replay_name, label in REPLAY_TO_LABEL.items():
            with self.subTest(replay_name=replay_name):
                self.assertIn(label, PIPELINE_LABELS)

    def test_v1_mapping_is_superset_of_v0(self) -> None:
        for replay_name, label in REPLAY_TO_LABEL.items():
            with self.subTest(replay_name=replay_name):
                self.assertEqual(REPLAY_TO_LABEL[replay_name], label)

    def test_oracle_route_only_in_v1(self) -> None:
        self.assertNotIn("oracle_route", REPLAY_TO_LABEL_BASE)
        self.assertIn("oracle_route", REPLAY_TO_LABEL)


# ── ECS Compatibility ──────────────────────────────────────────────────


class V1ECSCompatibilityTest(unittest.TestCase):
    """V1 labels produce valid ECS drafts through the existing pipeline."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.ingestion_case = load_probe_cases_v1(INGESTION_FIXTURE)[0]
        cls.route_case = load_probe_cases_v1(ROUTE_FIXTURE)[0]

    def test_run_case_full_v1_for_ingestion_case(self) -> None:

        result = run_case(self.ingestion_case, post_repair=True)
        self.assertEqual(result.attribution.predicted_label, "ingestion_error")
        self.assertTrue(result.ecs_draft.cause)
        self.assertTrue(result.ecs_draft.corrected_memory)

    def test_run_case_full_v1_for_route_case(self) -> None:

        result = run_case(self.route_case, post_repair=True)
        self.assertIsNotNone(result.attribution.predicted_label)
        self.assertTrue(result.ecs_draft.cause)
