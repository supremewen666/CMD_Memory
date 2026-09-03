#!/usr/bin/env python3
"""Controlled-track adapter for a pinned official LycheeMemory local service."""

from __future__ import annotations

import os
import json
from pathlib import Path
import sys
from typing import Mapping

try:
    from .industry_common import (
        AdapterRequestView, BackendUsageMeter, NativeResponseUnavailable, PostJson,
        ProtocolConfig, ProtocolError, UnmeteredBackend, UnsupportedRuntime, UsageLedger,
        WrapperError, cli_protocol_path, emit, event_text, json_safe,
        namespace_for, post_json, public_events, record_wrapper_failure, response_mapping, retrieval_query,
        select_with_shared_head, wrapper_revision,
    )
except ImportError:  # Direct execution inside the pinned official virtualenv.
    from industry_common import (
        AdapterRequestView, BackendUsageMeter, NativeResponseUnavailable, PostJson,
        ProtocolConfig, ProtocolError, UnmeteredBackend, UnsupportedRuntime, UsageLedger,
        WrapperError, cli_protocol_path, emit, event_text, json_safe,
        namespace_for, post_json, public_events, record_wrapper_failure, response_mapping, retrieval_query,
        select_with_shared_head, wrapper_revision,
    )


def retrieve_lycheemem(
    request: AdapterRequestView,
    protocol: ProtocolConfig,
    *,
    transport: PostJson = post_json,
    ledger: UsageLedger | None = None,
) -> object:
    config = protocol.system
    fields = {
        "base_url", "manager_url", "instance_receipt_path", "expected_commit", "api_key_env", "timeout_seconds",
        "consolidate", "include_graph", "include_skills", "backend_usage",
    }
    if set(config) != fields:
        raise ProtocolError("LycheeMemory wrapper config must use the closed schema")
    if not all(isinstance(config[name], bool) for name in ("consolidate", "include_graph", "include_skills")):
        raise ProtocolError("LycheeMemory boolean settings are invalid")
    namespace = namespace_for(request)
    base_url_template = str(config["base_url"])
    receipt_template = str(config["instance_receipt_path"])
    if "{namespace}" not in base_url_template or "{namespace}" not in receipt_template:
        raise ProtocolError("LycheeMemory endpoint and instance receipt must be namespace-bound")
    base_url = base_url_template.replace("{namespace}", namespace).rstrip("/")
    expected_commit = str(config["expected_commit"]).lower()
    if len(expected_commit) != 40 or any(char not in "0123456789abcdef" for char in expected_commit):
        raise ProtocolError("LycheeMemory expected_commit must be an exact git commit")
    receipt_path = Path(receipt_template.replace("{namespace}", namespace))
    manager_url = str(config["manager_url"]).rstrip("/")
    if not manager_url:
        raise ProtocolError("LycheeMemory manager_url is required")
    ensure = transport(
        manager_url + "/admin/ensure", {"Content-Type": "application/json"},
        {"scope": namespace, "base_url": base_url, "official_commit": expected_commit},
        min(120.0, float(config["timeout_seconds"])),
    )
    if ensure.get("status") != "ready" or ensure.get("scope") != namespace:
        raise ProtocolError("LycheeMemory isolated instance manager failed")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError("LycheeMemory isolated-instance receipt is unavailable") from exc
    receipt_fields = {"schema_version", "scope", "base_url", "official_commit", "empty_at_start"}
    if (
        not isinstance(receipt, dict) or set(receipt) != receipt_fields
        or receipt.get("schema_version") != "cmd-lycheemem-isolated-instance-v1"
        or receipt.get("scope") != namespace
        or receipt.get("base_url") != base_url
        or receipt.get("official_commit") != expected_commit
        or receipt.get("empty_at_start") is not True
    ):
        raise ProtocolError("LycheeMemory isolated-instance receipt is invalid")
    try:
        timeout = float(config["timeout_seconds"])
    except (TypeError, ValueError) as exc:
        raise ProtocolError("LycheeMemory timeout_seconds is invalid") from exc
    if timeout <= 0:
        raise ProtocolError("LycheeMemory timeout_seconds must be positive")
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get(str(config["api_key_env"]))
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    session_id = namespace

    def call(path: str, body: dict[str, object]) -> Mapping[str, object]:
        if ledger is not None:
            ledger.check_wall()
            call_timeout = min(timeout, ledger.remaining_wall_seconds)
        else:
            call_timeout = timeout
        return transport(base_url + path, headers, body, call_timeout)

    try:
        for event in public_events(request):
            call(
                "/memory/append-turn",
                {"session_id": session_id, "role": "user", "content": event_text(event)},
            )
        if config["consolidate"]:
            call(
                "/memory/consolidate",
                {"session_id": session_id, "background": False},
            )
        result = call(
            "/memory/search",
            {
                "query": retrieval_query(request), "top_k": protocol.retrieval_top_k,
                "include_graph": config["include_graph"], "include_skills": config["include_skills"],
            },
        )
    except ProtocolError:
        raise
    except Exception as exc:
        raise WrapperError("official LycheeMemory API call failed") from exc
    semantic = result.get("semantic_results")
    skills = result.get("skill_results", [])
    if not isinstance(semantic, list) or not isinstance(skills, list):
        raise ProtocolError("LycheeMemory search response omitted raw retrieval lists")
    normalized_semantic: list[dict[str, object]] = []
    for row in semantic[:protocol.retrieval_top_k]:
        if not isinstance(row, Mapping) or not isinstance(row.get("constructed_context"), str):
            raise ProtocolError("LycheeMemory semantic result has an invalid shape")
        normalized_semantic.append({
            "constructed_context": row["constructed_context"],
            "provenance": json_safe(row.get("provenance", [])),
        })
    return json_safe({
        "semantic_results": normalized_semantic,
        "skill_results": skills[:protocol.retrieval_top_k] if config["include_skills"] else [],
    })


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ledger = None
    protocol = None
    try:
        try:
            from .industry_common import read_stdin_request
        except ImportError:
            from industry_common import read_stdin_request
        request = read_stdin_request(expected_system_id="lycheemem")
        ledger = UsageLedger.start(request.budget)
        protocol = ProtocolConfig.load(cli_protocol_path(argv), system_id="lycheemem")
        if request.track not in {"controlled_a1", "controlled_a2"}:
            raise NativeResponseUnavailable("native track requires NativeMemoryResponse")
        ledger.check_wall()
        meter = BackendUsageMeter(protocol.system["backend_usage"], namespace=namespace_for(request))
        if not meter.claim_eligible:
            raise UnmeteredBackend("controlled results require enforcing backend usage metering")
        meter.bootstrap(timeout_seconds=min(30.0, ledger.remaining_wall_seconds))
        before_usage = meter.snapshot()
        try:
            results = retrieve_lycheemem(request, protocol, ledger=ledger)
        finally:
            meter.settle(before_usage, ledger)
        operator, reason = select_with_shared_head(request, protocol, results, ledger)
        emit(response_mapping(status="OK", operator=operator, reason=reason, ledger=ledger, revision=wrapper_revision("lycheemem", protocol)))
    except Exception as exc:
        record_wrapper_failure("lycheemem", exc)
        if ledger is None:
            try:
                from .industry_common import Budget
            except ImportError:
                from industry_common import Budget
            ledger = UsageLedger.start(Budget(0, 0, 0, 0.0, 0))
        status = "UNSUPPORTED" if isinstance(exc, UnsupportedRuntime) else "FAILED"
        reason = exc.reason if isinstance(exc, WrapperError) else "wrapper_unhandled_error"
        revision = "lycheemem:controlled-wrapper-v1" if protocol is None else wrapper_revision("lycheemem", protocol)
        emit(response_mapping(status=status, operator=None, reason=reason, ledger=ledger, revision=revision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
