"""Lightweight case metadata and phase-labelled event-order compilation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import random
from typing import Mapping, Sequence

from .contracts import canonical_sha256
from .event_order import EventOrderManifest, OrderedCase
from .repair_stream import (
    ALL_TEMPLATES,
    build_intervention,
    iter_public_episodes,
    supported_templates,
)
from .splits import SPLITS


@dataclass(frozen=True)
class CaseOrderMetadata:
    case_id: str
    family_id: str
    source_episode_id: str
    source_dataset_id: str
    incident_type: str

    def to_mapping(self) -> dict[str, str]:
        return asdict(self)


def compile_case_order_metadata(
    source: str,
    *,
    group_a_root: str | Path,
    limit: int,
    case_seed: int,
) -> tuple[CaseOrderMetadata, ...]:
    """Reproduce pilot case identities without serializing materialized states."""
    if limit < 1:
        raise ValueError("limit must be positive")
    episodes = []
    for episode in iter_public_episodes(source, Path(group_a_root)):
        episodes.append(episode)
        if len(episodes) >= limit:
            break
    if not episodes:
        raise ValueError(f"source has no executable episodes: {source}")

    episode_splits = {
        episode.episode_id: SPLITS[index % len(SPLITS)]
        for index, episode in enumerate(episodes)
    }
    templates = tuple(template for template in ALL_TEMPLATES if template != "clean")
    template_partition = {
        template: SPLITS[index % len(SPLITS)]
        for index, template in enumerate(templates)
    }
    rows: list[CaseOrderMetadata] = []
    for episode in episodes:
        capability = supported_templates(episode)
        for template in ALL_TEMPLATES:
            if capability.get(template) != "supported":
                continue
            if template != "clean" and template_partition[template] != episode_splits[episode.episode_id]:
                continue
            intervention = build_intervention(episode, template, seed=case_seed)
            case_id = "case-" + canonical_sha256(
                {"episode": episode.episode_id, "intervention": intervention.content_sha256}
            )
            rows.append(
                CaseOrderMetadata(
                    case_id=case_id,
                    family_id=f"{episode.source_dataset_id}:{episode.episode_id}",
                    source_episode_id=episode.episode_id,
                    source_dataset_id=episode.source_dataset_id,
                    incident_type=intervention.incident_type,
                )
            )
    if len({row.case_id for row in rows}) != len(rows):
        raise ValueError("compiled case metadata contains duplicate case IDs")
    return tuple(rows)


def verify_split_case_ids(
    rows: Sequence[CaseOrderMetadata], split_manifest: str | Path
) -> None:
    raw = json.loads(Path(split_manifest).read_text(encoding="utf-8"))
    assignments = raw.get("assignments") if isinstance(raw, Mapping) else None
    if not isinstance(assignments, Mapping):
        raise ValueError("split manifest lacks case assignments")
    expected = {row.case_id for row in rows}
    if expected != set(assignments):
        raise ValueError("case metadata does not match the frozen split manifest")


def compile_phase_labelled_recurring_order(
    rows: Sequence[CaseOrderMetadata], *, seed: int, maturity_delay: int = 2
) -> EventOrderManifest:
    """Compile process A -> state/poison B -> process A' with explicit phases."""
    if maturity_delay < 1:
        raise ValueError("maturity delay must be positive")
    shuffled = list(rows)
    random.Random(f"spec-v03-order:{seed}:recurring_a_b_a").shuffle(shuffled)
    process = [row for row in shuffled if row.incident_type == "process_fault"]
    shifted = [row for row in shuffled if row.incident_type in {"state_drift", "poison"}]
    clean = [row for row in shuffled if row.incident_type == "clean"]
    if len(process) < 2 or not shifted:
        raise ValueError(
            "phase-labelled recurring A-B-A requires two process cases and one state/poison case"
        )
    midpoint = max(1, min(len(process) - 1, len(process) // 2))
    ordered = process[:midpoint] + shifted + process[midpoint:]
    clean_rng = random.Random(f"spec-v03-recurring-clean:{seed}")
    for row in clean:
        ordered.insert(clean_rng.randrange(len(ordered) + 1), row)

    shifted_indexes = [
        index
        for index, row in enumerate(ordered)
        if row.incident_type in {"state_drift", "poison"}
    ]
    first_shift, last_shift = min(shifted_indexes), max(shifted_indexes)
    phases = []
    for index in range(len(ordered)):
        if index < first_shift:
            phases.append("recurring_a_stationary")
        elif index <= last_shift:
            phases.append("recurring_b_abrupt")
        else:
            phases.append("recurring_a_return_stationary")

    cas = ["conflicting", "benign"]
    while len(cas) < len(ordered):
        value = random.Random(f"cas:{seed}:{len(cas)}").randrange(2)
        cas.append("conflicting" if value else "benign")
    random.Random(f"cas-shuffle:{seed}").shuffle(cas)
    order_rows = tuple(
        OrderedCase(row.case_id, index, phases[index], index + maturity_delay, cas[index])
        for index, row in enumerate(ordered)
    )
    body = {
        "seed": seed,
        "schedule": "recurring_a_b_a",
        "rows": [asdict(row) for row in order_rows],
    }
    return EventOrderManifest(seed, "recurring_a_b_a", order_rows, canonical_sha256(body))
