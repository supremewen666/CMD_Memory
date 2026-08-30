from __future__ import annotations

import json
from pathlib import Path

import pytest

from cmd_audit.spec_v03.contracts import DecisionView
from cmd_audit.spec_v03.industry_adapters import (
    AdapterRequest,
    AdapterResponse,
    BuiltinNoRepair,
    BuiltinRandomLegal,
    PinnedJsonSubprocessAdapter,
    ResourceUsage,
    lightmem_adapter,
    lycheemem_adapter,
    mem0_adapter,
)


def _request(*, budget: ResourceUsage | None = None) -> AdapterRequest:
    decision = DecisionView(
        case_id="case-1", source_dataset_id="source", source_episode_id="episode",
        family_id="family", lineage_id="lineage", event_index=1,
        observation={"current_state": {"state_root": "a" * 64}}, provenance={"source": "public"},
    )
    return AdapterRequest.from_decision(
        run_id="stage9:case-1", system_id="lightmem", track="controlled_a1", decision=decision,
        legal_operator_ids=("process_restore", "process_cache_invalidate"), budget=budget or ResourceUsage(2, 100, 100, 2.0, 0),
    )


def _adapter(tmp_path: Path, command: tuple[str, ...], *, pinned: str = "a" * 40, timeout: float = 1.0) -> PinnedJsonSubprocessAdapter:
    return PinnedJsonSubprocessAdapter(
        capability_id="lightmem:adapter", command=command, repository=tmp_path,
        pinned_commit=pinned, timeout_seconds=timeout,
    )


def _fake_run(monkeypatch: pytest.MonkeyPatch, *, head: str = "a" * 40, output: str = "", returncode: int = 0, timeout: bool = False) -> None:
    calls: list[dict[str, object]] = []

    def run(*args: object, **kwargs: object):
        calls.append({"args": args, "kwargs": kwargs})
        if timeout and len(calls) == 2:
            raise __import__("subprocess").TimeoutExpired(args[0], kwargs.get("timeout", 0))
        if len(calls) == 1:
            return __import__("subprocess").CompletedProcess(args[0], 0, f"{head}\n", "")
        return __import__("subprocess").CompletedProcess(args[0], returncode, output, "")

    monkeypatch.setattr("cmd_audit.spec_v03.industry_adapters.subprocess.run", run)
    return calls  # type: ignore[return-value]


def _response(*, operator: str | None = "process_restore", usage: ResourceUsage | None = None) -> str:
    return json.dumps(AdapterResponse("OK", operator, None if operator else "abstain", usage or ResourceUsage(1, 10, 5, 0.1, 0), "lightmem:adapter").to_mapping())


def test_builtin_adapters_are_closed_and_deterministic() -> None:
    request = _request()
    assert BuiltinNoRepair().invoke(request).selected_operator_id is None
    first = BuiltinRandomLegal(seed=8).invoke(request)
    assert first == BuiltinRandomLegal(seed=8).invoke(request)
    assert first.selected_operator_id in request.legal_operator_ids


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_resource_usage_rejects_non_finite_values(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        ResourceUsage(0, 0, 0, value, 0)


def test_unconfigured_industry_factories_are_unsupported_without_results() -> None:
    request = _request()
    for factory in (lightmem_adapter, lycheemem_adapter, mem0_adapter):
        response = factory().invoke(request)
        assert response.status == "UNSUPPORTED"
        assert response.selected_operator_id is None
        assert response.usage == ResourceUsage.zero()


def test_subprocess_uses_tuple_command_json_and_exact_commit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = _fake_run(monkeypatch, output=_response())
    response = _adapter(tmp_path, ("wrapper", "--json")).invoke(_request())

    assert response.selected_operator_id == "process_restore"
    assert calls[0]["kwargs"]["shell"] is False
    assert calls[1]["args"][0] == ("wrapper", "--json")
    assert calls[1]["kwargs"]["shell"] is False
    payload = json.loads(calls[1]["kwargs"]["input"])
    assert set(payload) == {"schema_version", "run_id", "system_id", "track", "score_namespace", "decision", "legal_operator_ids", "budget"}


def test_commit_mismatch_fails_closed_without_starting_wrapper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = _fake_run(monkeypatch, head="b" * 40, output=_response())
    response = _adapter(tmp_path, ("wrapper",)).invoke(_request())

    assert response.status == "FAILED"
    assert response.abstain_reason == "pinned_commit_mismatch"
    assert len(calls) == 1


def test_illegal_operator_and_budget_excess_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_run(monkeypatch, output=_response(operator="poison_quarantine_audit"))
    illegal = _adapter(tmp_path, ("wrapper",)).invoke(_request())
    assert illegal.status == "FAILED"
    assert illegal.abstain_reason == "wrapper_invalid_response"

    _fake_run(monkeypatch, output=_response(usage=ResourceUsage(3, 10, 5, 0.1, 0)))
    over_budget = _adapter(tmp_path, ("wrapper",)).invoke(_request())
    assert over_budget.status == "FAILED"
    assert over_budget.abstain_reason == "wrapper_invalid_response"


def test_timeout_and_bad_json_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_run(monkeypatch, timeout=True)
    timed_out = _adapter(tmp_path, ("wrapper",), timeout=0.2).invoke(_request())
    assert timed_out.status == "FAILED"
    assert timed_out.abstain_reason == "wrapper_timeout"

    _fake_run(monkeypatch, output="not-json")
    malformed = _adapter(tmp_path, ("wrapper",)).invoke(_request())
    assert malformed.status == "FAILED"
    assert malformed.abstain_reason == "wrapper_invalid_response"
