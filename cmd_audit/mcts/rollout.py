"""MCTS rollout to terminal states for step-level attribution.

Implements rollout from intermediate nodes to terminal states using identity
completion, then evaluates terminal answer quality for back-propagation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..core.models import GoldEvidence, MemoryItem
from ..scoring.llm import score_answer_with_verifier
from .actions import PipelineAction, apply_pipeline_action

_logger = logging.getLogger(__name__)


@dataclass
class RolloutResult:
    """Result of rolling out from a node to terminal state."""
    terminal_context: str
    terminal_answer: str
    recovery_gain: float  # Terminal answer score; credit subtracts identity baseline.
    rollout_successful: bool
    generation_points_completed: int

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
        )

    try:
        # Complete trajectory with identity actions
        current_context = start_context
        current_generation_point = start_generation_point

        while current_generation_point < max_generation_points:
            # Apply identity (no intervention) for remaining points
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
        )

    except Exception as exc:
        _logger.error("Rollout failed: %s", exc)
        return RolloutResult(
            terminal_context=start_context,
            terminal_answer="",
            recovery_gain=0.0,
            rollout_successful=False,
            generation_points_completed=0,
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
    subtracted later by MCTS credit assignment.

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

    except Exception as exc:
        _logger.warning("Recovery gain computation failed: %s", exc)
        return 0.0


def rollout_with_early_stopping(
    client: Any,
    start_context: str,
    start_generation_point: int,
    max_generation_points: int,
    recall_set: tuple[MemoryItem, ...],
    gold_answer: str,
    *,
    answer_verifier: Any = None,
    baseline_answer_score: float = 0.0,
    recovery_threshold: float = 0.8,
) -> RolloutResult:
    """Rollout with early stopping on recovery achievement.

    Implements "shallowest recovery depth" stopping rule from DISCUSSION.md:
    if single-point intervention already recovers, stop expansion.

    Args:
        client: LLM client
        start_context: Starting context
        start_generation_point: Starting generation point
        max_generation_points: Maximum depth
        recall_set: Memory items
        gold_answer: Ground truth
        answer_verifier: Answer verifier
        recovery_threshold: Threshold for "recovered" classification

    Returns:
        RolloutResult with potential early stopping
    """
    # Check if current state is already recovered
    if start_generation_point == 1:  # Depth-1 intervention
        intermediate_answer = _generate_terminal_answer(client, start_context)
        if intermediate_answer:
            recovery_score = _compute_recovery_gain(
                intermediate_answer,
                gold_answer,
                answer_verifier,
                baseline_answer_score=baseline_answer_score,
            )

            if recovery_score >= recovery_threshold:
                _logger.debug("Early stopping: depth-1 recovery achieved")
                return RolloutResult(
                    terminal_context=start_context,
                    terminal_answer=intermediate_answer,
                    recovery_gain=recovery_score,
                    rollout_successful=True,
                    generation_points_completed=0,  # Early stop
                )

    # Continue with full rollout if no early recovery
    return rollout_to_terminal(
        client,
        start_context,
        start_generation_point,
        max_generation_points,
        recall_set,
        gold_answer,
        answer_verifier=answer_verifier,
        baseline_answer_score=baseline_answer_score,
    )
