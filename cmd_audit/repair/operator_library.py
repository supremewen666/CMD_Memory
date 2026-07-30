"""Append-only records and storage for Skill self-evolution experiments.

The legacy :mod:`cmd_audit.repair.governance` module models the exploratory
Exp24 operator bank.  This module implements the stricter, versioned storage
contract used by Experiments 24A/24B.  Executable revisions are immutable;
weights and lifecycle state are represented by new records and library
versions rather than by mutating a revision.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..counterfactual.operators import OperatorSpec


GRAMMAR_VERSION = "operator-spec-v1"
EXECUTOR_VERSION = "counterfactual-executor-v1"
EVOLUTION_ARMS = ("patterned", "unkeyed_global", "no_update")
LIFECYCLE_STATES = ("candidate", "provisional_active", "stable", "retired")


@dataclass(frozen=True)
class CompositeOperatorSpec:
    """An ordered, executable sequence of operator stages.

    A composite is not flattened into one :class:`OperatorSpec`: two stages
    may intentionally act at the same generation point, and flattening would
    erase the causal order that a chain experiment is designed to measure.
    """

    stages: tuple[OperatorSpec, ...]

    def __post_init__(self) -> None:
        if len(self.stages) < 2:
            raise ValueError("composite operator requires at least two stages")
        if any(not stage.steps and not stage.item_signal_hints for stage in self.stages):
            raise ValueError("composite operator stages cannot be identity")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "sequential_composite",
            "stages": [stage.to_dict() for stage in self.stages],
        }

    def content_hash(self) -> str:
        return content_id("composite-spec", self.to_dict())

    def format(self) -> str:
        return " -> ".join(stage.format() for stage in self.stages)

    @property
    def last_action(self):
        """Classify the composite by its terminal repair family.

        This makes a deposited ``retrieval -> conflict`` skill compete in the
        conflict niche, so replacement versus complementarity is observable.
        """
        return self.stages[-1].last_action


def merge_operators(
    operators: Iterable[OperatorSpec],
) -> CompositeOperatorSpec:
    """Create a true sequential composite without mutating its parents."""
    stages = tuple(operators)
    return CompositeOperatorSpec(stages=stages)


def canonical_json(value: Any) -> str:
    """Return deterministic JSON for content addressing and manifests."""
    return json.dumps(
        _jsonable(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def content_id(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest}"


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PatternRecord:
    pattern_id: str
    prototype_hash: str
    canonical_fingerprint: str
    gold_free_feature_hash: str
    catalog_version: str
    linked_family_ids: tuple[str, ...] = ()
    audit_counters: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class OperatorSpecRecord:
    spec_hash: str
    canonical_spec: str
    operator_shape_signature: str
    grammar_version: str = GRAMMAR_VERSION
    executor_version: str = EXECUTOR_VERSION

    @classmethod
    def from_operator(
        cls,
        spec: OperatorSpec,
        *,
        grammar_version: str = GRAMMAR_VERSION,
        executor_version: str = EXECUTOR_VERSION,
    ) -> "OperatorSpecRecord":
        canonical = canonical_json(spec.to_dict())
        spec_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        shape_payload = {
            "steps": [
                {
                    "generation_point": step.generation_point,
                    "action": step.action.value,
                    "select": step.selector,
                    "transform": step.transform,
                }
                for step in sorted(
                    spec.steps,
                    key=lambda item: (
                        item.generation_point,
                        item.action.value,
                        item.selector,
                        item.transform,
                    ),
                )
            ]
        }
        return cls(
            spec_hash=spec_hash,
            canonical_spec=canonical,
            operator_shape_signature=hash_text(canonical_json(shape_payload)),
            grammar_version=grammar_version,
            executor_version=executor_version,
        )

    def to_operator(self) -> OperatorSpec:
        return OperatorSpec.from_dict(json.loads(self.canonical_spec))


@dataclass(frozen=True)
class SkillRevisionRecord:
    revision_id: str
    arm_id: str
    family_id: str
    pattern_ids: tuple[str, ...]
    spec_hash: str
    parent_revision_id: str | None
    derivation_kind: str
    producing_case_id: str
    producing_tape_event_id: str
    creation_library_version: str
    created_after_case_index: int


@dataclass(frozen=True)
class CandidateManifestEntry:
    spec_hash: str
    recovery_gain: float
    rollout_cost: float
    accepted: bool
    rejection_reason: str
    selected_rank: int | None = None


@dataclass(frozen=True)
class ExperienceTapeEvent:
    tape_event_id: str
    case_id: str
    pre_repair_snapshot_hash: str
    discovery_config_hash: str
    candidate_manifest: tuple[CandidateManifestEntry, ...]
    selected_specs: tuple[str, ...]
    scorer_version: str
    threshold: float
    created_after_all_arm_outcomes: bool


@dataclass(frozen=True)
class RevisionEvidenceEvent:
    evidence_id: str
    arm_id: str
    revision_id: str
    case_id: str
    library_version_before_case: str
    evidence_kind: str
    recovery_gain: float
    binary_success: bool
    rollout_cost: float
    eligible_for_weight: bool
    eligible_for_stable_validation: bool
    recurrent_family_id_audit_only: str | None = None


@dataclass(frozen=True)
class OperatorWeightSnapshot:
    snapshot_id: str
    arm_id: str
    revision_id: str
    library_version: str
    success_count: int
    failure_count: int
    beta_alpha: int
    beta_beta: int
    weight: float
    supporting_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class RevisionAnchorSet:
    anchor_set_id: str
    stable_revision_id: str
    producing_case_id: str
    validation_case_ids: tuple[str, str, str]
    family_ids_audit_only: tuple[str, ...]
    creation_outcome_vector: tuple[bool, bool, bool, bool]
    pre_repair_snapshot_hashes: tuple[str, str, str, str]
    created_at_library_version: str


@dataclass(frozen=True)
class LifecycleEvent:
    event_id: str
    arm_id: str
    revision_id: str
    from_state: str
    to_state: str
    reason_code: str
    supporting_evidence_ids: tuple[str, ...]
    effective_library_version: str


@dataclass(frozen=True)
class LibraryVersion:
    library_version_id: str
    arm_id: str
    parent_version_id: str | None
    effective_after_case_id: str | None
    pattern_catalog_hash: str
    active_revision_ids: tuple[str, ...]
    stable_revision_ids: tuple[str, ...]
    retired_revision_ids: tuple[str, ...]
    weight_snapshot_ids: tuple[str, ...]
    lifecycle_event_ids: tuple[str, ...]
    tape_event_id: str | None
    manifest_hash: str


@dataclass(frozen=True)
class TapeCandidate:
    spec: OperatorSpec
    recovery_gain: float
    rollout_cost: float
    accepted: bool = True
    rejection_reason: str = ""


def build_experience_tape(
    *,
    case_id: str,
    pre_repair_snapshot_hash: str,
    discovery_config_hash: str,
    candidates: Iterable[TapeCandidate],
    scorer_version: str,
    threshold: float = 0.1,
    created_after_all_arm_outcomes: bool,
) -> tuple[ExperienceTapeEvent, tuple[OperatorSpecRecord, ...]]:
    """Create the deterministic, arm-independent Top-3 experience event."""
    if not created_after_all_arm_outcomes:
        raise ValueError("shadow discovery requires all arm outcomes first")
    raw: list[tuple[OperatorSpecRecord, TapeCandidate]] = [
        (OperatorSpecRecord.from_operator(item.spec), item) for item in candidates
    ]
    ordered = sorted(
        raw,
        key=lambda item: (
            -float(item[1].recovery_gain),
            float(item[1].rollout_cost),
            item[0].spec_hash,
        ),
    )
    selected: list[str] = []
    selected_records: list[OperatorSpecRecord] = []
    seen: set[str] = set()
    for record, item in ordered:
        if (
            item.accepted
            and float(item.recovery_gain) >= threshold
            and record.spec_hash not in seen
            and len(selected) < 3
        ):
            selected.append(record.spec_hash)
            selected_records.append(record)
            seen.add(record.spec_hash)

    rank_by_hash = {spec_hash: rank for rank, spec_hash in enumerate(selected, 1)}
    manifest_rows: list[CandidateManifestEntry] = []
    ranked_once: set[str] = set()
    for record, item in ordered:
        selected_rank = None
        if record.spec_hash in rank_by_hash and record.spec_hash not in ranked_once:
            selected_rank = rank_by_hash[record.spec_hash]
            ranked_once.add(record.spec_hash)
        manifest_rows.append(
            CandidateManifestEntry(
                spec_hash=record.spec_hash,
                recovery_gain=float(item.recovery_gain),
                rollout_cost=float(item.rollout_cost),
                accepted=bool(item.accepted),
                rejection_reason=(
                    item.rejection_reason
                    or (
                        ""
                        if selected_rank is not None
                        else "below_threshold"
                        if item.recovery_gain < threshold
                        else "duplicate_or_top3_cap"
                    )
                ),
                selected_rank=selected_rank,
            )
        )
    manifest = tuple(manifest_rows)
    payload = {
        "case_id": case_id,
        "pre_repair_snapshot_hash": pre_repair_snapshot_hash,
        "discovery_config_hash": discovery_config_hash,
        "candidate_manifest": manifest,
        "selected_specs": selected,
        "scorer_version": scorer_version,
        "threshold": threshold,
        "created_after_all_arm_outcomes": True,
    }
    event = ExperienceTapeEvent(
        tape_event_id=content_id("tape", payload),
        case_id=case_id,
        pre_repair_snapshot_hash=pre_repair_snapshot_hash,
        discovery_config_hash=discovery_config_hash,
        candidate_manifest=manifest,
        selected_specs=tuple(selected),
        scorer_version=scorer_version,
        threshold=float(threshold),
        created_after_all_arm_outcomes=True,
    )
    return event, tuple(selected_records)


def patterned_family_id(pattern_id: str, operator_shape_signature: str) -> str:
    return content_id("family", [pattern_id, operator_shape_signature])


def unkeyed_family_id(operator_shape_signature: str) -> str:
    return content_id("family", ["global", operator_shape_signature])


class AppendOnlyEvolutionStore:
    """In-memory append-only ledger with deterministic JSONL persistence."""

    def __init__(self) -> None:
        self.patterns: dict[str, PatternRecord] = {}
        self.specs: dict[str, OperatorSpecRecord] = {}
        self.revisions: dict[str, SkillRevisionRecord] = {}
        self.tape_events: dict[str, ExperienceTapeEvent] = {}
        self.evidence: dict[str, RevisionEvidenceEvent] = {}
        self.weight_snapshots: dict[str, OperatorWeightSnapshot] = {}
        self.anchor_sets: dict[str, RevisionAnchorSet] = {}
        self.lifecycle_events: dict[str, LifecycleEvent] = {}
        self.library_versions: dict[str, LibraryVersion] = {}
        self._version_order: dict[str, list[str]] = {
            arm: [] for arm in EVOLUTION_ARMS
        }

    def _append(self, collection: dict[str, Any], key: str, value: Any) -> None:
        existing = collection.get(key)
        if existing is not None and existing != value:
            raise ValueError(f"content-address collision or immutable rewrite: {key}")
        collection.setdefault(key, value)

    def append_pattern(self, record: PatternRecord) -> None:
        self._append(self.patterns, record.pattern_id, record)

    def append_spec(self, record: OperatorSpecRecord) -> None:
        self._append(self.specs, record.spec_hash, record)

    def append_revision(self, record: SkillRevisionRecord) -> None:
        if record.arm_id not in EVOLUTION_ARMS:
            raise ValueError(f"unknown arm: {record.arm_id}")
        if record.arm_id == "patterned" and not record.pattern_ids:
            raise ValueError("patterned revisions require pattern_ids")
        if record.arm_id == "unkeyed_global" and record.pattern_ids:
            raise ValueError("unkeyed revisions cannot carry pattern_ids")
        if record.spec_hash not in self.specs:
            raise ValueError(f"unknown spec: {record.spec_hash}")
        self._append(self.revisions, record.revision_id, record)

    def append_tape(self, record: ExperienceTapeEvent) -> None:
        if len(record.selected_specs) > 3:
            raise ValueError("one tape event may select at most three specs")
        self._append(self.tape_events, record.tape_event_id, record)

    def append_evidence(self, record: RevisionEvidenceEvent) -> None:
        if record.revision_id not in self.revisions:
            raise ValueError(f"unknown revision: {record.revision_id}")
        if record.eligible_for_weight:
            duplicate = any(
                item.arm_id == record.arm_id
                and item.revision_id == record.revision_id
                and item.case_id == record.case_id
                and item.eligible_for_weight
                for item in self.evidence.values()
            )
            if duplicate:
                raise ValueError("one case may update a revision posterior only once")
        self._append(self.evidence, record.evidence_id, record)

    def append_weight_snapshot(self, record: OperatorWeightSnapshot) -> None:
        self._append(self.weight_snapshots, record.snapshot_id, record)

    def append_anchor_set(self, record: RevisionAnchorSet) -> None:
        if len(record.validation_case_ids) != 3:
            raise ValueError("anchor set requires exactly three validation cases")
        if len(set((record.producing_case_id,) + record.validation_case_ids)) != 4:
            raise ValueError("anchor set members must be four distinct cases")
        self._append(self.anchor_sets, record.anchor_set_id, record)

    def append_lifecycle_event(self, record: LifecycleEvent) -> None:
        if record.from_state not in LIFECYCLE_STATES:
            raise ValueError(f"invalid from_state: {record.from_state}")
        if record.to_state not in LIFECYCLE_STATES:
            raise ValueError(f"invalid to_state: {record.to_state}")
        allowed = {
            ("candidate", "provisional_active"),
            ("provisional_active", "stable"),
            ("provisional_active", "retired"),
            ("stable", "retired"),
        }
        if (record.from_state, record.to_state) not in allowed:
            raise ValueError("illegal lifecycle transition")
        self._append(self.lifecycle_events, record.event_id, record)

    def create_empty_library(
        self,
        arm_id: str,
        *,
        pattern_catalog_hash: str,
    ) -> LibraryVersion:
        if arm_id not in EVOLUTION_ARMS:
            raise ValueError(f"unknown arm: {arm_id}")
        if self._version_order[arm_id]:
            raise ValueError(f"{arm_id} already has an initial library")
        return self.commit_library_version(
            arm_id=arm_id,
            parent_version_id=None,
            effective_after_case_id=None,
            pattern_catalog_hash=pattern_catalog_hash,
            active_revision_ids=(),
            stable_revision_ids=(),
            retired_revision_ids=(),
            weight_snapshot_ids=(),
            lifecycle_event_ids=(),
            tape_event_id=None,
        )

    def commit_library_version(
        self,
        *,
        arm_id: str,
        parent_version_id: str | None,
        effective_after_case_id: str | None,
        pattern_catalog_hash: str,
        active_revision_ids: Iterable[str],
        stable_revision_ids: Iterable[str],
        retired_revision_ids: Iterable[str],
        weight_snapshot_ids: Iterable[str],
        lifecycle_event_ids: Iterable[str],
        tape_event_id: str | None,
    ) -> LibraryVersion:
        if arm_id not in EVOLUTION_ARMS:
            raise ValueError(f"unknown arm: {arm_id}")
        order = self._version_order[arm_id]
        if parent_version_id != (order[-1] if order else None):
            raise ValueError("library versions must extend the current arm head")
        if parent_version_id:
            parent = self.library_versions[parent_version_id]
            if parent.pattern_catalog_hash != pattern_catalog_hash:
                raise ValueError("Pattern catalog hash changed after L0")
        active = tuple(dict.fromkeys(active_revision_ids))
        stable = tuple(dict.fromkeys(stable_revision_ids))
        retired = tuple(dict.fromkeys(retired_revision_ids))
        weights = tuple(dict.fromkeys(weight_snapshot_ids))
        lifecycle = tuple(dict.fromkeys(lifecycle_event_ids))
        if set(stable) - set(active):
            raise ValueError("stable revisions must be active")
        if set(active) & set(retired):
            raise ValueError("retired revisions cannot remain active")
        if arm_id == "no_update" and (active or stable or retired or weights or lifecycle):
            raise ValueError("no_update must keep an empty Active Skill Set")
        if any(self.revisions[item].arm_id != arm_id for item in active + retired):
            raise ValueError("library cannot reference another arm's revision")
        payload = {
            "arm_id": arm_id,
            "parent_version_id": parent_version_id,
            "effective_after_case_id": effective_after_case_id,
            "pattern_catalog_hash": pattern_catalog_hash,
            "active_revision_ids": active,
            "stable_revision_ids": stable,
            "retired_revision_ids": retired,
            "weight_snapshot_ids": weights,
            "lifecycle_event_ids": lifecycle,
            "tape_event_id": tape_event_id,
        }
        manifest_hash = hash_text(canonical_json(payload))
        version = LibraryVersion(
            library_version_id=content_id("library", payload),
            manifest_hash=manifest_hash,
            **payload,
        )
        self._append(self.library_versions, version.library_version_id, version)
        if not order or order[-1] != version.library_version_id:
            order.append(version.library_version_id)
        return version

    def head(self, arm_id: str) -> LibraryVersion:
        return self.library_versions[self._version_order[arm_id][-1]]

    def lifecycle_state(self, revision_id: str, version: LibraryVersion) -> str:
        if revision_id in version.retired_revision_ids:
            return "retired"
        if revision_id in version.stable_revision_ids:
            return "stable"
        if revision_id in version.active_revision_ids:
            return "provisional_active"
        return "candidate"

    def evidence_for(self, arm_id: str, revision_id: str) -> tuple[RevisionEvidenceEvent, ...]:
        return tuple(
            item
            for item in self.evidence.values()
            if item.arm_id == arm_id and item.revision_id == revision_id
        )

    def latest_weight(
        self, version: LibraryVersion, revision_id: str
    ) -> OperatorWeightSnapshot | None:
        candidates = [
            self.weight_snapshots[item]
            for item in version.weight_snapshot_ids
            if self.weight_snapshots[item].revision_id == revision_id
        ]
        return candidates[-1] if candidates else None

    def write_jsonl_artifacts(self, root: str | Path) -> None:
        """Write the required append-only ledgers in deterministic order."""
        output = Path(root)
        output.mkdir(parents=True, exist_ok=True)
        tables: tuple[tuple[str, Mapping[str, Any]], ...] = (
            ("experience_tape.jsonl", self.tape_events),
            ("revision_records.jsonl", self.revisions),
            ("revision_evidence.jsonl", self.evidence),
            ("operator_weight_snapshots.jsonl", self.weight_snapshots),
            ("lifecycle_events.jsonl", self.lifecycle_events),
            ("anchor_sets.jsonl", self.anchor_sets),
            ("library_versions.jsonl", self.library_versions),
        )
        for filename, values in tables:
            path = output / filename
            text = "".join(
                canonical_json(value) + "\n" for value in values.values()
            )
            path.write_text(text, encoding="utf-8")


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"not JSON serializable: {type(value).__name__}")
