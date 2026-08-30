"""Shared closed-JSON plumbing for controlled industry-system wrappers.

This module intentionally uses only the Python standard library so each wrapper
can run inside the official system's independently pinned virtual environment.
The official memory implementation remains behind the wrapper; only public
runtime state, retrieved memories, the legal operator mask, and measured usage
cross the process boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Callable, Mapping, Sequence
from urllib import error, parse, request as urllib_request


REQUEST_SCHEMA = "cmd-spec-v03-industry-adapter-request-v1"
RESPONSE_SCHEMA = "cmd-spec-v03-industry-adapter-response-v1"
PROTOCOL_SCHEMA = "cmd-controlled-memory-protocol-v1"
USAGE_RECEIPT_SCHEMA = "cmd-metered-model-usage-receipt-v1"
CONTROLLED_TRACKS = frozenset({"controlled_a1", "controlled_a2"})
SYSTEM_IDS = frozenset({"lightmem", "lycheemem", "mem0"})


class WrapperError(RuntimeError):
    """A closed, user-safe wrapper failure."""

    reason = "wrapper_failed"


class UnsupportedRuntime(WrapperError):
    reason = "official_runtime_unavailable"


class NativeResponseUnavailable(UnsupportedRuntime):
    reason = "native_response_unavailable"


class UnmeteredBackend(UnsupportedRuntime):
    reason = "backend_usage_unmetered"


class BudgetExhausted(WrapperError):
    reason = "budget_exhausted"


class ProtocolError(WrapperError):
    reason = "wrapper_protocol_error"


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProtocolError(f"{field} must be a positive integer")
    return value


def _positive_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value <= 0:
        raise ProtocolError(f"{field} must be a positive number")
    return float(value)


def _nonempty_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"{field} must be a non-empty string")
    return value


@dataclass(frozen=True)
class Budget:
    llm_calls: int
    input_tokens: int
    output_tokens: int
    wall_clock_seconds: float
    gpu_seconds: int

    @classmethod
    def from_mapping(cls, value: object) -> "Budget":
        fields = {"llm_calls", "input_tokens", "output_tokens", "wall_clock_seconds", "gpu_seconds"}
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ProtocolError("budget must use the closed resource schema")
        numbers = tuple(value[name] for name in fields)
        if any(
            isinstance(item, bool) or not isinstance(item, (int, float))
            or not math.isfinite(float(item)) or item < 0
            for item in numbers
        ):
            raise ProtocolError("budget values must be non-negative numbers")
        if any(name != "wall_clock_seconds" and not float(value[name]).is_integer() for name in fields):
            raise ProtocolError("resource counters other than wall time must be integral")
        return cls(
            int(value["llm_calls"]), int(value["input_tokens"]), int(value["output_tokens"]),
            float(value["wall_clock_seconds"]), int(value["gpu_seconds"]),
        )


@dataclass
class UsageLedger:
    budget: Budget
    started_at: float
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    gpu_seconds: int = 0

    @classmethod
    def start(cls, budget: Budget) -> "UsageLedger":
        return cls(budget=budget, started_at=time.monotonic())

    @property
    def elapsed(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)

    @property
    def remaining_wall_seconds(self) -> float:
        return max(0.0, self.budget.wall_clock_seconds - self.elapsed)

    def check_wall(self) -> None:
        if self.remaining_wall_seconds <= 0:
            raise BudgetExhausted("wall-clock budget exhausted")

    def preflight_llm(self, *, estimated_input_tokens: int, reserved_output_tokens: int) -> None:
        self.check_wall()
        if self.llm_calls + 1 > self.budget.llm_calls:
            raise BudgetExhausted("LLM call budget exhausted")
        if self.input_tokens + estimated_input_tokens > self.budget.input_tokens:
            raise BudgetExhausted("input-token budget exhausted")
        if self.output_tokens + reserved_output_tokens > self.budget.output_tokens:
            raise BudgetExhausted("output-token budget exhausted")

    def record_llm(self, *, input_tokens: int, output_tokens: int, gpu_seconds: int = 0) -> None:
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (input_tokens, output_tokens, gpu_seconds)):
            raise ProtocolError("model usage must contain non-negative integer counters")
        self.record_batch(
            llm_calls=1, input_tokens=input_tokens,
            output_tokens=output_tokens, gpu_seconds=gpu_seconds,
        )

    def record_batch(
        self, *, llm_calls: int, input_tokens: int, output_tokens: int, gpu_seconds: int = 0,
    ) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (llm_calls, input_tokens, output_tokens, gpu_seconds)
        ):
            raise ProtocolError("model usage must contain non-negative integer counters")
        projected = (
            self.llm_calls + llm_calls,
            self.input_tokens + input_tokens,
            self.output_tokens + output_tokens,
            self.gpu_seconds + gpu_seconds,
        )
        if (
            projected[0] > self.budget.llm_calls
            or projected[1] > self.budget.input_tokens
            or projected[2] > self.budget.output_tokens
            or projected[3] > self.budget.gpu_seconds
        ):
            # The parent response contract cannot carry counters above the
            # request budget. Saturate a failed result at that contract's cap.
            self.llm_calls = min(projected[0], self.budget.llm_calls)
            self.input_tokens = min(projected[1], self.budget.input_tokens)
            self.output_tokens = min(projected[2], self.budget.output_tokens)
            self.gpu_seconds = min(projected[3], self.budget.gpu_seconds)
            raise BudgetExhausted("reported model usage exceeded the unified budget")
        self.llm_calls, self.input_tokens, self.output_tokens, self.gpu_seconds = projected

    def mapping(self, *, wall_clock_seconds: float | None = None) -> dict[str, object]:
        return {
            "llm_calls": self.llm_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "wall_clock_seconds": self.elapsed if wall_clock_seconds is None else wall_clock_seconds,
            "gpu_seconds": self.gpu_seconds,
        }


@dataclass(frozen=True)
class AdapterRequestView:
    run_id: str
    system_id: str
    track: str
    score_namespace: str
    decision: Mapping[str, object]
    legal_operator_ids: tuple[str, ...]
    budget: Budget

    @classmethod
    def parse(cls, value: object, *, expected_system_id: str) -> "AdapterRequestView":
        fields = {
            "schema_version", "run_id", "system_id", "track", "score_namespace",
            "decision", "legal_operator_ids", "budget",
        }
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ProtocolError("adapter request must use the closed schema")
        if value["schema_version"] != REQUEST_SCHEMA:
            raise ProtocolError("unsupported adapter request schema")
        system_id = _nonempty_text(value["system_id"], "system_id")
        if system_id != expected_system_id or system_id not in SYSTEM_IDS:
            raise ProtocolError("wrapper system_id mismatch")
        track = _nonempty_text(value["track"], "track")
        namespace = _nonempty_text(value["score_namespace"], "score_namespace")
        if track not in CONTROLLED_TRACKS | {"native"}:
            raise ProtocolError("adapter request track is unsupported")
        if track in CONTROLLED_TRACKS and namespace != "controlled":
            raise ProtocolError("controlled request has the wrong score namespace")
        if track == "native" and namespace != "native":
            raise ProtocolError("native request has the wrong score namespace")
        decision = value["decision"]
        legal = value["legal_operator_ids"]
        if not isinstance(decision, Mapping) or not isinstance(legal, list):
            raise ProtocolError("decision and legal_operator_ids have invalid types")
        legal_ids = tuple(_nonempty_text(item, "legal_operator_ids[]") for item in legal)
        if not legal_ids or len(set(legal_ids)) != len(legal_ids):
            raise ProtocolError("legal_operator_ids must be non-empty and unique")
        return cls(
            _nonempty_text(value["run_id"], "run_id"), system_id, track, namespace,
            decision, legal_ids, Budget.from_mapping(value["budget"]),
        )


@dataclass(frozen=True)
class ProtocolConfig:
    raw: Mapping[str, object]
    retrieval_top_k: int
    head: Mapping[str, object]
    system: Mapping[str, object]
    protocol_sha256: str

    @classmethod
    def load(cls, path: str | Path, *, system_id: str) -> "ProtocolConfig":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProtocolError("protocol config is unreadable") from exc
        if not isinstance(raw, Mapping) or set(raw) != {"schema_version", "retrieval_top_k", "head", "systems"}:
            raise ProtocolError("protocol config must use the closed schema")
        if raw["schema_version"] != PROTOCOL_SCHEMA:
            raise ProtocolError("unsupported controlled protocol schema")
        head = raw["head"]
        systems = raw["systems"]
        head_fields = {
            "endpoint", "model_id", "model_snapshot", "api_key_env", "max_output_tokens",
            "timeout_seconds", "temperature", "max_memory_chars",
        }
        if not isinstance(head, Mapping) or set(head) != head_fields:
            raise ProtocolError("shared head config must use the closed schema")
        if not isinstance(systems, Mapping) or set(systems) != SYSTEM_IDS or not isinstance(systems.get(system_id), Mapping):
            raise ProtocolError("systems config must contain exactly the three official systems")
        _nonempty_text(head["endpoint"], "head.endpoint")
        _nonempty_text(head["model_id"], "head.model_id")
        _nonempty_text(head["model_snapshot"], "head.model_snapshot")
        _nonempty_text(head["api_key_env"], "head.api_key_env")
        _positive_int(head["max_output_tokens"], "head.max_output_tokens")
        _positive_number(head["timeout_seconds"], "head.timeout_seconds")
        _positive_int(head["max_memory_chars"], "head.max_memory_chars")
        if isinstance(head["temperature"], bool) or not isinstance(head["temperature"], (int, float)) or float(head["temperature"]) != 0.0:
            raise ProtocolError("controlled shared-head temperature must be exactly zero")
        return cls(
            raw=raw, retrieval_top_k=_positive_int(raw["retrieval_top_k"], "retrieval_top_k"),
            head=head, system=systems[system_id], protocol_sha256=canonical_sha256(raw),
        )


@dataclass(frozen=True)
class UsageSnapshot:
    llm_calls: int
    input_tokens: int
    output_tokens: int
    gpu_seconds: int

    def delta(self, before: "UsageSnapshot") -> "UsageSnapshot":
        values = tuple(getattr(self, field) - getattr(before, field) for field in (
            "llm_calls", "input_tokens", "output_tokens", "gpu_seconds",
        ))
        if any(value < 0 for value in values):
            raise ProtocolError("backend usage receipt counters moved backwards")
        return UsageSnapshot(*values)


class BackendUsageMeter:
    """Read cumulative counters from an enforcing model proxy receipt.

    Official SDKs often consume provider usage internally. Confirmatory runs
    therefore route their model traffic through a budget-enforcing proxy that
    atomically writes this cumulative receipt. Development smoke runs may opt
    into an explicitly labelled unmetered mode, which is never claim-eligible.
    """

    def __init__(self, config: object, *, namespace: str) -> None:
        if not isinstance(config, Mapping) or set(config) != {"mode", "receipt_path"}:
            raise ProtocolError("backend_usage must use the closed schema")
        mode = config["mode"]
        if mode not in {"enforcing_proxy_receipt", "development_unmetered"}:
            raise ProtocolError("backend_usage mode is unsupported")
        receipt_path = config["receipt_path"]
        if mode == "enforcing_proxy_receipt" and (not isinstance(receipt_path, str) or not receipt_path):
            raise ProtocolError("enforcing backend usage requires a receipt_path")
        if mode == "development_unmetered" and receipt_path is not None:
            raise ProtocolError("development_unmetered backend usage must set receipt_path to null")
        self.mode = str(mode)
        self.namespace = namespace
        self.path = None if receipt_path is None else Path(str(receipt_path).replace("{namespace}", namespace))

    @property
    def claim_eligible(self) -> bool:
        return self.mode == "enforcing_proxy_receipt"

    def snapshot(self) -> UsageSnapshot | None:
        if self.path is None:
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProtocolError("backend usage receipt is unavailable") from exc
        fields = {
            "schema_version", "scope", "llm_calls", "input_tokens", "output_tokens", "gpu_seconds",
        }
        if not isinstance(value, Mapping) or set(value) != fields or value["schema_version"] != USAGE_RECEIPT_SCHEMA:
            raise ProtocolError("backend usage receipt must use the closed schema")
        if value["scope"] != self.namespace:
            raise ProtocolError("backend usage receipt scope mismatch")
        counters = tuple(value[field] for field in ("llm_calls", "input_tokens", "output_tokens", "gpu_seconds"))
        if any(isinstance(counter, bool) or not isinstance(counter, int) or counter < 0 for counter in counters):
            raise ProtocolError("backend usage receipt counters are invalid")
        return UsageSnapshot(*counters)

    def settle(self, before: UsageSnapshot | None, ledger: UsageLedger) -> None:
        after = self.snapshot()
        if before is None and after is None:
            return
        if before is None or after is None:
            raise ProtocolError("backend usage receipt disappeared during execution")
        delta = after.delta(before)
        ledger.record_batch(
            llm_calls=delta.llm_calls, input_tokens=delta.input_tokens,
            output_tokens=delta.output_tokens, gpu_seconds=delta.gpu_seconds,
        )


PostJson = Callable[[str, Mapping[str, str], Mapping[str, object], float], Mapping[str, object]]


def post_json(url: str, headers: Mapping[str, str], body: Mapping[str, object], timeout_seconds: float) -> Mapping[str, object]:
    payload = _canonical_json(body).encode("utf-8")
    req = urllib_request.Request(url, data=payload, headers=dict(headers), method="POST")
    try:
        with urllib_request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
        raise WrapperError("official API request failed") from exc
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError("official API returned non-JSON output") from exc
    if not isinstance(decoded, Mapping):
        raise ProtocolError("official API response must be a JSON object")
    return decoded


def _endpoint(base: str) -> str:
    clean = base.rstrip("/")
    return clean if clean.endswith("/chat/completions") else clean + "/chat/completions"


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text.encode("utf-8")) / 4))


def namespace_for(request: AdapterRequestView) -> str:
    case_id = request.decision.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise ProtocolError("decision.case_id is required")
    return "cmd-" + canonical_sha256({
        "run_id": request.run_id, "system_id": request.system_id, "case_id": case_id,
    })[:24]


def public_events(request: AdapterRequestView) -> tuple[Mapping[str, object], ...]:
    observation = request.decision.get("observation")
    events = observation.get("event_log") if isinstance(observation, Mapping) else None
    if not isinstance(events, list) or any(not isinstance(event, Mapping) for event in events):
        raise ProtocolError("decision.observation.event_log must be a list of objects")
    return tuple(events)  # type: ignore[return-value]


def event_text(event: Mapping[str, object]) -> str:
    content = event.get("content")
    if isinstance(content, str):
        rendered = content
    else:
        rendered = _canonical_json(content)
    envelope = {
        "event_id": event.get("event_id"), "timestamp": event.get("timestamp"),
        "actor_scope": event.get("actor_scope"), "authority": event.get("authority"),
        "content": rendered,
    }
    return _canonical_json(envelope)


def retrieval_query(request: AdapterRequestView) -> str:
    observation = request.decision.get("observation")
    if not isinstance(observation, Mapping):
        raise ProtocolError("decision observation is missing")
    for field in ("query", "incident_query", "current_query"):
        candidate = observation.get(field)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    events = public_events(request)
    visible = {
        "predicted_syndrome": observation.get("predicted_syndrome"),
        "observable_telemetry": observation.get("observable_telemetry"),
        "latest_event": None if not events else json.loads(event_text(events[-1])),
    }
    return "Retrieve memories relevant to diagnosing this visible memory incident: " + _canonical_json(visible)


def expand_namespace(value: object, namespace: str) -> object:
    if isinstance(value, Mapping):
        return {str(key): expand_namespace(nested, namespace) for key, nested in value.items()}
    if isinstance(value, list):
        return [expand_namespace(nested, namespace) for nested in value]
    if isinstance(value, str):
        return value.replace("{namespace}", namespace)
    return value


def json_safe(value: object) -> object:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False, default=str))
    except (TypeError, ValueError) as exc:
        raise ProtocolError("official system returned a non-serializable retrieval result") from exc


def _incident_view(request: AdapterRequestView) -> Mapping[str, object]:
    observation = request.decision.get("observation")
    if not isinstance(observation, Mapping):
        raise ProtocolError("decision observation is missing")
    events = public_events(request)
    return {
        "case_id": request.decision.get("case_id"),
        "family_id": request.decision.get("family_id"),
        "event_index": request.decision.get("event_index"),
        "predicted_syndrome": observation.get("predicted_syndrome"),
        "observable_telemetry": observation.get("observable_telemetry"),
        "current_state": observation.get("current_state"),
        "latest_event": None if not events else json.loads(event_text(events[-1])),
    }


def select_with_shared_head(
    request: AdapterRequestView,
    protocol: ProtocolConfig,
    retrieved: object,
    ledger: UsageLedger,
    *,
    transport: PostJson = post_json,
) -> tuple[str | None, str | None]:
    if request.legal_operator_ids == ("noop_abstain",):
        return None, "no_legal_operator"
    max_memory_chars = int(protocol.head["max_memory_chars"])
    safe_retrieved = json_safe(retrieved)
    serialized_memories = _canonical_json(safe_retrieved)
    memories_for_prompt: object = (
        safe_retrieved if len(serialized_memories) <= max_memory_chars
        else serialized_memories[:max_memory_chars]
    )
    prompt = {
        "task": "Select exactly one legal memory-repair operator, or abstain when evidence is insufficient.",
        "incident": _incident_view(request),
        "retrieval_query": retrieval_query(request),
        "retrieved_memories": memories_for_prompt,
        "legal_operator_ids": list(request.legal_operator_ids),
        "rules": [
            "Use only serving-visible incident fields and retrieved memories.",
            "Never infer or request evaluator truth.",
            "selected_operator_id must be one listed legal ID, or null to abstain.",
        ],
    }
    prompt_text = _canonical_json(prompt)
    max_output = int(protocol.head["max_output_tokens"])
    ledger.preflight_llm(estimated_input_tokens=estimate_tokens(prompt_text), reserved_output_tokens=max_output)
    output_schema = {
        "type": "object",
        "properties": {
            "selected_operator_id": {"anyOf": [
                {"type": "string", "enum": list(request.legal_operator_ids)}, {"type": "null"},
            ]},
            "abstain_reason": {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]},
        },
        "required": ["selected_operator_id", "abstain_reason"],
        "additionalProperties": False,
    }
    body: dict[str, object] = {
        "model": protocol.head["model_id"], "temperature": 0.0, "max_tokens": max_output,
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "cmd_shared_repair_action", "strict": True, "schema": output_schema,
        }},
        "messages": [
            {"role": "system", "content": "Return only the requested closed JSON object."},
            {"role": "user", "content": prompt_text},
        ],
    }
    parsed_endpoint = parse.urlparse(str(protocol.head["endpoint"]))
    if parsed_endpoint.hostname in {"127.0.0.1", "localhost", "::1"}:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    headers = {"Content-Type": "application/json"}
    key = os.environ.get(str(protocol.head["api_key_env"]))
    if key:
        headers["Authorization"] = f"Bearer {key}"
    response = transport(
        _endpoint(str(protocol.head["endpoint"])), headers, body,
        min(float(protocol.head["timeout_seconds"]), ledger.remaining_wall_seconds),
    )
    if response.get("model") != protocol.head["model_id"]:
        raise ProtocolError("shared head returned a different model_id")
    usage = response.get("usage")
    if not isinstance(usage, Mapping):
        raise ProtocolError("shared head response omitted token usage")
    prompt_tokens, completion_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
    gpu_seconds = usage.get("gpu_seconds", 0)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (prompt_tokens, completion_tokens, gpu_seconds)):
        raise ProtocolError("shared head usage counters are invalid")
    total_tokens = usage.get("total_tokens")
    if total_tokens is not None and (
        isinstance(total_tokens, bool) or not isinstance(total_tokens, int)
        or total_tokens != prompt_tokens + completion_tokens
    ):
        raise ProtocolError("shared head total_tokens is inconsistent")
    ledger.record_llm(input_tokens=prompt_tokens, output_tokens=completion_tokens, gpu_seconds=gpu_seconds)
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], Mapping):
        raise ProtocolError("shared head response must contain exactly one choice")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(content, str):
        raise ProtocolError("shared head response content is missing")
    try:
        selected = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ProtocolError("shared head returned invalid JSON") from exc
    if not isinstance(selected, Mapping) or set(selected) != {"selected_operator_id", "abstain_reason"}:
        raise ProtocolError("shared head returned an open response schema")
    operator, reason = selected["selected_operator_id"], selected["abstain_reason"]
    if operator is not None and operator not in request.legal_operator_ids:
        raise ProtocolError("shared head selected an illegal operator")
    if operator is None and (not isinstance(reason, str) or not reason):
        raise ProtocolError("shared head abstention requires a reason")
    if operator is not None and reason is not None:
        raise ProtocolError("selected operator cannot also contain an abstain reason")
    return operator, reason


def response_mapping(
    *,
    status: str,
    operator: str | None,
    reason: str | None,
    ledger: UsageLedger,
    revision: str,
) -> dict[str, object]:
    elapsed = ledger.elapsed
    if elapsed > ledger.budget.wall_clock_seconds:
        status, operator, reason = "FAILED", None, BudgetExhausted.reason
    # Overruns use a saturated wall counter because the parent contract rejects
    # values above the request budget; the terminal reason records the overrun.
    represented_elapsed = min(elapsed, ledger.budget.wall_clock_seconds)
    return {
        "schema_version": RESPONSE_SCHEMA, "status": status,
        "selected_operator_id": operator, "abstain_reason": reason,
        "usage": ledger.mapping(wall_clock_seconds=represented_elapsed), "adapter_revision": revision,
    }


def wrapper_revision(system_id: str, protocol: ProtocolConfig) -> str:
    usage = protocol.system.get("backend_usage")
    mode = usage.get("mode") if isinstance(usage, Mapping) else "invalid-metering"
    return f"{system_id}:controlled-wrapper-v1:{mode}:protocol-{protocol.protocol_sha256[:16]}:model-{canonical_sha256(protocol.head['model_snapshot'])[:12]}"


def read_stdin_request(*, expected_system_id: str) -> AdapterRequestView:
    try:
        raw = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as exc:
        raise ProtocolError("stdin must contain one JSON request") from exc
    return AdapterRequestView.parse(raw, expected_system_id=expected_system_id)


def emit(value: Mapping[str, object]) -> None:
    sys.stdout.write(_canonical_json(value) + "\n")
    sys.stdout.flush()


def cli_protocol_path(argv: Sequence[str]) -> Path:
    if len(argv) != 2 or argv[0] != "--protocol-config":
        raise ProtocolError("wrapper requires --protocol-config PATH")
    return Path(argv[1])
