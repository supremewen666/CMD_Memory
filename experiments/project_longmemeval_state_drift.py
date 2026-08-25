#!/usr/bin/env python3
"""Project LongMemEval knowledge updates into gold-free runtime interventions.

The cohort selector and evaluator labels live in this offline adapter.  Its
runtime output contains only immutable session/event structure; answers,
question types, answer-session annotations, and message ``has_answer`` flags
are confined to the scorer-only sidecar.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from cmd_audit.core.state_codec import atomic_json_write, content_sha256
from experiments.longmemeval_arena import load_longmemeval_arena_cases, _render_session
from experiments.run_longmemeval_m0_r1 import _ordered_sessions, iter_json_array


INTERVENTION_SCHEMA = "cmd-ecc-runtime-intervention-v1"
LABEL_SCHEMA = "cmd-ecc-state-drift-evaluator-label-v2"
MANIFEST_SCHEMA = "cmd-longmemeval-state-drift-projection-v1"
PAIR_RE = re.compile(r"^answer_(.+)_([12])$")


def _write_jsonl(path: Path, rows: list[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError(f"projection refuses to overwrite existing output: {path}")
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _pair(row: Mapping[str, Any]) -> tuple[tuple[int, str, str, object], tuple[int, str, str, object]]:
    matches: dict[str, dict[str, tuple[int, str, str, object]]] = {}
    for index, (date, session_id, session) in enumerate(_ordered_sessions(row)):
        match = PAIR_RE.fullmatch(session_id)
        if match:
            matches.setdefault(match.group(1), {})[match.group(2)] = (index, date, session_id, session)
    complete = [value for value in matches.values() if set(value) == {"1", "2"}]
    if len(complete) != 1:
        raise ValueError(f"knowledge-update case {row.get('question_id')} has {len(complete)} update pairs")
    # The annotation suffix is a pair identity, not a chronology contract;
    # LongMemEval contains at least one pair whose _2 event predates _1.
    old, new = sorted(complete[0].values(), key=lambda item: item[0])
    return old, new


def project(
    *, dataset: Path, interventions_output: Path, labels_output: Path,
    manifest_output: Path, seed: int = 24, retrieval_top_k: int = 5,
    candidate_pool_k: int = 10, limit: int = 0,
) -> Mapping[str, object]:
    rows = list(iter_json_array(dataset))
    by_id = {str(row.get("question_id")): row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("LongMemEval dataset has missing or duplicate question_id")
    arena = load_longmemeval_arena_cases(
        dataset, seed=seed, limit=0, retrieval_top_k=retrieval_top_k,
        candidate_pool_k=candidate_pool_k,
    )
    runtime_rows: list[Mapping[str, object]] = []
    label_rows: list[Mapping[str, object]] = []
    excluded: list[Mapping[str, str]] = []
    for case in arena:
        source = by_id[case.case_id]
        # Offline cohort definition only; this field is never copied to runtime_rows.
        if source.get("question_type") != "knowledge-update":
            continue
        old, new = _pair(source)
        old_index, old_date, old_session_id, old_session = old
        new_index, new_date, new_session_id, new_session = new
        old_memory_id = f"session:{old_index:04d}:{old_session_id}"
        new_memory_id = f"session:{new_index:04d}:{new_session_id}"
        recall = set(case.raw["baseline_outputs"][0]["retrieved_memory_ids"])
        if old_memory_id not in recall:
            excluded.append({"case_id": case.case_id, "reason": "superseded-memory-outside-frozen-recall"})
            continue
        new_text = _render_session(new_date, new_session_id, new_session)
        event_id = f"event:{new_memory_id}"
        runtime_rows.append({
            "schema_version": INTERVENTION_SCHEMA,
            "case_id": case.case_id,
            "mechanism": "state_drift",
            "source_event_id": event_id,
            "source_event_sha256": content_sha256({"event_id": event_id, "text": new_text}),
            "superseded_memory_id": old_memory_id,
            "superseding_text": new_text,
        })
        answer = source.get("answer")
        if not isinstance(answer, (str, int)) or isinstance(answer, bool):
            raise ValueError(f"invalid scorer answer for {case.case_id}")
        label_rows.append({
            "schema_version": LABEL_SCHEMA,
            "case_id": case.case_id,
            "query_relation": "target",
            "question": str(source.get("question") or ""),
            "new_value": str(answer),
            "old_value": None,
            "old_value_status": "not-explicit-in-source-dataset",
            "old_session_id": old_session_id,
            "new_session_id": new_session_id,
            "old_evidence_text": _render_session(old_date, old_session_id, old_session),
            "new_evidence_text": new_text,
        })
        if limit and len(runtime_rows) >= limit:
            break
    if not runtime_rows:
        raise ValueError("projection produced no eligible state-drift cases")
    _write_jsonl(interventions_output, runtime_rows)
    _write_jsonl(labels_output, label_rows)
    manifest: dict[str, object] = {
        "schema_version": MANIFEST_SCHEMA,
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "cohort_selector": "question_type=knowledge-update (offline only)",
        "runtime_projection_uses_reference_targets": False,
        "runtime_forbidden_fields": ["answer", "answer_session_ids", "question_type", "has_answer", "old_value", "new_value"],
        "runtime_case_count": len(runtime_rows),
        "scorer_label_count": len(label_rows),
        "excluded_cases": excluded,
        "retrieval_top_k": retrieval_top_k,
        "candidate_pool_k": candidate_pool_k,
        "seed": seed,
        "interventions_sha256": hashlib.sha256(interventions_output.read_bytes()).hexdigest(),
        "labels_sha256": hashlib.sha256(labels_output.read_bytes()).hexdigest(),
        "old_value_labels_complete": False,
    }
    manifest["binding_root"] = content_sha256(manifest)
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    if manifest_output.exists():
        raise ValueError(f"projection refuses to overwrite existing output: {manifest_output}")
    atomic_json_write(manifest_output, manifest, ensure_ascii=False, allow_nan=False, indent=2, trailing_newline=True)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("data/external/longmemeval/input/longmemeval_s_cleaned.json"))
    parser.add_argument("--interventions-output", type=Path, default=Path("protocol/state_drift_interventions.jsonl"))
    parser.add_argument("--labels-output", type=Path, default=Path("protocol/state_drift_labels.jsonl"))
    parser.add_argument("--manifest-output", type=Path, default=Path("protocol/state_drift_projection_manifest.json"))
    parser.add_argument("--seed", type=int, default=24)
    parser.add_argument("--retrieval-top-k", type=int, default=5)
    parser.add_argument("--candidate-pool-k", type=int, default=10)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)
    print(json.dumps(project(**vars(args)), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
