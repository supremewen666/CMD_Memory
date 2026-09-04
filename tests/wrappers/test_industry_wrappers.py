from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from wrappers.industry_common import (
    AdapterRequestView,
    BackendUsageMeter,
    Budget,
    BudgetExhausted,
    ProtocolConfig,
    ProtocolError,
    UsageLedger,
    namespace_for,
    response_mapping,
    select_with_shared_head,
)
from wrappers.lightmem_adapter import normalize_lightmem_timestamp, retrieve_lightmem
from wrappers.lycheemem_adapter import retrieve_lycheemem
from wrappers.mem0_adapter import retrieve_mem0


def _request(system_id: str, *, track: str = "controlled_a1", llm_calls: int = 2) -> dict[str, object]:
    return {
        "schema_version": "cmd-spec-v03-industry-adapter-request-v1",
        "run_id": "run:stage9",
        "system_id": system_id,
        "track": track,
        "score_namespace": "native" if track == "native" else "controlled",
        "decision": {
            "schema_version": "cmd-spec-v03-decision-view-v1",
            "case_id": "case-1",
            "source_dataset_id": "fixture",
            "source_episode_id": "episode-1",
            "family_id": "family-1",
            "lineage_id": "lineage-1",
            "event_index": 2,
            "observation": {
                "query": "Which memory repair is supported by the visible incident?",
                "event_log": [
                    {
                        "event_id": "event-1", "timestamp": "2026-01-01",
                        "actor_scope": "trusted", "authority": "trusted",
                        "content": {"fact": "old value"},
                    },
                    {
                        "event_id": "event-2", "timestamp": "2026-01-02",
                        "actor_scope": "trusted", "authority": "trusted",
                        "content": {"fact": "new value"},
                    },
                ],
                "current_state": {"state_root": "a" * 64},
                "observable_telemetry": {"event_count": 2},
                "predicted_syndrome": {"class": "state_drift", "confidence": 0.8},
            },
            "provenance": {"source": "fixture"},
            "unsupported_fields": ["sealed_fields_omitted"],
        },
        "legal_operator_ids": ["state_supersede_lineage", "state_supersede_rebuild"],
        "budget": {
            "llm_calls": llm_calls,
            "input_tokens": 10_000,
            "output_tokens": 128,
            "wall_clock_seconds": 30.0,
            "gpu_seconds": 0,
        },
    }


def _protocol(tmp_path: Path) -> Path:
    lightmem_config = tmp_path / "lightmem.json"
    lightmem_config.write_text(json.dumps({
        "embedding_retriever": {"configs": {"path": "stores/{namespace}"}},
    }), encoding="utf-8")
    mem0_config = tmp_path / "mem0.json"
    mem0_config.write_text(json.dumps({
        "vector_store": {"config": {"collection_name": "cmd_{namespace}"}},
    }), encoding="utf-8")
    protocol = tmp_path / "protocol.json"
    protocol.write_text(json.dumps({
        "schema_version": "cmd-controlled-memory-protocol-v1",
        "retrieval_top_k": 5,
        "head": {
            "endpoint": "http://127.0.0.1:8001/v1",
            "model_id": "Qwen3-14B",
            "model_snapshot": "frozen-qwen3-snapshot",
            "api_key_env": "QWEN_API_KEY",
            "max_output_tokens": 64,
            "timeout_seconds": 10.0,
            "temperature": 0.0,
            "max_memory_chars": 4000,
        },
        "systems": {
            "lightmem": {
                "config_path": str(lightmem_config),
                "force_segment": True,
                "force_extract": True,
                "offline_update": True,
                "offline_score_threshold": 0.8,
                "backend_usage": {"mode": "development_unmetered", "receipt_path": None, "bootstrap_url": None},
            },
            "lycheemem": {
                "base_url": "http://127.0.0.1:8002/instances/{namespace}",
                "manager_url": "http://127.0.0.1:8002",
                "instance_receipt_path": str(tmp_path / "lychee-{namespace}.json"),
                "expected_commit": "c" * 40,
                "api_key_env": "LYCHEEMEM_API_KEY",
                "timeout_seconds": 10.0,
                "consolidate": True,
                "include_graph": True,
                "include_skills": False,
                "backend_usage": {"mode": "development_unmetered", "receipt_path": None, "bootstrap_url": None},
            },
            "mem0": {
                "config_path": str(mem0_config), "add_per_event": True,
                "backend_usage": {"mode": "development_unmetered", "receipt_path": None, "bootstrap_url": None},
            },
        },
    }), encoding="utf-8")
    return protocol


def _parsed(system_id: str) -> AdapterRequestView:
    return AdapterRequestView.parse(_request(system_id), expected_system_id=system_id)


def _write_lychee_instance_receipt(path: Path, request: AdapterRequestView) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    config = raw["systems"]["lycheemem"]
    namespace = namespace_for(request)
    base_url = config["base_url"].replace("{namespace}", namespace)
    receipt = Path(config["instance_receipt_path"].replace("{namespace}", namespace))
    receipt.write_text(json.dumps({
        "schema_version": "cmd-lycheemem-isolated-instance-v1",
        "scope": namespace,
        "base_url": base_url,
        "official_commit": config["expected_commit"],
        "empty_at_start": True,
    }), encoding="utf-8")


def test_shared_head_uses_closed_legal_output_and_measured_usage(tmp_path: Path) -> None:
    protocol = ProtocolConfig.load(_protocol(tmp_path), system_id="mem0")
    request = _parsed("mem0")
    ledger = UsageLedger.start(request.budget)
    seen: dict[str, object] = {}

    def transport(url, headers, body, timeout):
        seen.update(url=url, headers=headers, body=body, timeout=timeout)
        return {
            "model": "Qwen3-14B",
            "usage": {"prompt_tokens": 101, "completion_tokens": 7},
            "choices": [{"message": {"content": json.dumps({
                "selected_operator_id": "state_supersede_lineage",
                "abstain_reason": None,
            })}}],
        }

    selected, reason = select_with_shared_head(
        request, protocol, {"results": [{"memory": "new value"}]}, ledger,
        transport=transport,
    )

    assert (selected, reason) == ("state_supersede_lineage", None)
    assert seen["url"] == "http://127.0.0.1:8001/v1/chat/completions"
    assert seen["body"]["temperature"] == 0.0
    assert ledger.llm_calls == 1
    assert (ledger.input_tokens, ledger.output_tokens) == (101, 7)


def test_shared_head_preflight_stops_before_call_when_budget_is_zero(tmp_path: Path) -> None:
    protocol = ProtocolConfig.load(_protocol(tmp_path), system_id="mem0")
    request = AdapterRequestView.parse(_request("mem0", llm_calls=0), expected_system_id="mem0")
    ledger = UsageLedger.start(request.budget)
    called = False

    def transport(*_args):
        nonlocal called
        called = True
        return {}

    with pytest.raises(BudgetExhausted):
        select_with_shared_head(request, protocol, [], ledger, transport=transport)
    assert called is False


def test_over_budget_usage_saturates_failed_response_at_parent_contract() -> None:
    ledger = UsageLedger.start(Budget(1, 10, 5, 1.0, 0))
    with pytest.raises(BudgetExhausted):
        ledger.record_batch(llm_calls=2, input_tokens=12, output_tokens=3)

    response = response_mapping(
        status="FAILED", operator=None, reason="budget_exhausted",
        ledger=ledger, revision="test-wrapper",
    )
    usage = response["usage"]
    assert usage["llm_calls"] == 1
    assert usage["input_tokens"] == 10
    assert usage["output_tokens"] == 3
    assert 0 <= usage["wall_clock_seconds"] <= 1.0


def test_wall_overrun_emits_a_parent_valid_budget_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = UsageLedger(Budget(1, 10, 5, 1.0, 0), started_at=10.0)
    monkeypatch.setattr("wrappers.industry_common.time.monotonic", lambda: 11.5)

    response = response_mapping(
        status="OK", operator="state_supersede_lineage", reason=None,
        ledger=ledger, revision="test-wrapper",
    )
    assert response["status"] == "FAILED"
    assert response["selected_operator_id"] is None
    assert response["abstain_reason"] == "budget_exhausted"
    assert response["usage"]["wall_clock_seconds"] == 1.0


def test_lightmem_wrapper_calls_official_python_api(tmp_path: Path) -> None:
    protocol = ProtocolConfig.load(_protocol(tmp_path), system_id="lightmem")
    request = _parsed("lightmem")

    class FakeLightMemory:
        instance = None

        def __init__(self, config):
            self.config = config
            self.added = []
            self.queue_built = False
            self.threshold = None
            self.retrieval = None
            FakeLightMemory.instance = self

        @classmethod
        def from_config(cls, config):
            return cls(config)

        def add_memory(self, **kwargs):
            self.added.append(kwargs)

        def construct_update_queue_all_entries(self):
            self.queue_built = True

        def offline_update_all_entries(self, *, score_threshold):
            self.threshold = score_threshold

        def retrieve(self, query, *, limit):
            self.retrieval = (query, limit)
            return [{"memory": "official-lightmem-result"}]

    results = retrieve_lightmem(request, protocol, memory_class=FakeLightMemory)
    instance = FakeLightMemory.instance
    assert len(instance.added) == 2
    assert instance.queue_built is True
    assert instance.threshold == 0.8
    assert instance.retrieval[1] == 5
    assert namespace_for(request) in instance.config["embedding_retriever"]["configs"]["path"]
    assert results == [{"memory": "official-lightmem-result"}]


def test_lightmem_timestamp_normalization_accepts_public_benchmark_format() -> None:
    assert normalize_lightmem_timestamp("Sep 04, 2025, 19:58:38") == "2025-09-04T19:58:38"
    assert normalize_lightmem_timestamp("2025-09-04T19:58:38") == "2025-09-04T19:58:38"


def test_lightmem_wrapper_rejects_shared_persistent_store(tmp_path: Path) -> None:
    path = _protocol(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    Path(raw["systems"]["lightmem"]["config_path"]).write_text(
        json.dumps({"embedding_retriever": {"configs": {"path": "shared-store"}}}),
        encoding="utf-8",
    )
    protocol = ProtocolConfig.load(path, system_id="lightmem")
    with pytest.raises(ProtocolError, match="namespace"):
        retrieve_lightmem(_parsed("lightmem"), protocol, memory_class=object())


def test_mem0_wrapper_scopes_add_and_search_to_case_namespace(tmp_path: Path) -> None:
    protocol = ProtocolConfig.load(_protocol(tmp_path), system_id="mem0")
    request = _parsed("mem0")

    class FakeMemory:
        instance = None

        def __init__(self, config):
            self.config = config
            self.added = []
            self.search_call = None
            FakeMemory.instance = self

        @classmethod
        def from_config(cls, config):
            return cls(config)

        def add(self, messages, *, user_id):
            self.added.append((messages, user_id))

        def search(self, **kwargs):
            self.search_call = kwargs
            return {"results": [{"memory": "official-mem0-result"}]}

    results = retrieve_mem0(request, protocol, memory_class=FakeMemory)
    instance = FakeMemory.instance
    namespace = namespace_for(request)
    assert len(instance.added) == 2
    assert {user_id for _messages, user_id in instance.added} == {namespace}
    assert instance.search_call["filters"] == {"user_id": namespace}
    assert instance.search_call["top_k"] == 5
    assert namespace in instance.config["vector_store"]["config"]["collection_name"]
    assert results["results"][0]["memory"] == "official-mem0-result"


def test_lycheemem_wrapper_uses_append_consolidate_and_raw_search(tmp_path: Path) -> None:
    path = _protocol(tmp_path)
    request = _parsed("lycheemem")
    _write_lychee_instance_receipt(path, request)
    protocol = ProtocolConfig.load(path, system_id="lycheemem")
    calls: list[tuple[str, dict[str, object]]] = []
    timeouts: list[float] = []

    def transport(url, _headers, body, timeout):
        calls.append((url, dict(body)))
        timeouts.append(timeout)
        if url.endswith("/admin/ensure"):
            return {"status": "ready", "scope": namespace_for(request)}
        if url.endswith("/memory/search"):
            return {"semantic_results": [{"constructed_context": "official-lychee-result"}]}
        return {"status": "ok"}

    ledger = UsageLedger.start(request.budget)
    result = retrieve_lycheemem(request, protocol, transport=transport, ledger=ledger)
    assert [url.rsplit("/", 1)[-1] for url, _body in calls] == [
        "ensure", "append-turn", "append-turn", "consolidate", "search",
    ]
    session_ids = {
        body["session_id"] for url, body in calls
        if "/memory/" in url and not url.endswith("/memory/search")
    }
    assert session_ids == {namespace_for(request)}
    assert calls[-1][1]["top_k"] == 5
    assert calls[-1][1]["include_skills"] is False
    assert result["semantic_results"][0]["constructed_context"] == "official-lychee-result"
    assert all(0 < timeout <= request.budget.wall_clock_seconds for timeout in timeouts)


def test_protocol_rejects_unisolated_lycheemem_service(tmp_path: Path) -> None:
    path = _protocol(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["systems"]["lycheemem"]["base_url"] = "http://127.0.0.1:8002"
    path.write_text(json.dumps(raw), encoding="utf-8")
    protocol = ProtocolConfig.load(path, system_id="lycheemem")
    with pytest.raises(ProtocolError, match="namespace-bound"):
        retrieve_lycheemem(_parsed("lycheemem"), protocol, transport=lambda *_args: {})


def test_backend_usage_meter_records_enforcing_proxy_delta(tmp_path: Path) -> None:
    request = _parsed("mem0")
    namespace = namespace_for(request)
    receipt = tmp_path / f"{namespace}.json"

    def write(calls: int, inputs: int, outputs: int, gpu: int) -> None:
        receipt.write_text(json.dumps({
            "schema_version": "cmd-metered-model-usage-receipt-v1",
            "scope": namespace,
            "llm_calls": calls,
            "input_tokens": inputs,
            "output_tokens": outputs,
            "gpu_seconds": gpu,
        }), encoding="utf-8")

    write(2, 30, 8, 0)
    meter = BackendUsageMeter({
        "mode": "enforcing_proxy_receipt",
        "receipt_path": str(tmp_path / "{namespace}.json"),
        "bootstrap_url": "http://127.0.0.1:9999",
    }, namespace=namespace)
    before = meter.snapshot()
    write(4, 70, 17, 0)
    ledger = UsageLedger.start(request.budget)
    meter.settle(before, ledger)
    assert (ledger.llm_calls, ledger.input_tokens, ledger.output_tokens) == (2, 40, 9)


def test_executable_wrapper_fails_native_track_closed(tmp_path: Path) -> None:
    protocol = _protocol(tmp_path)
    wrapper = Path(__file__).parents[2] / "wrappers" / "mem0_adapter.py"
    completed = subprocess.run(
        [sys.executable, str(wrapper), "--protocol-config", str(protocol)],
        input=json.dumps(_request("mem0", track="native")), text=True,
        capture_output=True, check=False, timeout=10,
    )
    assert completed.returncode == 0
    response = json.loads(completed.stdout)
    assert response["status"] == "UNSUPPORTED"
    assert response["selected_operator_id"] is None
    assert response["abstain_reason"] == "native_response_unavailable"
    assert set(response["usage"]) == {
        "llm_calls", "input_tokens", "output_tokens", "wall_clock_seconds", "gpu_seconds",
    }


def test_executable_wrapper_rejects_unmetered_controlled_result(tmp_path: Path) -> None:
    protocol = _protocol(tmp_path)
    wrapper = Path(__file__).parents[2] / "wrappers" / "mem0_adapter.py"
    completed = subprocess.run(
        [sys.executable, str(wrapper), "--protocol-config", str(protocol)],
        input=json.dumps(_request("mem0")), text=True,
        capture_output=True, check=False, timeout=10,
    )
    assert completed.returncode == 0
    response = json.loads(completed.stdout)
    assert response["status"] == "UNSUPPORTED"
    assert response["abstain_reason"] == "backend_usage_unmetered"
    assert "development_unmetered" in response["adapter_revision"]
