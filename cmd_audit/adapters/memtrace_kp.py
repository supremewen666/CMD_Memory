"""Knowledge-point probe adapter implementing the MemTrace-B protocol.

Provenance disclaimer
=====================
This module is a **protocol reimplementation on shared source data**, not a
replication of a released artifact.

- **MemTrace-B** (arXiv 2606.17328, Long/Chen/Zeng/Tang, Michigan State) has
  **no public data release**: no repository, no HuggingFace dataset. Nothing
  here is derived from their file, and CMD must never claim to hold their data.
- MemTrace-B is itself derived from **HaluMem-Medium**, which *is* public. This
  adapter re-derives their measurement protocol from the same upstream source:
  ``data/stage4_1_events2memories.jsonl`` in ``github.com/MemTensor/HaluMem``
  (blob sha ``62566339d4b90678a63b0e53ac71a8aca1f936b0``, 20 users).
- Do **not** confuse arXiv 2606.17328 with the unrelated, identically-named
  arXiv 2605.28732 (zjunlp), which is an execution-graph attribution paper.

Why CMD carries this arm: MemTrace-B's headline is *"the dominant bottleneck is
evidence use, not retrieval — when systems fail, the evidence was retrievable
10x more often than it was missing"*, an independent restatement of CMD's C1
implicit-failure hypothesis, and its "failure attribution: reach vs. use" axis
is CMD's territory. This is an **additive** external-validity arm; it does not
replace or modify any main-line CMD dataset.

Protocol
========
The unit of measurement is a **knowledge point** (one typed fact), not one
question. Each knowledge point is expanded across three controlled dimensions:

1. **Memory age** — sessions elapsed since the fact first appeared, derived from
   ``memory_point.event_source`` -> ``event_list[i]``. Eight chronological
   checkpoints per user by default; a knowledge point is probed only at
   checkpoints at or after the event that wrote it (age 0 = same session).
2. **Question type** — ``current`` (state now), ``historical`` (state earlier),
   ``trajectory`` (how it changed). Only knowledge points with
   ``is_update == "True"`` *and* a non-empty ``original_memories`` can support
   ``historical`` / ``trajectory``; static facts get ``current`` only. No
   trajectory is fabricated for a fact that never changed.
3. **Evidence condition** — ``present`` (normal), ``missing`` (boundary probe:
   the fact was never mentioned, correct behaviour is to ABSTAIN),
   ``contradicted`` (conflict probe: the query asserts a false premise that
   contradicts memory, correct behaviour is to CORRECT the premise).

Knowledge-point typing follows the paper's Table 2 split: ``static`` /
``dynamic`` / ``preference`` are substantive; ``conflict_distractor`` and
``boundary_distractor`` are distractor classes, carried here as the probe class
implied by the ``contradicted`` / ``missing`` evidence conditions.

Label mapping into CMD's action space
=====================================
Labels come from :mod:`cmd_audit.core.labels` only — nothing is invented. Rules
are applied in this precedence order:

| # | Evidence condition | Question type | KP type          | CMD label           |
|---|--------------------|---------------|------------------|---------------------|
| 1 | ``contradicted``   | any           | any              | ``item_conflict``   |
| 2 | ``missing``        | any           | any              | ``safety_error``    |
| 3 | ``present``        | ``trajectory``| any              | ``granularity_error``|
| 4 | ``present``        | ``historical``| any              | ``retrieval_error`` |
| 5 | ``present``        | ``current``   | ``dynamic``      | ``item_stale``      |
| 6 | ``present``        | ``current``   | static / pref.   | ``None`` (control)  |

Rationale per row: a false premise contradicting stored memory is an item-level
coexisting contradiction (1); a probe whose correct answer is an abstention
fails by answering, which is the abstention/safety layer's job (2); a change
history cannot be read off a point state, so the granularity is wrong (3); an
earlier state that recall never surfaces is a retrieval miss (4); a dynamic
fact whose superseded state persists in recall is stale (5). Row 6 is the
paper's easy control condition — no fault is injected, so it carries no label
and CMD's Fill branch absorbs it (``perturbation_label=None`` is legal).

Structural legality of the mapped action is guaranteed per case, so the
counterfactual operators can actually fire:

- ``item_stale`` — prior and current states are both recalled, with ISO-8601
  ``store`` timestamps at least ``MIN_STALE_SEPARATION_DAYS`` apart so item-gate
  typing resolves to *stale* rather than *conflict*.
- ``item_conflict`` — prior and current states are both recalled but placed
  ``CONFLICT_SEPARATION_DAYS`` apart, inside the item gate's 7-day tolerance, so
  typing resolves to *conflict*; the two still carry distinct provenance scores
  so the de-confliction operator is not a no-op.
- ``retrieval_error`` — the prior-state item is in the pool but absent from
  recall and source-event-disjoint from it, the signature of a pure miss.
- ``granularity_error`` — recall carries a coarse summary item spanning two
  source events, which de-summarizes back to the raw events.
- ``safety_error`` — the recalled scope item is flagged ``passed_safety_filter``,
  which is what gates the safety action as legal.

Documented divergences from the paper
=====================================
- Question surface forms are **template-generated** (deterministic, stdlib
  only). The paper's questions were LLM-generated, so wording is coarser here.
  ``current`` probes on static / preference facts use a **cloze** frame so the
  question cannot leak its own answer.
- Boundary probes are grounded, not fabricated: each asks about a preference
  category genuinely absent from that user's ``profile.preferences``, so "never
  mentioned" is verifiable from the source. This caps boundary probes at the
  number of absent categories per user rather than the paper's flat count.
- Table 2's "100 each" distractor counts are corpus-level totals in the paper;
  here distractor volume follows from the source structure and the sampling
  parameters instead of being pinned to 100.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from cmd_audit.core.models import ProbeCase

# ── Protocol constants ────────────────────────────────────────────────────────

QUESTION_TYPES = ("current", "historical", "trajectory")
EVIDENCE_CONDITIONS = ("present", "missing", "contradicted")
SUBSTANTIVE_KP_TYPES = ("static", "dynamic", "preference")
DISTRACTOR_PROBE_CLASSES = ("conflict_distractor", "boundary_distractor")

DEFAULT_CHECKPOINTS = 8
DEFAULT_KPS_PER_USER = 6

#: Minimum ``store`` gap for a stale pair, above the item gate's 7-day
#: tolerance so timestamp typing resolves to *stale*.
MIN_STALE_SEPARATION_DAYS = 30
#: ``store`` gap for a conflict pair: inside the 7-day tolerance (so typing
#: resolves to *conflict*) but non-zero (so provenance scores stay distinct).
CONFLICT_SEPARATION_DAYS = 2

#: Preference categories observed across the full 20-user HaluMem source. A
#: category absent from one user's profile is a verifiably never-mentioned fact
#: and therefore a sound boundary probe.
PREFERENCE_CATEGORY_UNIVERSE = (
    "Beverage Preference",
    "Clothing Preference",
    "Food Preference",
    "Game Preference",
    "Movie Preference",
    "Music Preference",
    "Pet Preference",
    "Reading Preference",
    "Sports Preference",
    "Travel Preference",
)

_HALUMEM_TIMESTAMP_FORMAT = "%b %d, %Y, %H:%M:%S"
_HALUMEM_EVENT_DATE_FORMAT = "%Y-%m-%d"

_MAX_TEXT = 500
_MAX_CONTEXT = 900


class MemTraceKPError(ValueError):
    """Raised when a HaluMem record cannot be expanded into probe cases."""


# ── Knowledge point ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class KnowledgePoint:
    """One typed fact from a HaluMem user, plus the structure a probe needs."""

    user_uuid: str
    user_name: str
    position: int
    text: str
    memory_type: str
    is_update: bool
    prior_text: str
    first_event_index: int
    timestamp: datetime
    kp_type: str

    @property
    def supports_history(self) -> bool:
        """True when the fact actually changed, so history/trajectory exist."""
        return self.is_update and bool(self.prior_text)


@dataclass(frozen=True)
class ProbeSpec:
    """One point in the (memory age x question type x evidence condition) grid."""

    knowledge_point: KnowledgePoint | None
    checkpoint_index: int
    checkpoint_event_index: int
    age_sessions: int
    question_type: str
    evidence_condition: str
    probe_class: str
    boundary_category: str = ""


# ── Public trio: load / convert / write ───────────────────────────────────────


def load_memtrace_kp_probe_cases(
    path: str | Path,
    *,
    users: int = 0,
    checkpoints: int = DEFAULT_CHECKPOINTS,
    kps_per_user: int = DEFAULT_KPS_PER_USER,
    limit: int = 0,
) -> list[ProbeCase]:
    """Load HaluMem users from JSONL and expand them into CMD probe cases.

    ``users=0`` and ``limit=0`` are the production defaults and keep every user
    and every generated case. Positive values are for smoke tests.
    """
    cases: list[ProbeCase] = []
    for record in iter_halumem_records(path, users=users):
        cases.extend(
            memtrace_kp_record_to_probe_cases(
                record,
                checkpoints=checkpoints,
                kps_per_user=kps_per_user,
            )
        )
        if limit and len(cases) >= limit:
            return cases[:limit]
    return cases


def memtrace_kp_record_to_probe_cases(
    record: dict[str, Any],
    *,
    checkpoints: int = DEFAULT_CHECKPOINTS,
    kps_per_user: int = DEFAULT_KPS_PER_USER,
) -> list[ProbeCase]:
    """Convert one HaluMem user record into MemTrace-B protocol probe cases."""
    events = _events(record)
    checkpoint_indices = checkpoint_event_indices(len(events), checkpoints)
    if not checkpoint_indices:
        return []

    knowledge_points = _sample_knowledge_points(record, events, kps_per_user)
    specs: list[ProbeSpec] = []
    for kp in knowledge_points:
        specs.extend(expand_memtrace_kp_probes(kp, checkpoint_indices))
    specs.extend(
        _expand_boundary_probes(record, events, checkpoint_indices)
    )

    return [_build_probe_case(record, events, spec) for spec in specs]


def write_memtrace_kp_probe_cases(
    input_path: str | Path,
    output_path: str | Path,
    *,
    users: int = 0,
    checkpoints: int = DEFAULT_CHECKPOINTS,
    kps_per_user: int = DEFAULT_KPS_PER_USER,
    limit: int = 0,
) -> Path:
    """Convert HaluMem JSONL to CMD ProbeCase JSON, full output by default."""
    cases = load_memtrace_kp_probe_cases(
        input_path,
        users=users,
        checkpoints=checkpoints,
        kps_per_user=kps_per_user,
        limit=limit,
    )
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = [_case_to_mapping(case) for case in cases]
    out.write_text(
        json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return out


# ── Probe expander over the three dimensions ─────────────────────────────────


def checkpoint_event_indices(n_events: int, n_checkpoints: int) -> tuple[int, ...]:
    """Return the event index of each chronological checkpoint.

    Checkpoints partition the user's event timeline into equal segments and sit
    at the last event of each segment, so the sequence is strictly increasing
    and the final checkpoint is the final event.
    """
    if n_events <= 0 or n_checkpoints <= 0:
        return ()
    count = min(n_checkpoints, n_events)
    return tuple(((c + 1) * n_events) // count - 1 for c in range(count))


def expand_memtrace_kp_probes(
    kp: KnowledgePoint,
    checkpoint_indices: Iterable[int],
) -> list[ProbeSpec]:
    """Expand one knowledge point over age x question type x evidence condition.

    Gating rules: a knowledge point is probed only at checkpoints at or after
    the event that wrote it; ``historical`` / ``trajectory`` require an actual
    recorded change; ``contradicted`` requires a superseded state to assert as
    the false premise, so it also requires an actual change.
    """
    question_types = ("current", "historical", "trajectory") if kp.supports_history else ("current",)

    specs: list[ProbeSpec] = []
    for checkpoint_index, event_index in enumerate(checkpoint_indices):
        if event_index < kp.first_event_index:
            continue
        age = event_index - kp.first_event_index
        for question_type in question_types:
            specs.append(
                ProbeSpec(
                    knowledge_point=kp,
                    checkpoint_index=checkpoint_index,
                    checkpoint_event_index=event_index,
                    age_sessions=age,
                    question_type=question_type,
                    evidence_condition="present",
                    probe_class=kp.kp_type,
                )
            )
        if kp.supports_history:
            specs.append(
                ProbeSpec(
                    knowledge_point=kp,
                    checkpoint_index=checkpoint_index,
                    checkpoint_event_index=event_index,
                    age_sessions=age,
                    question_type="current",
                    evidence_condition="contradicted",
                    probe_class="conflict_distractor",
                )
            )
    return specs


def _expand_boundary_probes(
    record: dict[str, Any],
    events: list[dict[str, Any]],
    checkpoint_indices: tuple[int, ...],
) -> list[ProbeSpec]:
    """Boundary probes over preference categories absent from this user."""
    present = set(_preferences(record))
    absent = tuple(
        category
        for category in PREFERENCE_CATEGORY_UNIVERSE
        if category not in present
    )
    specs: list[ProbeSpec] = []
    for category in absent:
        for checkpoint_index, event_index in enumerate(checkpoint_indices):
            specs.append(
                ProbeSpec(
                    knowledge_point=None,
                    checkpoint_index=checkpoint_index,
                    checkpoint_event_index=event_index,
                    age_sessions=event_index,
                    question_type="current",
                    evidence_condition="missing",
                    probe_class="boundary_distractor",
                    boundary_category=category,
                )
            )
    return specs


# ── Label mapping ─────────────────────────────────────────────────────────────


def memtrace_kp_label(
    question_type: str,
    evidence_condition: str,
    kp_type: str,
) -> str | None:
    """Map a protocol grid point to a CMD label (see the module docstring table).

    Returns ``None`` for the control condition (a present, current probe on a
    fact that never changed): no fault is injected, so no label is asserted.
    """
    if evidence_condition == "contradicted":
        return "item_conflict"
    if evidence_condition == "missing":
        return "safety_error"
    if question_type == "trajectory":
        return "granularity_error"
    if question_type == "historical":
        return "retrieval_error"
    if kp_type == "dynamic":
        return "item_stale"
    return None


def memtrace_kp_dimensions(
    record: dict[str, Any],
    *,
    checkpoints: int = DEFAULT_CHECKPOINTS,
    kps_per_user: int = DEFAULT_KPS_PER_USER,
) -> list[dict[str, str]]:
    """Per-case dimension rows, so age / type / condition can group results.

    ``ProbeCase`` is a fixed dataclass with no extension field, so the grid
    coordinates travel two ways: encoded in ``case_id`` and emitted here as
    flat rows the CLI writes to a sidecar CSV.
    """
    events = _events(record)
    checkpoint_indices = checkpoint_event_indices(len(events), checkpoints)
    if not checkpoint_indices:
        return []

    specs: list[ProbeSpec] = []
    for kp in _sample_knowledge_points(record, events, kps_per_user):
        specs.extend(expand_memtrace_kp_probes(kp, checkpoint_indices))
    specs.extend(_expand_boundary_probes(record, events, checkpoint_indices))

    rows: list[dict[str, str]] = []
    for spec in specs:
        kp_type = spec.knowledge_point.kp_type if spec.knowledge_point else "boundary"
        rows.append(
            {
                "case_id": _case_id(record, spec),
                "user_uuid": _user_uuid(record),
                "kp_position": (
                    str(spec.knowledge_point.position)
                    if spec.knowledge_point
                    else ""
                ),
                "kp_type": kp_type,
                "probe_class": spec.probe_class,
                "question_type": spec.question_type,
                "evidence_condition": spec.evidence_condition,
                "checkpoint_index": str(spec.checkpoint_index),
                "checkpoint_event_index": str(spec.checkpoint_event_index),
                "age_sessions": str(spec.age_sessions),
                "perturbation_label": (
                    memtrace_kp_label(
                        spec.question_type, spec.evidence_condition, kp_type
                    )
                    or ""
                ),
            }
        )
    return rows


# ── HaluMem parsing ───────────────────────────────────────────────────────────


def parse_is_update(raw: Any) -> bool:
    """Parse HaluMem's ``is_update``, which is the STRING ``"True"``/``"False"``.

    A plain ``bool(raw)`` is wrong here: ``bool("False")`` is ``True``. Real
    booleans are still accepted for robustness; anything else is not an update.
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().casefold() == "true"
    return False


def iter_halumem_records(
    path: str | Path,
    *,
    users: int = 0,
) -> Iterable[dict[str, Any]]:
    """Yield HaluMem user records from a JSONL (or JSON list) file."""
    text = Path(path).read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        rows = json.loads(stripped)
        records = [row for row in rows if isinstance(row, dict)]
    else:
        records = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                records.append(row)
    if users:
        records = records[:users]
    return records


def _events(record: dict[str, Any]) -> list[dict[str, Any]]:
    events = record.get("event_list")
    if not isinstance(events, list) or not events:
        raise MemTraceKPError("HaluMem record is missing a non-empty event_list")
    return [event for event in events if isinstance(event, dict)]


def _memory_points(record: dict[str, Any]) -> list[dict[str, Any]]:
    points = record.get("memory_points_all")
    if not isinstance(points, list) or not points:
        raise MemTraceKPError(
            "HaluMem record is missing a non-empty memory_points_all"
        )
    return [point for point in points if isinstance(point, dict)]


def _preferences(record: dict[str, Any]) -> dict[str, Any]:
    profile = record.get("profile")
    if not isinstance(profile, dict):
        return {}
    preferences = profile.get("preferences")
    return preferences if isinstance(preferences, dict) else {}


def _user_uuid(record: dict[str, Any]) -> str:
    raw = str(record.get("uuid", "")).strip()
    return raw or "unknown"


def _user_uuid_short(record: dict[str, Any]) -> str:
    return _user_uuid(record).replace("-", "")[:8]


def _user_name(record: dict[str, Any], events: list[dict[str, Any]]) -> str:
    for source in (record.get("profile"), *(event for event in events)):
        if not isinstance(source, dict):
            continue
        fixed = source.get("fixed") or source.get("initial_fixed")
        if isinstance(fixed, dict):
            basic = fixed.get("basic_info")
            if isinstance(basic, dict):
                name = str(basic.get("name", "")).strip()
                if name:
                    return name
    return "the user"


def _parse_memory_timestamp(raw: Any, fallback: datetime) -> datetime:
    if isinstance(raw, str):
        try:
            return datetime.strptime(
                raw.strip(), _HALUMEM_TIMESTAMP_FORMAT
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return fallback


def _parse_event_date(raw: Any) -> datetime:
    if isinstance(raw, str):
        try:
            return datetime.strptime(
                raw.strip(), _HALUMEM_EVENT_DATE_FORMAT
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _kp_type(
    point: dict[str, Any],
    is_update: bool,
    preference_categories: Iterable[str],
) -> str:
    """Type a knowledge point: dynamic, then preference, then static.

    The precedence is deliberate and follows the brief: ``is_update`` decides
    ``dynamic`` first, so a changed preference is typed dynamic (its change
    history is what makes it probeable), and ``preference`` picks up the
    unchanged preference facts.
    """
    if is_update:
        return "dynamic"
    text = str(point.get("memory_content", "")).casefold()
    for category in preference_categories:
        keyword = category.replace(" Preference", "").casefold()
        if keyword and keyword in text:
            return "preference"
    return "static"


def _knowledge_points(
    record: dict[str, Any],
    events: list[dict[str, Any]],
) -> list[KnowledgePoint]:
    user_uuid = _user_uuid(record)
    user_name = _user_name(record, events)
    categories = tuple(PREFERENCE_CATEGORY_UNIVERSE)
    n_events = len(events)

    points: list[KnowledgePoint] = []
    for position, point in enumerate(_memory_points(record)):
        text = _shorten(point.get("memory_content", ""), _MAX_TEXT)
        if not text:
            continue
        is_update = parse_is_update(point.get("is_update"))
        originals = point.get("original_memories")
        prior_text = ""
        if isinstance(originals, list) and originals:
            prior_text = _shorten(originals[0], _MAX_TEXT)
        try:
            event_index = int(point.get("event_source", 0))
        except (TypeError, ValueError):
            event_index = 0
        event_index = max(0, min(event_index, n_events - 1))
        fallback = _parse_event_date(events[event_index].get("event_time"))
        points.append(
            KnowledgePoint(
                user_uuid=user_uuid,
                user_name=user_name,
                position=position,
                text=text,
                memory_type=str(point.get("memory_type", "Memory")).strip()
                or "Memory",
                is_update=is_update,
                prior_text=prior_text,
                first_event_index=event_index,
                timestamp=_parse_memory_timestamp(point.get("timestamp"), fallback),
                kp_type=_kp_type(point, is_update, categories),
            )
        )
    return points


def _sample_knowledge_points(
    record: dict[str, Any],
    events: list[dict[str, Any]],
    kps_per_user: int,
) -> list[KnowledgePoint]:
    """Deterministically sample knowledge points by even stride, no randomness.

    Sampling is balanced across the substantive types so the ``dynamic``-only
    question types are not starved: each type gets an even stride over its own
    members, then the union is re-sorted by source position.
    """
    points = _knowledge_points(record, events)
    if not points or kps_per_user <= 0:
        return points if kps_per_user <= 0 else []

    per_type = max(1, kps_per_user // len(SUBSTANTIVE_KP_TYPES))
    selected: list[KnowledgePoint] = []
    for kp_type in SUBSTANTIVE_KP_TYPES:
        members = [point for point in points if point.kp_type == kp_type]
        if kp_type == "dynamic":
            members = [point for point in members if point.supports_history]
        selected.extend(_stride_sample(members, per_type))

    if len(selected) < kps_per_user:
        chosen = {point.position for point in selected}
        for point in points:
            if len(selected) >= kps_per_user:
                break
            if point.position not in chosen:
                selected.append(point)
                chosen.add(point.position)

    selected.sort(key=lambda point: point.position)
    return selected[:kps_per_user]


def _stride_sample(items: list[KnowledgePoint], count: int) -> list[KnowledgePoint]:
    if not items or count <= 0:
        return []
    if count >= len(items):
        return list(items)
    stride = len(items) / count
    return [items[min(len(items) - 1, math.floor(i * stride))] for i in range(count)]


# ── Case construction ────────────────────────────────────────────────────────


def _build_probe_case(
    record: dict[str, Any],
    events: list[dict[str, Any]],
    spec: ProbeSpec,
) -> ProbeCase:
    if spec.evidence_condition == "missing":
        mapping = _boundary_case_mapping(record, events, spec)
    else:
        mapping = _knowledge_point_case_mapping(record, events, spec)
    return ProbeCase.from_mapping_v1(mapping)


def _knowledge_point_case_mapping(
    record: dict[str, Any],
    events: list[dict[str, Any]],
    spec: ProbeSpec,
) -> dict[str, Any]:
    kp = spec.knowledge_point
    if kp is None:  # pragma: no cover - guarded by caller
        raise MemTraceKPError("a substantive probe requires a knowledge point")

    label = memtrace_kp_label(spec.question_type, spec.evidence_condition, kp.kp_type)
    checkpoint_date = _event_date_text(events, spec.checkpoint_event_index)

    kp_event_index = kp.first_event_index
    prior_event_index = max(0, kp_event_index - 1)
    kp_event_id = _event_id(kp_event_index)
    prior_event_id = _event_id(prior_event_index)

    raw_events = _raw_events(
        events,
        (prior_event_index, kp_event_index, spec.checkpoint_event_index),
    )

    new_store, old_store = _store_pair(kp, spec.evidence_condition)
    siblings = _sibling_items(record, events, kp, kp_event_id)

    extracted: list[dict[str, Any]] = [
        {
            "memory_id": "m_kp",
            "text": kp.text,
            "source_event_ids": [kp_event_id],
            "store": new_store,
        }
    ]
    if kp.supports_history:
        extracted.append(
            {
                "memory_id": "m_prior",
                "text": kp.prior_text,
                "source_event_ids": [prior_event_id],
                "store": old_store,
            }
        )
    if spec.question_type == "trajectory":
        extracted.append(
            {
                "memory_id": "m_summary",
                "text": (
                    f"{kp.user_name} updated a {kp.memory_type} record during "
                    f"{_event_name(events, kp_event_index)}."
                ),
                "source_event_ids": [prior_event_id, kp_event_id],
                "store": new_store,
            }
        )
    extracted.extend(siblings)

    recalled = _recalled_memory_ids(spec, kp, siblings)
    query = _query_text(kp, spec, checkpoint_date)
    gold_answer, gold_evidence = _gold_for_spec(kp, spec, kp_event_id, prior_event_id)

    return {
        "case_id": _case_id(record, spec),
        "query": query,
        "raw_events": raw_events,
        "extracted_memory": extracted,
        "gold_evidence": gold_evidence,
        "gold_answer": gold_answer,
        "baseline_outputs": _baseline_outputs(kp, spec, recalled, extracted),
        "perturbation_label": label,
        "scoring": {
            "answer_metric": "casefold_exact_match",
            "evidence_metric": "gold_evidence_recall",
        },
        "default_store": "episodic",
    }


def _boundary_case_mapping(
    record: dict[str, Any],
    events: list[dict[str, Any]],
    spec: ProbeSpec,
) -> dict[str, Any]:
    """A probe about a fact never mentioned; the correct answer is abstention."""
    category = spec.boundary_category
    user_name = _user_name(record, events)
    checkpoint_date = _event_date_text(events, spec.checkpoint_event_index)
    checkpoint_event_id = _event_id(spec.checkpoint_event_index)
    recorded = sorted(_preferences(record))

    scope_text = _shorten(
        f"Recorded preference categories for {user_name}: "
        f"{', '.join(recorded) if recorded else 'none'}. "
        f"No {category} record exists.",
        _MAX_CONTEXT,
    )
    absence_phrase = f"No {category} record exists"
    gold_answer = (
        f"No stored memory records {user_name}'s {category}; abstain instead of answering."
    )

    return {
        "case_id": _case_id(record, spec),
        "query": (
            f"As of {checkpoint_date}, what does memory record about "
            f"{user_name}'s {category}? If nothing is recorded, say so instead "
            f"of answering."
        ),
        "raw_events": _raw_events(events, (0, spec.checkpoint_event_index)),
        "extracted_memory": [
            {
                "memory_id": "m_scope",
                "text": scope_text,
                "source_event_ids": [checkpoint_event_id],
                "store": _iso(_parse_event_date(
                    events[spec.checkpoint_event_index].get("event_time")
                )),
                # Flagged safe by the structural gate, which is what makes the
                # mapped ``safety_error`` action legal on this case.
                "passed_safety_filter": True,
            }
        ],
        "gold_evidence": [
            {
                "evidence_id": "ev_absence",
                "text": scope_text,
                "source_memory_id": "m_scope",
                "source_event_id": checkpoint_event_id,
                "required_phrases": [absence_phrase],
            }
        ],
        "gold_answer": gold_answer,
        "baseline_outputs": [
            {
                "baseline_name": "vector_memory",
                # The failure mode this probe targets: answering confidently
                # rather than abstaining.
                "answer": f"{user_name}'s {category} is recorded in memory.",
                "retrieved_memory_ids": ["m_scope"],
                "answer_score": 0.0,
                "evidence_score": 0.0,
                "injected_context": _shorten(
                    f"Retrieved memory scope:\n{scope_text}", _MAX_CONTEXT
                ),
            },
            {
                "baseline_name": "fixed_summary",
                "answer": "Unknown",
                "retrieved_memory_ids": [],
                "answer_score": 0.0,
                "evidence_score": 0.0,
                "injected_context": "Summary retained topics but not coverage bounds.",
            },
        ],
        "perturbation_label": memtrace_kp_label(
            spec.question_type, spec.evidence_condition, "boundary"
        ),
        "scoring": {
            "answer_metric": "casefold_exact_match",
            "evidence_metric": "gold_evidence_recall",
        },
        "default_store": "episodic",
    }


def _store_pair(kp: KnowledgePoint, evidence_condition: str) -> tuple[str, str]:
    """Return ``(new_store, old_store)`` ISO-8601 Z timestamps.

    A conflict probe keeps the pair inside the item gate's 7-day tolerance so
    typing resolves to *conflict*; every other probe forces a gap wider than the
    tolerance so typing resolves to *stale*.
    """
    separation = (
        CONFLICT_SEPARATION_DAYS
        if evidence_condition == "contradicted"
        else MIN_STALE_SEPARATION_DAYS
    )
    new_ts = kp.timestamp
    return _iso(new_ts), _iso(new_ts - timedelta(days=separation))


def _sibling_items(
    record: dict[str, Any],
    events: list[dict[str, Any]],
    kp: KnowledgePoint,
    kp_event_id: str,
) -> list[dict[str, Any]]:
    """Up to two same-session sibling facts, giving recall realistic body."""
    store = _iso(kp.timestamp)
    siblings: list[dict[str, Any]] = []
    for point in _knowledge_points(record, events):
        if len(siblings) >= 2:
            break
        if point.position == kp.position:
            continue
        if point.first_event_index != kp.first_event_index:
            continue
        if point.text == kp.text or point.text == kp.prior_text:
            continue
        siblings.append(
            {
                "memory_id": f"m_sib{len(siblings)}",
                "text": point.text,
                "source_event_ids": [kp_event_id],
                "store": store,
            }
        )
    return siblings


def _recalled_memory_ids(
    spec: ProbeSpec,
    kp: KnowledgePoint,
    siblings: list[dict[str, Any]],
) -> list[str]:
    """Which items the memory system surfaced, per the mapped failure mode.

    ``historical`` withholds the prior-state item from recall while leaving it
    in the pool, which is the pure-retrieval-miss signature the
    ``retrieval_error`` operator needs.
    """
    recalled = ["m_kp"]
    if spec.question_type == "trajectory":
        recalled.append("m_summary")
    if kp.supports_history and spec.question_type != "historical":
        recalled.append("m_prior")
    recalled.extend(item["memory_id"] for item in siblings)
    return recalled


def _query_text(kp: KnowledgePoint, spec: ProbeSpec, checkpoint_date: str) -> str:
    """Deterministic template question that cannot leak its own gold answer."""
    if spec.evidence_condition == "contradicted":
        return _shorten(
            f'As of {checkpoint_date}, I have it on record that "{kp.prior_text}" '
            f"is currently true. Confirm that this is still the case.",
            _MAX_CONTEXT,
        )
    if spec.question_type == "historical":
        return _shorten(
            f'As of {checkpoint_date}, memory currently records "{kp.text}". '
            f"What did memory record for this fact before that update?",
            _MAX_CONTEXT,
        )
    if spec.question_type == "trajectory":
        return _shorten(
            f"As of {checkpoint_date}, how has this {kp.memory_type} record for "
            f'{kp.user_name} changed over time: "{_topic_cue(kp.prior_text)}"? '
            f"Describe the earlier state and the current state.",
            _MAX_CONTEXT,
        )
    if kp.supports_history:
        return _shorten(
            f'As of {checkpoint_date}, memory once recorded "{kp.prior_text}". '
            f"What is the current state of this fact?",
            _MAX_CONTEXT,
        )
    # Static / preference fact: a cloze frame keeps the answer out of the query.
    return _shorten(
        f"As of {checkpoint_date}, complete this recorded {kp.memory_type} fact "
        f'about {kp.user_name}: "{_cloze(kp.text)}". '
        f"What does memory record in place of ___?",
        _MAX_CONTEXT,
    )


def _gold_for_spec(
    kp: KnowledgePoint,
    spec: ProbeSpec,
    kp_event_id: str,
    prior_event_id: str,
) -> tuple[str, list[dict[str, Any]]]:
    current_evidence = {
        "evidence_id": "ev_current",
        "text": kp.text,
        "source_memory_id": "m_kp",
        "source_event_id": kp_event_id,
        "required_phrases": _required_phrases(kp.text),
    }
    prior_evidence = {
        "evidence_id": "ev_prior",
        "text": kp.prior_text,
        "source_memory_id": "m_prior",
        "source_event_id": prior_event_id,
        "required_phrases": _required_phrases(kp.prior_text),
    }

    if spec.evidence_condition == "contradicted":
        return (
            f"Correction: that premise is outdated. Memory now records: {kp.text}",
            [current_evidence],
        )
    if spec.question_type == "historical":
        return kp.prior_text, [prior_evidence]
    if spec.question_type == "trajectory":
        return (
            f"Changed from: {kp.prior_text} To: {kp.text}",
            [prior_evidence, current_evidence],
        )
    return kp.text, [current_evidence]


def _baseline_outputs(
    kp: KnowledgePoint,
    spec: ProbeSpec,
    recalled: list[str],
    extracted: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    text_by_id = {item["memory_id"]: item["text"] for item in extracted}
    injected = "\n".join(
        f"- [{memory_id}] {text_by_id[memory_id]}"
        for memory_id in recalled
        if memory_id in text_by_id
    )
    # The comparator answers with the state the mapped failure mode makes it
    # produce: the superseded state when one exists, else the point state.
    faulty_answer = kp.prior_text if kp.supports_history else kp.text
    if spec.question_type == "historical":
        faulty_answer = kp.text
    return [
        {
            "baseline_name": "vector_memory",
            "answer": faulty_answer,
            "retrieved_memory_ids": recalled,
            "answer_score": 0.0,
            "evidence_score": 0.0,
            "injected_context": _shorten(
                f"Retrieved memory:\n{injected}", _MAX_CONTEXT
            ),
        },
        {
            "baseline_name": "fixed_summary",
            "answer": "Unknown",
            "retrieved_memory_ids": [],
            "answer_score": 0.0,
            "evidence_score": 0.0,
            "injected_context": (
                "Summary preserved the topic but not the specific record."
            ),
        },
    ]


def _case_id(record: dict[str, Any], spec: ProbeSpec) -> str:
    """Deterministic, stable case id — no content hashing, no randomness.

    ``memtraceb-<uuid8>-<kp slot>-a<age>c<ckpt>-<qtype>-<evidence cond>``. The
    age slot carries both the memory age in sessions and the checkpoint index,
    so both grouping variables are recoverable from the id alone.
    """
    if spec.knowledge_point is not None:
        kp_slot = f"kp{spec.knowledge_point.position:04d}"
    else:
        kp_slot = f"bd{_slug(spec.boundary_category)}"
    return "-".join(
        (
            "memtraceb",
            _user_uuid_short(record),
            kp_slot,
            f"a{spec.age_sessions}c{spec.checkpoint_index}",
            spec.question_type,
            spec.evidence_condition,
        )
    )


def _raw_events(
    events: list[dict[str, Any]],
    indices: Iterable[int],
) -> list[dict[str, Any]]:
    """Dialogue/event units for this case, so item->event provenance resolves."""
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index in sorted(set(indices)):
        if index in seen or not 0 <= index < len(events):
            continue
        seen.add(index)
        out.append({"event_id": _event_id(index), "text": _event_text(events, index)})
    if not out:
        out.append({"event_id": _event_id(0), "text": _event_text(events, 0)})
    return out


def _event_id(index: int) -> str:
    return f"e_ev{index}"


def _event_name(events: list[dict[str, Any]], index: int) -> str:
    if not 0 <= index < len(events):
        return "an earlier session"
    return _shorten(events[index].get("event_name", "a session"), 120) or "a session"


def _event_date_text(events: list[dict[str, Any]], index: int) -> str:
    if not 0 <= index < len(events):
        return "an unknown date"
    return str(events[index].get("event_time", "an unknown date")).strip()


def _event_text(events: list[dict[str, Any]], index: int) -> str:
    event = events[index]
    dialogue = event.get("dialogue_info")
    summary = ""
    if isinstance(dialogue, dict):
        summary = str(dialogue.get("dialogue_summary", "")).strip()
    parts = [
        f"[{event.get('event_type', 'event')} {event.get('event_time', '')}]".strip(),
        str(event.get("event_name", "")).strip(),
        str(event.get("event_description", "")).strip(),
        summary,
    ]
    return _shorten(" ".join(part for part in parts if part), 700) or f"event {index}"


# ── Text helpers ─────────────────────────────────────────────────────────────


def _cloze(text: str) -> str:
    """Mask the trailing content span so a cloze query cannot leak its answer."""
    words = text.split()
    if len(words) <= 2:
        return "___"
    masked = 1 if len(words) <= 4 else 2
    return " ".join(words[: len(words) - masked]) + " ___"


def _topic_cue(text: str) -> str:
    """A short leading cue used to anchor a trajectory question to its topic."""
    words = text.split()
    return " ".join(words[:6])


def _required_phrases(text: str) -> list[str]:
    phrases = [
        part.strip() for part in re.split(r"[.;:,]", text) if len(part.strip()) >= 3
    ]
    return phrases[:3] or [text.strip()]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text).casefold())


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _shorten(text: str, max_len: int) -> str:
    text = re.sub(r"\s+", " ", str(text)).strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "..."


def _case_to_mapping(case: ProbeCase) -> dict[str, Any]:
    row = asdict(case)
    row.pop("_cmd_baseline_name", None)
    return row


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Convert HaluMem JSONL to MemTrace-B protocol CMD ProbeCase JSON."
        )
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--users", type=int, default=0)
    parser.add_argument("--checkpoints", type=int, default=DEFAULT_CHECKPOINTS)
    parser.add_argument("--kps-per-user", type=int, default=DEFAULT_KPS_PER_USER)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Smoke-test limit; default 0 keeps every generated case.",
    )
    args = parser.parse_args()

    out = write_memtrace_kp_probe_cases(
        args.input,
        args.output,
        users=args.users,
        checkpoints=args.checkpoints,
        kps_per_user=args.kps_per_user,
        limit=args.limit,
    )
    count = len(
        load_memtrace_kp_probe_cases(
            args.input,
            users=args.users,
            checkpoints=args.checkpoints,
            kps_per_user=args.kps_per_user,
            limit=args.limit,
        )
    )
    print(f"Wrote {count} MemTrace-B protocol probe cases to {out}")


if __name__ == "__main__":
    main()
