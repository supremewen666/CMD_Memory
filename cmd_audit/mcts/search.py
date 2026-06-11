"""Main MCTS search orchestration for step-level attribution.

Implements the complete MCTS algorithm from DISCUSSION.md decision #3/G:
Selection → Expansion → Rollout → Back-propagation with UCT and max-backup.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ..core.models import GoldEvidence, MemoryItem
from .actions import PipelineAction, get_legal_actions, apply_pipeline_action
from .rollout import rollout_with_early_stopping, RolloutResult
from .tree import MCTSNode, MCTSTree
from .value import NaiveWeightedValue, ValueFunction, NestedValue

_logger = logging.getLogger(__name__)


@dataclass
class MCTSConfig:
    """Configuration for MCTS search."""
    max_iterations: int = 100
    max_depth: int = 4
    exploration_constant: float = 1.414  # sqrt(2) for UCB1
    early_stopping_threshold: float = 0.8
    evidence_threshold: float = 0.5
    time_limit_seconds: float = 30.0

    # Action gating
    include_gated_actions: bool = True

    # Value function settings
    use_nested_value: bool = True
    value_function_type: str = "nested"

    # Experience reuse settings
    action_priors: dict[str, float] = field(default_factory=dict)
    prior_bonus_weight: float = 0.25

    # Experiment-only action restriction. Hop indexes are 1-based; when set,
    # only that hop may use non-identity actions.
    restrict_to_hop: int | None = None


@dataclass
class SearchResult:
    """Complete result of MCTS search."""
    # Best path and attribution
    best_action_sequence: tuple[PipelineAction, ...]
    main_culprit: tuple[int, PipelineAction, float] | None  # (gen_point, action, credit)

    # Credit assignment
    action_credits: dict[int, dict[PipelineAction, float]]

    # Search statistics
    iterations_completed: int
    nodes_explored: int
    terminal_rollouts: int
    early_stops: int

    # Tree state
    tree: MCTSTree

    # Performance
    search_time_seconds: float
    avg_rollout_time: float

    @property
    def primary_attribution_label(self) -> PipelineAction | None:
        """Primary attributed action (highest credit)."""
        return self.main_culprit[1] if self.main_culprit else None

    @property
    def attribution_confidence(self) -> float:
        """Confidence in primary attribution based on credit gap."""
        if not self.main_culprit:
            return 0.0

        max_credit = self.main_culprit[2]

        # Find second-highest credit
        second_max = 0.0
        for gen_credits in self.action_credits.values():
            for action, credit in gen_credits.items():
                if action != self.main_culprit[1] and credit > second_max:
                    second_max = credit

        # Confidence based on gap
        if max_credit <= 0:
            return 0.0

        gap = max_credit - second_max
        return min(1.0, gap / max_credit) if max_credit > 0 else 0.0


class MCTSSearch:
    """Monte Carlo Tree Search for step-level memory failure attribution."""

    def __init__(
        self,
        config: MCTSConfig | None = None,
        *,
        value_function_type: str | None = None,
    ):
        self.config = config or MCTSConfig()
        if value_function_type is not None:
            self.config.value_function_type = value_function_type
        self.value_function = None
        self._stats = {
            'iterations': 0,
            'rollouts': 0,
            'early_stops': 0,
            'rollout_times': [],
        }

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
        """Run MCTS search for step-level attribution.

        Args:
            client: LLM client for value function and rollouts
            initial_context: Starting context (query + initial retrieval)
            recall_set: Retrieved memory items
            gold_evidence: Ground truth evidence atoms
            gold_answer: Ground truth answer
            answer_verifier: Answer verifier for terminal evaluation

        Returns:
            SearchResult with attribution and tree
        """
        start_time = time.time()
        self._reset_stats()

        # Initialize value function
        value_class = _value_function_class(self.config.value_function_type)
        self.value_function = value_class(
            client, evidence_threshold=self.config.evidence_threshold
        )

        # Create root node
        root = MCTSNode(
            node_id="root",
            generation_point=0,
            action_sequence=(),
            context=initial_context,
            is_terminal=False,
        )

        # Initialize tree
        tree = MCTSTree(root=root, max_depth=self.config.max_depth)

        # Main MCTS loop
        try:
            for iteration in range(self.config.max_iterations):
                # Check time limit
                if time.time() - start_time > self.config.time_limit_seconds:
                    _logger.info("MCTS search hit time limit after %d iterations", iteration)
                    break

                # MCTS iteration: Selection → Expansion → Rollout → Back-propagation
                should_stop = self._mcts_iteration(
                    tree,
                    client,
                    recall_set,
                    gold_evidence,
                    gold_answer,
                    answer_verifier,
                    baseline_answer_score,
                    intervention_config,
                )

                self._stats['iterations'] += 1

                if should_stop:
                    _logger.info("MCTS early stopping after %d iterations", iteration)
                    self._stats['early_stops'] += 1
                    break

        except Exception as exc:
            _logger.error("MCTS search failed: %s", exc)

        # Compute final results
        search_time = time.time() - start_time
        avg_rollout_time = (
            sum(self._stats['rollout_times']) / len(self._stats['rollout_times'])
            if self._stats['rollout_times'] else 0.0
        )

        action_credits = tree.get_action_credits()
        main_culprit = tree.find_main_culprit()
        best_path = tree.get_best_path()

        return SearchResult(
            best_action_sequence=best_path,
            main_culprit=main_culprit,
            action_credits=action_credits,
            iterations_completed=self._stats['iterations'],
            nodes_explored=tree.total_nodes,
            terminal_rollouts=self._stats['rollouts'],
            early_stops=self._stats['early_stops'],
            tree=tree,
            search_time_seconds=search_time,
            avg_rollout_time=avg_rollout_time,
        )

    def _mcts_iteration(
        self,
        tree: MCTSTree,
        client: Any,
        recall_set: tuple[MemoryItem, ...],
        gold_evidence: tuple[GoldEvidence, ...],
        gold_answer: str,
        answer_verifier: Any,
        baseline_answer_score: float,
        intervention_config: dict[str, Any] | None,
    ) -> bool:
        """Single MCTS iteration: Selection → Expansion → Rollout → Back-propagation."""

        # 1. Selection: Find leaf node using UCB policy
        selected_node = tree.select_leaf()

        # 2. Expansion: Add one new child if possible
        legal_actions = get_legal_actions(
            recall_set,
            selected_node.generation_point,
            include_gated_actions=self.config.include_gated_actions,
            restrict_to_hop=self.config.restrict_to_hop,
        )

        def context_generator(action: PipelineAction, parent_context: str) -> str:
            intervened_context = apply_pipeline_action(
                action,
                parent_context,
                recall_set,
                selected_node.generation_point,
                intervention_config=intervention_config,
            )
            return _generate_conditioned_context(
                client,
                intervened_context,
                selected_node.generation_point + 1,
            )

        expanded_node = tree.expand_node(
            selected_node,
            legal_actions,
            context_generator,
            action_prior=self._action_prior_bonus,
        )

        if expanded_node and self.config.use_nested_value:
            expanded_node.value = self.value_function.evaluate_node(
                expanded_node.context, gold_evidence, gold_answer
            )
            expanded_node.q_max = max(
                expanded_node.q_max,
                expanded_node.value.scalar_value,
            )

        # Use expanded node if available, otherwise use selected node
        rollout_node = expanded_node if expanded_node else selected_node

        # 3. Rollout: Complete trajectory and evaluate terminal state
        rollout_start = time.time()

        rollout_result = rollout_with_early_stopping(
            client,
            rollout_node.context,
            rollout_node.generation_point,
            self.config.max_depth,
            recall_set,
            gold_answer,
            answer_verifier=answer_verifier,
            baseline_answer_score=baseline_answer_score,
            recovery_threshold=self.config.early_stopping_threshold,
        )

        rollout_time = time.time() - rollout_start
        self._stats['rollout_times'].append(rollout_time)
        self._stats['rollouts'] += 1

        # 4. Back-propagation: Update Q-values with max-backup
        if rollout_result.rollout_successful:
            rollout_node.back_propagate(rollout_result.recovery_gain)

        return self._should_early_stop(rollout_node, rollout_result)

    def _should_early_stop(
        self,
        rollout_node: MCTSNode,
        rollout_result: RolloutResult,
    ) -> bool:
        """Stop only when a depth-1 single-point intervention already recovers."""
        if not rollout_result.rollout_successful:
            return False
        if rollout_node.generation_point != 1:
            return False
        if not rollout_node.action_sequence:
            return False
        if rollout_node.action_sequence[-1] == PipelineAction.IDENTITY:
            return False
        return (
            rollout_result.generation_points_completed == 0
            and rollout_result.recovery_gain >= self.config.early_stopping_threshold
        )

    def _action_prior_bonus(self, action: PipelineAction) -> float:
        """Soft UCB prior bonus from historical Failure Memory success rates."""
        if action == PipelineAction.IDENTITY:
            return 0.0
        prior = self.config.action_priors.get(action.value, 0.5)
        return max(0.0, prior - 0.5) * self.config.prior_bonus_weight

    def _reset_stats(self) -> None:
        """Reset search statistics."""
        self._stats = {
            'iterations': 0,
            'rollouts': 0,
            'early_stops': 0,
            'rollout_times': [],
        }


def _generate_conditioned_context(
    client: Any,
    context: str,
    generation_point: int,
) -> str:
    """Re-run the generation point under the already-applied interventions."""
    if client is None or not hasattr(client, "generate"):
        return context

    prompt = f"""Continue the trajectory for generation point {generation_point}.

Use the current counterfactual context exactly as the state so far. Generate only
the next reasoning/output prefix for this generation point.

CURRENT CONTEXT:
{context}

NEXT PREFIX:"""
    try:
        generated = client.generate(prompt)
    except Exception as exc:
        _logger.warning("Conditioned generation failed: %s", exc)
        return context
    if not isinstance(generated, str) or not generated.strip():
        return context
    return f"{context.rstrip()}\n\nGenerated prefix {generation_point}:\n{generated.strip()}"


def run_mcts_attribution(
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
    action_priors: dict[str, float] | None = None,
    value_function_type: str = "nested",
    restrict_to_hop: int | None = None,
) -> SearchResult:
    """Convenience function to run MCTS attribution with default config.

    Args:
        client: LLM client
        initial_context: Starting context
        recall_set: Memory items
        gold_evidence: Evidence atoms
        gold_answer: Gold answer
        max_iterations: Maximum MCTS iterations
        max_depth: Maximum tree depth
        answer_verifier: Answer verifier

    Returns:
        SearchResult with attribution
    """
    config = MCTSConfig(
        max_iterations=max_iterations,
        max_depth=max_depth,
        action_priors=dict(action_priors or {}),
        value_function_type=value_function_type,
        restrict_to_hop=restrict_to_hop,
    )

    search = MCTSSearch(config)
    return search.search(
        client, initial_context, recall_set, gold_evidence, gold_answer,
        answer_verifier=answer_verifier,
        baseline_answer_score=baseline_answer_score,
        intervention_config=intervention_config,
    )


def _value_function_class(value_function_type: str):
    if value_function_type == "nested":
        return ValueFunction
    if value_function_type == "naive":
        return NaiveWeightedValue
    raise ValueError(
        "value_function_type must be 'nested' or 'naive', "
        f"got {value_function_type!r}"
    )
