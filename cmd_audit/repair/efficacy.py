"""Gold-free repair execution core (CMD diagnosis -> repair -> recovery).

The repair loop's executable spine: given a CMD-predicted step-action label,
map it to its ``(generation_point, PipelineAction)``, construct a repaired
context that is a pure function of ``(recall_set, action)`` via
``apply_pipeline_action`` (never reading ``case.gold_evidence`` /
``case.gold_answer``), roll out to a terminal answer, and return the absolute
recovery gain.

Scoring (answer/evidence) legitimately uses gold; only *construction* is
gold-free — a recovered answer cannot come from copying gold into the context.

This module holds only what the production repair path needs. Experiment-only
scaffolding (random/no-repair comparison arms, multi-arm scheduling, net-gain
bookkeeping) lives in ``experiments/run_experiment_14_repair_efficacy.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core.models import MemoryItem, ProbeCase
from ..counterfactual.actions import (
    PipelineAction,
    apply_pipeline_action,
    get_legal_actions,
)
from ..counterfactual.rollout import rollout_to_terminal

# label string <-> PipelineAction: the 5 step actions share the enum's values.
LABEL_TO_ACTION = {action.value: action for action in PipelineAction}


def _consumer_gain(result: Any) -> float:
    """Shared status->gain mapping for RolloutResult consumers.

    timeout -> NaN (excluded from aggregation, never wins a maximisation);
    any other non-ok status -> 0.0 (unchanged prior behavior); ok -> the
    rollout's recovery_gain.
    """
    if result.status == "timeout":
        return float("nan")
    return result.recovery_gain if result.rollout_successful else 0.0


@dataclass(frozen=True)
class RepairResult:
    """Outcome of one gold-free repair execution on one case.

    ``recovery_gain`` is the ABSOLUTE terminal answer score (rollout does not
    subtract any baseline — see ``rollout._compute_recovery_gain``). Baseline
    subtraction / net-gain bookkeeping is the caller's concern. On a rollout
    timeout, ``recovery_gain`` is ``NaN`` (mirrors ``RolloutResult.status``)
    so it is excluded from mean/rate aggregation and can never win a
    maximisation; ``status`` carries the reason string.
    """

    case_id: str
    selected_label: str | None  # None when no action was applied
    selected_action: str | None
    generation_point: int | None
    recovery_gain: float  # absolute terminal score
    status: str = "ok"


def select_label_cmd(
    label: str,
    recall_set: tuple[MemoryItem, ...],
    max_depth: int,
    *,
    gen_point: int | None = None,
) -> tuple[int, PipelineAction] | None:
    """Map a CMD-predicted label to its ``(generation_point, action)``.

    When ``gen_point`` is given (``main_culprit`` locates the generation
    point, not just the action), honor it — dropping it and re-picking gp=0
    structurally cripples the repair, since the same action only recovers at
    the hop that owns the failure. Falls back to the first legal generation
    point when no gen_point is supplied or it is illegal there.
    """
    action = LABEL_TO_ACTION.get(label)
    if action is None or action == PipelineAction.IDENTITY:
        return None
    if gen_point is not None and 0 <= gen_point < max_depth:
        if action in get_legal_actions(recall_set, gen_point):
            return (gen_point, action)
    for gp in range(max_depth):
        if action in get_legal_actions(recall_set, gp):
            return (gp, action)
    return None


def run_single_repair(
    case: ProbeCase,
    choice: tuple[int, PipelineAction] | None,
    *,
    client: Any,
    answer_verifier: Any,
    base_context: str,
    recall_set: tuple[MemoryItem, ...],
    max_depth: int,
    intervention_config: dict[str, Any] | None = None,
) -> RepairResult:
    """Execute one repair: construct gold-free context for ``choice``, roll out,
    score the absolute recovery gain.

    ``choice`` is a ``(generation_point, action)`` pair (e.g. from
    ``select_label_cmd``), or ``None`` to roll out the base context unchanged
    (the identity backbone — no repair applied).
    """
    baseline = case.primary_baseline.answer_score

    if choice is None:
        result = rollout_to_terminal(
            client, base_context, 0, max_depth, recall_set, case.gold_answer,
            answer_verifier=answer_verifier, baseline_answer_score=baseline,
        )
        gain = _consumer_gain(result)
        return RepairResult(
            case_id=case.case_id, selected_label=None,
            selected_action=None, generation_point=None,
            recovery_gain=gain, status=result.status,
        )

    gen_point, action = choice
    # Gold-free construction: context is a pure function of (recall, action).
    repaired = apply_pipeline_action(
        action, base_context, recall_set, gen_point,
        intervention_config=intervention_config,
    )
    result = rollout_to_terminal(
        client, repaired, gen_point + 1, max_depth, recall_set, case.gold_answer,
        answer_verifier=answer_verifier, baseline_answer_score=baseline,
    )
    gain = _consumer_gain(result)
    return RepairResult(
        case_id=case.case_id, selected_label=action.value,
        selected_action=action.value, generation_point=gen_point,
        recovery_gain=gain, status=result.status,
    )
