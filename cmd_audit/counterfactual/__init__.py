"""Counterfactual pipeline action attribution.

The package keeps the historical ``mcts`` import path, but the live
implementation is single-point counterfactual scanning over generation points.
Tree search and value distillation are retired from the mainline.
"""

from .actions import (
    PIPELINE_ACTION_OPERATOR_DSL,
    SINGLE_GENERATION_POINT,
    SINGLE_POINT_DEPTH,
    PipelineAction,
    PipelineOperatorDSL,
    SelectPredicate,
    TransformPrimitive,
    apply_pipeline_action,
    get_legal_actions,
    operator_dsl_for_action,
)
from .context import generate_conditioned_context
from .operators import (
    OperatorExecutionResult,
    OperatorSpec,
    OperatorStep,
    apply_operator_static,
    evaluate_operator_spec,
)
from .search import SinglePointAttributor, SinglePointConfig, SearchResult, attribute_single_point
from .rollout import rollout_to_terminal, RolloutResult

__all__ = [
    "OperatorExecutionResult",
    "OperatorSpec",
    "OperatorStep",
    "PIPELINE_ACTION_OPERATOR_DSL",
    "SINGLE_GENERATION_POINT",
    "SINGLE_POINT_DEPTH",
    "PipelineAction",
    "PipelineOperatorDSL",
    "RolloutResult",
    "SearchResult",
    "SelectPredicate",
    "apply_pipeline_action",
    "apply_operator_static",
    "evaluate_operator_spec",
    "generate_conditioned_context",
    "get_legal_actions",
    "operator_dsl_for_action",
    "rollout_to_terminal",
    "SinglePointAttributor",
    "SinglePointConfig",
    "TransformPrimitive",
    "attribute_single_point",
]
