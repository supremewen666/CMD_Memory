"""Value function for MCTS nodes using nested ceiling approach.

Implements the nested value function from DISCUSSION.md decision #2:
V_scalar = ceiling(k/N) · (E[score_answer] / 4)

Where:
- k = count of evidence atoms with rubric_B score ≥ threshold
- ceiling = k/N provides evidence-based upper bound
- E[score_answer] provides continuous answer quality within [0, ceiling]
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..core.llm_client import LLMResponse
from ..core.models import GoldEvidence
from ..scoring.llm import (
    RUBRIC_MAX_SCORE,
    RubricParseError,
    _continuous_verify,
    _expected_score_from_logprobs,
    _find_score_digit_logprobs,
    _parse_rubric_output,
    _validate_context_isolation,
)

_logger = logging.getLogger(__name__)


@dataclass
class NestedValue:
    """Nested value function result with evidence ceiling and answer component."""
    evidence_count: int  # k = number of evidence atoms ≥ threshold
    total_evidence: int  # N = total evidence atoms
    evidence_ceiling: float  # k/N
    answer_score: float  # E[score_answer] in [0, RUBRIC_MAX_SCORE]
    scalar_value: float  # Final V_scalar = ceiling · (answer_score / 4)

    # Vector components for repair guidance
    per_atom_scores: list[float]  # Individual rubric_B scores
    answer_continuous: float  # Raw answer score before normalization

    @property
    def evidence_fraction(self) -> float:
        """Fraction of evidence atoms that meet threshold."""
        return self.evidence_ceiling

    @property
    def answer_normalized(self) -> float:
        """Answer score normalized to [0,1]."""
        return self.answer_score / RUBRIC_MAX_SCORE

    @property
    def is_evidence_complete(self) -> bool:
        """True if all evidence atoms meet threshold."""
        return self.evidence_count == self.total_evidence

    @property
    def has_evidence_gaps(self) -> bool:
        """True if some evidence atoms are missing/below threshold."""
        return self.evidence_count < self.total_evidence


class ValueFunction:
    """Nested ceiling value function for MCTS nodes."""

    def __init__(
        self,
        llm_client: Any,
        *,
        evidence_threshold: float = 0.5,
        max_retries: int = 1,
    ):
        self.client = llm_client
        self.evidence_threshold = evidence_threshold
        self.max_retries = max_retries

    def evaluate_node(
        self,
        context: str,
        gold_evidence: tuple[GoldEvidence, ...],
        gold_answer: str,
    ) -> NestedValue:
        """Evaluate a node using nested ceiling value function.

        Args:
            context: Current context at this node (partial trajectory)
            gold_evidence: Ground truth evidence atoms
            gold_answer: Ground truth answer

        Returns:
            NestedValue with scalar value and component scores
        """
        if not gold_evidence:
            return NestedValue(
                evidence_count=0, total_evidence=0, evidence_ceiling=0.0,
                answer_score=0.0, scalar_value=0.0,
                per_atom_scores=[], answer_continuous=0.0
            )

        # Compute evidence component (rubric_B per atom)
        per_atom_scores = self._evaluate_evidence_atoms(context, gold_evidence)

        # Count evidence atoms meeting threshold
        evidence_count = sum(
            1 for score in per_atom_scores
            if score >= (self.evidence_threshold * RUBRIC_MAX_SCORE)
        )

        evidence_ceiling = evidence_count / len(gold_evidence)

        # Compute answer component (rubric_A' for current prefix)
        answer_score = self._evaluate_answer_prefix(context, gold_answer)

        # Nested value: ceiling · (answer / 4)
        scalar_value = evidence_ceiling * (answer_score / RUBRIC_MAX_SCORE)

        return NestedValue(
            evidence_count=evidence_count,
            total_evidence=len(gold_evidence),
            evidence_ceiling=evidence_ceiling,
            answer_score=answer_score,
            scalar_value=scalar_value,
            per_atom_scores=per_atom_scores,
            answer_continuous=answer_score,
        )

    def _evaluate_evidence_atoms(
        self,
        context: str,
        gold_evidence: tuple[GoldEvidence, ...],
    ) -> list[float]:
        """Evaluate each evidence atom against current context.

        Uses rubric_B: per-atom SVO evaluation (0-4 scale).
        """
        scores = []

        for evidence_item in gold_evidence:
            try:
                # Use continuous_verify for G-Eval scoring
                score = _continuous_verify(
                    self.client, evidence_item.text, context
                )
                if score is not None:
                    scores.append(score)
                else:
                    _logger.debug("Evidence scoring fell back to conservative zero")
                    scores.append(0.0)
            except Exception as exc:
                _logger.warning("Evidence atom scoring failed: %s", exc)
                scores.append(0.0)

        return scores

    def _evaluate_answer_prefix(self, context: str, gold_answer: str) -> float:
        """Evaluate how much current context entails the gold answer.

        Uses rubric_A': tests "current prefix already contains gold answer degree"
        rather than "will future answer be correct".
        """
        return _score_answer_prefix(self.client, context, gold_answer)


_ANSWER_PREFIX_RUBRIC_SYSTEM_PROMPT = """\
TASK: Rate how much the CURRENT PREFIX already entails the GOLD ANSWER, on a 0-4 scale.

RUBRIC ANCHORS:

  0 = ABSENT or CONTRADICTED. The prefix does not contain the answer, or it
      contradicts the gold answer.

  1 = VAGUE. The prefix points to the right topic but includes no specific
      answer fact.

  2 = PARTIAL. The prefix contains some answer facts but misses an important
      claim, entity, value, or relation.

  3 = MOSTLY ENTAILED. The prefix contains the core answer with only minor
      omissions or wording differences.

  4 = FULLY ENTAILED. The prefix already contains the gold answer facts.

GUIDANCE:
  - Judge only what is already entailed by CURRENT PREFIX.
  - Do not predict whether a future generation will answer correctly.
  - Contradictions lower the score.
  - When uncertain between adjacent levels, choose the lower one.

OUTPUT: A single JSON object with this exact shape, no prose around it:
  {"reasoning": "<one short sentence>", "score": <integer 0..4>}"""


def _score_answer_prefix(client: Any, context: str, gold_answer: str) -> float:
    """Score rubric_A-prime for the current prefix; returns 0..4."""
    if not client or not context or not gold_answer:
        return 0.0

    expected = _continuous_verify_answer_prefix(client, context, gold_answer)
    if expected is not None:
        return expected

    user_message = f"CURRENT PREFIX:\n  {context}\n\nGOLD ANSWER:\n  {gold_answer}"
    _validate_context_isolation(user_message)
    try:
        response = client.generate(
            user_message,
            system=_ANSWER_PREFIX_RUBRIC_SYSTEM_PROMPT,
        )
        return float(_parse_rubric_output(response))
    except (RubricParseError, Exception) as exc:
        _logger.warning("Answer prefix scoring failed: %s", exc)
        return 0.0


def _continuous_verify_answer_prefix(
    client: Any,
    context: str,
    gold_answer: str,
    *,
    top_logprobs: int = 10,
) -> float | None:
    if not hasattr(client, "generate_with_logprobs"):
        return None

    user_message = f"CURRENT PREFIX:\n  {context}\n\nGOLD ANSWER:\n  {gold_answer}"
    _validate_context_isolation(user_message)
    try:
        response = client.generate_with_logprobs(
            user_message,
            system=_ANSWER_PREFIX_RUBRIC_SYSTEM_PROMPT,
            top_logprobs=top_logprobs,
        )
    except Exception as exc:
        _logger.warning("Answer prefix logprob call failed: %s", exc)
        return None

    if not isinstance(response, LLMResponse) or not response.token_logprobs:
        return None
    digits = _find_score_digit_logprobs(response.token_logprobs)
    if not digits:
        return None
    try:
        return _expected_score_from_logprobs(digits)
    except ValueError:
        return None


def compute_node_value(
    client: Any,
    context: str,
    gold_evidence: tuple[GoldEvidence, ...],
    gold_answer: str,
    *,
    evidence_threshold: float = 0.5,
) -> NestedValue:
    """Convenience function to compute node value.

    Args:
        client: LLM client
        context: Node context
        gold_evidence: Evidence atoms
        gold_answer: Gold answer
        evidence_threshold: Minimum score for evidence atoms

    Returns:
        NestedValue result
    """
    value_function = ValueFunction(client, evidence_threshold=evidence_threshold)
    return value_function.evaluate_node(context, gold_evidence, gold_answer)
