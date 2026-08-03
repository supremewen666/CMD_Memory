"""Timeout -> NaN propagation, from rollout through writers and runners.

A timed-out rollout must carry ``recovery_gain = NaN``, never 0.0: 0.0 is a
real measurement ("this repair did not help") while NaN means "not measured".
Conflating them biases every recovery rate and mean downward. These tests pin
the NaN contract at each layer that could quietly launder it back into a zero.
"""

from __future__ import annotations

import csv
import math
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from cmd_audit.core.llm_client import LLMTimeoutError
from cmd_audit.core.models import MemoryItem
from cmd_audit.counterfactual.actions import PipelineAction
from cmd_audit.counterfactual.operators import OperatorSpec, evaluate_operator_spec
from cmd_audit.counterfactual.rollout import rollout_to_terminal
from cmd_audit.counterfactual.search import SinglePointAttributor, attribute_single_point
from cmd_audit.eval.writers import (
    recovery_case_outcomes,
    recovery_mean,
    recovery_positive_rate,
    recovery_timeout_count,
    write_attribution_table,
    write_step_level_metrics_table,
)
from cmd_audit.repair.efficacy import run_single_repair


class TimeoutClient:
    """Client whose generation always times out."""

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        raise LLMTimeoutError("request timed out")


class ErrorClient:
    """Client whose generation fails for a non-timeout reason."""

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        raise RuntimeError("bad gateway")


class OkClient:
    def generate(self, prompt: str, *, system: str | None = None) -> str:
        if "NEXT PREFIX:" in prompt:
            return "prefix"
        if "Corrected retrieval candidates" in prompt:
            return "Paris"
        return "Berlin"


def answer_verifier(answer: str, gold_answer: str) -> float:
    return 1.0 if gold_answer in answer else 0.0


RECALL = (MemoryItem("recall", "France is in Europe", source_event_ids=("e1",)),)
MISSED = MemoryItem("gold", "Paris is the capital of France", source_event_ids=("e2",))


class RolloutTimeoutStatusTest(unittest.TestCase):
    def _rollout(self, client) -> object:
        return rollout_to_terminal(
            client,
            "Corrected retrieval candidates:\n- Paris",
            1,
            1,
            (),
            "Paris",
            answer_verifier=answer_verifier,
        )

    def test_timeout_yields_status_timeout_and_nan_not_zero(self) -> None:
        result = self._rollout(TimeoutClient())

        self.assertEqual(result.status, "timeout")
        self.assertFalse(result.rollout_successful)
        self.assertTrue(math.isnan(result.recovery_gain))
        # The whole point of A1: a timeout must NOT read as a measured zero.
        self.assertNotEqual(result.recovery_gain, 0.0)

    def test_generic_client_exception_yields_zero_not_nan(self) -> None:
        result = self._rollout(ErrorClient())

        # A generic client failure during answer generation is absorbed by
        # _generate_terminal_answer (returns ""), so it surfaces as
        # "empty_answer" rather than "error". Either way it stays a measured
        # 0.0 -- only timeouts become NaN.
        self.assertEqual(result.status, "empty_answer")
        self.assertFalse(result.rollout_successful)
        self.assertEqual(result.recovery_gain, 0.0)
        self.assertFalse(math.isnan(result.recovery_gain))

    def test_generic_exception_inside_rollout_body_yields_zero_and_status_error(
        self,
    ) -> None:
        # A generic exception raised in the rollout body (here: a non-string
        # context reaching .strip()) hits rollout_to_terminal's generic handler.
        result = rollout_to_terminal(
            OkClient(), None, 1, 1, (), "Paris", answer_verifier=answer_verifier
        )

        self.assertEqual(result.status, "error")
        self.assertFalse(result.rollout_successful)
        self.assertEqual(result.recovery_gain, 0.0)
        self.assertFalse(math.isnan(result.recovery_gain))

    def test_status_invariant_holds_for_every_branch(self) -> None:
        for client in (OkClient(), TimeoutClient(), ErrorClient()):
            with self.subTest(client=type(client).__name__):
                result = self._rollout(client)
                self.assertEqual(
                    result.rollout_successful, result.status == "ok"
                )

    def test_no_client_and_empty_answer_statuses_stay_zero(self) -> None:
        no_client = rollout_to_terminal(
            None, "ctx", 1, 1, (), "Paris", answer_verifier=answer_verifier
        )
        self.assertEqual(no_client.status, "no_client")
        self.assertEqual(no_client.recovery_gain, 0.0)

        empty = rollout_to_terminal(
            OkClient(), "   ", 1, 1, (), "Paris", answer_verifier=answer_verifier
        )
        self.assertEqual(empty.status, "empty_answer")
        self.assertEqual(empty.recovery_gain, 0.0)


class ConsumerTimeoutTest(unittest.TestCase):
    """The four RolloutResult consumers must map timeout -> NaN, error -> 0.0."""

    def test_operator_evaluation_scores_nan_on_timeout(self) -> None:
        spec = OperatorSpec.single(0, PipelineAction.RETRIEVAL_ERROR)

        timed_out = evaluate_operator_spec(
            TimeoutClient(),
            "Query: capital of France",
            RECALL,
            spec,
            max_depth=1,
            gold_answer="Paris",
            answer_verifier=answer_verifier,
            intervention_config={"candidate_items": RECALL + (MISSED,)},
        )
        self.assertEqual(timed_out.status, "timeout")
        self.assertTrue(math.isnan(timed_out.score))
        self.assertNotEqual(timed_out.score, 0.0)

        errored = evaluate_operator_spec(
            ErrorClient(),
            "Query: capital of France",
            RECALL,
            spec,
            max_depth=1,
            gold_answer="Paris",
            answer_verifier=answer_verifier,
            intervention_config={"candidate_items": RECALL + (MISSED,)},
        )
        # Non-timeout failure keeps the measured-zero semantics.
        self.assertNotEqual(errored.status, "timeout")
        self.assertEqual(errored.score, 0.0)
        self.assertFalse(math.isnan(errored.score))

    def test_single_point_attributor_scores_nan_on_timeout(self) -> None:
        attributor = SinglePointAttributor()
        score = attributor._rollout_score(
            TimeoutClient(), "ctx", 1, RECALL, "Paris", answer_verifier, 0.0
        )
        self.assertTrue(math.isnan(score))

        error_score = attributor._rollout_score(
            ErrorClient(), "ctx", 1, RECALL, "Paris", answer_verifier, 0.0
        )
        self.assertEqual(error_score, 0.0)

    def test_identity_timeout_is_recorded_not_converted_to_zero_credit(self) -> None:
        result = attribute_single_point(
            TimeoutClient(),
            "ctx",
            RECALL,
            (),
            "Paris",
            max_depth=1,
            answer_verifier=answer_verifier,
            time_limit_seconds=10.0,
        )

        self.assertTrue(result.truncated)
        self.assertIn((0, PipelineAction.IDENTITY), result.timed_out_actions)
        self.assertFalse(result.action_credits)
        self.assertIsNone(result.main_culprit)

    def test_max_iterations_caps_and_reports_unscored_actions(self) -> None:
        result = attribute_single_point(
            OkClient(),
            "ctx",
            RECALL,
            (),
            "Paris",
            max_depth=1,
            max_iterations=1,
            answer_verifier=answer_verifier,
            time_limit_seconds=10.0,
        )

        self.assertEqual(result.iterations_completed, 1)
        self.assertTrue(result.truncated)
        self.assertTrue(result.unscored_actions)

    def test_run_single_repair_reports_nan_on_timeout(self) -> None:
        case = SimpleNamespace(
            case_id="case-1",
            query="capital of France",
            gold_answer="Paris",
            extracted_memory=RECALL + (MISSED,),
            raw_events=(),
            primary_baseline=SimpleNamespace(answer_score=0.0),
        )

        timed_out = run_single_repair(
            case,
            None,
            client=TimeoutClient(),
            answer_verifier=answer_verifier,
            base_context="Query: capital of France",
            recall_set=RECALL,
            max_depth=1,
        )
        self.assertEqual(timed_out.status, "timeout")
        self.assertTrue(math.isnan(timed_out.recovery_gain))
        self.assertNotEqual(timed_out.recovery_gain, 0.0)

        errored = run_single_repair(
            case,
            None,
            client=ErrorClient(),
            answer_verifier=answer_verifier,
            base_context="Query: capital of France",
            recall_set=RECALL,
            max_depth=1,
        )
        self.assertNotEqual(errored.status, "timeout")
        self.assertEqual(errored.recovery_gain, 0.0)


class RecoveryAggregationTest(unittest.TestCase):
    def test_mean_and_rate_exclude_nan(self) -> None:
        values = [1.0, 0.0, float("nan")]

        self.assertEqual(recovery_timeout_count(values), 1)
        # 1.0 and 0.0 only: NaN must not enter numerator or denominator.
        self.assertEqual(recovery_mean(values), 0.5)
        self.assertEqual(recovery_positive_rate(values), 0.5)

    def test_all_nan_degrades_to_zero_not_crash(self) -> None:
        values = [float("nan"), float("nan")]
        self.assertEqual(recovery_timeout_count(values), 2)
        self.assertEqual(recovery_mean(values), 0.0)
        self.assertEqual(recovery_positive_rate(values), 0.0)


def _attribution(recovery_gain: float):
    return SimpleNamespace(
        predicted_label="retrieval_error",
        top_replay="oracle_retrieval",
        recovery_gain=recovery_gain,
        top2_labels=("retrieval_error",),
        is_ambiguous=False,
        top_k_labels=("retrieval_error",),
        close_deltas=(),
        distractor_provenance_ids=(),
    )


def _audit(recovery_gain: float, *, case_id: str = "case-1"):
    return SimpleNamespace(
        case_id=case_id,
        perturbation_label="retrieval_error",
        attribution=_attribution(recovery_gain),
        replay=None,
        replays=[],
        baseline_name="phrase",
        baseline_answer_score=0.0,
        baseline_evidence_score=0.0,
        baseline_evidence_score_llm=None,
        baseline_answer_score_llm=None,
        diagnosis_cost=1.0,
        attribution_correct=False,
    )


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class WriterTimeoutColumnTest(unittest.TestCase):
    def test_attribution_table_emits_timeout_count_and_nan_token(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "attribution_table.csv"
            write_attribution_table(
                [_audit(0.5, case_id="ok"), _audit(float("nan"), case_id="slow")],
                path,
            )
            rows = _read_rows(path)

        by_case = {row["case_id"]: row for row in rows}
        self.assertEqual(by_case["ok"]["timeout_count"], "0")
        self.assertEqual(by_case["ok"]["recovery_gain"], "0.500")
        self.assertEqual(by_case["slow"]["timeout_count"], "1")
        # Never a formatted 0.000 for an unmeasured value.
        self.assertEqual(by_case["slow"]["recovery_gain"], "nan")
        self.assertNotEqual(by_case["slow"]["recovery_gain"], "0.000")

    def test_step_level_metrics_excludes_nan_credit_from_positive_rate(self) -> None:
        def result(credit: float):
            return SimpleNamespace(
                runtime_branch="fix",
                perturbation_label="retrieval_error",
                attribution_result=SimpleNamespace(
                    primary_attribution_label=PipelineAction.RETRIEVAL_ERROR,
                    main_culprit=(0, PipelineAction.RETRIEVAL_ERROR, credit),
                    action_credits={
                        0: {
                            PipelineAction.RETRIEVAL_ERROR: credit,
                            PipelineAction.IDENTITY: 0.0,
                        }
                    },
                ),
            )

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "step_level_metrics.csv"
            write_step_level_metrics_table(
                [result(0.4), result(float("nan"))], path
            )
            rows = {row["metric_name"]: row for row in _read_rows(path)}

        positive = rows["positive_credit_rate"]
        self.assertEqual(positive["numerator"], "1")
        # Denominator drops the timed-out case instead of counting it as
        # non-positive, which would halve the reported rate.
        self.assertEqual(positive["denominator"], "1")
        self.assertEqual(positive["value"], "1.000000")
        self.assertEqual(positive["timeout_count"], "1")


class BaseGainExclusionTest(unittest.TestCase):
    """Regression for the Exp21/22/23 NaN base_gain bug.

    ``net(g) = g - base_gain`` makes every arm NaN once the identity-backbone
    rollout times out, so every ``net > threshold`` test is False and the seeded
    ``-1.0`` maximisation sentinels survive. Tallying that case as
    "not recovered" reintroduces the downward bias A1 removes; the case must be
    excluded instead.
    """

    def test_nan_base_gain_excludes_case_from_tally(self) -> None:
        arm_scores = {"single": float("nan"), "richer": float("nan")}

        self.assertIsNone(
            recovery_case_outcomes(float("nan"), arm_scores, threshold=0.1)
        )

    def test_finite_base_gain_still_tallies_normally(self) -> None:
        outcomes = recovery_case_outcomes(
            0.0,
            {"single": 0.0, "richer": 0.5},
            threshold=0.1,
        )

        self.assertEqual(outcomes, {"single": False, "richer": True})

    def test_timed_out_arm_is_not_recovered_but_case_still_counted(self) -> None:
        outcomes = recovery_case_outcomes(
            0.0,
            {"single": float("nan"), "richer": 0.5},
            threshold=0.1,
        )

        self.assertEqual(outcomes, {"single": False, "richer": True})

    def test_runner_sentinel_would_mistally_without_exclusion(self) -> None:
        """Shows the bug the exclusion prevents, at the runner's own arithmetic."""
        base_gain = float("nan")
        single_best = -1.0  # the runner's seed, never updated because net is NaN
        for observed in (0.9, 1.0):
            net = observed - base_gain
            self.assertTrue(math.isnan(net))
            if net > single_best:  # always False
                single_best = net
        self.assertEqual(single_best, -1.0)
        # Naive threshold test => "not recovered" (the bias).
        self.assertFalse(single_best > 0.1)
        # recovery_case_outcomes refuses to score the case at all.
        self.assertIsNone(
            recovery_case_outcomes(base_gain, {"single": single_best}, threshold=0.1)
        )


class NoTimeoutRegressionTest(unittest.TestCase):
    """With a client that never times out, nothing changes."""

    def test_rollout_and_operator_paths_unchanged(self) -> None:
        rollout = rollout_to_terminal(
            OkClient(),
            "Corrected retrieval candidates:\n- Paris",
            1,
            1,
            (),
            "Paris",
            answer_verifier=answer_verifier,
        )
        self.assertTrue(rollout.rollout_successful)
        self.assertEqual(rollout.status, "ok")
        self.assertEqual(rollout.recovery_gain, 1.0)

        operator = evaluate_operator_spec(
            OkClient(),
            "Query: capital of France",
            RECALL,
            OperatorSpec.single(0, PipelineAction.RETRIEVAL_ERROR),
            max_depth=1,
            gold_answer="Paris",
            answer_verifier=answer_verifier,
            intervention_config={"candidate_items": RECALL + (MISSED,)},
        )
        self.assertTrue(operator.successful)
        self.assertEqual(operator.status, "ok")
        self.assertEqual(operator.score, 1.0)

    def test_aggregation_matches_plain_arithmetic(self) -> None:
        values = [1.0, 0.0, 0.25]

        self.assertEqual(recovery_timeout_count(values), 0)
        self.assertEqual(recovery_mean(values), sum(values) / len(values))
        self.assertEqual(recovery_positive_rate(values), 2 / 3)

    def test_attribution_table_cells_byte_identical_without_timeouts(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "attribution_table.csv"
            write_attribution_table([_audit(0.5), _audit(0.0, case_id="case-2")], path)
            rows = _read_rows(path)

        self.assertEqual([row["recovery_gain"] for row in rows], ["0.500", "0.000"])
        self.assertEqual([row["timeout_count"] for row in rows], ["0", "0"])


if __name__ == "__main__":
    unittest.main()
