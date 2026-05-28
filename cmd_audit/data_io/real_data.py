"""Unified loaders for the four real-data sources (issue 0016)."""

from __future__ import annotations

from pathlib import Path

from cmd_audit.core.models import ProbeCase

from .probe_cases import load_probe_cases_v1


_REAL_DATA_DIR = Path("data/probe_cases")

_REAL_DATA_FILES: tuple[tuple[str, str], ...] = (
    ("longmemeval", "real_longmemeval_cases.json"),
    ("memoryarena", "real_memoryarena_cases.json"),
    ("toolbench", "real_toolbench_cases.json"),
    ("null_label", "v1_null_label_cases.json"),
)


def load_all_real_cases(
    base_dir: str | Path | None = None,
) -> list[ProbeCase]:
    """Load all 601 real-data probe cases (596 labeled + 5 null-label).

    Returns a single flat list from all four source files.
    """
    root = Path(base_dir) if base_dir else _REAL_DATA_DIR
    all_cases: list[ProbeCase] = []
    for _source, filename in _REAL_DATA_FILES:
        all_cases.extend(load_probe_cases_v1(root / filename))
    return all_cases


def load_real_cases_by_source(
    base_dir: str | Path | None = None,
) -> dict[str, list[ProbeCase]]:
    """Load all 601 real-data probe cases keyed by source name.

    Returns {"longmemeval": [...], "memoryarena": [...], "toolbench": [...], "null_label": [...]}.
    """
    root = Path(base_dir) if base_dir else _REAL_DATA_DIR
    return {
        source: load_probe_cases_v1(root / filename)
        for source, filename in _REAL_DATA_FILES
    }
