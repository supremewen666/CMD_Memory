"""MCTS tree structure for step-level attribution.

Implements the single-player MCTS tree from DISCUSSION.md:
- Nodes represent generation points with applied actions
- Edges represent pipeline actions (interventions)
- Tree depth = number of generation points
- Tree width = number of legal actions per generation point
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Callable

from .actions import PipelineAction
from .value import NestedValue

_logger = logging.getLogger(__name__)


@dataclass
class MCTSNode:
    """Single node in the MCTS tree.

    Represents a state after applying a sequence of pipeline actions
    up to a specific generation point.
    """
    # Node identification
    node_id: str
    generation_point: int
    action_sequence: tuple[PipelineAction, ...]

    # MCTS statistics
    visit_count: int = 0
    q_max: float = 0.0  # Max Q-value seen (max-backup), drives UCB selection.
    own_recovery: float = 0.0  # This node's own rollout recovery (no back-prop).
    prior_bonus: float = 0.0

    # Node state
    context: str = ""
    is_terminal: bool = False
    is_expanded: bool = False

    # Tree structure
    parent: MCTSNode | None = None
    children: dict[PipelineAction, MCTSNode] = field(default_factory=dict)

    # Value function results
    value: NestedValue | None = None

    @property
    def is_root(self) -> bool:
        """True if this is the root node."""
        return self.parent is None

    @property
    def is_leaf(self) -> bool:
        """True if this node has no children."""
        return len(self.children) == 0

    @property
    def depth(self) -> int:
        """Depth of this node (number of actions from root)."""
        return len(self.action_sequence)

    @property
    def ucb_score(self) -> float:
        """UCB1 score for node selection."""
        if self.visit_count == 0:
            return float('inf')  # Unvisited nodes have highest priority

        if self.parent is None or self.parent.visit_count <= 1:
            return self.q_max

        # UCB1: Q(s,a) + C * sqrt(ln(N(s)) / N(s,a))
        c_exploration = 1.414  # sqrt(2), standard UCB1 constant
        exploitation = self.q_max + self.prior_bonus
        exploration = c_exploration * math.sqrt(
            math.log(self.parent.visit_count) / self.visit_count
        )

        return exploitation + exploration

    def add_child(self, action: PipelineAction, child_node: MCTSNode) -> None:
        """Add a child node for the given action."""
        self.children[action] = child_node
        child_node.parent = self

    def select_best_child(self) -> MCTSNode:
        """Select child with highest UCB score."""
        if not self.children:
            raise ValueError("Cannot select child from leaf node")

        return max(self.children.values(), key=lambda child: child.ucb_score)

    def update_q_max(self, value: float) -> None:
        """Update Q-max with new value (max-backup)."""
        self.q_max = max(self.q_max, value)
        self.visit_count += 1

    def back_propagate(self, value: float) -> None:
        """Back-propagate value up the tree with max-backup."""
        self.update_q_max(value)
        if self.parent is not None:
            self.parent.back_propagate(value)


@dataclass
class MCTSTree:
    """Complete MCTS tree for step-level attribution search."""
    root: MCTSNode
    max_depth: int
    total_nodes: int = 0

    def __post_init__(self):
        self.total_nodes = 1  # Count root node

    def select_leaf(self) -> MCTSNode:
        """Select a leaf node using UCB policy.

        Traverses from root to a leaf using UCB1 selection.
        Returns the first unexpanded or terminal node encountered.
        """
        current = self.root

        while current.is_expanded and not current.is_terminal:
            if not current.children:
                break
            current = current.select_best_child()

        return current

    def expand_node(
        self,
        node: MCTSNode,
        legal_actions: list[PipelineAction],
        context_generator: Callable[[PipelineAction, str], str],
        action_prior: Callable[[PipelineAction], float] | None = None,
    ) -> MCTSNode | None:
        """Expand a node by adding one new child.

        Args:
            node: Node to expand
            legal_actions: Legal actions at this generation point
            context_generator: Function to generate context for new child

        Returns:
            Newly created child node, or None if no expansion possible
        """
        if node.is_terminal or node.generation_point >= self.max_depth:
            return None

        # Find an untried action
        untried_actions = [
            action for action in legal_actions
            if action not in node.children
        ]

        if not untried_actions:
            node.is_expanded = True
            return None

        # Establish the counterfactual baseline before any intervention credit.
        if PipelineAction.IDENTITY in untried_actions:
            action = PipelineAction.IDENTITY
        elif action_prior is not None:
            action = max(untried_actions, key=action_prior)
        else:
            action = untried_actions[0]

        # Create new child node
        child_sequence = node.action_sequence + (action,)
        child_id = f"node_{self.total_nodes}"

        child = MCTSNode(
            node_id=child_id,
            generation_point=node.generation_point + 1,
            action_sequence=child_sequence,
            context=context_generator(action, node.context),
            is_terminal=(node.generation_point + 1 >= self.max_depth),
            prior_bonus=action_prior(action) if action_prior is not None else 0.0,
        )

        # Add to tree
        node.add_child(action, child)
        self.total_nodes += 1

        # Mark parent as expanded if all actions tried
        if len(node.children) == len(legal_actions):
            node.is_expanded = True

        return child

    def get_best_path(self) -> tuple[PipelineAction, ...]:
        """Get the action sequence with highest Q-value from root."""
        current = self.root
        best_path = []

        while current.children:
            # Select child with highest Q-max
            best_child = max(
                current.children.values(),
                key=lambda child: child.q_max
            )

            # Find the action that leads to this child
            for action, child in current.children.items():
                if child == best_child:
                    best_path.append(action)
                    break

            current = best_child

        return tuple(best_path)

    def get_action_credits(self) -> dict[int, dict[PipelineAction, float]]:
        """Compute credit assignment for each generation point and action.

        Credit = own_recovery(prefix + action) - own_recovery(prefix + identity)

        Uses each node's own rollout recovery, NOT the back-propagated q_max.
        q_max carries the max recovery of the whole subtree (so a deep hop-2
        recovery would inflate its hop-1 ancestor); own_recovery is the
        single-point counterfactual "repair only this hop, identity elsewhere"
        signal, which is what step-level credit must attribute.

        Returns:
            Dictionary mapping generation_point -> action -> credit_score
        """
        credits = {}

        def _compute_credits_recursive(node: MCTSNode) -> None:
            if not node.children:
                return

            generation_point = node.generation_point
            identity_child = node.children.get(PipelineAction.IDENTITY)
            if identity_child is None:
                return
            identity_recovery = identity_child.own_recovery

            if generation_point not in credits:
                credits[generation_point] = {}

            # Compute credit for each action
            for action, child in node.children.items():
                credit = child.own_recovery - identity_recovery
                credits[generation_point][action] = credit

                # Recurse to children
                _compute_credits_recursive(child)

        _compute_credits_recursive(self.root)
        return credits

    def find_main_culprit(self) -> tuple[int, PipelineAction, float] | None:
        """Find the generation point and action with highest credit.

        Returns:
            Tuple of (generation_point, action, credit) or None if no credits
        """
        all_credits = self.get_action_credits()

        max_credit = float('-inf')
        best_generation = None
        best_action = None

        for generation_point, action_credits in all_credits.items():
            for action, credit in action_credits.items():
                if action != PipelineAction.IDENTITY and credit > max_credit:
                    max_credit = credit
                    best_generation = generation_point
                    best_action = action

        if best_generation is not None:
            return (best_generation, best_action, max_credit)
        return None
