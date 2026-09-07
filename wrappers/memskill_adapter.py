#!/usr/bin/env python3
"""Controlled adapter for frozen evidence exported by official MemSkill."""

from __future__ import annotations

import sys

try:
    from .industry_common import (
        Budget, NativeResponseUnavailable, ProtocolConfig, UnsupportedRuntime, UsageLedger,
        WrapperError, cli_protocol_path, emit, read_stdin_request, record_wrapper_failure,
        response_mapping, select_with_shared_head, wrapper_revision,
    )
    from .skill_evidence_common import load_frozen_skill_evidence
except ImportError:
    from industry_common import (
        Budget, NativeResponseUnavailable, ProtocolConfig, UnsupportedRuntime, UsageLedger,
        WrapperError, cli_protocol_path, emit, read_stdin_request, record_wrapper_failure,
        response_mapping, select_with_shared_head, wrapper_revision,
    )
    from skill_evidence_common import load_frozen_skill_evidence


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ledger = None
    protocol = None
    revision = "memskill:controlled-wrapper-v1"
    try:
        request = read_stdin_request(expected_system_id="memskill")
        ledger = UsageLedger.start(request.budget)
        protocol = ProtocolConfig.load(cli_protocol_path(argv), system_id="memskill")
        if request.track not in {"controlled_a1", "controlled_a2"}:
            raise NativeResponseUnavailable("MemSkill has no native CMD repair response")
        evidence, artifact_revision = load_frozen_skill_evidence(
            request, protocol, expected_system_id="memskill",
            allowed_implementations=frozenset({"official_memskill_checkpoint_export"}),
            ledger=ledger,
        )
        operator, reason = select_with_shared_head(request, protocol, evidence, ledger)
        revision = wrapper_revision("memskill", protocol) + ":artifact-" + artifact_revision
        emit(response_mapping(status="OK", operator=operator, reason=reason, ledger=ledger, revision=revision))
    except Exception as exc:
        record_wrapper_failure("memskill", exc)
        if ledger is None:
            ledger = UsageLedger.start(Budget(0, 0, 0, 0.0, 0))
        status = "UNSUPPORTED" if isinstance(exc, UnsupportedRuntime) else "FAILED"
        reason = exc.reason if isinstance(exc, WrapperError) else "wrapper_unhandled_error"
        emit(response_mapping(status=status, operator=None, reason=reason, ledger=ledger, revision=revision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
