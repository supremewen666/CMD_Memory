"""Directed entailment divergence computation for item gate reference-contrast.

Implements the "directed entailment divergence" mechanism from DISCUSSION.md decision #4.
This is NOT information-theoretic KL divergence, but judge-as-distribution reading
"m̂_i entails/contradicts m_i degree" score-token distribution expectation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..core.models import MemoryItem
from ..scoring.llm import _continuous_verify, RUBRIC_MAX_SCORE

_logger = logging.getLogger(__name__)


@dataclass
class DirectedDivergence:
    """Directed entailment divergence between two memory items.

    forward_score: m̂_i entails m_i degree (0-4, higher = more entailed)
    reverse_score: m_i entails m̂_i degree (0-4, higher = more entailed)
    forward_divergence: normalized lack of forward entailment (0-1)
    reverse_divergence: normalized lack of reverse entailment (0-1)

    Direction determines item label typing:
    - forward_divergence > reverse_divergence → m̂_i more specific → item_wrong
    - reverse_divergence > forward_divergence → m_i more outdated → item_stale
    """
    forward_score: float  # m̂_i entails m_i
    reverse_score: float  # m_i entails m̂_i
    forward_divergence: float  # 1 - forward_score / RUBRIC_MAX_SCORE
    reverse_divergence: float  # 1 - reverse_score / RUBRIC_MAX_SCORE

    @property
    def max_divergence(self) -> float:
        """Maximum divergence in either direction."""
        return max(self.forward_divergence, self.reverse_divergence)

    @property
    def is_forward_dominant(self) -> bool:
        """True if forward divergence > reverse (m̂_i more specific → wrong)."""
        return self.forward_divergence > self.reverse_divergence

    @property
    def is_reverse_dominant(self) -> bool:
        """True if reverse divergence > forward (m_i more outdated → stale)."""
        return self.reverse_divergence > self.forward_divergence


def compute_directed_divergence(
    client: Any,
    item_a: MemoryItem,
    item_b: MemoryItem,
    *,
    fallback_threshold: float = 0.5,
) -> DirectedDivergence:
    """Compute directed entailment divergence between two memory items.

    Uses _continuous_verify from scoring.llm to get G-Eval logprob scores.
    Falls back to discrete rubric if logprobs unavailable, then to threshold if all fails.

    Args:
        client: LLM client with generate_with_logprobs support
        item_a: First memory item (m_i in the docs)
        item_b: Second memory item (m̂_i in the docs)
        fallback_threshold: Used if both continuous and discrete fail

    Returns:
        DirectedDivergence with forward/reverse scores and normalized divergences
    """
    # Forward: item_b entails item_a (m̂_i entails m_i)
    forward_score = _compute_entailment_score(
        client, item_b.text, item_a.text, fallback_threshold
    )

    # Reverse: item_a entails item_b (m_i entails m̂_i)
    reverse_score = _compute_entailment_score(
        client, item_a.text, item_b.text, fallback_threshold
    )

    return DirectedDivergence(
        forward_score=forward_score,
        reverse_score=reverse_score,
        forward_divergence=_score_to_divergence(forward_score),
        reverse_divergence=_score_to_divergence(reverse_score),
    )


def _score_to_divergence(score: float) -> float:
    entailment = max(0.0, min(1.0, score / RUBRIC_MAX_SCORE))
    return 1.0 - entailment


def _compute_entailment_score(
    client: Any,
    entailing_text: str,
    entailed_text: str,
    fallback_threshold: float,
) -> float:
    """Compute how much entailing_text entails entailed_text.

    Uses the existing rubric infrastructure with FACT/TEXT format.
    entailing_text is treated as TEXT, entailed_text as FACT.

    Returns score in [0, RUBRIC_MAX_SCORE] range.
    """
    del fallback_threshold
    if not client:
        _logger.warning("No LLM client available for entailment scoring")
        return 0.0

    # Use continuous_verify from scoring.llm (G-Eval logprob path)
    try:
        continuous_score = _continuous_verify(
            client, entailed_text, entailing_text
        )
        if continuous_score is not None:
            return continuous_score
    except Exception as exc:
        _logger.debug("Continuous entailment scoring failed: %s", exc)

    _logger.info("Entailment scoring fell back to conservative zero-entailment")
    return 0.0
