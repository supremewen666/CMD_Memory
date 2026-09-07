"""Family-blocked runtime partitions for transfer experiments."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Mapping, Sequence

from .contracts import canonical_sha256
from .prequential_executor import RuntimeOrderManifest, RuntimeOrderRow
from .runtime_bundle import RuntimeBundle
from .splits import SPLITS


def load_split_assignments(path: str | Path) -> Mapping[str, str]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    assignments = raw.get("assignments") if isinstance(raw, Mapping) else None
    if not isinstance(assignments, Mapping):
        raise ValueError("split manifest lacks case assignments")
    parsed: dict[str, str] = {}
    for case_id, split in assignments.items():
        if not isinstance(case_id, str) or not case_id or split not in SPLITS:
            raise ValueError("split manifest contains an invalid assignment")
        parsed[case_id] = str(split)
    return parsed


def select_runtime_splits(
    bundles: Sequence[RuntimeBundle],
    order: RuntimeOrderManifest,
    split_manifest: str | Path,
    included_splits: Sequence[str],
) -> tuple[tuple[RuntimeBundle, ...], RuntimeOrderManifest, dict[str, object]]:
    """Select whole blocked families and rebuild a contiguous delayed order."""
    selected_splits = tuple(dict.fromkeys(included_splits))
    if not selected_splits or any(split not in SPLITS for split in selected_splits):
        raise ValueError("included splits must be non-empty spec-v0.3 split names")
    order.verify()
    by_id = {bundle.case_id: bundle for bundle in bundles}
    if not by_id or len(by_id) != len(bundles) or set(by_id) != {row.case_id for row in order.rows}:
        raise ValueError("runtime cases and order must be the same strict permutation")
    assignments = load_split_assignments(split_manifest)
    if set(assignments) != set(by_id):
        raise ValueError("split manifest and runtime cases must contain the same case IDs")

    family_splits: dict[str, set[str]] = {}
    for bundle in bundles:
        family_splits.setdefault(bundle.family_id, set()).add(assignments[bundle.case_id])
    leaking = sorted(family for family, values in family_splits.items() if len(values) != 1)
    if leaking:
        raise ValueError("split manifest leaks a family across partitions")

    selected_ids = {
        case_id for case_id, split in assignments.items() if split in selected_splits
    }
    if not selected_ids:
        raise ValueError("selected runtime split is empty")
    selected_bundles = tuple(bundle for bundle in bundles if bundle.case_id in selected_ids)
    selected_rows: list[RuntimeOrderRow] = []
    for old_row in order.rows:
        if old_row.case_id not in selected_ids:
            continue
        event_index = len(selected_rows)
        maturity_delay = old_row.receipt_matures_at - old_row.event_index
        selected_rows.append(RuntimeOrderRow(
            case_id=old_row.case_id,
            event_index=event_index,
            regime=old_row.regime,
            receipt_matures_at=event_index + maturity_delay,
            cas_interleaving=old_row.cas_interleaving,
        ))
    body = {
        "seed": order.seed,
        "schedule": order.schedule,
        "rows": [asdict(row) for row in selected_rows],
    }
    selected_order = RuntimeOrderManifest(
        seed=order.seed,
        schedule=order.schedule,
        rows=tuple(selected_rows),
        source_content_sha256=canonical_sha256(body),
    )
    selected_order.verify()

    selected_families = {bundle.family_id for bundle in selected_bundles}
    excluded_families = {bundle.family_id for bundle in bundles} - selected_families
    overlap = sorted(selected_families & excluded_families)
    if overlap:
        raise ValueError("selected and excluded runtime partitions overlap by family")
    audit = {
        "schema_version": "cmd-spec-v03-family-disjoint-audit-v1",
        "included_splits": list(selected_splits),
        "selected_case_count": len(selected_bundles),
        "selected_family_count": len(selected_families),
        "excluded_family_count": len(excluded_families),
        "family_overlap_count": 0,
        "order_schedule": order.schedule,
        "order_seed": order.seed,
    }
    return selected_bundles, selected_order, audit
