"""Dry-run/apply repair workflow for a Markdown memory directory."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import shutil
from typing import Callable
from uuid import uuid4

from ..adapters.memory_dir import load_memory_dir
from ..item_gate.bucketing import MemoryBucket, bucket_memory_items
from ..item_gate.freshness import FreshnessDecision, arbitrate_freshness


@dataclass(frozen=True)
class BucketRepair:
    bucket_id: str
    fingerprint: str
    item_ids: tuple[str, ...]
    decision: FreshnessDecision


@dataclass(frozen=True)
class StoreRepairPlan:
    memory_dir: str
    before_checksum: str
    buckets: tuple[BucketRepair, ...]

    @property
    def actionable(self) -> tuple[BucketRepair, ...]:
        return tuple(item for item in self.buckets if item.decision.applicable)


@dataclass(frozen=True)
class StoreRepairResult:
    mode: str
    applied: bool
    gate: str
    report_path: str
    snapshot_path: str
    before_checksum: str
    after_checksum: str
    rolled_back: bool
    demoted_ids: tuple[str, ...]


ValidationProbe = Callable[[Path, StoreRepairPlan], bool]


def plan_store_repair(
    memory_dir: str | Path,
    *,
    max_bucket_size: int = 5,
    similarity_threshold: float = 0.35,
    tolerance_days: int = 7,
) -> StoreRepairPlan:
    root = Path(memory_dir).resolve()
    items = load_memory_dir(root)
    buckets = bucket_memory_items(
        items,
        max_bucket_size=max_bucket_size,
        similarity_threshold=similarity_threshold,
    )
    repairs = tuple(
        _plan_bucket(bucket, tolerance_days=tolerance_days)
        for bucket in buckets
    )
    return StoreRepairPlan(
        memory_dir=str(root),
        before_checksum=memory_dir_checksum(root),
        buckets=repairs,
    )


def execute_store_repair(
    memory_dir: str | Path,
    *,
    mode: str = "dry-run",
    validation_probe: ValidationProbe | None = None,
    max_bucket_size: int = 5,
    similarity_threshold: float = 0.35,
    tolerance_days: int = 7,
) -> StoreRepairResult:
    if mode not in {"dry-run", "apply"}:
        raise ValueError("mode must be 'dry-run' or 'apply'")
    root = Path(memory_dir).resolve()
    plan = plan_store_repair(
        root,
        max_bucket_size=max_bucket_size,
        similarity_threshold=similarity_threshold,
        tolerance_days=tolerance_days,
    )
    private_dir = root / ".cmd"
    private_dir.mkdir(parents=True, exist_ok=True)
    report_path = private_dir / "repair-report.json"

    if mode == "dry-run" or not plan.actionable:
        gate = "dry_run_only" if mode == "dry-run" else "no_actionable_bucket"
        result = StoreRepairResult(
            mode=mode,
            applied=False,
            gate=gate,
            report_path=str(report_path),
            snapshot_path="",
            before_checksum=plan.before_checksum,
            after_checksum=plan.before_checksum,
            rolled_back=False,
            demoted_ids=(),
        )
        _write_report(report_path, plan, result)
        return result

    snapshot_path = _snapshot_memory_dir(root)
    demoted_ids = tuple(
        memory_id
        for repair in plan.actionable
        for memory_id in repair.decision.demoted_ids
    )
    _demote_files(root, demoted_ids)

    accepted = (
        validation_probe(root, plan)
        if validation_probe is not None
        else _retention_surrogate(root, plan)
    )
    rolled_back = False
    gate = "accepted_retention_surrogate"
    if validation_probe is not None:
        gate = "accepted_probe_replay" if accepted else "failed_probe_replay"
    if not accepted:
        _restore_snapshot(root, snapshot_path)
        rolled_back = True
        demoted_ids = ()
    after_checksum = memory_dir_checksum(root)
    if rolled_back and after_checksum != plan.before_checksum:
        raise RuntimeError("rollback checksum mismatch")

    result = StoreRepairResult(
        mode=mode,
        applied=accepted,
        gate=gate,
        report_path=str(report_path),
        snapshot_path=str(snapshot_path),
        before_checksum=plan.before_checksum,
        after_checksum=after_checksum,
        rolled_back=rolled_back,
        demoted_ids=demoted_ids,
    )
    _write_report(report_path, plan, result)
    return result


def memory_dir_checksum(memory_dir: str | Path) -> str:
    root = Path(memory_dir)
    digest = hashlib.sha256()
    for file_path in sorted(root.rglob("*.md")):
        relative = file_path.relative_to(root)
        if ".cmd" in relative.parts:
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _plan_bucket(bucket: MemoryBucket, *, tolerance_days: int) -> BucketRepair:
    decision = arbitrate_freshness(
        bucket.items,
        tolerance_days=tolerance_days,
    )
    return BucketRepair(
        bucket_id=bucket.bucket_id,
        fingerprint=bucket.fingerprint,
        item_ids=tuple(item.memory_id for item in bucket.items),
        decision=decision,
    )


def _snapshot_memory_dir(root: Path) -> Path:
    snapshot = root / ".cmd" / "snapshots" / uuid4().hex
    for file_path in sorted(root.rglob("*.md")):
        relative = file_path.relative_to(root)
        if ".cmd" in relative.parts:
            continue
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, target)
    return snapshot


def _demote_files(root: Path, memory_ids: tuple[str, ...]) -> None:
    for memory_id in memory_ids:
        source = root / f"{memory_id}.md"
        if not source.is_file():
            raise FileNotFoundError(f"memory item disappeared before apply: {source}")
        target = root / ".cmd" / "demoted" / f"{memory_id}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target = target.with_name(f"{target.stem}-{uuid4().hex}{target.suffix}")
        shutil.move(str(source), str(target))


def _retention_surrogate(root: Path, plan: StoreRepairPlan) -> bool:
    remaining = {item.memory_id for item in load_memory_dir(root)}
    for repair in plan.actionable:
        if not set(repair.decision.kept_ids) <= remaining:
            return False
        if set(repair.decision.demoted_ids) & remaining:
            return False
    return True


def _restore_snapshot(root: Path, snapshot: Path) -> None:
    for file_path in sorted(root.rglob("*.md")):
        relative = file_path.relative_to(root)
        if ".cmd" not in relative.parts:
            file_path.unlink()
    for file_path in sorted(snapshot.rglob("*.md")):
        relative = file_path.relative_to(snapshot)
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, target)


def _write_report(
    path: Path,
    plan: StoreRepairPlan,
    result: StoreRepairResult,
) -> None:
    payload = {
        "plan": asdict(plan),
        "result": asdict(result),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
