"""Local controlled-track service for namespace-scoped model metering."""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from typing import Mapping
from urllib import error, request


USAGE_SCHEMA = "cmd-metered-model-usage-receipt-v1"
_SCOPE = re.compile(r"cmd-[a-f0-9]{24}\Z")


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_bytes(_json_bytes(value))
    os.replace(temporary, path)


def valid_scope(value: object) -> str:
    if not isinstance(value, str) or _SCOPE.fullmatch(value) is None:
        raise ValueError("scope must match cmd-[a-f0-9]{24}")
    return value


@dataclass(frozen=True)
class ProxyLimits:
    llm_calls: int
    input_tokens: int
    output_tokens: int
    gpu_seconds: int

    def __post_init__(self) -> None:
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in self.__dict__.values()):
            raise ValueError("proxy limits must be non-negative integers")


class UsageReceiptStore:
    def __init__(self, root: Path, limits: ProxyLimits) -> None:
        self.root = root
        self.limits = limits
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()

    def path(self, scope: str) -> Path:
        return self.root / f"{valid_scope(scope)}.json"

    def lock(self, scope: str) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(valid_scope(scope), threading.RLock())

    def ensure(self, scope: str) -> dict[str, object]:
        scope = valid_scope(scope)
        path = self.path(scope)
        with self.lock(scope):
            if not path.exists():
                atomic_json(path, self.zero(scope))
            return self.read(scope)

    @staticmethod
    def zero(scope: str) -> dict[str, object]:
        return {
            "schema_version": USAGE_SCHEMA, "scope": scope, "llm_calls": 0,
            "input_tokens": 0, "output_tokens": 0, "gpu_seconds": 0,
        }

    def read(self, scope: str) -> dict[str, object]:
        value = json.loads(self.path(scope).read_text(encoding="utf-8"))
        fields = {"schema_version", "scope", "llm_calls", "input_tokens", "output_tokens", "gpu_seconds"}
        if not isinstance(value, dict) or set(value) != fields or value["schema_version"] != USAGE_SCHEMA or value["scope"] != scope:
            raise ValueError("invalid usage receipt")
        return value

    def within(self, value: Mapping[str, object]) -> bool:
        return all(int(value[field]) <= getattr(self.limits, field) for field in self.limits.__dict__)

    def add(self, scope: str, *, calls: int, inputs: int, outputs: int, gpu: int) -> dict[str, object]:
        current = self.read(scope)
        updated = dict(current)
        for field, delta in (("llm_calls", calls), ("input_tokens", inputs), ("output_tokens", outputs), ("gpu_seconds", gpu)):
            updated[field] = int(updated[field]) + int(delta)
        atomic_json(self.path(scope), updated)
        return updated


class MeteringProxy:
    def __init__(
        self, *, upstream: str, receipts: UsageReceiptStore,
        timeout_seconds: float = 300.0, disable_thinking: bool = False,
    ) -> None:
        self.upstream = upstream.rstrip("/")
        self.receipts = receipts
        self.timeout_seconds = timeout_seconds
        self.disable_thinking = disable_thinking

    def ensure(self, scope: str) -> dict[str, object]:
        self.receipts.ensure(scope)
        return {"status": "ready", "scope": scope}

    def forward(self, scope: str, suffix: str, body: bytes, headers: Mapping[str, str]) -> tuple[int, bytes, str]:
        scope = valid_scope(scope)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError("proxy request body must be JSON") from exc
        if self.disable_thinking and suffix.rstrip("/").endswith("chat/completions"):
            if not isinstance(payload, dict):
                raise ValueError("chat completion body must be a JSON object")
            template_kwargs = payload.get("chat_template_kwargs", {})
            if not isinstance(template_kwargs, dict):
                raise ValueError("chat_template_kwargs must be a JSON object")
            payload = dict(payload)
            payload["chat_template_kwargs"] = {**template_kwargs, "enable_thinking": False}
            body = _json_bytes(payload)
        max_output = payload.get("max_tokens", payload.get("max_completion_tokens", 0)) if isinstance(payload, dict) else 0
        if isinstance(max_output, bool) or not isinstance(max_output, int) or max_output < 0:
            raise ValueError("max output token reservation is invalid")
        reserved = {
            "llm_calls": 1,
            "input_tokens": max(1, math.ceil(len(body) / 4)),
            "output_tokens": max_output,
            "gpu_seconds": 0,
        }
        with self.receipts.lock(scope):
            self.receipts.ensure(scope)
            current = self.receipts.read(scope)
            remaining_gpu = self.receipts.limits.gpu_seconds - int(current["gpu_seconds"])
            reserved["gpu_seconds"] = 1
            projected = {field: int(current[field]) + reserved[field] for field in reserved}
            if not self.receipts.within(projected):
                return 429, _json_bytes({"error": {"type": "budget_exhausted", "message": "namespace model budget exhausted"}}), "application/json"
            started = time.monotonic()
            forwarded = request.Request(
                self.upstream + "/" + suffix.lstrip("/"), data=body, method="POST",
                headers={"Content-Type": headers.get("Content-Type", "application/json"), "Authorization": headers.get("Authorization", "Bearer EMPTY")},
            )
            try:
                with request.urlopen(forwarded, timeout=min(self.timeout_seconds, float(remaining_gpu))) as response:
                    raw = response.read()
                    status = response.status
                    content_type = response.headers.get("Content-Type", "application/json")
            except error.HTTPError as exc:
                raw, status, content_type = exc.read(), exc.code, exc.headers.get("Content-Type", "application/json")
            except (error.URLError, TimeoutError, OSError):
                elapsed = max(1, math.ceil(time.monotonic() - started))
                self.receipts.add(
                    scope, calls=1, inputs=reserved["input_tokens"],
                    outputs=reserved["output_tokens"], gpu=min(elapsed, remaining_gpu),
                )
                return 502, _json_bytes({"error": {"type": "upstream_unavailable", "message": "metered upstream call failed"}}), "application/json"
            elapsed = max(1, math.ceil(time.monotonic() - started))
            try:
                decoded = json.loads(raw)
                usage = decoded.get("usage", {}) if isinstance(decoded, dict) else {}
                inputs = int(usage.get("prompt_tokens", usage.get("input_tokens", reserved["input_tokens"])))
                outputs = int(usage.get("completion_tokens", usage.get("output_tokens", 0)))
            except (json.JSONDecodeError, TypeError, ValueError):
                inputs, outputs = reserved["input_tokens"], reserved["output_tokens"]
            updated = self.receipts.add(scope, calls=1, inputs=inputs, outputs=outputs, gpu=elapsed)
            if not self.receipts.within(updated):
                return 502, _json_bytes({"error": {"type": "budget_overrun", "message": "upstream usage exceeded reserved budget"}}), "application/json"
            return status, raw, content_type


class ServiceHandler(BaseHTTPRequestHandler):
    server_version = "CMDIndustryService/1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, _json_bytes({"status": "ok"}))
        else:
            self._send(404, _json_bytes({"error": "not_found"}))

    def do_POST(self) -> None:
        service = getattr(self.server, "service")
        try:
            body = self._body()
            if self.path == "/admin/ensure":
                payload = json.loads(body)
                result = service.ensure(valid_scope(payload.get("scope")))
                self._send(200, _json_bytes(result))
                return
            match = re.fullmatch(r"/(cmd-[a-f0-9]{24})/(.+)", self.path)
            if match is None:
                raise ValueError("invalid service path")
            status, response_body, content_type = service.forward(match.group(1), match.group(2), body, self.headers)
            self._send(status, response_body, content_type)
        except Exception as exc:
            self._send(400, _json_bytes({"error": type(exc).__name__, "message": str(exc)}))


def serve(service: object, *, host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), ServiceHandler)
    setattr(server, "service", service)
    try:
        server.serve_forever()
    finally:
        close = getattr(service, "close", None)
        if callable(close):
            close()
