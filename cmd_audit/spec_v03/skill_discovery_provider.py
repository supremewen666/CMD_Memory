"""Strict OpenAI-compatible discovery of typed CMD repair skills.

The model may nominate a catalog ``operator_id`` and a human-readable
``skill_key`` only.  All executable program content is compiled locally from
the runtime operator catalog, so discovery never accepts model-authored write
contracts, preconditions, rollback actions, or success criteria.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import re
from typing import Mapping, Protocol, Sequence
import urllib.error
from urllib.parse import urlparse
import urllib.request

from cmd_audit.repair.ghost_ecology import FailureDeposit, SkillRevision

from .contracts import canonical_sha256
from .repair_stream import OperatorSpec, execute_operator, operator_catalog
from .runtime_bundle import RuntimeBundle, serialize as serialize_runtime_bundle
from .syndrome_runtime import audit_structural_telemetry


_SCHEMA = "cmd-spec-v03-skill-library-v1"
_EXTERNAL_MANIFEST = "external_manifest"
_RESPONSE_FINGERPRINT = "response_fingerprint"
_BINDINGS = frozenset({_EXTERNAL_MANIFEST, _RESPONSE_FINGERPRINT})
_KEY_RE = re.compile(r"[a-z][a-z0-9_-]{0,79}\Z")
_LIBRARY_FIELDS = frozenset({"schema_version", "skills", "library_sha256"})
_SKILL_FIELDS = frozenset({
    "skill_revision_id", "skill_id", "program", "program_sha256",
    "parameter_schema", "preconditions", "postconditions", "success_probe",
    "mutation_budget", "rollback_program", "parent_revision_ids",
    "derivation_kind", "producing_failure_id", "state",
})


class SkillDiscoveryProviderError(RuntimeError):
    """Raised for transport, budget, pin, or closed-output failures."""


@dataclass(frozen=True)
class DiscoveryUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    request_count: int = 1

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "total_tokens", "request_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens + output_tokens")

    def plus(self, other: "DiscoveryUsage") -> "DiscoveryUsage":
        return DiscoveryUsage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.total_tokens + other.total_tokens,
            self.request_count + other.request_count,
        )


@dataclass(frozen=True)
class DiscoveryBudget:
    max_requests: int
    max_total_tokens: int
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        for name in ("max_requests", "max_total_tokens", "max_input_tokens", "max_output_tokens"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or None")

    def preflight(self, current: DiscoveryUsage, *, estimated_input: int, reserved_output: int) -> None:
        if current.request_count + 1 > self.max_requests:
            raise SkillDiscoveryProviderError("skill discovery request budget exhausted")
        if current.total_tokens + estimated_input + reserved_output > self.max_total_tokens:
            raise SkillDiscoveryProviderError("skill discovery total-token budget exhausted")
        if self.max_input_tokens is not None and current.input_tokens + estimated_input > self.max_input_tokens:
            raise SkillDiscoveryProviderError("skill discovery input-token budget exhausted")
        if self.max_output_tokens is not None and current.output_tokens + reserved_output > self.max_output_tokens:
            raise SkillDiscoveryProviderError("skill discovery output-token budget exhausted")

    def verify(self, current: DiscoveryUsage, actual: DiscoveryUsage) -> None:
        if actual.request_count != 1:
            raise SkillDiscoveryProviderError("one response must account for exactly one request")
        if current.request_count + actual.request_count > self.max_requests:
            raise SkillDiscoveryProviderError("skill discovery request budget exceeded by response")
        if current.total_tokens + actual.total_tokens > self.max_total_tokens:
            raise SkillDiscoveryProviderError("skill discovery total-token budget exceeded by response")
        if self.max_input_tokens is not None and current.input_tokens + actual.input_tokens > self.max_input_tokens:
            raise SkillDiscoveryProviderError("skill discovery input-token budget exceeded by response")
        if self.max_output_tokens is not None and current.output_tokens + actual.output_tokens > self.max_output_tokens:
            raise SkillDiscoveryProviderError("skill discovery output-token budget exceeded by response")


def _is_loopback(endpoint: str | None) -> bool:
    host = urlparse(str(endpoint)).hostname
    return isinstance(host, str) and (host.lower() == "localhost" or host in {"127.0.0.1", "::1"})


@dataclass(frozen=True)
class SkillDiscoveryConfig:
    model_id: str
    snapshot: str
    endpoint: str
    max_output_tokens: int = 256
    temperature: float = 0.0
    timeout_seconds: float = 60.0
    api_key: str | None = None
    snapshot_binding: str = _EXTERNAL_MANIFEST

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not self.model_id:
            raise ValueError("model_id must be a non-empty string")
        if not isinstance(self.snapshot, str) or not self.snapshot:
            raise ValueError("snapshot must be a non-empty exact pin")
        parsed = urlparse(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("endpoint must be an HTTP(S) URL")
        if not self.api_key and not _is_loopback(self.endpoint):
            raise ValueError("non-loopback endpoint requires an api_key")
        if self.snapshot_binding not in _BINDINGS:
            raise ValueError("snapshot_binding must be external_manifest or response_fingerprint")
        if isinstance(self.max_output_tokens, bool) or not isinstance(self.max_output_tokens, int) or self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be a positive integer")
        if not isinstance(self.temperature, (int, float)) or not math.isfinite(float(self.temperature)) or self.temperature < 0:
            raise ValueError("temperature must be finite and non-negative")
        if not isinstance(self.timeout_seconds, (int, float)) or not math.isfinite(float(self.timeout_seconds)) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")

    @property
    def config_sha256(self) -> str:
        safe = asdict(self)
        safe["api_key"] = "configured" if self.api_key else None
        return canonical_sha256(safe)


class OpenAICompatibleTransport(Protocol):
    def post_json(self, *, url: str, headers: Mapping[str, str], body: Mapping[str, object], timeout_seconds: float) -> Mapping[str, object]: ...


class _UrllibTransport:
    def post_json(self, *, url: str, headers: Mapping[str, str], body: Mapping[str, object], timeout_seconds: float) -> Mapping[str, object]:
        request = urllib.request.Request(
            url,
            data=json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"),
            headers=dict(headers), method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:4096]
            suffix = f"; response={detail}" if detail else ""
            raise SkillDiscoveryProviderError(
                f"OpenAI-compatible transport failed: HTTP {error.code} {error.reason}{suffix}"
            ) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise SkillDiscoveryProviderError(f"OpenAI-compatible transport failed: {error}") from error
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SkillDiscoveryProviderError("OpenAI-compatible transport returned invalid JSON") from error
        if not isinstance(value, Mapping):
            raise SkillDiscoveryProviderError("OpenAI-compatible transport returned a non-object response")
        return value


@dataclass(frozen=True)
class SkillDiscoveryCallAudit:
    call_index: int
    cache_key: tuple[str, int, str]
    model_id: str
    snapshot: str
    snapshot_binding: str
    config_sha256: str
    runtime_bundle_sha256: str
    failure_sha256: str
    catalog_sha256: str
    prompt_sha256: str
    request_sha256: str
    response_sha256: str
    candidate_revision_ids: tuple[str, ...]
    usage: DiscoveryUsage


def _catalog_row(spec: OperatorSpec) -> dict[str, object]:
    return {
        "operator_id": spec.operator_id,
        "precondition": spec.precondition,
        "read_contract": spec.read_contract,
        "write_contract": spec.write_contract,
        "invariant_contract": spec.invariant_contract,
        "safety_contract": spec.safety_contract,
        "locality_bound": spec.locality_bound,
        "rollback_action": spec.rollback_action,
    }


def _legal_specs(bundle: RuntimeBundle) -> tuple[OperatorSpec, ...]:
    before = bundle.memory_state
    return tuple(
        spec for spec in operator_catalog()
        if spec.operator_id != "noop_abstain"
        and (after := execute_operator(before, spec)) != before
        and after.immutable_source_log == before.immutable_source_log
        and after.audit_log == before.audit_log
    )


def _bounded_event(row: Mapping[str, object], *, max_content_chars: int = 1024) -> dict[str, object]:
    content = row.get("content")
    rendered = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    result = {key: row[key] for key in ("event_id", "timestamp", "actor_scope", "authority") if key in row}
    result.update({
        "content_sha256": canonical_sha256(content),
        "content_chars": len(rendered),
        "content_preview": rendered[:max_content_chars],
        "content_truncated": len(rendered) > max_content_chars,
    })
    provenance = row.get("provenance")
    if provenance is not None:
        result["provenance_sha256"] = canonical_sha256(provenance)
    return result


def _bounded_ids(values: Sequence[str], *, limit: int = 64) -> dict[str, object]:
    rows = tuple(values)
    return {
        "count": len(rows),
        "ids": list(rows[:limit]),
        "ids_sha256": canonical_sha256(rows),
        "truncated": len(rows) > limit,
    }


def build_skill_discovery_prompt(bundle: RuntimeBundle, *, event_index: int, failure: FailureDeposit) -> dict[str, object]:
    """Return the complete serving-visible discovery prompt, deterministically."""
    if event_index < 0 or failure.case_id != bundle.case_id:
        raise ValueError("event_index must be non-negative and failure must bind this runtime bundle")
    descriptor = audit_structural_telemetry(bundle.decision_view, bundle.memory_state)
    priority_ids = set(descriptor.root.affected_projection_ids) | set(descriptor.root.suspect_event_ids)
    raw_events = bundle.decision_view.observation.get("event_log", [])
    events = raw_events if isinstance(raw_events, list) else []
    priority = [row for row in events if isinstance(row, Mapping) and row.get("event_id") in priority_ids]
    remaining = [row for row in events if row not in priority]
    event_sample = [_bounded_event(row) for row in (priority + remaining[-32:])[:32]]
    state = bundle.memory_state
    return {
        "task": "nominate one or more legal typed repair operators for a runtime-visible memory failure",
        "event_index": event_index,
        "runtime_summary": {
            "case_id": bundle.case_id,
            "source_dataset_id": bundle.source_dataset_id,
            "source_episode_id": bundle.source_episode_id,
            "family_id": bundle.family_id,
            "lineage_id": bundle.lineage_id,
            "decision_sha256": bundle.decision_view.content_sha256,
            "state_root": state.root,
            "source_event_count": len(state.immutable_source_log),
            "audit_event_count": len(state.audit_log),
            "projection_size": len(state.projection_order),
            "cache_size": len(state.cache_event_ids),
            "quarantine_size": len(state.quarantine_set),
            "structural_classification": descriptor.classification,
            "signal_ids": list(descriptor.signal_ids),
            "affected_events": _bounded_ids(descriptor.root.affected_projection_ids),
            "suspect_events": _bounded_ids(descriptor.root.suspect_event_ids),
            "event_sample": event_sample,
            "event_sample_truncated": len(events) > len(event_sample),
        },
        "failure_deposit": failure.to_mapping(),
        "legal_operator_catalog": [_catalog_row(spec) for spec in _legal_specs(bundle)],
        "output_contract": {
            "type": "object", "required": ["candidates"], "additionalProperties": False,
            "candidate_fields": ["operator_id", "skill_key"], "candidate_additional_properties": False,
            "skill_key_pattern": "[a-z][a-z0-9_-]{0,79}",
            "skill_key_example": "restore-projection-from-source-log",
            "max_candidates": 8,
        },
    }


def _usage(response: Mapping[str, object]) -> DiscoveryUsage:
    raw = response.get("usage")
    if not isinstance(raw, Mapping):
        raise SkillDiscoveryProviderError("response must include usage")
    values = (raw.get("prompt_tokens"), raw.get("completion_tokens"), raw.get("total_tokens"))
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in values):
        raise SkillDiscoveryProviderError("response usage must contain non-negative integer token counts")
    return DiscoveryUsage(*values)  # type: ignore[arg-type]


def _parse_candidates(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping) or set(value) != {"candidates"} or not isinstance(value["candidates"], list):
        raise SkillDiscoveryProviderError("discovery output must be the closed object {candidates}")
    rows = value["candidates"]
    if not rows or len(rows) > 8:
        raise SkillDiscoveryProviderError("discovery output candidates must contain 1..8 entries")
    legal = {spec.operator_id for spec in operator_catalog()}
    result: list[tuple[str, str]] = []
    raw_rows: set[tuple[str, str]] = set()
    normalized_rows: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"operator_id", "skill_key"}:
            raise SkillDiscoveryProviderError("discovery candidate must be closed operator_id/skill_key")
        operator_id, skill_key = row["operator_id"], row["skill_key"]
        if not isinstance(operator_id, str) or operator_id not in legal:
            raise SkillDiscoveryProviderError("discovery candidate names an unknown legal operator")
        if not isinstance(skill_key, str) or not skill_key.strip() or len(skill_key) > 512:
            raise SkillDiscoveryProviderError("discovery skill_key must be a non-empty string of at most 512 characters")
        raw_pair = (operator_id, skill_key)
        if raw_pair in raw_rows:
            raise SkillDiscoveryProviderError("discovery candidates must be unique")
        raw_rows.add(raw_pair)
        normalized = re.sub(r"[^a-z0-9]+", "-", skill_key.casefold()).strip("-")
        if not normalized or not normalized[0].isalpha():
            normalized = f"skill-{normalized}" if normalized else f"skill-{operator_id}"
        normalized = normalized[:80].rstrip("-")
        pair = (operator_id, normalized)
        if pair in normalized_rows:
            suffix = canonical_sha256({"operator_id": operator_id, "skill_key": skill_key})[:12]
            normalized = f"{normalized[:67].rstrip('-')}-{suffix}"
            pair = (operator_id, normalized)
        if not _KEY_RE.fullmatch(normalized):
            raise SkillDiscoveryProviderError("normalized discovery skill_key violates the local slug contract")
        normalized_rows.add(pair)
        result.append(pair)
    return tuple(result)


def _compile_candidate(operator_id: str, skill_key: str, *, failure_id: str) -> SkillRevision:
    spec = next(item for item in operator_catalog() if item.operator_id == operator_id)
    return SkillRevision.create(
        skill_id=f"catalog:{operator_id}:{skill_key}",
        program={"kind": "cmd-spec-v03-operator", "operator_id": operator_id, "write_contract": spec.write_contract},
        parameter_schema={"type": "object", "additionalProperties": False},
        preconditions=({"kind": "catalog_precondition", "contract": spec.precondition},),
        postconditions=(
            {"kind": "catalog_invariant", "contract": spec.invariant_contract},
            {"kind": "catalog_safety", "contract": spec.safety_contract},
        ),
        success_probe={"probe_id": f"catalog:{operator_id}:invariant-and-safety"},
        mutation_budget={"locality_bound": spec.locality_bound, "write_contract": spec.write_contract},
        rollback_program={"action": spec.rollback_action},
        producing_failure_id=failure_id,
        derivation_kind="discovery", state="stable",
    )


class OpenAICompatibleSkillDiscoveryProvider:
    """Cached, pinned OpenAI-compatible provider for Stage 6 discovery."""

    def __init__(self, config: SkillDiscoveryConfig, budget: DiscoveryBudget, *, transport: OpenAICompatibleTransport | None = None) -> None:
        self.config = config
        self.budget = budget
        self._transport = transport or _UrllibTransport()
        self._usage = DiscoveryUsage(0, 0, 0, 0)
        self._audits: list[SkillDiscoveryCallAudit] = []
        self._cache: dict[tuple[str, int, str], tuple[SkillRevision, ...]] = {}

    @property
    def usage(self) -> DiscoveryUsage:
        return self._usage

    @property
    def call_audit(self) -> tuple[SkillDiscoveryCallAudit, ...]:
        return tuple(self._audits)

    def _url(self) -> str:
        endpoint = self.config.endpoint.rstrip("/")
        return endpoint if endpoint.endswith("/chat/completions") else endpoint + "/chat/completions"

    def candidates(self, bundle: RuntimeBundle, *, event_index: int, failure: FailureDeposit) -> tuple[SkillRevision, ...]:
        key = (bundle.case_id, event_index, failure.failure_id)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        prompt = build_skill_discovery_prompt(bundle, event_index=event_index, failure=failure)
        estimated = max(
            1,
            math.ceil(
                len(json.dumps(prompt, sort_keys=True, separators=(",", ":")).encode("utf-8")) / 4
            ),
        )
        self.budget.preflight(self._usage, estimated_input=estimated, reserved_output=self.config.max_output_tokens)
        request: dict[str, object] = {
            "model": self.config.model_id, "temperature": float(self.config.temperature),
            "max_tokens": self.config.max_output_tokens, "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return only the closed JSON object requested by the user message."},
                {"role": "user", "content": json.dumps(prompt, sort_keys=True, separators=(",", ":"), allow_nan=False)},
            ],
        }
        if self.config.snapshot_binding == _EXTERNAL_MANIFEST and _is_loopback(self.config.endpoint):
            request["chat_template_kwargs"] = {"enable_thinking": False}
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        response = self._transport.post_json(url=self._url(), headers=headers, body=request, timeout_seconds=float(self.config.timeout_seconds))
        if response.get("model") != self.config.model_id:
            raise SkillDiscoveryProviderError("response model_id does not exactly match configured model_id")
        if self.config.snapshot_binding == _RESPONSE_FINGERPRINT and response.get("system_fingerprint") != self.config.snapshot:
            raise SkillDiscoveryProviderError("response snapshot does not exactly match configured snapshot")
        choices = response.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], Mapping):
            raise SkillDiscoveryProviderError("response must contain exactly one choice")
        message = choices[0].get("message")
        if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
            raise SkillDiscoveryProviderError("response choice must contain JSON string content")
        try:
            nominations = _parse_candidates(json.loads(str(message["content"])))
        except json.JSONDecodeError as error:
            raise SkillDiscoveryProviderError("response content is not JSON") from error
        legal_operator_ids = {spec.operator_id for spec in _legal_specs(bundle)}
        if any(operator_id not in legal_operator_ids for operator_id, _skill_key in nominations):
            raise SkillDiscoveryProviderError("discovery candidate is outside the current typed legal mask")
        usage = _usage(response)
        if usage.output_tokens > self.config.max_output_tokens:
            raise SkillDiscoveryProviderError("response exceeded configured output token limit")
        self.budget.verify(self._usage, usage)
        revisions = tuple(_compile_candidate(operator_id, skill_key, failure_id=failure.failure_id) for operator_id, skill_key in nominations)
        if len({skill.skill_revision_id for skill in revisions}) != len(revisions):
            raise SkillDiscoveryProviderError("compiled discovery revisions must be unique")
        self._usage = self._usage.plus(usage)
        runtime_hash = canonical_sha256(serialize_runtime_bundle(
            case_id=bundle.case_id, source_dataset_id=bundle.source_dataset_id,
            source_episode_id=bundle.source_episode_id, family_id=bundle.family_id,
            lineage_id=bundle.lineage_id, source_event_ids=bundle.source_event_ids,
            decision_view=bundle.decision_view, memory_state=bundle.memory_state,
        ))
        self._audits.append(SkillDiscoveryCallAudit(
            len(self._audits), key, self.config.model_id, self.config.snapshot,
            self.config.snapshot_binding, self.config.config_sha256, runtime_hash,
            canonical_sha256(failure.to_mapping()), canonical_sha256([_catalog_row(spec) for spec in operator_catalog()]),
            canonical_sha256(prompt), canonical_sha256(request), canonical_sha256(response),
            tuple(skill.skill_revision_id for skill in revisions), usage,
        ))
        self._cache[key] = revisions
        return revisions

    @property
    def discovered_skills(self) -> tuple[SkillRevision, ...]:
        """All unique cached revisions, suitable for a frozen library sidecar."""
        unique = {
            skill.skill_revision_id: skill
            for revisions in self._cache.values()
            for skill in revisions
        }
        return tuple(unique[key] for key in sorted(unique))


@dataclass(frozen=True)
class SkillLibrary:
    skills: tuple[SkillRevision, ...]
    library_sha256: str


def serialize_skill_library(skills: Sequence[SkillRevision]) -> dict[str, object]:
    """Encode a closed library and reject duplicate revision identities."""
    ordered = tuple(sorted(skills, key=lambda skill: skill.skill_revision_id))
    if len({skill.skill_revision_id for skill in ordered}) != len(ordered):
        raise ValueError("skill library rejects duplicate skill_revision_id")
    body = {"schema_version": _SCHEMA, "skills": [skill.to_mapping() for skill in ordered]}
    value = {**body, "library_sha256": canonical_sha256(body)}
    load_skill_library_mapping(value)
    return value


def _decode_skill(value: object) -> SkillRevision:
    if not isinstance(value, Mapping) or set(value) != _SKILL_FIELDS:
        raise ValueError("skill library entry must use the closed SkillRevision schema")
    if value["program_sha256"] != canonical_sha256(value["program"]):
        raise ValueError("skill library program_sha256 mismatch")
    fields = {key: value[key] for key in _SKILL_FIELDS if key != "program_sha256"}
    required_lists = ("preconditions", "postconditions", "parent_revision_ids")
    if any(not isinstance(fields[name], list) for name in required_lists):
        raise ValueError("skill library sequence fields must be JSON lists")
    skill = SkillRevision(
        skill_revision_id=fields["skill_revision_id"], skill_id=fields["skill_id"], program=dict(fields["program"]),
        parameter_schema=dict(fields["parameter_schema"]), preconditions=tuple(dict(row) for row in fields["preconditions"]),
        postconditions=tuple(dict(row) for row in fields["postconditions"]), success_probe=dict(fields["success_probe"]),
        mutation_budget=dict(fields["mutation_budget"]), rollback_program=dict(fields["rollback_program"]),
        parent_revision_ids=tuple(fields["parent_revision_ids"]), derivation_kind=fields["derivation_kind"],
        producing_failure_id=fields["producing_failure_id"], state=fields["state"],
    )
    if skill.to_mapping() != dict(value):
        raise ValueError("skill library entry is not canonical SkillRevision content")
    return skill


def load_skill_library_mapping(value: Mapping[str, object]) -> SkillLibrary:
    if set(value) != _LIBRARY_FIELDS or value.get("schema_version") != _SCHEMA or not isinstance(value.get("skills"), list):
        raise ValueError("skill library must use the closed schema")
    body = {"schema_version": value["schema_version"], "skills": value["skills"]}
    if value.get("library_sha256") != canonical_sha256(body):
        raise ValueError("skill library sha256 mismatch")
    skills = tuple(_decode_skill(row) for row in value["skills"])
    if len({skill.skill_revision_id for skill in skills}) != len(skills):
        raise ValueError("skill library rejects duplicate skill_revision_id")
    if tuple(sorted(skill.skill_revision_id for skill in skills)) != tuple(skill.skill_revision_id for skill in skills):
        raise ValueError("skill library entries must be sorted by skill_revision_id")
    return SkillLibrary(skills, str(value["library_sha256"]))


def load_skill_library(path: str | Path) -> SkillLibrary:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("skill library file must contain a JSON object")
    return load_skill_library_mapping(value)
