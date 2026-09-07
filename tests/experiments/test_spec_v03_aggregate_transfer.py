from __future__ import annotations

import json
from pathlib import Path
from dataclasses import replace
import subprocess
import sys

import pytest

from cmd_audit.spec_v03.contracts import canonical_sha256
from cmd_audit.spec_v03.stage5_executor import STAGE5_EXECUTOR_SCHEMA
from cmd_audit.spec_v03.stage59_runner import STAGE59_SCHEMA
from experiments.spec_v03_aggregate_transfer import aggregate, main
from cmd_audit.spec_v03.backbone_provider import (
    BackboneProviderConfig,
    DeterministicDevelopmentProvider,
    ProviderBudget,
)
from cmd_audit.spec_v03.industry_adapters import ResourceUsage
from cmd_audit.spec_v03.prequential_executor import (
    RuntimeOrderManifest,
    RuntimeOrderRow,
)
from cmd_audit.spec_v03.stage59_runner import (
    Stage59Capabilities,
    Stage59Config,
    Stage59Runner,
)
from cmd_audit.spec_v03.stage5_executor import (
    StructuralDevelopmentStage5FeedbackProvider,
)
from tests.spec_v03.test_stage59_runner import _bundle


def test_aggregate_cli_help_runs_as_direct_script() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "experiments" / "spec_v03_aggregate_transfer.py"),
            "--help",
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "usage" in result.stdout.casefold()
    assert "--manifest" in result.stdout


def _sha(value: object) -> str:
    return canonical_sha256(value)


def _selection(arm: str, event: int) -> dict[str, object]:
    body = {
        "arm": arm,
        "event_index": event - 1,
        "case_id": f"case-{event}",
        "candidate_skill_revision_ids": ["skill"],
        "selected_skill_revision_id": "skill",
        "backbone_prediction_sha256": _sha("prediction"),
        "backbone_scores": [["skill", 0.5]],
        "selected_at_event_index": event - 1,
        "observed_after_event_index": event,
        "selection_id": f"{arm}-{event}",
        "selection_mode": "fixture",
        "router_snapshot_before_sha256": _sha("before"),
        "router_snapshot_after_sha256": _sha("after"),
        "algorithm_snapshot_sha256": _sha("algorithm"),
        "abstain_reason": None,
    }
    return {**body, "record_sha256": _sha(body)}


def _receipt(
    arm: str,
    event: int,
    utility: float,
    *,
    family="process_restore",
    strategy="targeted",
    regime="stationary",
    safety=True,
) -> dict[str, object]:
    updates = (
        [
            {
                "key": ["global", "skill"],
                "before_precision": 1.0,
                "before_natural": 0.2,
                "before_mean": 0.2,
                "after_precision": 2.0,
                "after_natural": 1.0,
                "after_mean": 0.5,
            }
        ]
        if arm in {"mix_ghost", "ghost_hierarchy"}
        else []
    )
    return {
        "arm": arm,
        "receipt_sha256": _sha([arm, event]),
        "selection_id": f"{arm}-{event}",
        "selected_skill_revision_id": "skill",
        "selected_at_event_index": event - 1,
        "observed_after_event_index": event,
        "utility": utility,
        "settled_before_event_index": event,
        "posterior_before_sha256": _sha(["b", event]),
        "posterior_after_sha256": _sha(["a", event]),
        "valid": True,
        "rolled_back": False,
        "delayed_regression": False,
        "safety_passed": safety,
        "invariant_passed": safety,
        "locality_cost": 1.0,
        "collateral_cost": 0.2,
        "operator_family": family,
        "strategy_id": strategy,
        "recurrence_after_commit": False,
        "regime": regime,
        "posterior_updates": updates,
    }


def _arm(
    name: str, utility: float, receipts: list[dict[str, object]] | None = None
) -> dict[str, object]:
    records = receipts or [
        _receipt(name, 2, utility),
        _receipt(name, 4, utility + 0.1, strategy="rebuild", regime="abrupt"),
    ]
    return {
        "arm": name,
        "status": "COMPLETE",
        "selection_records": [
            _selection(name, int(row["observed_after_event_index"])) for row in records
        ],
        "receipt_records": records,
        "censored_selection_ids": [],
        "algorithm_snapshot": {},
        "algorithm_snapshot_sha256": _sha(name),
        "resource_usage": {},
        "adaptation_prefix_event_count": 0,
        "scored_suffix_event_count": 2,
        "imported_router_snapshot": False,
    }


def _stage59(
    path: Path,
    run_id: str,
    arms: list[dict[str, object]],
    *,
    model="target",
    seed=7,
    imported=False,
    prefix=False,
) -> Path:
    for arm in arms:
        arm["imported_router_snapshot"] = imported
        arm["adaptation_prefix_event_count"] = 2 if prefix else 0
    stage = {
        "schema_version": STAGE5_EXECUTOR_SCHEMA,
        "config_sha256": _sha(run_id),
        "order_manifest_sha256": _sha("order"),
        "backbone_prediction_sha256s": [],
        "arms": arms,
        "resource_usage": {},
    }
    stage["report_sha256"] = _sha(stage)
    config = {
        "run_id": run_id,
        "model_id": model,
        "seed": seed,
        "track": "controlled_a1",
        "stages": ["stage5"],
        "development_non_model": True,
        "schema_version": STAGE59_SCHEMA,
    }
    out = {
        "schema_version": STAGE59_SCHEMA,
        "config": config,
        "order_manifest_sha256": _sha("order"),
        "results": {"stage5": stage},
        "unsupported_capabilities": [],
    }
    out["report_sha256"] = _sha(out)
    path.write_text(json.dumps(out))
    return path


def _manifest(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    p = tmp_path / "manifest.json"
    p.write_text(
        json.dumps(
            {
                "schema_version": "cmd-spec-v03-stage5-aggregation-manifest-v1",
                "inputs": rows,
            }
        )
    )
    return p


def test_paired_router_only_trajectory_regret_and_safety_table(tmp_path: Path) -> None:
    path = _stage59(
        tmp_path / "a.json",
        "run",
        [
            _arm("mix_ghost", 0.8),
            _arm("ghost_hierarchy", 0.2),
            _arm("random_legal", 1.0),
        ],
    )
    result = aggregate([path])
    comparisons = result["machine"]["router_comparisons"]
    assert (
        comparisons[0]["comparison"] == "mix_ghost>ghost_hierarchy"
        and comparisons[0]["matched_event_count"] == 2
    )
    assert result["machine"]["development_gates"]["mix_ghost_router"] == "PASS"
    event = result["machine"]["ecology_trajectory"][0]["events"][0]
    assert (
        event["cumulative_strategy_share"]
        and event["pseudo_regret"] is not None
        and event["posterior_updates"]
    )
    process = result["machine"]["safety_repair"][0]
    assert (
        process["valid_repair_rate"] == 1.0
        and process["mean_collateral"] == pytest.approx(0.2)
        and process["safety_pass_rate_coverage"] > 0
    )


def test_manifest_matched_pairing_suffix_intersection_and_cli_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset = _stage59(
        tmp_path / "reset.json",
        "reset",
        [
            _arm("mix_ghost", 0.2),
            _arm("ghost_hierarchy", 0.2),
            _arm("random_legal", 0.2),
        ],
    )
    global_ = _stage59(
        tmp_path / "global.json",
        "global",
        [
            _arm("mix_ghost", 0.5),
            _arm("ghost_hierarchy", 0.5),
            _arm("random_legal", 0.9),
        ],
        imported=True,
    )
    prefix = _stage59(
        tmp_path / "prefix.json",
        "prefix",
        [
            _arm("mix_ghost", 0.8),
            _arm("ghost_hierarchy", 0.8),
            _arm("random_legal", 1.0),
        ],
        imported=True,
        prefix=True,
    )
    manifest = _manifest(
        tmp_path,
        [
            {"path": reset.name, "condition": "reset", "stream_id": "target-stream"},
            {"path": global_.name, "condition": "global", "stream_id": "target-stream"},
            {
                "path": prefix.name,
                "condition": "global_prefix",
                "stream_id": "target-stream",
            },
        ],
    )
    result = aggregate([], manifest=manifest)
    assert any(
        x["comparison"] == "global>reset" and x["matched_event_count"] == 2
        for x in result["machine"]["transfer_comparisons"]
    )
    assert any(
        x["comparison"] == "global_prefix>global" and x["matched_event_count"] == 2
        for x in result["machine"]["transfer_comparisons"]
    )
    assert "random_legal" not in {
        row["arm"] for row in result["machine"]["transfer_comparisons"]
    }
    output = tmp_path / "nested" / "out.json"
    monkeypatch.setattr(
        "sys.argv", ["aggregate", "--manifest", str(manifest), "--output", str(output)]
    )
    assert main() == 0 and output.exists() and output.with_suffix(".md").exists()
    assert "| comparison |" in output.with_suffix(".md").read_text()
    assert "| --- |" in output.with_suffix(".md").read_text()


def test_v1_and_unknown_telemetry_are_rejected(tmp_path: Path) -> None:
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"schema_version": "cmd-spec-v03-stage5-executor-v1"}))
    with pytest.raises(ValueError, match="cannot be recovered"):
        aggregate([old])
    path = _stage59(
        tmp_path / "bad.json",
        "bad",
        [_arm("mix_ghost", 0.2), _arm("ghost_hierarchy", 0.2)],
    )
    raw = json.loads(path.read_text())
    del raw["results"]["stage5"]["arms"][0]["receipt_records"][0]["safety_passed"]
    stage = raw["results"]["stage5"]
    stage["report_sha256"] = _sha(
        {k: v for k, v in stage.items() if k != "report_sha256"}
    )
    raw["report_sha256"] = _sha({k: v for k, v in raw.items() if k != "report_sha256"})
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="receipt schema"):
        aggregate([path])


def test_matched_requires_manifest_label_and_missing_pairs_fail_closed(
    tmp_path: Path,
) -> None:
    only = _stage59(tmp_path / "only.json", "only", [_arm("mix_ghost", 0.5)])
    result = aggregate([only])
    assert (
        result["machine"]["development_gates"]["mix_ghost_router"]
        == "INSUFFICIENT_DATA"
    )
    assert result["machine"]["development_gates"]["transfer"] == "INSUFFICIENT_DATA"
    manifest = _manifest(
        tmp_path, [{"path": only.name, "condition": "matched", "stream_id": "s"}]
    )
    with pytest.raises(ValueError, match="contradicts"):
        aggregate([], manifest=manifest)


def test_real_stage59_report_aggregates_provider_audit_and_posterior(
    tmp_path: Path,
) -> None:
    first = _bundle()
    second = replace(
        first,
        case_id="case-2",
        decision_view=replace(first.decision_view, case_id="case-2", event_index=1),
    )
    rows = (
        RuntimeOrderRow("case-1", 0, "stationary", 1, "benign"),
        RuntimeOrderRow("case-2", 1, "stationary", 2, "benign"),
    )
    order_body = {
        "seed": 37,
        "schedule": "stationary",
        "rows": [
            {
                "case_id": row.case_id,
                "event_index": row.event_index,
                "regime": row.regime,
                "receipt_matures_at": row.receipt_matures_at,
                "cas_interleaving": row.cas_interleaving,
            }
            for row in rows
        ],
    }
    order = RuntimeOrderManifest(37, "stationary", rows, canonical_sha256(order_body))
    model_id = "real-stage59-fixture"
    provider = DeterministicDevelopmentProvider(
        BackboneProviderConfig(
            model_id=model_id, snapshot="fixture", environment="DEVELOPMENT"
        ),
        ProviderBudget(max_requests=20, max_total_tokens=10_000),
    )
    report = (
        Stage59Runner(
            Stage59Config("real-aggregate", model_id, 37, stages=("stage5",)),
            Stage59Capabilities(
                backbone_provider=provider,
                feedback_provider=StructuralDevelopmentStage5FeedbackProvider(
                    model_id=model_id
                ),
                stage5_arms=("mix_ghost", "ghost_hierarchy"),
            ),
        )
        .run((first, second), order, system_budget=ResourceUsage.zero())
        .to_mapping()
    )
    assert isinstance(report["results"]["stage5"]["provider_call_audit"], list)
    path = tmp_path / "real-stage59.json"
    path.write_text(json.dumps(report))
    result = aggregate([path])
    receipt = report["results"]["stage5"]["arms"][0]["receipt_records"][0]
    assert receipt["safety_passed"] is not None and receipt["posterior_updates"]
    assert result["machine"]["coverage"]["runs"] == 1


@pytest.mark.parametrize(
    ("mix_safety", "ghost_safety", "expected"),
    [(True, True, "PASS"), (False, True, "FAIL"), (None, True, "INSUFFICIENT_DATA")],
)
def test_safety_gate_pass_fail_and_unknown(
    tmp_path, mix_safety, ghost_safety, expected
):
    result = aggregate(
        [
            _stage59(
                tmp_path / "safety.json",
                "safety",
                [
                    _arm(
                        "mix_ghost",
                        0.8,
                        [_receipt("mix_ghost", 2, 0.8, safety=mix_safety)],
                    ),
                    _arm(
                        "ghost_hierarchy",
                        0.2,
                        [_receipt("ghost_hierarchy", 2, 0.2, safety=ghost_safety)],
                    ),
                ],
            )
        ]
    )
    assert (
        result["machine"]["development_gates"]["safety_not_down_structural_proxy"]
        == expected
    )


def test_regime_switch_uses_three_event_post_window_and_detects_strategy_reversal(
    tmp_path,
):
    mix = [
        _receipt("mix_ghost", event, utility, strategy="targeted", regime="stationary")
        for event, utility in ((2, 0.1), (4, 0.2), (6, 0.3))
    ]
    mix += [
        _receipt("mix_ghost", event, utility, strategy="rebuild", regime="abrupt")
        for event, utility in ((8, 0.7), (10, 0.8), (12, 0.9))
    ]
    ghost = [
        _receipt(
            "ghost_hierarchy",
            event,
            0.0,
            regime="stationary" if event < 8 else "abrupt",
        )
        for event in (2, 4, 6, 8, 10, 12)
    ]
    result = aggregate(
        [
            _stage59(
                tmp_path / "switch.json",
                "switch",
                [_arm("mix_ghost", 0.1, mix), _arm("ghost_hierarchy", 0.0, ghost)],
            )
        ]
    )
    trajectory = next(
        row
        for row in result["machine"]["ecology_trajectory"]
        if row["arm"] == "mix_ghost"
    )
    switch = trajectory["regime_switches"][0]
    assert switch["pre_window_count"] == switch["post_window_count"] == 3
    assert switch["post_window_outcome"] == pytest.approx(0.8)
    assert switch["pre_window_dominant_strategy"] == "targeted"
    assert switch["post_window_dominant_strategy"] == "rebuild"
    assert switch["strategy_reversal"] is True
    assert (
        result["machine"]["development_gates"][
            "strategy_niche_change_stationary_abrupt"
        ]
        == "PASS"
    )


@pytest.mark.parametrize(
    ("abrupt", "expected"), [(True, "FAIL"), (False, "INSUFFICIENT_DATA")]
)
def test_niche_gate_fail_without_reversal_and_insufficient_without_switch(
    tmp_path, abrupt, expected
):
    mix = [_receipt("mix_ghost", 2, 0.2, strategy="targeted", regime="stationary")]
    ghost = [
        _receipt("ghost_hierarchy", 2, 0.1, strategy="targeted", regime="stationary")
    ]
    if abrupt:
        mix.append(_receipt("mix_ghost", 4, 0.2, strategy="targeted", regime="abrupt"))
        ghost.append(
            _receipt("ghost_hierarchy", 4, 0.1, strategy="targeted", regime="abrupt")
        )
    result = aggregate(
        [
            _stage59(
                tmp_path / "niche.json",
                "niche",
                [_arm("mix_ghost", 0.2, mix), _arm("ghost_hierarchy", 0.1, ghost)],
            )
        ]
    )
    assert (
        result["machine"]["development_gates"][
            "strategy_niche_change_stationary_abrupt"
        ]
        == expected
    )


def test_multistream_failure_dominates_pass_and_missing_coverage(tmp_path: Path) -> None:
    a_reset = _stage59(tmp_path / "a-reset.json", "a-reset", [_arm("mix_ghost", 0.2)])
    a_global = _stage59(tmp_path / "a-global.json", "a-global", [_arm("mix_ghost", 0.5)], imported=True)
    a_prefix = _stage59(tmp_path / "a-prefix.json", "a-prefix", [_arm("mix_ghost", 0.8)], imported=True, prefix=True)
    b_reset = _stage59(tmp_path / "b-reset.json", "b-reset", [_arm("mix_ghost", 0.6)])
    b_global = _stage59(tmp_path / "b-global.json", "b-global", [_arm("mix_ghost", 0.4)], imported=True)
    b_prefix = _stage59(
        tmp_path / "b-prefix.json", "b-prefix",
        [_arm("mix_ghost", 0.8, [_receipt("mix_ghost", 20, 0.8), _receipt("mix_ghost", 22, 0.9)])],
        imported=True, prefix=True,
    )
    manifest = _manifest(tmp_path, [
        {"path": a_reset.name, "condition": "reset", "stream_id": "stream-a"},
        {"path": a_global.name, "condition": "global", "stream_id": "stream-a"},
        {"path": a_prefix.name, "condition": "global_prefix", "stream_id": "stream-a"},
        {"path": b_reset.name, "condition": "reset", "stream_id": "stream-b"},
        {"path": b_global.name, "condition": "global", "stream_id": "stream-b"},
        {"path": b_prefix.name, "condition": "global_prefix", "stream_id": "stream-b"},
    ])
    gates = aggregate([], manifest=manifest)["machine"]["development_gates"]
    assert gates["matched_or_global_over_reset"] == "FAIL"
    assert gates["global_prefix_improves_suffix"] == "INSUFFICIENT_DATA"
    assert gates["overall"] == "FAIL"
