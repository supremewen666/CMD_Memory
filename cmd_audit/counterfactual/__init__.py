"""Counterfactual pipeline action attribution.

The package keeps the historical ``mcts`` import path, but the live
implementation is single-point counterfactual scanning over generation points.
Tree search and value distillation are retired from the mainline.
"""

from .actions import PipelineAction, get_legal_actions, apply_pipeline_action
from .context import generate_conditioned_context
from .search import SinglePointAttributor, SinglePointConfig, SearchResult, attribute_single_point
from .rollout import rollout_to_terminal, RolloutResult

__all__ = [
    "PipelineAction",
    "RolloutResult",
    "SearchResult",
    "apply_pipeline_action",
    "generate_conditioned_context",
    "get_legal_actions",
    "rollout_to_terminal",
    "SinglePointAttributor",
    "SinglePointConfig",
    "attribute_single_point",
]
