"""Behavior-level tests for issue 0019 Phase B: Subagent-based LLM Scoring."""

from __future__ import annotations

from pathlib import Path
import unittest

from cmd_audit import (
    AnswerVerifier,
    ContextLeakError,
    EvidenceVerifier,
    OutputFormatError,
    SubagentScorer,
    load_probe_cases,
)
from cmd_audit.core.models import GoldEvidence
from cmd_audit.scoring.llm import (
    _validate_context_isolation,
    _validate_output_format,
    _phrase_fallback,
)

V0_SMOKE = Path("data/probe_cases/v0_issue3_cases.json")


# ── Context Isolation ──────────────────────────────────────────────────


class ContextIsolationTest(unittest.TestCase):
    """_validate_context_isolation rejects forbidden tokens."""

    def test_clean_prompt_passes(self) -> None:
        _validate_context_isolation("FACT: 6S algorithm\nTEXT: SIAC_GEE toolbox")

    def test_rejects_case_id(self) -> None:
        with self.assertRaises(ContextLeakError):
            _validate_context_isolation("case_id: v0-write-001")

    def test_rejects_gold_label(self) -> None:
        with self.assertRaises(ContextLeakError):
            _validate_context_isolation("gold_label=write_error")

    def test_rejects_perturbation_type(self) -> None:
        with self.assertRaises(ContextLeakError):
            _validate_context_isolation("perturbation_type: write_error")

    def test_rejects_perturbation_label(self) -> None:
        with self.assertRaises(ContextLeakError):
            _validate_context_isolation("perturbation_label=compression_error")

    def test_rejects_gold_answer(self) -> None:
        with self.assertRaises(ContextLeakError):
            _validate_context_isolation("gold_answer: some answer")

    def test_rejects_gold_evidence(self) -> None:
        with self.assertRaises(ContextLeakError):
            _validate_context_isolation("gold_evidence present")

    def test_rejects_ptype(self) -> None:
        with self.assertRaises(ContextLeakError):
            _validate_context_isolation("ptype: write_error")

    def test_rejects_cross_case(self) -> None:
        with self.assertRaises(ContextLeakError):
            _validate_context_isolation("cross_case: v0-compression-002")

    def test_rejects_other_case(self) -> None:
        with self.assertRaises(ContextLeakError):
            _validate_context_isolation("other_case data")

    def test_violations_listed_in_error(self) -> None:
        with self.assertRaises(ContextLeakError) as ctx:
            _validate_context_isolation("gold_label=retrieval_error\nFACT: test")
        self.assertIn("gold_label", str(ctx.exception))


# ── Output Format ──────────────────────────────────────────────────────


class OutputFormatValidationTest(unittest.TestCase):
    """_validate_output_format normalises binary subagent outputs."""

    def test_accepts_present(self) -> None:
        self.assertEqual(_validate_output_format("PRESENT", kind="evidence"), "PRESENT")

    def test_accepts_absent(self) -> None:
        self.assertEqual(_validate_output_format("ABSENT", kind="evidence"), "ABSENT")

    def test_accepts_present_with_extra(self) -> None:
        self.assertEqual(
            _validate_output_format("PRESENT - fact matches", kind="evidence"),
            "PRESENT",
        )

    def test_accepts_lowercase(self) -> None:
        self.assertEqual(_validate_output_format("present", kind="evidence"), "PRESENT")

    def test_accepts_whitespace(self) -> None:
        self.assertEqual(
            _validate_output_format("  PRESENT  ", kind="evidence"), "PRESENT"
        )

    def test_rejects_non_binary(self) -> None:
        with self.assertRaises(OutputFormatError):
            _validate_output_format("The fact is partially present.", kind="evidence")

    def test_rejects_empty(self) -> None:
        with self.assertRaises(OutputFormatError):
            _validate_output_format("", kind="evidence")

    def test_accepts_equivalent(self) -> None:
        self.assertEqual(
            _validate_output_format("EQUIVALENT", kind="answer"), "EQUIVALENT"
        )

    def test_accepts_not_equivalent(self) -> None:
        self.assertEqual(
            _validate_output_format("NOT_EQUIVALENT", kind="answer"), "NOT_EQUIVALENT"
        )

    def test_rejects_non_binary_answer(self) -> None:
        with self.assertRaises(OutputFormatError):
            _validate_output_format("maybe equivalent", kind="answer")


# ── EvidenceVerifier Boundary ──────────────────────────────────────────


class EvidenceVerifierBoundaryTest(unittest.TestCase):
    """EvidenceVerifier user message must NOT contain gold data."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)

    def test_user_message_rejects_gold_label_token(self) -> None:
        case = self.cases[0]
        with self.assertRaises(ContextLeakError):
            _validate_context_isolation(
                f"gold_label: {case.perturbation_label}\nFACT: test"
            )

    def test_user_message_rejects_case_id_token(self) -> None:
        case = self.cases[0]
        with self.assertRaises(ContextLeakError):
            _validate_context_isolation(f"case_id: {case.case_id}\nFACT: test")


# ── EvidenceVerifier Fallback ──────────────────────────────────────────


class EvidenceVerifierFallbackTest(unittest.TestCase):
    """EvidenceVerifier degrades when LLM unavailable."""

    def test_none_client_returns_absent(self) -> None:
        verifier = EvidenceVerifier(None)
        self.assertEqual(verifier.verify("fact", "text with fact"), "ABSENT")

    def test_empty_fact_returns_absent_with_none_client(self) -> None:
        verifier = EvidenceVerifier(None)
        self.assertEqual(verifier.verify("", "some text"), "ABSENT")


# ── AnswerVerifier Fallback ────────────────────────────────────────────


class AnswerVerifierFallbackTest(unittest.TestCase):
    """AnswerVerifier degrades when LLM unavailable."""

    def test_none_client_returns_not_equivalent(self) -> None:
        verifier = AnswerVerifier(None)
        self.assertEqual(verifier.verify("A", "B"), "NOT_EQUIVALENT")


# ── SubagentScorer Fallback ────────────────────────────────────────────


class SubagentScorerFallbackTest(unittest.TestCase):
    """SubagentScorer falls back to phrase-matching when LLM is None."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)

    def test_scorer_without_llm_uses_phrase_fallback(self) -> None:
        scorer = SubagentScorer(None)
        case = self.cases[0]
        evidence = case.gold_evidence
        text = "\n".join(ev.text for ev in evidence)
        score = scorer.score_evidence(evidence, text)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_empty_evidence_returns_zero(self) -> None:
        scorer = SubagentScorer(None)
        self.assertEqual(scorer.score_evidence((), "text"), 0.0)

    def test_empty_text_returns_zero(self) -> None:
        scorer = SubagentScorer(None)
        case = self.cases[0]
        self.assertEqual(scorer.score_evidence(case.gold_evidence, ""), 0.0)

    def test_call_matches_score_evidence(self) -> None:
        scorer = SubagentScorer(None)
        case = self.cases[0]
        evidence = case.gold_evidence
        text = "\n".join(ev.text for ev in evidence)
        self.assertEqual(scorer(evidence, text), scorer.score_evidence(evidence, text))


# ── SubagentScorer Contract ────────────────────────────────────────────


class SubagentScorerContractTest(unittest.TestCase):
    """SubagentScorer implements scorer contract."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)

    def test_returns_float_in_zero_one(self) -> None:
        scorer = SubagentScorer(None)
        case = self.cases[0]
        evidence = case.gold_evidence
        text = "\n".join(ev.text for ev in evidence)
        score = scorer(evidence, text)
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_phrase_fallback_matches_on_exact_text(self) -> None:
        case = self.cases[0]
        evidence = case.gold_evidence
        text = "\n".join(ev.text for ev in evidence)
        score = _phrase_fallback(evidence, text)
        self.assertGreater(score, 0.0)

    def test_phrase_fallback_zero_when_no_match(self) -> None:
        evidence = (GoldEvidence(evidence_id="ev-1", text="completely absent fact", source_memory_id=None),)
        self.assertEqual(_phrase_fallback(evidence, "unrelated text"), 0.0)


# ── SubagentScorer Edge Cases ──────────────────────────────────────────


class SubagentScorerEdgeCaseTest(unittest.TestCase):
    """Edge case behavior for SubagentScorer."""

    def test_single_evidence_present(self) -> None:
        evidence = (GoldEvidence(evidence_id="ev-1", text="hello world", source_memory_id=None),)
        scorer = SubagentScorer(None)
        self.assertEqual(scorer.score_evidence(evidence, "hello world"), 1.0)

    def test_single_evidence_absent(self) -> None:
        evidence = (GoldEvidence(evidence_id="ev-1", text="hello world", source_memory_id=None),)
        scorer = SubagentScorer(None)
        self.assertEqual(scorer.score_evidence(evidence, "goodbye universe"), 0.0)

    def test_mixed_evidence(self) -> None:
        evidence = (
            GoldEvidence(evidence_id="ev-1", text="fact one", source_memory_id=None),
            GoldEvidence(evidence_id="ev-2", text="fact two", source_memory_id=None),
        )
        scorer = SubagentScorer(None)
        score = scorer.score_evidence(evidence, "fact one is here")
        self.assertEqual(score, 0.5)

    def test_max_workers_configurable(self) -> None:
        scorer = SubagentScorer(None, max_workers=3)
        self.assertEqual(scorer._max_workers, 3)


# ── Exception Hierarchy ────────────────────────────────────────────────


class ExceptionHierarchyTest(unittest.TestCase):
    """Phase B exceptions are ValueError subclasses."""

    def test_context_leak_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(ContextLeakError, ValueError))

    def test_output_format_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(OutputFormatError, ValueError))
