"""Audit live item-gate shadow events and write the Stage-1 scope ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable, Mapping

from cmd_audit.eval.scope_audit import (
    ScopeAuditObservation,
    audit_scope_signals,
    write_scope_audit_events,
)
from cmd_audit.repair.scope_ledger import ScopeLedger


LIVE_ITEM_GATE_SURFACE = "tier2_item_gate"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="+", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--generation", type=int, default=1)
    parser.add_argument("--n-min", type=int, default=30)
    parser.add_argument("--validity-threshold", type=float, default=0.8)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--bootstrap-seed", type=int, default=24)
    args = parser.parse_args()

    output = Path(args.output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    ledger = ScopeLedger(
        threshold=args.validity_threshold,
        n_min=args.n_min,
        bootstrap_samples=args.bootstrap_samples,
        seed=args.bootstrap_seed,
    )
    all_events = []
    input_summaries = []
    for offset, raw_path in enumerate(args.inputs):
        source = Path(raw_path).expanduser().resolve()
        manifest, observations, indications = load_live_scope_input(source)
        dataset_path = Path(str(manifest["dataset_source_path"]))
        rows = build_scope_observations(
            observations,
            indications,
            domain_fingerprint=str(manifest["arena_id"]),
        )
        if not indications:
            extractor_manifest = str(
                manifest.get("structural_extractor_version") or ""
            )
            if "live-item-gate" not in extractor_manifest:
                raise ValueError(
                    f"{source}: manifest does not prove live item-gate execution"
                )
            input_summaries.append(
                {
                    "arena_id": manifest["arena_id"],
                    "artifact_path": str(source),
                    "artifact_sha256": file_sha256(source),
                    "dataset_path": str(dataset_path.resolve()),
                    "live_item_gate_events": 0,
                    "audit_observations": 0,
                    "audit_decisions": ["no_live_signal_fired"],
                }
            )
            continue
        extractor_versions = sorted(
            {
                str(row["extractor_version"])
                for row in indications
                if row.get("runtime_surface") == LIVE_ITEM_GATE_SURFACE
            }
        )
        allowlist_hashes = sorted(
            {
                str(row["input_allowlist_sha256"])
                for row in indications
                if row.get("runtime_surface") == LIVE_ITEM_GATE_SURFACE
            }
        )
        if len(extractor_versions) != 1 or len(allowlist_hashes) != 1:
            raise ValueError(
                f"{source}: live item-gate provenance must be single-version"
            )
        provenance = {
            "runtime_uses_gold": manifest.get("runtime_uses_gold"),
            "uses_injection_control": False,
            "input_allowlist_sha256": allowlist_hashes[0],
            "extractor_version": extractor_versions[0],
            "evaluator_identity": manifest.get(
                "evaluation_judge_identity"
            ),
            "arena_artifact_path": str(source),
            "arena_artifact_sha256": file_sha256(source),
            "created_before_outcome": all(
                row.get("created_before_outcome") is True
                for row in indications
                if row.get("runtime_surface") == LIVE_ITEM_GATE_SURFACE
            ),
        }
        if provenance["runtime_uses_gold"] is not False:
            raise ValueError(f"{source}: runtime_uses_gold must be false")
        if provenance["created_before_outcome"] is not True:
            raise ValueError(
                f"{source}: item-gate events must precede outcome scoring"
            )
        events = audit_scope_signals(
            rows,
            ledger=ledger,
            generation=args.generation + offset,
            dataset_path=dataset_path,
            provenance=provenance,
        )
        all_events.extend(events)
        input_summaries.append(
            {
                "arena_id": manifest["arena_id"],
                "artifact_path": str(source),
                "artifact_sha256": file_sha256(source),
                "dataset_path": str(dataset_path.resolve()),
                "live_item_gate_events": len(indications),
                "audit_observations": len(rows),
                "audit_decisions": [event.decision for event in events],
            }
        )

    event_path = write_scope_audit_events(
        all_events,
        output / "scope_audit_events.jsonl",
        append=False,
    )
    ledger_path = ledger.write(output / "scope_ledger.json")
    latest_event = {
        (event.signal_type, event.domain_fingerprint): event
        for event in all_events
    }
    active = [
        {
            "signal_type": entry.signal_type,
            "domain_fingerprint": entry.domain_fingerprint,
            "validity": entry.audited_validity,
            "ci_lower": latest_event[
                (entry.signal_type, entry.domain_fingerprint)
            ].ci_lower,
            "mean_incremental_gain": entry.mean_incremental_gain,
        }
        for entry in ledger.entries()
        if entry.status == "active"
    ]
    summary = {
        "protocol": "sigil-stage1-live-item-gate-v1",
        "inputs": input_summaries,
        "scope_audit_events": str(event_path),
        "scope_ledger": str(ledger_path),
        "active_scopes": active,
        "stage1_gate_passed": bool(active),
        "stop_stage2_and_stage3": not bool(active),
        "ledger": ledger.to_dict(),
    }
    summary_path = output / "stage1_summary.json"
    summary_path.write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print("[RESULT] protocol=sigil-stage1-live-item-gate-v1")
    print(f"[RESULT] inputs={len(input_summaries)}")
    print(f"[RESULT] audit_events={len(all_events)}")
    print(f"[RESULT] active_scopes={len(active)}")
    print(f"[RESULT] stage1_gate_passed={int(bool(active))}")
    print(f"[RESULT] scope_ledger={ledger_path}")
    print(f"[RESULT] summary={summary_path}")
    return 0


def load_live_scope_input(
    path: Path,
) -> tuple[
    Mapping[str, object],
    tuple[Mapping[str, object], ...],
    tuple[Mapping[str, object], ...],
]:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows = tuple(
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    manifests = tuple(
        row for row in rows if row.get("record_type") == "arena_manifest"
    )
    if len(manifests) != 1:
        raise ValueError(f"{path}: expected exactly one arena manifest")
    observations = tuple(
        row
        for row in rows
        if row.get("record_type") == "gold_free_observation"
    )
    indications = tuple(
        row
        for row in rows
        if row.get("record_type") == "structural_indication_event"
        and row.get("runtime_surface") == LIVE_ITEM_GATE_SURFACE
    )
    if not observations:
        raise ValueError(f"{path}: no gold-free observations")
    return manifests[0], observations, indications


def build_scope_observations(
    observations: Iterable[Mapping[str, object]],
    indications: Iterable[Mapping[str, object]],
    *,
    domain_fingerprint: str,
) -> tuple[ScopeAuditObservation, ...]:
    by_case = {
        str(row["case_id"]): row
        for row in observations
    }
    output = []
    for indication in indications:
        case_id = str(indication["case_id"])
        try:
            observation = by_case[case_id]
        except KeyError as exc:
            raise ValueError(
                f"item-gate indication has no outcome row: {case_id}"
            ) from exc
        shadow_scores = dict(observation.get("shadow_gold_scores") or ())
        action = str(indication["action"])
        action_gain = optional_finite(shadow_scores.get(f"seed:{action}"))
        oracle_values = tuple(
            value
            for raw in shadow_scores.values()
            if (value := optional_finite(raw)) is not None
        )
        output.append(
            ScopeAuditObservation(
                case_id=case_id,
                signal_type=str(indication["signal_type"]),
                domain_fingerprint=domain_fingerprint,
                indication_action=action,
                indication_gain=action_gain,
                oracle_gain=max(oracle_values) if oracle_values else None,
                frozen_gain=optional_finite(
                    observation.get("selected_shadow_gain")
                ),
                family_id=str(
                    observation.get("family_id") or case_id
                ),
                evidence_ids=tuple(
                    str(value)
                    for value in indication.get("evidence_ids", ())
                ),
            )
        )
    return tuple(output)


def optional_finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
