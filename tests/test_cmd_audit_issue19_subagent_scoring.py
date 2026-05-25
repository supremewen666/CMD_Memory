"""Behavior-level tests for issue 0019 Phase A: LLM-as-Judge Baseline."""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from cmd_audit import (
    V0_PIPELINE_LABELS,
    LLMClientConfig,
    LLMClientError,
    LLMEmptyResponseError,
    LLMJudgeOutputError,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
    build_judge_prompt,
    load_probe_cases,
    parse_label_from_response,
    run_baseline_suite,
)

V0_SMOKE = Path("data/probe_cases/v0_issue3_cases.json")


# ── LLMClientConfig ───────────────────────────────────────────────────


class LLMClientConfigTest(unittest.TestCase):
    """Configuration defaults and property computation."""

    def test_default_base_url_is_localhost(self) -> None:
        config = LLMClientConfig()
        self.assertIn("localhost", config.base_url)

    def test_default_model_is_qwen(self) -> None:
        config = LLMClientConfig()
        self.assertIn("qwen", config.model)

    def test_temperature_is_zero_for_reproducibility(self) -> None:
        config = LLMClientConfig()
        self.assertEqual(config.temperature, 0.0)

    def test_chat_endpoint_appends_chat_completions(self) -> None:
        config = LLMClientConfig(base_url="http://localhost:11434/v1")
        self.assertTrue(config.chat_endpoint.endswith("/chat/completions"))

    def test_api_key_can_come_from_deepseek_env(self) -> None:
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}, clear=True):
            config = LLMClientConfig()

        self.assertEqual(config.api_key, "test-key")


# ── LLMClient Error Hierarchy ─────────────────────────────────────────


class LLMClientErrorTest(unittest.TestCase):
    """Exception hierarchy for LLM failures."""

    def test_llm_client_error_is_base(self) -> None:
        self.assertTrue(issubclass(LLMUnavailableError, LLMClientError))
        self.assertTrue(issubclass(LLMTimeoutError, LLMClientError))
        self.assertTrue(issubclass(LLMResponseError, LLMClientError))
        self.assertTrue(issubclass(LLMEmptyResponseError, LLMClientError))

    def test_llm_client_error_can_be_caught_as_base(self) -> None:
        with self.assertRaises(LLMClientError):
            raise LLMUnavailableError("endpoint down")

    def test_errors_carry_message(self) -> None:
        exc = LLMTimeoutError("timed out after 60s")
        self.assertIn("timed out", str(exc))


# ── Prompt Construction ───────────────────────────────────────────────


class LLMPromptConstructionTest(unittest.TestCase):
    """build_judge_prompt uses only observable artifacts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)
        cls.case = cls.cases[0]

    def test_prompt_includes_query(self) -> None:
        prompt = build_judge_prompt(self.case)
        self.assertIn(self.case.query[:20], prompt)

    def test_prompt_includes_raw_events(self) -> None:
        prompt = build_judge_prompt(self.case)
        for event in self.case.raw_events:
            self.assertIn(event.text[:15], prompt)

    def test_prompt_includes_extracted_memory(self) -> None:
        prompt = build_judge_prompt(self.case)
        for item in self.case.extracted_memory:
            self.assertIn(item.text[:15], prompt)

    def test_prompt_includes_baseline_answer(self) -> None:
        prompt = build_judge_prompt(self.case)
        baseline = self.case.primary_baseline
        self.assertIn(baseline.answer[:20], prompt)

    def test_prompt_includes_all_six_v0_labels(self) -> None:
        prompt = build_judge_prompt(self.case)
        for label in V0_PIPELINE_LABELS:
            self.assertIn(label, prompt)

    def test_prompt_has_expected_sections(self) -> None:
        prompt = build_judge_prompt(self.case)
        self.assertIn("## Query", prompt)
        self.assertIn("## Raw Events", prompt)
        self.assertIn("## Extracted Memory", prompt)
        self.assertIn("## Baseline Agent Output", prompt)
        self.assertIn("LABEL:", prompt)
        self.assertIn("EXPLANATION:", prompt)


# ── Prompt Boundary (No Gold Leak) ────────────────────────────────────


class LLMPromptBoundaryTest(unittest.TestCase):
    """build_judge_prompt must NEVER include gold data."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)
        cls.case = cls.cases[0]

    def test_prompt_excludes_gold_label(self) -> None:
        prompt = build_judge_prompt(self.case)
        if self.case.perturbation_label:
            label_section_start = prompt.find("## Memory Pipeline Failure Labels")
            if label_section_start >= 0:
                non_label_part = prompt[:label_section_start]
                self.assertNotIn(self.case.perturbation_label, non_label_part)

    def test_prompt_excludes_gold_answer(self) -> None:
        prompt = build_judge_prompt(self.case)
        self.assertNotIn(self.case.gold_answer, prompt)

    def test_prompt_excludes_gold_evidence_texts(self) -> None:
        prompt = build_judge_prompt(self.case)
        for evidence in self.case.gold_evidence:
            self.assertNotIn(evidence.text, prompt)

    def test_prompt_excludes_case_id_label_hints(self) -> None:
        prompt = build_judge_prompt(self.case)
        self.assertNotIn("v0-write", prompt)
        self.assertNotIn("v0-compression", prompt)
        self.assertNotIn("v0-retrieval", prompt)

    def test_prompt_does_not_contain_perturbation_field(self) -> None:
        prompt = build_judge_prompt(self.case)
        self.assertNotIn("perturbation", prompt.lower())


# ── Output Parsing ────────────────────────────────────────────────────


class LLMOutputParsingTest(unittest.TestCase):
    """parse_label_from_response extracts label and explanation."""

    def test_parses_valid_label_and_explanation(self) -> None:
        label, explanation = parse_label_from_response(
            "LABEL: write_error\nEXPLANATION: Events not stored in memory."
        )
        self.assertEqual(label, "write_error")
        self.assertIn("Events", explanation)

    def test_parses_extra_whitespace(self) -> None:
        label, explanation = parse_label_from_response(
            "  LABEL:  retrieval_error  \n\nEXPLANATION:  Evidence not retrieved.  "
        )
        self.assertEqual(label, "retrieval_error")

    def test_parses_v1_only_label(self) -> None:
        label, explanation = parse_label_from_response(
            "LABEL: route_error\nEXPLANATION: The semantic store was not queried."
        )
        self.assertEqual(label, "route_error")
        self.assertIn("semantic", explanation)

    def test_rejects_invalid_label(self) -> None:
        with self.assertRaises(LLMJudgeOutputError):
            parse_label_from_response("LABEL: not_a_label\nEXPLANATION: test")

    def test_rejects_missing_label_line(self) -> None:
        with self.assertRaises(LLMJudgeOutputError):
            parse_label_from_response("EXPLANATION: no label above")

    def test_explanation_defaults_when_missing(self) -> None:
        label, explanation = parse_label_from_response(
            "LABEL: reasoning_error"
        )
        self.assertEqual(label, "reasoning_error")
        self.assertIn("reasoning_error", explanation)


# ── LLM Judge Fallback (llm_client=None) ──────────────────────────────


class LLMJudgeFallbackTest(unittest.TestCase):
    """When llm_client is None, run_llm_judge_baseline falls back."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)

    def test_fallback_produces_comparator_result(self) -> None:
        from baselines.comparators import run_llm_judge_baseline
        result = run_llm_judge_baseline(self.cases[0], llm_client=None)
        self.assertEqual(result.comparator_name, "llm_judge")
        self.assertIn(result.predicted_label, V0_PIPELINE_LABELS)

    def test_fallback_explanation_mentions_unavailable(self) -> None:
        from baselines.comparators import run_llm_judge_baseline
        result = run_llm_judge_baseline(self.cases[0], llm_client=None)
        self.assertIn("unavailable", result.explanation.lower())

    def test_fallback_cost_is_point_five(self) -> None:
        from baselines.comparators import run_llm_judge_baseline
        result = run_llm_judge_baseline(self.cases[0], llm_client=None)
        self.assertEqual(result.cost_per_diagnosis, 0.5)


# ── Suite Integration ─────────────────────────────────────────────────


class LLMJudgeSuiteIntegrationTest(unittest.TestCase):
    """llm_judge integrates into BaselineSuiteResult without errors."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)

    def test_suite_result_has_llm_judge_field(self) -> None:
        suite = run_baseline_suite(self.cases[0])
        self.assertIsNotNone(suite.llm_judge)

    def test_comparator_results_includes_llm_judge(self) -> None:
        suite = run_baseline_suite(self.cases[0])
        names = [c.comparator_name for c in suite.comparator_results]
        self.assertIn("llm_judge", names)

    def test_comparator_results_has_four_entries(self) -> None:
        suite = run_baseline_suite(self.cases[0])
        self.assertEqual(len(suite.comparator_results), 4)

    def test_llm_judge_uses_counterfactual_replay_false(self) -> None:
        suite = run_baseline_suite(self.cases[0])
        self.assertFalse(suite.llm_judge.uses_counterfactual_replay)


# ── Isolation ─────────────────────────────────────────────────────────


class LLMJudgeIsolationTest(unittest.TestCase):
    """Each case gets an independent llm_judge diagnosis."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)

    def test_each_case_produces_independent_fallback_result(self) -> None:
        from baselines.comparators import run_llm_judge_baseline
        results = [
            run_llm_judge_baseline(c, llm_client=None) for c in self.cases
        ]
        self.assertEqual(len(results), len(self.cases))
        for result in results:
            self.assertEqual(result.comparator_name, "llm_judge")

    def test_no_cross_case_leakage_in_prompts(self) -> None:
        case_a = self.cases[0]
        case_b = self.cases[1] if len(self.cases) > 1 else self.cases[0]
        prompt_a = build_judge_prompt(case_a)
        prompt_b = build_judge_prompt(case_b)
        self.assertNotIn(case_a.case_id, prompt_b)
        self.assertNotIn(case_b.case_id, prompt_a)
