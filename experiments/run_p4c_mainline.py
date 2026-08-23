"""Plan or verify the paper-mainline gold-free P4C experiment bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from experiments.run_p4c1_real_sources import (
    P4C1_MANIFEST_SCHEMA,
    SESSION_PROJECTION_SCHEMA,
)
from experiments.run_p4c45_prequential_v2 import REPORT_SCHEMA as P4C45_REPORT_SCHEMA


SCHEMA = "cmd-p4c-mainline-program-v1"
PRIMARY_CLAIM = "gold-free memory fault correction and evolution"
DEFAULT_P4C1 = Path("artifacts/experiments/p4c1_real_sources_full_v2")
DEFAULT_P4C3 = Path("artifacts/experiments/p4c3_native_detection_full_v2")
DEFAULT_P4C45 = Path("artifacts/experiments/p4c45_prequential_v2")


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _object(path: Path, name: str) -> Mapping[str, object]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name} is unavailable or invalid: {path}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def build_plan() -> dict[str, object]:
    return {
        "schema_version": SCHEMA,
        "mode": "plan",
        "paper_role": "mainline",
        "primary_claim": PRIMARY_CLAIM,
        "external_calls_authorized": False,
        "runtime_gold_free": True,
        "router_feedback": "EccRepairReceipt-only",
        "mainline_stages": [
            {
                "stage": "p4c1",
                "evidence": "real-source structural correction receipts",
            },
            {
                "stage": "p4c3",
                "evidence": "native detection, abstention, and false-repair audit",
            },
            {
                "stage": "p4c45",
                "evidence": "router/ECC ablation, evolution, and robustness",
            },
        ],
        "supplementary_stages": ["p4c2", "p4c6"],
        "legacy_stages": ["legacy-answer"],
        "excluded_mainline_claims": [
            "legacy LongMemEval answer quality",
            "same-trace answer replay",
            "dataset-label router reward",
        ],
    }


def verify(*, p4c1_run: Path, p4c3_run: Path, p4c45_run: Path) -> dict[str, object]:
    p4c1_path = Path(p4c1_run) / "p4c1_manifest.json"
    p4c3_runtime_path = Path(p4c3_run) / "runtime_manifest.json"
    p4c3_audit_path = Path(p4c3_run) / "detector_audit.json"
    p4c45_path = Path(p4c45_run) / "p4c45_manifest.json"
    p4c1 = _object(p4c1_path, "P4C-1 mainline manifest")
    p4c3_runtime = _object(p4c3_runtime_path, "P4C-3 runtime manifest")
    p4c3_audit = _object(p4c3_audit_path, "P4C-3 audit")
    p4c45 = _object(p4c45_path, "P4C-4/5 manifest")
    source_counts = p4c1.get("source_counts")
    p4c45_arms = p4c45.get("arms")
    ghost_holdout_frozen = isinstance(p4c45_arms, Mapping) and all(
        isinstance(p4c45_arms.get(arm), Mapping)
        and p4c45_arms[arm].get("holdout_router_updates") == 0
        for arm in (
            "ghost_zero_frozen",
            "ghost_zero_evolution",
            "ghost_typed_prior_frozen",
            "ghost_typed_prior_evolution",
        )
    )
    if not (
        p4c1.get("schema_version") == P4C1_MANIFEST_SCHEMA
        and p4c1.get("status") == "success"
        and p4c1.get("paper_role") == "mainline"
        and p4c1.get("primary_claim") == PRIMARY_CLAIM
        and p4c1.get("runtime_uses_gold") is False
        and p4c1.get("runtime_uses_labels") is False
        and p4c1.get("router_feedback") == "EccRepairReceipt"
        and p4c1.get("session_projection_schema") == SESSION_PROJECTION_SCHEMA
        and isinstance(source_counts, Mapping)
        and int(source_counts.get("longmemeval", 0)) >= 500
        and int(source_counts.get("memfail", 0)) >= 92
        and int(source_counts.get("poison_sweep", 0)) >= 92
    ):
        raise ValueError("P4C-1 is not eligible for the mainline claim")
    if not (
        p4c3_runtime.get("status") == "prediction_sealed"
        and p4c3_runtime.get("paper_role") == "mainline"
        and p4c3_runtime.get("runtime_gold_free") is True
        and p4c3_runtime.get("external_call_count") == 0
        and p4c3_audit.get("paper_role") == "mainline"
        and p4c3_audit.get("runtime_feedback_written") is False
        and int(p4c3_runtime.get("case_count", 0)) >= 1368
        and int(p4c3_runtime.get("syndrome_count", 0)) >= 684
        and int(p4c3_runtime.get("abstain_count", 0)) >= 684
    ):
        raise ValueError("P4C-3 is not eligible for the mainline claim")
    if not (
        p4c45.get("schema_version") == P4C45_REPORT_SCHEMA
        and p4c45.get("holdout_update_policy") == "frozen_no_observe"
        and p4c45.get("status") == "success"
        and p4c45.get("paper_role") == "mainline"
        and p4c45.get("primary_claim") == PRIMARY_CLAIM
        and p4c45.get("runtime_uses_gold") is False
        and p4c45.get("runtime_uses_labels") is False
        and p4c45.get("router_implementation") == "GHOSTEcologyRouter"
        and p4c45.get("router_feedback_channel")
        == "GHOSTEcologyRouter.observe_receipt(EccRepairReceipt) only"
        and isinstance(p4c45.get("metric_semantics"), Mapping)
        and p4c45["metric_semantics"].get("primary")
        == "safe_committed_resolution_per_incident"
        and int(p4c45.get("case_count", 0)) >= 600
        and int(p4c45.get("outcome_count", 0)) >= 4800
        and isinstance(p4c45.get("phase_case_counts"), Mapping)
        and int(p4c45["phase_case_counts"].get("holdout", 0)) >= 240
        and ghost_holdout_frozen
    ):
        raise ValueError("P4C-4/5 is not eligible for the mainline claim")
    return {
        **build_plan(),
        "mode": "verify",
        "status": "mainline_evidence_ready",
        "roots": {
            "p4c1_manifest_sha256": _sha(p4c1_path),
            "p4c3_runtime_manifest_sha256": _sha(p4c3_runtime_path),
            "p4c3_audit_sha256": _sha(p4c3_audit_path),
            "p4c45_manifest_sha256": _sha(p4c45_path),
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Zero-call planner/verifier for the primary claim: gold-free memory "
            "fault correction and evolution. Answer-quality runs are supplementary "
            "or legacy and have no commit authority."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="show the mainline plan (default)")
    mode.add_argument("--verify", action="store_true", help="verify completed mainline manifests")
    parser.add_argument("--p4c1-run", type=Path, default=DEFAULT_P4C1)
    parser.add_argument("--p4c3-run", type=Path, default=DEFAULT_P4C3)
    parser.add_argument("--p4c45-run", type=Path, default=DEFAULT_P4C45)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        result = (
            verify(
                p4c1_run=args.p4c1_run,
                p4c3_run=args.p4c3_run,
                p4c45_run=args.p4c45_run,
            )
            if args.verify
            else build_plan()
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
