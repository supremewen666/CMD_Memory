"""Roll out counterfactual contexts to terminal answers."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..core.llm_client import LLMTimeoutError
from ..core.models import GoldEvidence, MemoryItem
from ..scoring.llm import score_answer_with_verifier
from .actions import PipelineAction, apply_pipeline_action

_logger = logging.getLogger(__name__)


@dataclass
class RolloutResult:
    """Result of rolling out from a node to terminal state.

    ``status`` distinguishes *why* a rollout did not complete normally:
    ``"ok"`` (successful), ``"timeout"`` (an ``LLMTimeoutError`` occurred;
    ``recovery_gain`` is ``NaN`` so it can never win a maximisation and is
    excluded from mean/rate aggregation), ``"error"`` (any other exception),
    ``"no_client"`` (no LLM client was supplied), or ``"empty_answer"``
    (terminal answer generation returned nothing). The invariant
    ``rollout_successful == (status == "ok")`` always holds.
    """
    terminal_context: str
    terminal_answer: str
    recovery_gain: float  # Terminal answer score; credit subtracts identity baseline.
    rollout_successful: bool
    generation_points_completed: int
    status: str = "ok"

    @property
    def is_recovered(self) -> bool:
        """True if rollout achieved significant recovery."""
        return self.recovery_gain > 0.1  # Configurable threshold


def rollout_to_terminal(
    client: Any,
    start_context: str,
    start_generation_point: int,
    max_generation_points: int,
    recall_set: tuple[MemoryItem, ...],
    gold_answer: str,
    *,
    answer_verifier: Any = None,
    baseline_answer_score: float = 0.0,
    completion_strategy: str = "identity",
) -> RolloutResult:
    """Rollout from current state to terminal answer.

    Applies identity actions for remaining generation points, then
    generates final answer and computes recovery gain.

    Args:
        client: LLM client for generation
        start_context: Context at rollout start point
        start_generation_point: Current generation point
        max_generation_points: Maximum depth to roll out to
        recall_set: Available memory items
        gold_answer: Ground truth answer for scoring
        answer_verifier: Verifier for terminal answer evaluation
        completion_strategy: How to complete remaining steps ("identity" only for now)

    Returns:
        RolloutResult with terminal evaluation
    """
    if not client:
        _logger.warning("No LLM client for rollout")
        return RolloutResult(
            terminal_context=start_context,
            terminal_answer="",
            recovery_gain=0.0,
            rollout_successful=False,
            generation_points_completed=0,
            status="no_client",
        )

    try:
        # Complete the trajectory with a single identity step. Depth is
        # pinned to one generation point (memory is recalled once per turn;
        # see REFACTOR_SPEC_SINGLE_POINT.md §0), so at most one step of
        # identity padding can ever be needed here.
        current_context = start_context
        current_generation_point = start_generation_point

        if current_generation_point < max_generation_points:
            current_context = apply_pipeline_action(
                PipelineAction.IDENTITY,
                current_context,
                recall_set,
                current_generation_point,
            )
            current_generation_point += 1

        # Generate terminal answer
        terminal_answer = _generate_terminal_answer(client, current_context)

        # If terminal answer generation failed, mark rollout as unsuccessful
        if not terminal_answer:
            return RolloutResult(
                terminal_context=current_context,
                terminal_answer="",
                recovery_gain=0.0,
                rollout_successful=False,
                generation_points_completed=current_generation_point - start_generation_point,
                status="empty_answer",
            )

        # Compute terminal answer score. Baseline subtraction happens only in
        # tree credit assignment against the identity sibling.
        recovery_gain = _compute_recovery_gain(
            terminal_answer,
            gold_answer,
            answer_verifier,
            baseline_answer_score=baseline_answer_score,
        )

        return RolloutResult(
            terminal_context=current_context,
            terminal_answer=terminal_answer,
            recovery_gain=recovery_gain,
            rollout_successful=True,
            generation_points_completed=current_generation_point - start_generation_point,
            status="ok",
        )

    except LLMTimeoutError as exc:
        _logger.warning("Rollout timed out: %s", exc)
        return RolloutResult(
            terminal_context=start_context,
            terminal_answer="",
            recovery_gain=float("nan"),
            rollout_successful=False,
            generation_points_completed=0,
            status="timeout",
        )

    except Exception as exc:
        _logger.error("Rollout failed: %s", exc)
        return RolloutResult(
            terminal_context=start_context,
            terminal_answer="",
            recovery_gain=0.0,
            rollout_successful=False,
            generation_points_completed=0,
            status="error",
        )


def _generate_terminal_answer(client: Any, terminal_context: str) -> str:
    """Generate final answer from terminal context.

    Args:
        client: LLM client
        terminal_context: Complete context after all interventions

    Returns:
        Generated answer string
    """
    if not terminal_context.strip():
        return ""

    try:
        # Simple prompt to extract answer from context
        prompt = f"""Based on the following context, provide a direct answer to the question.

Context:
{terminal_context}

Answer:"""

        response = client.generate(prompt)
        return response.strip() if response else ""

    except LLMTimeoutError:
        # Let timeouts propagate to rollout_to_terminal's outer handler so
        # they are recorded as status="timeout" (NaN), not laundered into
        # the empty_answer path (0.0).
        raise

    except Exception as exc:
        _logger.warning("Terminal answer generation failed: %s", exc)
        return ""


def _compute_recovery_gain(
    terminal_answer: str,
    gold_answer: str,
    answer_verifier: Any,
    *,
    baseline_answer_score: float = 0.0,
) -> float:
    """Compute recovery gain from terminal answer quality.

    Recovery gain = terminal AnswerVerifier score. The identity sibling is
    subtracted later by step-level credit assignment.

    Args:
        terminal_answer: Generated answer from rollout
        gold_answer: Ground truth answer
        answer_verifier: Verifier for answer scoring

    Returns:
        Recovery gain score [0, 1]
    """
    del baseline_answer_score
    if not terminal_answer or not gold_answer:
        return 0.0

    try:
        # Score terminal answer
        terminal_score = score_answer_with_verifier(
            answer_verifier, terminal_answer, gold_answer
        )

        return max(0.0, min(1.0, terminal_score))

    except LLMTimeoutError:
        # Propagate so rollout_to_terminal's outer handler records timeout
        # status distinctly from a generic scoring error.
        raise

    except Exception as exc:
        _logger.warning("Recovery gain computation failed: %s", exc)
        return 0.0
