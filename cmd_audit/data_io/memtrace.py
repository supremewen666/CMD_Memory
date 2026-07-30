"""Typed loader for the family-keyed MemTrace observational dataset."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Mapping

from cmd_audit.core.models import ProbeCase
from cmd_audit.eval.evolution_gates import FamilyNetGains
from cmd_audit.repair.memtrace_families import (
    FamilySplit,
    MemtraceFamily,
    MemtraceMember,
    build_families,
    family_stream,
    split_families,
)

from .probe_cases import load_probe_cases


DEFAULT_MEMTRACE_CASES = Path("data/probe_cases/memtrace_kp_cases.json")


@dataclass(frozen=True)
class MemtraceDataset:
    """One validated MemTrace dataset with its immutable family structure."""

    source_path: Path
    cases: tuple[ProbeCase, ...]
    families: tuple[MemtraceFamily, ...]
    split: FamilySplit

    def stream(
        self,
        *,
        seed: int,
        represented_only: bool = False,
    ) -> tuple[MemtraceMember, ...]:
        """Return a deterministic, family-contiguous member stream.

        ``represented_only=True`` excludes the user-bucketed unseen safety
        families.  It never changes member ordering within a family.
        """

        families = self.split.represented if represented_only else self.families
        return family_stream(families, seed=seed)


def load_memtrace_dataset(
    path: str | Path = DEFAULT_MEMTRACE_CASES,
) -> MemtraceDataset:
    """Load MemTrace cases and fail closed while building their family split."""

    source_path = Path(path)
    cases = tuple(load_probe_cases(source_path))
    families = build_families(cases)
    return MemtraceDataset(
        source_path=source_path,
        cases=cases,
        families=families,
        split=split_families(families),
    )


def build_memtrace_family_net_gains(
    dataset: MemtraceDataset,
    net_gain_by_case_id: Mapping[str, float],
) -> tuple[FamilyNetGains, ...]:
    """Bind one finite net-gain measurement to every MemTrace member.

    Missing or extra case ids are rejected.  A gate input is an audit artifact,
    not a best-effort join: silently losing measurements would change the
    family resampling population and make ``excluded_families`` misleading.
    """

    expected = {case.case_id for case in dataset.cases}
    observed = {str(case_id) for case_id in net_gain_by_case_id}
    missing = sorted(expected - observed)
    extra = sorted(observed - expected)
    if missing or extra:
        raise ValueError(
            "MemTrace net-gain case ids do not match the dataset: "
            f"missing={missing[:3]}, extra={extra[:3]}"
        )
    normalized: dict[str, float] = {}
    for case_id, raw_value in net_gain_by_case_id.items():
        value = float(raw_value)
        if not math.isfinite(value):
            raise ValueError(f"{case_id}: net_gain must be finite")
        normalized[str(case_id)] = value
    return tuple(
        FamilyNetGains(
            family_id=family.family_id,
            keying=family.keying,
            baseline_gains=tuple(
                normalized[member.case_id]
                for member in family.members
                if member.c_index == 0
            ),
            later_gains=tuple(
                normalized[member.case_id]
                for member in family.members
                if member.c_index > 0
            ),
        )
        for family in dataset.families
    )


def load_memtrace_family_net_gains(
    path: str | Path,
    *,
    dataset: MemtraceDataset,
) -> tuple[FamilyNetGains, ...]:
    """Load a strict JSON net-gain artifact and bind it to MemTrace families.

    Accepted JSON shapes are either ``{"case_id": net_gain, ...}`` or a list
    of ``{"case_id": ..., "net_gain": ...}`` rows.
    """

    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        values = {str(case_id): float(value) for case_id, value in payload.items()}
    elif isinstance(payload, list):
        values: dict[str, float] = {}
        for index, row in enumerate(payload):
            if not isinstance(row, dict):
                raise ValueError(f"{source}: row {index} must be an object")
            try:
                case_id = str(row["case_id"])
                value = float(row["net_gain"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"{source}: row {index} requires case_id and numeric net_gain"
                ) from exc
            if case_id in values:
                raise ValueError(f"{source}: duplicate case_id {case_id!r}")
            values[case_id] = value
    else:
        raise ValueError(
            f"{source}: expected an object mapping or a list of result rows"
        )
    return build_memtrace_family_net_gains(dataset, values)
