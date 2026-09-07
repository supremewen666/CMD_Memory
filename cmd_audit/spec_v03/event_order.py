"""Outcome-independent event order, maturity, and CAS interleaving compiler."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import random
from typing import Sequence

from .contracts import canonical_sha256
from .repair_stream import RepairCase


@dataclass(frozen=True)
class OrderedCase:
    case_id: str
    event_index: int
    regime: str
    receipt_matures_at: int
    cas_interleaving: str


@dataclass(frozen=True)
class EventOrderManifest:
    seed: int
    schedule: str
    rows: tuple[OrderedCase, ...]
    content_sha256: str

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


def compile_event_order(cases: Sequence[RepairCase], *, seed: int, schedule: str, maturity_delay: int = 2) -> EventOrderManifest:
    if schedule not in {"stationary", "abrupt_process_state_poison", "recurring_a_b_a"}:
        raise ValueError("unsupported event-order schedule")
    if maturity_delay < 1:
        raise ValueError("maturity delay must be positive")
    shuffled = list(cases)
    random.Random(f"spec-v03-order:{seed}:{schedule}").shuffle(shuffled)
    if schedule == "abrupt_process_state_poison":
        order = {"process_fault": 0, "state_drift": 1, "poison": 2, "clean": 3}
        shuffled.sort(key=lambda case: order.get(case.intervention.incident_type, 4))
    elif schedule == "recurring_a_b_a":
        process = [case for case in shuffled if case.intervention.incident_type == "process_fault"]
        others = [case for case in shuffled if case.intervention.incident_type in {"state_drift", "poison"}]
        clean = [case for case in shuffled if case.intervention.incident_type == "clean"]
        if not process or not others:
            raise ValueError("recurring A-B-A requires non-empty process A and state/poison B groups")
        midpoint = len(process) // 2
        shuffled = process[:midpoint] + others + process[midpoint:]
        clean_rng = random.Random(f"spec-v03-recurring-clean:{seed}")
        for case in clean:
            shuffled.insert(clean_rng.randrange(len(shuffled) + 1), case)
    if sorted(case.case_id for case in shuffled) != sorted(case.case_id for case in cases):
        raise AssertionError("event-order schedule must be a strict input-case permutation")
    cas = ["conflicting", "benign"]
    while len(cas) < len(shuffled):
        cas.append("conflicting" if random.Random(f"cas:{seed}:{len(cas)}").randrange(2) else "benign")
    random.Random(f"cas-shuffle:{seed}").shuffle(cas)
    rows = tuple(OrderedCase(case.case_id, index, schedule, index + maturity_delay, cas[index]) for index, case in enumerate(shuffled))
    body = {"seed": seed, "schedule": schedule, "rows": [asdict(row) for row in rows]}
    return EventOrderManifest(seed, schedule, rows, canonical_sha256(body))
