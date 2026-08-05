"""Leak-safe structural indications and scope-limited routing.

Extractors receive only deployment-visible query and recall payload.  Benchmark
flags, perturbation labels, gold fields, and post-outcome scores are absent from
the API.  The router receives the frozen selection explicitly so an empty scope
is an exact identity operation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..core.models import MemoryItem


SAFETY_SIGNAL = "safety"
COVERAGE_SIGNAL = "coverage"
DIVERGENCE_SIGNAL = "reference_contrast_divergence"
COLLISION_SIGNAL = "recall_set_collision"
TEMPORAL_SIGNAL = "temporal_content_contradiction"
ITEM_GATE_SIGNAL = "item_gate"
# Backwards-compatible names for pre-SIGIL artifacts.  New events use the
# explicit collision/temporal names above.
NEGATION_SIGNAL = COLLISION_SIGNAL
RECENCY_SIGNAL = TEMPORAL_SIGNAL
STRUCTURAL_SIGNAL_TYPES = frozenset(
    {
        SAFETY_SIGNAL,
        COVERAGE_SIGNAL,
        DIVERGENCE_SIGNAL,
        COLLISION_SIGNAL,
        TEMPORAL_SIGNAL,
        ITEM_GATE_SIGNAL,
    }
)

_ACTION_BY_SIGNAL = {
    SAFETY_SIGNAL: "safety_error",
    COVERAGE_SIGNAL: "retrieval_error",
    DIVERGENCE_SIGNAL: "item_wrong",
    COLLISION_SIGNAL: "item_conflict",
    TEMPORAL_SIGNAL: "item_stale",
}
_NEGATIONS = frozenset(
    {"not", "no", "never", "none", "neither", "nor", "without"}
)
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
RUNTIME_INPUT_FIELDS = ("query", "memory_id", "text", "store")
RUNTIME_INPUT_ALLOWLIST_SHA256 = hashlib.sha256(
    json.dumps(
        RUNTIME_INPUT_FIELDS,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
DETERMINISTIC_EXTRACTOR_VERSION = "sigil-structural-v1"


@dataclass(frozen=True)
class StructuralIndication:
    """One runtime-computed signal with auditable supporting identifiers."""

    signal_type: str
    action: str
    strength: float
    evidence_ids: tuple[str, ...]
    runtime_surface: str = "tier2_item"
    extractor_version: str = DETERMINISTIC_EXTRACTOR_VERSION
    input_allowlist_sha256: str = RUNTIME_INPUT_ALLOWLIST_SHA256
    model_identity: str | None = None
    prompt_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.signal_type not in STRUCTURAL_SIGNAL_TYPES:
            raise ValueError(f"unsupported structural signal: {self.signal_type}")
        if not self.action:
            raise ValueError("action must not be empty")
        if not math.isfinite(float(self.strength)):
            raise ValueError("strength must be finite")
        if not 0.0 <= float(self.strength) <= 1.0:
            raise ValueError("strength must be in [0, 1]")
        if not self.runtime_surface:
            raise ValueError("runtime_surface must not be empty")
        if not self.extractor_version:
            raise ValueError("extractor_version must not be empty")
        if not re.fullmatch(r"[0-9a-f]{64}", self.input_allowlist_sha256):
            raise ValueError("input_allowlist_sha256 must be a SHA-256 hex digest")
        object.__setattr__(
            self,
            "evidence_ids",
            tuple(str(value) for value in self.evidence_ids),
        )


@dataclass(frozen=True)
class ScopePolicy:
    """Active structural signals and optional per-signal domain allow-lists."""

    active_signal_types: frozenset[str] = frozenset()
    domain_scopes: tuple[tuple[str, tuple[str, ...]], ...] = ()
    version: str = "scope-v0-empty"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "active_signal_types",
            frozenset(str(value) for value in self.active_signal_types),
        )
        normalized = tuple(
            sorted(
                (
                    str(signal_type),
                    tuple(sorted({str(domain) for domain in domains})),
                )
                for signal_type, domains in self.domain_scopes
            )
        )
        object.__setattr__(self, "domain_scopes", normalized)
        if not self.version:
            raise ValueError("scope version must not be empty")

    @classmethod
    def active(
        cls,
        signal_types: Iterable[str],
        *,
        domains: Mapping[str, Iterable[str]] | None = None,
        version: str = "scope-v1",
    ) -> "ScopePolicy":
        return cls(
            active_signal_types=frozenset(str(value) for value in signal_types),
            domain_scopes=tuple(
                (signal_type, tuple(values))
                for signal_type, values in (domains or {}).items()
            ),
            version=version,
        )

    def is_active(self, signal_type: str, domain_fingerprint: str) -> bool:
        if signal_type not in self.active_signal_types:
            return False
        scopes = dict(self.domain_scopes).get(signal_type)
        return scopes is None or domain_fingerprint in scopes or "*" in scopes


@dataclass(frozen=True)
class RouteDecision:
    """Selected skill ids plus enough detail to audit an override."""

    selected_ids: tuple[str, ...]
    frozen_selected_ids: tuple[str, ...]
    routed: bool
    signal_type: str | None
    action: str | None
    reason: str


@dataclass(frozen=True)
class StructuralIndicationEvent:
    arena_id: str
    case_id: str
    domain_fingerprint: str
    scope_version: str
    signal_type: str
    action: str
    strength: float
    evidence_ids: tuple[str, ...]
    runtime_surface: str
    extractor_version: str
    input_allowlist_sha256: str
    model_identity: str | None
    prompt_sha256: str | None
    created_before_outcome: bool
    scope_active: bool
    route_selected: bool
    selected_skill_ids: tuple[str, ...]
    frozen_selected_skill_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["record_type"] = "structural_indication_event"
        return value


IndicationExtractor = Callable[
    [str, tuple[MemoryItem, ...]],
    Iterable[StructuralIndication],
]
ItemGateExtractor = IndicationExtractor


def build_live_item_gate_extractor(
    client: Any,
    *,
    model_identity: str,
    divergence_threshold: float = 0.5,
    timestamp_tolerance_days: int = 7,
    reconstruction_prompt_template: str | None = None,
    enable_collision: bool = True,
    enable_loo: bool = True,
    extractor_version: str = "live-item-gate-v1",
) -> ItemGateExtractor:
    """Adapt the live Tier-2 item gate to the leak-safe indication API.

    The returned callable accepts only the deployment-visible query and recall
    set.  PASS, technical failure, poisoning, and HITL outcomes deliberately
    abstain: none is allowed to become an automatic repair route.
    """

    if not str(model_identity).strip():
        raise ValueError("model_identity must not be empty")
    if not 0.0 <= float(divergence_threshold) <= 1.0:
        raise ValueError("divergence_threshold must be in [0, 1]")
    if timestamp_tolerance_days < 0:
        raise ValueError("timestamp_tolerance_days must be >= 0")
    if not str(extractor_version).strip():
        raise ValueError("extractor_version must not be empty")
    prompt_identity = (
        reconstruction_prompt_template
        if reconstruction_prompt_template is not None
        else "cmd_audit.item_gate.default_reconstruction_prompt"
    )
    prompt_sha256 = hashlib.sha256(
        prompt_identity.encode("utf-8")
    ).hexdigest()

    def extract(
        query: str,
        memory_items: tuple[MemoryItem, ...],
    ) -> tuple[StructuralIndication, ...]:
        from ..item_gate.gate import run_item_gate_for_recall_set

        if not memory_items:
            return ()
        result = run_item_gate_for_recall_set(
            client,
            memory_items,
            query,
            divergence_threshold=divergence_threshold,
            timestamp_tolerance_days=timestamp_tolerance_days,
            reconstruction_prompt_template=reconstruction_prompt_template,
            enable_collision=enable_collision,
            enable_loo=enable_loo,
        )
        return item_gate_result_to_indications(
            result,
            model_identity=model_identity,
            prompt_sha256=prompt_sha256,
            extractor_version=extractor_version,
        )

    setattr(extract, "extractor_version", extractor_version)
    setattr(extract, "input_allowlist_sha256", RUNTIME_INPUT_ALLOWLIST_SHA256)
    setattr(extract, "model_identity", model_identity)
    setattr(extract, "prompt_sha256", prompt_sha256)
    return extract


def item_gate_result_to_indications(
    result: object | None,
    *,
    model_identity: str,
    prompt_sha256: str,
    extractor_version: str = "live-item-gate-v1",
) -> tuple[StructuralIndication, ...]:
    """Convert a completed live item-gate verdict without reading case labels."""

    if result is None:
        return ()
    status = getattr(getattr(result, "status", None), "value", None)
    mappings = {
        "item_stale": (TEMPORAL_SIGNAL, "item_stale"),
        "item_conflict": (COLLISION_SIGNAL, "item_conflict"),
        "item_wrong": (DIVERGENCE_SIGNAL, "item_wrong"),
        "item_compression_distorted": (
            DIVERGENCE_SIGNAL,
            "item_compression_distorted",
        ),
    }
    mapped = mappings.get(str(status))
    if mapped is None:
        return ()

    target = getattr(result, "target_item", None)
    target_id = str(getattr(target, "memory_id", ""))
    collisions = tuple(getattr(result, "collision_results", ()) or ())
    relevant = tuple(
        collision
        for collision in collisions
        if target_id
        in {
            str(getattr(getattr(collision, "item_a", None), "memory_id", "")),
            str(getattr(getattr(collision, "item_b", None), "memory_id", "")),
        }
    )
    evidence_ids = _item_gate_evidence_ids(
        status=str(status),
        target_id=target_id,
        collisions=relevant,
    )
    strength = _item_gate_strength(result, relevant)
    return (
        StructuralIndication(
            signal_type=mapped[0],
            action=mapped[1],
            strength=strength,
            evidence_ids=evidence_ids,
            runtime_surface="tier2_item_gate",
            extractor_version=extractor_version,
            input_allowlist_sha256=RUNTIME_INPUT_ALLOWLIST_SHA256,
            model_identity=model_identity,
            prompt_sha256=prompt_sha256,
        ),
    )


def extract_structural_indications(
    query: str,
    items: Sequence[MemoryItem | Mapping[str, object]],
    *,
    item_gate_extractor: ItemGateExtractor | None = None,
    independent_safety_extractor: IndicationExtractor | None = None,
) -> tuple[StructuralIndication, ...]:
    """Compute indications without accepting benchmark-control metadata.

    Safety is deliberately absent from the built-in extractors.  A caller may
    provide an independently versioned safety extractor, but it receives the
    same query/recall-only view as every other runtime extractor.
    """

    memory_items = tuple(_memory_item(value) for value in items)
    output: list[StructuralIndication] = []

    query_tokens = _tokens(query)
    covered = (
        set().union(*(_tokens(item.text) for item in memory_items))
        if memory_items
        else set()
    )
    coverage = (
        len(query_tokens & covered) / len(query_tokens) if query_tokens else 1.0
    )
    if coverage < 0.2:
        output.append(
            StructuralIndication(
                COVERAGE_SIGNAL,
                _ACTION_BY_SIGNAL[COVERAGE_SIGNAL],
                1.0 - coverage,
                tuple(item.memory_id for item in memory_items),
            )
        )

    for left_index, left in enumerate(memory_items):
        for right in memory_items[left_index + 1 :]:
            left_tokens = _tokens(left.text)
            right_tokens = _tokens(right.text)
            overlap = _overlap_coefficient(left_tokens, right_tokens)
            if (
                overlap >= 0.5
                and bool(left_tokens & _NEGATIONS)
                != bool(right_tokens & _NEGATIONS)
            ):
                output.append(
                    StructuralIndication(
                        NEGATION_SIGNAL,
                        _ACTION_BY_SIGNAL[COLLISION_SIGNAL],
                        overlap,
                        (left.memory_id, right.memory_id),
                    )
                )

            left_time = _timestamp(left.store)
            right_time = _timestamp(right.store)
            if (
                overlap >= 0.2
                and left_tokens != right_tokens
                and left_time is not None
                and right_time is not None
                and abs((left_time - right_time).total_seconds())
                > 7 * 24 * 60 * 60
            ):
                newer, older = (
                    (left, right) if left_time > right_time else (right, left)
                )
                output.append(
                    StructuralIndication(
                        RECENCY_SIGNAL,
                        _ACTION_BY_SIGNAL[TEMPORAL_SIGNAL],
                        overlap,
                        (newer.memory_id, older.memory_id),
                    )
                )

    if item_gate_extractor is not None:
        output.extend(item_gate_extractor(query, memory_items))
    if independent_safety_extractor is not None:
        safety_rows = tuple(independent_safety_extractor(query, memory_items))
        if any(row.signal_type != SAFETY_SIGNAL for row in safety_rows):
            raise ValueError(
                "independent_safety_extractor may emit only safety indications"
            )
        output.extend(safety_rows)
    return _deduplicate_indications(output)


def route(
    candidates: Sequence[object],
    gf_scores: Mapping[str, float | None],
    indications: Sequence[StructuralIndication],
    scope: ScopePolicy,
    *,
    domain_fingerprint: str = "",
    frozen_selected_ids: Sequence[str] | None = None,
) -> RouteDecision:
    """Route within active scope; otherwise preserve frozen selection exactly.

    Abstention/null protection stays outside this function.  Callers may pass
    their already-materialized ``frozen_selected_ids`` to make the G-S1
    invariant byte-for-byte exact.
    """

    frozen = (
        tuple(str(value) for value in frozen_selected_ids)
        if frozen_selected_ids is not None
        else _frozen_argmax(candidates, gf_scores)
    )
    active = [
        (index, indication)
        for index, indication in enumerate(indications)
        if scope.is_active(indication.signal_type, domain_fingerprint)
    ]
    if not active:
        return RouteDecision(
            selected_ids=frozen,
            frozen_selected_ids=frozen,
            routed=False,
            signal_type=None,
            action=None,
            reason="scope_empty_or_no_active_indication",
        )

    candidate_rows = [
        (_candidate_id(candidate), _candidate_actions(candidate))
        for candidate in candidates
    ]
    for _index, indication in sorted(
        active,
        key=lambda row: (-float(row[1].strength), row[0]),
    ):
        matching_ids = [
            candidate_id
            for candidate_id, actions in candidate_rows
            if indication.action in actions
        ]
        if not matching_ids:
            continue
        selected = min(
            matching_ids,
            key=lambda candidate_id: (
                -_finite_score(gf_scores.get(candidate_id)),
                candidate_id,
            ),
        )
        return RouteDecision(
            selected_ids=(selected,),
            frozen_selected_ids=frozen,
            routed=(selected,) != frozen,
            signal_type=indication.signal_type,
            action=indication.action,
            reason="active_structural_indication",
        )

    return RouteDecision(
        selected_ids=frozen,
        frozen_selected_ids=frozen,
        routed=False,
        signal_type=None,
        action=None,
        reason="active_indication_has_no_legal_candidate",
    )


def indication_events(
    *,
    arena_id: str,
    case_id: str,
    domain_fingerprint: str,
    scope: ScopePolicy,
    indications: Sequence[StructuralIndication],
    decision: RouteDecision,
) -> tuple[StructuralIndicationEvent, ...]:
    return tuple(
        StructuralIndicationEvent(
            arena_id=arena_id,
            case_id=case_id,
            domain_fingerprint=domain_fingerprint,
            scope_version=scope.version,
            signal_type=indication.signal_type,
            action=indication.action,
            strength=indication.strength,
            evidence_ids=indication.evidence_ids,
            runtime_surface=indication.runtime_surface,
            extractor_version=indication.extractor_version,
            input_allowlist_sha256=indication.input_allowlist_sha256,
            model_identity=indication.model_identity,
            prompt_sha256=indication.prompt_sha256,
            created_before_outcome=True,
            scope_active=scope.is_active(
                indication.signal_type,
                domain_fingerprint,
            ),
            route_selected=(
                decision.signal_type == indication.signal_type
                and decision.action == indication.action
            ),
            selected_skill_ids=decision.selected_ids,
            frozen_selected_skill_ids=decision.frozen_selected_ids,
        )
        for indication in indications
    )


def _frozen_argmax(
    candidates: Sequence[object],
    scores: Mapping[str, float | None],
) -> tuple[str, ...]:
    values = [
        (_finite_score(scores.get(_candidate_id(candidate))), _candidate_id(candidate))
        for candidate in candidates
    ]
    positive = [row for row in values if row[0] > 0.0]
    return (min(positive, key=lambda row: (-row[0], row[1]))[1],) if positive else ()


def _candidate_id(candidate: object) -> str:
    if isinstance(candidate, str):
        return candidate
    value = getattr(candidate, "skill_id", None)
    if value is None and isinstance(candidate, Mapping):
        value = candidate.get("skill_id")
    if value is None:
        raise TypeError("candidate must be a skill id or expose skill_id")
    return str(value)


def _candidate_actions(candidate: object) -> frozenset[str]:
    operator = getattr(candidate, "operator", None)
    if operator is None and isinstance(candidate, Mapping):
        operator = candidate.get("operator")
    steps = getattr(operator, "steps", ())
    actions = {
        str(getattr(getattr(step, "action", ""), "value", getattr(step, "action", "")))
        for step in steps
    }
    candidate_id = _candidate_id(candidate)
    if candidate_id.startswith("seed:"):
        actions.add(candidate_id.split(":", 1)[1])
    return frozenset(action for action in actions if action)


def _memory_item(value: MemoryItem | Mapping[str, object]) -> MemoryItem:
    if isinstance(value, MemoryItem):
        return value
    return MemoryItem.from_mapping(dict(value))


def _tokens(text: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(str(text))}


def _overlap_coefficient(left: set[str], right: set[str]) -> float:
    denominator = min(len(left), len(right))
    return len(left & right) / denominator if denominator else 0.0


def _timestamp(value: str) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _item_gate_evidence_ids(
    *,
    status: str,
    target_id: str,
    collisions: Sequence[object],
) -> tuple[str, ...]:
    if status == "item_stale" and collisions:
        collision = max(
            collisions,
            key=lambda row: _collision_strength(row),
        )
        item_a = str(
            getattr(getattr(collision, "item_a", None), "memory_id", "")
        )
        item_b = str(
            getattr(getattr(collision, "item_b", None), "memory_id", "")
        )
        direction = getattr(collision, "timestamp_direction", None)
        if direction == "a_newer":
            return tuple(value for value in (item_a, item_b) if value)
        if direction == "b_newer":
            return tuple(value for value in (item_b, item_a) if value)
    ids = {
        str(getattr(getattr(row, "item_a", None), "memory_id", ""))
        for row in collisions
    } | {
        str(getattr(getattr(row, "item_b", None), "memory_id", ""))
        for row in collisions
    }
    if target_id:
        ids.add(target_id)
    return tuple(sorted(value for value in ids if value))


def _item_gate_strength(
    result: object,
    collisions: Sequence[object],
) -> float:
    strengths = [_collision_strength(row) for row in collisions]
    loo = getattr(result, "loo_result", None)
    divergence = getattr(loo, "divergence", None)
    if divergence is not None:
        strengths.append(_divergence_strength(divergence))
    finite = [value for value in strengths if math.isfinite(value)]
    return max(0.0, min(1.0, max(finite, default=1.0)))


def _collision_strength(collision: object) -> float:
    return _divergence_strength(getattr(collision, "divergence", None))


def _divergence_strength(divergence: object | None) -> float:
    if divergence is None:
        return 0.0
    try:
        value = float(getattr(divergence, "max_divergence"))
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def _finite_score(value: float | None) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return -math.inf
    return score if math.isfinite(score) else -math.inf


def _deduplicate_indications(
    values: Sequence[StructuralIndication],
) -> tuple[StructuralIndication, ...]:
    by_key: dict[tuple[str, str, tuple[str, ...]], StructuralIndication] = {}
    for value in values:
        key = (value.signal_type, value.action, value.evidence_ids)
        existing = by_key.get(key)
        if existing is None or value.strength > existing.strength:
            by_key[key] = value
    return tuple(by_key.values())
