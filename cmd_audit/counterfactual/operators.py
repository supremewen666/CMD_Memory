"""Composable operator specs for counterfactual repair experiments.

The live attribution path still scores single ``(generation_point, action)``
interventions. Exp21 needs a clearer representation for richer operators:
multiple step actions plus item-level promote/demote hints. This module keeps
that representation structured so experiments and future skill records do not
pass around ad hoc tuples and side dictionaries.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ..core.models import MemoryItem
from .actions import PipelineAction, apply_pipeline_action


@dataclass(frozen=True)
class OperatorStep:
    """One non-identity pipeline action at one generation point."""

    generation_point: int
    action: PipelineAction

    def __post_init__(self) -> None:
        if self.generation_point < 0:
            raise ValueError("generation_point must be >= 0")
        if self.action == PipelineAction.IDENTITY:
            raise ValueError("OperatorStep cannot use identity")


@dataclass(frozen=True)
class OperatorSpec:
    """Executable counterfactual operator.

    ``steps`` names the structural repair actions. ``item_signal_hints`` is the
    parameter channel consumed by ``apply_pipeline_action`` to promote or demote
    memory items without reading gold evidence.
    """

    steps: tuple[OperatorStep, ...] = ()
    item_signal_hints: tuple[tuple[str, float], ...] = ()

    @classmethod
    def from_actions(
        cls,
        actions: Iterable[tuple[int, PipelineAction]],
        *,
        item_signal_hints: dict[str, float] | None = None,
    ) -> "OperatorSpec":
        """Build a spec from ``(generation_point, action)`` pairs.

        Identity actions are omitted. Duplicate generation points are rejected
        because the executor can apply at most one repair action at each point.
        """
        by_gp: dict[int, PipelineAction] = {}
        for generation_point, action in actions:
            if action == PipelineAction.IDENTITY:
                continue
            if generation_point in by_gp:
                raise ValueError(f"duplicate generation point: {generation_point}")
            by_gp[generation_point] = action
        steps = tuple(
            OperatorStep(generation_point, action)
            for generation_point, action in sorted(by_gp.items())
        )
        hints = _normalize_hints(item_signal_hints)
        return cls(steps=steps, item_signal_hints=hints)

    @classmethod
    def single(
        cls,
        generation_point: int,
        action: PipelineAction,
        *,
        item_signal_hints: dict[str, float] | None = None,
    ) -> "OperatorSpec":
        """Build a one-step operator."""
        return cls.from_actions(
            ((generation_point, action),),
            item_signal_hints=item_signal_hints,
        )

    def with_item_signal_hint(self, memory_id: str, weight: float) -> "OperatorSpec":
        """Return a copy with one promote/demote parameter set."""
        hints = self.item_signal_hints_dict()
        hints[str(memory_id)] = float(weight)
        return OperatorSpec(steps=self.steps, item_signal_hints=_normalize_hints(hints))

    def item_signal_hints_dict(self) -> dict[str, float]:
        """Return item hints as a mutable plain dict."""
        return dict(self.item_signal_hints)

    def action_by_generation_point(self) -> dict[int, PipelineAction]:
        """Return non-identity actions keyed by 0-based generation point."""
        return {step.generation_point: step.action for step in self.steps}

    def intervention_config(
        self,
        base_config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Merge this operator's parameters into a base intervention config."""
        merged = dict(base_config or {})
        if not self.item_signal_hints:
            return merged
        base_hints = merged.get("item_signal_hints") or {}
        if not isinstance(base_hints, dict):
            base_hints = {}
        hints = dict(_normalize_hints(base_hints))
        hints.update(self.item_signal_hints_dict())
        merged["item_signal_hints"] = hints
        return merged

    @property
    def last_action(self) -> PipelineAction | None:
        """Last structural action in generation order."""
        return self.steps[-1].action if self.steps else None

    def format(self) -> str:
        """Stable compact representation for artifact rows."""
        pieces = [
            f"gp{step.generation_point}:{step.action.value}"
            for step in self.steps
        ]
        if self.item_signal_hints:
            hint_text = ",".join(
                f"{memory_id}={weight:g}"
                for memory_id, weight in self.item_signal_hints
            )
            pieces.append(f"hints[{hint_text}]")
        return "+".join(pieces) if pieces else "identity"


def apply_operator_static(
    context: str,
    recall_set: tuple[MemoryItem, ...],
    operator: OperatorSpec,
    *,
    intervention_config: dict[str, Any] | None = None,
) -> str:
    """Apply an operator chain without regenerating intermediate prefixes."""
    cfg = operator.intervention_config(intervention_config)
    current = context
    for step in operator.steps:
        current = apply_pipeline_action(
            step.action,
            current,
            recall_set,
            step.generation_point,
            intervention_config=cfg,
        )
    return current


def _normalize_hints(
    item_signal_hints: dict[str, float] | None,
) -> tuple[tuple[str, float], ...]:
    if not item_signal_hints:
        return ()
    normalized: list[tuple[str, float]] = []
    for key, value in item_signal_hints.items():
        try:
            normalized.append((str(key), float(value)))
        except (TypeError, ValueError):
            continue
    return tuple(sorted(normalized))
