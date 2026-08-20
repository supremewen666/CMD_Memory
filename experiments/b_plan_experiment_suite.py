"""Unified, frozen, stage-addressable B-plan experiment suite.

This module is orchestration only.  It connects the existing lineage and
experiment entry points in the frozen order::

    lineage_plan -> followup_capture -> lineage_project -> lineage_merge
    -> E2 -> E3 -> E4 -> E4b

The orchestration layer itself does not call a model or the network.  The
fresh V4 materializer runs before this suite, and the follow-up capture backend
is the one explicit seam inside this stage graph where observed calls may
occur.  Its returned accounting is validated and copied into the closed suite
manifest.  All outputs are no-overwrite and the final manifest is published
atomically only after the requested stages complete.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Callable, Mapping, Sequence

from cmd_audit.adapters.session_lineage_cli import export_lineage
from experiments.e2_typed_identifiability import run_e2_suite
from experiments.e4b_descriptor_policy import run_e4b
from experiments.poison_density_sweep import run_sweep
from experiments.v4_followup_capture import capture_followups
from experiments.v4_lineage_dataset import build_capture_plan, merge_lineage_cases
from experiments.v4_prequential_runner import load_cases, main as run_v4


B_PLAN_SCHEMA_VERSION = "cmd-b-plan-experiment-suite-v1"
B_PLAN_MANIFEST_SCHEMA_VERSION = "cmd-b-plan-experiment-suite-manifest-v1"
STAGES = (
    "lineage_plan",
    "followup_capture",
    "lineage_project",
    "lineage_merge",
    "E2",
    "E3",
    "E4",
    "E4b",
)
_STAGE_ALIASES = {stage.lower(): stage for stage in STAGES}
CaptureBackend = Callable[[Mapping[str, object]], Mapping[str, object]]


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _atomic_json(path: Path, value: object) -> None:
    if path.exists():
        raise ValueError(f"refusing to overwrite suite manifest: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    except FileExistsError as error:
        raise ValueError(f"refusing to overwrite suite manifest: {path}") from error
    finally:
        temporary.unlink(missing_ok=True)


def _stage_name(value: str) -> str:
    key = value.strip().lower()
    if key not in _STAGE_ALIASES:
        raise ValueError(f"unknown B-plan stage: {value}")
    return _STAGE_ALIASES[key]


@dataclass(frozen=True)
class BPlanConfig:
    prepared_path: Path
    cases_path: Path
    output_dir: Path
    backend_locator: str
    candidate_budget: int
    seeds: tuple[int, ...]
    protocol: Mapping[str, object]
    ghost_evaluator_path: Path
    ghost_protocol_path: Path
    source_materialization_manifest: Path
    exposure_events: int = 2
    bootstrap_samples: int = 100
    stages: tuple[str, ...] = STAGES

    def __post_init__(self) -> None:
        if isinstance(self.candidate_budget, bool) or not isinstance(self.candidate_budget, int) or self.candidate_budget < 1:
            raise ValueError("candidate_budget must be a positive integer")
        if not self.seeds or len(set(self.seeds)) != len(self.seeds) or any(isinstance(seed, bool) or not isinstance(seed, int) for seed in self.seeds):
            raise ValueError("seeds must be non-empty distinct integers")
        if not isinstance(self.protocol, Mapping) or not self.protocol:
            raise ValueError("protocol must be a non-empty frozen mapping")
        if isinstance(self.exposure_events, bool) or not isinstance(self.exposure_events, int) or self.exposure_events < 1:
            raise ValueError("exposure_events must be positive")
        if isinstance(self.bootstrap_samples, bool) or not isinstance(self.bootstrap_samples, int) or self.bootstrap_samples < 100:
            raise ValueError("bootstrap_samples must be at least 100")
        normalized = tuple(_stage_name(stage) for stage in self.stages)
        if len(set(normalized)) != len(normalized):
            raise ValueError("stages must be unique")
        object.__setattr__(self, "stages", normalized)

    @property
    def protocol_sha256(self) -> str:
        return _sha256(dict(self.protocol))

    @property
    def paths(self) -> dict[str, Path]:
        root = self.output_dir
        stage_label = "-".join(stage.lower() for stage in self.stages) or "preflight"
        suite_manifest = (
            root / "b-plan.manifest.json"
            if self.stages == STAGES
            else root / "stage-manifests" / f"{stage_label}.manifest.json"
        )
        return {
            "capture_plan": root / "lineage" / "capture_plan.jsonl",
            "selections": root / "lineage" / "selections.jsonl",
            "plan_manifest": root / "lineage" / "plan.manifest.json",
            "normalized": root / "lineage" / "normalized.jsonl",
            "capture_manifest": root / "lineage" / "capture.manifest.json",
            "lineage": root / "lineage" / "lineage.jsonl",
            "lineage_manifest": root / "lineage" / "lineage.manifest.json",
            "merged": root / "lineage" / "merged.jsonl",
            "merge_manifest": root / "lineage" / "merge.manifest.json",
            "e2_dir": root / "E2",
            "e3_report": root / "E3" / "report.json",
            "e4_report": root / "E4" / "report.json",
            "e4b_dir": root / "E4b",
            "suite_manifest": suite_manifest,
        }


def _validate_inputs(config: BPlanConfig, backend: CaptureBackend | None) -> dict[str, object]:
    if config.paths["suite_manifest"].exists():
        raise ValueError(
            f"refusing to overwrite B-plan suite manifest: {config.paths['suite_manifest']}"
        )
    cases = load_cases(config.cases_path)
    if any(len(case.intents) != config.candidate_budget for case in cases):
        raise ValueError("candidate budget is not aligned across cases")
    if backend is None or not callable(backend):
        raise ValueError("capture backend must be callable")
    evaluator_sha256 = _file_sha256(config.ghost_evaluator_path)
    ghost_protocol_sha256 = _file_sha256(config.ghost_protocol_path)
    source_manifest = json.loads(
        config.source_materialization_manifest.read_text(encoding="utf-8")
    )
    if not isinstance(source_manifest, Mapping):
        raise ValueError("source materialization manifest must be an object")
    if source_manifest.get("output_sha256") != _file_sha256(config.cases_path):
        raise ValueError("source materialization manifest does not bind cases")
    source_manifest_sha256 = _file_sha256(config.source_materialization_manifest)
    return {
        "prepared_path": str(config.prepared_path.resolve()),
        "prepared_sha256": _file_sha256(config.prepared_path),
        "cases_path": str(config.cases_path.resolve()),
        "cases_sha256": _file_sha256(config.cases_path),
        "case_count": len(cases),
        "candidate_budget": config.candidate_budget,
        "backend_locator": config.backend_locator,
        "protocol_sha256": config.protocol_sha256,
        "ghost_evaluator_path": str(config.ghost_evaluator_path.resolve()),
        "ghost_evaluator_sha256": evaluator_sha256,
        "ghost_protocol_path": str(config.ghost_protocol_path.resolve()),
        "ghost_protocol_sha256": ghost_protocol_sha256,
        "seeds": list(config.seeds),
        "source_materialization_manifest": (
            str(config.source_materialization_manifest.resolve())
        ),
        "source_materialization_manifest_sha256": (
            source_manifest_sha256
        ),
    }


def exact_argv(config: BPlanConfig, stage: str) -> tuple[str, ...]:
    """Return the reproducible command shape for one frozen stage."""
    stage = _stage_name(stage)
    seed_args = tuple(
        value
        for seed in config.seeds
        for value in ("--seed", str(seed))
    )
    common = ("python", "-m", "experiments.b_plan_experiment_suite", "--stage", stage, "--prepared", str(config.prepared_path), "--cases", str(config.cases_path), "--output-dir", str(config.output_dir), "--backend", config.backend_locator, "--candidate-budget", str(config.candidate_budget), "--ghost-evaluator", str(config.ghost_evaluator_path), "--ghost-protocol", str(config.ghost_protocol_path), "--exposure-events", str(config.exposure_events), "--bootstrap-samples", str(config.bootstrap_samples), *seed_args)
    suffix = ("--protocol-json", json.dumps(dict(config.protocol), sort_keys=True, separators=(",", ":")))
    suffix += ("--source-materialization-manifest", str(config.source_materialization_manifest))
    return common + suffix


def _stage_output_hashes(config: BPlanConfig, stage: str) -> dict[str, str]:
    p = config.paths
    names = {
        "lineage_plan": ("capture_plan", "selections", "plan_manifest"),
        "followup_capture": ("normalized", "capture_manifest"),
        "lineage_project": ("lineage", "lineage_manifest"),
        "lineage_merge": ("merged", "merge_manifest"),
        "E2": ("e2_dir",),
        "E3": ("e3_report",),
        "E4": ("e4_report",),
        "E4b": ("e4b_dir",),
    }[stage]
    result: dict[str, str] = {}
    for name in names:
        path = p[name]
        if path.is_file():
            result[str(path.relative_to(config.output_dir))] = _file_sha256(path)
        elif path.is_dir():
            rows = {str(child.relative_to(path)): _file_sha256(child) for child in sorted(path.rglob("*")) if child.is_file()}
            result[str(path.relative_to(config.output_dir))] = _sha256(rows)
        else:
            raise ValueError(f"stage did not publish expected output: {path}")
    return result


def run_stage(config: BPlanConfig, stage: str, *, backend: CaptureBackend) -> dict[str, object]:
    stage = _stage_name(stage)
    p = config.paths
    if stage == "lineage_plan":
        result = build_capture_plan(prepared_path=config.prepared_path, cases_path=config.cases_path, capture_output=p["capture_plan"], selections_output=p["selections"], manifest_output=p["plan_manifest"], exposure_events=config.exposure_events)
    elif stage == "followup_capture":
        result = capture_followups(plan_path=p["capture_plan"], backend=backend, backend_locator=config.backend_locator, output_path=p["normalized"], manifest_path=p["capture_manifest"])
    elif stage == "lineage_project":
        result = export_lineage(p["normalized"], p["lineage"], p["lineage_manifest"], selections=p["selections"])
    elif stage == "lineage_merge":
        result = merge_lineage_cases(cases_path=config.cases_path, lineage_path=p["lineage"], output_path=p["merged"], manifest_path=p["merge_manifest"], source_materialization_manifest=config.source_materialization_manifest, capture_manifest=p["capture_manifest"], lineage_manifest=p["lineage_manifest"])
    elif stage == "E2":
        result = run_e2_suite(cases_path=p["merged"], output_dir=p["e2_dir"], seeds=config.seeds, bootstrap_samples=config.bootstrap_samples, materialization_manifest=p["merge_manifest"])
    elif stage == "E3":
        result = run_sweep(
            recall_size=10,
            max_density=0.9,
            threshold=0.6,
            cases_per_cell=5,
        )
        _json(p["e3_report"], result)
    elif stage == "E4":
        status = run_v4(
            (
                "--cases",
                str(p["merged"]),
                "--output-dir",
                str(p["e4_report"].parent),
                "--candidate-budget",
                str(config.candidate_budget),
                "--ghost-evaluator",
                str(config.ghost_evaluator_path),
                "--ghost-protocol",
                str(config.ghost_protocol_path),
                "--materialization-manifest",
                str(p["merge_manifest"]),
                "--ghost-feedback-mode",
                "prospective_deployment",
                "--bootstrap-samples",
                str(config.bootstrap_samples),
                "--bootstrap-seed",
                str(config.seeds[0]),
            )
        )
        if status != 0:
            raise RuntimeError(f"E4 runner failed with status {status}")
        result = json.loads(p["e4_report"].read_text(encoding="utf-8"))
    else:
        result = run_e4b(
            cases_path=p["merged"],
            output_dir=p["e4b_dir"],
            candidate_budget=config.candidate_budget,
            bootstrap_samples=config.bootstrap_samples,
            bootstrap_seed=config.seeds[0],
            materialization_manifest=p["merge_manifest"],
        )
    return {"stage": stage, "argv": list(exact_argv(config, stage)), "outputs": _stage_output_hashes(config, stage), "model_calls": int(result.get("model_calls", 0)), "network_calls": int(result.get("network_calls", 0)), "result_sha256": _sha256(result)}


def run_b_plan(config: BPlanConfig, *, backend: CaptureBackend) -> dict[str, object]:
    inputs = _validate_inputs(config, backend)
    completed: list[dict[str, object]] = []
    for stage in config.stages:
        completed.append(run_stage(config, stage, backend=backend))
    manifest = {
        "schema_version": B_PLAN_MANIFEST_SCHEMA_VERSION,
        "plan_schema_version": B_PLAN_SCHEMA_VERSION,
        "inputs": inputs,
        "protocol": dict(config.protocol),
        "stages": completed,
        "requested_stages": list(config.stages),
        "model_calls": sum(int(row["model_calls"]) for row in completed),
        "network_calls": sum(int(row["network_calls"]) for row in completed),
        "closed": True,
    }
    manifest["manifest_sha256"] = _sha256(manifest)
    _atomic_json(config.paths["suite_manifest"], manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", action="append", choices=STAGES, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--candidate-budget", type=int, required=True)
    parser.add_argument("--ghost-evaluator", type=Path, required=True)
    parser.add_argument("--ghost-protocol", type=Path, required=True)
    parser.add_argument("--seed", type=int, action="append", required=True)
    parser.add_argument("--protocol-json", required=True)
    parser.add_argument("--source-materialization-manifest", type=Path, required=True)
    parser.add_argument("--exposure-events", type=int, default=2)
    parser.add_argument("--bootstrap-samples", type=int, default=100)
    args = parser.parse_args(argv)
    from experiments.v4_followup_capture import load_backend
    protocol = json.loads(args.protocol_json)
    config = BPlanConfig(prepared_path=args.prepared, cases_path=args.cases, output_dir=args.output_dir, backend_locator=args.backend, candidate_budget=args.candidate_budget, seeds=tuple(args.seed), protocol=protocol, ghost_evaluator_path=args.ghost_evaluator, ghost_protocol_path=args.ghost_protocol, source_materialization_manifest=args.source_materialization_manifest, exposure_events=args.exposure_events, bootstrap_samples=args.bootstrap_samples, stages=tuple(args.stage))
    result = run_b_plan(config, backend=load_backend(args.backend))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["BPlanConfig", "B_PLAN_MANIFEST_SCHEMA_VERSION", "B_PLAN_SCHEMA_VERSION", "STAGES", "exact_argv", "run_b_plan", "run_stage"]
