from __future__ import annotations

from cmd_audit.spec_v03.contracts import DecisionView, canonical_sha256
from cmd_audit.spec_v03.experiment_matrix import (
    STAGE5_VARIANTS,
    STAGE6_VARIANTS,
    STAGE7_VARIANTS,
    STAGE8A_VARIANTS,
    STAGE8B_VARIANTS,
    STAGE9_VARIANTS,
)
from cmd_audit.spec_v03.governance_system_executor import FirstLegalProposalPolicy
from cmd_audit.spec_v03.industry_adapters import ResourceUsage
from cmd_audit.spec_v03.prequential_executor import RuntimeOrderManifest, RuntimeOrderRow
from cmd_audit.spec_v03.repair_stream import MemoryState, PublicEvent
from cmd_audit.spec_v03.runtime_bundle import RuntimeBundle
from cmd_audit.spec_v03.stage59_runner import Stage59Capabilities, Stage59Config, Stage59Runner


def _bundle() -> RuntimeBundle:
    payload = {"content": "runtime memory"}
    payload_sha = canonical_sha256(payload)
    event = PublicEvent("source-event", "public-source", 0, None, "trusted", payload, payload_sha, payload_sha)
    state = MemoryState((event,), (), (), (), ((event.event_id, "trusted"),), (), (), ())
    decision = DecisionView(
        "case-1", "fixture", "episode-1", "family-1", "lineage-1", 0,
        {
            "event_log": [{
                "event_id": event.event_id, "timestamp": None, "actor_scope": "trusted",
                "content": payload, "authority": "trusted",
                "provenance": {"source_payload_sha256": payload_sha},
            }],
            "current_state": {
                "projection_order": [], "projection_index": [],
                "scope_projection": [[event.event_id, "trusted"]], "cache_event_ids": [],
                "supersession_edges": [], "quarantine_set": [], "state_root": state.root,
            },
            "observable_telemetry": {"event_count": 1, "projection_size": 0},
        },
        {"source_sha256": payload_sha},
        ("sealed_fields_omitted",),
    )
    return RuntimeBundle(
        "case-1", "fixture", "episode-1", "family-1", "lineage-1",
        (event.event_id,), decision, state,
    )


def _order() -> RuntimeOrderManifest:
    row = RuntimeOrderRow("case-1", 0, "stationary", 1, "benign")
    body = {
        "seed": 17,
        "schedule": "stationary",
        "rows": [{
            "case_id": "case-1", "event_index": 0, "regime": "stationary",
            "receipt_matures_at": 1, "cas_interleaving": "benign",
        }],
    }
    return RuntimeOrderManifest(17, "stationary", (row,), canonical_sha256(body))


def test_stage59_runner_covers_the_frozen_matrix_and_fails_missing_capabilities_closed() -> None:
    report = Stage59Runner(
        Stage59Config("wiring", "development-non-model", 17, development_non_model=True),
        Stage59Capabilities(proposal_provider=FirstLegalProposalPolicy()),
    ).run((_bundle(),), _order(), system_budget=ResourceUsage.zero())

    assert report.results["stage5"]["status"] == "UNSUPPORTED"
    assert len(report.results["stage6"]) == len(STAGE6_VARIANTS)
    assert len(report.results["stage7"]) == len(STAGE7_VARIANTS)
    assert len(report.results["stage8a"]) == len(STAGE8A_VARIANTS)
    assert len(report.results["stage8b"]) == len(STAGE8B_VARIANTS)
    assert len(report.results["stage9"]) == len(STAGE9_VARIANTS)
    assert tuple(row["arm"] for row in report.results["stage6"]) == STAGE6_VARIANTS
    assert tuple(row["arm"] for row in report.results["stage8a"]) == STAGE8A_VARIANTS
    assert tuple(row["arm"] for row in report.results["stage8b"]) == STAGE8B_VARIANTS
    assert {row["variant"] for row in report.results["stage7"]} == set(STAGE7_VARIANTS)
    assert tuple(row["system_id"] for row in report.results["stage9"]) == STAGE9_VARIANTS
    assert "backbone_provider_missing" in report.unsupported_capabilities
    assert "sealed_oracle_provider_required" in report.unsupported_capabilities
    assert len(report.report_sha256) == 64


def test_stage59_runner_is_content_addressed_and_stage_selective() -> None:
    config = Stage59Config("wiring-selective", "development-non-model", 23, stages=("stage6", "stage8a"))
    first = Stage59Runner(config, Stage59Capabilities()).run(
        (_bundle(),), _order(), system_budget=ResourceUsage.zero(),
    )
    second = Stage59Runner(config, Stage59Capabilities()).run(
        (_bundle(),), _order(), system_budget=ResourceUsage.zero(),
    )
    assert first.report_sha256 == second.report_sha256
    assert tuple(first.results) == ("stage6", "stage8a")
    assert first.to_mapping()["report_sha256"] == first.report_sha256
