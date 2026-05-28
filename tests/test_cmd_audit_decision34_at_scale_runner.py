from pathlib import Path
import csv
import json
import tempfile
import unittest

from cmd_audit.harness import AuditResult
from cmd_audit.core.llm_client import LLMResponse, TokenLogprob
from cmd_audit.core.models import GoldEvidence
from cmd_audit.replays import ReplayResult
from scripts.run_at_scale_llm_retest import (
    RetestCaseResult,
    build_answer_verifier,
    build_evidence_scorer,
    assert_g_eval_available,
    load_case_ids,
    load_labeled_cases,
    write_retest_csv,
)


class _RubricOnlyClient:
    def generate(self, prompt, *, system=None):
        return '{"reasoning": "partial", "score": 2}'


class _LogprobClient:
    def generate(self, prompt, *, system=None):
        return '{"reasoning": "fallback should not be used", "score": 0}'

    def generate_with_logprobs(self, prompt, *, system=None, top_logprobs=10):
        return LLMResponse(
            text='{"reasoning": "strong", "score": 3}',
            token_logprobs=(
                TokenLogprob(token='{"reasoning"', logprob=0.0),
                TokenLogprob(token='"score"', logprob=0.0),
                TokenLogprob(token=":", logprob=0.0),
                TokenLogprob(token="3", logprob=0.0),
            ),
        )


class AtScaleRunnerTest(unittest.TestCase):
    def test_load_labeled_cases_excludes_null_label_cases(self) -> None:
        rows = load_labeled_cases("data/probe_cases")

        self.assertEqual(len(rows), 596)
        self.assertTrue(all(case.perturbation_label is not None for _src, case in rows))

    def test_load_case_ids_accepts_researcher_subset_shape(self) -> None:
        payload = {"cases": [{"case_id": "c1"}, {"case_id": "c2"}]}

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "subset.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            case_ids = load_case_ids(path)

        self.assertEqual(case_ids, {"c1", "c2"})

    def test_rubric_continuous_evidence_scorer_returns_fractional_score(self) -> None:
        scorer = build_evidence_scorer(
            _RubricOnlyClient(),
            scorer_mode="rubric-continuous",
            max_workers=1,
            max_retries=0,
        )
        score = scorer(
            (
                GoldEvidence(
                    evidence_id="e1",
                    text="Lisbon is the capital of Portugal.",
                    source_memory_id=None,
                ),
            ),
            "Lisbon is mentioned as a Portuguese city.",
        )

        self.assertAlmostEqual(score, 0.5)

    def test_rubric_continuous_answer_verifier_returns_fractional_score(self) -> None:
        verifier = build_answer_verifier(
            _RubricOnlyClient(),
            answer_mode="rubric-continuous",
            max_workers=1,
            max_retries=0,
        )

        score = verifier("Lisbon is mentioned.", "Lisbon")

        self.assertAlmostEqual(score, 0.5)
        self.assertNotIn(score, (0.0, 1.0))

    def test_g_eval_evidence_scorer_requires_logprob_expectation(self) -> None:
        scorer = build_evidence_scorer(
            _LogprobClient(),
            scorer_mode="g-eval",
            max_workers=1,
            max_retries=0,
        )

        score = scorer(
            (
                GoldEvidence(
                    evidence_id="e1",
                    text="Lisbon is the capital of Portugal.",
                    source_memory_id=None,
                ),
            ),
            "Lisbon is the capital of Portugal.",
        )

        self.assertAlmostEqual(score, 0.75)

    def test_g_eval_hybrid_falls_back_when_logprobs_are_unparseable(self) -> None:
        scorer = build_evidence_scorer(
            _RubricOnlyClient(),
            scorer_mode="g-eval-hybrid",
            max_workers=1,
            max_retries=0,
        )

        score = scorer(
            (
                GoldEvidence(
                    evidence_id="e1",
                    text="Lisbon is the capital of Portugal.",
                    source_memory_id=None,
                ),
            ),
            "Lisbon is mentioned as a Portuguese city.",
        )

        self.assertAlmostEqual(score, 0.5)

    def test_strict_g_eval_evidence_scorer_raises_without_logprob_expectation(self) -> None:
        scorer = build_evidence_scorer(
            _RubricOnlyClient(),
            scorer_mode="g-eval-strict",
            max_workers=1,
            max_retries=0,
        )

        with self.assertRaises(RuntimeError):
            scorer(
                (
                    GoldEvidence(
                        evidence_id="e1",
                        text="Lisbon is the capital of Portugal.",
                        source_memory_id=None,
                    ),
                ),
                "Lisbon is mentioned as a Portuguese city.",
            )

    def test_g_eval_preflight_rejects_discrete_only_client(self) -> None:
        with self.assertRaises(RuntimeError):
            assert_g_eval_available(_RubricOnlyClient(), role="evaluator")

    def test_write_retest_csv_records_attribution_failed(self) -> None:
        _source, case = load_labeled_cases("data/probe_cases")[0]
        audit = AuditResult(
            case_id=case.case_id,
            perturbation_label=case.perturbation_label,
            baseline_name=case.primary_baseline.baseline_name,
            baseline_answer_score=case.primary_baseline.answer_score,
            baseline_evidence_score=case.primary_baseline.evidence_score,
            replays=(
                ReplayResult(
                    replay_name="oracle_retrieval",
                    answer="",
                    answer_score=0.0,
                    evidence_score=0.0,
                    evidence_block="",
                    recovery_gain=0.0,
                ),
            ),
            attribution=None,
            baseline_suite=None,
            baseline_evidence_score_llm=0.0,
            baseline_answer_score_llm=0.0,
        )
        result = RetestCaseResult(
            source="unit",
            case=case,
            audit=audit,
            elapsed_seconds=1.25,
            failure_reason="zero_gain",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "retest.csv"
            write_retest_csv(path, [result])
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["attribution_failed"], "1")
        self.assertEqual(rows[0]["failure_reason"], "zero_gain")
        self.assertEqual(rows[0]["replay_name"], "oracle_retrieval")


if __name__ == "__main__":
    unittest.main()
