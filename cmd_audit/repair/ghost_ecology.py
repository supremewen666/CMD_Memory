"""Open-world GHOST ecology for failure-memory, pattern and repair-skill sedimentation.

The core is deterministic and performs no model calls.  It separates routing from
discovery/governance: a router may update the selected skill posterior, while only
explicit governance can change pattern, skill, niche or registry lifecycle state.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import random
from statistics import fmean
from typing import Mapping, Sequence


SCHEMA_VERSION = "cmd-ghost-ecology-v2"
EVENT_TYPES = frozenset(
    {
        "failure_observed",
        "pattern_revision",
        "pattern_binding",
        "skill_revision",
        "pattern_skill_binding",
        "selection",
        "skill_feedback",
        "posterior_snapshot",
        "lifecycle_transition",
        "niche_snapshot",
        "niche_transition",
        "registry_snapshot",
        "discovery_pressure",
    }
)
PATTERN_STATES = frozenset(
    {"candidate", "recurring", "validated", "stable", "split", "merged", "deprecated"}
)
SKILL_STATES = frozenset(
    {"proposed", "sandboxed", "shadow_validated", "calibrated", "stable", "revised", "retired"}
)
NICHE_STATES = frozenset(
    {"latent", "emerging", "occupied", "stable", "contested", "branching", "collapsing", "extinct"}
)
_PATTERN_TRANSITIONS = frozenset(
    {
        ("candidate", "recurring"), ("recurring", "validated"),
        ("validated", "stable"), ("stable", "split"),
        ("stable", "merged"), ("stable", "deprecated"),
        ("split", "deprecated"), ("merged", "deprecated"),
    }
)
_SKILL_TRANSITIONS = frozenset(
    {
        ("proposed", "sandboxed"), ("sandboxed", "shadow_validated"),
        ("shadow_validated", "calibrated"), ("calibrated", "stable"),
        ("stable", "revised"), ("proposed", "retired"),
        ("sandboxed", "retired"), ("shadow_validated", "retired"),
        ("calibrated", "retired"), ("stable", "retired"),
    }
)
# Niches need their own table for the same reason patterns and skills do: without
# one, ``lifecycle_transition`` accepted any pair of distinct NICHE_STATES, so a
# ledger could record ``extinct -> latent`` and audit clean.  A niche is observed
# rather than promoted, so the legal moves are the observable ones: occupation
# rises, contention resolves either way, and extinction is terminal.
_NICHE_TRANSITIONS = frozenset(
    {
        ("latent", "emerging"), ("emerging", "occupied"), ("emerging", "stable"),
        ("emerging", "extinct"),
        ("occupied", "stable"), ("occupied", "contested"), ("occupied", "collapsing"),
        ("stable", "contested"), ("stable", "branching"), ("stable", "collapsing"),
        ("contested", "occupied"), ("contested", "branching"),
        ("contested", "collapsing"),
        ("branching", "occupied"), ("branching", "stable"),
        ("collapsing", "occupied"), ("collapsing", "extinct"),
    }
)


def is_legal_niche_transition(from_state: str, to_state: str) -> bool:
    """Return whether two observed niche states form a legal ledger step."""
    if from_state not in NICHE_STATES or to_state not in NICHE_STATES:
        raise ValueError("unknown niche lifecycle state")
    return from_state == to_state or (from_state, to_state) in _NICHE_TRANSITIONS


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def content_sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _closed(value: Mapping[str, object], fields: set[str], name: str) -> None:
    if set(value) != fields:
        raise ValueError(f"{name} must be a closed mapping")


class EcologyLedger:
    """Hash-chained append-only event ledger with deterministic replay."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._events = self._read()

    @property
    def events(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(row) for row in self._events)

    @property
    def head_sha256(self) -> str:
        if not self._events:
            return content_sha256({"schema_version": SCHEMA_VERSION, "genesis": True})
        return str(self._events[-1]["event_sha256"])

    @property
    def last_event_index(self) -> int:
        return -1 if not self._events else int(self._events[-1]["event_index"])

    def append(
        self, event_type: str, *, event_index: int, payload: Mapping[str, object]
    ) -> dict[str, object]:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unregistered ecology event: {event_type}")
        if isinstance(event_index, bool) or not isinstance(event_index, int):
            raise TypeError("event_index must be an integer")
        if event_index <= self.last_event_index:
            raise ValueError("event_index must be strictly increasing")
        body = {
            "schema_version": SCHEMA_VERSION,
            "event_type": event_type,
            "event_index": event_index,
            "previous_event_sha256": self.head_sha256,
            "payload": dict(payload),
        }
        row = {
            **body,
            "event_id": f"ecology-{content_sha256(body)}",
            "event_sha256": content_sha256(body),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as stream:
            stream.write(_canonical(row) + b"\n")
            stream.flush()
        self._events.append(row)
        return dict(row)

    def by_type(self, event_type: str) -> tuple[dict[str, object], ...]:
        return tuple(row for row in self.events if row["event_type"] == event_type)

    def _read(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        events: list[dict[str, object]] = []
        previous = content_sha256({"schema_version": SCHEMA_VERSION, "genesis": True})
        last_index = -1
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            row = json.loads(line)
            if not isinstance(row, Mapping):
                raise ValueError(f"ledger line {number} is not an object")
            _closed(
                row,
                {
                    "schema_version", "event_type", "event_index", "previous_event_sha256",
                    "payload", "event_id", "event_sha256",
                },
                f"ledger line {number}",
            )
            body = {
                key: row[key]
                for key in (
                    "schema_version", "event_type", "event_index",
                    "previous_event_sha256", "payload",
                )
            }
            if (
                row["schema_version"] != SCHEMA_VERSION
                or row["event_type"] not in EVENT_TYPES
                or row["previous_event_sha256"] != previous
                or row["event_sha256"] != content_sha256(body)
                or row["event_id"] != f"ecology-{content_sha256(body)}"
                or int(row["event_index"]) <= last_index
            ):
                raise ValueError(f"ledger integrity failure at line {number}")
            previous = str(row["event_sha256"])
            last_index = int(row["event_index"])
            events.append(dict(row))
        return events


@dataclass(frozen=True)
class FailureDeposit:
    failure_id: str
    case_id: str
    family_id_audit_only: str
    failure_memory_sha256: str
    features: tuple[tuple[str, float], ...]
    context_sha256: str
    provenance_sha256: str

    def __post_init__(self) -> None:
        if tuple(sorted(self.features)) != self.features or len(dict(self.features)) != len(self.features):
            raise ValueError("failure features must be sorted and unique")
        for name, value in self.features:
            if not name:
                raise ValueError("failure feature name is empty")
            _finite(value, f"feature:{name}")

    def to_mapping(self) -> dict[str, object]:
        return {
            "failure_id": self.failure_id,
            "case_id": self.case_id,
            "family_id_audit_only": self.family_id_audit_only,
            "failure_memory_sha256": self.failure_memory_sha256,
            "features": [list(row) for row in self.features],
            "context_sha256": self.context_sha256,
            "provenance_sha256": self.provenance_sha256,
        }

    @classmethod
    def from_failure_memory(
        cls,
        record: object,
        *,
        failure_id: str,
        case_id: str,
        family_id_audit_only: str,
        features: Mapping[str, float],
        context_sha256: str,
        provenance_sha256: str,
    ) -> "FailureDeposit":
        """Bind an existing failure-memory record without duplicating its authority."""
        from dataclasses import asdict, is_dataclass

        raw = asdict(record) if is_dataclass(record) else record
        if not isinstance(raw, Mapping):
            raise TypeError("failure memory must be a dataclass or mapping")
        return cls(
            failure_id, case_id, family_id_audit_only, content_sha256(raw),
            tuple(sorted((str(key), _finite(value, f"feature:{key}")) for key, value in features.items())),
            context_sha256, provenance_sha256,
        )


@dataclass(frozen=True)
class PatternRevision:
    pattern_revision_id: str
    pattern_id: str
    predicate: Mapping[str, object]
    feature_signature: tuple[str, ...]
    parent_revision_ids: tuple[str, ...]
    derivation_kind: str
    state: str = "candidate"

    def __post_init__(self) -> None:
        if self.state not in PATTERN_STATES:
            raise ValueError("invalid pattern lifecycle state")
        if self.derivation_kind not in {"seed", "birth", "split", "merge", "revision"}:
            raise ValueError("invalid pattern derivation kind")
        if self.derivation_kind in {"split", "merge", "revision"} and not self.parent_revision_ids:
            raise ValueError("derived pattern requires parent revisions")

    @classmethod
    def create(
        cls,
        *,
        pattern_id: str,
        predicate: Mapping[str, object],
        feature_signature: Sequence[str],
        parent_revision_ids: Sequence[str] = (),
        derivation_kind: str = "birth",
        state: str = "candidate",
    ) -> "PatternRevision":
        payload = {
            "pattern_id": pattern_id,
            "predicate": dict(predicate),
            "feature_signature": sorted(set(feature_signature)),
            "parent_revision_ids": sorted(set(parent_revision_ids)),
            "derivation_kind": derivation_kind,
        }
        return cls(
            f"pattern-revision-{content_sha256(payload)}", pattern_id, dict(predicate),
            tuple(payload["feature_signature"]), tuple(payload["parent_revision_ids"]),
            derivation_kind, state,
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "pattern_revision_id": self.pattern_revision_id,
            "pattern_id": self.pattern_id,
            "predicate": dict(self.predicate),
            "feature_signature": list(self.feature_signature),
            "parent_revision_ids": list(self.parent_revision_ids),
            "derivation_kind": self.derivation_kind,
            "state": self.state,
        }

    @classmethod
    def from_pattern_record(
        cls, record: object, *, state: str = "stable"
    ) -> "PatternRevision":
        """Import the existing pattern catalog as a versioned V2 seed."""
        pattern_id = getattr(record, "pattern_id", None)
        fingerprint = getattr(record, "canonical_fingerprint", None)
        feature_hash = getattr(record, "gold_free_feature_hash", None)
        if not all(isinstance(row, str) and row for row in (pattern_id, fingerprint, feature_hash)):
            raise TypeError("record is not a compatible PatternRecord")
        return cls.create(
            pattern_id=pattern_id,
            predicate={"kind": "legacy_catalog_fingerprint", "fingerprint": fingerprint},
            feature_signature=(f"legacy-feature-hash:{feature_hash}",),
            derivation_kind="seed",
            state=state,
        )


@dataclass(frozen=True)
class PatternResponsibility:
    pattern_revision_id: str
    responsibility: float

    def __post_init__(self) -> None:
        value = _finite(self.responsibility, "responsibility")
        if not 0.0 <= value <= 1.0:
            raise ValueError("pattern responsibility must be in [0, 1]")


def validate_responsibilities(rows: Sequence[PatternResponsibility]) -> None:
    if not rows or len({row.pattern_revision_id for row in rows}) != len(rows):
        raise ValueError("pattern responsibilities must be non-empty and unique")
    if abs(sum(row.responsibility for row in rows) - 1.0) > 1e-9:
        raise ValueError("pattern responsibilities must sum to one")


@dataclass(frozen=True)
class SkillRevision:
    skill_revision_id: str
    skill_id: str
    program: Mapping[str, object]
    parameter_schema: Mapping[str, object]
    preconditions: tuple[Mapping[str, object], ...]
    postconditions: tuple[Mapping[str, object], ...]
    success_probe: Mapping[str, object]
    mutation_budget: Mapping[str, object]
    rollback_program: Mapping[str, object]
    parent_revision_ids: tuple[str, ...]
    derivation_kind: str
    producing_failure_id: str
    state: str = "proposed"

    def __post_init__(self) -> None:
        if self.state not in SKILL_STATES:
            raise ValueError("invalid skill lifecycle state")
        if self.derivation_kind not in {
            "seed", "discovery", "structural_revision", "chain_deposition", "migration"
        }:
            raise ValueError("invalid skill derivation kind")
        if self.derivation_kind in {"structural_revision", "chain_deposition", "migration"} and not self.parent_revision_ids:
            raise ValueError("derived skill requires parent lineage")
        probe_id = self.success_probe.get("probe_id")
        if not isinstance(probe_id, str) or not probe_id:
            raise ValueError("skill success probe requires probe_id")
        if not self.program or not self.rollback_program:
            raise ValueError("skill and rollback programs must be explicit")

    @classmethod
    def create(
        cls,
        *,
        skill_id: str,
        program: Mapping[str, object],
        parameter_schema: Mapping[str, object],
        preconditions: Sequence[Mapping[str, object]],
        postconditions: Sequence[Mapping[str, object]],
        success_probe: Mapping[str, object],
        mutation_budget: Mapping[str, object],
        rollback_program: Mapping[str, object],
        producing_failure_id: str,
        parent_revision_ids: Sequence[str] = (),
        derivation_kind: str = "discovery",
        state: str = "proposed",
    ) -> "SkillRevision":
        payload = {
            "skill_id": skill_id,
            "program": dict(program),
            "parameter_schema": dict(parameter_schema),
            "preconditions": [dict(row) for row in preconditions],
            "postconditions": [dict(row) for row in postconditions],
            "success_probe": dict(success_probe),
            "mutation_budget": dict(mutation_budget),
            "rollback_program": dict(rollback_program),
            "parent_revision_ids": sorted(set(parent_revision_ids)),
            "derivation_kind": derivation_kind,
            "producing_failure_id": producing_failure_id,
        }
        return cls(
            f"skill-revision-{content_sha256(payload)}", skill_id, dict(program),
            dict(parameter_schema), tuple(dict(row) for row in preconditions),
            tuple(dict(row) for row in postconditions), dict(success_probe),
            dict(mutation_budget), dict(rollback_program),
            tuple(payload["parent_revision_ids"]), derivation_kind,
            producing_failure_id, state,
        )

    @property
    def program_sha256(self) -> str:
        return content_sha256(self.program)

    def to_mapping(self) -> dict[str, object]:
        return {
            "skill_revision_id": self.skill_revision_id,
            "skill_id": self.skill_id,
            "program": dict(self.program),
            "program_sha256": self.program_sha256,
            "parameter_schema": dict(self.parameter_schema),
            "preconditions": [dict(row) for row in self.preconditions],
            "postconditions": [dict(row) for row in self.postconditions],
            "success_probe": dict(self.success_probe),
            "mutation_budget": dict(self.mutation_budget),
            "rollback_program": dict(self.rollback_program),
            "parent_revision_ids": list(self.parent_revision_ids),
            "derivation_kind": self.derivation_kind,
            "producing_failure_id": self.producing_failure_id,
            "state": self.state,
        }

    @classmethod
    def from_operator_revision(
        cls,
        revision: object,
        spec: object,
        *,
        producing_failure_id: str,
        success_probe: Mapping[str, object],
        mutation_budget: Mapping[str, object],
        rollback_program: Mapping[str, object],
        state: str = "stable",
    ) -> "SkillRevision":
        """Import an existing executable skill as an initial registered species."""
        revision_id = getattr(revision, "revision_id", None)
        family_id = getattr(revision, "family_id", None)
        canonical_spec = getattr(spec, "canonical_spec", None)
        if not all(isinstance(row, str) and row for row in (revision_id, family_id, canonical_spec)):
            raise TypeError("records are not compatible operator-library revisions")
        program = json.loads(canonical_spec)
        if not isinstance(program, Mapping):
            raise ValueError("legacy operator spec is not a typed program mapping")
        return cls.create(
            skill_id=family_id,
            program={"kind": "operator_spec_v1", "program": dict(program)},
            parameter_schema={"type": "object", "additionalProperties": False},
            preconditions=(), postconditions=(), success_probe=success_probe,
            mutation_budget=mutation_budget, rollback_program=rollback_program,
            producing_failure_id=producing_failure_id,
            derivation_kind="seed", state=state,
        )


@dataclass(frozen=True)
class RegistrySnapshot:
    registry_id: str
    epoch: int
    stable_pattern_revision_ids: tuple[str, ...]
    stable_skill_revision_ids: tuple[str, ...]
    parent_registry_id: str | None
    config_sha256: str
    sealed: bool

    @classmethod
    def create(
        cls,
        *,
        epoch: int,
        stable_pattern_revision_ids: Sequence[str],
        stable_skill_revision_ids: Sequence[str],
        config_sha256: str,
        parent_registry_id: str | None = None,
        sealed: bool = True,
    ) -> "RegistrySnapshot":
        payload = {
            "epoch": epoch,
            "stable_pattern_revision_ids": sorted(set(stable_pattern_revision_ids)),
            "stable_skill_revision_ids": sorted(set(stable_skill_revision_ids)),
            "parent_registry_id": parent_registry_id,
            "config_sha256": config_sha256,
            "sealed": sealed,
        }
        return cls(
            f"registry-{content_sha256(payload)}", epoch,
            tuple(payload["stable_pattern_revision_ids"]),
            tuple(payload["stable_skill_revision_ids"]), parent_registry_id,
            config_sha256, sealed,
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "registry_id": self.registry_id,
            "epoch": self.epoch,
            "stable_pattern_revision_ids": list(self.stable_pattern_revision_ids),
            "stable_skill_revision_ids": list(self.stable_skill_revision_ids),
            "parent_registry_id": self.parent_registry_id,
            "config_sha256": self.config_sha256,
            "sealed": self.sealed,
        }


@dataclass(frozen=True)
class EcologySelection:
    selection_id: str
    event_index: int
    failure_id: str
    registry_id: str
    candidate_skill_revision_ids: tuple[str, ...]
    selected_skill_revision_id: str
    pattern_responsibilities: tuple[PatternResponsibility, ...]
    scores: tuple[tuple[str, float], ...]
    posterior_before_sha256: str


@dataclass(frozen=True)
class DeploymentSkillFeedback:
    selection_id: str
    selected_skill_revision_id: str
    probe_id: str
    success: float
    locality_cost: float
    execution_cost: float
    rolled_back: bool
    delayed_regression: bool
    valid: bool
    provenance: str
    gold_derived: bool = False
    evaluation_only: bool = False
    estimated_utility: float | None = None

    def __post_init__(self) -> None:
        success = _finite(self.success, "success")
        locality = _finite(self.locality_cost, "locality_cost")
        cost = _finite(self.execution_cost, "execution_cost")
        if not 0.0 <= success <= 1.0:
            raise ValueError("success must be in [0, 1]")
        if locality < 0.0 or cost < 0.0:
            raise ValueError("deployment costs must be non-negative")
        if not self.provenance:
            raise ValueError("deployment feedback requires provenance")
        if self.estimated_utility is not None:
            estimate = _finite(self.estimated_utility, "estimated_utility")
            if not -1.0 <= estimate <= 1.0:
                raise ValueError("estimated_utility must be in [-1, 1]")

    @property
    def reward(self) -> float:
        if not self.valid or self.rolled_back or self.delayed_regression:
            return -1.0
        if self.estimated_utility is not None:
            return self.estimated_utility
        return max(
            -1.0,
            min(
                1.0,
                _finite(self.success, "success")
                - _finite(self.locality_cost, "locality_cost")
                - _finite(self.execution_cost, "execution_cost"),
            ),
        )


@dataclass(frozen=True)
class DelayedOutcomeFeedback:
    """A selected action's matured outcome relative to its pre-action prior.

    ``development_proxy`` is an explicit firewall: materialized shadow outcomes
    may exercise the zero-call protocol, but cannot be mistaken for live
    deployment feedback.
    """

    selection_id: str
    selected_skill_revision_id: str
    probe_id: str
    selected_at_event_index: int
    observed_after_event_index: int
    pre_action_prior: float
    delayed_utility: float
    valid: bool
    rolled_back: bool
    delayed_regression: bool
    provenance: str
    evaluation_only: bool = False
    development_proxy: bool = True

    def __post_init__(self) -> None:
        if self.observed_after_event_index <= self.selected_at_event_index:
            raise ValueError("delayed outcome must be observed after selection")
        prior = _finite(self.pre_action_prior, "pre_action_prior")
        utility = _finite(self.delayed_utility, "delayed_utility")
        if not -1.0 <= prior <= 1.0 or not -1.0 <= utility <= 1.0:
            raise ValueError("prior and delayed utility must be in [-1, 1]")
        if not self.provenance:
            raise ValueError("delayed outcome requires provenance")

    @property
    def reward(self) -> float:
        if not self.valid or self.rolled_back or self.delayed_regression:
            delayed = -1.0
        else:
            delayed = self.delayed_utility
        return max(-1.0, min(1.0, delayed - self.pre_action_prior))


class GHOSTEcologyRouter:
    """Recursive global/pattern/local posterior over a frozen skill registry."""

    def __init__(
        self,
        *,
        seed: int = 24,
        exploration: float = 0.08,
        min_pattern_support: float = 2.0,
        min_local_support: float = 3.0,
        allow_development_proxy: bool = False,
    ) -> None:
        if exploration < 0:
            raise ValueError("exploration must be non-negative")
        if min_pattern_support < 0.0 or min_local_support < min_pattern_support:
            raise ValueError("hierarchy support thresholds are invalid")
        self.seed = int(seed)
        self.exploration = float(exploration)
        self.min_pattern_support = float(min_pattern_support)
        self.min_local_support = float(min_local_support)
        self.allow_development_proxy = bool(allow_development_proxy)
        self._stats: dict[tuple[str, ...], tuple[float, float]] = {}
        self._pending: dict[str, tuple[EcologySelection, FailureDeposit, tuple[SkillRevision, ...]]] = {}

    @property
    def snapshot(self) -> dict[str, object]:
        payload = {
            "schema_version": "cmd-ghost-ecology-posterior-v3",
            "seed": self.seed,
            "exploration": self.exploration,
            "min_pattern_support": self.min_pattern_support,
            "min_local_support": self.min_local_support,
            "allow_development_proxy": self.allow_development_proxy,
            "stats": [[list(key), precision, natural] for key, (precision, natural) in sorted(self._stats.items())],
        }
        return {**payload, "snapshot_sha256": content_sha256(payload)}

    @classmethod
    def from_snapshot(cls, value: Mapping[str, object]) -> "GHOSTEcologyRouter":
        _closed(
            value,
            {
                "schema_version", "seed", "exploration", "min_pattern_support",
                "min_local_support", "allow_development_proxy", "stats",
                "snapshot_sha256",
            },
            "GHOST ecology posterior snapshot",
        )
        payload = dict(value)
        claimed = payload.pop("snapshot_sha256")
        if (
            value["schema_version"] != "cmd-ghost-ecology-posterior-v3"
            or content_sha256(payload) != claimed
        ):
            raise ValueError("GHOST ecology posterior snapshot hash/schema mismatch")
        result = cls(
            seed=int(value["seed"]),
            exploration=float(value["exploration"]),
            min_pattern_support=float(value["min_pattern_support"]),
            min_local_support=float(value["min_local_support"]),
            allow_development_proxy=bool(value["allow_development_proxy"]),
        )
        stats: dict[tuple[str, ...], tuple[float, float]] = {}
        for raw in value["stats"]:
            key = tuple(str(item) for item in raw[0])
            if key in stats:
                raise ValueError("posterior snapshot repeats a key")
            stats[key] = (
                _finite(raw[1], "posterior precision"),
                _finite(raw[2], "posterior natural parameter"),
            )
            if stats[key][0] <= 0:
                raise ValueError("posterior precision must be positive")
        result._stats = stats
        return result

    def select(
        self,
        failure: FailureDeposit,
        *,
        pattern_responsibilities: Sequence[PatternResponsibility],
        skills: Sequence[SkillRevision],
        registry: RegistrySnapshot,
        event_index: int,
        skill_priors: Mapping[str, float] | None = None,
    ) -> EcologySelection:
        responsibilities = tuple(pattern_responsibilities)
        validate_responsibilities(responsibilities)
        if not registry.sealed:
            raise PermissionError("serving requires a sealed registry snapshot")
        if set(row.pattern_revision_id for row in responsibilities) - set(
            registry.stable_pattern_revision_ids
        ):
            raise PermissionError("pattern responsibility is absent from frozen registry")
        candidates = tuple(skills)
        if not candidates or len({row.skill_revision_id for row in candidates}) != len(candidates):
            raise ValueError("candidate skills must be non-empty and unique")
        if any(row.state != "stable" for row in candidates):
            raise PermissionError("only stable skills may serve")
        if set(row.skill_revision_id for row in candidates) - set(registry.stable_skill_revision_ids):
            raise PermissionError("candidate skill is absent from frozen registry")
        candidate_ids = {row.skill_revision_id for row in candidates}
        priors = (
            {key: 0.0 for key in candidate_ids}
            if skill_priors is None
            else {
                str(key): _finite(value, f"skill prior:{key}")
                for key, value in skill_priors.items()
            }
        )
        if set(priors) != candidate_ids:
            raise ValueError("skill priors must exactly cover the candidate skills")
        if any(not -1.0 <= value <= 1.0 for value in priors.values()):
            raise ValueError("skill priors must be in [-1, 1]")
        before = str(self.snapshot["snapshot_sha256"])
        scores = []
        for skill in candidates:
            keys = self._keys(failure, responsibilities, skill)
            score = priors[skill.skill_revision_id]
            for key, weight in keys:
                precision, natural = self._stats.get(key, (1.0, 0.0))
                support = precision - 1.0
                if key[0] == "pattern" and support < self.min_pattern_support:
                    continue
                if key[0] == "local" and support < self.min_local_support:
                    continue
                mean = natural / precision
                address = content_sha256(
                    {"seed": self.seed, "event_index": event_index, "key": key}
                )
                draw = random.Random(int(address, 16)).gauss(
                    mean, self.exploration / math.sqrt(precision)
                )
                score += weight * draw
            scores.append((skill.skill_revision_id, score))
        ranked = tuple(sorted(scores, key=lambda row: (-row[1], row[0])))
        body = {
            "event_index": event_index,
            "failure_id": failure.failure_id,
            "registry_id": registry.registry_id,
            "candidate_skill_revision_ids": sorted(row.skill_revision_id for row in candidates),
            "selected_skill_revision_id": ranked[0][0],
            "pattern_responsibilities": [
                [row.pattern_revision_id, row.responsibility] for row in responsibilities
            ],
            "scores": [list(row) for row in ranked],
            "posterior_before_sha256": before,
        }
        decision = EcologySelection(
            f"selection-{content_sha256(body)}", event_index, failure.failure_id,
            registry.registry_id, tuple(body["candidate_skill_revision_ids"]), ranked[0][0],
            responsibilities, ranked, before,
        )
        self._pending[decision.selection_id] = (decision, failure, candidates)
        return decision

    def observe(
        self,
        decision: EcologySelection,
        feedback: DeploymentSkillFeedback | DelayedOutcomeFeedback,
    ) -> dict[str, object]:
        pending = self._pending.get(decision.selection_id)
        if pending is None or pending[0] != decision:
            raise ValueError("feedback refers to an unknown or consumed selection")
        if feedback.selection_id != decision.selection_id:
            raise ValueError("feedback selection binding mismatch")
        if feedback.selected_skill_revision_id != decision.selected_skill_revision_id:
            raise ValueError("unselected skill feedback is forbidden")
        if isinstance(feedback, DeploymentSkillFeedback) and feedback.gold_derived:
            raise ValueError("deployment feedback cannot be gold-derived")
        if (
            isinstance(feedback, DelayedOutcomeFeedback)
            and feedback.development_proxy
            and not self.allow_development_proxy
        ):
            raise PermissionError("development delayed-outcome proxy is not enabled")
        if (
            isinstance(feedback, DelayedOutcomeFeedback)
            and feedback.selected_at_event_index != decision.event_index
        ):
            raise ValueError("delayed outcome selection event binding mismatch")
        selected = next(
            row for row in pending[2]
            if row.skill_revision_id == decision.selected_skill_revision_id
        )
        if feedback.probe_id != selected.success_probe["probe_id"]:
            raise ValueError("feedback does not use the selected skill's registered probe")
        self._pending.pop(decision.selection_id)
        if feedback.evaluation_only:
            return self.snapshot
        pre_update = dict(self._stats)
        pattern_means: dict[str, float] = {}
        global_key = ("global", selected.skill_revision_id)
        global_precision, global_natural = pre_update.get(global_key, (1.0, 0.0))
        global_mean = global_natural / global_precision
        for responsibility in decision.pattern_responsibilities:
            key = (
                "pattern", responsibility.pattern_revision_id,
                selected.skill_revision_id,
            )
            precision, natural = pre_update.get(key, (1.0, 0.0))
            pattern_means[responsibility.pattern_revision_id] = natural / precision
        for key, weight in self._keys(pending[1], decision.pattern_responsibilities, selected):
            precision, natural = self._stats.get(key, (1.0, 0.0))
            target = feedback.reward
            if key[0] == "pattern":
                target -= global_mean
            elif key[0] == "local":
                target -= global_mean + pattern_means.get(key[1], 0.0)
            self._stats[key] = (
                precision + weight * weight,
                natural + weight * target,
            )
        return self.snapshot

    def restore_pending(
        self,
        decision: EcologySelection,
        failure: FailureDeposit,
        skills: Sequence[SkillRevision],
    ) -> None:
        if decision.selection_id in self._pending:
            raise ValueError("pending selection is already restored")
        candidates = tuple(skills)
        if tuple(sorted(row.skill_revision_id for row in candidates)) != decision.candidate_skill_revision_ids:
            raise ValueError("restored pending candidate set disagrees")
        self._pending[decision.selection_id] = (decision, failure, candidates)

    @staticmethod
    def _keys(
        failure: FailureDeposit,
        responsibilities: Sequence[PatternResponsibility],
        skill: SkillRevision,
    ) -> tuple[tuple[tuple[str, ...], float], ...]:
        rows: list[tuple[tuple[str, ...], float]] = [
            (("global", skill.skill_revision_id), 1.0)
        ]
        for responsibility in responsibilities:
            rows.append(
                (("pattern", responsibility.pattern_revision_id, skill.skill_revision_id), responsibility.responsibility)
            )
            scale = sum(abs(value) for _feature, value in failure.features if value)
            for feature, value in failure.features:
                if value:
                    rows.append(
                        (
                            ("local", responsibility.pattern_revision_id, feature,
                             skill.skill_revision_id),
                            responsibility.responsibility * value / scale,
                        )
                    )
        return tuple(rows)


@dataclass(frozen=True)
class ObservableResidualSelection:
    """A backbone decision plus any support-gated residual correction."""

    selection_id: str
    event_index: int
    failure_id: str
    registry_id: str
    candidate_skill_revision_ids: tuple[str, ...]
    selected_skill_revision_id: str | None
    pattern_responsibilities: tuple[PatternResponsibility, ...]
    scores: tuple[tuple[str, float], ...]
    posterior_before_sha256: str
    base_selected_skill_revision_id: str | None
    selection_mode: str
    exploration_activated: bool
    active_levels: tuple[str, ...]


class ObservableResidualGHOSTRouter:
    """Keep the observable V4 winner until residual evidence can improve it."""

    def __init__(
        self,
        *,
        seed: int = 24,
        exploration: float = 0.08,
        min_global_support: float = 2.0,
        min_pattern_support: float = 4.0,
        min_local_support: float = 8.0,
        min_exploration_support: float = 4.0,
        allow_development_proxy: bool = False,
    ) -> None:
        if exploration < 0.0:
            raise ValueError("exploration must be non-negative")
        if not (
            0.0 <= min_global_support <= min_pattern_support <= min_local_support
        ):
            raise ValueError("hierarchy support thresholds are invalid")
        if min_exploration_support < min_global_support:
            raise ValueError("exploration support cannot precede global support")
        self.seed = int(seed)
        self.exploration = float(exploration)
        self.min_global_support = float(min_global_support)
        self.min_pattern_support = float(min_pattern_support)
        self.min_local_support = float(min_local_support)
        self.min_exploration_support = float(min_exploration_support)
        self.allow_development_proxy = bool(allow_development_proxy)
        self._stats: dict[tuple[str, ...], tuple[float, float]] = {}
        self._pending: dict[
            str,
            tuple[
                ObservableResidualSelection,
                FailureDeposit,
                tuple[SkillRevision, ...],
            ],
        ] = {}
        self._selection_count = 0
        self._fallback_count = 0
        self._exploration_count = 0
        self._exploration_changed_count = 0
        self._level_use_counts = {"global": 0, "pattern": 0, "local": 0}

    @property
    def snapshot(self) -> dict[str, object]:
        payload = {
            "schema_version": "cmd-observable-residual-ghost-posterior-v1",
            "seed": self.seed,
            "exploration": self.exploration,
            "min_global_support": self.min_global_support,
            "min_pattern_support": self.min_pattern_support,
            "min_local_support": self.min_local_support,
            "min_exploration_support": self.min_exploration_support,
            "allow_development_proxy": self.allow_development_proxy,
            "stats": [
                [list(key), precision, natural]
                for key, (precision, natural) in sorted(self._stats.items())
            ],
        }
        return {**payload, "snapshot_sha256": content_sha256(payload)}

    @classmethod
    def from_snapshot(
        cls, value: Mapping[str, object]
    ) -> "ObservableResidualGHOSTRouter":
        _closed(
            value,
            {
                "schema_version",
                "seed",
                "exploration",
                "min_global_support",
                "min_pattern_support",
                "min_local_support",
                "min_exploration_support",
                "allow_development_proxy",
                "stats",
                "snapshot_sha256",
            },
            "observable residual GHOST posterior snapshot",
        )
        payload = dict(value)
        claimed = payload.pop("snapshot_sha256")
        if (
            value["schema_version"]
            != "cmd-observable-residual-ghost-posterior-v1"
            or content_sha256(payload) != claimed
        ):
            raise ValueError(
                "observable residual GHOST posterior snapshot hash/schema mismatch"
            )
        result = cls(
            seed=int(value["seed"]),
            exploration=float(value["exploration"]),
            min_global_support=float(value["min_global_support"]),
            min_pattern_support=float(value["min_pattern_support"]),
            min_local_support=float(value["min_local_support"]),
            min_exploration_support=float(value["min_exploration_support"]),
            allow_development_proxy=bool(value["allow_development_proxy"]),
        )
        stats: dict[tuple[str, ...], tuple[float, float]] = {}
        for raw in value["stats"]:
            key = tuple(str(item) for item in raw[0])
            if key in stats:
                raise ValueError("posterior snapshot repeats a key")
            precision = _finite(raw[1], "posterior precision")
            natural = _finite(raw[2], "posterior natural parameter")
            if precision <= 0.0:
                raise ValueError("posterior precision must be positive")
            stats[key] = (precision, natural)
        result._stats = stats
        return result

    @property
    def diagnostics(self) -> dict[str, object]:
        return {
            "selection_count": self._selection_count,
            "fallback_count": self._fallback_count,
            "fallback_rate": (
                0.0
                if self._selection_count == 0
                else self._fallback_count / self._selection_count
            ),
            "exploration_count": self._exploration_count,
            "exploration_changed_count": self._exploration_changed_count,
            "level_use_counts": dict(self._level_use_counts),
        }

    def select(
        self,
        failure: FailureDeposit,
        *,
        pattern_responsibilities: Sequence[PatternResponsibility],
        skills: Sequence[SkillRevision],
        registry: RegistrySnapshot,
        event_index: int,
        base_scores: Mapping[str, float],
        base_selected_skill_revision_id: str | None,
    ) -> ObservableResidualSelection:
        responsibilities = tuple(pattern_responsibilities)
        validate_responsibilities(responsibilities)
        candidates = tuple(skills)
        candidate_ids = {row.skill_revision_id for row in candidates}
        scores = {
            str(key): _finite(value, f"base score:{key}")
            for key, value in base_scores.items()
        }
        if not registry.sealed:
            raise PermissionError("serving requires a sealed registry snapshot")
        if not candidates or len(candidate_ids) != len(candidates):
            raise ValueError("candidate skills must be non-empty and unique")
        if any(row.state != "stable" for row in candidates):
            raise PermissionError("only stable skills may serve")
        if candidate_ids - set(registry.stable_skill_revision_ids):
            raise PermissionError("candidate skill is absent from frozen registry")
        if set(scores) != candidate_ids:
            raise ValueError("base scores must exactly cover the candidate skills")
        if (
            base_selected_skill_revision_id is not None
            and base_selected_skill_revision_id not in candidate_ids
        ):
            raise ValueError("base selection is absent from candidate skills")

        before = str(self.snapshot["snapshot_sha256"])
        active_levels: set[str] = set()
        routed_scores: list[tuple[str, float]] = []
        exploration_supported_skill_ids = {
            skill.skill_revision_id
            for skill in candidates
            if self._stats.get(
                ("global", skill.skill_revision_id), (1.0, 0.0)
            )[0]
            - 1.0
            >= self.min_exploration_support
        }
        exploration_activated = (
            self.exploration > 0.0
            and len(exploration_supported_skill_ids) >= 2
        )
        for skill in candidates:
            score = scores[skill.skill_revision_id]
            for key, weight in self._keys(
                failure, responsibilities, skill
            ):
                precision, natural = self._stats.get(key, (1.0, 0.0))
                support = precision - 1.0
                threshold = {
                    "global": self.min_global_support,
                    "pattern": self.min_pattern_support,
                    "local": self.min_local_support,
                }[key[0]]
                if support >= threshold:
                    active_levels.add(key[0])
                    score += weight * natural / precision
                if (
                    exploration_activated
                    and skill.skill_revision_id
                    in exploration_supported_skill_ids
                    and (
                    key[0] == "global" or support >= threshold
                    )
                ):
                    address = content_sha256(
                        {
                            "seed": self.seed,
                            "event_index": event_index,
                            "key": key,
                            "router": "observable-residual-ghost-v1",
                        }
                    )
                    score += weight * random.Random(int(address, 16)).gauss(
                        0.0, self.exploration / math.sqrt(precision)
                    )
            routed_scores.append((skill.skill_revision_id, score))
        ranked = tuple(
            sorted(routed_scores, key=lambda row: (-row[1], row[0]))
        )
        residual_active = bool(active_levels)
        if base_selected_skill_revision_id is None:
            selected_skill_revision_id = None
        elif residual_active or exploration_activated:
            selected_skill_revision_id = ranked[0][0]
        else:
            selected_skill_revision_id = base_selected_skill_revision_id
        changed = selected_skill_revision_id != base_selected_skill_revision_id
        if not residual_active and not exploration_activated:
            selection_mode = "observable_fallback"
        elif exploration_activated and changed:
            selection_mode = "exploration_override"
        elif changed:
            selection_mode = "residual_override"
        else:
            selection_mode = "residual_supported"
        ordered_levels = tuple(
            level for level in ("global", "pattern", "local")
            if level in active_levels
        )
        body = {
            "event_index": event_index,
            "failure_id": failure.failure_id,
            "registry_id": registry.registry_id,
            "candidate_skill_revision_ids": sorted(candidate_ids),
            "selected_skill_revision_id": selected_skill_revision_id,
            "base_selected_skill_revision_id": base_selected_skill_revision_id,
            "pattern_responsibilities": [
                [row.pattern_revision_id, row.responsibility]
                for row in responsibilities
            ],
            "scores": [list(row) for row in ranked],
            "posterior_before_sha256": before,
            "selection_mode": selection_mode,
            "exploration_activated": exploration_activated,
            "active_levels": list(ordered_levels),
        }
        decision = ObservableResidualSelection(
            f"selection-{content_sha256(body)}",
            event_index,
            failure.failure_id,
            registry.registry_id,
            tuple(body["candidate_skill_revision_ids"]),
            selected_skill_revision_id,
            responsibilities,
            ranked,
            before,
            base_selected_skill_revision_id,
            selection_mode,
            exploration_activated,
            ordered_levels,
        )
        self._selection_count += 1
        self._fallback_count += selection_mode == "observable_fallback"
        self._exploration_count += exploration_activated
        self._exploration_changed_count += exploration_activated and changed
        for level in ordered_levels:
            self._level_use_counts[level] += 1
        if decision.selected_skill_revision_id is not None:
            self._pending[decision.selection_id] = (decision, failure, candidates)
        return decision

    def observe(
        self,
        decision: ObservableResidualSelection,
        feedback: DelayedOutcomeFeedback,
    ) -> dict[str, object]:
        pending = self._pending.get(decision.selection_id)
        if pending is None or pending[0] != decision:
            raise ValueError("feedback refers to an unknown or consumed selection")
        if not isinstance(feedback, DelayedOutcomeFeedback):
            raise TypeError("observable residual routing requires delayed outcome feedback")
        if feedback.selection_id != decision.selection_id:
            raise ValueError("feedback selection binding mismatch")
        if feedback.selected_skill_revision_id != decision.selected_skill_revision_id:
            raise ValueError("unselected skill feedback is forbidden")
        if feedback.development_proxy and not self.allow_development_proxy:
            raise PermissionError("development delayed-outcome proxy is not enabled")
        if feedback.selected_at_event_index != decision.event_index:
            raise ValueError("delayed outcome selection event binding mismatch")
        selected = next(
            row
            for row in pending[2]
            if row.skill_revision_id == decision.selected_skill_revision_id
        )
        if feedback.probe_id != selected.success_probe["probe_id"]:
            raise ValueError("feedback does not use the selected skill's registered probe")
        self._pending.pop(decision.selection_id)
        if feedback.evaluation_only:
            return self.snapshot

        pre_update = dict(self._stats)
        global_key = ("global", selected.skill_revision_id)
        global_precision, global_natural = pre_update.get(global_key, (1.0, 0.0))
        global_mean = global_natural / global_precision
        pattern_means: dict[str, float] = {}
        for responsibility in decision.pattern_responsibilities:
            key = (
                "pattern",
                responsibility.pattern_revision_id,
                selected.skill_revision_id,
            )
            precision, natural = pre_update.get(key, (1.0, 0.0))
            pattern_means[responsibility.pattern_revision_id] = natural / precision
        for key, weight in self._keys(
            pending[1], decision.pattern_responsibilities, selected
        ):
            precision, natural = self._stats.get(key, (1.0, 0.0))
            target = feedback.reward
            if key[0] == "pattern":
                target -= global_mean
            elif key[0] == "local":
                target -= global_mean + pattern_means.get(key[1], 0.0)
            self._stats[key] = (
                precision + weight * weight,
                natural + weight * target,
            )
        return self.snapshot

    @staticmethod
    def _keys(
        failure: FailureDeposit,
        responsibilities: Sequence[PatternResponsibility],
        skill: SkillRevision,
    ) -> tuple[tuple[tuple[str, ...], float], ...]:
        rows: list[tuple[tuple[str, ...], float]] = [
            (("global", skill.skill_revision_id), 1.0)
        ]
        scale = sum(abs(value) for _feature, value in failure.features if value)
        for responsibility in responsibilities:
            rows.append(
                (
                    (
                        "pattern",
                        responsibility.pattern_revision_id,
                        skill.skill_revision_id,
                    ),
                    responsibility.responsibility,
                )
            )
            if scale == 0.0:
                continue
            for feature, value in failure.features:
                if value:
                    rows.append(
                        (
                            (
                                "local",
                                responsibility.pattern_revision_id,
                                feature,
                                skill.skill_revision_id,
                            ),
                            responsibility.responsibility * value / scale,
                        )
                    )
        return tuple(rows)


@dataclass(frozen=True)
class NicheObservation:
    failure_id: str
    pattern_revision_id: str
    skill_revision_id: str | None
    responsibility: float
    selected: bool
    success: float
    resolved: bool

    def __post_init__(self) -> None:
        responsibility = _finite(self.responsibility, "niche responsibility")
        success = _finite(self.success, "niche success")
        if not 0.0 <= responsibility <= 1.0 or not 0.0 <= success <= 1.0:
            raise ValueError("niche responsibility/success must be in [0, 1]")


@dataclass(frozen=True)
class NicheSnapshot:
    niche_id: str
    window_start: int
    window_end: int
    resource_mass: float
    unresolved_mass: float
    arrival_count: int
    recurrence_rate: float
    skill_occupancy: tuple[tuple[str, float], ...]
    successful_occupancy: tuple[tuple[str, float], ...]
    skill_fitness: tuple[tuple[str, float, float], ...]
    skill_richness: int
    selection_entropy: float
    effective_species_count: float
    dominant_skill_revision_id: str | None
    dominant_share: float
    fitness_margin: float
    split_pressure: float
    state: str
    snapshot_sha256: str


class NicheObserver:
    def snapshot(
        self,
        *,
        pattern_revision_id: str,
        observations: Sequence[NicheObservation],
        window_start: int,
        window_end: int,
        previous_state: str = "latent",
    ) -> NicheSnapshot:
        if previous_state not in NICHE_STATES or window_end < window_start:
            raise ValueError("invalid niche window/state")
        rows = tuple(row for row in observations if row.pattern_revision_id == pattern_revision_id)
        resource = sum(row.responsibility for row in rows)
        unresolved = sum(row.responsibility for row in rows if not row.resolved)
        arrival_count = len(rows)
        recurrence_rate = (
            0.0 if not rows
            else 1.0 - len({row.failure_id for row in rows}) / len(rows)
        )
        occupancy: dict[str, float] = defaultdict(float)
        successful: dict[str, float] = defaultdict(float)
        for row in rows:
            if row.selected and row.skill_revision_id is not None:
                occupancy[row.skill_revision_id] += row.responsibility
                successful[row.skill_revision_id] += row.responsibility * row.success
        total = sum(occupancy.values())
        shares = {
            skill: mass / total for skill, mass in occupancy.items()
        } if total else {}
        entropy = -sum(value * math.log(value) for value in shares.values() if value > 0)
        effective = math.exp(entropy) if shares else 0.0
        ranked = sorted(shares.items(), key=lambda row: (-row[1], row[0]))
        dominant = None if not ranked else ranked[0][0]
        dominant_share = 0.0 if not ranked else ranked[0][1]
        fitness = sorted(
            (
                (successful[skill] / occupancy[skill], skill)
                for skill in occupancy if occupancy[skill] > 0
            ),
            reverse=True,
        )
        skill_fitness = []
        for skill in sorted(occupancy):
            alpha = 1.0 + successful[skill]
            beta = 1.0 + occupancy[skill] - successful[skill]
            mean = alpha / (alpha + beta)
            uncertainty = math.sqrt(
                alpha * beta / ((alpha + beta) ** 2 * (alpha + beta + 1.0))
            )
            skill_fitness.append((skill, mean, uncertainty))
        fitness_margin = 0.0 if len(fitness) < 2 else fitness[0][0] - fitness[1][0]
        # High resource plus multiple similarly occupied skills indicates a real
        # contested niche; high unresolved mass turns that contest into split pressure.
        split_pressure = 0.0 if resource == 0 else min(
            1.0, (unresolved / resource) * max(0.0, effective - 1.0)
        )
        state = self._state(
            previous_state=previous_state, resource=resource,
            unresolved=unresolved, richness=len(occupancy),
            dominant_share=dominant_share, split_pressure=split_pressure,
        )
        payload = {
            "niche_id": f"niche:{pattern_revision_id}",
            "window_start": window_start,
            "window_end": window_end,
            "resource_mass": resource,
            "unresolved_mass": unresolved,
            "arrival_count": arrival_count,
            "recurrence_rate": recurrence_rate,
            "skill_occupancy": sorted(occupancy.items()),
            "successful_occupancy": sorted(successful.items()),
            "skill_fitness": skill_fitness,
            "skill_richness": len(occupancy),
            "selection_entropy": entropy,
            "effective_species_count": effective,
            "dominant_skill_revision_id": dominant,
            "dominant_share": dominant_share,
            "fitness_margin": fitness_margin,
            "split_pressure": split_pressure,
            "state": state,
        }
        return NicheSnapshot(
            **payload, snapshot_sha256=content_sha256(payload)
        )

    @staticmethod
    def _state(
        *, previous_state: str, resource: float, unresolved: float,
        richness: int, dominant_share: float, split_pressure: float,
    ) -> str:
        if resource == 0:
            return "extinct" if previous_state in {"collapsing", "extinct"} else "latent"
        if resource < 2.0:
            return "emerging"
        if split_pressure >= 0.35:
            return "branching"
        if unresolved / resource > 0.75 and previous_state in {"stable", "contested"}:
            return "collapsing"
        if richness >= 2 and dominant_share < 0.70:
            return "contested"
        if resource >= 5.0 and dominant_share >= 0.70:
            return "stable"
        return "occupied"


@dataclass(frozen=True)
class NichePerturbationReport:
    niche_id: str
    removed_skill_revision_id: str
    baseline_snapshot_sha256: str
    window_snapshot_sha256s: tuple[str, ...]
    winnerless_windows: int
    replacement_after_windows: int | None
    recovered_after_windows: int | None
    terminal_state: str
    report_sha256: str


def observe_niche_perturbation(
    *,
    baseline: NicheSnapshot,
    removed_skill_revision_id: str,
    windows: Sequence[NicheSnapshot],
) -> NichePerturbationReport:
    """Measure replacement and recovery after a controlled skill removal.

    The caller owns the intervention.  This function only observes immutable
    snapshots, so sealed-test execution cannot accidentally mutate the ecology.
    """
    if baseline.dominant_skill_revision_id != removed_skill_revision_id:
        raise ValueError("perturbation must remove the baseline dominant skill")
    rows = tuple(windows)
    if any(row.niche_id != baseline.niche_id for row in rows):
        raise ValueError("perturbation windows belong to different niches")
    winnerless = sum(row.dominant_skill_revision_id is None for row in rows)
    replacement = next(
        (
            index
            for index, row in enumerate(rows, 1)
            if row.dominant_skill_revision_id not in {None, removed_skill_revision_id}
        ),
        None,
    )
    recovered = next(
        (
            index
            for index, row in enumerate(rows, 1)
            if row.dominant_skill_revision_id == removed_skill_revision_id
        ),
        None,
    )
    payload = {
        "niche_id": baseline.niche_id,
        "removed_skill_revision_id": removed_skill_revision_id,
        "baseline_snapshot_sha256": baseline.snapshot_sha256,
        "window_snapshot_sha256s": [row.snapshot_sha256 for row in rows],
        "winnerless_windows": winnerless,
        "replacement_after_windows": replacement,
        "recovered_after_windows": recovered,
        "terminal_state": baseline.state if not rows else rows[-1].state,
    }
    return NichePerturbationReport(
        niche_id=baseline.niche_id,
        removed_skill_revision_id=removed_skill_revision_id,
        baseline_snapshot_sha256=baseline.snapshot_sha256,
        window_snapshot_sha256s=tuple(payload["window_snapshot_sha256s"]),
        winnerless_windows=winnerless,
        replacement_after_windows=replacement,
        recovered_after_windows=recovered,
        terminal_state=str(payload["terminal_state"]),
        report_sha256=content_sha256(payload),
    )


@dataclass(frozen=True)
class DiscoveryPressure:
    niche_id: str
    window_start: int
    window_end: int
    unmatched_mass: float
    abstention_rate: float
    mean_absolute_residual: float
    proposal_kind: str
    trigger_reasons: tuple[str, ...]
    governance_required: bool
    pressure_sha256: str


def derive_discovery_pressure(
    *,
    niche_id: str,
    window_start: int,
    window_end: int,
    unmatched_responsibilities: Sequence[float],
    abstentions: Sequence[bool],
    prediction_residuals: Sequence[float],
    unmatched_threshold: float = 2.0,
    abstention_threshold: float = 0.35,
    residual_threshold: float = 0.30,
) -> DiscoveryPressure | None:
    """Derive a proposal trigger without generating or approving a repair."""
    if window_end < window_start:
        raise ValueError("discovery window is reversed")
    unmatched = sum(_finite(row, "unmatched responsibility") for row in unmatched_responsibilities)
    if any(row < 0.0 for row in unmatched_responsibilities):
        raise ValueError("unmatched responsibility cannot be negative")
    abstention_rate = 0.0 if not abstentions else sum(abstentions) / len(abstentions)
    residual = (
        0.0 if not prediction_residuals
        else fmean(abs(_finite(row, "prediction residual")) for row in prediction_residuals)
    )
    reasons = []
    if unmatched >= unmatched_threshold:
        reasons.append("persistent_unmatched_failure_mass")
    if abstention_rate >= abstention_threshold:
        reasons.append("persistent_abstention")
    if residual >= residual_threshold:
        reasons.append("systematic_prediction_residual")
    if not reasons:
        return None
    proposal_kind = (
        "new_pattern" if "persistent_unmatched_failure_mass" in reasons
        else "new_skill" if "persistent_abstention" in reasons
        else "revise_skill"
    )
    payload = {
        "niche_id": niche_id,
        "window_start": window_start,
        "window_end": window_end,
        "unmatched_mass": unmatched,
        "abstention_rate": abstention_rate,
        "mean_absolute_residual": residual,
        "proposal_kind": proposal_kind,
        "trigger_reasons": reasons,
        "governance_required": True,
    }
    return DiscoveryPressure(
        niche_id=niche_id, window_start=window_start, window_end=window_end,
        unmatched_mass=unmatched, abstention_rate=abstention_rate,
        mean_absolute_residual=residual, proposal_kind=proposal_kind,
        trigger_reasons=tuple(reasons), governance_required=True,
        pressure_sha256=content_sha256(payload),
    )


@dataclass(frozen=True)
class PromotionEvidence:
    feedback_id: str
    failure_id: str
    family_id_audit_only: str
    skill_revision_id: str
    success: bool
    rolled_back: bool
    gold_derived: bool


@dataclass(frozen=True)
class GovernanceDecision:
    eligible: bool
    reason: str
    supporting_feedback_ids: tuple[str, ...] = ()


def skill_promotion_decision(
    skill: SkillRevision,
    evidence: Sequence[PromotionEvidence],
    *,
    anchor_non_regression: bool,
) -> GovernanceDecision:
    eligible = [
        row for row in evidence
        if row.skill_revision_id == skill.skill_revision_id
        and row.failure_id != skill.producing_failure_id
        and row.success and not row.rolled_back and not row.gold_derived
    ]
    unique: dict[str, PromotionEvidence] = {}
    for row in eligible:
        unique.setdefault(row.failure_id, row)
    rows = tuple(unique.values())
    if len(rows) < 3:
        return GovernanceDecision(False, "needs_three_later_successes")
    if len({row.family_id_audit_only for row in rows}) < 2:
        return GovernanceDecision(False, "needs_two_failure_families")
    if not anchor_non_regression:
        return GovernanceDecision(False, "anchor_regression")
    return GovernanceDecision(
        True, "eligible", tuple(row.feedback_id for row in rows[:3])
    )


def propose_pattern_split(
    pattern: PatternRevision, snapshot: NicheSnapshot, *, threshold: float = 0.35
) -> dict[str, object] | None:
    if snapshot.split_pressure < threshold:
        return None
    payload = {
        "proposal_kind": "pattern_split",
        "parent_pattern_revision_id": pattern.pattern_revision_id,
        "niche_snapshot_sha256": snapshot.snapshot_sha256,
        "split_pressure": snapshot.split_pressure,
        "governance_required": True,
    }
    return {**payload, "proposal_id": f"proposal-{content_sha256(payload)}"}


def propose_pattern_merge(
    left: PatternRevision,
    right: PatternRevision,
    *,
    feature_similarity: float,
    skill_ranking_similarity: float,
    feedback_similarity: float,
    threshold: float = 0.90,
) -> dict[str, object] | None:
    scores = tuple(
        _finite(value, name)
        for value, name in (
            (feature_similarity, "feature similarity"),
            (skill_ranking_similarity, "skill ranking similarity"),
            (feedback_similarity, "feedback similarity"),
        )
    )
    if any(not 0.0 <= row <= 1.0 for row in scores):
        raise ValueError("pattern merge similarities must be in [0, 1]")
    if min(scores) < threshold:
        return None
    parents = sorted((left.pattern_revision_id, right.pattern_revision_id))
    if parents[0] == parents[1]:
        raise ValueError("pattern merge requires distinct parents")
    payload = {
        "proposal_kind": "pattern_merge",
        "parent_pattern_revision_ids": parents,
        "feature_similarity": scores[0],
        "skill_ranking_similarity": scores[1],
        "feedback_similarity": scores[2],
        "governance_required": True,
    }
    return {**payload, "proposal_id": f"proposal-{content_sha256(payload)}"}


class GhostEcology:
    """Coordinate durable three-layer sedimentation around a frozen registry."""

    def __init__(
        self,
        ledger: EcologyLedger,
        *,
        router: GHOSTEcologyRouter | None = None,
        discovery_authorized: bool = True,
        evaluation_only: bool = False,
    ) -> None:
        if evaluation_only and discovery_authorized:
            raise ValueError("sealed evaluation cannot authorize discovery")
        self.ledger = ledger
        self.router = router or GHOSTEcologyRouter()
        self.discovery_authorized = discovery_authorized
        self.evaluation_only = evaluation_only
        self.failures: dict[str, FailureDeposit] = {}
        self.patterns: dict[str, PatternRevision] = {}
        self.skills: dict[str, SkillRevision] = {}
        self.registries: dict[str, RegistrySnapshot] = {}
        self._replay()

    def deposit_failure(self, failure: FailureDeposit, *, event_index: int) -> str:
        if self.evaluation_only:
            raise PermissionError("sealed evaluation cannot sediment failure memory")
        existing = self.failures.get(failure.failure_id)
        if existing is not None and existing != failure:
            raise ValueError("failure deposit is immutable")
        event = self.ledger.append(
            "failure_observed", event_index=event_index, payload=failure.to_mapping()
        )
        self.failures.setdefault(failure.failure_id, failure)
        return str(event["event_id"])

    def propose_pattern(self, pattern: PatternRevision, *, event_index: int) -> str:
        self._require_discovery()
        self._validate_parents(pattern.parent_revision_ids, self.patterns, "pattern")
        event = self.ledger.append(
            "pattern_revision", event_index=event_index, payload=pattern.to_mapping()
        )
        self.patterns[pattern.pattern_revision_id] = pattern
        return str(event["event_id"])

    def bind_failure(
        self,
        failure_id: str,
        responsibilities: Sequence[PatternResponsibility],
        *,
        event_index: int,
    ) -> str:
        if self.evaluation_only:
            raise PermissionError("sealed evaluation cannot alter pattern bindings")
        if failure_id not in self.failures:
            raise ValueError("unknown failure deposit")
        rows = tuple(responsibilities)
        validate_responsibilities(rows)
        if any(row.pattern_revision_id not in self.patterns for row in rows):
            raise ValueError("binding references unknown pattern revision")
        event = self.ledger.append(
            "pattern_binding",
            event_index=event_index,
            payload={
                "failure_id": failure_id,
                "responsibilities": [
                    [row.pattern_revision_id, row.responsibility] for row in rows
                ],
            },
        )
        return str(event["event_id"])

    def propose_skill(self, skill: SkillRevision, *, event_index: int) -> str:
        self._require_discovery()
        self._validate_parents(skill.parent_revision_ids, self.skills, "skill")
        if skill.producing_failure_id not in self.failures:
            raise ValueError("skill producer is absent from failure memory")
        event = self.ledger.append(
            "skill_revision", event_index=event_index, payload=skill.to_mapping()
        )
        self.skills[skill.skill_revision_id] = skill
        return str(event["event_id"])

    def bind_pattern_skill(
        self,
        pattern_revision_id: str,
        skill_revision_id: str,
        *,
        applicability: float,
        event_index: int,
    ) -> str:
        if self.evaluation_only:
            raise PermissionError("sealed evaluation cannot alter skill bindings")
        if pattern_revision_id not in self.patterns or skill_revision_id not in self.skills:
            raise ValueError("pattern-skill binding references unknown revision")
        score = _finite(applicability, "applicability")
        if not 0.0 <= score <= 1.0:
            raise ValueError("applicability must be in [0, 1]")
        event = self.ledger.append(
            "pattern_skill_binding",
            event_index=event_index,
            payload={
                "pattern_revision_id": pattern_revision_id,
                "skill_revision_id": skill_revision_id,
                "applicability": score,
            },
        )
        return str(event["event_id"])

    def freeze_registry(
        self, registry: RegistrySnapshot, *, event_index: int
    ) -> str:
        self._require_discovery()
        if set(registry.stable_pattern_revision_ids) - set(self.patterns):
            raise ValueError("registry contains unknown pattern revision")
        if set(registry.stable_skill_revision_ids) - set(self.skills):
            raise ValueError("registry contains unknown skill revision")
        if any(self.patterns[row].state != "stable" for row in registry.stable_pattern_revision_ids):
            raise ValueError("registry may contain only stable patterns")
        if any(self.skills[row].state != "stable" for row in registry.stable_skill_revision_ids):
            raise ValueError("registry may contain only stable skills")
        event = self.ledger.append(
            "registry_snapshot", event_index=event_index, payload=registry.to_mapping()
        )
        self.registries[registry.registry_id] = registry
        return str(event["event_id"])

    def select(
        self,
        failure: FailureDeposit,
        *,
        responsibilities: Sequence[PatternResponsibility],
        candidate_skill_revision_ids: Sequence[str],
        registry_id: str,
        event_index: int,
    ) -> EcologySelection:
        registry = self.registries.get(registry_id)
        if registry is None:
            raise ValueError("unknown frozen registry")
        try:
            skills = tuple(self.skills[row] for row in candidate_skill_revision_ids)
        except KeyError as error:
            raise ValueError("unknown skill candidate") from error
        decision = self.router.select(
            failure,
            pattern_responsibilities=responsibilities,
            skills=skills,
            registry=registry,
            event_index=event_index,
        )
        self.ledger.append(
            "selection",
            event_index=event_index,
            payload={
                "selection_id": decision.selection_id,
                "failure_id": decision.failure_id,
                "registry_id": decision.registry_id,
                "candidate_skill_revision_ids": list(decision.candidate_skill_revision_ids),
                "selected_skill_revision_id": decision.selected_skill_revision_id,
                "pattern_responsibilities": [
                    [row.pattern_revision_id, row.responsibility]
                    for row in decision.pattern_responsibilities
                ],
                "scores": [list(row) for row in decision.scores],
                "posterior_before_sha256": decision.posterior_before_sha256,
            },
        )
        return decision

    def observe(
        self,
        decision: EcologySelection,
        feedback: DeploymentSkillFeedback | DelayedOutcomeFeedback,
        *,
        event_index: int,
    ) -> dict[str, object]:
        if self.evaluation_only and not feedback.evaluation_only:
            raise PermissionError("sealed evaluation feedback must be evaluation-only")
        snapshot = self.router.observe(decision, feedback)
        if isinstance(feedback, DelayedOutcomeFeedback):
            feedback_payload: dict[str, object] = {
                "feedback_kind": "delayed_outcome",
                "selection_id": feedback.selection_id,
                "selected_skill_revision_id": feedback.selected_skill_revision_id,
                "probe_id": feedback.probe_id,
                "selected_at_event_index": feedback.selected_at_event_index,
                "observed_after_event_index": feedback.observed_after_event_index,
                "pre_action_prior": feedback.pre_action_prior,
                "delayed_utility": feedback.delayed_utility,
                "valid": feedback.valid,
                "rolled_back": feedback.rolled_back,
                "delayed_regression": feedback.delayed_regression,
                "provenance": feedback.provenance,
                "evaluation_only": feedback.evaluation_only,
                "development_proxy": feedback.development_proxy,
                "reward": feedback.reward,
            }
        else:
            feedback_payload = {
                "feedback_kind": "immediate_probe",
                "selection_id": feedback.selection_id,
                "selected_skill_revision_id": feedback.selected_skill_revision_id,
                "probe_id": feedback.probe_id,
                "success": feedback.success,
                "locality_cost": feedback.locality_cost,
                "execution_cost": feedback.execution_cost,
                "rolled_back": feedback.rolled_back,
                "delayed_regression": feedback.delayed_regression,
                "valid": feedback.valid,
                "provenance": feedback.provenance,
                "gold_derived": feedback.gold_derived,
                "evaluation_only": feedback.evaluation_only,
                "reward": feedback.reward,
                "estimated_utility": feedback.estimated_utility,
            }
        self.ledger.append(
            "skill_feedback",
            event_index=event_index,
            payload=feedback_payload,
        )
        if not feedback.evaluation_only:
            self.ledger.append(
                "posterior_snapshot", event_index=event_index + 1, payload=snapshot
            )
        return snapshot

    def record_niche_snapshot(
        self, snapshot: NicheSnapshot, *, event_index: int
    ) -> str:
        if self.evaluation_only:
            raise PermissionError("sealed evaluation cannot change niche state")
        event = self.ledger.append(
            "niche_snapshot", event_index=event_index,
            payload={
                key: value
                for key, value in snapshot.__dict__.items()
            },
        )
        return str(event["event_id"])

    def record_niche_transition(
        self,
        previous: NicheSnapshot,
        current: NicheSnapshot,
        *,
        event_index: int,
    ) -> str | None:
        if self.evaluation_only:
            raise PermissionError("sealed evaluation cannot change niche lifecycle")
        if previous.niche_id != current.niche_id:
            raise ValueError("niche transition snapshots disagree")
        if previous.state == current.state:
            return None
        if not is_legal_niche_transition(previous.state, current.state):
            raise ValueError("invalid niche transition")
        event = self.ledger.append(
            "niche_transition", event_index=event_index,
            payload={
                "niche_id": current.niche_id,
                "from_state": previous.state,
                "to_state": current.state,
                "previous_snapshot_sha256": previous.snapshot_sha256,
                "current_snapshot_sha256": current.snapshot_sha256,
                "governance_observation_only": True,
            },
        )
        return str(event["event_id"])

    def record_discovery_pressure(
        self, pressure: DiscoveryPressure, *, event_index: int
    ) -> str:
        self._require_discovery()
        event = self.ledger.append(
            "discovery_pressure", event_index=event_index,
            payload={
                "niche_id": pressure.niche_id,
                "window_start": pressure.window_start,
                "window_end": pressure.window_end,
                "unmatched_mass": pressure.unmatched_mass,
                "abstention_rate": pressure.abstention_rate,
                "mean_absolute_residual": pressure.mean_absolute_residual,
                "proposal_kind": pressure.proposal_kind,
                "trigger_reasons": list(pressure.trigger_reasons),
                "governance_required": pressure.governance_required,
                "pressure_sha256": pressure.pressure_sha256,
            },
        )
        return str(event["event_id"])

    def lifecycle_transition(
        self,
        *,
        subject_kind: str,
        subject_revision_id: str,
        from_state: str,
        to_state: str,
        reason: str,
        supporting_event_ids: Sequence[str],
        event_index: int,
    ) -> str:
        self._require_discovery()
        states = PATTERN_STATES if subject_kind == "pattern" else SKILL_STATES if subject_kind == "skill" else NICHE_STATES if subject_kind == "niche" else None
        allowed = (
            _PATTERN_TRANSITIONS if subject_kind == "pattern"
            else _SKILL_TRANSITIONS if subject_kind == "skill"
            else _NICHE_TRANSITIONS if subject_kind == "niche"
            else None
        )
        if (
            states is None or from_state not in states or to_state not in states
            or from_state == to_state
            or (allowed is not None and (from_state, to_state) not in allowed)
        ):
            raise ValueError("invalid lifecycle transition")
        if subject_kind == "pattern" and subject_revision_id not in self.patterns:
            raise ValueError("unknown pattern lifecycle subject")
        if subject_kind == "skill" and subject_revision_id not in self.skills:
            raise ValueError("unknown skill lifecycle subject")
        event = self.ledger.append(
            "lifecycle_transition",
            event_index=event_index,
            payload={
                "subject_kind": subject_kind,
                "subject_revision_id": subject_revision_id,
                "from_state": from_state,
                "to_state": to_state,
                "reason": reason,
                "supporting_event_ids": list(supporting_event_ids),
                "governance_approved": True,
            },
        )
        if subject_kind == "pattern":
            old = self.patterns[subject_revision_id]
            self.patterns[subject_revision_id] = PatternRevision(**{
                **old.__dict__, "state": to_state
            })
        elif subject_kind == "skill":
            old = self.skills[subject_revision_id]
            self.skills[subject_revision_id] = SkillRevision(**{
                **old.__dict__, "state": to_state
            })
        return str(event["event_id"])

    def _require_discovery(self) -> None:
        if self.evaluation_only or not self.discovery_authorized:
            raise PermissionError("discovery/governance is not authorized in this epoch")

    @staticmethod
    def _validate_parents(
        parent_ids: Sequence[str], known: Mapping[str, object], name: str
    ) -> None:
        if set(parent_ids) - set(known):
            raise ValueError(f"{name} lineage references unknown parent")

    def _replay(self) -> None:
        selection_rows: list[tuple[int, Mapping[str, object]]] = []
        observed_selection_ids: set[str] = set()
        for event in self.ledger.events:
            payload = event["payload"]
            if not isinstance(payload, Mapping):
                raise ValueError("ecology event payload is invalid")
            if event["event_type"] == "failure_observed":
                failure = FailureDeposit(
                    str(payload["failure_id"]), str(payload["case_id"]),
                    str(payload["family_id_audit_only"]),
                    str(payload["failure_memory_sha256"]),
                    tuple((str(row[0]), float(row[1])) for row in payload["features"]),
                    str(payload["context_sha256"]), str(payload["provenance_sha256"]),
                )
                self.failures[failure.failure_id] = failure
            elif event["event_type"] == "pattern_revision":
                pattern = PatternRevision(
                    str(payload["pattern_revision_id"]), str(payload["pattern_id"]),
                    dict(payload["predicate"]), tuple(payload["feature_signature"]),
                    tuple(payload["parent_revision_ids"]), str(payload["derivation_kind"]),
                    str(payload["state"]),
                )
                self.patterns[pattern.pattern_revision_id] = pattern
            elif event["event_type"] == "skill_revision":
                skill_payload = dict(payload)
                skill_payload.pop("program_sha256", None)
                skill = SkillRevision(
                    str(skill_payload["skill_revision_id"]), str(skill_payload["skill_id"]),
                    dict(skill_payload["program"]), dict(skill_payload["parameter_schema"]),
                    tuple(dict(row) for row in skill_payload["preconditions"]),
                    tuple(dict(row) for row in skill_payload["postconditions"]),
                    dict(skill_payload["success_probe"]), dict(skill_payload["mutation_budget"]),
                    dict(skill_payload["rollback_program"]),
                    tuple(skill_payload["parent_revision_ids"]),
                    str(skill_payload["derivation_kind"]),
                    str(skill_payload["producing_failure_id"]), str(skill_payload["state"]),
                )
                self.skills[skill.skill_revision_id] = skill
            elif event["event_type"] == "lifecycle_transition":
                kind = payload["subject_kind"]
                revision_id = str(payload["subject_revision_id"])
                if kind == "pattern" and revision_id in self.patterns:
                    old = self.patterns[revision_id]
                    self.patterns[revision_id] = PatternRevision(**{
                        **old.__dict__, "state": str(payload["to_state"])
                    })
                elif kind == "skill" and revision_id in self.skills:
                    old = self.skills[revision_id]
                    self.skills[revision_id] = SkillRevision(**{
                        **old.__dict__, "state": str(payload["to_state"])
                    })
            elif event["event_type"] == "registry_snapshot":
                registry = RegistrySnapshot(
                    str(payload["registry_id"]), int(payload["epoch"]),
                    tuple(payload["stable_pattern_revision_ids"]),
                    tuple(payload["stable_skill_revision_ids"]),
                    payload["parent_registry_id"], str(payload["config_sha256"]),
                    bool(payload["sealed"]),
                )
                self.registries[registry.registry_id] = registry
            elif event["event_type"] == "posterior_snapshot":
                self.router = GHOSTEcologyRouter.from_snapshot(payload)
            elif event["event_type"] == "selection":
                selection_rows.append((int(event["event_index"]), payload))
            elif event["event_type"] == "skill_feedback":
                observed_selection_ids.add(str(payload["selection_id"]))
        for event_index, payload in selection_rows:
            selection_id = str(payload["selection_id"])
            if selection_id in observed_selection_ids:
                continue
            responsibilities = tuple(
                PatternResponsibility(str(row[0]), float(row[1]))
                for row in payload["pattern_responsibilities"]
            )
            decision = EcologySelection(
                selection_id, event_index, str(payload["failure_id"]),
                str(payload["registry_id"]),
                tuple(payload["candidate_skill_revision_ids"]),
                str(payload["selected_skill_revision_id"]), responsibilities,
                tuple((str(row[0]), float(row[1])) for row in payload["scores"]),
                str(payload["posterior_before_sha256"]),
            )
            self.router.restore_pending(
                decision, self.failures[decision.failure_id],
                tuple(self.skills[row] for row in decision.candidate_skill_revision_ids),
            )


__all__ = [
    "DelayedOutcomeFeedback", "DeploymentSkillFeedback", "DiscoveryPressure",
    "EcologyLedger", "EcologySelection",
    "FailureDeposit", "GHOSTEcologyRouter", "GovernanceDecision",
    "GhostEcology", "NicheObservation", "NicheObserver", "NichePerturbationReport",
    "ObservableResidualGHOSTRouter", "ObservableResidualSelection",
    "NicheSnapshot", "PatternResponsibility",
    "PatternRevision", "PromotionEvidence", "RegistrySnapshot", "SkillRevision",
    "content_sha256", "derive_discovery_pressure", "observe_niche_perturbation",
    "is_legal_niche_transition",
    "propose_pattern_merge", "propose_pattern_split",
    "skill_promotion_decision",
    "validate_responsibilities",
]
