"""Fail-closed, split-aware compiler for CMD-RepairStream F-DATA bundles."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid
from typing import Iterable, Mapping

from .contracts import canonical_sha256
from .runtime_bundle import deserialize as deserialize_runtime_bundle
from .event_order import compile_event_order
from .repair_stream import ALL_TEMPLATES, RepairCase, build_intervention, build_shadow_matrix, compile_repair_case, iter_public_episodes, operator_catalog, supported_templates
from .source_audit import DownloadAuditReport, audit_downloads
from .splits import SPLITS, build_lockbox_manifest, build_split_manifest


FREEZE_SCHEMA_VERSION = "cmd-spec-v03-f-data-freeze-v2"
DEVELOPMENT_STATUS = "DEVELOPMENT_NON_CONFIRMATORY_NOT_F_DATA_FROZEN"
FROZEN_STATUS = "F_DATA_FROZEN"
_PUBLIC_SOURCES = frozenset({"locomo", "halumem", "memfail", "memtracebench"})
_KNOWN_SOURCES = _PUBLIC_SOURCES | frozenset({"stale", "memsecbench", "memevobench", "longmemeval", "evo_memory", "evo_bench"})
_INCIDENTS = frozenset({"clean", "process_fault", "state_drift", "poison"})
_EXCEPTION_TEMPLATES = tuple(template for template in ALL_TEMPLATES if template != "clean")


class FreezeError(ValueError):
    """A condition that makes a frozen bundle unpublishable."""


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_file(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return _file_sha256(path)


def _require_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise FreezeError(f"{field} must be a mapping")
    return value


def _quota_mapping(value: object, field: str, allowed: Iterable[str]) -> dict[str, int | None]:
    raw = _require_mapping(value, field)
    allowed_names = set(allowed)
    result: dict[str, int | None] = {}
    for key, quota in raw.items():
        name = str(key).casefold()
        if name not in allowed_names:
            raise FreezeError(f"{field} contains unsupported key: {key}")
        if quota is not None and (isinstance(quota, bool) or not isinstance(quota, int) or quota < 0):
            raise FreezeError(f"{field}.{name} must be a non-negative integer or null")
        result[name] = quota
    return result


def _split_mapping(value: object, field: str, allowed_keys: Iterable[str] | None = None) -> dict[str, str]:
    raw = _require_mapping(value, field)
    keys = set(allowed_keys) if allowed_keys is not None else None
    result: dict[str, str] = {}
    for key, split in raw.items():
        name = str(key)
        if keys is not None and name not in keys:
            raise FreezeError(f"{field} contains unsupported key: {key}")
        if split not in SPLITS:
            raise FreezeError(f"{field}.{name} contains unknown split: {split}")
        result[name] = str(split)
    return result


@dataclass(frozen=True)
class FreezeConfig:
    """Hashed quota and partition contract for one compilation."""

    source_quotas: Mapping[str, int | None]
    incident_quotas: Mapping[str, int | None]
    template_quotas: Mapping[str, int | None]
    order_seeds: tuple[int, ...]
    order_schedule: str
    case_seed: int
    split_seed: int
    template_partition: Mapping[str, str]
    episode_split_assignments: Mapping[str, str]
    forced_split_assignments: Mapping[str, str]
    lockbox_custodian: str | None

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "FreezeConfig":
        allowed = {"source_quotas", "incident_quotas", "template_quotas", "order_seeds", "order_schedule", "case_seed", "split_seed", "template_partition", "episode_split_assignments", "forced_split_assignments", "lockbox_custodian"}
        unknown = set(value) - allowed
        if unknown:
            raise FreezeError(f"unknown freeze config keys: {sorted(unknown)}")
        source_quotas = _quota_mapping(value.get("source_quotas"), "source_quotas", _KNOWN_SOURCES)
        if len(source_quotas) < 2:
            raise FreezeError("source_quotas must request at least two sources")
        incident_quotas = _quota_mapping(value.get("incident_quotas", {}), "incident_quotas", _INCIDENTS)
        template_quotas = _quota_mapping(value.get("template_quotas", {}), "template_quotas", ALL_TEMPLATES)
        seeds = value.get("order_seeds")
        if not isinstance(seeds, list) or not 3 <= len(seeds) <= 5 or len(set(seeds)) != len(seeds):
            raise FreezeError("order_seeds must contain 3-5 unique integers")
        if any(isinstance(seed, bool) or not isinstance(seed, int) for seed in seeds):
            raise FreezeError("order_seeds must contain integers")
        schedule = value.get("order_schedule", "stationary")
        if schedule not in {"stationary", "abrupt_process_state_poison", "recurring_a_b_a"}:
            raise FreezeError("unsupported order_schedule")
        case_seed, split_seed = value.get("case_seed", 20260827), value.get("split_seed", 20260827)
        if any(isinstance(seed, bool) or not isinstance(seed, int) for seed in (case_seed, split_seed)):
            raise FreezeError("case_seed and split_seed must be integers")
        partition = {template: SPLITS[index % len(SPLITS)] for index, template in enumerate(_EXCEPTION_TEMPLATES)}
        partition.update(_split_mapping(value.get("template_partition", {}), "template_partition", _EXCEPTION_TEMPLATES))
        episode_splits = _split_mapping(value.get("episode_split_assignments", {}), "episode_split_assignments")
        forced = _split_mapping(value.get("forced_split_assignments", {}), "forced_split_assignments")
        custodian = value.get("lockbox_custodian")
        if custodian is not None and (not isinstance(custodian, str) or not custodian.strip()):
            raise FreezeError("lockbox_custodian must be a non-empty string")
        return cls(source_quotas, incident_quotas, template_quotas, tuple(seeds), str(schedule), case_seed, split_seed, partition, episode_splits, forced, custodian)

    def to_mapping(self) -> dict[str, object]:
        return {"source_quotas": dict(sorted(self.source_quotas.items())), "incident_quotas": dict(sorted(self.incident_quotas.items())), "template_quotas": dict(sorted(self.template_quotas.items())), "order_seeds": list(self.order_seeds), "order_schedule": self.order_schedule, "case_seed": self.case_seed, "split_seed": self.split_seed, "template_partition": dict(sorted(self.template_partition.items())), "episode_split_assignments": dict(sorted(self.episode_split_assignments.items())), "forced_split_assignments": dict(sorted(self.forced_split_assignments.items())), "lockbox_custodian": self.lockbox_custodian}


def _collect_episodes(config: FreezeConfig, audit: DownloadAuditReport, group_a_root: Path) -> list[object]:
    statuses = {row.dataset_id: row for row in audit.datasets}
    episodes: list[object] = []
    for source, quota in sorted(config.source_quotas.items()):
        status = statuses.get(source)
        if status is None or not status.executable:
            reason = "not registered" if status is None else "; ".join(status.errors)
            raise FreezeError(f"blocked source requested: {source}: {reason}")
        rows = list(iter_public_episodes(source, group_a_root))
        if quota is not None and len(rows) < quota:
            raise FreezeError(f"source quota unavailable: {source} requested={quota} available={len(rows)}")
        selected = rows if quota is None else rows[:quota]
        if not selected:
            raise FreezeError(f"source quota produced no episodes: {source}")
        episodes.extend(selected)
    return episodes


def _episode_partition(episodes: Iterable[object], config: FreezeConfig) -> dict[str, str]:
    result: dict[str, str] = {}
    for index, episode in enumerate(episodes):
        result[episode.episode_id] = config.episode_split_assignments.get(episode.episode_id, SPLITS[index % len(SPLITS)])
    unknown = set(config.episode_split_assignments) - set(result)
    if unknown:
        raise FreezeError(f"episode_split_assignments references unknown episodes: {sorted(unknown)}")
    return result


def _compile_cases(episodes: Iterable[object], episode_partition: Mapping[str, str], config: FreezeConfig) -> tuple[list[RepairCase], dict[str, dict[str, str]]]:
    candidates: list[RepairCase] = []
    unsupported: dict[str, dict[str, str]] = {}
    for episode in episodes:
        capability = supported_templates(episode)
        unsupported[episode.episode_id] = {name: status for name, status in capability.items() if status != "supported"}
        for template in ALL_TEMPLATES:
            if capability.get(template) != "supported":
                continue
            if template != "clean" and config.template_partition[template] != episode_partition[episode.episode_id]:
                continue
            candidates.append(compile_repair_case(episode, build_intervention(episode, template, seed=config.case_seed)))
    candidates.sort(key=lambda case: canonical_sha256({"case": case.case_id, "template": case.intervention.template_id}))
    selected: list[RepairCase] = []
    selected_ids: set[str] = set()
    template_counts: dict[str, int] = {}
    incident_counts: dict[str, int] = {}

    def add(case: RepairCase) -> None:
        if case.case_id not in selected_ids:
            selected.append(case)
            selected_ids.add(case.case_id)
            template_counts[case.intervention.template_id] = template_counts.get(case.intervention.template_id, 0) + 1
            incident = case.intervention.incident_type
            incident_counts[incident] = incident_counts.get(incident, 0) + 1

    for template, quota in config.template_quotas.items():
        if quota is None:
            continue
        rows = [case for case in candidates if case.intervention.template_id == template]
        if len(rows) < quota:
            raise FreezeError(f"template quota unavailable: {template} requested={quota} available={len(rows)}")
        for case in rows[:quota]:
            add(case)
    for incident, quota in config.incident_quotas.items():
        if quota is None:
            continue
        if incident_counts.get(incident, 0) > quota:
            raise FreezeError(f"incident quota conflict: {incident} requested={quota} preselected={incident_counts[incident]}")
        needed = quota - incident_counts.get(incident, 0)
        rows = [case for case in candidates if case.intervention.incident_type == incident and case.case_id not in selected_ids]
        if len(rows) < needed:
            raise FreezeError(f"incident quota unavailable: {incident} requested={quota} available={incident_counts.get(incident, 0) + len(rows)}")
        for case in rows[:needed]:
            add(case)
    for case in candidates:
        if case.case_id in selected_ids:
            continue
        if config.template_quotas.get(case.intervention.template_id) is not None or config.incident_quotas.get(case.intervention.incident_type) is not None:
            continue
        add(case)
    for template, quota in config.template_quotas.items():
        if quota is not None and template_counts.get(template, 0) != quota:
            raise FreezeError(f"template quota mismatch: {template}")
    for incident, quota in config.incident_quotas.items():
        if quota is not None and incident_counts.get(incident, 0) != quota:
            raise FreezeError(f"incident quota mismatch: {incident}")
    missing = sorted(_INCIDENTS - set(incident_counts))
    if missing:
        raise FreezeError(f"empty incident class after quotas: {', '.join(missing)}")
    return selected, unsupported


def _block_keys(cases: Iterable[RepairCase]) -> dict[str, tuple[str, ...]]:
    blocks: dict[str, tuple[str, ...]] = {}
    for case in cases:
        keys = [f"family:{case.family_id}", f"source_episode:{case.source_episode_id}"]
        if case.intervention.template_id != "clean":
            keys.append(f"constructor:{case.intervention.constructor_family}:{case.intervention.template_id}")
            trigger = case.intervention.expected_effect.get("trigger")
            if trigger is not None:
                keys.append(f"trigger:{trigger}")
        blocks[case.case_id] = tuple(keys)
    return blocks


def _case_assignments(cases: Iterable[RepairCase], episodes: Mapping[str, str], configured: Mapping[str, str]) -> dict[str, str]:
    result = {case.case_id: episodes[case.source_episode_id] for case in cases}
    unknown = set(configured) - set(result)
    if unknown:
        raise FreezeError(f"forced_split_assignments references unknown cases: {sorted(unknown)}")
    for case_id, split in configured.items():
        if result[case_id] != split:
            raise FreezeError(f"split conflict: case {case_id} is source-episode blocked to {result[case_id]}, not {split}")
    return result


def _assert_partition(cases: list[RepairCase], assignments: Mapping[str, str], partition: Mapping[str, str]) -> None:
    by_episode: dict[str, set[str]] = {}
    by_template: dict[str, set[str]] = {}
    by_trigger: dict[str, set[str]] = {}
    for case in cases:
        split = assignments[case.case_id]
        by_episode.setdefault(case.source_episode_id, set()).add(split)
        by_template.setdefault(case.intervention.template_id, set()).add(split)
        trigger = case.intervention.expected_effect.get("trigger")
        if trigger is not None:
            by_trigger.setdefault(str(trigger), set()).add(split)
    if any(len(splits) != 1 for splits in by_episode.values()):
        raise FreezeError("source episode split cardinality exceeds one")
    for template, splits in by_template.items():
        # ``clean`` is deliberately episode-bound rather than a global
        # constructor family; every exception template is global-blocked.
        if template == "clean":
            continue
        if len(splits) != 1:
            raise FreezeError(f"template split cardinality exceeds one: {template}")
        if next(iter(splits)) != partition[template]:
            raise FreezeError(f"template partition mismatch: {template}")
    if any(len(splits) != 1 for splits in by_trigger.values()):
        raise FreezeError("trigger split cardinality exceeds one")


def _runtime_leak_scan(runtime_cases: list[dict[str, object]]) -> None:
    for row in runtime_cases:
        try:
            deserialize_runtime_bundle(json.loads(json.dumps(row, sort_keys=True)))
        except (KeyError, TypeError, ValueError) as exc:
            raise FreezeError(f"runtime leak scan failed: {exc}") from exc


def _compiler_closure() -> tuple[dict[str, str], str]:
    directory = Path(__file__).parent
    files = ("freeze.py", "runtime_bundle.py", "repair_stream.py", "splits.py", "event_order.py", "contracts.py", "source_audit.py")
    closure = {name: _file_sha256(directory / name) for name in files}
    return closure, canonical_sha256(closure)


def _logical_path(runtime_root: Path, sealed_root: Path, name: str) -> Path:
    prefix, relative = name.split("/", 1)
    if prefix == "runtime":
        return runtime_root / "runtime" / relative
    if prefix == "sealed":
        return sealed_root / relative
    raise FreezeError(f"invalid logical access path: {name}")


def _verify_bundle(runtime_root: Path, sealed_root: Path, manifest: Mapping[str, object]) -> None:
    body = dict(manifest)
    actual_body_hash = body.pop("manifest_body_sha256", None)
    if actual_body_hash != canonical_sha256(body):
        raise FreezeError("manifest_body_sha256 mismatch")
    checksums = _require_mapping(manifest.get("checksums"), "manifest.checksums")
    for name, expected in checksums.items():
        if not isinstance(name, str) or not isinstance(expected, str) or _file_sha256(_logical_path(runtime_root, sealed_root, name)) != expected:
            raise FreezeError(f"checksum mismatch after staging: {name}")
    visible = json.loads(_logical_path(runtime_root, sealed_root, "runtime/runtime_cases.json").read_text(encoding="utf-8"))
    locked = json.loads(_logical_path(runtime_root, sealed_root, "sealed/lockbox/runtime_cases.json").read_text(encoding="utf-8"))
    if not isinstance(visible, list) or not isinstance(locked, list):
        raise FreezeError("runtime case files must be JSON lists")
    _runtime_leak_scan(visible + locked)
    _runtime_leak_scan(locked)
    split = _require_mapping(json.loads(_logical_path(runtime_root, sealed_root, "sealed/lockbox/split_manifest.json").read_text(encoding="utf-8")), "split manifest")
    lockbox = _require_mapping(json.loads(_logical_path(runtime_root, sealed_root, "sealed/lockbox/lockbox_manifest.json").read_text(encoding="utf-8")), "lockbox manifest")
    if split.get("content_sha256") != manifest.get("split_manifest_sha256") or lockbox.get("split_manifest_sha256") != split.get("content_sha256"):
        raise FreezeError("split/lockbox manifest binding mismatch")
    expected_ids = set(split.get("assignments", {}))
    actual_ids = {row.get("case_id") for row in visible + locked if isinstance(row, Mapping)}
    if actual_ids != expected_ids or len(visible) + len(locked) != len(actual_ids):
        raise FreezeError("runtime case completeness mismatch")
    order_paths = manifest.get("order_manifest_sha256s")
    if not isinstance(order_paths, Mapping):
        raise FreezeError("order manifest checksum mapping missing")
    for name in order_paths:
        order = _require_mapping(json.loads(_logical_path(runtime_root, sealed_root, str(name)).read_text(encoding="utf-8")), "order manifest")
        rows = order.get("rows")
        if not isinstance(rows, list) or {row.get("case_id") for row in rows if isinstance(row, Mapping)} != expected_ids:
            raise FreezeError("order manifest case completeness mismatch")
        if sorted(row.get("event_index") for row in rows if isinstance(row, Mapping)) != list(range(len(expected_ids))):
            raise FreezeError("order manifest event-index integrity mismatch")
    shadow = json.loads(_logical_path(runtime_root, sealed_root, "sealed/shadow_matrix.json").read_text(encoding="utf-8"))
    if not isinstance(shadow, list) or {row.get("case_id") for row in shadow if isinstance(row, Mapping)} != expected_ids:
        raise FreezeError("shadow matrix case completeness mismatch")


def compile_freeze_bundle(config: FreezeConfig | Mapping[str, object], *, output_dir: str | Path, group_a_root: str | Path = "data/external/group_a", group_b_root: str | Path = "data/external/group_b", freeze_id: str | None = None, acknowledge_lockbox: bool = False, sealed_output_dir: str | Path | None = None) -> dict[str, object]:
    """Compile and verify both trees, then publish runtime as the commit point.

    A filesystem cannot atomically rename two independent directories.  For a
    confirmatory freeze the sealed tree is therefore published first and the
    runtime tree last.  A visible runtime directory is the publication marker;
    failures during the second rename remove the sealed tree created here.
    """
    resolved = config if isinstance(config, FreezeConfig) else FreezeConfig.from_mapping(config)
    if freeze_id is not None and (not isinstance(freeze_id, str) or not freeze_id.strip()):
        raise FreezeError("freeze_id must be non-empty when supplied")
    status = FROZEN_STATUS if freeze_id and acknowledge_lockbox else DEVELOPMENT_STATUS
    output = Path(output_dir)
    if output.exists():
        raise FreezeError(f"refusing to overwrite existing bundle: {output}")
    if status == FROZEN_STATUS and sealed_output_dir is None and resolved.lockbox_custodian is None:
        raise FreezeError("F_DATA_FROZEN requires --sealed-output-dir or lockbox_custodian")
    sealed_target = Path(sealed_output_dir) if sealed_output_dir is not None else output.parent / f"{output.name}.sealed"
    if status == FROZEN_STATUS and (sealed_target == output or sealed_target.exists()):
        raise FreezeError("F_DATA_FROZEN sealed output must be a distinct, new directory")
    audit = audit_downloads(Path(group_a_root), Path(group_b_root))
    episodes = _collect_episodes(resolved, audit, Path(group_a_root))
    episode_partition = _episode_partition(episodes, resolved)
    cases, unsupported = _compile_cases(episodes, episode_partition, resolved)
    forced = _case_assignments(cases, episode_partition, resolved.forced_split_assignments)
    blocks = _block_keys(cases)
    sealed_published = False
    try:
        split = build_split_manifest([case.decision_view for case in cases], seed=resolved.split_seed, extra_block_keys=blocks, forced_assignments=forced)
    except ValueError as exc:
        raise FreezeError(f"split conflict: {exc}") from exc
    _assert_partition(cases, split.assignments, resolved.template_partition)
    lockbox = build_lockbox_manifest(split)
    matrices = [build_shadow_matrix(case) for case in cases]
    orders = [compile_event_order(cases, seed=seed, schedule=resolved.order_schedule) for seed in resolved.order_seeds]
    lockbox_splits = set(lockbox.lockbox_splits)
    visible_cases = [case.public_mapping() for case in cases if split.assignments[case.case_id] not in lockbox_splits]
    lockbox_cases = [case.public_mapping() for case in cases if split.assignments[case.case_id] in lockbox_splits]
    _runtime_leak_scan(visible_cases)
    output.parent.mkdir(parents=True, exist_ok=True)
    runtime_staging = output.parent / f".{output.name}.staging-{uuid.uuid4().hex}"
    sealed_staging = runtime_staging / "sealed" if status != FROZEN_STATUS else sealed_target.parent / f".{sealed_target.name}.staging-{uuid.uuid4().hex}"
    try:
        runtime_staging.mkdir()
        if sealed_staging != runtime_staging / "sealed":
            sealed_staging.parent.mkdir(parents=True, exist_ok=True)
            sealed_staging.mkdir()
        checksums = {
            "runtime/runtime_cases.json": _canonical_file(_logical_path(runtime_staging, sealed_staging, "runtime/runtime_cases.json"), visible_cases),
            "runtime/source_checksums.json": _canonical_file(_logical_path(runtime_staging, sealed_staging, "runtime/source_checksums.json"), {episode.source_path: episode.source_sha256 for episode in episodes}),
            "runtime/operator_catalog.json": _canonical_file(_logical_path(runtime_staging, sealed_staging, "runtime/operator_catalog.json"), [asdict(operator) for operator in operator_catalog()]),
            "sealed/source_episodes.json": _canonical_file(_logical_path(runtime_staging, sealed_staging, "sealed/source_episodes.json"), [episode.public_mapping() for episode in episodes]),
            "sealed/evaluator_sidecar.json": _canonical_file(_logical_path(runtime_staging, sealed_staging, "sealed/evaluator_sidecar.json"), {"episodes": [episode.evaluator_mapping() for episode in episodes], "cases": [case.evaluator_mapping() for case in cases]}),
            "sealed/shadow_matrix.json": _canonical_file(_logical_path(runtime_staging, sealed_staging, "sealed/shadow_matrix.json"), [asdict(matrix) for matrix in matrices]),
            "sealed/lockbox/runtime_cases.json": _canonical_file(_logical_path(runtime_staging, sealed_staging, "sealed/lockbox/runtime_cases.json"), lockbox_cases),
            "sealed/lockbox/split_manifest.json": _canonical_file(_logical_path(runtime_staging, sealed_staging, "sealed/lockbox/split_manifest.json"), split.to_mapping()),
            "sealed/lockbox/lockbox_manifest.json": _canonical_file(_logical_path(runtime_staging, sealed_staging, "sealed/lockbox/lockbox_manifest.json"), lockbox.to_mapping()),
        }
        order_checksums: dict[str, str] = {}
        for order in orders:
            name = f"sealed/lockbox/order_manifest_seed_{order.seed}.json"
            order_checksums[name] = _canonical_file(_logical_path(runtime_staging, sealed_staging, name), order.to_mapping())
        checksums.update(order_checksums)
        closure, closure_hash = _compiler_closure()
        config_mapping = resolved.to_mapping()
        template_splits = {
            template: sorted(splits)
            for template in _EXCEPTION_TEMPLATES
            if (splits := {split.assignments[case.case_id] for case in cases if case.intervention.template_id == template})
        }
        manifest = {
            "schema_version": FREEZE_SCHEMA_VERSION, "status": status,
            "confirmation": {"non_confirmatory": status != FROZEN_STATUS, "freeze_id": freeze_id if status == FROZEN_STATUS else None, "acknowledge_lockbox": bool(acknowledge_lockbox), "lockbox_custodian": resolved.lockbox_custodian},
            "config": config_mapping, "config_sha256": canonical_sha256(config_mapping), "compiler_closure_sha256": closure_hash, "compiler_closure": closure, "source_audit_sha256": audit.report_sha256,
            "source_episode_count": len(episodes), "case_count": len(cases), "incident_counts": {incident: sum(case.intervention.incident_type == incident for case in cases) for incident in sorted(_INCIDENTS)}, "template_counts": {template: sum(case.intervention.template_id == template for case in cases) for template in ALL_TEMPLATES},
            "episode_partition": episode_partition, "template_partition": dict(sorted(resolved.template_partition.items())), "exception_template_split_cardinality": template_splits, "unsupported_capabilities": unsupported,
            "blocking": {"keys_by_case": blocks, "dimensions": ["family", "source_episode", "constructor", "trigger"]}, "split_manifest_sha256": split.content_sha256, "lockbox_manifest_sha256": lockbox.content_sha256, "order_manifest_sha256s": order_checksums,
            "access_classes": {name: ("runtime" if name.startswith("runtime/") else "sealed_lockbox" if name.startswith("sealed/lockbox/") else "sealed") for name in checksums}, "checksums": checksums,
        }
        manifest["manifest_body_sha256"] = canonical_sha256(manifest)
        _canonical_file(runtime_staging / "f_data_manifest.json", manifest)
        _verify_bundle(runtime_staging, sealed_staging, manifest)
        if status == FROZEN_STATUS:
            os.replace(sealed_staging, sealed_target)
            sealed_published = True
        os.replace(runtime_staging, output)
        return manifest
    except Exception:
        if runtime_staging.exists():
            shutil.rmtree(runtime_staging)
        if sealed_staging.exists() and sealed_staging != runtime_staging / "sealed":
            shutil.rmtree(sealed_staging)
        if sealed_published and sealed_target.exists() and not output.exists():
            shutil.rmtree(sealed_target)
        raise
