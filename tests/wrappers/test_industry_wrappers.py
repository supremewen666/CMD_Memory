from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sys

import pytest

from wrappers.industry_common import (
    AdapterRequestView,
    Budget,
    BudgetExhausted,
    ProtocolConfig,
    ProtocolError,
    UsageLedger,
    response_mapping,
    select_with_shared_head,
)
from wrappers.mem0_adapter import retrieve_mem0
from wrappers import erskill_adapter, memskill_adapter
from wrappers.skill_evidence_common import load_frozen_skill_evidence


def _request(system_id: str, *, llm_calls: int = 8) -> AdapterRequestView:
    raw = {
        "schema_version": "cmd-spec-v03-industry-adapter-request-v1",
        "run_id": "run:stage9",
        "system_id": system_id,
        "track": "controlled_a1",
        "score_namespace": "controlled",
        "decision": {
            "schema_version": "cmd-spec-v03-decision-view-v1",
            "case_id": "case-1",
            "source_dataset_id": "fixture",
            "source_episode_id": "episode-1",
            "family_id": "family-1",
            "lineage_id": "lineage-1",
            "event_index": 2,
            "observation": {
                "query": "Which repair is supported?",
                "event_log": [
                    {"event_id": "event-1", "timestamp": "2026-01-01", "actor_scope": "trusted", "authority": "trusted", "content": {"fact": "old"}},
                    {"event_id": "event-2", "timestamp": "2026-01-02", "actor_scope": "trusted", "authority": "trusted", "content": {"fact": "new"}},
                ],
                "current_state": {"state_root": "a" * 64},
                "observable_telemetry": {"event_count": 2},
                "predicted_syndrome": {"class": "state_drift", "confidence": 0.8},
            },
            "provenance": {"source": "fixture"},
            "unsupported_fields": ["sealed_fields_omitted"],
        },
        "legal_operator_ids": ["state_supersede_lineage", "state_supersede_rebuild"],
        "budget": {"llm_calls": llm_calls, "input_tokens": 10_000, "output_tokens": 512, "wall_clock_seconds": 30.0, "gpu_seconds": 10},
    }
    return AdapterRequestView.parse(raw, expected_system_id=system_id)


def _request_mapping(system_id: str) -> dict[str, object]:
    request = _request(system_id)
    return {
        "schema_version": "cmd-spec-v03-industry-adapter-request-v1",
        "run_id": request.run_id,
        "system_id": request.system_id,
        "track": request.track,
        "score_namespace": request.score_namespace,
        "decision": dict(request.decision),
        "legal_operator_ids": list(request.legal_operator_ids),
        "budget": {
            "llm_calls": request.budget.llm_calls,
            "input_tokens": request.budget.input_tokens,
            "output_tokens": request.budget.output_tokens,
            "wall_clock_seconds": request.budget.wall_clock_seconds,
            "gpu_seconds": request.budget.gpu_seconds,
        },
    }


def _artifact(tmp_path: Path, system_id: str, implementation: str, *, splits=None, evidence=None, source_ids=None) -> tuple[Path, str]:
    path = tmp_path / f"{system_id}.json"
    value = {
        "schema_version": "cmd-frozen-skill-evidence-v1",
        "system_id": system_id,
        "implementation": implementation,
        "artifact_revision": "checkpoint-7",
        "producer_repository": "https://example.test/skill-system.git",
        "producer_commit": "a" * 40,
        "frozen": True,
        "training_splits": splits or ["D_skill", "D_router"],
        "records": {
            "case-1": {
                "evidence": evidence or [{"memory": "new supersedes old"}],
                "selected_skill_ids": ["temporal-update"],
                "retrieval_trace": [{"primitive": "temporal_search", "rank": 1}],
                "source_event_ids": source_ids or ["event-1", "event-2"],
                "usage": {"llm_calls": 1, "input_tokens": 50, "output_tokens": 10, "wall_clock_seconds": 0.5, "gpu_seconds": 1},
            }
        },
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _protocol(tmp_path: Path, system_id: str = "memskill", *, artifact_options=None) -> ProtocolConfig:
    memskill_path, memskill_sha = _artifact(tmp_path, "memskill", "official_memskill_checkpoint_export")
    if system_id == "erskill" and artifact_options:
        erskill_path = Path(artifact_options["artifact_path"])
        erskill_sha = artifact_options["artifact_sha256"]
    else:
        erskill_path, erskill_sha = _artifact(tmp_path, "erskill", "paper_faithful_erskill_reimplementation")
    mem0_path = tmp_path / "mem0.json"
    mem0_path.write_text(json.dumps({"vector_store": {"config": {"collection_name": "cmd_{namespace}"}}}), encoding="utf-8")
    systems = {
        "memskill": {"artifact_path": str(memskill_path), "artifact_sha256": memskill_sha, "implementation": "official_memskill_checkpoint_export"},
        "erskill": {"artifact_path": str(erskill_path), "artifact_sha256": erskill_sha, "implementation": "paper_faithful_erskill_reimplementation"},
        "mem0": {"config_path": str(mem0_path), "add_per_event": True, "backend_usage": {"mode": "development_unmetered", "receipt_path": None, "bootstrap_url": None}},
    }
    if artifact_options and system_id != "erskill":
        systems[system_id].update(artifact_options)
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps({
        "schema_version": "cmd-controlled-memory-protocol-v1",
        "retrieval_top_k": 5,
        "head": {"endpoint": "http://127.0.0.1:8001/v1", "model_id": "Qwen3-14B", "model_snapshot": "frozen", "api_key_env": "QWEN_API_KEY", "max_output_tokens": 64, "timeout_seconds": 10.0, "temperature": 0.0, "max_memory_chars": 4000},
        "systems": systems,
    }), encoding="utf-8")
    return ProtocolConfig.load(path, system_id=system_id)


def test_frozen_memskill_evidence_is_grounded_and_charged(tmp_path: Path) -> None:
    request = _request("memskill")
    protocol = _protocol(tmp_path)
    ledger = UsageLedger.start(request.budget)
    evidence, revision = load_frozen_skill_evidence(
        request, protocol, expected_system_id="memskill",
        allowed_implementations=frozenset({"official_memskill_checkpoint_export"}), ledger=ledger,
    )
    assert evidence["selected_skill_ids"] == ["temporal-update"]
    assert revision == "checkpoint-7"
    assert (ledger.llm_calls, ledger.input_tokens, ledger.output_tokens, ledger.gpu_seconds) == (1, 50, 10, 1)
    assert ledger.elapsed >= 0.5


@pytest.mark.parametrize(
    ("splits", "evidence", "source_ids", "message"),
    [
        (["T_final"], None, None, "evaluation split"),
        (None, [{"ground_truth": "state_supersede_lineage"}], None, "evaluator-only"),
        (None, None, ["hidden-event"], "outside the serving view"),
    ],
)
def test_skill_evidence_fails_closed_on_leakage(tmp_path: Path, splits, evidence, source_ids, message: str) -> None:
    path, digest = _artifact(tmp_path, "erskill", "paper_faithful_erskill_reimplementation", splits=splits, evidence=evidence, source_ids=source_ids)
    protocol = _protocol(tmp_path, "erskill", artifact_options={"artifact_path": str(path), "artifact_sha256": digest})
    request = _request("erskill")
    with pytest.raises(ProtocolError, match=message):
        load_frozen_skill_evidence(
            request, protocol, expected_system_id="erskill",
            allowed_implementations=frozenset({"paper_faithful_erskill_reimplementation"}),
            ledger=UsageLedger.start(request.budget),
        )


def test_shared_head_uses_closed_legal_output_and_measured_usage(tmp_path: Path) -> None:
    protocol = _protocol(tmp_path)
    request = _request("memskill")
    ledger = UsageLedger.start(request.budget)

    def transport(*_args):
        return {"model": "Qwen3-14B", "usage": {"prompt_tokens": 101, "completion_tokens": 7}, "choices": [{"message": {"content": json.dumps({"selected_operator_id": "state_supersede_lineage", "abstain_reason": None})}}]}

    selected, reason = select_with_shared_head(request, protocol, {"memory": "new"}, ledger, transport=transport)
    assert (selected, reason) == ("state_supersede_lineage", None)
    assert (ledger.llm_calls, ledger.input_tokens, ledger.output_tokens) == (1, 101, 7)


def test_precomputed_wall_time_participates_in_budget() -> None:
    ledger = UsageLedger.start(Budget(2, 100, 100, 1.0, 2))
    with pytest.raises(BudgetExhausted):
        ledger.record_batch(llm_calls=1, input_tokens=1, output_tokens=1, gpu_seconds=1, wall_clock_seconds=2.0)
    response = response_mapping(status="FAILED", operator=None, reason="budget_exhausted", ledger=ledger, revision="test")
    assert response["usage"]["wall_clock_seconds"] == 1.0


def test_mem0_wrapper_calls_official_sdk(tmp_path: Path) -> None:
    protocol = _protocol(tmp_path, "mem0")
    request = _request("mem0")

    class FakeMemory:
        @classmethod
        def from_config(cls, config):
            return cls()

        def add(self, messages, *, user_id):
            pass

        def search(self, *, query, filters, top_k):
            return {"results": [{"memory": "official-mem0-result"}]}

    assert retrieve_mem0(request, protocol, memory_class=FakeMemory) == {"results": [{"memory": "official-mem0-result"}]}


@pytest.mark.parametrize(
    ("system_id", "module"),
    [("memskill", memskill_adapter), ("erskill", erskill_adapter)],
)
def test_skill_wrapper_emits_closed_controlled_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    system_id: str, module,
) -> None:
    protocol_path = tmp_path / "protocol.json"
    protocol = _protocol(tmp_path, system_id)
    protocol_path.write_text(json.dumps(protocol.raw), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_request_mapping(system_id))))
    monkeypatch.setattr(
        module, "select_with_shared_head",
        lambda request, _protocol, _evidence, _ledger: (request.legal_operator_ids[0], None),
    )
    assert module.main(["--protocol-config", str(protocol_path)]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "OK"
    assert response["selected_operator_id"] == "state_supersede_lineage"
    assert "frozen-evidence" in response["adapter_revision"]
