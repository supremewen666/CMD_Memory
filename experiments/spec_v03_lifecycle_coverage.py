"""Exercise Stage 6 deduplication and supersession using frozen candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cmd_audit.spec_v03.ecology_transfer_executor import (
    EcologyTransferExecutor,
    LifecycleCoverageCandidateProvider,
)
from cmd_audit.spec_v03.family_disjoint import select_runtime_splits
from cmd_audit.spec_v03.prequential_executor import RuntimeOrderManifest
from cmd_audit.spec_v03.runtime_bundle import load_runtime_cases
from cmd_audit.spec_v03.runtime_pipeline import RuntimePipeline
from cmd_audit.spec_v03.skill_discovery_provider import load_skill_library


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--skill-library", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260827)
    return parser.parse_args()


def _transition_counts(transitions: tuple[tuple[str, str], ...]) -> dict[str, int]:
    prefixes = ("seed", "birth", "dedup", "supersede", "retire")
    return {
        name: sum(transition.startswith(name) for _revision_id, transition in transitions)
        for name in prefixes
    }


def main() -> int:
    args = _args()
    discovered = load_skill_library(args.skill_library).skills
    parents = RuntimePipeline().frozen_skill_library
    attempts: list[dict[str, object]] = []

    for stream_dir in sorted(path for path in args.data_root.iterdir() if path.is_dir()):
        runtime_path = stream_dir / "runtime_cases.json"
        order_path = stream_dir / "event_order_manifest.json"
        split_path = stream_dir / "split_manifest.json"
        if not all(path.is_file() for path in (runtime_path, order_path, split_path)):
            continue
        bundles = load_runtime_cases(runtime_path)
        raw_order = json.loads(order_path.read_text(encoding="utf-8"))
        if not isinstance(raw_order, dict):
            raise ValueError(f"invalid event order: {order_path}")
        order = RuntimeOrderManifest.from_mapping(raw_order)
        bundles, order, split_audit = select_runtime_splits(
            bundles, order, split_path, ("D_lifecycle",),
        )
        dedup_provider = LifecycleCoverageCandidateProvider(discovered, mode="dedup")
        supersede_provider = LifecycleCoverageCandidateProvider(
            discovered, mode="supersede", parent_library=parents,
        )
        executor = EcologyTransferExecutor(model_id="frozen-lifecycle-coverage", seed=args.seed)
        dedup = executor.run_stage6(
            "add_dedup", bundles, order, candidate_provider=dedup_provider,
        )
        supersede = executor.run_stage6(
            "add_revision", bundles, order, candidate_provider=supersede_provider,
            frozen_library=parents,
        )
        counts = {
            "dedup_arm": _transition_counts(dedup.transitions),
            "supersede_arm": _transition_counts(supersede.transitions),
        }
        attempts.append({"stream": stream_dir.name, "counts": counts})
        if counts["dedup_arm"]["dedup"] and counts["supersede_arm"]["supersede"]:
            output = {
                "schema_version": "cmd-spec-v03-lifecycle-coverage-v1",
                "status": "COMPLETE",
                "evidence_scope": "mechanism_coverage_not_ecology_effectiveness",
                "stream": stream_dir.name,
                "split": "D_lifecycle",
                "dedup_trigger": dedup_provider.selected,
                "supersede_trigger": supersede_provider.selected,
                "counts": counts,
                "split_audit": split_audit,
                "results": {
                    "dedup": dedup.to_mapping(),
                    "supersede": supersede.to_mapping(),
                },
                "attempts": attempts,
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print("[RESULT] status=COMPLETE")
            print(f"[RESULT] stream={stream_dir.name}")
            print(f"[RESULT] dedup={counts['dedup_arm']['dedup']}")
            print(f"[RESULT] supersede={counts['supersede_arm']['supersede']}")
            print(f"[RESULT] output={args.output}")
            return 0
    raise RuntimeError("no D_lifecycle stream could trigger both dedup and supersede from the frozen library")


if __name__ == "__main__":
    raise SystemExit(main())
