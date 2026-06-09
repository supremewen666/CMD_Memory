"""Tier 3 MCTS — Single-player Monte Carlo Tree Search for step-level attribution.

Implements the MCTS framework from DISCUSSION.md decision #3/G:
- Selection: UCT with max-backup
- Expansion: Legal actions per generation point
- Rollout: Identity completion to terminal state
- Back-propagation: Max-backup along path

Tree structure:
- Depth = generation points (not hops/tool calls)
- Width = legal actions (5 pipeline step actions)
- Value function: Nested ceiling(k/N) · E[score_answer]/4
"""

from .actions import PipelineAction, get_legal_actions, apply_pipeline_action
from .search import MCTSSearch, MCTSConfig, SearchResult, run_mcts_attribution
from .tree import MCTSNode, MCTSTree
from .value import ValueFunction, compute_node_value, NestedValue
from .rollout import rollout_to_terminal, RolloutResult

__all__ = [
    "MCTSConfig",
    "MCTSNode",
    "MCTSSearch",
    "MCTSTree",
    "NestedValue",
    "PipelineAction",
    "RolloutResult",
    "SearchResult",
    "ValueFunction",
    "apply_pipeline_action",
    "compute_node_value",
    "get_legal_actions",
    "rollout_to_terminal",
    "run_mcts_attribution",
]