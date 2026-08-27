"""CLI for the result-free Group B experiment evaluation layer.

The input is deliberately an executor-produced JSON list.  It contains only
per-arm receipts/outcomes; this runner does not open artifacts, datasets, or
historical metric summaries.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .mix_ghost_ecology_repair import (
    ArmOutcome,
    BlockedData,
    ECOLOGY_ARMS,
    GroupBConfig,
    GroupBExperimentRunner,
    REPAIR_ARMS,
    ROUTER_ARMS,
    FreezeManifest,
    SeedOrderManifest,
    STAGE_ARMS,
    StageExecutionConfig,
    plan_stage_execution,
)


def _arms(track: str) -> tuple[str, ...]:
    return {"mix_ghost_routing": ROUTER_ARMS, "skill_ecology": ECOLOGY_ARMS, "safe_memory_repair": REPAIR_ARMS}[track]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--track", choices=("mix_ghost_routing", "skill_ecology", "safe_memory_repair"))
    parser.add_argument("--input", type=Path, help="executor-produced outcome JSON; not an artifacts file")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lambda-locality", type=float, default=0.0)
    parser.add_argument("--lambda-compute", type=float, default=0.0)
    parser.add_argument(
        "--stage", choices=tuple(STAGE_ARMS),
        help="plan a Stage 5--8 run; requires freeze and seed/order manifests",
    )
    parser.add_argument("--freeze-manifest", type=Path)
    parser.add_argument("--seed-order-manifest", type=Path)
    parser.add_argument("--source-residual-snapshot-sha256")
    parser.add_argument("--target-prefix-split")
    parser.add_argument("--adapter-available", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.stage:
        if not args.dry_run:
            parser.error("Stage 5--8 execution is adapter-owned; use --dry-run to emit a verified plan")
        if args.freeze_manifest is None or args.seed_order_manifest is None:
            parser.error("--stage requires --freeze-manifest and --seed-order-manifest")
        freeze = FreezeManifest.from_mapping(json.loads(args.freeze_manifest.read_text(encoding="utf-8")))
        seed_order = SeedOrderManifest.from_mapping(json.loads(args.seed_order_manifest.read_text(encoding="utf-8")))
        stage = StageExecutionConfig(
            args.stage, STAGE_ARMS[args.stage], "T_online",
            args.stage == "stage5_router",
            args.source_residual_snapshot_sha256,
            args.target_prefix_split,
        )
        plan = plan_stage_execution(stage, freeze, seed_order, adapter_available=args.adapter_available)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(plan.to_mapping(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"[RESULT] status={plan.status}")
        print(f"[RESULT] plan_sha256={plan.plan_sha256}")
        return 0 if plan.status == "READY" else 3
    if args.input is None:
        parser.error("result aggregation requires --input")
    if args.track is None:
        parser.error("result aggregation requires --track")
    raw = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) - {"outcomes", "blocked"}:
        raise ValueError("input must be a closed mapping with outcomes and optional blocked")
    outcomes = tuple(ArmOutcome(**row) for row in raw.get("outcomes", ()))
    blocked = tuple(BlockedData(**row) for row in raw.get("blocked", ()))
    config = GroupBConfig(args.track, _arms(args.track), args.lambda_locality, args.lambda_compute)
    report = GroupBExperimentRunner(config).write(args.output, outcomes, blocked=blocked)
    print(f"[RESULT] status={report.status}")
    print(f"[RESULT] report_sha256={report.report_sha256}")
    print(f"[RESULT] skipped={len(report.skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
