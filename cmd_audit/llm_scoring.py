"""Subagent-based LLM scoring replacing phrase-matching (Issue 0019 Phase B).

Binary atomic subagent judgments — continuous scores emerge from aggregation.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from typing import Any, Callable

from typing import Literal

from .models import GoldEvidence
from .scoring import evidence_recall_from_text

_logger = logging.getLogger(__name__)

# ── Prompts ────────────────────────────────────────────────────────────

_EVIDENCE_SYSTEM_PROMPT = """\
TASK: Verify whether the fact exists in the given text.

DECISION RULES:

  PRESENT — The core propositional content of FACT is communicated by TEXT:
    - Paraphrase or reworded expression with same meaning
    - Abbreviation or expansion preserving the core claims
    - Fact embedded within a larger passage
    - Different ordering of the same claims

  ABSENT — The core proposition is NOT communicated:
    - Fact completely missing from TEXT
    - TEXT mentions a related but different concept (not the same proposition)
    - TEXT directly contradicts the fact
    - Only tangential mention; proposition not actually established
    - UNCERTAIN / BOUNDARY CASE -> ABSENT (conservative tie-break)

  To resolve boundary cases, extract the core subject-verb-object triple
  from FACT and check whether TEXT preserves that same triple.

OUTPUT: PRESENT | ABSENT"""

_ANSWER_SYSTEM_PROMPT = """\
TASK: Verify whether two answers are semantically equivalent.

DECISION RULES:

  EQUIVALENT — Both answers communicate the same factual information:
    - Different word choice, same meaning
    - Non-contradictory extra details in either answer
    - Different presentation order of the same facts
    - Abbreviation or expansion of the same claims

  NOT_EQUIVALENT — The answers differ in factual content:
    - Core fact(s) missing from ANSWER
    - ANSWER contains information contradicting GOLD ANSWER
    - Extra details change the factual meaning of the original
    - UNCERTAIN / BOUNDARY CASE -> NOT_EQUIVALENT (conservative tie-break)

OUTPUT: EQUIVALENT | NOT_EQUIVALENT"""

# ── Forbidden context tokens ───────────────────────────────────────────

_FORBIDDEN_CONTEXT_TOKENS = (
    "case_id",
    "gold_label",
    "perturbation_type",
    "perturbation_label",
    "gold_answer",
    "gold_evidence",
    "ptype",
    "cross_case",
    "other_case",
)

# ── Exceptions ─────────────────────────────────────────────────────────


class ContextLeakError(ValueError):
    """Raised when a subagent prompt contains forbidden context data."""


class OutputFormatError(ValueError):
    """Raised when a subagent response is not a valid binary output."""


# ── Validation ─────────────────────────────────────────────────────────


def _validate_context_isolation(prompt: str) -> None:
    """Reject prompts that leak gold labels, case metadata, or cross-case data."""
    lowered = prompt.casefold()
    violations = [tok for tok in _FORBIDDEN_CONTEXT_TOKENS if tok in lowered]
    if violations:
        raise ContextLeakError(
            f"Subagent prompt contains forbidden tokens: {', '.join(violations)}"
        )


def _validate_output_format(
    response: str, *, kind: Literal["evidence", "answer"] = "evidence"
) -> str:
    """Validate and normalise binary subagent output.

    Returns canonical form: ``"PRESENT"`` or ``"ABSENT"`` for evidence
    verdicts; ``"EQUIVALENT"`` or ``"NOT_EQUIVALENT"`` for answer verdicts.
    """
    stripped = response.strip().upper()

    if kind == "evidence":
        if stripped.startswith("PRESENT"):
            return "PRESENT"
        if stripped.startswith("ABSENT"):
            return "ABSENT"
        valid = ("PRESENT", "ABSENT")
    else:
        if stripped.startswith("EQUIVALENT"):
            return "EQUIVALENT"
        if stripped.startswith("NOT_EQUIVALENT"):
            return "NOT_EQUIVALENT"
        valid = ("EQUIVALENT", "NOT_EQUIVALENT")

    raise OutputFormatError(
        f"Expected one of {valid}, got: {response!r}"
    )


# ── Verifiers ──────────────────────────────────────────────────────────


class EvidenceVerifier:
    """Binary atomic verifier for a single evidence fact against a text.

    Each :meth:`verify` call is one subagent invocation: receives
    ``{FACT, TEXT}`` as user message and fixed decision rules as system
    prompt.  Returns ``"PRESENT"`` or ``"ABSENT"``.
    """

    def __init__(self, llm_client: Any, *, max_retries: int = 1) -> None:
        self._client = llm_client
        self._max_retries = max_retries

    @property
    def is_available(self) -> bool:
        """True when an LLM client is configured and ready."""
        return self._client is not None

    def verify(self, fact: str, text: str) -> str:
        """Run one atomic evidence verification.

        On LLM failure falls back to ``"ABSENT"`` (conservative tie-break).
        On output format failure retries once with stricter prompt before
        falling back.
        """
        user_message = f"FACT:\n  {fact}\n\nTEXT:\n  {text}"
        _validate_context_isolation(user_message)

        try:
            response = self._client.generate(
                user_message, system=_EVIDENCE_SYSTEM_PROMPT
            )
        except Exception as exc:
            _logger.warning("EvidenceVerifier LLM call failed: %s", exc)
            return "ABSENT"

        for attempt in range(self._max_retries + 1):
            try:
                return _validate_output_format(response, kind="evidence")
            except OutputFormatError:
                if attempt < self._max_retries:
                    _logger.debug(
                        "EvidenceVerifier parse failed, retrying: %r", response
                    )
                    retry_msg = (
                        user_message
                        + "\n\nOUTPUT ONLY THE SINGLE WORD PRESENT OR ABSENT. NO OTHER TEXT."
                    )
                    try:
                        response = self._client.generate(
                            retry_msg, system=_EVIDENCE_SYSTEM_PROMPT
                        )
                    except Exception as exc:
                        _logger.warning(
                            "EvidenceVerifier retry LLM call failed: %s", exc
                        )

        _logger.warning(
            "EvidenceVerifier exhausted retries. Last response: %r", response
        )
        return "ABSENT"


class AnswerVerifier:
    """Binary atomic verifier for answer equivalence.

    Used only at Post-Repair Context Replay validation — not in the
    attribution loop.
    """

    def __init__(self, llm_client: Any, *, max_retries: int = 1) -> None:
        self._client = llm_client
        self._max_retries = max_retries

    def verify(self, answer: str, gold_answer: str) -> str:
        """Run one atomic answer equivalence check.

        Returns ``"EQUIVALENT"`` or ``"NOT_EQUIVALENT"``.
        """
        user_message = (
            f"ANSWER:\n  {answer}\n\nGOLD ANSWER:\n  {gold_answer}"
        )
        _validate_context_isolation(user_message)

        try:
            response = self._client.generate(
                user_message, system=_ANSWER_SYSTEM_PROMPT
            )
        except Exception as exc:
            _logger.warning("AnswerVerifier LLM call failed: %s", exc)
            return "NOT_EQUIVALENT"

        for attempt in range(self._max_retries + 1):
            try:
                return _validate_output_format(response, kind="answer")
            except OutputFormatError:
                if attempt < self._max_retries:
                    retry_msg = (
                        user_message
                        + "\n\nOUTPUT ONLY THE SINGLE WORD EQUIVALENT OR NOT_EQUIVALENT. NO OTHER TEXT."
                    )
                    try:
                        response = self._client.generate(
                            retry_msg, system=_ANSWER_SYSTEM_PROMPT
                        )
                    except Exception as exc:
                        _logger.warning(
                            "AnswerVerifier retry LLM call failed: %s", exc
                        )

        _logger.warning(
            "AnswerVerifier exhausted retries. Last response: %r", response
        )
        return "NOT_EQUIVALENT"


# ── Scorer ─────────────────────────────────────────────────────────────


class SubagentScorer:
    """Subagent-based evidence scoring — replaces phrase-matching.

    Implements the scorer contract ``(gold_evidence, text) -> float`` so it
    can be passed as the ``scorer`` parameter to
    :func:`_score_recovered_evidence`.

    Evidence items are evaluated in parallel via :class:`ThreadPoolExecutor`.
    """

    def __init__(
        self,
        llm_client: Any,
        *,
        max_workers: int = 5,
        max_retries: int = 1,
        fallback_scorer: Callable[[tuple[GoldEvidence, ...], str], float]
        | None = None,
    ) -> None:
        self._verifier = EvidenceVerifier(llm_client, max_retries=max_retries)
        self._max_workers = max_workers
        self._fallback = fallback_scorer

    def score_evidence(
        self, gold_evidence: tuple[GoldEvidence, ...], text: str
    ) -> float:
        """Score evidence presence in text using parallel subagent calls.

        Returns fraction of evidence items judged PRESENT, a float in [0,1].
        """
        if not gold_evidence:
            return 0.0
        if not text:
            return 0.0
        if not self._verifier.is_available:
            _logger.info("SubagentScorer: LLM client unavailable, using fallback")
            if self._fallback is not None:
                return self._fallback(gold_evidence, text)
            return evidence_recall_from_text(gold_evidence, text)

        facts = [ev.text for ev in gold_evidence]

        try:
            with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
                futures = {
                    pool.submit(self._score_one, i, fact, text): i
                    for i, fact in enumerate(facts)
                }
                verdicts: list[str | None] = [None for _ in facts]
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        verdicts[idx] = future.result()
                    except Exception as exc:
                        _logger.warning(
                            "Subagent evidence item %d failed: %s", idx, exc
                        )
                        verdicts[idx] = "ABSENT"

                present = sum(1 for v in verdicts if v == "PRESENT")
                return present / len(facts)
        except Exception as exc:
            _logger.warning(
                "SubagentScorer parallel execution failed: %s", exc
            )
            if self._fallback is not None:
                return self._fallback(gold_evidence, text)
            return evidence_recall_from_text(gold_evidence, text)

    def __call__(
        self, gold_evidence: tuple[GoldEvidence, ...], text: str
    ) -> float:
        """Scorer contract — drop-in replacement for phrase-matching."""
        return self.score_evidence(gold_evidence, text)

    def _score_one(self, index: int, fact: str, text: str) -> str:
        return self._verifier.verify(fact, text)


def _phrase_fallback(
    gold_evidence: tuple[GoldEvidence, ...], text: str
) -> float:
    """Deterministic phrase-matching — delegates to canonical scorer."""
    return evidence_recall_from_text(gold_evidence, text)
