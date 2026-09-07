"""Fail-closed Stage 9 bridges for external memory-system wrappers.

The bridge is intentionally narrow: an external system receives a runtime
decision view plus the legal action mask, and may only abstain or choose one
member of that mask.  It never receives evaluator-only repair truth.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import random
import subprocess
from typing import Mapping, Protocol, Sequence

from .contracts import DecisionView, canonical_sha256, deserialize_decision_view


REQUEST_SCHEMA = "cmd-spec-v03-industry-adapter-request-v1"
RESPONSE_SCHEMA = "cmd-spec-v03-industry-adapter-response-v1"
_TRACKS = frozenset({"controlled_a1", "controlled_a2", "native"})
_NAMESPACES = {"controlled_a1": "controlled", "controlled_a2": "controlled", "native": "native"}
_TERMINAL_STATUSES = frozenset({"OK", "UNSUPPORTED", "FAILED"})


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _exact_commit(value: object, field: str = "pinned_commit") -> str:
    text = _text(value, field)
    if len(text) != 40 or any(char not in "0123456789abcdef" for char in text.lower()):
        raise ValueError(f"{field} must be an exact 40-character git commit")
    return text.lower()


@dataclass(frozen=True)
class ResourceUsage:
    """Closed resource record emitted for every adapter result."""

    llm_calls: int
    input_tokens: int
    output_tokens: int
    wall_clock_seconds: float
    gpu_seconds: float

    def __post_init__(self) -> None:
        values = asdict(self)
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value)) or value < 0
            for value in values.values()
        ):
            raise ValueError("resource usage values must be finite non-negative numbers")
        if any(name != "wall_clock_seconds" and isinstance(value, float) and not value.is_integer() for name, value in values.items()):
            raise ValueError("resource usage counters must be integral except wall_clock_seconds")

    @classmethod
    def zero(cls) -> "ResourceUsage":
        return cls(0, 0, 0, 0.0, 0)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ResourceUsage":
        if set(value) != {"llm_calls", "input_tokens", "output_tokens", "wall_clock_seconds", "gpu_seconds"}:
            raise ValueError("resource usage must use the closed schema")
        return cls(**value)  # type: ignore[arg-type]

    def within(self, budget: "ResourceUsage") -> bool:
        return all(getattr(self, field) <= getattr(budget, field) for field in asdict(self))

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AdapterRequest:
    """Only serving-visible data crosses this boundary."""

    run_id: str
    system_id: str
    track: str
    score_namespace: str
    decision: Mapping[str, object]
    legal_operator_ids: tuple[str, ...]
    budget: ResourceUsage
    schema_version: str = REQUEST_SCHEMA

    def __post_init__(self) -> None:
        _text(self.run_id, "run_id")
        _text(self.system_id, "system_id")
        if self.schema_version != REQUEST_SCHEMA:
            raise ValueError("unsupported adapter request schema")
        if self.track not in _TRACKS or self.score_namespace != _NAMESPACES[self.track]:
            raise ValueError("track and score namespace are incompatible")
        if not self.legal_operator_ids or len(set(self.legal_operator_ids)) != len(self.legal_operator_ids):
            raise ValueError("legal operator IDs must be a non-empty unique tuple")
        if any(not isinstance(item, str) or not item for item in self.legal_operator_ids):
            raise ValueError("legal operator IDs must be non-empty strings")
        deserialize_decision_view(self.decision)

    @classmethod
    def from_decision(
        cls,
        *,
        run_id: str,
        system_id: str,
        track: str,
        decision: DecisionView,
        legal_operator_ids: Sequence[str],
        budget: ResourceUsage,
    ) -> "AdapterRequest":
        # The serving contract's transport representation is JSON, including
        # ``unsupported_fields`` as a list rather than the in-memory tuple.
        decision_json = json.loads(json.dumps(decision.to_mapping(), sort_keys=True))
        return cls(run_id, system_id, track, _NAMESPACES[track], decision_json, tuple(legal_operator_ids), budget)

    def to_mapping(self) -> dict[str, object]:
        return {**asdict(self), "legal_operator_ids": list(self.legal_operator_ids), "budget": self.budget.to_mapping()}


@dataclass(frozen=True)
class AdapterResponse:
    """Closed response: a legal runtime action or an explicit abstention."""

    status: str
    selected_operator_id: str | None
    abstain_reason: str | None
    usage: ResourceUsage
    adapter_revision: str
    schema_version: str = RESPONSE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != RESPONSE_SCHEMA:
            raise ValueError("unsupported adapter response schema")
        if self.status not in _TERMINAL_STATUSES:
            raise ValueError("adapter response status is unsupported")
        _text(self.adapter_revision, "adapter_revision")
        if self.selected_operator_id is not None:
            _text(self.selected_operator_id, "selected_operator_id")
        if self.abstain_reason is not None:
            _text(self.abstain_reason, "abstain_reason")
        if self.status == "OK" and self.selected_operator_id is None and self.abstain_reason is None:
            raise ValueError("an OK abstention requires a reason")
        if self.status != "OK" and (self.selected_operator_id is not None or self.abstain_reason is None):
            raise ValueError("non-OK responses must fail closed with an abstain reason")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "AdapterResponse":
        fields = {"schema_version", "status", "selected_operator_id", "abstain_reason", "usage", "adapter_revision"}
        if set(value) != fields or not isinstance(value.get("usage"), Mapping):
            raise ValueError("adapter response must use the closed schema")
        return cls(
            schema_version=value["schema_version"], status=value["status"],
            selected_operator_id=value["selected_operator_id"], abstain_reason=value["abstain_reason"],
            usage=ResourceUsage.from_mapping(value["usage"]), adapter_revision=value["adapter_revision"],
        )

    def verify_for(self, request: AdapterRequest) -> "AdapterResponse":
        if self.selected_operator_id is not None and self.selected_operator_id not in request.legal_operator_ids:
            raise ValueError("adapter selected an operator outside the runtime legal mask")
        if not self.usage.within(request.budget):
            raise ValueError("adapter resource usage exceeds the unified budget")
        return self

    def to_mapping(self) -> dict[str, object]:
        return {**asdict(self), "usage": self.usage.to_mapping()}


class IndustryAdapter(Protocol):
    capability_id: str
    supported_tracks: tuple[str, ...]

    def invoke(self, request: AdapterRequest) -> AdapterResponse: ...


def _failure(reason: str, revision: str) -> AdapterResponse:
    return AdapterResponse("FAILED", None, reason, ResourceUsage.zero(), revision)


class BuiltinNoRepair:
    capability_id = "builtin:no-repair"
    supported_tracks = ("controlled_a1", "controlled_a2")

    def invoke(self, request: AdapterRequest) -> AdapterResponse:
        _validate_supported_request(request, self)
        return AdapterResponse("OK", None, "no_repair", ResourceUsage.zero(), self.capability_id).verify_for(request)


class BuiltinRandomLegal:
    capability_id = "builtin:random-legal"
    supported_tracks = ("controlled_a1", "controlled_a2")

    def __init__(self, *, seed: int = 0) -> None:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("seed must be an integer")
        self.seed = seed

    def invoke(self, request: AdapterRequest) -> AdapterResponse:
        _validate_supported_request(request, self)
        # A per-request deterministic RNG makes paired replay independent of call order.
        sample_seed = int(canonical_sha256({"seed": self.seed, "run_id": request.run_id})[:16], 16)
        operator = random.Random(sample_seed).choice(tuple(sorted(request.legal_operator_ids)))
        return AdapterResponse("OK", operator, None, ResourceUsage.zero(), self.capability_id).verify_for(request)


class UnsupportedAdapter:
    """Declared capability without an executable wrapper; it never invents data."""

    def __init__(self, capability_id: str, supported_tracks: Sequence[str]) -> None:
        self.capability_id = _text(capability_id, "capability_id")
        self.supported_tracks = tuple(supported_tracks)
        if not self.supported_tracks or not set(self.supported_tracks).issubset(_TRACKS):
            raise ValueError("supported tracks are invalid")

    def invoke(self, request: AdapterRequest) -> AdapterResponse:
        _validate_supported_request(request, self)
        return AdapterResponse("UNSUPPORTED", None, "wrapper_unconfigured", ResourceUsage.zero(), self.capability_id).verify_for(request)


class PinnedJsonSubprocessAdapter:
    """Run an independently pinned wrapper over closed JSON stdin/stdout."""

    def __init__(
        self,
        *,
        capability_id: str,
        command: Sequence[str],
        repository: str | Path,
        pinned_commit: str,
        supported_tracks: Sequence[str] = ("controlled_a1", "controlled_a2", "native"),
        timeout_seconds: float = 60.0,
    ) -> None:
        self.capability_id = _text(capability_id, "capability_id")
        self.command = tuple(command)
        if not self.command or any(not isinstance(part, str) or not part for part in self.command):
            raise ValueError("command must be a non-empty tuple of strings")
        self.repository = Path(repository)
        self.pinned_commit = _exact_commit(pinned_commit)
        self.supported_tracks = tuple(supported_tracks)
        if not self.supported_tracks or not set(self.supported_tracks).issubset(_TRACKS):
            raise ValueError("supported tracks are invalid")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = float(timeout_seconds)

    def _verify_checkout(self) -> str | None:
        try:
            completed = subprocess.run(
                ("git", "-C", str(self.repository), "rev-parse", "HEAD"),
                shell=False, check=False, capture_output=True, text=True, timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        current = completed.stdout.strip().lower()
        return current if completed.returncode == 0 and len(current) == 40 else None

    def invoke(self, request: AdapterRequest) -> AdapterResponse:
        _validate_supported_request(request, self)
        if request.budget.wall_clock_seconds <= 0:
            return _failure("budget_exhausted", self.capability_id)
        current = self._verify_checkout()
        if current != self.pinned_commit:
            return _failure("pinned_commit_mismatch", self.capability_id)
        try:
            completed = subprocess.run(
                self.command, shell=False, cwd=self.repository, check=False, capture_output=True,
                input=json.dumps(request.to_mapping(), sort_keys=True, separators=(",", ":")), text=True,
                timeout=min(self.timeout_seconds, request.budget.wall_clock_seconds),
            )
        except subprocess.TimeoutExpired:
            return _failure("wrapper_timeout", self.capability_id)
        except OSError:
            return _failure("wrapper_start_failed", self.capability_id)
        if completed.returncode != 0:
            return _failure("wrapper_nonzero_exit", self.capability_id)
        try:
            response = AdapterResponse.from_mapping(json.loads(completed.stdout))
            return response.verify_for(request)
        except (TypeError, ValueError, json.JSONDecodeError):
            return _failure("wrapper_invalid_response", self.capability_id)


def _validate_supported_request(request: AdapterRequest, adapter: IndustryAdapter) -> None:
    if request.track not in adapter.supported_tracks:
        raise ValueError("adapter does not support this track")


def _capability_factory(system: str, config: Mapping[str, object] | None) -> IndustryAdapter:
    capability_id = f"{system}:adapter"
    if config is None:
        return UnsupportedAdapter(capability_id, ("controlled_a1", "controlled_a2", "native"))
    required = {"command", "repository", "pinned_commit"}
    if set(config) - (required | {"supported_tracks", "timeout_seconds"}) or not required.issubset(config):
        raise ValueError(f"{system} adapter configuration uses an unsupported schema")
    command = config["command"]
    if not isinstance(command, (tuple, list)):
        raise ValueError("command must be a JSON array or tuple")
    return PinnedJsonSubprocessAdapter(
        capability_id=capability_id, command=tuple(command), repository=config["repository"],
        pinned_commit=config["pinned_commit"], supported_tracks=tuple(config.get("supported_tracks", ("controlled_a1", "controlled_a2", "native"))),
        timeout_seconds=config.get("timeout_seconds", 60.0),
    )


def memskill_adapter(config: Mapping[str, object] | None = None) -> IndustryAdapter:
    return _capability_factory("memskill", config)


def erskill_adapter(config: Mapping[str, object] | None = None) -> IndustryAdapter:
    return _capability_factory("erskill", config)


def mem0_adapter(config: Mapping[str, object] | None = None) -> IndustryAdapter:
    return _capability_factory("mem0", config)
