"""Repair-efficacy four-arm executor (Phase 1, E2/E3).

All four arms share one **gold-free** context constructor: the repaired context
is a pure function of ``(recall_set, pipeline_action)`` via
``apply_pipeline_action`` and never reads ``case.gold_evidence`` or
``case.gold_answer``. The arms differ only in HOW the action label is selected:

  - ``no_repair``    : no action (identity) — the floor.
  - ``random``       : case_id-seeded pick over legal step actions — noise floor.
  - ``llm_judge``    : LLM names the fault, mapped to its action — E3 competitor.
  - ``cmd``          : recovery-gain pick (caller supplies the label).

Scoring (answer/evidence) legitimately uses gold; only *construction* is
gold-free. This keeps E2 (loop vs no-repair) and E3 (CMD selection vs random /
llm-judge selection) honest by construction — a recovered answer cannot come
from copying gold into the context.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Callable

from ..core.models import MemoryItem, ProbeCase
from ..mcts.actions import (
    PipelineAction,
    apply_pipeline_action,
    get_legal_actions,
)
from ..mcts.rollout import rollout_to_terminal

REPAIR_ARMS = ("no_repair", "random", "llm_judge", "cmd")

# label string <-> PipelineAction: the 5 step actions share the enum's values.
LABEL_TO_ACTION = {action.value: action for action in PipelineAction}


@dataclass(frozen=True)
class RepairArmResult:
    """Outcome of one repair arm on one case."""

    case_id: str
    arm: str
    selected_label: str | None  # None for no_repair
    selected_action: str | None
    generation_point: int | None
    recovery_gain: float
    recovered: bool


def _legal_actions_all_points(
    recall_set: tuple[MemoryItem, ...], max_depth: int
) -> list[tuple[int, PipelineAction]]:
    """Enumerate (gen_point, action) pairs legal along the trajectory."""
    pairs: list[tuple[int, PipelineAction]] = []
    for gp in range(max_depth):
        for action in get_legal_actions(recall_set, gp):
            if action == PipelineAction.IDENTITY:
                continue
            pairs.append((gp, action))
    return pairs


def select_label_random(
    case: ProbeCase,
    recall_set: tuple[MemoryItem, ...],
    max_depth: int,
) -> tuple[int, PipelineAction] | None:
    """case_id-seeded pick over legal (gen_point, action) pairs. Deterministic."""
    pairs = _legal_actions_all_points(recall_set, max_depth)
    if not pairs:
        return None
    rng = random.Random(case.case_id)
    return rng.choice(pairs)


def select_label_cmd(
    label: str,
    recall_set: tuple[MemoryItem, ...],
    max_depth: int,
) -> tuple[int, PipelineAction] | None:
    """Map a CMD-predicted label to its (gen_point, action). Picks the first
    generation point where the action is legal."""
    action = LABEL_TO_ACTION.get(label)
    if action is None or action == PipelineAction.IDENTITY:
        return None
    for gp in range(max_depth):
        if action in get_legal_actions(recall_set, gp):
            return (gp, action)
    return None


def run_repair_arm(
    case: ProbeCase,
    arm: str,
    *,
    client: Any,
    answer_verifier: Any,
    base_context: str,
    recall_set: tuple[MemoryItem, ...],
    max_depth: int,
    intervention_config: dict[str, Any] | None = None,
    cmd_label: str | None = None,
    llm_label_selector: Callable[[ProbeCase], str | None] | None = None,
    recovered_threshold: float = 0.1,
) -> RepairArmResult:
    """Run one arm: select an action (per arm), construct gold-free context,
    roll out to a terminal answer, score recovery.

    no_repair applies no action (identity rollout from the base context).
    """
    if arm not in REPAIR_ARMS:
        raise ValueError(f"unknown repair arm: {arm!r} (expected one of {REPAIR_ARMS})")

    baseline = case.primary_baseline.answer_score

    choice: tuple[int, PipelineAction] | None
    if arm == "no_repair":
        choice = None
    elif arm == "random":
        choice = select_label_random(case, recall_set, max_depth)
    elif arm == "cmd":
        choice = select_label_cmd(cmd_label or "", recall_set, max_depth)
    else:  # llm_judge
        label = llm_label_selector(case) if llm_label_selector else None
        choice = select_label_cmd(label or "", recall_set, max_depth)

    if choice is None:
        # No action: roll out the base context unchanged (identity backbone).
        result = rollout_to_terminal(
            client, base_context, 0, max_depth, recall_set, case.gold_answer,
            answer_verifier=answer_verifier, baseline_answer_score=baseline,
        )
        gain = result.recovery_gain if result.rollout_successful else 0.0
        return RepairArmResult(
            case_id=case.case_id, arm=arm, selected_label=None,
            selected_action=None, generation_point=None,
            recovery_gain=gain, recovered=gain > recovered_threshold,
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
    gain = result.recovery_gain if result.rollout_successful else 0.0
    return RepairArmResult(
        case_id=case.case_id, arm=arm, selected_label=action.value,
        selected_action=action.value, generation_point=gen_point,
        recovery_gain=gain, recovered=gain > recovered_threshold,
    )
