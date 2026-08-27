"""Model-neutral serving providers for frozen CMD repair skills.

This module deliberately lives on the serving side of the v0.3 boundary.  It
accepts only a :class:`DecisionView`, a materialized :class:`MemoryState`, and
frozen ``SkillRevision`` candidates.  It does not import repair cases,
interventions, shadow matrices, or evaluator contracts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Any, Protocol
import urllib.error
from urllib.parse import urlparse
import urllib.request

from cmd_audit.repair.ghost_ecology import SkillRevision

from .contracts import DecisionView, canonical_sha256
from .repair_stream import MemoryState
from .router_stage5 import BackbonePrediction
from .syndrome_runtime import audit_structural_telemetry


_DEVELOPMENT = "DEVELOPMENT"
_PRODUCTION = "PRODUCTION"
_RESPONSE_FINGERPRINT = "response_fingerprint"
_EXTERNAL_MANIFEST = "external_manifest"
_SNAPSHOT_BINDINGS = {_RESPONSE_FINGERPRINT, _EXTERNAL_MANIFEST}


class BackboneProviderError(RuntimeError):
    """A provider, transport, budget, or closed-output validation failure."""


@dataclass(frozen=True)
class ResourceUsage:
    """Usage for one call or a cumulative provider session."""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    request_count: int = 1

    def __post_init__(self) -> None:
        for field in ("input_tokens", "output_tokens", "total_tokens", "request_count"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens + output_tokens")
        # A call record always carries one request; provider-level usage is its
        # exact cumulative sum and therefore may be zero or greater than one.

    def plus(self, other: "ResourceUsage") -> "ResourceUsage":
        return ResourceUsage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.total_tokens + other.total_tokens,
            self.request_count + other.request_count,
        )


@dataclass(frozen=True)
class ProviderBudget:
    """Hard aggregate budget, checked before a network request is issued."""

    max_requests: int
    max_total_tokens: int
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        for field in ("max_requests", "max_total_tokens", "max_input_tokens", "max_output_tokens"):
            value = getattr(self, field)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"{field} must be a non-negative integer or None")

    def preflight(self, current: ResourceUsage, *, estimated_input_tokens: int, reserved_output_tokens: int) -> None:
        if current.request_count + 1 > self.max_requests:
            raise BackboneProviderError("backbone request budget exhausted")
        if self.max_input_tokens is not None and current.input_tokens + estimated_input_tokens > self.max_input_tokens:
            raise BackboneProviderError("backbone input-token budget exhausted")
        if self.max_output_tokens is not None and current.output_tokens + reserved_output_tokens > self.max_output_tokens:
            raise BackboneProviderError("backbone output-token budget exhausted")
        if current.total_tokens + estimated_input_tokens + reserved_output_tokens > self.max_total_tokens:
            raise BackboneProviderError("backbone total-token budget exhausted")

    def verify_actual(self, current: ResourceUsage, actual: ResourceUsage) -> None:
        if current.request_count + actual.request_count > self.max_requests:
            raise BackboneProviderError("backbone request budget exceeded by response")
        if current.total_tokens + actual.total_tokens > self.max_total_tokens:
            raise BackboneProviderError("backbone total-token budget exceeded by response")
        if self.max_input_tokens is not None and current.input_tokens + actual.input_tokens > self.max_input_tokens:
            raise BackboneProviderError("backbone input-token budget exceeded by response")
        if self.max_output_tokens is not None and current.output_tokens + actual.output_tokens > self.max_output_tokens:
            raise BackboneProviderError("backbone output-token budget exceeded by response")


@dataclass(frozen=True)
class BackboneProviderConfig:
    """Identity and decoding configuration, all included in the config hash."""

    model_id: str
    snapshot: str
    environment: str
    max_output_tokens: int = 256
    temperature: float = 0.0
    timeout_seconds: float = 60.0
    endpoint: str | None = "https://api.openai.com/v1"
    api_key: str | None = None
    max_context_events: int = 32
    snapshot_binding: str = _RESPONSE_FINGERPRINT

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ValueError("model_id must be an exact non-empty string")
        if not isinstance(self.snapshot, str) or not self.snapshot.strip():
            raise ValueError("snapshot must be an exact non-empty string")
        if self.environment not in {_DEVELOPMENT, _PRODUCTION}:
            raise ValueError("environment must be DEVELOPMENT or PRODUCTION")
        if isinstance(self.max_output_tokens, bool) or not isinstance(self.max_output_tokens, int) or self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be a positive integer")
        if not isinstance(self.temperature, (int, float)) or not math.isfinite(float(self.temperature)):
            raise ValueError("temperature must be finite")
        if self.temperature < 0:
            raise ValueError("temperature must be non-negative")
        if not isinstance(self.timeout_seconds, (int, float)) or not math.isfinite(float(self.timeout_seconds)) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        if self.snapshot_binding not in _SNAPSHOT_BINDINGS:
            raise ValueError(
                "snapshot_binding must be response_fingerprint or external_manifest"
            )
        if self.environment == _PRODUCTION:
            parsed = urlparse(str(self.endpoint))
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("PRODUCTION provider requires an HTTP(S) endpoint")
            if not self.api_key and not _is_loopback_endpoint(parsed):
                raise ValueError("PRODUCTION public endpoint requires an api_key")
        if isinstance(self.max_context_events, bool) or not isinstance(self.max_context_events, int) or self.max_context_events < 1:
            raise ValueError("max_context_events must be a positive integer")

    @property
    def config_sha256(self) -> str:
        # Do not include the secret itself in a durable audit record.
        safe = asdict(self)
        safe["api_key"] = "configured" if self.api_key else None
        return canonical_sha256(safe)


@dataclass(frozen=True)
class BackboneCallAudit:
    """Append-only, content-addressed evidence for one accepted prediction."""

    provider_kind: str
    call_index: int
    model_id: str
    snapshot: str
    snapshot_binding: str
    decision_sha256: str
    memory_state_sha256: str
    candidate_skill_revision_ids: tuple[str, ...]
    prompt_sha256: str
    config_sha256: str
    request_sha256: str
    response_sha256: str
    prediction_sha256: str
    usage: ResourceUsage


class OpenAICompatibleTransport(Protocol):
    """Injectable stdlib-compatible HTTP boundary; tests never need a network."""

    def post_json(self, *, url: str, headers: Mapping[str, str], body: Mapping[str, object], timeout_seconds: float) -> Mapping[str, object]: ...


def _is_loopback_endpoint(parsed: object) -> bool:
    """Return true only for local OpenAI-compatible servers such as vLLM."""
    hostname = getattr(parsed, "hostname", None)
    if not isinstance(hostname, str):
        return False
    return hostname.lower() == "localhost" or hostname in {"127.0.0.1", "::1"}


class BackboneProvider(Protocol):
    """Model-neutral prediction contract consumed by ``RuntimePipeline``."""

    @property
    def usage(self) -> ResourceUsage: ...

    @property
    def call_audit(self) -> tuple[BackboneCallAudit, ...]: ...

    def predict(
        self, decision: DecisionView, state: MemoryState, candidates: Sequence[SkillRevision]
    ) -> BackbonePrediction: ...


class _UrllibTransport:
    def post_json(self, *, url: str, headers: Mapping[str, str], body: Mapping[str, object], timeout_seconds: float) -> Mapping[str, object]:
        data = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as error:
            raise BackboneProviderError(f"OpenAI-compatible transport failed: {error}") from error
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BackboneProviderError("OpenAI-compatible transport returned invalid JSON") from error
        if not isinstance(decoded, Mapping):
            raise BackboneProviderError("OpenAI-compatible transport returned a non-object response")
        return decoded


def _estimate_tokens(value: object) -> int:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return max(1, math.ceil(len(encoded) / 4))


def _state_view(state: MemoryState) -> dict[str, object]:
    """A structural serving view, intentionally excluding evaluator namespaces."""
    return {
        "state_root": state.root,
        "source_event_count": len(state.immutable_source_log),
        "audit_event_count": len(state.audit_log),
        "projection_size": len(state.projection_order),
        "projection_index_size": len(state.projection_index),
        "scope_projection_size": len(state.scope_projection),
        "cache_event_ids": list(state.cache_event_ids),
        "supersession_edges": [list(row) for row in state.supersession_edges],
        "quarantine_set": list(state.quarantine_set),
    }


def _typed_skill_content(skill: SkillRevision) -> dict[str, object]:
    """Expose only the revision's typed executable content, never its evidence."""
    return {
        "skill_revision_id": skill.skill_revision_id,
        "skill_id": skill.skill_id,
        "program": dict(skill.program),
        "parameter_schema": dict(skill.parameter_schema),
        "preconditions": [dict(row) for row in skill.preconditions],
        "postconditions": [dict(row) for row in skill.postconditions],
        "success_probe": dict(skill.success_probe),
        "mutation_budget": dict(skill.mutation_budget),
        "rollback_program": dict(skill.rollback_program),
    }


def build_backbone_prompt(
    *, decision: DecisionView, state: MemoryState,
    candidates: Sequence[SkillRevision], max_context_events: int = 32,
) -> dict[str, object]:
    """Build a deterministic JSON prompt from serving-visible, typed inputs."""
    candidate_ids = tuple(skill.skill_revision_id for skill in candidates)
    if not candidate_ids or len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("candidates must be non-empty and have unique revision ids")
    if isinstance(max_context_events, bool) or not isinstance(max_context_events, int) or max_context_events < 1:
        raise ValueError("max_context_events must be a positive integer")
    event_rows = decision.observation.get("event_log", [])
    if not isinstance(event_rows, list):
        raise ValueError("decision event_log must be a list")
    current_state = decision.observation.get("current_state")
    descriptor = (
        audit_structural_telemetry(decision, state)
        if isinstance(current_state, Mapping)
        else None
    )
    affected = () if descriptor is None else descriptor.root.affected_projection_ids
    suspects = () if descriptor is None else descriptor.root.suspect_event_ids
    priority_ids = set(affected) | set(suspects)
    priority = [row for row in event_rows if isinstance(row, Mapping) and row.get("event_id") in priority_ids]
    remaining = [row for row in event_rows if row not in priority]
    event_sample = (priority + remaining[-max_context_events:])[:max_context_events]
    decision_summary = {
        "case_id": decision.case_id,
        "source_dataset_id": decision.source_dataset_id,
        "source_episode_id": decision.source_episode_id,
        "family_id": decision.family_id,
        "lineage_id": decision.lineage_id,
        "event_index": decision.event_index,
        "decision_sha256": decision.content_sha256,
        "observable_telemetry": decision.observation.get("observable_telemetry", {}),
        "structural_syndrome": {
            "classification": "unavailable" if descriptor is None else descriptor.classification,
            "confidence": 0.0 if descriptor is None else descriptor.confidence,
            "signal_ids": [] if descriptor is None else list(descriptor.signal_ids),
            "affected_event_ids": list(affected),
            "suspect_event_ids": list(suspects),
        },
        "event_sample": event_sample,
        "event_sample_truncated": len(event_rows) > len(event_sample),
    }
    return {
        "task": "score frozen typed repair operators for the current memory state",
        "output_contract": {
            "type": "object",
            "required": ["selected_skill_revision_id", "scores"],
            "additionalProperties": False,
            "scores_must_cover_exactly": list(sorted(candidate_ids)),
            "score_range": [-1.0, 1.0],
        },
        "decision_view_summary": decision_summary,
        "memory_state": _state_view(state),
        "candidate_operators": [_typed_skill_content(skill) for skill in sorted(candidates, key=lambda row: row.skill_revision_id)],
    }


def _validate_closed_output(value: object, candidate_ids: Sequence[str]) -> tuple[str, dict[str, float]]:
    if not isinstance(value, Mapping) or set(value) != {"selected_skill_revision_id", "scores"}:
        raise BackboneProviderError("backbone output must be a closed object with selected_skill_revision_id and scores")
    selected = value["selected_skill_revision_id"]
    scores = value["scores"]
    expected = set(candidate_ids)
    if not isinstance(selected, str) or selected not in expected:
        raise BackboneProviderError("backbone output selected_skill_revision_id is not a candidate")
    if not isinstance(scores, Mapping) or set(scores) != expected:
        raise BackboneProviderError("backbone output scores must cover exactly the candidate ids")
    normalized: dict[str, float] = {}
    for skill_id, raw in scores.items():
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise BackboneProviderError("backbone output score must be numeric")
        score = float(raw)
        if not math.isfinite(score) or not -1.0 <= score <= 1.0:
            raise BackboneProviderError("backbone output score must be finite and in [-1, 1]")
        normalized[str(skill_id)] = score
    return selected, normalized


def _usage_from_response(value: Mapping[str, object]) -> ResourceUsage:
    usage = value.get("usage")
    if not isinstance(usage, Mapping):
        raise BackboneProviderError("OpenAI-compatible response must include usage")
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    total = usage.get("total_tokens")
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in (prompt, completion, total)):
        raise BackboneProviderError("OpenAI-compatible usage must contain non-negative integer token counts")
    return ResourceUsage(prompt, completion, total)


class _BaseBackboneProvider:
    provider_kind = "base"

    def __init__(self, config: BackboneProviderConfig, budget: ProviderBudget) -> None:
        self.config = config
        self.budget = budget
        self._usage = ResourceUsage(0, 0, 0, 0)
        self._audits: list[BackboneCallAudit] = []

    @property
    def usage(self) -> ResourceUsage:
        return self._usage

    @property
    def call_audit(self) -> tuple[BackboneCallAudit, ...]:
        return tuple(self._audits)

    def _prepare(self, decision: DecisionView, state: MemoryState, candidates: Sequence[SkillRevision]) -> tuple[dict[str, object], tuple[str, ...], int]:
        prompt = build_backbone_prompt(
            decision=decision,
            state=state,
            candidates=candidates,
            max_context_events=self.config.max_context_events,
        )
        candidate_ids = tuple(skill.skill_revision_id for skill in candidates)
        estimate = _estimate_tokens(prompt)
        self.budget.preflight(self._usage, estimated_input_tokens=estimate, reserved_output_tokens=self.config.max_output_tokens)
        return prompt, candidate_ids, estimate

    def _accept(
        self, *, decision: DecisionView, state: MemoryState, candidate_ids: tuple[str, ...], prompt: Mapping[str, object], request: Mapping[str, object], response: Mapping[str, object], output: object, usage: ResourceUsage,
    ) -> BackbonePrediction:
        if usage.request_count != 1:
            raise BackboneProviderError("a provider response must account for exactly one request")
        if usage.output_tokens > self.config.max_output_tokens:
            raise BackboneProviderError("backbone response exceeded configured max_output_tokens")
        self.budget.verify_actual(self._usage, usage)
        selected, scores = _validate_closed_output(output, candidate_ids)
        prediction = BackbonePrediction.create(
            case_id=decision.case_id,
            event_index=decision.event_index,
            model_id=self.config.model_id,
            candidate_skill_revision_ids=candidate_ids,
            scores=scores,
            selected_skill_revision_id=selected,
            backbone_state_sha256=state.root,
        )
        prediction.verify(candidate_ids, decision)
        self._usage = self._usage.plus(usage)
        self._audits.append(BackboneCallAudit(
            self.provider_kind, len(self._audits), self.config.model_id, self.config.snapshot,
            self.config.snapshot_binding,
            decision.content_sha256, state.root, tuple(sorted(candidate_ids)), canonical_sha256(prompt),
            self.config.config_sha256, canonical_sha256(request), canonical_sha256(response),
            prediction.prediction_sha256, usage,
        ))
        return prediction


class DeterministicDevelopmentProvider(_BaseBackboneProvider):
    """Hash-based, reproducible stand-in that is explicitly not a model result."""

    provider_kind = "deterministic_development_non_model"

    def __init__(self, config: BackboneProviderConfig, budget: ProviderBudget) -> None:
        if config.environment != _DEVELOPMENT:
            raise ValueError("DeterministicDevelopmentProvider is DEVELOPMENT-only and is not a model result")
        super().__init__(config, budget)

    def predict(self, decision: DecisionView, state: MemoryState, candidates: Sequence[SkillRevision]) -> BackbonePrediction:
        prompt, candidate_ids, estimated_input = self._prepare(decision, state, candidates)
        # Stable integers in [-1, 1] avoid any dependence on Python's hash seed.
        scores = {
            skill_id: (int(hashlib.sha256(f"{decision.content_sha256}:{state.root}:{skill_id}".encode("utf-8")).hexdigest()[:8], 16) / 0x7FFFFFFF) - 1.0
            for skill_id in candidate_ids
        }
        selected = min(candidate_ids, key=lambda skill_id: (-scores[skill_id], skill_id))
        output = {"selected_skill_revision_id": selected, "scores": scores}
        usage = ResourceUsage(estimated_input, 0, estimated_input)
        request = {"provider": self.provider_kind, "prompt": prompt}
        response = {"development_non_model": True, "output": output, "usage": asdict(usage)}
        return self._accept(
            decision=decision, state=state, candidate_ids=candidate_ids, prompt=prompt,
            request=request, response=response, output=output, usage=usage,
        )


class OpenAICompatibleBackboneProvider(_BaseBackboneProvider):
    """A strict OpenAI-compatible JSON provider with an injectable transport."""

    provider_kind = "openai_compatible"

    def __init__(self, config: BackboneProviderConfig, budget: ProviderBudget, *, transport: OpenAICompatibleTransport | None = None) -> None:
        if config.environment != _PRODUCTION:
            raise ValueError("OpenAICompatibleBackboneProvider requires PRODUCTION configuration")
        parsed = urlparse(str(config.endpoint))
        if not config.api_key and not _is_loopback_endpoint(parsed):
            raise ValueError("OpenAICompatibleBackboneProvider requires an api_key for public endpoints")
        super().__init__(config, budget)
        self._transport = transport or _UrllibTransport()

    def _endpoint_url(self) -> str:
        endpoint = str(self.config.endpoint).rstrip("/")
        if endpoint.endswith("/chat/completions"):
            return endpoint
        return endpoint + "/chat/completions"

    def predict(self, decision: DecisionView, state: MemoryState, candidates: Sequence[SkillRevision]) -> BackbonePrediction:
        prompt, candidate_ids, _estimated_input = self._prepare(decision, state, candidates)
        request: dict[str, object] = {
            "model": self.config.model_id,
            "temperature": float(self.config.temperature),
            "max_tokens": self.config.max_output_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return only the closed JSON object requested by the user message."},
                {"role": "user", "content": json.dumps(prompt, sort_keys=True, separators=(",", ":"), allow_nan=False)},
            ],
        }
        if self.config.snapshot_binding == _EXTERNAL_MANIFEST and _is_loopback_endpoint(urlparse(str(self.config.endpoint))):
            request["chat_template_kwargs"] = {"enable_thinking": False}
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        response = self._transport.post_json(
            url=self._endpoint_url(),
            headers=headers,
            body=request,
            timeout_seconds=float(self.config.timeout_seconds),
        )
        returned_model = response.get("model")
        if returned_model != self.config.model_id:
            raise BackboneProviderError("OpenAI-compatible response model_id does not exactly match configured model_id")
        if (
            self.config.snapshot_binding == _RESPONSE_FINGERPRINT
            and response.get("system_fingerprint") != self.config.snapshot
        ):
            raise BackboneProviderError(
                "OpenAI-compatible response snapshot does not exactly match configured snapshot"
            )
        choices = response.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], Mapping):
            raise BackboneProviderError("OpenAI-compatible response must contain exactly one choice")
        message = choices[0].get("message")
        if not isinstance(message, Mapping) or set(message) - {"role", "content", "refusal"}:
            raise BackboneProviderError("OpenAI-compatible response message has unsupported fields")
        content = message.get("content")
        if not isinstance(content, str):
            raise BackboneProviderError("OpenAI-compatible response choice must contain JSON string content")
        try:
            output = json.loads(content)
        except json.JSONDecodeError as error:
            raise BackboneProviderError("OpenAI-compatible response content is not JSON") from error
        usage = _usage_from_response(response)
        return self._accept(
            decision=decision, state=state, candidate_ids=candidate_ids, prompt=prompt,
            request=request, response=response, output=output, usage=usage,
        )
