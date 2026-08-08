#!/usr/bin/env python3
"""Route A E0b: exact depth <= 2 IR envelope and behavior fingerprints (§6.4).

Enumerates the registered shallow IR space, fingerprints each program against
the frozen 64-probe suite, collapses behaviorally identical programs, and
freezes the result. §15 permits E3 only after this has run and the pre-search
envelope is frozen.

Writes the five §13 `e0b/` artifacts plus `presearch_envelope_manifest.json`.
The pre-search envelope is the behaviorally deduplicated union of the closed
grammar, the initial population, and this enumeration, so it is only complete
when E0's fingerprints are on disk; without them this command writes the
shallow half and records the union as pending rather than claiming a union it
did not compute.

Fingerprinting the full space costs roughly `enumerated_count * 1.7 ms`, so the
default run is minutes, not seconds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.counterfactual.behavior_fingerprint import (
    PROBE_SUITE_VERSION,
    neutral_probe_suite,
    probe_manifest,
    probe_suite_sha256,
)
from cmd_audit.counterfactual.program_ir import (
    IR_GRAMMAR_VERSION,
    REGISTERED_BOUNDS,
    program_action_count,
    program_depth,
    program_node_count,
    program_to_mapping,
)
from cmd_audit.counterfactual.repair_state import initial_state_from_runtime_case
from cmd_audit.counterfactual.shallow_ir_enumerator import (
    SHALLOW_ENVELOPE_VERSION,
    SHALLOW_SEQUENCE_LIMIT,
    count_shallow_ir_space,
    enumerate_shallow_programs,
    shallow_envelope,
    shallow_grammar_manifest,
)
from cmd_audit.counterfactual.state_executor import (
    ExecutionLimitError,
    execute_program,
)
from cmd_audit.counterfactual.program_ir import (
    IdentityActionError,
    ProgramBoundsError,
)

OUTPUT_DIR = Path("artifacts/route_a/e0b")
PROBE_DIR = Path("artifacts/route_a/probes")
E0_FINGERPRINTS = Path("artifacts/route_a/e0/closed_behavior_fingerprints.jsonl")
PROTOCOL_VERSION = "route-a-state-fitness-open-synthesis-v1"


def code_revision() -> str:
    """Digest of the modules that define the space, for the artifact header."""
    digest = hashlib.sha256()
    for name in (
        "cmd_audit/counterfactual/program_ir.py",
        "cmd_audit/counterfactual/behavior_fingerprint.py",
        "cmd_audit/counterfactual/shallow_ir_enumerator.py",
        "cmd_audit/counterfactual/state_executor.py",
    ):
        digest.update(Path(name).read_bytes())
    return digest.hexdigest()


def _state_row(program, probe) -> dict[str, object]:
    """One program's observed state outcome on one probe."""
    state = initial_state_from_runtime_case(probe.case)
    try:
        result = execute_program(program, probe.case, state)
    except (ExecutionLimitError, ProgramBoundsError, IdentityActionError) as error:
        return {"probe_id": probe.probe_id, "error": type(error).__name__}
    return {
        "probe_id": probe.probe_id,
        "state_hash": result.state.state_hash,
        "matched_item_count": result.matched_item_count,
        "retrieved_additions": result.retrieved_additions,
        "token_delta": result.token_delta,
        "logical_cost": result.logical_cost,
        "abstained": result.abstained,
        "fired_rules": result.fired_rules,
    }


def load_closed_fingerprints(path: Path) -> tuple[str, ...] | None:
    """E0's behavior fingerprints, or None when E0 has not been materialized."""
    if not path.exists():
        return None
    fingerprints: list[str] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        value = row.get("behavior_fingerprint")
        if value:
            fingerprints.append(value)
    return tuple(fingerprints)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sequence-limit",
        type=int,
        default=SHALLOW_SEQUENCE_LIMIT,
        help="rules per enumerated sequence (registered truncation)",
    )
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--probe-dir", type=Path, default=PROBE_DIR)
    parser.add_argument(
        "--closed-fingerprints",
        type=Path,
        default=E0_FINGERPRINTS,
        help="E0 fingerprint JSONL; the union is recorded as pending if absent",
    )
    parser.add_argument(
        "--state-matrix-probes",
        type=int,
        default=8,
        help=(
            "probes per program in the state matrix. The full 64 would write "
            "enumerated_count * 64 rows; the manifest records what was used."
        ),
    )
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.probe_dir.mkdir(parents=True, exist_ok=True)

    header = {
        "protocol_version": PROTOCOL_VERSION,
        "shallow_envelope_version": SHALLOW_ENVELOPE_VERSION,
        "ir_grammar_version": IR_GRAMMAR_VERSION,
        "probe_suite_version": PROBE_SUITE_VERSION,
        "probe_suite_sha256": probe_suite_sha256(),
        "code_revision": code_revision(),
        "runtime_uses_gold": False,
        "llm_calls": 0,
        "seed": None,
    }

    # -- probe manifest (§13 probes/) -------------------------------------
    probes = neutral_probe_suite()
    manifest = probe_manifest()
    manifest.update(header)
    (args.probe_dir / "neutral_probe_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    coverage = manifest["coverage"]
    lines = ["section,key,count"]
    for section in (
        "predicate_true",
        "predicate_false",
        "action_applied",
        "action_noop",
        "action_fail_closed",
        "threshold_values",
        "families",
    ):
        for key, count in sorted(coverage[section].items()):
            lines.append(f"{section},{key},{count}")
    (args.probe_dir / "neutral_probe_coverage.csv").write_text(
        "\n".join(lines) + "\n"
    )

    # -- grammar manifest -------------------------------------------------
    grammar = shallow_grammar_manifest(args.sequence_limit)
    grammar.update(header)
    (args.output_dir / "shallow_ir_grammar_manifest.json").write_text(
        json.dumps(grammar, indent=2, sort_keys=True) + "\n"
    )
    expected = count_shallow_ir_space(args.sequence_limit)
    print(
        f"enumerating {expected:,} programs "
        f"(sequence_limit={args.sequence_limit}, "
        f"omitted={grammar['omitted_program_count']:,})"
    )

    # -- specs + state matrix --------------------------------------------
    matrix_probes = probes[: max(0, args.state_matrix_probes)]
    started = time.perf_counter()
    specs_path = args.output_dir / "shallow_ir_specs.jsonl"
    matrix_path = args.output_dir / "shallow_ir_state_matrix.jsonl"
    written = 0
    with specs_path.open("w") as specs, matrix_path.open("w") as matrix:
        for index, program in enumerate(enumerate_shallow_programs(args.sequence_limit)):
            specs.write(
                json.dumps(
                    {
                        "index": index,
                        "depth": program_depth(program),
                        "nodes": program_node_count(program),
                        "actions": program_action_count(program),
                        "program": program_to_mapping(program),
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            for probe in matrix_probes:
                matrix.write(
                    json.dumps(
                        {"index": index, **_state_row(program, probe)}, sort_keys=True
                    )
                    + "\n"
                )
            written += 1
    if written != expected:
        print(f"FAIL: generated {written} programs, analytic count is {expected}")
        return 1
    print(f"wrote {written:,} specs in {time.perf_counter() - started:.1f}s")

    # -- behavioral collapse ---------------------------------------------
    started = time.perf_counter()
    envelope = shallow_envelope(args.sequence_limit)
    elapsed = time.perf_counter() - started
    with (args.output_dir / "shallow_ir_behavior_fingerprints.jsonl").open("w") as out:
        for member in envelope.members:
            out.write(json.dumps(member.as_mapping(), sort_keys=True) + "\n")

    result = envelope.as_mapping()
    result.update(header)
    result["fingerprint_seconds"] = round(elapsed, 3)
    (args.output_dir / "shallow_ir_envelope_result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    print(
        f"collapsed {envelope.enumerated_count:,} -> "
        f"{envelope.behavior_class_count:,} behavior classes "
        f"({envelope.collapsed_count:,} duplicates) in {elapsed:.1f}s"
    )

    # -- pre-search envelope (§6.4) --------------------------------------
    shallow = {member.behavior_fingerprint for member in envelope.members}
    closed = load_closed_fingerprints(args.closed_fingerprints)
    presearch: dict[str, object] = dict(header)
    presearch["shallow_behavior_class_count"] = len(shallow)
    presearch["shallow_envelope_sha256"] = envelope.envelope_sha256()
    presearch["sequence_limit"] = args.sequence_limit
    if closed is None:
        # §6.4's envelope is a union over three sources. Writing a union digest
        # from one of them would freeze a baseline that E5 later compares
        # against, so the gap is recorded instead of papered over.
        presearch["status"] = "pending_closed_grammar"
        presearch["union_behavior_class_count"] = None
        presearch["union_sha256"] = None
        presearch["missing_input"] = str(args.closed_fingerprints)
        print(
            f"\nPRE-SEARCH ENVELOPE PENDING: {args.closed_fingerprints} absent, "
            "so the union of closed grammar + initial population + shallow IR "
            "is not frozen. Run E0 first, then re-run this command."
        )
        exit_code = 1
    else:
        union = shallow | set(closed)
        presearch["status"] = "frozen"
        presearch["closed_behavior_class_count"] = len(set(closed))
        presearch["union_behavior_class_count"] = len(union)
        presearch["union_sha256"] = hashlib.sha256(
            json.dumps(sorted(union), sort_keys=True).encode("utf-8")
        ).hexdigest()
        print(
            f"\npre-search envelope frozen: {len(union):,} behavior classes "
            f"({len(set(closed)):,} closed + {len(shallow):,} shallow)"
        )
        exit_code = 0
    (args.output_dir / "presearch_envelope_manifest.json").write_text(
        json.dumps(presearch, indent=2, sort_keys=True) + "\n"
    )
    print(f"wrote artifacts under {args.output_dir}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
