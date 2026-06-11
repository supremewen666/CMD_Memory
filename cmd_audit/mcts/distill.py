"""Utilities for distilling MCTS action-credit traces into action priors."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..core.labels import PIPELINE_STEP_ACTIONS
from .actions import PipelineAction


def distill_action_priors(
    results: list[Any],
    *,
    labels: tuple[str, ...] = PIPELINE_STEP_ACTIONS,
) -> dict[str, dict[str, float]]:
    """Distill per-gold-label action priors from MCTS or Audit results.

    Returns ``{gold_label: {action_label: prior}}``. Priors are soft, normalized
    mean positive credits with neutral ``0.5`` for unseen actions.
    """
    credit_sums: dict[str, dict[str, float]] = {
        label: {action: 0.0 for action in labels} for label in labels
    }
    counts: dict[str, dict[str, int]] = {
        label: {action: 0 for action in labels} for label in labels
    }

    for result in results:
        gold_label = _gold_label(result)
        if gold_label not in labels:
            continue
        search_result = _search_result(result)
        if search_result is None:
            continue
        for action_label, credit in _iter_action_credits(search_result):
            if action_label not in labels:
                continue
            credit_sums[gold_label][action_label] += max(0.0, float(credit))
            counts[gold_label][action_label] += 1

    priors: dict[str, dict[str, float]] = {}
    for gold_label in labels:
        means = {
            action: (
                credit_sums[gold_label][action] / counts[gold_label][action]
                if counts[gold_label][action]
                else 0.0
            )
            for action in labels
        }
        max_mean = max(means.values(), default=0.0)
        if max_mean <= 0.0:
            priors[gold_label] = {action: 0.5 for action in labels}
        else:
            priors[gold_label] = {
                action: 0.5 + 0.5 * (means[action] / max_mean)
                for action in labels
            }
    return priors


def flatten_action_priors(
    prior_map: dict[str, dict[str, float]],
    *,
    labels: tuple[str, ...] = PIPELINE_STEP_ACTIONS,
) -> dict[str, float]:
    """Average a per-label prior map into the flat form accepted by MCTS."""
    if not prior_map:
        return {label: 0.5 for label in labels}
    flat: dict[str, float] = {}
    for action in labels:
        values = [row.get(action, 0.5) for row in prior_map.values()]
        flat[action] = sum(values) / len(values) if values else 0.5
    return flat


def prior_alignment(
    prior_map: dict[str, dict[str, float]],
    *,
    labels: tuple[str, ...] = PIPELINE_STEP_ACTIONS,
) -> float:
    """Share of gold labels whose top distilled action matches the label."""
    if not prior_map:
        return 0.0
    total = 0
    aligned = 0
    for gold_label in labels:
        row = prior_map.get(gold_label)
        if not row:
            continue
        total += 1
        top_action = max(row, key=row.get)
        aligned += int(top_action == gold_label)
    return aligned / total if total else 0.0


def oracle_action_priors(
    gold_label: str,
    *,
    labels: tuple[str, ...] = PIPELINE_STEP_ACTIONS,
) -> dict[str, float]:
    """A per-case oracle prior used only as the Exp12 upper bound."""
    return {label: (1.0 if label == gold_label else 0.5) for label in labels}


def _gold_label(result: Any) -> str | None:
    return getattr(result, "perturbation_label", None) or getattr(
        getattr(result, "case", None),
        "perturbation_label",
        None,
    )


def _search_result(result: Any) -> Any:
    return getattr(result, "mcts_result", None) or getattr(result, "search_result", None) or result


def _iter_action_credits(search_result: Any):
    action_credits = getattr(search_result, "action_credits", {}) or {}
    for per_hop in action_credits.values():
        for action, credit in per_hop.items():
            if action == PipelineAction.IDENTITY:
                continue
            action_label = getattr(action, "value", str(action))
            yield action_label, credit
