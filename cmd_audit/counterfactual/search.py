"""Single-point counterfactual attribution for pipeline step actions.

The tree-search implementation was retired after the repair-efficacy pivot:
single-point attribution is the live path, while coupled multi-point search is
future work. This module keeps the old public entry point shape so callers can
consume ``SearchResult.main_culprit`` without depending on tree internals.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..core.models import GoldEvidence, MemoryItem
from .actions import PipelineAction, apply_pipeline_action, get_legal_actions
from .context import generate_conditioned_context
from .rollout import rollout_to_terminal


@dataclass
class SinglePointConfig:
    """Configuration for exhaustive single-point attribution."""

    max_iterations: int = 100
    max_depth: int = 4
    early_stopping_threshold: float = 0.8
    time_limit_seconds: float = 30.0
    include_gated_actions: bool = True
    include_item_actions: bool = False
    action_priors: dict[str, float] | dict[int, dict[str, float]] = field(default_factory=dict)
    restrict_to_hop: int | None = None




@dataclass
class SearchResult:
    """Result shape shared by attribution callers.

    ``main_culprit`` uses a 0-based generation point to preserve existing
    callers and artifact schemas.
    """

    best_action_sequence: tuple[PipelineAction, ...]
    main_culprit: tuple[int, PipelineAction, float] | None
    action_credits: dict[int, dict[PipelineAction, float]]
    iterations_completed: int
    nodes_explored: int
    terminal_rollouts: int
    early_stops: int
    search_time_seconds: float
    avg_rollout_time: float

    @property
    def primary_attribution_label(self) -> PipelineAction | None:
        """Primary attributed action with highest positive credit."""
        return self.main_culprit[1] if self.main_culprit else None

    @property
    def attribution_confidence(self) -> float:
        """Confidence based on the credit gap between the top two actions."""
        if not self.main_culprit:
            return 0.0

        max_credit = self.main_culprit[2]
        second_max = 0.0
        for gen_credits in self.action_credits.values():
            for action, credit in gen_credits.items():
                if action != self.main_culprit[1] and credit > second_max:
                    second_max = credit

        if max_credit <= 0:
            return 0.0
        return min(1.0, (max_credit - second_max) / max_credit)


class SinglePointAttributor:
    """Exhaustively score one intervention at each generation point."""

    def __init__(
        self,
        config: SinglePointConfig | None = None,
        *,
        value_function_type: str | None = None,
    ):
        del value_function_type
        self.config = config or SinglePointConfig()
        self._rollout_times: list[float] = []
        self._rollouts = 0

    def search(
        self,
        client: Any,
        initial_context: str,
        recall_set: tuple[MemoryItem, ...],
        gold_evidence: tuple[GoldEvidence, ...],
        gold_answer: str,
        *,
        answer_verifier: Any = None,
        baseline_answer_score: float = 0.0,
        intervention_config: dict[str, Any] | None = None,
    ) -> SearchResult:
        """Run exhaustive single-point attribution."""
        del gold_evidence
        start_time = time.time()
        self._rollout_times = []
        self._rollouts = 0

        contexts = _identity_backbone_contexts(
            client,
            initial_context,
            recall_set,
            self.config.max_depth,
            intervention_config,
            deadline=start_time + self.config.time_limit_seconds,
        )
        action_credits: dict[int, dict[PipelineAction, float]] = {}

        for gen_point in range(self.config.max_depth):
            if time.time() - start_time > self.config.time_limit_seconds:
                break
            if self.config.restrict_to_hop is not None and gen_point + 1 != self.config.restrict_to_hop:
                continue

            parent_context = contexts[gen_point]
            identity_context = _step_context(
                client,
                parent_context,
                PipelineAction.IDENTITY,
                recall_set,
                gen_point,
                intervention_config,
            )
            identity_score = self._rollout_score(
                client,
                identity_context,
                gen_point + 1,
                recall_set,
                gold_answer,
                answer_verifier,
                baseline_answer_score,
            )

            credits: dict[PipelineAction, float] = {PipelineAction.IDENTITY: 0.0}
            for action in _ordered_legal_actions(
                recall_set,
                gen_point,
                self.config.include_gated_actions,
                self.config.include_item_actions,
                _priors_for_generation_point(self.config.action_priors, gen_point),
                self.config.restrict_to_hop,
                intervention_config,
            ):
                if action == PipelineAction.IDENTITY:
                    continue
                if time.time() - start_time > self.config.time_limit_seconds:
                    break
                action_context = _step_context(
                    client,
                    parent_context,
                    action,
                    recall_set,
                    gen_point,
                    intervention_config,
                )
                action_score = self._rollout_score(
                    client,
                    action_context,
                    gen_point + 1,
                    recall_set,
                    gold_answer,
                    answer_verifier,
                    baseline_answer_score,
                )
                credits[action] = action_score - identity_score

            if credits:
                action_credits[gen_point] = credits

        search_time = time.time() - start_time
        avg_rollout_time = (
            sum(self._rollout_times) / len(self._rollout_times)
            if self._rollout_times else 0.0
        )
        main_culprit = _find_main_culprit(action_credits)
        best_path = (main_culprit[1],) if main_culprit else ()

        return SearchResult(
            best_action_sequence=best_path,
            main_culprit=main_culprit,
            action_credits=action_credits,
            iterations_completed=len(action_credits),
            nodes_explored=1 + sum(len(actions) for actions in action_credits.values()),
            terminal_rollouts=self._rollouts,
            early_stops=0,
            search_time_seconds=search_time,
            avg_rollout_time=avg_rollout_time,
        )

    def _rollout_score(
        self,
        client: Any,
        context: str,
        start_generation_point: int,
        recall_set: tuple[MemoryItem, ...],
        gold_answer: str,
        answer_verifier: Any,
        baseline_answer_score: float,
    ) -> float:
        rollout_start = time.time()
        result = rollout_to_terminal(
            client,
            context,
            start_generation_point,
            self.config.max_depth,
            recall_set,
            gold_answer,
            answer_verifier=answer_verifier,
            baseline_answer_score=baseline_answer_score,
        )
        self._rollout_times.append(time.time() - rollout_start)
        self._rollouts += 1
        if result.status == "timeout":
            return float("nan")
        return result.recovery_gain if result.rollout_successful else 0.0




def _step_context(
    client: Any,
    parent_context: str,
    action: PipelineAction,
    recall_set: tuple[MemoryItem, ...],
    gen_point: int,
    intervention_config: dict[str, Any] | None,
) -> str:
    intervened = apply_pipeline_action(
        action,
        parent_context,
        recall_set,
        gen_point,
        intervention_config=intervention_config,
    )
    return generate_conditioned_context(client, intervened, gen_point + 1)


def _identity_backbone_contexts(
    client: Any,
    initial_context: str,
    recall_set: tuple[MemoryItem, ...],
    max_depth: int,
    intervention_config: dict[str, Any] | None,
    *,
    deadline: float,
) -> list[str]:
    contexts = [initial_context]
    current = initial_context
    for gen_point in range(max(0, max_depth - 1)):
        if time.time() > deadline:
            break
        current = _step_context(
            client,
            current,
            PipelineAction.IDENTITY,
            recall_set,
            gen_point,
            intervention_config,
        )
        contexts.append(current)
    while len(contexts) < max_depth:
        contexts.append(contexts[-1])
    return contexts


def _ordered_legal_actions(
    recall_set: tuple[MemoryItem, ...],
    gen_point: int,
    include_gated_actions: bool,
    include_item_actions: bool,
    action_priors: dict[str, float],
    restrict_to_hop: int | None,
    intervention_config: dict[str, Any] | None,
) -> list[PipelineAction]:
    actions = get_legal_actions(
        recall_set,
        gen_point,
        include_gated_actions=include_gated_actions,
        include_item_actions=include_item_actions,
        intervention_config=intervention_config,
        restrict_to_hop=restrict_to_hop,
    )
    identity = [action for action in actions if action == PipelineAction.IDENTITY]
    non_identity = sorted(
        (action for action in actions if action != PipelineAction.IDENTITY),
        key=lambda action: (action_priors.get(action.value, 0.5), action.value),
        reverse=True,
    )
    return identity + non_identity


def _priors_for_generation_point(
    action_priors: dict[str, float] | dict[int, dict[str, float]],
    gen_point: int,
) -> dict[str, float]:
    if not action_priors:
        return {}
    if all(isinstance(key, int) for key in action_priors):
        nested = action_priors.get(gen_point + 1)  # type: ignore[arg-type]
        return dict(nested or {})
    return dict(action_priors)  # type: ignore[arg-type]


def _find_main_culprit(
    action_credits: dict[int, dict[PipelineAction, float]],
) -> tuple[int, PipelineAction, float] | None:
    culprit: tuple[int, PipelineAction, float] | None = None
    best = 0.0
    for gen_point, credits in action_credits.items():
        for action, credit in credits.items():
            if action == PipelineAction.IDENTITY:
                continue
            if credit > best:
                best = credit
                culprit = (gen_point, action, credit)
    return culprit


def attribute_single_point(
    client: Any,
    initial_context: str,
    recall_set: tuple[MemoryItem, ...],
    gold_evidence: tuple[GoldEvidence, ...],
    gold_answer: str,
    *,
    max_iterations: int = 50,
    max_depth: int = 3,
    answer_verifier: Any = None,
    baseline_answer_score: float = 0.0,
    intervention_config: dict[str, Any] | None = None,
    action_priors: dict[str, float] | dict[int, dict[str, float]] | None = None,
    include_item_actions: bool = False,
    value_function_type: str = "nested",
    restrict_to_hop: int | None = None,
) -> SearchResult:
    """Compatibility wrapper for the live single-point attribution path."""
    del value_function_type
    config = SinglePointConfig(
        max_iterations=max_iterations,
        max_depth=max_depth,
        include_item_actions=include_item_actions,
        action_priors=dict(action_priors or {}),
        restrict_to_hop=restrict_to_hop,
    )

    search = SinglePointAttributor(config)
    return search.search(
        client,
        initial_context,
        recall_set,
        gold_evidence,
        gold_answer,
        answer_verifier=answer_verifier,
        baseline_answer_score=baseline_answer_score,
        intervention_config=intervention_config,
    )
