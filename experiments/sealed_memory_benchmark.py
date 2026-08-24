"""Gold-free prediction seal for real LoCoMo and LongMemEval model runs."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import time
from typing import Mapping, Protocol, Sequence

from cmd_audit.core.state_codec import atomic_json_write, content_sha256
from experiments.arena_runner_common import ArenaCase, DualScoreExecution


SEAL_SCHEMA = "cmd-memory-benchmark-prediction-seal-v1"
ROW_SCHEMA = "cmd-memory-benchmark-selection-v1"
GENESIS = "0" * 64


class PredictionBackend(Protocol):
    selection_judge_identity: str

    def candidates(self, case: ArenaCase): ...
    def evaluate(self, case: ArenaCase, candidate, *, input_context: str, origin_context: str) -> DualScoreExecution: ...
    def cmd_call_counts(self, case: ArenaCase) -> tuple[int, int]: ...
    def answer_context(self, case: ArenaCase, context: str, *, purpose: str = "benchmark_control") -> str: ...


def predict_and_seal(
    *,
    benchmark: str,
    cases: Sequence[ArenaCase],
    backend: PredictionBackend,
    dataset_path: Path,
    output: Path,
    candidate_limit: int | None = None,
    include_full_context: bool = True,
) -> Mapping[str, object]:
    """Run BM25, CMD and optional full-context arms without opening gold."""
    if benchmark not in {"locomo", "longmemeval"}:
        raise ValueError("benchmark must be locomo or longmemeval")
    if not cases or any(case.arena_id != benchmark for case in cases):
        raise ValueError("case stream does not match benchmark")
    if candidate_limit is not None and candidate_limit < 1:
        raise ValueError("candidate_limit must be positive")
    output = Path(output)
    if output.exists() and any(path.is_file() for path in output.rglob("*")):
        raise ValueError("fresh prediction refuses a non-empty output directory")
    predictions_dir = output / "predictions"
    arm_rows: dict[str, list[dict[str, str]]] = {"bm25": [], "cmd": []}
    if include_full_context:
        arm_rows["full_context"] = []
    ledger_rows: list[dict[str, object]] = []
    previous = GENESIS
    for position, case in enumerate(cases, 1):
        case_started = time.perf_counter()
        candidates = tuple(backend.candidates(case))
        if candidate_limit is not None:
            candidates = candidates[:candidate_limit]
        executions = tuple(
            backend.evaluate(
                case,
                candidate,
                input_context=case.base_context,
                origin_context=case.base_context,
            )
            for candidate in candidates
        )
        if not executions:
            raise ValueError(f"no legal CMD candidates for {case.case_id}")
        baseline = next(
            (row.baseline_hypothesis for row in executions if row.baseline_hypothesis is not None),
            None,
        )
        if baseline is None:
            statuses = sorted({row.status for row in executions})
            raise RuntimeError(
                "answer endpoint produced no baseline hypothesis; "
                f"candidate statuses={statuses}. Verify LLM_BASE_URL /models "
                "and that LLM_MODEL matches a served model name."
            )
        viable = [
            row for row in executions
            if row.repaired_hypothesis is not None
            and row.gold_free_gain is not None
            and math.isfinite(float(row.gold_free_gain))
            and float(row.gold_free_gain) > 0.0
        ]
        selected = sorted(
            viable,
            key=lambda row: (-float(row.gold_free_gain), row.skill_id),
        )[0] if viable else None
        repaired = selected.repaired_hypothesis if selected is not None else baseline
        arm_rows["bm25"].append({"question_id": case.case_id, "hypothesis": baseline})
        arm_rows["cmd"].append({"question_id": case.case_id, "hypothesis": str(repaired)})
        if include_full_context:
            context = case.raw.get("full_context")
            if not isinstance(context, str) or not context:
                raise ValueError("full-context arm requires a non-empty runtime context")
            arm_rows["full_context"].append({
                "question_id": case.case_id,
                "hypothesis": backend.answer_context(case, context),
            })
        answer_calls, selection_calls = backend.cmd_call_counts(case)
        payload = {
            "schema_version": ROW_SCHEMA,
            "position": position,
            "case_id": case.case_id,
            "previous_row_hash": previous,
            "runtime_input_root": content_sha256(_runtime_projection(case)),
            "candidate_count": len(candidates),
            "finite_positive_candidate_count": len(viable),
            "selected_skill_id": selected.skill_id if selected else None,
            "selected_gold_free_gain": selected.gold_free_gain if selected else None,
            "abstained_to_bm25": selected is None,
            "cmd_answer_calls": answer_calls,
            "cmd_selection_judge_calls": selection_calls,
            "full_context_answer_calls": 1 if include_full_context else 0,
            "case_latency_seconds": time.perf_counter() - case_started,
            "prediction_roots": {
                arm: content_sha256(rows[-1]) for arm, rows in arm_rows.items()
            },
        }
        row_hash = content_sha256(payload)
        ledger_rows.append({**payload, "row_hash": row_hash})
        previous = row_hash
    predictions_dir.mkdir(parents=True, exist_ok=True)
    for arm, rows in arm_rows.items():
        _write_jsonl(predictions_dir / f"{arm}.jsonl", rows)
    _write_jsonl(output / "selection_ledger.jsonl", ledger_rows)
    roots = {
        path.name: _sha_file(path)
        for path in sorted(predictions_dir.glob("*.jsonl"))
    }
    dataset_path = Path(dataset_path)
    seal = {
        "schema_version": SEAL_SCHEMA,
        "benchmark": benchmark,
        "dataset_sha256": _sha_file(dataset_path),
        "case_count": len(cases),
        "case_ids_root": content_sha256([case.case_id for case in cases]),
        "runtime_stream_root": content_sha256([_runtime_projection(case) for case in cases]),
        "selection_judge_identity": str(backend.selection_judge_identity),
        "candidate_limit": candidate_limit,
        "arms": list(arm_rows),
        "prediction_file_roots": roots,
        "selection_ledger_sha256": _sha_file(output / "selection_ledger.jsonl"),
        "selection_ledger_head": previous,
        "prediction_shape": ["question_id", "hypothesis"],
        "runtime_uses_gold": False,
        "sealed": True,
    }
    seal["binding_root"] = content_sha256(seal)
    atomic_json_write(
        output / "prediction_seal.json",
        seal,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        trailing_newline=True,
    )
    return seal


def validate_seal(output: Path) -> Mapping[str, object]:
    output = Path(output)
    seal = json.loads((output / "prediction_seal.json").read_text(encoding="utf-8"))
    if not isinstance(seal, Mapping) or seal.get("schema_version") != SEAL_SCHEMA or seal.get("sealed") is not True:
        raise ValueError("invalid memory benchmark prediction seal")
    expected = seal.get("prediction_file_roots")
    actual = {
        path.name: _sha_file(path)
        for path in sorted((output / "predictions").glob("*.jsonl"))
    }
    if expected != actual:
        raise ValueError("prediction files changed after seal")
    if seal.get("selection_ledger_sha256") != _sha_file(output / "selection_ledger.jsonl"):
        raise ValueError("selection ledger changed after seal")
    check = dict(seal)
    binding = check.pop("binding_root", None)
    if binding != content_sha256(check):
        raise ValueError("prediction seal binding root mismatch")
    return seal


def _runtime_projection(case: ArenaCase) -> dict[str, object]:
    return {
        "arena_id": case.arena_id,
        "case_id": case.case_id,
        "family_id": case.family_id,
        "base_context": case.base_context,
        "runtime_branch": case.runtime_branch,
        "hook_confidence": case.hook_confidence,
        "query": case.raw.get("query"),
        "raw_events": case.raw.get("raw_events"),
        "extracted_memory": case.raw.get("extracted_memory"),
        "baseline_outputs": case.raw.get("baseline_outputs"),
        "full_context": case.raw.get("full_context"),
    }


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False))
            handle.write("\n")


def _sha_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
