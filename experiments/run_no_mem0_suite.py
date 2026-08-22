#!/usr/bin/env python3
"""Closed offline orchestration for experiments that do not require Mem0.

This is deliberately a small allowlist, not a legacy-runner collector.  Every
child is invoked with a structured argv and a scrubbed environment: no shell,
network configuration, API key, Mem0 backend, or OpenAI-compatible backend is
available.  Smoke fixtures are labelled as wiring evidence, never headlines.
"""
from __future__ import annotations

import argparse, hashlib, json, os, subprocess, sys, time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from cmd_audit.core.state_codec import atomic_json_write, content_sha256

ROOT = Path(__file__).resolve().parents[1]
LME_S = ROOT / "data/external/longmemeval/input/longmemeval_s_cleaned.json"
LME_M = ROOT / "data/external/longmemeval/input/longmemeval_m_cleaned.json"
LME_ORACLE = ROOT / "data/external/longmemeval/oracle/longmemeval_oracle.json"
MEMFAIL = ROOT / "data/external/memfail/datasets"
EVO_SUITE = ROOT / "data/external/evobench/public_validation/benchmark/suites/evobench_validation.json"
EVO_SEED = ROOT / "data/external/evobench/public_validation/policy_harness_seed"
V4_CASES = ROOT / "artifacts/v4-confirm-002/cases.merged.jsonl"

@dataclass(frozen=True)
class Step:
    name: str; module: str; inputs: tuple[Path, ...]; argv: tuple[str, ...]; fixture: bool = False; accepted_exit_codes: tuple[int, ...] = (0,)

def _hash(path: Path) -> str:
    h = hashlib.sha256()
    if path.is_dir():
        for child in sorted(x for x in path.rglob("*") if x.is_file()): h.update(child.relative_to(path).as_posix().encode()); h.update(_hash(child).encode())
    else:
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""): h.update(block)
    return h.hexdigest()

def _steps(profile: str, out: Path, limit: int) -> tuple[Step, ...]:
    n = max(1, limit)
    if profile == "plumbing-smoke":
        retrieval = out / "p3a_retrieval"
        return (
            Step("p3a-longmemeval-m0-r1", "experiments.run_longmemeval_m0_r1", (LME_S, LME_ORACLE), ("--data", str(LME_S), "--oracle", str(LME_ORACLE), "--backend", "in-memory", "--limit", str(n), "--output", str(retrieval)), True),
            Step("p3b-memfail-m0-r1", "experiments.run_memfail_m0_r1", (MEMFAIL,), ("--data-root", str(MEMFAIL), "--backend", "in-memory", "--limit", str(n), "--output", str(out / "p3b_memfail")), True),
            Step("p3c-fake-e2e", "experiments.run_longmemeval_e2e", (LME_S, retrieval / "manifest.json"), ("--mode", "all", "--retrieval-run", str(retrieval), "--data", str(LME_S), "--answerer-backend", "fake", "--judge-backend", "fake", "--limit", str(n), "--output", str(out / "p3c_e2e")), True),
            Step("p3d-fake-lifecycle", "experiments.run_evobench_harness", (EVO_SUITE, EVO_SEED), ("fake-lifecycle", "--run-dir", str(out / "p3d_harness"), "--validation-suite", str(EVO_SUITE), "--seed-harness", str(EVO_SEED), "--output", str(out / "p3d_sealed_request.json")), True),
        )
    if profile == "offline-memory":
        return tuple(Step(f"longmemeval-{tag}-in-memory", "experiments.run_longmemeval_m0_r1", (data, LME_ORACLE), ("--data", str(data), "--oracle", str(LME_ORACLE), "--backend", "in-memory", "--limit", str(n if tag == "s" else max(n, 2)), "--output", str(out / f"longmemeval_{tag}"))) for tag, data in (("s", LME_S), ("m", LME_M))) + (Step("memfail-full-retrieval", "experiments.run_memfail_m0_r1", (MEMFAIL,), ("--data-root", str(MEMFAIL), "--backend", "in-memory", "--limit", "0", "--output", str(out / "memfail_full"))),)
    if profile == "zero-call-governance":
        # This current typed audit is retained; older arena/legacy scripts are
        # excluded because their evidence/claim contract predates typed-v2.
        return (Step("typed-identifiability-governance", "experiments.ghost_ecology_zero_call", (V4_CASES,), ("--cases", str(V4_CASES), "--output", str(out / "typed_identifiability.json"), "--feedback-version", "typed-v2", "--bootstrap-samples", str(max(100, n * 100))), accepted_exit_codes=(0, 2)),)
    if profile == "baseline-confirmation":
        # Vanilla only: the four P3 arms have identical retrieval until a later
        # repair comparison, so shadow-arm replication would add no baseline evidence.
        steps=[]
        for dataset, limit_arg in (("longmemeval-s", "0"), ("memfail", "0")):
            for strategy in ("lexical", "bm25"):
                steps.append(Step(f"p4a-{dataset}-{strategy}", "experiments.baselines.retrieval_confirmation", ((LME_S if dataset == "longmemeval-s" else MEMFAIL),), ("--dataset", dataset, "--strategy", strategy, "--top-k", "5", "--limit", limit_arg, "--output", str(out / f"{dataset}_{strategy}"))))
        # The dense adapter is permitted to return a truthful unavailable receipt.
        steps.append(Step("p4a-longmemeval-s-minilm", "experiments.baselines.retrieval_confirmation", (LME_S,), ("--dataset", "longmemeval-s", "--strategy", "minilm", "--top-k", "5", "--limit", str(n), "--output", str(out / "longmemeval_s_minilm"))))
        return tuple(steps)
    if profile == "p4b-typed-evidence":
        evidence=out/"typed_evidence_s"
        p4a=ROOT/"artifacts/experiments/p4a_baseline_confirmation/longmemeval_s_bm25_optimized"
        return (Step("p4b-build-typed-evidence-s", "experiments.build_p4b_typed_evidence", (p4a/"rankings.jsonl",p4a/"run_receipt.json"), ("--dataset","longmemeval-s","--ranking-root",str(p4a),"--limit",str(n),"--output",str(evidence)), True), Step("p4b-frozen-bm25-s", "experiments.run_p4b_cmd_bm25", (evidence/"manifest.json",), ("--evidence",str(evidence),"--context-budget","5","--output",str(out/"p4b_run_s")), True))
    raise ValueError("unknown profile")

def _safe_env() -> dict[str, str]:
    return {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "PYTHONPATH") if k in os.environ}

def _execute(step: Step, *, root: Path, output_root: Path, resume: bool) -> dict[str, object]:
    missing = [str(p) for p in step.inputs if not p.exists()]
    row: dict[str, object] = {"name": step.name, "module": step.module, "command": [sys.executable, "-m", step.module, *step.argv], "fixture_only": step.fixture, "input_hashes": {} if missing else {str(p): _hash(p) for p in step.inputs}}
    if missing: return {**row, "status": "skip_with_reason", "reason": "missing_required_local_input", "missing": missing}
    # Resume is exact-only: a finished step may be reused only when its recorded
    # inputs and command match; no output collision can be silently overwritten.
    started = time.monotonic(); result = subprocess.run(row["command"], cwd=root, env=_safe_env(), text=True, capture_output=True, shell=False)
    stdout, stderr = output_root / "suite_logs" / f"{step.name}.stdout.txt", output_root / "suite_logs" / f"{step.name}.stderr.txt"
    stdout.write_text(result.stdout, encoding="utf-8"); stderr.write_text(result.stderr, encoding="utf-8")
    status = "passed" if result.returncode == 0 else "completed_conditionally_blocked" if result.returncode in step.accepted_exit_codes else "failed"
    return {**row, "status": status, "exit_code": result.returncode, "duration_seconds": time.monotonic() - started, "stdout": str(stdout), "stderr": str(stderr)}

def run(*, profile: str, output_root: Path, run_mode: str, limit: int, plan_only: bool, fail_fast: bool) -> dict[str, object]:
    output_root = Path(output_root); steps = _steps(profile, output_root, limit)
    if run_mode == "fresh" and output_root.exists() and any(output_root.iterdir()): raise ValueError("fresh refuses a non-empty output root")
    manifest = output_root / "suite_manifest.json"
    if run_mode == "resume" and not manifest.exists(): raise ValueError("resume requires an existing closed suite manifest")
    prior = json.loads(manifest.read_text()) if manifest.exists() else None
    plan = [{"name": s.name, "module": s.module, "command": [sys.executable, "-m", s.module, *s.argv], "fixture_only": s.fixture} for s in steps]
    if plan_only: return {"profile": profile, "plan_only": True, "steps": plan, "network": "prohibited", "mem0": "prohibited"}
    output_root.mkdir(parents=True, exist_ok=True); (output_root / "suite_logs").mkdir(exist_ok=True)
    if prior and prior.get("plan") != plan: raise ValueError("resume plan root mismatch")
    records = list(prior.get("steps", [])) if prior else []
    for i, step in enumerate(steps):
        if i < len(records):
            if records[i].get("status") in {"passed", "completed_conditionally_blocked"}:
                current = {str(p): _hash(p) for p in step.inputs if p.exists()}
                if records[i].get("input_hashes") != current: raise ValueError("resume input root mismatch")
                continue
            records = records[:i]
        record = _execute(step, root=ROOT, output_root=output_root, resume=run_mode == "resume"); records.append(record)
        atomic_json_write(manifest, {"schema_version": "cmd-no-mem0-suite-v1", "profile": profile, "plan": plan, "plan_root": content_sha256(plan), "steps": records, "closed": False, "network": "prohibited", "api": "prohibited", "mem0": "prohibited"}, ensure_ascii=False, allow_nan=False, indent=2, trailing_newline=True)
        if record["status"] == "failed" and fail_fast: break
    summary = {"schema_version": "cmd-no-mem0-suite-summary-v1", "profile": profile, "passed": sum(x["status"] == "passed" for x in records), "conditionally_blocked": sum(x["status"] == "completed_conditionally_blocked" for x in records), "skipped": sum(x["status"] == "skip_with_reason" for x in records), "failed": sum(x["status"] == "failed" for x in records), "fixture_or_smoke_only": any(x.get("fixture_only") for x in records), "headline_result": False}
    atomic_json_write(output_root / "summary.json", summary, ensure_ascii=False, allow_nan=False, indent=2, trailing_newline=True)
    atomic_json_write(manifest, {"schema_version": "cmd-no-mem0-suite-v1", "profile": profile, "plan": plan, "plan_root": content_sha256(plan), "steps": records, "summary_root": _hash(output_root / "summary.json"), "closed": True, "network": "prohibited", "api": "prohibited", "mem0": "prohibited"}, ensure_ascii=False, allow_nan=False, indent=2, trailing_newline=True)
    return summary

def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("--profile", choices=("plumbing-smoke", "offline-memory", "zero-call-governance", "baseline-confirmation", "p4b-typed-evidence"), required=True); p.add_argument("--output-root", type=Path, required=True); p.add_argument("--run-mode", choices=("fresh", "resume"), default="fresh"); p.add_argument("--limit", type=int, default=1); p.add_argument("--plan-only", action="store_true"); p.add_argument("--fail-fast", action="store_true")
    a = p.parse_args(argv); result = run(profile=a.profile, output_root=a.output_root, run_mode=a.run_mode, limit=a.limit, plan_only=a.plan_only, fail_fast=a.fail_fast); print(json.dumps(result, ensure_ascii=False, sort_keys=True)); return 0 if not result.get("failed") else 1

if __name__ == "__main__": raise SystemExit(main())
