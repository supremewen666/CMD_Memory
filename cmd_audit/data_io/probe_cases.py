"""Probe-case JSON loaders for CMD-Audit."""

from __future__ import annotations

import json
from pathlib import Path

from cmd_audit.core.models import ProbeCase, ProbeCaseError


def load_probe_cases(path: str | Path) -> list[ProbeCase]:
    """Load a JSON file containing one case object or a list of case objects."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        cases = [raw]
    elif isinstance(raw, list):
        cases = raw
    else:
        raise ProbeCaseError(
            "probe case JSON must contain an object or a list of objects"
        )
    return [ProbeCase.from_mapping(item) for item in cases]


def load_probe_cases_v1(path: str | Path) -> list[ProbeCase]:
    """Load a JSON file of probe cases with V1 label validation."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        cases = [raw]
    elif isinstance(raw, list):
        cases = raw
    else:
        raise ProbeCaseError(
            "probe case JSON must contain an object or a list of objects"
        )
    return [ProbeCase.from_mapping_v1(item) for item in cases]
