"""Small numeric helpers shared by observational experiment modules."""
from __future__ import annotations

import math
from typing import Iterable


def finite_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def is_finite_number(value: object) -> bool:
    return finite_float(value) is not None


def mean_finite(values: Iterable[object]) -> float | None:
    materialized = [
        numeric
        for value in values
        if (numeric := finite_float(value)) is not None
    ]
    return (
        sum(materialized) / len(materialized)
        if materialized
        else None
    )
