#!/usr/bin/env python3
"""Run the frozen typed-E2 audit over multiple seeds as one atomic suite."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Mapping, Sequence

from experiments.ghost_ecology_zero_call import audit_identifiability_v2


E2_SUITE_SCHEMA_VERSION = "cmd-e2-typed-identifiability-suite-v1"
MIN_TYPED_PAIRWISE_COVERAGE = 0.50
FRESH_MANIFEST_SCHEMAS = {
    "cmd-v4-materialized-merge-v1",
    "cmd-v4-lineage-merge-manifest-v1",
}


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _validate_coverage_gate(report: Mapping[str, object]) -> None:
    """Enforce the typed-E2 null/blocked contract before publishing a suite."""
    if report.get("model_calls") != 0:
        raise ValueError("typed E2 audit unexpectedly recorded model calls")
    typed = _mapping(report.get("typed_coverage"), "typed coverage")
    pairwise = _mapping(typed.get("pairwise_comparable_coverage"), "pairwise coverage")
    coverage = pairwise.get("value")
    if coverage is None:
        coverage = 0.0
    if isinstance(coverage, bool) or not isinstance(coverage, (int, float)):
        raise ValueError("typed E2 pairwise coverage must be numeric")
    controls = _mapping(report.get("decoupling_controls"), "decoupling controls")
    if float(coverage) < MIN_TYPED_PAIRWISE_COVERAGE:
        if report.get("decision") != "BLOCKED_TYPED_EVIDENCE_UNAVAILABLE":
            raise ValueError("E2 coverage gate was bypassed")
        for name in ("family_macro_pearson", "family_bootstrap_lower_95_one_sided", "within_case_pairwise_concordance", "candidate_level_pearson", "comparable_pair_count"):
            if typed.get(name) is not None:
                raise ValueError(f"coverage-blocked E2 claim statistic is not null: {name}")
        for name in ("telemetry_permutation", "telemetry_placebo"):
            if _mapping(controls.get(name), name).get("status") != "NOT_RUN_COVERAGE_BLOCKED":
                raise ValueError(f"coverage-blocked E2 control was run: {name}")


def run_e2_suite(
    *,
    cases_path: Path,
    output_dir: Path,
    seeds: Sequence[int],
    bootstrap_samples: int = 10_000,
    decoupling_seed: int = 91,
    materialization_manifest: Path | None = None,
) -> dict[str, object]:
    if output_dir.exists():
        raise ValueError(f"refusing to overwrite E2 output directory: {output_dir}")
    if not cases_path.is_file():
        raise FileNotFoundError(cases_path)
    seeds = tuple(seeds)
    if not seeds or len(set(seeds)) != len(seeds) or any(
        isinstance(seed, bool) or not isinstance(seed, int) for seed in seeds
    ):
        raise ValueError("E2 seeds must be a non-empty distinct integer sequence")
    reference_is_fresh_replay = False
    if materialization_manifest is not None:
        raw_manifest = _mapping(
            json.loads(materialization_manifest.read_text(encoding="utf-8")),
            "materialization manifest",
        )
        if raw_manifest.get("output_sha256") != _file_sha256(cases_path):
            raise ValueError("materialization manifest does not bind E2 cases")
        raw_fresh = raw_manifest.get("reference_is_fresh_replay", False)
        if not isinstance(raw_fresh, bool):
            raise ValueError("materialization fresh-replay flag must be boolean")
        if raw_fresh and raw_manifest.get("schema_version") not in FRESH_MANIFEST_SCHEMAS:
            raise ValueError("fresh-replay flag requires a recognized materialization manifest")
        reference_is_fresh_replay = raw_fresh
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        reports = []
        for seed in seeds:
            path = staged / f"seed-{seed}.json"
            report = audit_identifiability_v2(
                cases_path=cases_path,
                output=path,
                bootstrap_samples=bootstrap_samples,
                bootstrap_seed=seed,
                decoupling_seed=decoupling_seed,
                reference_is_fresh_replay=reference_is_fresh_replay,
            )
            _validate_coverage_gate(report)
            reports.append(
                {
                    "seed": seed,
                    "decision": report["decision"],
                    "report_sha256": _file_sha256(path),
                    "candidate_observed_coverage": report["typed_coverage"][
                        "candidate_observed_coverage"
                    ],
                    "family_observed_coverage": report["typed_coverage"][
                        "family_observed_coverage"
                    ],
                    "pairwise_comparable_coverage": report["typed_coverage"][
                        "pairwise_comparable_coverage"
                    ]["value"],
                    "claim_statistics_available": report["typed_coverage"][
                        "family_macro_pearson"
                    ]
                    is not None,
                }
            )
        summary = {
            "schema_version": E2_SUITE_SCHEMA_VERSION,
            "cases_path": str(cases_path.resolve()),
            "cases_sha256": _file_sha256(cases_path),
            "materialization_manifest": (
                None
                if materialization_manifest is None
                else str(materialization_manifest.resolve())
            ),
            "materialization_manifest_sha256": (
                None
                if materialization_manifest is None
                else _file_sha256(materialization_manifest)
            ),
            "seeds": list(seeds),
            "bootstrap_samples": bootstrap_samples,
            "decoupling_seed": decoupling_seed,
            "reference_is_fresh_replay": reference_is_fresh_replay,
            "reports": reports,
            "all_pass": all(row["decision"] == "PASS" for row in reports),
            "coverage_blocked": any(
                row["decision"] == "BLOCKED_TYPED_EVIDENCE_UNAVAILABLE"
                for row in reports
            ),
            "model_calls": 0,
            "network_calls": 0,
        }
        (staged / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.rename(staged, output_dir)
    except Exception:
        shutil.rmtree(staged, ignore_errors=True)
        raise
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, action="append", required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--decoupling-seed", type=int, default=91)
    parser.add_argument("--materialization-manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    result = run_e2_suite(
        cases_path=args.cases,
        output_dir=args.output_dir,
        seeds=args.seed,
        bootstrap_samples=args.bootstrap_samples,
        decoupling_seed=args.decoupling_seed,
        materialization_manifest=args.materialization_manifest,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["all_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
