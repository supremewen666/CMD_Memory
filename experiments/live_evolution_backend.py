"""Live model/scorer backend for the Experiment 24A/24B state machines."""

from __future__ import annotations

import hashlib
import os
from typing import Any, Mapping, Sequence

from cmd_audit.core.models import ProbeCase
from cmd_audit.counterfactual.actions import (
    SINGLE_GENERATION_POINT,
    SINGLE_POINT_DEPTH,
    PipelineAction,
    get_legal_actions,
)
from cmd_audit.counterfactual.operators import OperatorSpec
from cmd_audit.counterfactual.rollout_cache import (
    EvolutionRolloutCache,
    RolloutCacheKey,
)
from cmd_audit.eval.provenance import require_scorer_version
from cmd_audit.eval.writers import is_timeout_value
from cmd_audit.repair.operator_library import (
    AppendOnlyEvolutionStore,
    TapeCandidate,
    canonical_json,
)
from experiments.evolution_runner_common import ArmEvaluation
from experiments.experiment_runner_common import (
    assert_g_eval_available,
    assert_live_llm_env_configured,
    build_answer_verifier,
    build_clients,
)
from experiments.probe_exhaustive import _own_recovery, _step_context


class LiveEvolutionBackend:
    """Frozen live executor with content-addressed rollout reuse."""

    def __init__(
        self,
        *,
        scorer_version: str | None = None,
        threshold: float = 0.1,
        max_discovery_candidates: int = 128,
        discovery_classes: str = "all",
        judge_seed: int = 24,
        pinned_judge: bool = True,
    ) -> None:
        if max_discovery_candidates < 1:
            raise ValueError("max_discovery_candidates must be positive")
        if discovery_classes not in {"single", "all"}:
            raise ValueError("discovery_classes must be single or all")
        assert_live_llm_env_configured()
        self.answer_client, self.judge_client = build_clients()
        assert_g_eval_available(
            self.judge_client, role="skill-evolution-frozen-judge"
        )
        self.verifier = build_answer_verifier(
            self.judge_client, answer_mode="answer-rubric"
        )
        # Scorer version frozen (CONTRACT §1): derived from the real judge
        # identity rather than accepted as a free-form label. ``scorer_version``
        # passed in (e.g. a caller's ``--scorer-version``) is validated against
        # the derived hash, not used directly; see
        # ``cmd_audit.eval.provenance.require_scorer_version``.
        self.scorer_identity = require_scorer_version(
            self.judge_client, explicit_scorer_version=scorer_version
        )
        self.scorer_version = self.scorer_identity["scorer_version"]
        self.threshold = threshold
        self.max_discovery_candidates = max_discovery_candidates
        self.discovery_classes = discovery_classes
        self.judge_seed = judge_seed
        self.cache = EvolutionRolloutCache(
            pinned_judge=pinned_judge,
            threshold=threshold,
        )
        self.store: AppendOnlyEvolutionStore | None = None
        self._runtime: dict[str, dict[str, Any]] = {}
        self._scores: dict[tuple[str, str], float] = {}

    def bind_store(self, store: AppendOnlyEvolutionStore) -> None:
        self.store = store

    def evaluate(
        self,
        row: Mapping[str, Any],
        _arm_id: str,
        _version,
        revisions: Sequence[Any],
    ) -> ArmEvaluation:
        if self.store is None:
            raise RuntimeError("bind_store must be called before live execution")
        runtime = self._case_runtime(row)
        attempted: list[str] = []
        executed: list[tuple[str, float, float]] = []
        best_gain = 0.0
        for revision in revisions:
            attempted.append(revision.revision_id)
            spec = self.store.specs[revision.spec_hash].to_operator()
            gain = self._net_gain(row, spec)
            executed.append((revision.revision_id, gain, 1.0))
            if not is_timeout_value(gain):
                best_gain = max(best_gain, gain)
            if not is_timeout_value(gain) and gain >= self.threshold:
                return ArmEvaluation(
                    attempted_revision_ids=tuple(attempted),
                    executed_revision_gains=tuple(executed),
                    recovered=True,
                    recovery_gain=gain,
                    library_rollouts=len(attempted),
                    discovery_rollouts=0,
                )
        discovery_rollouts = 0
        for spec in self._discovery_specs(runtime):
            discovery_rollouts += 1
            gain = self._net_gain(row, spec)
            if not is_timeout_value(gain):
                best_gain = max(best_gain, gain)
            if not is_timeout_value(gain) and gain >= self.threshold:
                return ArmEvaluation(
                    attempted_revision_ids=tuple(attempted),
                    executed_revision_gains=tuple(executed),
                    recovered=True,
                    recovery_gain=gain,
                    library_rollouts=len(attempted),
                    discovery_rollouts=discovery_rollouts,
                )
        return ArmEvaluation(
            attempted_revision_ids=tuple(attempted),
            executed_revision_gains=tuple(executed),
            recovered=False,
            recovery_gain=best_gain,
            library_rollouts=len(attempted),
            discovery_rollouts=discovery_rollouts,
        )

    def probe(self, row, arm_id, version, revisions) -> bool:
        return self.evaluate(row, arm_id, version, revisions).recovered

    def direct_revision_gain(
        self,
        row: Mapping[str, Any],
        _arm_id: str,
        revision,
    ) -> float:
        """Execute one specified revision without retrieval or discovery."""
        if self.store is None:
            raise RuntimeError("bind_store must be called before live execution")
        spec = self.store.specs[revision.spec_hash].to_operator()
        return self._net_gain(row, spec)

    def shadow(self, row: Mapping[str, Any]) -> tuple[TapeCandidate, ...]:
        runtime = self._case_runtime(row)
        candidates = []
        for spec in self._discovery_specs(runtime):
            gain = self._net_gain(row, spec)
            timed_out = is_timeout_value(gain)
            candidates.append(
                TapeCandidate(
                    spec=spec,
                    recovery_gain=0.0 if timed_out else gain,
                    rollout_cost=1.0,
                    accepted=not timed_out,
                    rejection_reason="timeout" if timed_out else "",
                )
            )
        return tuple(candidates)

    def _case_runtime(self, row: Mapping[str, Any]) -> dict[str, Any]:
        case_id = str(row["case_id"])
        existing = self._runtime.get(case_id)
        if existing is not None:
            return existing
        from cmd_audit.harness import (
            _initial_mcts_context,
            _retrieved_memory_items,
        )

        case = ProbeCase.from_mapping(dict(row))
        recall = _retrieved_memory_items(case)
        runtime = {
            "case": case,
            "recall": recall,
            "max_depth": SINGLE_POINT_DEPTH,
            "base_context": _initial_mcts_context(case, recall),
            "config": {
                "candidate_items": case.extracted_memory,
                "raw_events": case.raw_events,
            },
            "item_ids": tuple(
                dict.fromkeys(
                    item.memory_id
                    for item in recall + tuple(case.extracted_memory)
                )
            ),
            "pre_repair_snapshot_hash": _snapshot_hash(row),
        }
        self._runtime[case_id] = runtime
        base = self._execute(row, OperatorSpec())
        if is_timeout_value(base):
            raise RuntimeError(f"{case_id}: identity-backbone rollout timed out")
        runtime["base_gain"] = base
        return runtime

    def _execute(self, row: Mapping[str, Any], spec: OperatorSpec) -> float:
        runtime = self._runtime.get(str(row["case_id"]))
        if runtime is None:
            runtime = self._case_runtime_without_base(row)
        spec_hash = spec.content_hash()
        score_key = (str(row["case_id"]), spec_hash)
        if score_key in self._scores:
            return self._scores[score_key]
        case = runtime["case"]
        operator_config = spec.intervention_config(runtime["config"])
        actions = spec.action_by_generation_point()
        context = _step_context(
            self.answer_client,
            runtime["base_context"],
            actions.get(SINGLE_GENERATION_POINT, PipelineAction.IDENTITY),
            runtime["recall"],
            SINGLE_GENERATION_POINT,
            operator_config,
        )
        score = _own_recovery(
            self.answer_client,
            context,
            runtime["max_depth"],
            runtime["max_depth"],
            runtime["recall"],
            case.gold_answer,
            self.verifier,
            case.primary_baseline.answer_score,
        )
        self._scores[score_key] = score
        return score

    def _case_runtime_without_base(
        self, row: Mapping[str, Any]
    ) -> dict[str, Any]:
        # Break the _case_runtime -> identity _execute recursion.
        from cmd_audit.harness import (
            _initial_mcts_context,
            _retrieved_memory_items,
        )

        case = ProbeCase.from_mapping(dict(row))
        recall = _retrieved_memory_items(case)
        runtime = {
            "case": case,
            "recall": recall,
            "max_depth": SINGLE_POINT_DEPTH,
            "base_context": _initial_mcts_context(case, recall),
            "config": {
                "candidate_items": case.extracted_memory,
                "raw_events": case.raw_events,
            },
            "item_ids": tuple(
                dict.fromkeys(
                    item.memory_id
                    for item in recall + tuple(case.extracted_memory)
                )
            ),
            "pre_repair_snapshot_hash": _snapshot_hash(row),
        }
        self._runtime[str(row["case_id"])] = runtime
        return runtime

    def _net_gain(self, row: Mapping[str, Any], spec: OperatorSpec) -> float:
        runtime = self._case_runtime(row)
        score = self._execute(row, spec)
        if is_timeout_value(score):
            return float("nan")
        net = score - float(runtime["base_gain"])
        key = RolloutCacheKey.build(
            case_id=str(row["case_id"]),
            spec=spec,
            pre_repair_snapshot_hash=runtime["pre_repair_snapshot_hash"],
            scorer_version=self.scorer_version,
            judge_config={
                "temperature": 0,
                "seed": self.judge_seed,
                "role": "judge",
            },
        )
        if self.cache.lookup(key) is None:
            self.cache.record(
                key,
                score=score,
                recovery_gain=net,
                rollout_cost=1.0,
                seed=self.judge_seed,
            )
        return net

    def _discovery_specs(
        self, runtime: Mapping[str, Any]
    ) -> tuple[OperatorSpec, ...]:
        cached = runtime.get("discovery_specs")
        if cached is not None:
            return cached
        recall = runtime["recall"]
        base_specs: list[OperatorSpec] = [
            OperatorSpec.single(SINGLE_GENERATION_POINT, action)
            for action in get_legal_actions(recall, SINGLE_GENERATION_POINT)
            if action != PipelineAction.IDENTITY
        ]
        if self.discovery_classes == "all":
            # With depth pinned to one generation point there is no second
            # point to pair an action with, so the item-signal-hint axis is
            # the composition axis for composite operators (see
            # REFACTOR_SPEC_SINGLE_POINT.md §3.4).
            structural = tuple(base_specs)
            for base in structural:
                for memory_id in runtime["item_ids"]:
                    for weight in (-1.0, 1.0):
                        base_specs.append(
                            base.with_item_signal_hint(memory_id, weight)
                        )
        unique = {
            spec.content_hash(): spec for spec in base_specs
        }
        ordered = tuple(
            unique[key]
            for key in sorted(unique)[: self.max_discovery_candidates]
        )
        runtime["discovery_specs"] = ordered
        return ordered


def _snapshot_hash(row: Mapping[str, Any]) -> str:
    prohibited = {
        "gold_answer",
        "gold_evidence",
        "expected_fault",
        "perturbation_label",
        "recurrent_family_id",
        "recurrent_variant_index",
    }
    payload = {
        key: value for key, value in row.items() if key not in prohibited
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
