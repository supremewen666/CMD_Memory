"""Evaluator-isolated scoring for frozen Stage 5 selections and Stage 7 governance."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
import json
from pathlib import Path
from typing import Mapping, Sequence

from .contracts import canonical_sha256
from .governance_system_executor import (
    ExecutionOrder,
    GovernanceRecord,
    GovernanceSystemExecutor,
    STAGE7_VARIANTS,
)
from .industry_adapters import AdapterRequest, AdapterResponse, ResourceUsage
from .prequential_executor import RuntimeOrderManifest
from .runtime_bundle import RuntimeBundle


class FrozenSelectionProposalProvider:
    """Replay model choices without another model call or evaluator access."""

    capability_id = "frozen-stage5-selection-replay-v1"

    def __init__(self, operators_by_case: Mapping[str, str]) -> None:
        if not operators_by_case:
            raise ValueError("frozen selection replay requires at least one operator")
        self._operators = dict(operators_by_case)

    def invoke(self, request: AdapterRequest) -> AdapterResponse:
        case_id = request.decision.get("case_id")
        operator_id = self._operators.get(str(case_id))
        if operator_id is None:
            return AdapterResponse(
                "FAILED", None, "frozen_selection_missing", ResourceUsage.zero(), self.capability_id,
            ).verify_for(request)
        if operator_id not in request.legal_operator_ids:
            return AdapterResponse(
                "FAILED", None, "frozen_selection_outside_runtime_mask", ResourceUsage.zero(), self.capability_id,
            ).verify_for(request)
        return AdapterResponse(
            "OK", operator_id, None, ResourceUsage.zero(), self.capability_id,
        ).verify_for(request)


class SealedCaseOracleProvider:
    """Explicit evaluator-owned oracle upper bound; never used by non-oracle arms."""

    sealed = True
    capability_id = "sealed-case-oracle-v1"

    def __init__(self, legal_by_case: Mapping[str, Sequence[str]]) -> None:
        self._legal = {case_id: tuple(values) for case_id, values in legal_by_case.items()}

    def invoke(self, request: AdapterRequest) -> AdapterResponse:
        case_id = str(request.decision.get("case_id"))
        candidates = sorted(set(self._legal.get(case_id, ())) & set(request.legal_operator_ids))
        if not candidates:
            return AdapterResponse(
                "FAILED", None, "sealed_oracle_has_no_runtime_legal_action",
                ResourceUsage.zero(), self.capability_id,
            ).verify_for(request)
        return AdapterResponse(
            "OK", candidates[0], None, ResourceUsage.zero(), self.capability_id,
        ).verify_for(request)


def frozen_operators_from_stage5_report(
    report_path: str | Path,
    operator_by_skill_revision: Mapping[str, str],
    *,
    arm: str = "mix_ghost",
) -> dict[str, str]:
    raw = json.loads(Path(report_path).read_text(encoding="utf-8"))
    stage5 = raw.get("results", {}).get("stage5") if isinstance(raw, Mapping) else None
    arms = stage5.get("arms") if isinstance(stage5, Mapping) else None
    if not isinstance(arms, list):
        raise ValueError("selection report lacks Stage 5 arms")
    selected_arm = next(
        (row for row in arms if isinstance(row, Mapping) and row.get("arm") == arm and row.get("status") == "COMPLETE"),
        None,
    )
    if not isinstance(selected_arm, Mapping) or not isinstance(selected_arm.get("selection_records"), list):
        raise ValueError("selection report lacks a complete requested arm")
    result: dict[str, str] = {}
    for row in selected_arm["selection_records"]:
        if not isinstance(row, Mapping) or row.get("selected_skill_revision_id") is None:
            continue
        case_id = row.get("case_id")
        skill_id = row.get("selected_skill_revision_id")
        if not isinstance(case_id, str) or not isinstance(skill_id, str):
            raise ValueError("selection record has invalid identities")
        operator_id = operator_by_skill_revision.get(skill_id)
        if operator_id is None:
            raise ValueError(f"selection references an unknown skill revision: {skill_id}")
        previous = result.setdefault(case_id, operator_id)
        if previous != operator_id:
            raise ValueError("selection report chooses multiple operators for one case")
    if not result:
        raise ValueError("selection report contains no replayable choices")
    return result


def load_sealed_cases(path: str | Path) -> dict[str, dict[str, object]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = raw.get("cases") if isinstance(raw, Mapping) else None
    if not isinstance(cases, list):
        raise ValueError("sealed evaluator sidecar lacks cases")
    result: dict[str, dict[str, object]] = {}
    for row in cases:
        evaluator = row.get("evaluator_only") if isinstance(row, Mapping) else None
        if not isinstance(row, Mapping) or not isinstance(row.get("case_id"), str) or not isinstance(evaluator, Mapping):
            raise ValueError("sealed evaluator case has an invalid schema")
        oracle = evaluator.get("safety_oracle")
        legal = evaluator.get("legal_operator_ids")
        if not isinstance(oracle, Mapping) or not isinstance(oracle.get("expected_state_root"), str):
            raise ValueError("sealed evaluator case lacks expected state root")
        if not isinstance(legal, list) or any(not isinstance(item, str) for item in legal):
            raise ValueError("sealed evaluator case lacks legal operators")
        result[str(row["case_id"])] = {
            "incident_type": evaluator.get("incident_type"),
            "expected_state_root": oracle["expected_state_root"],
            "legal_operator_ids": tuple(legal),
        }
    return result


def execute_governance_replay(
    bundles: Sequence[RuntimeBundle],
    order: RuntimeOrderManifest,
    frozen_provider: FrozenSelectionProposalProvider,
    sealed_cases: Mapping[str, Mapping[str, object]],
    *,
    run_id: str,
) -> tuple[GovernanceRecord, ...]:
    by_id = {bundle.case_id: bundle for bundle in bundles}
    if set(by_id) != {row.case_id for row in order.rows}:
        raise ValueError("governance replay bundles and order differ")
    missing = set(by_id) - set(sealed_cases)
    if missing:
        raise ValueError("sealed sidecar is missing scored runtime cases")
    oracle = SealedCaseOracleProvider({
        case_id: tuple(values["legal_operator_ids"])  # type: ignore[arg-type]
        for case_id, values in sealed_cases.items()
    })
    executor = GovernanceSystemExecutor()
    records = []
    for row in order.rows:
        execution_order = ExecutionOrder(row.event_index, row.cas_interleaving == "conflicting")
        for variant in sorted(STAGE7_VARIANTS):
            records.append(executor.execute_stage7(
                by_id[row.case_id], execution_order,
                None if variant == "oracle_repair" else frozen_provider,
                variant=variant,
                run_id=run_id,
                oracle_provider=oracle if variant == "oracle_repair" else None,
            ))
    return tuple(records)


def score_governance_records(
    records: Sequence[GovernanceRecord],
    sealed_cases: Mapping[str, Mapping[str, object]],
    family_by_case: Mapping[str, str],
) -> dict[str, object]:
    rows = []
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        truth = sealed_cases[record.case_id]
        incident = str(truth["incident_type"])
        legal = set(truth["legal_operator_ids"])  # type: ignore[arg-type]
        root_corrected = record.after_root == truth["expected_state_root"]
        selected_legal = record.selected_operator_id is None or record.selected_operator_id in legal
        gates = all(value is True for value in (
            record.invariant_passed, record.safety_passed, record.locality_passed,
        ))
        safe_success = incident != "clean" and record.committed and root_corrected and selected_legal and gates
        false_commit = record.committed and not (root_corrected and selected_legal and gates)
        clean_false_repair = incident == "clean" and record.committed
        row = {
            "variant": record.variant,
            "case_id": record.case_id,
            "family_id": family_by_case[record.case_id],
            "incident_type": incident,
            "selected_operator_id": record.selected_operator_id,
            "committed": record.committed,
            "root_corrected": root_corrected,
            "selected_operator_legal": selected_legal,
            "safe_repair_success": safe_success,
            "false_commit": false_commit,
            "clean_false_repair": clean_false_repair,
            "invariant_passed": record.invariant_passed,
            "safety_passed": record.safety_passed,
            "locality_passed": record.locality_passed,
            "locality_cost": record.locality_cost,
            "cas_conflicted": record.cas_conflicted,
            "receipt_valid": record.receipt_provenance is not None,
            "status": record.status,
        }
        rows.append(row)
        grouped[record.variant].append(row)

    def rate(values: Sequence[bool]) -> float | None:
        return sum(values) / len(values) if values else None

    metrics = []
    for variant, values in sorted(grouped.items()):
        incidents = [row for row in values if row["incident_type"] != "clean"]
        clean = [row for row in values if row["incident_type"] == "clean"]
        commits = [row for row in values if row["committed"]]
        metrics.append({
            "variant": variant,
            "case_count": len(values),
            "incident_count": len(incidents),
            "safe_repair_success_rate": rate([bool(row["safe_repair_success"]) for row in incidents]),
            "false_commit_rate": rate([bool(row["false_commit"]) for row in commits]),
            "clean_false_repair_rate": rate([bool(row["clean_false_repair"]) for row in clean]),
            "commit_rate": rate([bool(row["committed"]) for row in values]),
            "root_correction_rate": rate([bool(row["root_corrected"]) for row in incidents]),
            "cas_conflict_rate": rate([bool(row["cas_conflicted"]) for row in values]),
            "mean_locality_cost": (
                sum(float(row["locality_cost"]) for row in values if row["locality_cost"] is not None)
                / len([row for row in values if row["locality_cost"] is not None])
                if any(row["locality_cost"] is not None for row in values) else None
            ),
        })
    body = {
        "schema_version": "cmd-spec-v03-sealed-governance-score-v1",
        "evaluator_boundary": "sealed sidecar used only by oracle arm and post-execution scorer",
        "rows": rows,
        "variant_metrics": metrics,
    }
    return {**body, "report_sha256": canonical_sha256(body)}
