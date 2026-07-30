from __future__ import annotations

import pytest

from cmd_audit.counterfactual.actions import PipelineAction
from cmd_audit.counterfactual.operators import OperatorSpec
from cmd_audit.counterfactual.rollout_cache import (
    EvolutionRolloutCache,
    RolloutCacheKey,
)


def _key():
    return RolloutCacheKey.build(
        case_id="case-1",
        spec=OperatorSpec.single(0, PipelineAction.RETRIEVAL_ERROR),
        pre_repair_snapshot_hash="snapshot",
        scorer_version="judge-v1",
        judge_config={"temperature": 0, "seed": 24},
    )


def test_unpinned_cache_carries_variance_and_threshold_flips():
    cache = EvolutionRolloutCache(pinned_judge=False)
    cache.record(_key(), score=0.09, recovery_gain=0.09, rollout_cost=1, seed=1)
    cache.record(_key(), score=0.11, recovery_gain=0.11, rollout_cost=1, seed=2)
    estimate = cache.lookup(_key())
    assert estimate.observations == 2
    assert estimate.mean_recovery_gain == pytest.approx(0.1)
    assert estimate.standard_deviation > 0
    assert estimate.threshold_flip_observed


def test_pinned_cache_rejects_inconsistent_repeat():
    cache = EvolutionRolloutCache(pinned_judge=True)
    cache.record(_key(), score=0.2, recovery_gain=0.2, rollout_cost=1, seed=24)
    with pytest.raises(ValueError, match="pinned judge"):
        cache.record(
            _key(), score=0.3, recovery_gain=0.3, rollout_cost=1, seed=24
        )
