"""Composable operator specs for counterfactual repair experiments.

The live attribution path still scores single ``(generation_point, action)``
interventions. Exp21 needs a clearer representation for richer operators:
multiple step actions plus item-level promote/demote hints. This module keeps
that representation structured so experiments and future skill records do not
pass around ad hoc tuples and side dictionaries.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable

from ..core.models import MemoryItem
from .actions import (
    SINGLE_GENERATION_POINT,
    PipelineAction,
    apply_pipeline_action,
    operator_dsl_for_action,
)
from .context import generate_conditioned_context
from .rollout import rollout_to_terminal

#: §12.3. `item_signal_hints` is keyed by literal memory IDs, which is exactly
#: what §6.1 excludes from the sealed grammar and §8.2 forbids the typed IR from
#: carrying. It stays available to the legacy live path -- `harness.py` feeds
#: item-gate findings through it -- and is refused at the Route A boundary.
LITERAL_ITEM_HINTS_PERMITTED_IN_ROUTE_A = False


class LegacyOnlyChannelError(ValueError):
    """A legacy-only channel reached a Route A conversion (§12.3)."""


@dataclass(frozen=True)
class OperatorStep:
    """One non-identity pipeline action at one generation point."""

    generation_point: int
    action: PipelineAction
    selector: str = ""
    transform: str = ""

    def __post_init__(self) -> None:
        if self.generation_point < 0:
            raise ValueError("generation_point must be >= 0")
        if self.action == PipelineAction.IDENTITY:
            raise ValueError("OperatorStep cannot use identity")
        dsl = operator_dsl_for_action(self.action)
        if dsl is not None:
            if not self.selector:
                object.__setattr__(self, "selector", dsl.selector.value)
            if not self.transform:
                object.__setattr__(self, "transform", dsl.transform.value)

    def to_dict(self) -> dict[str, Any]:
        """Serialize this step for markdown skills and artifacts."""
        return {
            "generation_point": self.generation_point,
            "hop_index": self.generation_point + 1,
            "action": self.action.value,
            "select": self.selector,
            "transform": self.transform,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "OperatorStep":
        """Build a step from a serialized operator spec."""
        raw_action = value.get("action", "")
        action = raw_action if isinstance(raw_action, PipelineAction) else PipelineAction(str(raw_action))
        generation_point = value.get("generation_point")
        if generation_point is None:
            generation_point = int(value.get("hop_index", 1)) - 1
        return cls(
            generation_point=int(generation_point),
            action=action,
            selector=str(value.get("select") or value.get("selector") or ""),
            transform=str(value.get("transform") or ""),
        )


@dataclass(frozen=True)
class OperatorSpec:
    """Executable counterfactual operator.

    ``steps`` names the structural repair actions. ``item_signal_hints`` is the
    parameter channel consumed by ``apply_pipeline_action`` to promote or demote
    memory items without reading gold evidence.

    ``item_signal_hints`` is **legacy-only** (§12.3). Its keys are literal memory
    IDs, so it cannot cross into Route A: §6.1 excludes the item actions for that
    reason and §8.2 forbids the typed IR from holding a case literal at all. Pass
    a spec through :func:`assert_route_a_convertible` before converting it.
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
    def from_dict(cls, value: dict[str, Any]) -> "OperatorSpec":
        """Build a spec from a serialized markdown/artifact shape."""
        steps = tuple(OperatorStep.from_dict(item) for item in value.get("steps", ()))
        raw_hints = value.get("item_signal_hints") or value.get("params", {}).get("item_signal_hints") or {}
        return cls(steps=steps, item_signal_hints=_normalize_hints(raw_hints))

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

    def to_dict(self) -> dict[str, Any]:
        """Serialize this operator for skill records and artifacts."""
        return {
            "steps": [step.to_dict() for step in self.steps],
            "params": {"item_signal_hints": self.item_signal_hints_dict()},
        }

    def content_hash(self) -> str:
        """Return a stable hash for governance deduplication.

        The canonical payload contains only executable structure. Presentation
        fields and insertion order therefore cannot create duplicate shapes.
        """
        payload = {
            "steps": [
                {
                    "generation_point": step.generation_point,
                    "action": step.action.value,
                    "select": step.selector,
                    "transform": step.transform,
                }
                for step in sorted(
                    self.steps,
                    key=lambda item: (
                        item.generation_point,
                        item.action.value,
                        item.selector,
                        item.transform,
                    ),
                )
            ],
            "item_signal_hints": [
                [memory_id, weight]
                for memory_id, weight in sorted(self.item_signal_hints)
            ],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

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

    def to_markdown_block(self) -> str:
        """Human-readable executable operator block for markdown skills."""
        lines = ["```operator-spec"]
        for step in self.steps:
            lines.append(
                " - "
                f"hop={step.generation_point + 1} "
                f"action={step.action.value} "
                f"select={step.selector} "
                f"transform={step.transform}"
            )
        if self.item_signal_hints:
            hints = ", ".join(
                f"{memory_id}:{weight:g}"
                for memory_id, weight in self.item_signal_hints
            )
            lines.append(f" - params.item_signal_hints={hints}")
        lines.append("```")
        return "\n".join(lines)


def assert_route_a_convertible(operator: OperatorSpec) -> None:
    """Raise unless this legacy spec can cross into Route A (§12.3).

    The action channel is already sealed on the far side: `translate_action`
    rejects every action §6.1 excludes. This guards the *parameter* channel,
    which is not otherwise checked -- a spec whose steps are all legal converts
    cleanly while its `item_signal_hints` are silently dropped, so the resulting
    typed-IR program is not behaviorally equivalent to the operator it came from
    and nothing in the artifact says so. Refusing is the honest outcome: the
    hints have no representation in the typed IR to be translated into.

    Emptiness is the test, not truthiness of the weights: a recorded `0.0` is
    still a parameter, and dropping it changes the operator.
    """
    if operator.item_signal_hints and not LITERAL_ITEM_HINTS_PERMITTED_IN_ROUTE_A:
        keys = ", ".join(sorted(memory_id for memory_id, _ in operator.item_signal_hints))
        raise LegacyOnlyChannelError(
            "item_signal_hints is legacy-only and cannot enter a Route A "
            f"conversion (§12.3): keys [{keys}] are literal memory IDs, which "
            "§6.1 excludes from the sealed grammar and §8.2 forbids the typed "
            "IR from carrying. Converting would drop them silently and the "
            "converted program would not match the legacy operator."
        )


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


@dataclass(frozen=True)
class OperatorExecutionResult:
    """Terminal score for one executed operator.

    ``status`` mirrors ``RolloutResult.status``; on ``"timeout"`` ``score``
    is ``NaN`` (never wins a maximisation) and ``successful`` is ``False``.
    """

    operator: OperatorSpec
    terminal_context: str
    terminal_answer: str
    score: float
    successful: bool
    generation_points_completed: int
    status: str = "ok"


def evaluate_operator_spec(
    client: Any,
    initial_context: str,
    recall_set: tuple[MemoryItem, ...],
    operator: OperatorSpec,
    *,
    max_depth: int,
    gold_answer: str,
    answer_verifier: Any = None,
    intervention_config: dict[str, Any] | None = None,
) -> OperatorExecutionResult:
    """Execute an operator along the generation backbone and score terminal answer.

    Context construction is gold-free: the operator only reads the recall set,
    candidate memory pool, raw events, and item-signal params in
    ``intervention_config``. The gold answer is used only for scoring.
    """
    cfg = operator.intervention_config(intervention_config)
    action_by_gp = operator.action_by_generation_point()
    action = action_by_gp.get(SINGLE_GENERATION_POINT, PipelineAction.IDENTITY)
    intervened = apply_pipeline_action(
        action,
        initial_context,
        recall_set,
        SINGLE_GENERATION_POINT,
        intervention_config=cfg,
    )
    current = generate_conditioned_context(
        client,
        intervened,
        SINGLE_GENERATION_POINT + 1,
    )

    result = rollout_to_terminal(
        client,
        current,
        max_depth,
        max_depth,
        recall_set,
        gold_answer,
        answer_verifier=answer_verifier,
    )
    score = float("nan") if result.status == "timeout" else result.recovery_gain
    return OperatorExecutionResult(
        operator=operator,
        terminal_context=result.terminal_context,
        terminal_answer=result.terminal_answer,
        score=score,
        successful=result.rollout_successful,
        generation_points_completed=result.generation_points_completed,
        status=result.status,
    )


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
