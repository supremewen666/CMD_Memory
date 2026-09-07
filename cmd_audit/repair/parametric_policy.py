"""Deterministic, replayable policy seam for V4 neuro-symbolic repair.

This module deliberately learns *selection*, never a direct store mutation.
Complete intents are checked against a frozen relation graph and compiled into
the already-audited typed IR.  The small linear policy is a reference runtime:
it makes deposits inspectable and deterministic without model calls.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

from cmd_audit.counterfactual.actionability import ActionMode
from cmd_audit.counterfactual.program_ir import ActionKind
from cmd_audit.counterfactual.relation_graph import FrozenRelationGraph
from cmd_audit.counterfactual.successor_program_ir import (
    Action,
    If,
    Predicate,
    PredicateKind,
    Program,
    canonical_ast_hash,
)

COMPILER_VERSION = "cmd-neuro-symbolic-memory-evolution-v4"
FEATURE_SCHEMA_VERSION = "v4-deployment-visible-features-1"
_HEX = re.compile(r"^[0-9a-f]{64}$")
_EFFECTS = frozenset(
    {"annotate_conflict", "abstain", "verify", "demote", "suppress", "replace"}
)
_DESTRUCTIVE = frozenset({"demote", "suppress", "replace"})
_DENYLIST = re.compile(
    r"(?:^|[_:/.-])(gold|label|inject(?:or|ion)?|case|eval(?:uation)?|family|"
    r"outcome|recovery|locality|post)(?:$|[_:/.-])",
    re.IGNORECASE,
)
_STRATEGY_LEAK = re.compile(
    r"(?:^|[_:/.-])(gold(?:_answer|_evidence)?|label|eval(?:uation)?|"
    r"case_id|target_item_id|family_id)(?:$|[_:/.-])",
    re.IGNORECASE,
)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _require_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256")


def _require_identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return float(value)


@dataclass(frozen=True)
class RepairIntent:
    """A complete, case-bound proposal; ``strategy_id`` is reusable identity."""

    intent_id: str
    strategy_id: str
    relation_edge_id: str
    target_item_id: str | None
    effect: str
    replacement_item_id: str | None
    proposer_id: str
    proposer_model_hash: str
    evidence_ids: tuple[str, ...]

    @classmethod
    def build(
        cls,
        *,
        strategy_id: str,
        relation_edge_id: str,
        target_item_id: str | None,
        effect: str,
        replacement_item_id: str | None = None,
        proposer_id: str,
        proposer_model_hash: str,
        evidence_ids: Sequence[str],
    ) -> "RepairIntent":
        payload = {
            "strategy_id": strategy_id,
            "relation_edge_id": relation_edge_id,
            "target_item_id": target_item_id,
            "effect": effect,
            "replacement_item_id": replacement_item_id,
            "proposer_id": proposer_id,
            "proposer_model_hash": proposer_model_hash,
            "evidence_ids": list(evidence_ids),
        }
        return cls(intent_id=_hash(payload), **{**payload, "evidence_ids": tuple(evidence_ids)})

    def __post_init__(self) -> None:
        _require_hash(self.intent_id, "intent_id")
        _require_hash(self.proposer_model_hash, "proposer_model_hash")
        for name in ("strategy_id", "relation_edge_id", "proposer_id"):
            _require_identifier(getattr(self, name), name)
        if _STRATEGY_LEAK.search(self.strategy_id):
            raise ValueError("strategy_id carries reserved evaluation marker leakage")
        if self.effect not in _EFFECTS:
            raise ValueError("unregistered repair effect")
        if self.target_item_id is not None:
            _require_identifier(self.target_item_id, "target_item_id")
        if self.effect in _DESTRUCTIVE and self.target_item_id is None:
            raise ValueError("destructive intent requires target_item_id")
        if self.effect not in _DESTRUCTIVE and self.target_item_id is not None:
            raise ValueError("non-destructive intent cannot carry target_item_id")
        if (self.effect == "replace") != (self.replacement_item_id is not None):
            raise ValueError("replacement_item_id is required only for replace")
        if self.replacement_item_id is not None:
            _require_identifier(self.replacement_item_id, "replacement_item_id")
            if self.replacement_item_id == self.target_item_id:
                raise ValueError("replacement and target must differ")
        if not self.evidence_ids or tuple(sorted(self.evidence_ids)) != self.evidence_ids:
            raise ValueError("evidence_ids must be a sorted non-empty tuple")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence_ids must be unique")
        if any(not isinstance(item, str) or not item for item in self.evidence_ids):
            raise ValueError("evidence_ids must be non-empty strings")
        expected = _hash(self._payload())
        if self.intent_id != expected:
            raise ValueError("intent_id does not bind the concrete intent payload")

    def _payload(self) -> dict[str, object]:
        return {
            "strategy_id": self.strategy_id,
            "relation_edge_id": self.relation_edge_id,
            "target_item_id": self.target_item_id,
            "effect": self.effect,
            "replacement_item_id": self.replacement_item_id,
            "proposer_id": self.proposer_id,
            "proposer_model_hash": self.proposer_model_hash,
            "evidence_ids": list(self.evidence_ids),
        }

    def as_mapping(self) -> dict[str, object]:
        return {"intent_id": self.intent_id, **self._payload()}

    to_mapping = as_mapping

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "RepairIntent":
        if set(value) != {
            "intent_id", "strategy_id", "relation_edge_id", "target_item_id", "effect",
            "replacement_item_id", "proposer_id", "proposer_model_hash", "evidence_ids",
        }:
            raise ValueError("RepairIntent mapping is not closed")
        return cls(
            intent_id=value["intent_id"], strategy_id=value["strategy_id"],
            relation_edge_id=value["relation_edge_id"], target_item_id=value["target_item_id"],
            effect=value["effect"], replacement_item_id=value["replacement_item_id"],
            proposer_id=value["proposer_id"], proposer_model_hash=value["proposer_model_hash"],
            evidence_ids=tuple(value["evidence_ids"]),
        )

    @property
    def species_key(self) -> tuple[str, str, str, str]:
        return (self.strategy_id, self.effect, COMPILER_VERSION, self.proposer_model_hash)


@dataclass(frozen=True)
class PolicyContext:
    case_id: str
    event_index: int
    graph_sha256: str
    runtime_surface: str
    domain: str
    semantic_cluster: str
    signal_signature: str
    features: Mapping[str, float]

    def __post_init__(self) -> None:
        for name in ("case_id", "runtime_surface", "domain", "semantic_cluster", "signal_signature"):
            _require_identifier(getattr(self, name), name)
        if isinstance(self.event_index, bool) or not isinstance(self.event_index, int) or self.event_index <= 0:
            raise ValueError("event_index must be a positive integer")
        _require_hash(self.graph_sha256, "graph_sha256")
        canonical_features: dict[str, float] = {}
        for key, value in self.features.items():
            _require_identifier(key, "feature name")
            if _DENYLIST.search(key):
                raise ValueError("feature name contains a forbidden non-runtime token")
            canonical_features[key] = _finite(value, f"feature {key}")
        object.__setattr__(self, "features", MappingProxyType(dict(sorted(canonical_features.items()))))

    def as_mapping(self) -> dict[str, object]:
        return {
            "case_id": self.case_id, "event_index": self.event_index,
            "graph_sha256": self.graph_sha256, "runtime_surface": self.runtime_surface,
            "domain": self.domain, "semantic_cluster": self.semantic_cluster,
            "signal_signature": self.signal_signature, "features": dict(self.features),
        }

    def content_hash(self) -> str:
        return _hash(self.as_mapping())

    to_mapping = as_mapping

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "PolicyContext":
        if set(value) != {
            "case_id", "event_index", "graph_sha256", "runtime_surface", "domain",
            "semantic_cluster", "signal_signature", "features",
        }:
            raise ValueError("PolicyContext mapping is not closed")
        return cls(**value)


@dataclass(frozen=True)
class SelectionDecision:
    selection_id: str
    case_id: str
    event_index: int
    graph_sha256: str
    context_sha256: str
    niche_path: tuple[str, ...]
    selected_intent_id: str | None
    ranked_intent_ids: tuple[str, ...]
    scores: tuple[tuple[str, float], ...]
    policy_snapshot_sha256: str
    reason: str

    def _payload(self) -> dict[str, object]:
        return {
            "case_id": self.case_id, "event_index": self.event_index,
            "graph_sha256": self.graph_sha256,
            "context_sha256": self.context_sha256,
            "niche_path": list(self.niche_path),
            "selected_intent_id": self.selected_intent_id,
            "ranked_intent_ids": list(self.ranked_intent_ids),
            "scores": [list(item) for item in self.scores],
            "policy_snapshot_sha256": self.policy_snapshot_sha256, "reason": self.reason,
        }

    def __post_init__(self) -> None:
        _require_hash(self.selection_id, "selection_id")
        _require_hash(self.graph_sha256, "graph_sha256")
        _require_hash(self.context_sha256, "context_sha256")
        _require_hash(self.policy_snapshot_sha256, "policy_snapshot_sha256")
        if self.reason not in {"selected", "no_candidates", "abstain", "below_margin", "invalid_context"}:
            raise ValueError("unregistered selection reason")
        if tuple(sorted(self.ranked_intent_ids)) != tuple(sorted(dict.fromkeys(self.ranked_intent_ids))):
            raise ValueError("ranked intent IDs must be unique")
        if tuple(item[0] for item in self.scores) != self.ranked_intent_ids:
            raise ValueError("scores must follow ranked_intent_ids")
        if any(not math.isfinite(score) for _, score in self.scores):
            raise ValueError("selection scores must be finite")
        if self.selected_intent_id is not None and self.selected_intent_id not in self.ranked_intent_ids:
            raise ValueError("selected intent must be ranked")
        if self.selection_id != _hash(self._payload()):
            raise ValueError("selection_id does not bind decision payload")

    @property
    def score_by_intent(self) -> dict[str, float]:
        return dict(self.scores)

    def as_mapping(self) -> dict[str, object]:
        return {"selection_id": self.selection_id, **self._payload()}

    to_mapping = as_mapping

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "SelectionDecision":
        if set(value) != set(cls.__dataclass_fields__):
            raise ValueError("SelectionDecision mapping is not closed")
        converted = dict(value)
        converted["niche_path"] = tuple(value["niche_path"])
        converted["ranked_intent_ids"] = tuple(value["ranked_intent_ids"])
        converted["scores"] = tuple((str(item[0]), float(item[1])) for item in value["scores"])
        return cls(**converted)


@dataclass(frozen=True)
class PolicySnapshot:
    parent_snapshot_sha256: str | None
    effective_after_event_index: int
    feature_schema_version: str
    action_biases: tuple[tuple[str, str, float], ...]
    feature_weights: tuple[tuple[str, str, str, float], ...]
    strategy_priors: tuple[tuple[str, str, float], ...]
    intent_priors: tuple[tuple[str, float], ...]
    learning_config: tuple[tuple[str, float], ...]
    niche_statuses: tuple[tuple[str, str], ...]
    source_observation_hashes: tuple[str, ...]
    snapshot_sha256: str

    @classmethod
    def build(cls, **kwargs: object) -> "PolicySnapshot":
        payload = cls._payload_from_kwargs(**kwargs)
        return cls(snapshot_sha256=_hash(payload), **kwargs)

    @staticmethod
    def _payload_from_kwargs(**kwargs: object) -> dict[str, object]:
        return {key: value for key, value in kwargs.items() if key != "snapshot_sha256"}

    def _payload(self) -> dict[str, object]:
        return self._payload_from_kwargs(**{name: getattr(self, name) for name in self.__dataclass_fields__})

    def __post_init__(self) -> None:
        if self.parent_snapshot_sha256 is not None:
            _require_hash(self.parent_snapshot_sha256, "parent_snapshot_sha256")
        if not isinstance(self.effective_after_event_index, int) or self.effective_after_event_index < -1:
            raise ValueError("effective_after_event_index must be >= -1")
        _require_identifier(self.feature_schema_version, "feature_schema_version")
        _require_hash(self.snapshot_sha256, "snapshot_sha256")
        for values, width, label in (
            (self.action_biases, 3, "action_biases"), (self.feature_weights, 4, "feature_weights"),
            (self.strategy_priors, 3, "strategy_priors"), (self.intent_priors, 2, "intent_priors"),
            (self.learning_config, 2, "learning_config"),
        ):
            if tuple(sorted(values)) != values or len(set(values)) != len(values):
                raise ValueError(f"{label} must be sorted and unique")
            for row in values:
                if len(row) != width or not math.isfinite(float(row[-1])):
                    raise ValueError(f"{label} has invalid numeric content")
        if tuple(sorted(self.niche_statuses)) != self.niche_statuses or len(set(self.niche_statuses)) != len(self.niche_statuses):
            raise ValueError("niche_statuses must be sorted and unique")
        if any(status not in NicheStatus.VALUES for _, status in self.niche_statuses):
            raise ValueError("niche_statuses contains an unknown status")
        if tuple(sorted(self.source_observation_hashes)) != self.source_observation_hashes:
            raise ValueError("source_observation_hashes must be sorted")
        for value in self.source_observation_hashes:
            _require_hash(value, "source observation hash")
        if self.snapshot_sha256 != _hash(self._payload()):
            raise ValueError("policy snapshot hash mismatch")

    def as_mapping(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    to_mapping = as_mapping

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "PolicySnapshot":
        if set(value) != set(cls.__dataclass_fields__):
            raise ValueError("PolicySnapshot mapping is not closed")
        converted = dict(value)
        for name in ("action_biases", "feature_weights", "strategy_priors", "intent_priors", "learning_config", "niche_statuses"):
            converted[name] = tuple(tuple(item) for item in value[name])
        converted["source_observation_hashes"] = tuple(value["source_observation_hashes"])
        return cls(**converted)


class NicheStatus:
    COLD = "cold"
    PROBATION = "probation"
    STABLE = "stable"
    RETIRED = "retired"
    VALUES = frozenset({COLD, PROBATION, STABLE, RETIRED})


def niche_path(context: PolicyContext) -> tuple[str, ...]:
    surface = f"global/surface:{context.runtime_surface}"
    semantic = f"{surface}/semantic:{context.semantic_cluster}"
    return ("global", surface, semantic, f"{semantic}/signals:{context.signal_signature}")


def _strategy_embeds_graph_identifier(
    strategy_id: str,
    graph: FrozenRelationGraph,
) -> bool:
    identifiers = {
        graph.case_id,
        graph.graph_sha256,
        *(
            identifier
            for edge in graph.edges
            for identifier in (
                edge.edge_id,
                edge.pair_id,
                edge.left_item_id,
                edge.right_item_id,
            )
        ),
    }
    normalized_strategy = strategy_id.casefold()
    return any(
        len(identifier) >= 7 and identifier.casefold() in normalized_strategy
        for identifier in identifiers
    )


def compile_intent(intent: RepairIntent, *, graph: FrozenRelationGraph) -> Program:
    """Compile one already-complete intent; this never enumerates programs."""
    if graph.graph_sha256 == "":  # placate structural readers; graph validates itself.
        raise ValueError("unreachable invalid graph")
    if _strategy_embeds_graph_identifier(intent.strategy_id, graph):
        raise ValueError("strategy_id carries frozen graph identifier leakage")
    edge = next((item for item in graph.edges if item.edge_id == intent.relation_edge_id), None)
    if edge is None:
        raise ValueError("intent relation edge is absent from frozen graph")
    if edge.case_id != graph.case_id:
        raise ValueError("cross-case relation edge")
    action = Action(ActionKind(intent.effect))
    if intent.effect in _DESTRUCTIVE:
        if edge.actionability.mode is not ActionMode.DESTRUCTIVE:
            raise ValueError("unknown/conflicting direction cannot compile destructively")
        if intent.target_item_id != edge.actionability.target_item_id:
            raise ValueError("destructive target is not the frozen actionability target")
        if intent.effect == "replace" and intent.replacement_item_id != edge.actionability.survivor_item_id:
            raise ValueError("replace must name the frozen surviving item")
        program: Program = If(
            Predicate(
                PredicateKind.SUPERSEDED_ITEM,
                relation_edge_id=edge.edge_id,
                target_item_id=intent.target_item_id,
            ),
            action,
        )
    else:
        if edge.actionability.mode is ActionMode.DESTRUCTIVE:
            # Non-destructive actions remain safe even when a direction happens
            # to exist; they still bind to the divergent relation itself.
            pass
        program = If(
            Predicate(PredicateKind.DIVERGENT_PAIR_MEMBER, relation_edge_id=edge.edge_id),
            action,
        )
    canonical_ast_hash(program)  # exercises registered bounds/IR canonicalization.
    return program


@dataclass(frozen=True)
class OutcomeObservation:
    selection_id: str
    case_id: str
    observed_after_event_index: int
    family_id: str
    intent_id: str
    recovery_gain: float
    locality_cost: float
    changed_item_count: int
    valid: bool
    rolled_back: bool

    def __post_init__(self) -> None:
        for name in ("selection_id", "case_id", "family_id", "intent_id"):
            _require_identifier(getattr(self, name), name)
        if isinstance(self.observed_after_event_index, bool) or not isinstance(self.observed_after_event_index, int):
            raise ValueError("observed_after_event_index must be an integer")
        _finite(self.recovery_gain, "recovery_gain")
        _finite(self.locality_cost, "locality_cost")
        if isinstance(self.changed_item_count, bool) or not isinstance(self.changed_item_count, int) or self.changed_item_count < 0:
            raise ValueError("changed_item_count must be a non-negative integer")

    def content_hash(self) -> str:
        return _hash({name: getattr(self, name) for name in self.__dataclass_fields__})

    def as_mapping(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    to_mapping = as_mapping

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "OutcomeObservation":
        if set(value) != set(cls.__dataclass_fields__):
            raise ValueError("OutcomeObservation mapping is not closed")
        return cls(**value)


class OnlineRepairPolicy:
    """Small deterministic linear policy with immutable, content-addressed snapshots."""

    def __init__(
        self,
        *,
        learning_rate: float = 0.1,
        pairwise_margin: float = 0.1,
        locality_penalty: float = 1.0,
        change_penalty: float = 0.05,
        selection_margin: float = 0.0,
    ) -> None:
        config = {
            "change_penalty": _finite(change_penalty, "change_penalty"),
            "learning_rate": _finite(learning_rate, "learning_rate"),
            "locality_penalty": _finite(locality_penalty, "locality_penalty"),
            "pairwise_margin": _finite(pairwise_margin, "pairwise_margin"),
            "selection_margin": _finite(selection_margin, "selection_margin"),
        }
        if config["learning_rate"] <= 0 or config["pairwise_margin"] < 0 or config["selection_margin"] < 0:
            raise ValueError("learning_rate must be positive and margins non-negative")
        self._config = config
        self._action_biases: dict[tuple[str, str], float] = {}
        self._feature_weights: dict[tuple[str, str, str], float] = {}
        self._strategy_priors: dict[tuple[str, str], float] = {}
        self._intent_priors: dict[str, float] = {}
        self._niche_statuses: dict[str, str] = {"global": NicheStatus.STABLE}
        self._selections: dict[str, tuple[PolicyContext, tuple[RepairIntent, ...], SelectionDecision]] = {}
        self._last_selection_event = -1
        self._source_observations: set[str] = set()
        self._snapshot = self._make_snapshot(parent=None, effective_after=-1)

    @property
    def snapshot(self) -> PolicySnapshot:
        return self._snapshot

    @classmethod
    def from_snapshot(cls, snapshot: PolicySnapshot) -> "OnlineRepairPolicy":
        """Restore a frozen policy state; selection history is intentionally not restored."""
        config = dict(snapshot.learning_config)
        policy = cls(**config)
        policy._action_biases = {(path, effect): value for path, effect, value in snapshot.action_biases}
        policy._feature_weights = {
            (path, effect, feature): value
            for path, effect, feature, value in snapshot.feature_weights
        }
        policy._strategy_priors = {
            (strategy, effect): value for strategy, effect, value in snapshot.strategy_priors
        }
        policy._intent_priors = dict(snapshot.intent_priors)
        policy._niche_statuses = dict(snapshot.niche_statuses)
        policy._source_observations = set(snapshot.source_observation_hashes)
        policy._snapshot = snapshot
        policy._last_selection_event = snapshot.effective_after_event_index
        return policy

    def set_niche_status(self, path: str, status: str) -> None:
        if status not in NicheStatus.VALUES:
            raise ValueError("unregistered niche status")
        if path != "global" and not re.fullmatch(r"global/surface:[^/]+(?:/semantic:[^/]+(?:/signals:[^/]+)?)?", path):
            raise ValueError("invalid runtime niche path")
        self._niche_statuses[path] = status

    def niche_status(self, path: str) -> str:
        return self._niche_statuses.get(path, NicheStatus.COLD)

    def resolved_niche_path(self, context: PolicyContext) -> tuple[str, ...]:
        full = niche_path(context)
        stable = [path for path in full if self.niche_status(path) == NicheStatus.STABLE]
        chosen = stable[-1] if stable else "global"
        return full[: full.index(chosen) + 1]

    def select(
        self,
        context: PolicyContext,
        candidates: Sequence[RepairIntent],
        *,
        score_offsets: Mapping[str, float] | None = None,
    ) -> SelectionDecision:
        if context.event_index <= self._last_selection_event:
            raise ValueError("selection event indexes must be strictly increasing")
        if len({item.intent_id for item in candidates}) != len(candidates):
            raise ValueError("candidate intent IDs must be unique")
        chosen_path = self.resolved_niche_path(context)
        chosen_niche = chosen_path[-1]
        validated = tuple(candidates)
        for item in validated:
            # Dataclass validation above is intentionally repeated at this public seam.
            RepairIntent.from_mapping(item.as_mapping())
        candidate_ids = {item.intent_id for item in validated}
        offsets = (
            {intent_id: 0.0 for intent_id in candidate_ids}
            if score_offsets is None
            else {
                str(intent_id): _finite(value, f"score offset:{intent_id}")
                for intent_id, value in score_offsets.items()
            }
        )
        if set(offsets) != candidate_ids:
            raise ValueError("score offsets must exactly cover candidates")
        scores = sorted(
            (
                (
                    item.intent_id,
                    self._score(context, item, chosen_niche) + offsets[item.intent_id],
                )
                for item in validated
            ),
            key=lambda item: (-item[1], item[0]),
        )
        ranked = tuple(item[0] for item in scores)
        selected: str | None = None
        reason = "no_candidates"
        if scores:
            winner = next(item for item in validated if item.intent_id == scores[0][0])
            if winner.effect == "abstain":
                reason = "abstain"
            elif len(scores) > 1 and scores[0][1] - scores[1][1] < self._config["selection_margin"]:
                reason = "below_margin"
            else:
                selected, reason = winner.intent_id, "selected"
        decision_payload = {
            "case_id": context.case_id, "event_index": context.event_index,
            "graph_sha256": context.graph_sha256,
            "context_sha256": context.content_hash(),
            "niche_path": list(chosen_path),
            "selected_intent_id": selected, "ranked_intent_ids": list(ranked),
            "scores": [list(item) for item in scores],
            "policy_snapshot_sha256": self.snapshot.snapshot_sha256, "reason": reason,
        }
        decision = SelectionDecision(
            selection_id=_hash(decision_payload), case_id=context.case_id,
            event_index=context.event_index, graph_sha256=context.graph_sha256,
            context_sha256=context.content_hash(),
            niche_path=chosen_path, selected_intent_id=selected, ranked_intent_ids=ranked,
            scores=tuple(scores), policy_snapshot_sha256=self.snapshot.snapshot_sha256, reason=reason,
        )
        self._selections[decision.selection_id] = (context, validated, decision)
        self._last_selection_event = context.event_index
        return decision

    def observe(
        self,
        decision: SelectionDecision | Sequence[OutcomeObservation],
        observations: Sequence[OutcomeObservation] | None = None,
        observed_after_event_index: int | None = None,
    ) -> PolicySnapshot:
        """Accept only post-selection outcomes, then create one new snapshot."""
        if observations is None:
            if isinstance(decision, SelectionDecision):
                raise ValueError("observations are required for a selection decision")
            observation_rows = tuple(decision)
            decision_id: str | None = None
        else:
            if not isinstance(decision, SelectionDecision):
                raise ValueError("decision must be a SelectionDecision")
            decision_id = decision.selection_id
            observation_rows = tuple(observations)
            if observed_after_event_index is not None:
                if not isinstance(observed_after_event_index, int):
                    raise ValueError("observed_after_event_index must be an integer")
                if any(row.observed_after_event_index != observed_after_event_index for row in observation_rows):
                    raise ValueError("observation event indexes disagree with call boundary")
        if not observation_rows:
            return self.snapshot
        checked: list[tuple[OutcomeObservation, PolicyContext, tuple[RepairIntent, ...], SelectionDecision]] = []
        for observation in observation_rows:
            if decision_id is not None and observation.selection_id != decision_id:
                raise ValueError("observation does not belong to supplied decision")
            record = self._selections.get(observation.selection_id)
            if record is None:
                raise ValueError("outcome refers to an unknown selection")
            context, intents, decision = record
            if observation.case_id != context.case_id or observation.observed_after_event_index <= context.event_index:
                raise ValueError("outcome must occur after its own selection")
            if observation.intent_id not in {item.intent_id for item in intents}:
                raise ValueError("outcome intent was not a selection candidate")
            observation_hash = observation.content_hash()
            if observation_hash in self._source_observations:
                continue
            checked.append((observation, context, intents, decision))
        if not checked:
            return self.snapshot
        parent = self.snapshot.snapshot_sha256
        for observation, context, intents, decision in checked:
            self._source_observations.add(observation.content_hash())
            if not observation.valid or observation.rolled_back:
                continue
            utility = (
                observation.recovery_gain
                - self._config["locality_penalty"] * observation.locality_cost
                - self._config["change_penalty"] * observation.changed_item_count
            )
            candidate = next(item for item in intents if item.intent_id == observation.intent_id)
            niche = decision.niche_path[-1]
            lr = self._config["learning_rate"]
            self._strategy_priors[(candidate.strategy_id, candidate.effect)] = (
                self._strategy_priors.get((candidate.strategy_id, candidate.effect), 0.0) + lr * utility
            )
            self._intent_priors[candidate.intent_id] = self._intent_priors.get(candidate.intent_id, 0.0) + lr * utility
            self._action_biases[(niche, candidate.effect)] = self._action_biases.get((niche, candidate.effect), 0.0) + lr * utility
            for feature, value in context.features.items():
                key = (niche, candidate.effect, feature)
                self._feature_weights[key] = self._feature_weights.get(key, 0.0) + lr * utility * value
            # Pairwise margin: validated positive utility pushes this strategy
            # above every competing candidate from that pre-outcome selection.
            if utility > 0:
                for inferior in intents:
                    if inferior.intent_id == candidate.intent_id:
                        continue
                    gap = self._score(context, candidate, niche) - self._score(context, inferior, niche)
                    if gap < self._config["pairwise_margin"]:
                        delta = lr * (self._config["pairwise_margin"] - gap)
                        self._strategy_priors[(candidate.strategy_id, candidate.effect)] += delta
                        self._strategy_priors[(inferior.strategy_id, inferior.effect)] = self._strategy_priors.get((inferior.strategy_id, inferior.effect), 0.0) - delta
        effective_after = max(item[0].observed_after_event_index for item in checked)
        self._snapshot = self._make_snapshot(parent=parent, effective_after=effective_after)
        return self.snapshot

    def _score(self, context: PolicyContext, intent: RepairIntent, niche: str) -> float:
        return (
            self._intent_priors.get(intent.intent_id, 0.0)
            + self._strategy_priors.get((intent.strategy_id, intent.effect), 0.0)
            + self._action_biases.get((niche, intent.effect), 0.0)
            + sum(self._feature_weights.get((niche, intent.effect, key), 0.0) * value for key, value in context.features.items())
        )

    def _make_snapshot(self, *, parent: str | None, effective_after: int) -> PolicySnapshot:
        return PolicySnapshot.build(
            parent_snapshot_sha256=parent, effective_after_event_index=effective_after,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            action_biases=tuple(sorted((path, effect, value) for (path, effect), value in self._action_biases.items())),
            feature_weights=tuple(sorted((path, effect, name, value) for (path, effect, name), value in self._feature_weights.items())),
            strategy_priors=tuple(sorted((strategy, effect, value) for (strategy, effect), value in self._strategy_priors.items())),
            intent_priors=tuple(sorted(self._intent_priors.items())),
            learning_config=tuple(sorted(self._config.items())),
            niche_statuses=tuple(sorted(self._niche_statuses.items())),
            source_observation_hashes=tuple(sorted(self._source_observations)),
        )


__all__ = [
    "COMPILER_VERSION", "FEATURE_SCHEMA_VERSION", "NicheStatus", "OnlineRepairPolicy",
    "OutcomeObservation", "PolicyContext", "PolicySnapshot", "RepairIntent",
    "SelectionDecision", "compile_intent", "niche_path",
]
