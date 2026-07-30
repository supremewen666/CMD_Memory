"""Content-addressed rollout cache with explicit judge-variance handling."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Mapping

from .operators import OperatorSpec
from ..repair.operator_library import (
    OperatorSpecRecord,
    canonical_json,
    content_id,
    hash_text,
)


@dataclass(frozen=True)
class RolloutCacheKey:
    cache_key: str
    case_id: str
    spec_hash: str
    pre_repair_snapshot_hash: str
    scorer_version: str
    judge_config_hash: str

    @classmethod
    def build(
        cls,
        *,
        case_id: str,
        spec: OperatorSpec,
        pre_repair_snapshot_hash: str,
        scorer_version: str,
        judge_config: Mapping[str, Any],
    ) -> "RolloutCacheKey":
        spec_hash = OperatorSpecRecord.from_operator(spec).spec_hash
        judge_config_hash = hash_text(canonical_json(judge_config))
        payload = {
            "case_id": case_id,
            "spec_hash": spec_hash,
            "pre_repair_snapshot_hash": pre_repair_snapshot_hash,
            "scorer_version": scorer_version,
            "judge_config_hash": judge_config_hash,
        }
        return cls(
            cache_key=content_id("rollout", payload),
            **payload,
        )


@dataclass(frozen=True)
class RolloutObservation:
    observation_id: str
    cache_key: str
    score: float
    recovery_gain: float
    rollout_cost: float
    seed: int | None


@dataclass(frozen=True)
class RolloutEstimate:
    cache_key: str
    observations: int
    mean_recovery_gain: float
    standard_deviation: float
    min_recovery_gain: float
    max_recovery_gain: float
    threshold_flip_observed: bool


class EvolutionRolloutCache:
    """Cache deterministic judges exactly or retain repeated-score variance."""

    def __init__(
        self,
        *,
        pinned_judge: bool,
        threshold: float = 0.1,
        equality_tolerance: float = 1e-12,
    ) -> None:
        self.pinned_judge = pinned_judge
        self.threshold = threshold
        self.equality_tolerance = equality_tolerance
        self.keys: dict[str, RolloutCacheKey] = {}
        self.observations: dict[str, list[RolloutObservation]] = {}

    def record(
        self,
        key: RolloutCacheKey,
        *,
        score: float,
        recovery_gain: float,
        rollout_cost: float,
        seed: int | None,
    ) -> RolloutObservation:
        if not math.isfinite(score) or not math.isfinite(recovery_gain):
            raise ValueError("cache observations must be finite")
        existing_key = self.keys.get(key.cache_key)
        if existing_key is not None and existing_key != key:
            raise ValueError("rollout cache key collision")
        self.keys.setdefault(key.cache_key, key)
        existing = self.observations.setdefault(key.cache_key, [])
        if self.pinned_judge and existing and not math.isclose(
            existing[0].recovery_gain,
            recovery_gain,
            rel_tol=0.0,
            abs_tol=self.equality_tolerance,
        ):
            raise ValueError(
                "pinned judge produced inconsistent recovery gain for cache key"
            )
        payload = {
            "cache_key": key.cache_key,
            "score": float(score),
            "recovery_gain": float(recovery_gain),
            "rollout_cost": float(rollout_cost),
            "seed": seed,
            "ordinal": len(existing),
        }
        observation = RolloutObservation(
            observation_id=content_id("rollout-observation", payload),
            cache_key=key.cache_key,
            score=float(score),
            recovery_gain=float(recovery_gain),
            rollout_cost=float(rollout_cost),
            seed=seed,
        )
        existing.append(observation)
        return observation

    def lookup(self, key: RolloutCacheKey) -> RolloutEstimate | None:
        values = self.observations.get(key.cache_key, ())
        if not values:
            return None
        gains = [item.recovery_gain for item in values]
        hits = {gain >= self.threshold for gain in gains}
        return RolloutEstimate(
            cache_key=key.cache_key,
            observations=len(gains),
            mean_recovery_gain=fmean(gains),
            standard_deviation=pstdev(gains) if len(gains) > 1 else 0.0,
            min_recovery_gain=min(gains),
            max_recovery_gain=max(gains),
            threshold_flip_observed=len(hits) > 1,
        )

    def write_jsonl(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for cache_key in sorted(self.keys):
            key = self.keys[cache_key]
            for observation in self.observations.get(cache_key, ()):
                rows.append(
                    {
                        "key": asdict(key),
                        "observation": asdict(observation),
                    }
                )
        output.write_text(
            "".join(canonical_json(row) + "\n" for row in rows),
            encoding="utf-8",
        )
        return output

    @classmethod
    def read_jsonl(
        cls,
        path: str | Path,
        *,
        pinned_judge: bool,
        threshold: float = 0.1,
    ) -> "EvolutionRolloutCache":
        cache = cls(pinned_judge=pinned_judge, threshold=threshold)
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = RolloutCacheKey(**row["key"])
            observation = row["observation"]
            cache.record(
                key,
                score=float(observation["score"]),
                recovery_gain=float(observation["recovery_gain"]),
                rollout_cost=float(observation["rollout_cost"]),
                seed=observation.get("seed"),
            )
        return cache
