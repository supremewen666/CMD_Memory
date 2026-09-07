"""Group B experiment contracts for Mix GHOST, ecology, and safe repair.

This is a result-free evaluation layer.  It consumes per-case arm outcomes from
an isolated executor, produces family-blocked metrics, and records unavailable
data as explicit skipped rows instead of silently dropping denominators.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence


SCHEMA_VERSION = "cmd-group-b-experiment-v1"
TRACKS = ("mix_ghost_routing", "skill_ecology", "safe_memory_repair")
ROUTER_ARMS = ("random_legal", "best_global_static", "global_thompson", "niche_thompson", "contextual_bandit", "ghost_hierarchy", "mix_ghost", "oracle_legal_operator")
ECOLOGY_ARMS = ("no_skill", "seed_frozen", "add_only", "add_dedup", "add_revision", "add_revision_retirement", "full_ecology", "random_key_ecology", "oracle_library")
REPAIR_ARMS = ("detection_only", "in_place", "copy_on_write", "ecc_no_cas", "ecc_cas", "full_governance", "oracle_repair")
INCIDENT_CLASSES = ("process_fault", "state_drift", "poison", "clean", "unknown")

# These are execution contracts, deliberately separate from the result reader
# below.  They make a proposed run fail closed until the F-* content hashes and
# a family-blocked adapter are supplied.  In particular they do not materialize
# cases from a public dataset or inspect prior run directories.
STAGE_ARMS: Mapping[str, tuple[str, ...]] = {
    "stage5_router": ROUTER_ARMS,
    "stage6_ecology": ECOLOGY_ARMS,
    "stage7_repair": REPAIR_ARMS,
    "stage8a_transfer_state": (
        "no_repair", "random_legal", "skill_content_only", "reset_online",
        "frozen_source", "niche_shuffled", "mean_only", "reset_prefix",
        "source_prefix", "oracle_legal_operator",
    ),
    "stage8b_transfer_skills": (
        "seed_only", "source_skills", "target_native_skills", "oracle_library",
    ),
}
_REQUIRED_FREEZES = (
    "F-DATA", "F-MG-ALG", "F-SKILL", "F-SYNDROME", "F-REWARD", "F-EVAL",
    "F-MODEL", "F-BASELINE",
)


class ExperimentAdapter(Protocol):
    """Future data/executor seam; it may expose decision-view fields only."""

    def iter_cases(self, *, split: str, seed: int) -> Sequence[Mapping[str, object]]:
        """Return family-blocked decision views, never evaluator-only labels."""


@dataclass(frozen=True)
class FreezeManifest:
    """Content-addressed preregistration inputs required before execution."""

    freezes: Mapping[str, str]
    data_source_manifest_sha256: str
    split_manifest_sha256: str
    adapter_id: str
    adapter_sha256: str
    schema_version: str = "cmd-mix-ghost-freeze-v1"

    def __post_init__(self) -> None:
        if set(self.freezes) != set(_REQUIRED_FREEZES):
            raise ValueError("freeze manifest must contain exactly the required F-* IDs")
        values = (*self.freezes.values(), self.data_source_manifest_sha256,
                  self.split_manifest_sha256, self.adapter_sha256)
        if not self.adapter_id or any(not _is_sha256(value) for value in values):
            raise ValueError("freeze manifest requires adapter ID and SHA-256 values")

    @property
    def content_sha256(self) -> str:
        return canonical_sha256(asdict(self))

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "FreezeManifest":
        expected = {"schema_version", "freezes", "data_source_manifest_sha256",
                    "split_manifest_sha256", "adapter_id", "adapter_sha256"}
        if set(value) != expected:
            raise ValueError("freeze manifest uses an unsupported schema")
        return cls(**value)  # type: ignore[arg-type]


@dataclass(frozen=True)
class SeedOrderManifest:
    event_order_manifest_sha256: str
    seeds: tuple[int, ...]
    schema_version: str = "cmd-mix-ghost-seed-order-v1"

    def __post_init__(self) -> None:
        if not _is_sha256(self.event_order_manifest_sha256) or not self.seeds:
            raise ValueError("seed/order manifest requires an order hash and seeds")
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("execution seeds must be unique")

    @property
    def content_sha256(self) -> str:
        return canonical_sha256(asdict(self))

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "SeedOrderManifest":
        expected = {"schema_version", "event_order_manifest_sha256", "seeds"}
        if set(value) != expected or not isinstance(value.get("seeds"), list):
            raise ValueError("seed/order manifest uses an unsupported schema")
        return cls(value["event_order_manifest_sha256"], tuple(value["seeds"]), value["schema_version"])  # type: ignore[arg-type]


@dataclass(frozen=True)
class StageExecutionConfig:
    """Frozen setup for one orthogonal Stage 5--8 experimental slice."""

    stage: str
    arms: tuple[str, ...]
    scored_split: str
    router_updates: bool
    source_residual_snapshot_sha256: str | None = None
    target_prefix_split: str | None = None

    def __post_init__(self) -> None:
        if self.stage not in STAGE_ARMS or tuple(self.arms) != STAGE_ARMS[self.stage]:
            raise ValueError("stage requires its complete preregistered arm matrix in canonical order")
        if self.scored_split not in {"T_online", "T_anchor", "T_final"}:
            raise ValueError("stage scored split must be a sealed T_* partition")
        source = self.source_residual_snapshot_sha256
        if source is not None and not _is_sha256(source):
            raise ValueError("source residual snapshot must be a SHA-256")
        if self.stage == "stage5_router" and source is not None:
            raise ValueError("Stage 5 must not import the source residual snapshot")
        if self.stage == "stage5_router" and not self.router_updates:
            raise ValueError("Stage 5 must provide selected-action updates to every adaptive arm")
        if self.stage in {"stage6_ecology", "stage7_repair"} and (self.router_updates or source is None):
            raise ValueError("Stages 6/7 require a frozen residual snapshot and disabled router updates")
        if self.stage == "stage8a_transfer_state" and source is None:
            raise ValueError("Stage 8A requires the frozen source residual snapshot")
        if self.stage == "stage8b_transfer_skills" and source is not None:
            raise ValueError("Stage 8B resets residual state and cannot import a source snapshot")
        if self.stage.startswith("stage8") and self.router_updates:
            raise ValueError("Stage 8 update behavior is arm-specific and may not be globally enabled")
        if self.stage == "stage8a_transfer_state":
            if self.target_prefix_split is None or self.target_prefix_split == self.scored_split:
                raise ValueError("Stage 8A requires a target prefix split disjoint from scoring")
        elif self.target_prefix_split is not None:
            raise ValueError("only Stage 8A may declare a target prefix split")


@dataclass(frozen=True)
class ExecutionPlan:
    stage: Mapping[str, object]
    freeze_manifest_sha256: str
    seed_order_manifest_sha256: str
    status: str
    blockers: tuple[str, ...]
    plan_sha256: str

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


def plan_stage_execution(
    stage: StageExecutionConfig,
    freeze: FreezeManifest,
    seed_order: SeedOrderManifest,
    *,
    adapter_available: bool,
) -> ExecutionPlan:
    """Create a reproducible, result-free execution plan.

    An unavailable adapter remains a blocker: this method never substitutes a
    synthetic outcome or opens evaluator-only fields to make a plan runnable.
    """
    blockers = () if adapter_available else (
        "ADAPTER_TODO: no F-DATA-verified decision-view adapter is available; no cases were executed",
    )
    status = "READY" if not blockers else "BLOCKED_ADAPTER"
    body = {"stage": asdict(stage), "freeze_manifest_sha256": freeze.content_sha256,
            "seed_order_manifest_sha256": seed_order.content_sha256,
            "status": status, "blockers": list(blockers)}
    return ExecutionPlan(**body, plan_sha256=canonical_sha256(body))


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class GroupBConfig:
    track: str
    arms: tuple[str, ...]
    lambda_locality: float = 0.0
    lambda_compute: float = 0.0
    version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        permitted = {"mix_ghost_routing": ROUTER_ARMS, "skill_ecology": ECOLOGY_ARMS, "safe_memory_repair": REPAIR_ARMS}
        if self.track not in permitted or not self.arms or len(set(self.arms)) != len(self.arms):
            raise ValueError("invalid Group B track or arm set")
        if set(self.arms) - set(permitted[self.track]):
            raise ValueError("arm is not permitted for track")
        if min(self.lambda_locality, self.lambda_compute) < 0:
            raise ValueError("utility penalties must be non-negative")


@dataclass(frozen=True)
class BlockedData:
    case_id: str
    family_id: str
    reason: str


@dataclass(frozen=True)
class ArmOutcome:
    case_id: str
    family_id: str
    incident_type: str
    arm_id: str
    committed: bool = False
    resolved: bool = False
    invariants_passed: bool = False
    safety_passed: bool = False
    locality_passed: bool = False
    locality_cost: float = 0.0
    compute_cost: float = 0.0
    abstained: bool = False
    selected_skill_id: str | None = None
    legal_candidate_count: int = 0
    lifecycle_event: str | None = None
    niche_id: str | None = None
    anchor: bool = False

    def __post_init__(self) -> None:
        if self.incident_type not in INCIDENT_CLASSES:
            raise ValueError("incident_type must be protocol-defined")
        if min(self.locality_cost, self.compute_cost) < 0 or self.legal_candidate_count < 0:
            raise ValueError("costs and candidate count must be non-negative")


def safe_utility(row: ArmOutcome, config: GroupBConfig) -> float:
    """Evaluator utility; this deliberately is not the router residual target."""
    if row.committed and (not row.resolved or not row.invariants_passed or not row.safety_passed or not row.locality_passed):
        return -1.0
    if not row.committed:
        return 0.0
    return max(0.0, min(1.0, 1.0 - config.lambda_locality * row.locality_cost - config.lambda_compute * row.compute_cost))


@dataclass(frozen=True)
class GroupBReport:
    schema_version: str
    config: Mapping[str, object]
    status: str
    metrics: Mapping[str, object]
    skipped: tuple[Mapping[str, str], ...]
    report_sha256: str

    def to_mapping(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "config": dict(self.config), "status": self.status, "metrics": dict(self.metrics), "skipped": [dict(row) for row in self.skipped], "report_sha256": self.report_sha256}


class GroupBExperimentRunner:
    def __init__(self, config: GroupBConfig) -> None:
        self.config = config

    def evaluate(self, outcomes: Sequence[ArmOutcome], *, blocked: Sequence[BlockedData] = ()) -> GroupBReport:
        rows = tuple(outcomes)
        if any(row.arm_id not in self.config.arms for row in rows):
            raise ValueError("outcome arm is absent from frozen config")
        by_case: dict[str, dict[str, ArmOutcome]] = {}
        for row in rows:
            by_case.setdefault(row.case_id, {})[row.arm_id] = row
        metrics: dict[str, object] = {"case_count": len(by_case), "family_count": len({row.family_id for row in rows}), "arms": {}}
        for arm in self.config.arms:
            arm_rows = tuple(row for row in rows if row.arm_id == arm)
            metrics["arms"][arm] = self._arm_metrics(arm_rows)
        if self.config.track == "mix_ghost_routing":
            metrics["router"] = self._router_metrics(by_case)
        elif self.config.track == "skill_ecology":
            metrics["ecology"] = self._ecology_metrics(rows)
        else:
            metrics["repair"] = self._repair_metrics(rows)
        skipped = tuple({"case_id": row.case_id, "family_id": row.family_id, "reason": row.reason} for row in blocked)
        status = "SKIPPED_NO_EXECUTABLE_DATA" if not rows else ("COMPLETE_WITH_SKIPPED_DATA" if skipped else "COMPLETE")
        payload = {"schema_version": SCHEMA_VERSION, "config": asdict(self.config), "status": status, "metrics": metrics, "skipped": list(skipped)}
        return GroupBReport(SCHEMA_VERSION, asdict(self.config), status, metrics, skipped, canonical_sha256(payload))

    def write(self, path: Path, outcomes: Sequence[ArmOutcome], *, blocked: Sequence[BlockedData] = ()) -> GroupBReport:
        report = self.evaluate(outcomes, blocked=blocked)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report.to_mapping(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return report

    def _arm_metrics(self, rows: Sequence[ArmOutcome]) -> Mapping[str, object]:
        utilities = [safe_utility(row, self.config) for row in rows]
        per_incident = {incident: [row for row in rows if row.incident_type == incident] for incident in INCIDENT_CLASSES}
        return {"event_micro_utility": _mean(utilities), "family_macro_utility": _family_macro(rows, lambda row: safe_utility(row, self.config)), "false_commit_rate": _rate(rows, lambda row: row.committed and safe_utility(row, self.config) < 0), "clean_false_repair_rate": _rate([row for row in rows if row.incident_type == "clean"], lambda row: row.committed), "incident": {key: {"count": len(value), "utility": _mean([safe_utility(row, self.config) for row in value])} for key, value in per_incident.items()}}

    def _router_metrics(self, by_case: Mapping[str, Mapping[str, ArmOutcome]]) -> Mapping[str, object]:
        regrets: dict[str, list[tuple[str, float]]] = {arm: [] for arm in self.config.arms if arm != "oracle_legal_operator"}
        for case_rows in by_case.values():
            oracle = case_rows.get("oracle_legal_operator")
            if oracle is None:
                continue
            oracle_u = safe_utility(oracle, self.config)
            for arm in regrets:
                if arm in case_rows:
                    regrets[arm].append((case_rows[arm].family_id, (oracle_u - safe_utility(case_rows[arm], self.config)) / 2.0))
        result = {arm: _grouped_mean(values) for arm, values in regrets.items()}
        if "mix_ghost" in result:
            baseline = min((value for arm, value in result.items() if arm != "mix_ghost"), default=None)
            result["delta_c1_vs_strongest_baseline"] = None if baseline is None else baseline - result["mix_ghost"]
        return {"family_macro_normalized_safe_regret": result}

    def _ecology_metrics(self, rows: Sequence[ArmOutcome]) -> Mapping[str, object]:
        full = [row for row in rows if row.arm_id == "full_ecology"]
        niche_pairs = {(row.selected_skill_id, row.niche_id) for row in full if row.selected_skill_id and row.niche_id}
        return {"anchor_family_macro_utility": _family_macro([row for row in full if row.anchor], lambda row: safe_utility(row, self.config)), "skill_niche_pairs": len(niche_pairs), "lifecycle_counts": {event: sum(row.lifecycle_event == event for row in full) for event in ("birth", "activation", "revision", "quarantine", "retirement")}}

    def _repair_metrics(self, rows: Sequence[ArmOutcome]) -> Mapping[str, object]:
        result: dict[str, object] = {}
        for incident in INCIDENT_CLASSES:
            selected = [row for row in rows if row.incident_type == incident]
            result[incident] = {"count": len(selected), "safe_repair_success": _rate(selected, lambda row: row.committed and row.resolved and row.invariants_passed and row.safety_passed and row.locality_passed), "false_commit": _rate(selected, lambda row: row.committed and safe_utility(row, self.config) < 0)}
        return result


def _mean(values: Sequence[float]) -> float | None:
    return None if not values else sum(values) / len(values)


def _rate(
    rows: Sequence[ArmOutcome], predicate: Callable[[ArmOutcome], bool]
) -> float | None:
    return None if not rows else sum(predicate(row) for row in rows) / len(rows)


def _grouped_mean(values: Sequence[tuple[str, float]]) -> float | None:
    grouped: dict[str, list[float]] = {}
    for family, value in values:
        grouped.setdefault(family, []).append(value)
    return _mean([_mean(values) for values in grouped.values() if _mean(values) is not None])


def _family_macro(
    rows: Sequence[ArmOutcome], metric: Callable[[ArmOutcome], float]
) -> float | None:
    return _grouped_mean([(row.family_id, metric(row)) for row in rows])
