#!/usr/bin/env python3
"""Controlled-track adapter for the pinned official Mem0 OSS Python SDK."""

from __future__ import annotations

from contextlib import redirect_stdout
import json
from pathlib import Path
import sys

try:
    from .industry_common import (
        AdapterRequestView, BackendUsageMeter, NativeResponseUnavailable, ProtocolConfig,
        ProtocolError, UnmeteredBackend, UnsupportedRuntime, UsageLedger,
        WrapperError, cli_protocol_path, emit, event_text, expand_namespace, json_safe,
        namespace_for, public_events, response_mapping, retrieval_query,
        select_with_shared_head, wrapper_revision,
    )
except ImportError:  # Direct execution inside the pinned official virtualenv.
    from industry_common import (
        AdapterRequestView, BackendUsageMeter, NativeResponseUnavailable, ProtocolConfig,
        ProtocolError, UnmeteredBackend, UnsupportedRuntime, UsageLedger,
        WrapperError, cli_protocol_path, emit, event_text, expand_namespace, json_safe,
        namespace_for, public_events, response_mapping, retrieval_query,
        select_with_shared_head, wrapper_revision,
    )


def retrieve_mem0(
    request: AdapterRequestView,
    protocol: ProtocolConfig,
    *,
    memory_class: object | None = None,
    ledger: UsageLedger | None = None,
) -> object:
    config = protocol.system
    if set(config) != {"config_path", "add_per_event", "backend_usage"}:
        raise ProtocolError("Mem0 wrapper config must use the closed schema")
    if not isinstance(config["add_per_event"], bool):
        raise ProtocolError("Mem0 add_per_event must be boolean")
    try:
        raw_config = json.loads(Path(str(config["config_path"])).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError("Mem0 config_path is unreadable") from exc
    namespace = namespace_for(request)
    expanded = expand_namespace(raw_config, namespace)
    if memory_class is None:
        try:
            with redirect_stdout(sys.stderr):
                from mem0 import Memory
            memory_class = Memory
        except (ImportError, ModuleNotFoundError) as exc:
            raise UnsupportedRuntime("official Mem0 OSS SDK is not installed") from exc
    messages = [{"role": "user", "content": event_text(event)} for event in public_events(request)]
    try:
        with redirect_stdout(sys.stderr):
            if ledger is not None:
                ledger.check_wall()
            memory = memory_class.from_config(expanded)  # type: ignore[union-attr]
            if config["add_per_event"]:
                for message in messages:
                    if ledger is not None:
                        ledger.check_wall()
                    memory.add([message], user_id=namespace)
            elif messages:
                if ledger is not None:
                    ledger.check_wall()
                memory.add(messages, user_id=namespace)
            if ledger is not None:
                ledger.check_wall()
            results = memory.search(
                query=retrieval_query(request), filters={"user_id": namespace},
                top_k=protocol.retrieval_top_k,
            )
    except UnsupportedRuntime:
        raise
    except Exception as exc:
        raise WrapperError("official Mem0 OSS SDK call failed") from exc
    return json_safe(results)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ledger = None
    protocol = None
    try:
        try:
            from .industry_common import read_stdin_request
        except ImportError:
            from industry_common import read_stdin_request
        request = read_stdin_request(expected_system_id="mem0")
        ledger = UsageLedger.start(request.budget)
        protocol = ProtocolConfig.load(cli_protocol_path(argv), system_id="mem0")
        if request.track not in {"controlled_a1", "controlled_a2"}:
            raise NativeResponseUnavailable("native track requires NativeMemoryResponse")
        ledger.check_wall()
        meter = BackendUsageMeter(protocol.system["backend_usage"], namespace=namespace_for(request))
        if not meter.claim_eligible:
            raise UnmeteredBackend("controlled results require enforcing backend usage metering")
        meter.bootstrap(timeout_seconds=min(30.0, ledger.remaining_wall_seconds))
        before_usage = meter.snapshot()
        try:
            results = retrieve_mem0(request, protocol, ledger=ledger)
        finally:
            meter.settle(before_usage, ledger)
        operator, reason = select_with_shared_head(request, protocol, results, ledger)
        emit(response_mapping(status="OK", operator=operator, reason=reason, ledger=ledger, revision=wrapper_revision("mem0", protocol)))
    except Exception as exc:
        if ledger is None:
            try:
                from .industry_common import Budget
            except ImportError:
                from industry_common import Budget
            ledger = UsageLedger.start(Budget(0, 0, 0, 0.0, 0))
        status = "UNSUPPORTED" if isinstance(exc, UnsupportedRuntime) else "FAILED"
        reason = exc.reason if isinstance(exc, WrapperError) else "wrapper_unhandled_error"
        revision = "mem0:controlled-wrapper-v1" if protocol is None else wrapper_revision("mem0", protocol)
        emit(response_mapping(status=status, operator=None, reason=reason, ledger=ledger, revision=revision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
