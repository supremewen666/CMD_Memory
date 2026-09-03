#!/usr/bin/env python3
"""Controlled-track adapter for the pinned official LightMem Python SDK."""

from __future__ import annotations

from contextlib import redirect_stdout
import json
from pathlib import Path
import sys

try:
    from .industry_common import (
        AdapterRequestView, BackendUsageMeter, NativeResponseUnavailable, ProtocolConfig,
        ProtocolError, UnmeteredBackend, UnsupportedRuntime,
        UsageLedger, WrapperError, cli_protocol_path, emit, event_text, expand_namespace,
        json_safe, namespace_for, public_events, response_mapping, retrieval_query,
        select_with_shared_head, wrapper_revision,
    )
except ImportError:  # Direct execution inside the pinned official virtualenv.
    from industry_common import (
        AdapterRequestView, BackendUsageMeter, NativeResponseUnavailable, ProtocolConfig,
        ProtocolError, UnmeteredBackend, UnsupportedRuntime,
        UsageLedger, WrapperError, cli_protocol_path, emit, event_text, expand_namespace,
        json_safe, namespace_for, public_events, response_mapping, retrieval_query,
        select_with_shared_head, wrapper_revision,
    )


def retrieve_lightmem(
    request: AdapterRequestView,
    protocol: ProtocolConfig,
    *,
    memory_class: object | None = None,
    ledger: UsageLedger | None = None,
) -> object:
    config_fields = {"config_path", "force_segment", "force_extract", "offline_update", "offline_score_threshold", "backend_usage"}
    config = protocol.system
    if set(config) != config_fields:
        raise ProtocolError("LightMem wrapper config must use the closed schema")
    if not all(isinstance(config[name], bool) for name in ("force_segment", "force_extract", "offline_update")):
        raise ProtocolError("LightMem boolean settings are invalid")
    threshold = config["offline_score_threshold"]
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ProtocolError("LightMem offline_score_threshold must be numeric")
    try:
        raw_config = json.loads(Path(str(config["config_path"])).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError("LightMem config_path is unreadable") from exc
    if "{namespace}" not in json.dumps(raw_config, sort_keys=True):
        raise ProtocolError("LightMem config must namespace persistent stores with {namespace}")
    namespace = namespace_for(request)
    expanded = expand_namespace(raw_config, namespace)
    if memory_class is None:
        try:
            with redirect_stdout(sys.stderr):
                from lightmem.memory.lightmem import LightMemory
            memory_class = LightMemory
        except (ImportError, ModuleNotFoundError) as exc:
            raise UnsupportedRuntime("official LightMem SDK is not installed") from exc
    try:
        with redirect_stdout(sys.stderr):
            if ledger is not None:
                ledger.check_wall()
            memory = memory_class.from_config(expanded)  # type: ignore[union-attr]
            for event in public_events(request):
                if ledger is not None:
                    ledger.check_wall()
                message = {"role": "user", "content": event_text(event)}
                timestamp = event.get("timestamp")
                if isinstance(timestamp, str) and timestamp:
                    message["time_stamp"] = timestamp
                memory.add_memory(
                    messages=[message], force_segment=config["force_segment"],
                    force_extract=config["force_extract"],
                )
            if config["offline_update"]:
                if ledger is not None:
                    ledger.check_wall()
                memory.construct_update_queue_all_entries()
                if ledger is not None:
                    ledger.check_wall()
                memory.offline_update_all_entries(score_threshold=float(threshold))
            if ledger is not None:
                ledger.check_wall()
            results = memory.retrieve(retrieval_query(request), limit=protocol.retrieval_top_k)
    except UnsupportedRuntime:
        raise
    except Exception as exc:
        raise WrapperError("official LightMem SDK call failed") from exc
    return json_safe(results)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    request = None
    ledger = None
    protocol = None
    try:
        try:
            from .industry_common import read_stdin_request
        except ImportError:
            from industry_common import read_stdin_request
        request = read_stdin_request(expected_system_id="lightmem")
        ledger = UsageLedger.start(request.budget)
        protocol = ProtocolConfig.load(cli_protocol_path(argv), system_id="lightmem")
        if request.track not in {"controlled_a1", "controlled_a2"}:
            raise NativeResponseUnavailable("native track requires NativeMemoryResponse")
        ledger.check_wall()
        meter = BackendUsageMeter(protocol.system["backend_usage"], namespace=namespace_for(request))
        if not meter.claim_eligible:
            raise UnmeteredBackend("controlled results require enforcing backend usage metering")
        meter.bootstrap(timeout_seconds=min(30.0, ledger.remaining_wall_seconds))
        before_usage = meter.snapshot()
        try:
            results = retrieve_lightmem(request, protocol, ledger=ledger)
        finally:
            meter.settle(before_usage, ledger)
        operator, reason = select_with_shared_head(request, protocol, results, ledger)
        emit(response_mapping(status="OK", operator=operator, reason=reason, ledger=ledger, revision=wrapper_revision("lightmem", protocol)))
    except Exception as exc:
        if ledger is None:
            try:
                from .industry_common import Budget
            except ImportError:
                from industry_common import Budget
            ledger = UsageLedger.start(Budget(0, 0, 0, 0.0, 0))
        status = "UNSUPPORTED" if isinstance(exc, UnsupportedRuntime) else "FAILED"
        reason = exc.reason if isinstance(exc, WrapperError) else "wrapper_unhandled_error"
        revision = "lightmem:controlled-wrapper-v1" if protocol is None else wrapper_revision("lightmem", protocol)
        emit(response_mapping(status=status, operator=None, reason=reason, ledger=ledger, revision=revision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
