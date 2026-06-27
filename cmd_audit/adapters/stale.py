"""Adapter for STALE-style implicit-conflict memory scenarios.

The public STALE artifact shape is intentionally not baked into CMD yet. This
adapter accepts common benchmark JSON layouts and normalizes each scenario/query
into the existing :class:`ProbeCase` contract:

- recall contains both the stale/conflicting item and the current item;
- raw events carry the reachable current state;
- item labels are limited to ``item_stale`` and ``item_conflict``.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import re
from pathlib import Path
from typing import Any

from cmd_audit.core.models import ProbeCase


def load_stale_probe_cases(path: str | Path, *, limit: int = 0) -> list[ProbeCase]:
    """Load STALE scenarios from JSON and convert them to CMD probe cases.

    ``limit=0`` is the production default and keeps the full dataset. Positive
    limits are only for smoke tests and local debugging.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = _scenario_rows(payload)
    cases: list[ProbeCase] = []
    for scenario_index, row in enumerate(rows):
        cases.extend(stale_record_to_probe_cases(row, scenario_index=scenario_index))
        if limit and len(cases) >= limit:
            return cases[:limit]
    return cases


def write_stale_probe_cases(
    input_path: str | Path,
    output_path: str | Path,
    *,
    limit: int = 0,
) -> Path:
    """Convert STALE JSON to CMD ProbeCase JSON, preserving full data by default."""
    cases = load_stale_probe_cases(input_path, limit=limit)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = [_case_to_mapping(case) for case in cases]
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def stale_record_to_probe_cases(
    record: dict[str, Any],
    *,
    scenario_index: int = 0,
) -> list[ProbeCase]:
    """Convert one STALE scenario record into one or more ``ProbeCase`` objects."""
    queries = _query_records(record)
    return [
        ProbeCase.from_mapping_v1(
            _build_case_mapping(record, query_record, scenario_index, query_index)
        )
        for query_index, query_record in enumerate(queries)
    ]


def _build_case_mapping(
    record: dict[str, Any],
    query_record: dict[str, Any],
    scenario_index: int,
    query_index: int,
) -> dict[str, Any]:
    scenario_id = _first_text(
        record,
        "scenario_id",
        "id",
        "uid",
        default=f"stale-{scenario_index:04d}",
    )
    query = _first_text(
        query_record,
        "query",
        "question",
        "prompt",
        default=_first_text(record, "query", "question", "prompt", default=""),
    )
    if not query:
        query = f"What is the current state for scenario {scenario_id}?"

    current = _current_answer(record, query_record)
    stale = _stale_answer(record, query_record, current)
    label = _item_label(record, query_record)
    old_ts, new_ts = _timestamps_for_label(label)

    context = _context_text(record)
    stale_text = _memory_sentence(query, stale, stale=True)
    current_text = _memory_sentence(query, current, stale=False)
    case_id = f"stale-{scenario_id}-q{query_index:03d}"

    raw_events = [
        {"event_id": "e_context", "text": context or f"STALE scenario {scenario_id}."},
        {"event_id": "e_stale", "text": f"Earlier memory state: {stale}"},
        {"event_id": "e_current", "text": f"Current memory state: {current}"},
    ]
    extracted_memory = [
        {
            "memory_id": "m_stale",
            "text": stale_text,
            "source_event_ids": ["e_stale"],
            "store": old_ts,
        },
        {
            "memory_id": "m_current",
            "text": current_text,
            "source_event_ids": ["e_current"],
            "store": new_ts,
        },
    ]
    return {
        "case_id": case_id,
        "query": query,
        "raw_events": raw_events,
        "extracted_memory": extracted_memory,
        "gold_evidence": [
            {
                "evidence_id": "ev_current",
                "text": f"Current memory state: {current}",
                "source_memory_id": "m_current",
                "source_event_id": "e_current",
                "required_phrases": _required_phrases(str(current)),
            }
        ],
        "gold_answer": str(current),
        "baseline_outputs": [
            {
                "baseline_name": "vector_memory",
                "answer": str(stale),
                "retrieved_memory_ids": ["m_stale", "m_current"],
                "answer_score": 0.0,
                "evidence_score": 0.0,
                "injected_context": stale_text,
            },
            {
                "baseline_name": "fixed_summary",
                "answer": "Unknown",
                "retrieved_memory_ids": [],
                "answer_score": 0.0,
                "evidence_score": 0.0,
                "injected_context": "Summary preserved the topic but not the current state.",
            },
        ],
        "perturbation_label": label,
        "scoring": {
            "answer_metric": "casefold_exact_match",
            "evidence_metric": "gold_evidence_recall",
        },
        "default_store": "episodic",
        "source": "stale",
        "source_scenario_id": str(scenario_id),
    }


def _scenario_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("scenarios", "data", "records", "examples", "cases"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        return [payload]
    raise ValueError("STALE JSON must contain an object or list of objects")


def _query_records(record: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("queries", "questions", "examples"):
        raw = record.get(key)
        if isinstance(raw, list) and raw:
            return [
                item if isinstance(item, dict) else {"query": str(item)}
                for item in raw
            ]
    return [record]


def _item_label(record: dict[str, Any], query_record: dict[str, Any]) -> str:
    raw = " ".join(
        _first_text(source, "label", "type", "failure_type", "conflict_type")
        for source in (query_record, record)
    ).casefold()
    if any(token in raw for token in ("stale", "outdated", "old", "newer")):
        return "item_stale"
    return "item_conflict"


def _current_answer(record: dict[str, Any], query_record: dict[str, Any]) -> str:
    return _first_text(
        query_record,
        "gold_answer",
        "answer",
        "correct_answer",
        "current_answer",
        "current_state",
        default=_first_text(
            record,
            "gold_answer",
            "answer",
            "correct_answer",
            "current_answer",
            "current_state",
            default="the current state",
        ),
    )


def _stale_answer(
    record: dict[str, Any],
    query_record: dict[str, Any],
    current: str,
) -> str:
    stale = _first_text(
        query_record,
        "stale_answer",
        "old_answer",
        "incorrect_answer",
        "conflicting_answer",
        "old_state",
        default=_first_text(
            record,
            "stale_answer",
            "old_answer",
            "incorrect_answer",
            "conflicting_answer",
            "old_state",
            default="an outdated or conflicting state",
        ),
    )
    if stale == current:
        return f"not-{current}"
    return stale


def _context_text(record: dict[str, Any]) -> str:
    raw = record.get("context") or record.get("long_context") or record.get("memory_context")
    if isinstance(raw, str):
        return _shorten(raw, 900)
    if isinstance(raw, list):
        return _shorten(" ".join(str(item) for item in raw), 900)
    return ""


def _first_text(
    source: dict[str, Any],
    *keys: str,
    default: str = "",
) -> str:
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False)
        else:
            text = str(value)
        text = text.strip()
        if text:
            return _shorten(text, 500)
    return default


def _timestamps_for_label(label: str) -> tuple[str, str]:
    if label == "item_stale":
        return "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"
    return "2026-01-01T00:00:00Z", "2026-01-03T00:00:00Z"


def _memory_sentence(query: str, answer: str, *, stale: bool) -> str:
    state = "Earlier remembered answer" if stale else "Current remembered answer"
    return _shorten(f"{state} for '{query}': {answer}", 700)


def _required_phrases(text: str) -> list[str]:
    phrases = [
        part.strip()
        for part in re.split(r"[.;:,]", text)
        if len(part.strip()) >= 3
    ]
    return phrases[:3] or [text.strip()]


def _shorten(text: str, max_len: int) -> str:
    text = re.sub(r"\s+", " ", str(text)).strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "..."


def _case_to_mapping(case: ProbeCase) -> dict[str, Any]:
    row = asdict(case)
    row.pop("_cmd_baseline_name", None)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert STALE JSON to CMD ProbeCase JSON.")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Smoke-test limit; default 0 keeps the full STALE dataset.",
    )
    args = parser.parse_args()

    out = write_stale_probe_cases(args.input, args.output, limit=args.limit)
    count = len(load_stale_probe_cases(args.input, limit=args.limit))
    print(f"Wrote {count} STALE probe cases to {out}")


if __name__ == "__main__":
    main()
