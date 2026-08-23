"""P4C-2 paired repair-efficacy prediction runner.

The runner consumes the completed P4C-1 ECC ABI and a separate gold-free
answer-input projection.  It compares the same query over the pre-repair state
and the ECC-committed state, seals predictions, and deliberately has no scoring
or router-update seam.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cmd_audit.core.state_codec import append_jsonl_fsync, atomic_json_write, content_sha256
from cmd_audit.repair.ecc import EccRepairReceipt
from experiments.p4c_ecc_runner import RUN_SCHEMA_VERSION
from experiments.run_longmemeval_e2e import (
    AnswerRequest,
    Answerer,
    FakeAnswerer,
    OpenAICompatibleAnswerer,
)
from experiments.run_longmemeval_m0_r1 import iter_json_array


P4C2_INPUT_SCHEMA = "cmd-p4c2-answer-input-v1"
P4C2_ROW_SCHEMA = "cmd-p4c2-paired-prediction-v1"
P4C2_SEAL_SCHEMA = "cmd-p4c2-prediction-seal-v1"
P4C2_MANIFEST_SCHEMA = "cmd-p4c2-live-efficacy-v1"
ARMS = ("control", "repaired")
DEFAULT_PROMPT = (
    "Answer the query using only the active memory state below. "
    "If the state does not support an answer, say Unknown."
)
_FORBIDDEN_KEYS = frozenset(
    {"answer", "answers", "gold", "label", "labels", "oracle", "reference", "references"}
)
_INPUT_FIELDS = frozenset(
    {
        "schema_version",
        "case_id",
        "source",
        "query",
        "control_state_root",
        "repaired_state_root",
        "control_memories",
        "repaired_memories",
        "incident_overlay_sha256",
        "repair_receipt_sha256",
    }
)
_ROW_FIELDS = frozenset(
    {
        "schema_version",
        "event_index",
        "case_id",
        "source",
        "mechanism",
        "arm",
        "initial_state_root",
        "arm_state_root",
        "incident_overlay_sha256",
        "repair_receipt_sha256",
        "hypothesis",
        "previous_hash",
        "event_hash",
    }
)


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unavailable or invalid: {path}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _jsonl(path: Path, label: str) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"{label} is unavailable: {path}") from exc
    for index, line in enumerate(lines, 1):
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} line {index} is invalid JSON") from exc
        if not isinstance(value, Mapping):
            raise ValueError(f"{label} line {index} must be an object")
        rows.append(value)
    return rows


def _assert_gold_free(value: object, path: str = "input") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).casefold() in _FORBIDDEN_KEYS:
                raise ValueError(f"P4C-2 gold-free boundary rejects {path}.{key}")
            _assert_gold_free(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_gold_free(nested, f"{path}[{index}]")


def _memories(value: object, label: str) -> tuple[Mapping[str, str], ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    result: list[Mapping[str, str]] = []
    for row in value:
        if not isinstance(row, Mapping) or set(row) != {"memory_id", "content", "source_hash"}:
            raise ValueError(f"{label} record is not closed")
        if not all(isinstance(row.get(key), str) for key in ("memory_id", "content", "source_hash")):
            raise ValueError(f"{label} record values must be strings")
        if content_sha256(row["content"], ensure_ascii=False, allow_nan=False) != row["source_hash"]:
            raise ValueError(f"{label} content/source hash mismatch")
        result.append({key: str(row[key]) for key in ("memory_id", "content", "source_hash")})
    return tuple(result)


@dataclass(frozen=True)
class P4c2Case:
    case_id: str
    source: str
    mechanism: str
    query: str
    initial_state_root: str
    repaired_state_root: str
    control_memories: tuple[Mapping[str, str], ...]
    repaired_memories: tuple[Mapping[str, str], ...]
    incident_overlay_sha256: str
    repair_receipt_sha256: str


def build_plan(*, limit: int, output: Path, run_mode: str) -> dict[str, object]:
    if limit < 1:
        raise ValueError("P4C-2 limit must be positive")
    if run_mode not in {"fresh", "resume"}:
        raise ValueError("P4C-2 run mode must be fresh or resume")
    return {
        "schema_version": P4C2_MANIFEST_SCHEMA,
        "mode": "plan",
        "stage": "P4C-2 repair-vs-control paired live efficacy",
        "arms": list(ARMS),
        "case_limit": limit,
        "planned_calls": limit * len(ARMS),
        "external_calls_authorized": False,
        "runtime_gold_free": True,
        "same_trace_answer_replay": False,
        "router_feedback": "EccRepairReceipt-only",
        "evaluator_boundary": "prediction-seal-first",
        "run_mode": run_mode,
        "output": str(output),
    }


def _session_text(session: object) -> str:
    if not isinstance(session, list):
        raise ValueError("LongMemEval session must be a list")
    lines: list[str] = []
    for message in session:
        if (
            not isinstance(message, Mapping)
            or not isinstance(message.get("role"), str)
            or not isinstance(message.get("content"), str)
        ):
            raise ValueError("LongMemEval session message is invalid")
        lines.append(f"{message['role']}: {message['content']}")
    return "\n".join(lines)


def prepare_inputs(
    *, p4c1_run: Path, longmemeval_data: Path, output: Path, limit: int
) -> Mapping[str, object]:
    """Build LongMemEval paired views without projecting benchmark answers.

    P4C-1 currently provides a natural query contract only for LongMemEval.
    MemFail and poison cases fail closed here until a separately frozen query
    contract is available for those sources.
    """
    if limit < 1:
        raise ValueError("P4C-2 prepare limit must be positive")
    p4c1_run, longmemeval_data, output = (
        Path(p4c1_run),
        Path(longmemeval_data),
        Path(output),
    )
    if output.exists():
        raise ValueError("P4C-2 prepare refuses to overwrite an existing input projection")
    p4c1_manifest = _json_object(p4c1_run / "p4c1_manifest.json", "P4C-1 manifest")
    expected_source_root = (p4c1_manifest.get("source_roots") or {}).get("longmemeval")
    if expected_source_root != _sha_file(longmemeval_data):
        raise ValueError("LongMemEval source root differs from the P4C-1 frozen root")
    projections = _jsonl(p4c1_run / "source_projection.jsonl", "P4C-1 source projection")
    overlays = _jsonl(p4c1_run / "incident_overlay.jsonl", "P4C-1 incident overlay")
    receipts = [
        EccRepairReceipt.from_mapping(row)
        for row in _jsonl(
            p4c1_run / "runtime" / "repair_receipts.jsonl", "P4C-1 repair receipts"
        )
    ]
    if not (len(projections) == len(overlays) == len(receipts)):
        raise ValueError("P4C-1 streams are not aligned")
    frozen: dict[str, tuple[Mapping[str, Any], Mapping[str, Any], EccRepairReceipt]] = {}
    for projection, overlay, receipt in zip(projections, overlays, receipts, strict=True):
        if projection.get("source") == "longmemeval":
            source_case_id = projection.get("source_case_id")
            if not isinstance(source_case_id, str):
                raise ValueError("P4C-1 LongMemEval projection lacks source_case_id")
            frozen[source_case_id] = (projection, overlay, receipt)
    rows: list[dict[str, object]] = []
    for raw in iter_json_array(longmemeval_data):
        # This is an allowlist projection.  In particular, `answer`,
        # `answer_session_ids`, and `question_type` are never accessed.
        question_id = raw.get("question_id")
        if not isinstance(question_id, str) or question_id not in frozen:
            continue
        question, sessions = raw.get("question"), raw.get("haystack_sessions")
        session_ids = raw.get("haystack_session_ids")
        if (
            not isinstance(question, str)
            or not isinstance(sessions, list)
            or not isinstance(session_ids, list)
        ):
            raise ValueError("LongMemEval gold-free fields are invalid")
        projection, overlay, receipt = frozen[question_id]
        memory_records = projection.get("memory_records")
        if not isinstance(memory_records, list) or len(memory_records) != 2:
            raise ValueError("P4C-1 LongMemEval projection must bind exactly two memories")
        indexed = {str(value): index for index, value in enumerate(session_ids)}
        projected_sessions: list[tuple[str, object]] = []
        for memory in memory_records:
            if not isinstance(memory, Mapping) or str(memory.get("source_event_id")) not in indexed:
                raise ValueError("P4C-1 memory has no LongMemEval session binding")
            session = sessions[indexed[str(memory["source_event_id"])]]
            if content_sha256(session, ensure_ascii=False, allow_nan=False) != memory.get("content_sha256"):
                raise ValueError("LongMemEval session differs from P4C-1 content root")
            projected_sessions.append((str(memory["memory_id"]), session))
        if not receipt.committed or receipt.before_root != projection.get("state_root"):
            raise ValueError("P4C-2 input preparation requires committed P4C-1 state drift")
        control_memories = []
        for memory_id, session in projected_sessions:
            content = _session_text(session)
            control_memories.append(
                {
                    "memory_id": memory_id,
                    "content": content,
                    "source_hash": content_sha256(content, ensure_ascii=False, allow_nan=False),
                }
            )
        # P4C-1's chronology repair deterministically supersedes the first of
        # the two projected memories with the second.
        repaired_memories = [control_memories[1]]
        row = {
            "schema_version": P4C2_INPUT_SCHEMA,
            "case_id": overlay["case_id"],
            "source": "longmemeval",
            "query": question,
            "control_state_root": receipt.before_root,
            "repaired_state_root": receipt.after_root,
            "control_memories": control_memories,
            "repaired_memories": repaired_memories,
            "incident_overlay_sha256": content_sha256(
                overlay, ensure_ascii=False, allow_nan=False
            ),
            "repair_receipt_sha256": receipt.content_hash,
        }
        _assert_gold_free(row)
        rows.append(row)
        if len(rows) >= limit:
            break
    if not rows:
        raise ValueError("no eligible LongMemEval P4C-2 cases were prepared")
    output.parent.mkdir(parents=True, exist_ok=True)
    for row in rows:
        append_jsonl_fsync(output, row, ensure_ascii=False, allow_nan=False)
    return {
        "schema_version": "cmd-p4c2-input-preparation-v1",
        "status": "success",
        "source": "longmemeval",
        "case_count": len(rows),
        "runtime_gold_free": True,
        "unsupported_sources": ["memfail", "poison_sweep"],
        "unsupported_reason": "no frozen natural answer-query contract",
        "p4c1_manifest_sha256": _sha_file(p4c1_run / "p4c1_manifest.json"),
        "source_sha256": _sha_file(longmemeval_data),
        "inputs_sha256": _sha_file(output),
    }


def preflight(
    *, p4c1_run: Path, inputs: Path, limit: int
) -> tuple[Mapping[str, object], tuple[P4c2Case, ...]]:
    """Validate committed P4C-1 state roots and project only gold-free inputs."""
    if limit < 1:
        raise ValueError("P4C-2 limit must be positive")
    p4c1_run, inputs = Path(p4c1_run), Path(inputs)
    p4c1_manifest = _json_object(p4c1_run / "p4c1_manifest.json", "P4C-1 manifest")
    runtime_manifest = _json_object(p4c1_run / "runtime" / "manifest.json", "P4C-1 runtime manifest")
    if not (
        p4c1_manifest.get("schema_version") == "cmd-p4c1-real-source-zero-call-v1"
        and p4c1_manifest.get("status") == "success"
        and p4c1_manifest.get("runtime_uses_gold") is False
        and p4c1_manifest.get("runtime_uses_labels") is False
        and p4c1_manifest.get("router_feedback") == "EccRepairReceipt"
        and p4c1_manifest.get("model_call_count") == 0
        and runtime_manifest.get("schema_version") == RUN_SCHEMA_VERSION
        and runtime_manifest.get("status") == "success"
    ):
        raise ValueError("P4C-1 ECC live-ABI prerequisite is invalid")

    projections = _jsonl(p4c1_run / "source_projection.jsonl", "P4C-1 source projection")
    overlays = _jsonl(p4c1_run / "incident_overlay.jsonl", "P4C-1 incident overlay")
    raw_receipts = _jsonl(p4c1_run / "runtime" / "repair_receipts.jsonl", "P4C-1 repair receipts")
    if not projections or not (len(projections) == len(overlays) == len(raw_receipts)):
        raise ValueError("P4C-1 projection/overlay/receipt streams are not aligned")
    receipts = [EccRepairReceipt.from_mapping(row) for row in raw_receipts]
    bindings: dict[str, tuple[Mapping[str, Any], Mapping[str, Any], EccRepairReceipt]] = {}
    for projection, overlay, receipt in zip(projections, overlays, receipts, strict=True):
        case_id = overlay.get("case_id")
        if (
            not isinstance(case_id, str)
            or overlay.get("schema_version") != "cmd-p4c1-incident-overlay-v1"
            or projection.get("schema_version") != "cmd-p4c1-source-projection-v1"
            or overlay.get("source") != projection.get("source")
            or overlay.get("state_root") != projection.get("state_root")
            or receipt.before_root != projection.get("state_root")
        ):
            raise ValueError("P4C-1 case streams have an identity/root mismatch")
        bindings[case_id] = (projection, overlay, receipt)

    cases: list[P4c2Case] = []
    for raw in _jsonl(inputs, "P4C-2 answer inputs"):
        _assert_gold_free(raw)
        if set(raw) != _INPUT_FIELDS or raw.get("schema_version") != P4C2_INPUT_SCHEMA:
            raise ValueError("P4C-2 answer input is not closed or versioned")
        case_id = raw.get("case_id")
        if not isinstance(case_id, str) or case_id not in bindings:
            raise ValueError("P4C-2 answer input has no P4C-1 case binding")
        projection, overlay, receipt = bindings[case_id]
        if not receipt.committed:
            raise ValueError("P4C-2 repaired arm requires an ECC-committed receipt")
        overlay_hash = content_sha256(overlay, ensure_ascii=False, allow_nan=False)
        if (
            raw.get("source") != overlay.get("source")
            or raw.get("control_state_root") != receipt.before_root
            or raw.get("repaired_state_root") != receipt.after_root
            or raw.get("incident_overlay_sha256") != overlay_hash
            or raw.get("repair_receipt_sha256") != receipt.content_hash
        ):
            raise ValueError("P4C-2 answer input is not bound to its ECC transition")
        query = raw.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("P4C-2 query must be non-empty")
        cases.append(
            P4c2Case(
                case_id=case_id,
                source=str(raw["source"]),
                mechanism=str(overlay["mechanism"]),
                query=query,
                initial_state_root=receipt.before_root,
                repaired_state_root=receipt.after_root,
                control_memories=_memories(raw["control_memories"], "control_memories"),
                repaired_memories=_memories(raw["repaired_memories"], "repaired_memories"),
                incident_overlay_sha256=overlay_hash,
                repair_receipt_sha256=receipt.content_hash,
            )
        )
        if len(cases) >= limit:
            break
    if not cases or len({case.case_id for case in cases}) != len(cases):
        raise ValueError("P4C-2 requires unique eligible cases")
    roots = {
        "p4c1_manifest_sha256": _sha_file(p4c1_run / "p4c1_manifest.json"),
        "p4c1_runtime_manifest_sha256": _sha_file(p4c1_run / "runtime" / "manifest.json"),
        "source_projection_sha256": _sha_file(p4c1_run / "source_projection.jsonl"),
        "incident_overlay_sha256": _sha_file(p4c1_run / "incident_overlay.jsonl"),
        "repair_receipts_sha256": _sha_file(p4c1_run / "runtime" / "repair_receipts.jsonl"),
        "answer_inputs_sha256": _sha_file(inputs),
    }
    return (
        {
            "schema_version": P4C2_MANIFEST_SCHEMA,
            "mode": "preflight",
            "preflight_passed": True,
            "runtime_gold_free": True,
            "eligible_case_count": len(cases),
            "roots": roots,
        },
        tuple(cases),
    )


def _load_prediction_prefix(path: Path) -> tuple[list[dict[str, object]], str]:
    rows: list[dict[str, object]] = []
    head = "0" * 64
    if not path.exists():
        return rows, head
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if not isinstance(row, dict) or set(row) != _ROW_FIELDS:
            raise ValueError("P4C-2 prediction journal row is not closed")
        expected = content_sha256(
            {key: value for key, value in row.items() if key != "event_hash"},
            ensure_ascii=False,
            allow_nan=False,
        )
        if (
            row.get("schema_version") != P4C2_ROW_SCHEMA
            or row.get("event_index") != len(rows) + 1
            or row.get("previous_hash") != head
            or row.get("event_hash") != expected
        ):
            raise ValueError("P4C-2 prediction journal chain is invalid")
        rows.append(row)
        head = str(row["event_hash"])
    return rows, head


def run_p4c2(
    *,
    p4c1_run: Path,
    inputs: Path,
    output: Path,
    answerer: Answerer | None = None,
    limit: int,
    prompt: str = DEFAULT_PROMPT,
    temperature: float = 0.0,
    run_mode: str = "fresh",
) -> Mapping[str, object]:
    """Generate and seal paired predictions; scoring is intentionally absent."""
    report, cases = preflight(p4c1_run=p4c1_run, inputs=inputs, limit=limit)
    if run_mode not in {"fresh", "resume"}:
        raise ValueError("P4C-2 run mode must be fresh or resume")
    output = Path(output)
    if run_mode == "fresh" and output.exists() and any(output.iterdir()):
        raise ValueError("fresh P4C-2 run refuses a non-empty output directory")
    output.mkdir(parents=True, exist_ok=True)
    selected_answerer = answerer or FakeAnswerer()
    binding = {
        "binding_schema_version": P4C2_MANIFEST_SCHEMA,
        "case_stream_sha256": content_sha256(
            [
                {
                    "case_id": case.case_id,
                    "source": case.source,
                    "mechanism": case.mechanism,
                    "initial_state_root": case.initial_state_root,
                    "repaired_state_root": case.repaired_state_root,
                    "incident_overlay_sha256": case.incident_overlay_sha256,
                    "repair_receipt_sha256": case.repair_receipt_sha256,
                }
                for case in cases
            ],
            ensure_ascii=False,
            allow_nan=False,
        ),
        "roots": report["roots"],
        "prompt_sha256": content_sha256(prompt, ensure_ascii=False, allow_nan=False),
        "answerer_model": selected_answerer.model_id,
        "temperature": temperature,
        "arms": list(ARMS),
    }
    binding_root = content_sha256(binding, ensure_ascii=False, allow_nan=False)
    journal_path = output / "paired_predictions.jsonl"
    rows, head = _load_prediction_prefix(journal_path)
    expected = [(case, arm) for case in cases for arm in ARMS]
    if len(rows) > len(expected):
        raise ValueError("P4C-2 prediction prefix exceeds the frozen case stream")
    for row, (case, arm) in zip(rows, expected, strict=False):
        state_root = case.initial_state_root if arm == "control" else case.repaired_state_root
        if (
            row["case_id"] != case.case_id
            or row["arm"] != arm
            or row["arm_state_root"] != state_root
            or row["incident_overlay_sha256"] != case.incident_overlay_sha256
            or row["repair_receipt_sha256"] != case.repair_receipt_sha256
        ):
            raise ValueError("P4C-2 prediction prefix does not match frozen inputs")
    if rows and run_mode != "resume":
        raise ValueError("existing P4C-2 predictions require resume mode")

    for case, arm in expected[len(rows):]:
        memories = case.control_memories if arm == "control" else case.repaired_memories
        response = selected_answerer.answer(AnswerRequest(case.query, memories, prompt, temperature))
        if not isinstance(response.hypothesis, str) or not response.hypothesis.strip():
            raise ValueError("P4C-2 answerer returned an empty hypothesis")
        body: dict[str, object] = {
            "schema_version": P4C2_ROW_SCHEMA,
            "event_index": len(rows) + 1,
            "case_id": case.case_id,
            "source": case.source,
            "mechanism": case.mechanism,
            "arm": arm,
            "initial_state_root": case.initial_state_root,
            "arm_state_root": case.initial_state_root if arm == "control" else case.repaired_state_root,
            "incident_overlay_sha256": case.incident_overlay_sha256,
            "repair_receipt_sha256": case.repair_receipt_sha256,
            "hypothesis": response.hypothesis,
            "previous_hash": head,
        }
        body["event_hash"] = content_sha256(body, ensure_ascii=False, allow_nan=False)
        append_jsonl_fsync(journal_path, body, ensure_ascii=False, allow_nan=False)
        rows.append(body)
        head = str(body["event_hash"])

    seal: dict[str, object] = {
        "schema_version": P4C2_SEAL_SCHEMA,
        **binding,
        "binding_root": binding_root,
        "paired_prediction_head": head,
        "paired_prediction_count": len(rows),
        "paired_case_count": len(cases),
        "paired_predictions_sha256": _sha_file(journal_path),
        "gold_opened": False,
        "router_updated_from_predictions": False,
        "sealed": True,
    }
    atomic_json_write(
        output / "prediction_seal.json",
        seal,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        trailing_newline=True,
    )
    manifest = {
        "schema_version": P4C2_MANIFEST_SCHEMA,
        "status": "prediction_sealed",
        "runtime_gold_free": True,
        "external_calls_authorized": not isinstance(selected_answerer, FakeAnswerer),
        "paired_case_count": len(cases),
        "paired_prediction_count": len(rows),
        "paired_prediction_head": head,
        "paired_predictions_sha256": _sha_file(journal_path),
        "binding_root": binding_root,
        "roots": report["roots"],
        "prediction_seal_sha256": _sha_file(output / "prediction_seal.json"),
        "router_feedback": "EccRepairReceipt-only",
        "router_updated_from_predictions": False,
        "sealed_evaluator": "not_opened_by_runtime",
    }
    atomic_json_write(
        output / "manifest.json",
        manifest,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        trailing_newline=True,
    )
    return seal


def _config(path: Path) -> Mapping[str, object]:
    value = _json_object(path, "LLM config")
    if set(value) != {"base_url", "api_key", "model"} or not all(
        isinstance(value.get(key), str) and value[key] for key in value
    ):
        raise ValueError("LLM config requires only non-empty base_url/api_key/model")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="print the default zero-call plan")
    mode.add_argument("--preflight", action="store_true", help="validate roots without model calls")
    mode.add_argument("--prepare", action="store_true", help="prepare gold-free LongMemEval paired inputs")
    mode.add_argument("--execute-fake", action="store_true", help="run deterministic zero-call wiring")
    mode.add_argument("--execute-live", action="store_true", help="explicitly authorize OpenAI-compatible calls")
    parser.add_argument("--p4c1-run", type=Path, default=Path("artifacts/experiments/p4c1_real_sources_v1"))
    parser.add_argument("--inputs", type=Path, default=Path("artifacts/experiments/p4c2_answer_inputs_v1.jsonl"))
    parser.add_argument("--longmemeval-data", type=Path, default=Path("data/external/longmemeval/input/longmemeval_s_cleaned.json"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/experiments/p4c2_live_efficacy_v1"))
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--run-mode", choices=("fresh", "resume"), default="fresh")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--llm-config", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not any((args.preflight, args.prepare, args.execute_fake, args.execute_live)):
        print(json.dumps(build_plan(limit=args.limit, output=args.output, run_mode=args.run_mode), indent=2))
        return 0
    if args.prepare:
        print(
            json.dumps(
                prepare_inputs(
                    p4c1_run=args.p4c1_run,
                    longmemeval_data=args.longmemeval_data,
                    output=args.inputs,
                    limit=args.limit,
                ),
                indent=2,
            )
        )
        return 0
    report, _cases = preflight(p4c1_run=args.p4c1_run, inputs=args.inputs, limit=args.limit)
    if args.preflight:
        print(json.dumps(report, indent=2))
        return 0
    prompt = args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else DEFAULT_PROMPT
    answerer: Answerer | None = None
    if args.execute_live:
        if args.llm_config is None:
            raise ValueError("--execute-live requires --llm-config")
        config = _config(args.llm_config)
        answerer = OpenAICompatibleAnswerer(config, str(config["model"]), args.temperature)
    seal = run_p4c2(
        p4c1_run=args.p4c1_run,
        inputs=args.inputs,
        output=args.output,
        answerer=answerer,
        limit=args.limit,
        prompt=prompt,
        temperature=args.temperature,
        run_mode=args.run_mode,
    )
    print(json.dumps(seal, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
