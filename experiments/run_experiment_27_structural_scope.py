#!/usr/bin/env python3
"""Experiment 27: offline leak-safe structural-scope regression anchor.

This script never creates an LLM client.  It joins immutable probe cases to
existing arena observations, verifies dataset bytes against each manifest, and
recomputes repair-based indication validity and shadow-only potential endpoints.
It activates no runtime scope and is not confirmatory evidence.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import random
from typing import Iterable, Mapping, Sequence

from cmd_audit.core.models import ProbeCase
from cmd_audit.data_io.probe_cases import load_probe_cases
from cmd_audit.repair.structural_router import (
    StructuralIndication,
    extract_structural_indications,
)


DEFAULT_ARENAS = (
    (
        "memfail",
        "data/probe_cases/memfail_cases.json",
        "artifacts/arena/memfail_observations.jsonl",
    ),
    (
        "memtrace_seed24",
        "data/probe_cases/memtrace_kp_cases.json",
        "artifacts/arena/memtrace_seed24.jsonl",
    ),
    (
        "memtrace_rep2",
        "data/probe_cases/memtrace_kp_cases.json",
        "artifacts/arena/memtrace_observations.jsonl",
    ),
    (
        "memtrace_seed124",
        "data/probe_cases/memtrace_kp_cases.json",
        "artifacts/arena/memtrace_seed124.jsonl",
    ),
    (
        "memtrace_seed224",
        "data/probe_cases/memtrace_kp_cases.json",
        "artifacts/arena/memtrace_seed224.jsonl",
    ),
    (
        "stale",
        "data/probe_cases/stale_item_cases.json",
        "artifacts/arena/stale_observations.jsonl",
    ),
)


@dataclass(frozen=True)
class IndicationValidityRow:
    arena: str
    signal_type: str
    fires: int
    valid: int
    validity: float | None


@dataclass(frozen=True)
class EndpointRow:
    arena: str
    cases: int
    frozen_sum: float
    routed_sum: float
    delta: float
    overrides: int
    permutation_p: float | None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--arena",
        action="append",
        default=[],
        metavar="NAME:DATASET:OBSERVATIONS",
        help="Repeatable arena binding; defaults to shipped local artifacts.",
    )
    parser.add_argument(
        "--out",
        default="artifacts/exp_runs/exp27_structural_scope",
    )
    parser.add_argument("--seed", type=int, default=24)
    parser.add_argument("--permutations", type=int, default=10_000)
    args = parser.parse_args()
    if args.permutations < 100:
        parser.error("--permutations must be >= 100")

    bindings = (
        tuple(_parse_binding(value) for value in args.arena)
        if args.arena
        else DEFAULT_ARENAS
    )
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    validity_rows: list[IndicationValidityRow] = []
    endpoint_rows: list[EndpointRow] = []
    manifests: list[dict[str, object]] = []
    detail_rows: list[dict[str, object]] = []

    for name, dataset_value, observation_value in bindings:
        dataset = Path(dataset_value)
        observations = Path(observation_value)
        manifest, records = _load_arena_observations(observations)
        dataset_sha256 = _file_sha256(dataset)
        expected_sha256 = manifest.get("dataset_source_sha256")
        if expected_sha256 and dataset_sha256 != expected_sha256:
            raise ValueError(
                f"{name}: dataset SHA256 mismatch "
                f"(expected {expected_sha256}, got {dataset_sha256})"
            )
        cases = {
            case.case_id: case for case in load_probe_cases(dataset)
        }
        joined = [
            (record, cases[str(record["case_id"])])
            for record in records
            if str(record["case_id"]) in cases
        ]
        missing = {
            str(record["case_id"])
            for record in records
            if str(record["case_id"]) not in cases
        }
        if missing:
            raise ValueError(
                f"{name}: {len(missing)} observation case ids are absent "
                "from the verified dataset"
            )

        per_signal: dict[str, list[bool]] = {}
        per_family_delta: dict[str, float] = {}
        frozen_sum = 0.0
        routed_sum = 0.0
        overrides = 0
        for record, case in joined:
            indications = _case_indications(case)
            shadow_scores = dict(record.get("shadow_gold_scores") or ())
            finite_shadow = tuple(
                value
                for raw_value in shadow_scores.values()
                if (value := _optional_float(raw_value)) is not None
            )
            oracle_gain = max(finite_shadow) if finite_shadow else None
            for indication in indications:
                indication_gain = _optional_float(
                    shadow_scores.get(f"seed:{indication.action}")
                )
                valid = (
                    indication_gain is not None
                    and oracle_gain is not None
                    and indication_gain >= 0.1
                    and indication_gain >= oracle_gain - 0.05
                )
                per_signal.setdefault(indication.signal_type, []).append(valid)

            frozen_gain = _optional_float(record.get("selected_shadow_gain")) or 0.0
            routed_gain = frozen_gain
            selected_skill = record.get("selected_skill_id")
            strongest = max(
                indications,
                key=lambda row: (row.strength, row.signal_type),
                default=None,
            )
            routed_skill = (
                f"seed:{strongest.action}"
                if strongest is not None
                else None
            )
            # Null/fill protection: a structural action may only route when the
            # arena actually evaluated that legal candidate.
            if routed_skill is not None and routed_skill in shadow_scores:
                routed_value = _optional_float(shadow_scores[routed_skill])
                if routed_value is not None:
                    routed_gain = routed_value
                    overrides += routed_skill != selected_skill
            delta = routed_gain - frozen_gain
            family_id = str(record.get("family_id") or record["case_id"])
            per_family_delta[family_id] = (
                per_family_delta.get(family_id, 0.0) + delta
            )
            frozen_sum += frozen_gain
            routed_sum += routed_gain
            detail_rows.append(
                {
                    "arena": name,
                    "case_id": case.case_id,
                    "family_id": family_id,
                    "signal_types": "|".join(
                        indication.signal_type for indication in indications
                    ),
                    "frozen_skill_id": selected_skill or "",
                    "routed_skill_id": routed_skill or selected_skill or "",
                    "frozen_shadow_gain": frozen_gain,
                    "routed_shadow_gain": routed_gain,
                    "delta": delta,
                }
            )

        for signal_type in sorted(per_signal):
            values = per_signal[signal_type]
            validity_rows.append(
                IndicationValidityRow(
                    arena=name,
                    signal_type=signal_type,
                    fires=len(values),
                    valid=sum(values),
                    validity=sum(values) / len(values) if values else None,
                )
            )
        endpoint_rows.append(
            EndpointRow(
                arena=name,
                cases=len(joined),
                frozen_sum=frozen_sum,
                routed_sum=routed_sum,
                delta=routed_sum - frozen_sum,
                overrides=overrides,
                permutation_p=_family_sign_permutation_p(
                    tuple(per_family_delta.values()),
                    permutations=args.permutations,
                    seed=args.seed,
                ),
            )
        )
        manifests.append(
            {
                "arena": name,
                "dataset": str(dataset.resolve()),
                "dataset_sha256": dataset_sha256,
                "observations": str(observations.resolve()),
                "observations_sha256": _file_sha256(observations),
                "observation_manifest": manifest,
            }
        )

    _write_csv(
        output / "indication_validity.csv",
        [asdict(row) for row in validity_rows],
    )
    _write_csv(
        output / "endpoint_table.csv",
        [asdict(row) for row in endpoint_rows],
    )
    _write_csv(output / "case_detail.csv", detail_rows)
    summary = {
        "experiment": "27",
        "protocol": "sigil-structural-scope-v1",
        "llm_calls": 0,
        "active_scope": [],
        "endpoint_kind": "shadow_potential_not_runtime_activation",
        "permutations": args.permutations,
        "seed": args.seed,
        "indication_validity": [asdict(row) for row in validity_rows],
        "endpoints": [asdict(row) for row in endpoint_rows],
        "sources": manifests,
    }
    (output / "summary.json").write_text(
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
    print("[RESULT] experiment=27")
    print("[RESULT] llm_calls=0")
    print(f"[RESULT] arenas={len(endpoint_rows)}")
    print(f"[RESULT] output={output}")
    return 0


def _case_indications(case: ProbeCase) -> tuple[StructuralIndication, ...]:
    by_id = {item.memory_id: item for item in case.extracted_memory}
    recalled = tuple(
        by_id[memory_id]
        for memory_id in case.primary_baseline.retrieved_memory_ids
        if memory_id in by_id
    )
    return extract_structural_indications(
        case.query,
        recalled,
    )


def _load_arena_observations(
    path: Path,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    manifest: dict[str, object] | None = None
    observations = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("record_type") == "arena_manifest":
                if manifest is not None:
                    raise ValueError(f"{path}: duplicate arena manifest")
                manifest = row
            elif row.get("record_type") == "gold_free_observation":
                observations.append(row)
    if manifest is None:
        raise ValueError(f"{path}: missing arena manifest")
    return manifest, observations


def _family_sign_permutation_p(
    family_deltas: Sequence[float],
    *,
    permutations: int,
    seed: int,
) -> float | None:
    finite = tuple(
        float(value) for value in family_deltas if math.isfinite(float(value))
    )
    if not finite:
        return None
    observed = sum(finite)
    if observed <= 0.0:
        return 1.0
    rng = random.Random(seed)
    extreme = 0
    for _ in range(permutations):
        permuted = sum(
            value if rng.randrange(2) else -value for value in finite
        )
        extreme += permuted >= observed - 1e-12
    return (extreme + 1) / (permutations + 1)


def _parse_binding(value: str) -> tuple[str, str, str]:
    parts = value.split(":", 2)
    if len(parts) != 3 or not all(parts):
        raise argparse.ArgumentTypeError(
            "--arena must be NAME:DATASET:OBSERVATIONS"
        )
    return parts[0], parts[1], parts[2]


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    fieldnames = list(rows[0]) if rows else ()
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
