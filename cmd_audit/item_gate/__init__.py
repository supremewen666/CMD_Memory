"""Tier 2 Item Gate — Reference-Contrast Divergence for memory content validation.

Implements the cost ladder from DISCUSSION.md decision #4:
① Timestamp → "old" flag only, no verdict
② Recall-set collision (≤C(5,2) G-Eval contrasts, 0 generation)
③ LOO Reconstruction (1 generation + contrast)

Item gate runs before pipeline attribution (Tier 3) to validate memory content correctness.
"""

from .collision import detect_item_collision, compute_recall_set_divergence
from .divergence import (
    DirectedDivergence,
    compute_directed_divergence,
    compute_symmetric_divergence,
)
from .gate import (
    ItemGateResult,
    ItemGateStatus,
    item_signal_hints_from_result,
    run_item_gate,
    run_item_gate_for_recall_set,
)
from .loo import leave_one_out_reconstruct, compute_loo_divergence, order_items_by_experience
from .bucketing import MemoryBucket, bucket_memory_items
from .freshness import FreshnessDecision, arbitrate_freshness

__all__ = [
    "DirectedDivergence",
    "ItemGateResult",
    "ItemGateStatus",
    "MemoryBucket",
    "FreshnessDecision",
    "arbitrate_freshness",
    "bucket_memory_items",
    "compute_directed_divergence",
    "compute_symmetric_divergence",
    "compute_loo_divergence",
    "compute_recall_set_divergence",
    "detect_item_collision",
    "leave_one_out_reconstruct",
    "item_signal_hints_from_result",
    "order_items_by_experience",
    "run_item_gate",
    "run_item_gate_for_recall_set",
]
