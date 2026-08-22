#!/usr/bin/env python3
"""Fail-closed, provider-neutral governance for the Evo-Bench harness track.

This module never runs an Evo-Bench task, imports an evolver, or materializes a
sealed suite.  It records external candidate artifacts and external validation
or sealed-score receipts in a closed, hash-chained journal.  Validation is a
selection lane; evaluation is an opaque post-freeze reporting lane.
"""
from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from cmd_audit.core.state_codec import append_jsonl_fsync, atomic_json_write, content_sha256, require_closed_mapping
from experiments.v4_run_checkpoint import OutcomeJournal, RunCheckpoint, RunCheckpointStore

SCHEMA = "cmd-p3d-evobench-harness-v1"
GENESIS = "0" * 64
ROOT_FIELDS = frozenset({"previous_root", "harness_root", "code_root", "config_root", "test_root", "task_suite_root", "runtime_root", "budget_root", "scorer_contract_root", "router_root", "policy_root", "prompt_root"})
ARM_KINDS = frozenset({"seed", "static", "cmd", "ghost", "thompson"})


def _closed(value: object, fields: set[str] | frozenset[str], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    require_closed_mapping(value, fields, name)
    return dict(value)


def _sha_file(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"required file is missing: {path}")
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha_tree(path: Path) -> str:
    if not path.exists():
        raise ValueError(f"harness artifact is missing: {path}")
    if path.is_file():
        return _sha_file(path)
    rows = []
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        rows.append({"path": child.relative_to(path).as_posix(), "sha256": _sha_file(child)})
    return content_sha256(rows, ensure_ascii=False, allow_nan=False)


def _root(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 root")
    return value


def _roots(value: object) -> dict[str, str]:
    raw = _closed(value, ROOT_FIELDS, "roots")
    return {key: _root(raw[key], key) for key in ROOT_FIELDS}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON: {path}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON object required: {path}")
    return dict(value)


def _write_stdout(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))


def validation_metadata(path: Path) -> tuple[str, set[str], dict[str, int]]:
    """Parse only the released validation suite and prove its exact cardinality."""
    raw = _load_json(path)
    _closed(raw, {"name", "description", "assets_dir", "validation"}, "validation suite")
    if raw["name"] != "evobench_validation" or not isinstance(raw["validation"], list):
        raise ValueError("only the public evobench_validation suite is accepted")
    tasks = raw["validation"]
    if len(tasks) != 160:
        raise ValueError("public validation suite must contain exactly 160 tasks")
    ids: set[str] = set(); strata: dict[str, int] = {}
    for task in tasks:
        if not isinstance(task, Mapping) or not isinstance(task.get("id"), str) or not task["id"]:
            raise ValueError("validation task ID is invalid")
        if task["id"] in ids:
            raise ValueError("validation task ID collision")
        ids.add(task["id"])
        domain = task.get("domain")
        if not isinstance(domain, str) or not domain:
            raise ValueError("validation domain is invalid")
        family = str(task.get("metadata", {}).get("canary", "unknown")) if isinstance(task.get("metadata"), Mapping) else "unknown"
        strata[f"{domain}/{family}"] = strata.get(f"{domain}/{family}", 0) + 1
    return _sha_file(path), ids, strata


@dataclass(frozen=True)
class Event:
    event_index: int
    previous_event_hash: str
    event_type: str
    logical_id: str
    roots: Mapping[str, str]
    payload: Mapping[str, Any]
    event_hash: str = ""

    def mapping(self, include_hash: bool = True) -> dict[str, Any]:
        data = {"schema_version": SCHEMA, "event_index": self.event_index, "previous_event_hash": self.previous_event_hash,
                "event_type": self.event_type, "logical_id": self.logical_id, "roots": dict(self.roots), "payload": dict(self.payload)}
        if include_hash: data["event_hash"] = self.event_hash
        return data


class HarnessLedger:
    """The authority; state.json and checkpoints are derived caches only."""
    def __init__(self, directory: Path) -> None:
        self.directory = directory; self.path = directory / "harness_events.jsonl"; self.events: list[Event] = []
        self._load()

    @property
    def head(self) -> str: return self.events[-1].event_hash if self.events else GENESIS

    def append(self, event_type: str, logical_id: str, roots: Mapping[str, str], payload: Mapping[str, Any]) -> Event:
        if not isinstance(logical_id, str) or not logical_id: raise ValueError("logical_id is required")
        event = Event(len(self.events) + 1, self.head, event_type, logical_id, _roots(roots), dict(payload))
        event = Event(**{**event.__dict__, "event_hash": content_sha256(event.mapping(False), ensure_ascii=False, allow_nan=False)})
        append_jsonl_fsync(self.path, event.mapping(), ensure_ascii=False, allow_nan=False); self.events.append(event); return event

    def _load(self) -> None:
        if not self.path.exists(): return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            raw = _closed(json.loads(line), {"schema_version", "event_index", "previous_event_hash", "event_type", "logical_id", "roots", "payload", "event_hash"}, "harness event")
            if raw["schema_version"] != SCHEMA or raw["event_index"] != len(self.events) + 1 or raw["previous_event_hash"] != self.head: raise ValueError("harness journal discontinuity")
            roots = _roots(raw["roots"])
            if not isinstance(raw["event_type"], str) or not isinstance(raw["logical_id"], str) or not isinstance(raw["payload"], Mapping): raise ValueError("invalid harness journal event")
            expected = content_sha256({k: v for k, v in raw.items() if k != "event_hash"}, ensure_ascii=False, allow_nan=False)
            if raw["event_hash"] != expected: raise ValueError("harness journal hash mismatch")
            self.events.append(Event(raw["event_index"], raw["previous_event_hash"], raw["event_type"], raw["logical_id"], roots, dict(raw["payload"]), raw["event_hash"]))


class HarnessRun:
    def __init__(self, directory: Path) -> None:
        self.directory = directory; self.ledger = HarnessLedger(directory)
        self._replay()

    def _replay(self) -> None:
        self.run: dict[str, Any] | None = None; self.candidates: dict[str, dict[str, Any]] = {}; self.validation: dict[str, dict[str, Any]] = {}
        self.committed: set[str] = set(); self.rolled_back: set[str] = set(); self.frozen: dict[str, Any] | None = None; self.request: dict[str, Any] | None = None; self.evaluation: dict[str, Any] | None = None
        for event in self.ledger.events:
            typ, payload = event.event_type, dict(event.payload)
            if typ == "seed_registered":
                if self.run is not None: raise ValueError("duplicate seed registration")
                self.run = payload
            elif typ == "candidate_prepared":
                if event.logical_id in self.candidates: raise ValueError("candidate collision")
                self.candidates[event.logical_id] = payload
            elif typ == "validation_recorded":
                candidate_id = payload.get("candidate_id")
                if not isinstance(candidate_id, str) or candidate_id not in self.candidates or candidate_id in self.validation: raise ValueError("validation candidate collision")
                self.validation[candidate_id] = payload
            elif typ == "candidate_committed":
                if event.logical_id not in self.validation or event.logical_id in self.committed or event.logical_id in self.rolled_back: raise ValueError("invalid commit")
                self.committed.add(event.logical_id)
            elif typ == "candidate_rolled_back":
                if event.logical_id not in self.candidates or event.logical_id in self.committed or event.logical_id in self.rolled_back: raise ValueError("invalid rollback")
                self.rolled_back.add(event.logical_id)
            elif typ == "harness_frozen":
                if self.frozen is not None or event.logical_id not in self.committed: raise ValueError("invalid freeze")
                self.frozen = payload
            elif typ == "sealed_evaluation_exported":
                if self.frozen is None or self.request is not None: raise ValueError("invalid evaluation export")
                self.request = payload
            elif typ == "sealed_evaluation_ingested":
                if self.request is None or self.evaluation is not None: raise ValueError("invalid evaluation ingest")
                self.evaluation = payload
            else: raise ValueError("unknown harness event type")
        if self.run is None and self.ledger.events: raise ValueError("seed registration missing")

    def _ready(self) -> dict[str, Any]:
        if self.run is None: raise ValueError("run is not initialized")
        return self.run

    def _checkpoint(self) -> None:
        run = self._ready(); journal = OutcomeJournal(self.directory / "outcomes.jsonl")
        latest = self.ledger.events[-1]
        journal.append(latest.event_index, latest.logical_id, [{"event_type": latest.event_type, "event_hash": latest.event_hash}])
        # Reuse the V4 committed checkpoint primitive; it only snapshots the append-only authority.
        cp = RunCheckpoint(
            run_id=str(run["run_id"]), manifest_sha256=str(run["manifest_root"]), case_stream_sha256=str(run["task_suite_root"]),
            next_position=len(self.ledger.events), last_completed_event_index=len(self.ledger.events),
            global_policy_snapshot={"mode": "external-artifact-only", "frozen": self.frozen is not None},
            arm_policy_snapshots={str(row["arm_id"]): {"kind": row["kind"], "description": row["description"]} for row in run["arms"]},
            repository_identities={"harness_journal": self.ledger.head}, settlement_head=self.ledger.head,
            pending_root=self.ledger.head, router_snapshots={"router_root": self.ledger.events[-1].roots["router_root"]},
            outcome_head=journal.head, outcome_count=len(journal.events),
        )
        RunCheckpointStore(self.directory / "checkpoints").commit(cp)
        atomic_json_write(self.directory / "state.json", self.status(), ensure_ascii=False, allow_nan=False, indent=2, trailing_newline=True)

    def init(self, spec: Mapping[str, Any], suite: Path) -> dict[str, Any]:
        if self.ledger.events: raise ValueError("run already initialized")
        raw = _closed(spec, {"run_id", "seed_harness_path", "roots", "arms"}, "init spec")
        suite_root, _, strata = validation_metadata(suite); roots = _roots(raw["roots"])
        if roots["previous_root"] != GENESIS: raise ValueError("seed registration previous root must be genesis")
        if roots["task_suite_root"] != suite_root: raise ValueError("validation suite root mismatch")
        seed_path = Path(str(raw["seed_harness_path"])); seed_root = _sha_tree(seed_path)
        if roots["harness_root"] != seed_root: raise ValueError("seed harness root mismatch")
        arms = raw["arms"]
        if not isinstance(raw["run_id"], str) or not raw["run_id"] or not isinstance(arms, list) or not arms: raise ValueError("run_id and arms are required")
        arm_ids: set[str] = set()
        for arm in arms:
            item = _closed(arm, {"arm_id", "kind", "description"}, "arm")
            if not isinstance(item["arm_id"], str) or not item["arm_id"] or item["arm_id"] in arm_ids or item["kind"] not in ARM_KINDS or not isinstance(item["description"], str) or not item["description"]: raise ValueError("invalid arm registration")
            arm_ids.add(item["arm_id"])
        manifest_root = content_sha256({"run_id": raw["run_id"], "roots": roots, "arms": arms, "validation_strata": strata}, ensure_ascii=False, allow_nan=False)
        payload = {"run_id": raw["run_id"], "manifest_root": manifest_root, "task_suite_root": suite_root, "validation_strata": strata, "arms": arms}
        self.ledger.append("seed_registered", str(raw["run_id"]), roots, payload); self._replay(); self._checkpoint(); return self.status()

    def prepare(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        run = self._ready()
        if self.frozen is not None: raise ValueError("harness is frozen; edits and policy/router changes are rejected")
        raw = _closed(spec, {"candidate_id", "arm_id", "artifact_path", "roots", "evolver_id"}, "candidate spec")
        if raw["candidate_id"] in self.candidates: raise ValueError("candidate ID collision")
        arms = {str(row["arm_id"]) for row in run["arms"]}
        if raw["arm_id"] not in arms or not isinstance(raw["evolver_id"], str) or not raw["evolver_id"]: raise ValueError("unknown arm or evolver")
        roots = _roots(raw["roots"])
        if roots["previous_root"] != self.ledger.head or roots["task_suite_root"] != run["task_suite_root"]: raise ValueError("candidate previous/suite root mismatch")
        actual = _sha_tree(Path(str(raw["artifact_path"])))
        if roots["harness_root"] != actual: raise ValueError("candidate harness root mismatch")
        payload = {"candidate_id": raw["candidate_id"], "arm_id": raw["arm_id"], "artifact_path": str(Path(str(raw["artifact_path"])).resolve()), "evolver_id": raw["evolver_id"], "artifact_harness_root": actual, "candidate_root": content_sha256({"candidate_id": raw["candidate_id"], "roots": roots}, ensure_ascii=False, allow_nan=False)}
        self.ledger.append("candidate_prepared", str(raw["candidate_id"]), roots, payload); self._replay(); self._checkpoint(); return self.status()

    def record_validation(self, result: Mapping[str, Any], suite: Path) -> dict[str, Any]:
        run = self._ready()
        if self.frozen is not None: raise ValueError("harness is frozen; validation cannot update selection")
        raw = _closed(result, {"candidate_id", "executor_id", "task_scores", "cost", "failed_runs", "roots"}, "validation result")
        candidate_id = raw["candidate_id"]
        if candidate_id not in self.candidates or candidate_id in self.validation: raise ValueError("unknown or duplicate validation candidate")
        suite_root, valid_ids, _ = validation_metadata(suite); roots = _roots(raw["roots"])
        candidate = self.candidates[candidate_id]
        prep = next(event for event in self.ledger.events if event.event_type == "candidate_prepared" and event.logical_id == candidate_id)
        if roots["previous_root"] != self.ledger.head or suite_root != run["task_suite_root"] or any(roots[key] != prep.roots[key] for key in ROOT_FIELDS - {"previous_root"}): raise ValueError("validation roots mismatch")
        scores = raw["task_scores"]
        if not isinstance(raw["executor_id"], str) or not raw["executor_id"] or not isinstance(scores, list) or not scores: raise ValueError("external validation executor and scores are required")
        seen: set[str] = set()
        for score in scores:
            item = _closed(score, {"task_id", "score"}, "validation task score")
            if not isinstance(item["task_id"], str) or item["task_id"] not in valid_ids or item["task_id"] in seen or isinstance(item["score"], bool) or not isinstance(item["score"], (int, float)): raise ValueError("invalid validation score")
            seen.add(item["task_id"])
        if isinstance(raw["cost"], bool) or not isinstance(raw["cost"], (int, float)) or not isinstance(raw["failed_runs"], int) or raw["failed_runs"] < 0: raise ValueError("invalid validation cost/failures")
        payload = {"candidate_id": candidate_id, "executor_id": raw["executor_id"], "task_scores": scores, "cost": raw["cost"], "failed_runs": raw["failed_runs"], "validation_root": content_sha256({"candidate_root": candidate["candidate_root"], "scores": scores, "cost": raw["cost"], "failed_runs": raw["failed_runs"]}, ensure_ascii=False, allow_nan=False)}
        self.ledger.append("validation_recorded", str(candidate_id), roots, payload); self._replay(); self._checkpoint(); return self.status()

    def commit(self, candidate_id: str) -> dict[str, Any]:
        self._ready()
        if self.frozen is not None: raise ValueError("harness is frozen")
        if candidate_id not in self.validation or candidate_id in self.committed or candidate_id in self.rolled_back: raise ValueError("candidate cannot be committed")
        roots = dict(self.ledger.events[-1].roots); roots["previous_root"] = self.ledger.head
        roots["harness_root"] = self.candidates[candidate_id]["artifact_harness_root"]
        self.ledger.append("candidate_committed", candidate_id, roots, {"candidate_id": candidate_id, "validation_root": self.validation[candidate_id]["validation_root"]}); self._replay(); self._checkpoint(); return self.status()

    def rollback(self, candidate_id: str, reason: str) -> dict[str, Any]:
        self._ready()
        if self.frozen is not None: raise ValueError("harness is frozen")
        if not isinstance(reason, str) or not reason or candidate_id not in self.candidates or candidate_id in self.committed or candidate_id in self.rolled_back: raise ValueError("candidate cannot be rolled back")
        roots = dict(self.ledger.events[-1].roots); roots["previous_root"] = self.ledger.head
        self.ledger.append("candidate_rolled_back", candidate_id, roots, {"candidate_id": candidate_id, "reason": reason}); self._replay(); self._checkpoint(); return self.status()

    def freeze(self, candidate_id: str) -> dict[str, Any]:
        self._ready()
        if self.frozen is not None or candidate_id not in self.committed: raise ValueError("only one committed candidate can be frozen")
        roots = dict(self.ledger.events[-1].roots); roots["previous_root"] = self.ledger.head
        frozen_root = content_sha256({"candidate_id": candidate_id, "roots": roots, "validation_root": self.validation[candidate_id]["validation_root"]}, ensure_ascii=False, allow_nan=False)
        self.ledger.append("harness_frozen", candidate_id, roots, {"candidate_id": candidate_id, "frozen_root": frozen_root, "validation_only": True}); self._replay(); self._checkpoint(); return self.status()

    def export_eval(self, spec: Mapping[str, Any], output: Path) -> dict[str, Any]:
        self._ready()
        if self.frozen is None or self.request is not None: raise ValueError("freeze is required before a single evaluation export")
        raw = _closed(spec, {"request_id", "sealed_suite_root", "budget_root", "runtime_root", "scorer_contract_root"}, "evaluation request")
        for key, value in raw.items(): _root(value, key)
        roots = dict(self.ledger.events[-1].roots); roots["previous_root"] = self.ledger.head
        if any(raw[key] != roots[key] for key in ("budget_root", "runtime_root", "scorer_contract_root")): raise ValueError("evaluation contract root mismatch")
        request_root = content_sha256({"request_id": raw["request_id"], "frozen_root": self.frozen["frozen_root"], **raw}, ensure_ascii=False, allow_nan=False)
        payload = {**raw, "frozen_root": self.frozen["frozen_root"], "request_root": request_root, "sealed_task_content_included": False}
        self.ledger.append("sealed_evaluation_exported", str(raw["request_id"]), roots, payload); self._replay(); self._checkpoint()
        atomic_json_write(output, payload, ensure_ascii=False, allow_nan=False, indent=2, trailing_newline=True); return payload

    def ingest_eval(self, result: Mapping[str, Any]) -> dict[str, Any]:
        self._ready()
        if self.request is None or self.evaluation is not None: raise ValueError("exactly one exported request and no prior result are required")
        raw = _closed(result, {"request_root", "frozen_root", "sealed_suite_root", "budget_root", "runtime_root", "scorer_contract_root", "native_score", "cost", "failed_runs"}, "sealed result")
        for key in ("request_root", "frozen_root", "sealed_suite_root", "budget_root", "runtime_root", "scorer_contract_root"): _root(raw[key], key)
        if any(raw[key] != self.request[key] for key in ("request_root", "frozen_root", "sealed_suite_root", "budget_root", "runtime_root", "scorer_contract_root")): raise ValueError("external sealed result root mismatch")
        if isinstance(raw["native_score"], bool) or not isinstance(raw["native_score"], (int, float)) or isinstance(raw["cost"], bool) or not isinstance(raw["cost"], (int, float)) or not isinstance(raw["failed_runs"], int) or raw["failed_runs"] < 0: raise ValueError("invalid sealed result metrics")
        roots = dict(self.ledger.events[-1].roots); roots["previous_root"] = self.ledger.head
        self.ledger.append("sealed_evaluation_ingested", self.request["request_id"], roots, dict(raw)); self._replay(); self._checkpoint(); return self.status()

    def status(self) -> dict[str, Any]:
        validation_rows = []
        for candidate_id, receipt in sorted(self.validation.items()):
            scores = [float(row["score"]) for row in receipt["task_scores"]]
            validation_rows.append({"candidate_id": candidate_id, "mean_score": sum(scores) / len(scores), "task_count": len(scores), "cost": receipt["cost"], "failed_runs": receipt["failed_runs"], "committed": candidate_id in self.committed, "rolled_back": candidate_id in self.rolled_back})
        track_b = {"lane": "public_validation_selection_only", "validation": validation_rows, "rollback_count": len(self.rolled_back), "failed_runs": sum(int(row["failed_runs"]) for row in validation_rows), "resume_parity": "checkpoint-bound; audit verifies journal head", "memory_metric_pooling": "prohibited"}
        if self.evaluation is not None:
            track_b["external_sealed_evaluation"] = {"native_score": self.evaluation["native_score"], "cost": self.evaluation["cost"], "failed_runs": self.evaluation["failed_runs"]}
        return {"schema_version": SCHEMA, "journal_head": self.ledger.head, "event_count": len(self.ledger.events), "run_id": None if self.run is None else self.run["run_id"], "prepared": sorted(self.candidates), "validated": sorted(self.validation), "committed": sorted(self.committed), "rolled_back": sorted(self.rolled_back), "frozen_root": None if self.frozen is None else self.frozen["frozen_root"], "evaluation_request_root": None if self.request is None else self.request["request_root"], "evaluation_ingested": self.evaluation is not None, "track_b_report": track_b, "memory_metric_pooling": "prohibited"}

    def audit(self, suite: Path) -> dict[str, Any]:
        suite_root, _, strata = validation_metadata(suite); run = self._ready()
        if suite_root != run["task_suite_root"]: raise ValueError("audit validation suite root mismatch")
        # Re-open hashes, transition legality and V4 checkpoint compatibility fail-closed.
        HarnessLedger(self.directory); self._replay()
        cp = RunCheckpointStore(self.directory / "checkpoints").load_latest(manifest_sha256=run["manifest_root"], case_stream_sha256=run["task_suite_root"])
        outcomes = OutcomeJournal(self.directory / "outcomes.jsonl")
        if cp.next_position != len(self.ledger.events) or cp.pending_root != self.ledger.head or cp.outcome_head != outcomes.head or cp.outcome_count != len(outcomes.events): raise ValueError("checkpoint/outcome resume parity mismatch")
        return {"schema_version": SCHEMA, "audit_passed": True, "journal_head": self.ledger.head, "checkpoint_root": cp.checkpoint_sha256, "validation_task_count": 160, "validation_strata": strata, "evaluation_only_if_external": True, "memory_metric_pooling": "prohibited"}


def _arg_json(value: str) -> dict[str, Any]:
    try: return _closed(json.loads(value), set(json.loads(value).keys()), "JSON")
    except Exception as exc: raise argparse.ArgumentTypeError("must be a JSON object") from exc


def fake_lifecycle(*, run_dir: Path, validation_suite: Path, seed_harness: Path, output: Path) -> dict[str, Any]:
    """Deterministic local wiring fixture; it never executes a benchmark task.

    The single score is explicitly synthetic metadata used to exercise the
    lifecycle and must never be presented as an Evo-Bench result.
    """
    suite_root, task_ids, _ = validation_metadata(validation_suite)
    roots = {key: (GENESIS if key == "previous_root" else _sha_tree(seed_harness) if key == "harness_root" else suite_root if key == "task_suite_root" else content_sha256("fake-lifecycle:" + key)) for key in ROOT_FIELDS}
    run = HarnessRun(run_dir)
    run.init({"run_id": "p3d-local-fake-lifecycle", "seed_harness_path": str(seed_harness), "roots": roots,
              "arms": [{"arm_id": "seed", "kind": "seed", "description": "local seed"}, {"arm_id": "cmd", "kind": "cmd", "description": "local fixture"}]}, validation_suite)
    candidate = run_dir / "fake_candidate.json"; atomic_json_write(candidate, {"fixture": True, "network": False}, ensure_ascii=False, allow_nan=False, indent=2, trailing_newline=True)
    prepared = dict(roots); prepared["previous_root"] = run.ledger.head; prepared["harness_root"] = _sha_tree(candidate)
    run.prepare({"candidate_id": "local-fake", "arm_id": "cmd", "artifact_path": str(candidate), "roots": prepared, "evolver_id": "deterministic-local-fixture"})
    recorded = dict(prepared); recorded["previous_root"] = run.ledger.head
    run.record_validation({"candidate_id": "local-fake", "executor_id": "deterministic-local-fixture", "task_scores": [{"task_id": sorted(task_ids)[0], "score": 0.0}], "cost": 0.0, "failed_runs": 0, "roots": recorded}, validation_suite)
    run.commit("local-fake"); run.freeze("local-fake")
    request = run.export_eval({"request_id": content_sha256("p3d-local-fake-request"), "sealed_suite_root": content_sha256("opaque-not-opened"), "budget_root": roots["budget_root"], "runtime_root": roots["runtime_root"], "scorer_contract_root": roots["scorer_contract_root"]}, output)
    return {"fixture_only": True, "sealed_result_ingested": False, "request_root": request["request_root"], "audit": run.audit(validation_suite)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest="action", required=True)
    def common(p: argparse.ArgumentParser) -> None: p.add_argument("--run-dir", type=Path, required=True)
    p = sub.add_parser("init"); common(p); p.add_argument("--spec", type=Path, required=True); p.add_argument("--validation-suite", type=Path, required=True)
    p = sub.add_parser("prepare"); common(p); p.add_argument("--spec", type=Path, required=True)
    p = sub.add_parser("record-validation"); common(p); p.add_argument("--result", type=Path, required=True); p.add_argument("--validation-suite", type=Path, required=True)
    for name in ("commit", "rollback", "freeze"):
        p = sub.add_parser(name); common(p); p.add_argument("--candidate-id", required=True)
        if name == "rollback": p.add_argument("--reason", required=True)
    p = sub.add_parser("export-eval"); common(p); p.add_argument("--spec", type=Path, required=True); p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("ingest-eval"); common(p); p.add_argument("--result", type=Path, required=True)
    p = sub.add_parser("fake-lifecycle"); common(p); p.add_argument("--validation-suite", type=Path, required=True); p.add_argument("--seed-harness", type=Path, required=True); p.add_argument("--output", type=Path, required=True)
    for name in ("audit", "status"):
        p = sub.add_parser(name); common(p)
        if name == "audit": p.add_argument("--validation-suite", type=Path, required=True)
    args = parser.parse_args(argv); run = HarnessRun(args.run_dir)
    try:
        if args.action == "init": out = run.init(_load_json(args.spec), args.validation_suite)
        elif args.action == "prepare": out = run.prepare(_load_json(args.spec))
        elif args.action == "record-validation": out = run.record_validation(_load_json(args.result), args.validation_suite)
        elif args.action == "commit": out = run.commit(args.candidate_id)
        elif args.action == "rollback": out = run.rollback(args.candidate_id, args.reason)
        elif args.action == "freeze": out = run.freeze(args.candidate_id)
        elif args.action == "export-eval": out = run.export_eval(_load_json(args.spec), args.output)
        elif args.action == "ingest-eval": out = run.ingest_eval(_load_json(args.result))
        elif args.action == "audit": out = run.audit(args.validation_suite)
        elif args.action == "fake-lifecycle": out = fake_lifecycle(run_dir=args.run_dir, validation_suite=args.validation_suite, seed_harness=args.seed_harness, output=args.output)
        else: out = run.status()
        _write_stdout(out); return 0
    except ValueError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__": raise SystemExit(main())
