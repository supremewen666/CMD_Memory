"""Adapter for STALE implicit-conflict memory scenarios.

The STALE schema maps directly into the existing :class:`ProbeCase` contract:

- ``probing_queries.dim1_query`` / ``dim2_query`` / ``dim3_query`` each become
  one query-case;
- ``M_old`` becomes the stale memory item;
- ``M_new`` becomes the current memory item and gold answer;
- ``type`` maps T1/T2 to ``item_stale`` / ``item_conflict``;
- ``explanation`` is preserved as causal/provenance raw evidence;
- ``haystack_session`` becomes a distractor memory item.
- recall contains both the stale/conflicting item and the current item;
- raw events carry the reachable current state.
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
    query = str(query_record["query"]).strip()
    query_dim = str(query_record.get("dimension", f"q{query_index + 1}")).strip()

    current = _current_answer(record)
    stale = _stale_answer(record, current)
    label = _item_label(record)
    old_ts, new_ts = _timestamps_for_label(label)
    explanation = _explanation_text(record)
    haystack = _haystack_text(record)

    stale_text = _memory_sentence(stale, stale=True)
    current_text = _memory_sentence(current, stale=False)
    haystack_text = _shorten(haystack, 700) if haystack else "No haystack distractor provided."
    case_id = f"stale-{scenario_id}-{query_dim}"

    raw_events = [
        {
            "event_id": "e_haystack",
            "text": haystack or f"STALE haystack context for scenario {scenario_id}.",
        },
        {"event_id": "e_stale", "text": f"M_old: {stale}"},
        {"event_id": "e_current", "text": f"M_new: {current}"},
        {
            "event_id": "e_explanation",
            "text": explanation or "STALE explanation not provided.",
        },
    ]
    extracted_memory = [
        {
            "memory_id": "m_stale",
            "text": stale_text,
            "source_event_ids": ["e_stale", "e_explanation"],
            "store": old_ts,
        },
        {
            "memory_id": "m_current",
            "text": current_text,
            "source_event_ids": ["e_current", "e_explanation"],
            "store": new_ts,
        },
        {
            "memory_id": "m_haystack",
            "text": haystack_text,
            "source_event_ids": ["e_haystack"],
            "store": "haystack",
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
                "text": f"M_new: {current}",
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
                "retrieved_memory_ids": ["m_stale", "m_current", "m_haystack"],
                "answer_score": 0.0,
                "evidence_score": 0.0,
                "injected_context": _shorten(
                    f"{stale_text}\n\nDistractor context:\n{haystack_text}",
                    700,
                ),
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
        "stale_query_dimension": query_dim,
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
    probing = record.get("probing_queries")
    if isinstance(probing, dict):
        records = []
        for dimension in ("dim1", "dim2", "dim3"):
            value = probing.get(f"{dimension}_query")
            if value is None:
                continue
            query = str(value).strip()
            if query:
                records.append({"dimension": dimension, "query": query})
        if records:
            return records

    for key in ("queries", "questions", "examples"):
        raw = record.get(key)
        if isinstance(raw, list) and raw:
            return [
                {
                    "dimension": f"q{i + 1}",
                    "query": (
                        _first_text(item, "query", "question", "prompt")
                        if isinstance(item, dict)
                        else str(item)
                    ),
                }
                for i, item in enumerate(raw)
            ]
    query = _first_text(record, "query", "question", "prompt")
    if query:
        return [{"dimension": "q1", "query": query}]
    raise ValueError("STALE record is missing probing_queries.dim*_query fields")


def _item_label(record: dict[str, Any]) -> str:
    raw = _first_text(record, "type", "label", "failure_type", "conflict_type").casefold()
    normalized = re.sub(r"[^a-z0-9]+", "", raw)
    if normalized == "t1":
        return "item_stale"
    if normalized == "t2":
        return "item_conflict"
    if any(token in raw for token in ("stale", "outdated", "old", "newer")):
        return "item_stale"
    if any(token in raw for token in ("conflict", "contradict")):
        return "item_conflict"
    raise ValueError(f"unsupported STALE type: {raw!r}")


def _current_answer(record: dict[str, Any]) -> str:
    current = _first_text(
        record,
        "M_new",
        "m_new",
        "gold_answer",
        "answer",
        "correct_answer",
        "current_answer",
        "current_state",
    )
    if not current:
        raise ValueError("STALE record is missing M_new")
    return current


def _stale_answer(
    record: dict[str, Any],
    current: str,
) -> str:
    stale = _first_text(
        record,
        "M_old",
        "m_old",
        "stale_answer",
        "old_answer",
        "incorrect_answer",
        "conflicting_answer",
        "old_state",
    )
    if not stale:
        raise ValueError("STALE record is missing M_old")
    if stale == current:
        return f"not-{current}"
    return stale


def _explanation_text(record: dict[str, Any]) -> str:
    return _first_text(record, "explanation", "cause", "reason")


def _haystack_text(record: dict[str, Any]) -> str:
    raw = (
        record.get("haystack_session")
        or record.get("haystack")
        or record.get("context")
        or record.get("long_context")
        or record.get("memory_context")
    )
    return _stringify_context(raw)


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


def _memory_sentence(answer: str, *, stale: bool) -> str:
    state = "M_old" if stale else "M_new"
    return _shorten(f"{state}: {answer}", 700)


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


def _stringify_context(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return _shorten(raw, 900)
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            if isinstance(item, dict):
                role = item.get("role") or item.get("speaker") or item.get("name") or "context"
                content = item.get("content") or item.get("text") or item.get("message") or ""
                content_text = _stringify_context(content)
                if content_text:
                    parts.append(f"[{role}] {content_text}")
            else:
                text = _stringify_context(item)
                if text:
                    parts.append(text)
        return _shorten(" ".join(parts), 900)
    if isinstance(raw, dict):
        return _shorten(json.dumps(raw, ensure_ascii=False, sort_keys=True), 900)
    return _shorten(str(raw), 900)


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
