"""Compile a small development-only CMD-RepairStream pilot from public data."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cmd_audit.spec_v03.event_order import compile_event_order
from cmd_audit.spec_v03.repair_stream import (
    ALL_TEMPLATES,
    build_intervention,
    build_shadow_matrix,
    compile_repair_case,
    iter_public_episodes,
    operator_catalog,
    supported_templates,
)
from cmd_audit.spec_v03.source_audit import audit_downloads
from cmd_audit.spec_v03.splits import SPLITS, build_lockbox_manifest, build_split_manifest


def _write_json(path: Path, value: object) -> str:
    from cmd_audit.spec_v03.contracts import canonical_sha256

    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return canonical_sha256(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("locomo", "halumem", "memfail", "memtracebench"), required=True)
    parser.add_argument("--seed", type=int, default=20260826)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--schedule", choices=("stationary", "abrupt_process_state_poison", "recurring_a_b_a"), default="stationary")
    parser.add_argument("--group-a-root", type=Path, default=Path("data/external/group_a"))
    parser.add_argument("--group-b-root", type=Path, default=Path("data/external/group_b"))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be positive")
    audit = audit_downloads(args.group_a_root, args.group_b_root)
    source_status = next((row for row in audit.datasets if row.dataset_id == args.source), None)
    if source_status is None or not source_status.executable:
        parser.error(f"source is not executable: {args.source}")
    episodes = []
    for episode in iter_public_episodes(args.source, args.group_a_root):
        episodes.append(episode)
        if len(episodes) >= args.limit:
            break
    episode_splits = {episode.episode_id: SPLITS[index % len(SPLITS)] for index, episode in enumerate(episodes)}
    template_partition = {template: SPLITS[index % len(SPLITS)] for index, template in enumerate(template for template in ALL_TEMPLATES if template != "clean")}
    cases = []
    unsupported: dict[str, dict[str, str]] = {}
    for episode in episodes:
        capability = supported_templates(episode)
        unsupported[episode.episode_id] = {key: value for key, value in capability.items() if value != "supported"}
        for template in ALL_TEMPLATES:
            if capability.get(template) != "supported":
                continue
            if template != "clean" and template_partition[template] != episode_splits[episode.episode_id]:
                continue
            cases.append(compile_repair_case(episode, build_intervention(episode, template, seed=args.seed)))
    matrices = [build_shadow_matrix(case) for case in cases]
    template_blocks = {
        case.case_id: (f"constructor:{case.intervention.template_id}",)
        for case in cases
        if case.intervention.template_id != "clean"
    }
    forced = {case.case_id: episode_splits[case.source_episode_id] for case in cases}
    split = build_split_manifest([case.decision_view for case in cases], seed=args.seed, extra_block_keys=template_blocks, forced_assignments=forced)
    lockbox = build_lockbox_manifest(split)
    order = compile_event_order(cases, seed=args.seed, schedule=args.schedule)
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    checksums = {
        "source_episodes.json": _write_json(output / "source_episodes.json", [episode.public_mapping() for episode in episodes]),
        "interventions.json": _write_json(output / "interventions.json", [case.intervention.to_mapping() for case in cases]),
        "runtime_cases.json": _write_json(output / "runtime_cases.json", [case.public_mapping() for case in cases]),
        "sealed_evaluator_sidecar.json": _write_json(output / "sealed_evaluator_sidecar.json", {"episodes": [episode.evaluator_mapping() for episode in episodes], "cases": [case.evaluator_mapping() for case in cases]}),
        "operator_catalog.json": _write_json(output / "operator_catalog.json", [operator.__dict__ for operator in operator_catalog()]),
        "event_order_manifest.json": _write_json(output / "event_order_manifest.json", order.to_mapping()),
        "shadow_outcome_matrix.json": _write_json(output / "shadow_outcome_matrix.json", [asdict(matrix) for matrix in matrices]),
        "split_manifest.json": _write_json(output / "split_manifest.json", split.to_mapping()),
        "lockbox_manifest.json": _write_json(output / "lockbox_manifest.json", lockbox.to_mapping()),
    }
    manifest = {
        "schema_version": "cmd-spec-v03-development-pilot-v1",
        "status": "DEVELOPMENT_PILOT_NOT_F_DATA_FROZEN",
        "source": args.source,
        "seed": args.seed,
        "source_episode_count": len(episodes),
        "case_count": len(cases),
        "matrix_count": len(matrices),
        "unsupported_capabilities": unsupported,
        "episode_split_assignment": episode_splits,
        "template_family_partition": template_partition,
        "source_audit_sha256": audit.report_sha256,
        "checksums": checksums,
    }
    _write_json(output / "pilot_manifest.json", manifest)
    print(f"[RESULT] status={manifest['status']}")
    print(f"[RESULT] source_episodes={len(episodes)}")
    print(f"[RESULT] repair_cases={len(cases)}")
    print(f"[RESULT] output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
