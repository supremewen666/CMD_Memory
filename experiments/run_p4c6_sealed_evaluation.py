"""P4C-6 independent evaluation after the P4C-2 prediction seal."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Mapping, Protocol, Sequence

from cmd_audit.core.state_codec import append_jsonl_fsync, atomic_json_write, content_sha256
from experiments.run_longmemeval_m0_r1 import iter_json_array


SIDECAR_SCHEMA = "cmd-p4c6-sealed-sidecar-v1"
EVALUATION_SCHEMA = "cmd-p4c6-sealed-evaluation-v1"
OUTCOME_SCHEMA = "cmd-p4c6-evaluation-outcome-v1"
P4C2_ROW_SCHEMA = "cmd-p4c2-paired-prediction-v1"
P4C2_SEAL_SCHEMA = "cmd-p4c2-prediction-seal-v1"
P4C2_MANIFEST_SCHEMA = "cmd-p4c2-live-efficacy-v1"
ARMS = ("control", "repaired")
_ROW_FIELDS = {
    "schema_version", "event_index", "case_id", "source", "mechanism", "arm",
    "initial_state_root", "arm_state_root", "incident_overlay_sha256",
    "repair_receipt_sha256", "hypothesis", "previous_hash", "event_hash",
}
_SIDECAR_FIELDS = {"schema_version", "case_id", "question", "reference"}
_OUTCOME_FIELDS = {
    "schema_version", "event_index", "previous_hash", "binding_root", "case_id",
    "source", "mechanism", "reference_sha256", "question_sha256",
    "control_hypothesis_sha256", "repaired_hypothesis_sha256",
    "control_correct", "repaired_correct", "backend", "judge_model",
    "external_call_count", "event_hash",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _object(path: Path, name: str) -> Mapping[str, object]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name} is unavailable or invalid") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _jsonl(path: Path, name: str) -> list[Mapping[str, object]]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"{name} is unavailable") from exc
    rows: list[Mapping[str, object]] = []
    for index, line in enumerate(lines, 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} line {index} must be an object")
        rows.append(value)
    if not rows:
        raise ValueError(f"{name} must not be empty")
    return rows


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _reference(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    raise ValueError("sealed reference must be a string or integer")


def _norm(value: str) -> str:
    return re.sub(r"\W+", "", value).casefold()


def _require_outside_runtime(path: Path, p4c2_run: Path, name: str) -> None:
    resolved = Path(path).resolve()
    runtime = Path(p4c2_run).resolve()
    if resolved == runtime or runtime in resolved.parents:
        raise ValueError(f"{name} must be outside the P4C-2 runtime directory")


@dataclass(frozen=True)
class EvaluationPair:
    case_id: str
    source: str
    mechanism: str
    question: str
    reference: str
    control_hypothesis: str
    repaired_hypothesis: str


def _validate_p4c2(p4c2_run: Path) -> tuple[Mapping[str, object], list[tuple[Mapping[str, object], Mapping[str, object]]]]:
    p4c2_run = Path(p4c2_run)
    manifest_path = p4c2_run / "manifest.json"
    seal_path = p4c2_run / "prediction_seal.json"
    journal_path = p4c2_run / "paired_predictions.jsonl"
    manifest = _object(manifest_path, "P4C-2 manifest")
    seal = _object(seal_path, "P4C-2 prediction seal")
    if not (
        manifest.get("schema_version") == P4C2_MANIFEST_SCHEMA
        and manifest.get("status") == "prediction_sealed"
        and manifest.get("runtime_gold_free") is True
        and manifest.get("router_updated_from_predictions") is False
        and manifest.get("sealed_evaluator") == "not_opened_by_runtime"
        and seal.get("schema_version") == P4C2_SEAL_SCHEMA
        and seal.get("sealed") is True
        and seal.get("gold_opened") is False
        and seal.get("router_updated_from_predictions") is False
        and seal.get("arms") == list(ARMS)
    ):
        raise ValueError("P4C-2 prediction seal prerequisite is invalid")
    if manifest.get("prediction_seal_sha256") != _sha(seal_path):
        raise ValueError("P4C-2 prediction seal root mismatch")
    journal_root = _sha(journal_path)
    if seal.get("paired_predictions_sha256") != journal_root or manifest.get("paired_predictions_sha256") != journal_root:
        raise ValueError("P4C-2 paired prediction file root mismatch")

    rows = _jsonl(journal_path, "P4C-2 paired predictions")
    head = "0" * 64
    for index, row in enumerate(rows, 1):
        expected_hash = content_sha256(
            {key: value for key, value in row.items() if key != "event_hash"},
            ensure_ascii=False,
            allow_nan=False,
        )
        if (
            set(row) != _ROW_FIELDS
            or row.get("schema_version") != P4C2_ROW_SCHEMA
            or row.get("event_index") != index
            or row.get("previous_hash") != head
            or row.get("event_hash") != expected_hash
        ):
            raise ValueError("P4C-2 paired prediction hash chain is invalid")
        head = str(row["event_hash"])
    if not (
        seal.get("paired_prediction_head") == head
        and manifest.get("paired_prediction_head") == head
        and seal.get("paired_prediction_count") == len(rows)
        and manifest.get("paired_prediction_count") == len(rows)
        and len(rows) == 2 * int(seal.get("paired_case_count", -1))
        and manifest.get("paired_case_count") == seal.get("paired_case_count")
        and manifest.get("binding_root") == seal.get("binding_root")
        and manifest.get("roots") == seal.get("roots")
    ):
        raise ValueError("P4C-2 paired prediction head/count/binding mismatch")
    pairs: list[tuple[Mapping[str, object], Mapping[str, object]]] = []
    for position in range(0, len(rows), 2):
        control, repaired = rows[position : position + 2]
        identity_fields = (
            "case_id", "source", "mechanism", "initial_state_root",
            "incident_overlay_sha256", "repair_receipt_sha256",
        )
        if (
            control.get("arm") != "control"
            or repaired.get("arm") != "repaired"
            or any(control.get(field) != repaired.get(field) for field in identity_fields)
            or not isinstance(control.get("hypothesis"), str)
            or not isinstance(repaired.get("hypothesis"), str)
        ):
            raise ValueError("P4C-2 predictions are not complete control/repaired pairs")
        pairs.append((control, repaired))
    if len({str(control["case_id"]) for control, _ in pairs}) != len(pairs):
        raise ValueError("P4C-2 paired case ids must be unique")
    binding = {
        "p4c2_manifest_sha256": _sha(manifest_path),
        "prediction_seal_sha256": _sha(seal_path),
        "paired_predictions_sha256": journal_root,
        "paired_prediction_head": head,
        "paired_prediction_count": len(rows),
        "paired_case_count": len(pairs),
        "p4c2_binding_root": seal["binding_root"],
    }
    return binding, pairs


def _sidecar(path: Path) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for row in _jsonl(path, "P4C-6 sealed sidecar"):
        if set(row) != _SIDECAR_FIELDS or row.get("schema_version") != SIDECAR_SCHEMA:
            raise ValueError("P4C-6 sidecar schema is not closed")
        case_id = _text(row.get("case_id"), "sidecar case_id")
        question = _text(row.get("question"), "sidecar question")
        reference = _reference(row.get("reference"))
        if case_id in result:
            raise ValueError("P4C-6 sidecar case ids must be unique")
        result[case_id] = (question, reference)
    return result


def preflight(*, p4c2_run: Path, sealed_sidecar: Path) -> tuple[Mapping[str, object], tuple[EvaluationPair, ...]]:
    _require_outside_runtime(Path(sealed_sidecar), Path(p4c2_run), "sealed sidecar")
    binding, prediction_pairs = _validate_p4c2(p4c2_run)
    sidecar = _sidecar(Path(sealed_sidecar))
    prediction_ids = {str(control["case_id"]) for control, _ in prediction_pairs}
    if set(sidecar) != prediction_ids:
        raise ValueError("sealed sidecar must exactly cover P4C-2 paired cases")
    pairs = tuple(
        EvaluationPair(
            case_id=str(control["case_id"]),
            source=str(control["source"]),
            mechanism=str(control["mechanism"]),
            question=sidecar[str(control["case_id"])][0],
            reference=sidecar[str(control["case_id"])][1],
            control_hypothesis=str(control["hypothesis"]),
            repaired_hypothesis=str(repaired["hypothesis"]),
        )
        for control, repaired in prediction_pairs
    )
    report = {
        "schema_version": EVALUATION_SCHEMA,
        "mode": "preflight",
        "preflight_passed": True,
        "paired_case_count": len(pairs),
        "sealed_sidecar_sha256": _sha(Path(sealed_sidecar)),
        "p4c2_binding": binding,
        "external_calls_authorized": False,
        "paper_role": "supplementary",
        "mainline_commit_authority": False,
    }
    return report, pairs


def prepare_sidecar(*, p4c2_run: Path, longmemeval_data: Path, output: Path) -> Mapping[str, object]:
    """After validating the P4C-2 seal, project references into a separate sidecar."""
    binding, prediction_pairs = _validate_p4c2(p4c2_run)
    output = Path(output)
    _require_outside_runtime(output, Path(p4c2_run), "sealed sidecar")
    if output.exists():
        raise ValueError("P4C-6 sidecar preparation refuses to overwrite output")
    raw_by_id: dict[str, Mapping[str, object]] = {}
    for row in iter_json_array(Path(longmemeval_data)):
        question_id = row.get("question_id")
        if isinstance(question_id, str):
            raw_by_id[question_id] = row
    projected: list[Mapping[str, object]] = []
    for control, _ in prediction_pairs:
        case_id = str(control["case_id"])
        matches = [qid for qid in raw_by_id if case_id == qid or case_id.endswith("-" + qid)]
        if len(matches) != 1:
            raise ValueError("P4C-6 could not uniquely bind a case to LongMemEval")
        raw = raw_by_id[matches[0]]
        question = _text(raw.get("question"), "LongMemEval question")
        raw_reference = raw.get("answer")
        _reference(raw_reference)
        projected.append(
            {
                "schema_version": SIDECAR_SCHEMA,
                "case_id": case_id,
                "question": question,
                "reference": raw_reference,
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    for row in projected:
        append_jsonl_fsync(output, row, ensure_ascii=False, allow_nan=False)
    return {
        "schema_version": SIDECAR_SCHEMA,
        "status": "prepared_after_prediction_seal",
        "case_count": len(projected),
        "sidecar_sha256": _sha(output),
        "source_dataset_sha256": _sha(Path(longmemeval_data)),
        "prediction_seal_sha256": binding["prediction_seal_sha256"],
        "output": str(output),
    }


class SemanticJudge(Protocol):
    model_id: str

    def verdict(self, *, question: str, hypothesis: str, reference: str) -> bool: ...


class OpenAICompatibleSemanticJudge:
    def __init__(self, config: Mapping[str, object]) -> None:
        if set(config) != {"base_url", "api_key", "model"} or not all(
            isinstance(config.get(key), str) and config[key] for key in config
        ):
            raise ValueError("semantic judge config requires only base_url/api_key/model")
        self.model_id = str(config["model"])
        self._config = dict(config)

    def verdict(self, *, question: str, hypothesis: str, reference: str) -> bool:
        from cmd_audit.core.llm_client import LLMClient, LLMClientConfig

        client = LLMClient(
            LLMClientConfig(
                base_url=str(self._config["base_url"]),
                api_key=str(self._config["api_key"]),
                model=self.model_id,
                temperature=0.0,
            )
        )
        payload = json.dumps(
            {"question": question, "hypothesis": hypothesis, "reference": reference},
            ensure_ascii=False,
        )
        raw = client.generate_json(
            "Decide whether the hypothesis correctly answers the question according to the reference.\n" + payload,
            schema={
                "type": "object",
                "properties": {"correct": {"type": "boolean"}},
                "required": ["correct"],
                "additionalProperties": False,
            },
            schema_name="p4c6_semantic_verdict",
            system="Return only the schema-constrained correctness verdict.",
        )
        value = json.loads(raw)
        if not isinstance(value, Mapping) or set(value) != {"correct"} or not isinstance(value["correct"], bool):
            raise ValueError("semantic judge returned an invalid verdict")
        return bool(value["correct"])


def _load_outcomes(path: Path, binding_root: str) -> tuple[list[dict[str, object]], str]:
    if not path.exists():
        return [], "0" * 64
    rows: list[dict[str, object]] = []
    head = "0" * 64
    for index, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        row = json.loads(raw)
        expected = content_sha256({key: value for key, value in row.items() if key != "event_hash"}, ensure_ascii=False, allow_nan=False)
        if (
            not isinstance(row, dict)
            or set(row) != _OUTCOME_FIELDS
            or row.get("schema_version") != OUTCOME_SCHEMA
            or row.get("event_index") != index
            or row.get("previous_hash") != head
            or row.get("binding_root") != binding_root
            or row.get("event_hash") != expected
        ):
            raise ValueError("P4C-6 outcome journal chain is invalid")
        rows.append(row)
        head = str(row["event_hash"])
    return rows, head


def _metric(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    count = len(rows)
    control_correct = sum(row["control_correct"] is True for row in rows)
    repaired_correct = sum(row["repaired_correct"] is True for row in rows)
    recovery = sum(row["control_correct"] is False and row["repaired_correct"] is True for row in rows)
    harm = sum(row["control_correct"] is True and row["repaired_correct"] is False for row in rows)
    return {
        "count": count,
        "control_accuracy": control_correct / count if count else None,
        "repaired_accuracy": repaired_correct / count if count else None,
        "paired_delta": (repaired_correct - control_correct) / count if count else None,
        "recovery_count": recovery,
        "recovery_rate": recovery / count if count else None,
        "harm_count": harm,
        "harm_rate": harm / count if count else None,
    }


def evaluate(
    *,
    p4c2_run: Path,
    sealed_sidecar: Path,
    output: Path,
    backend: str,
    run_mode: str = "fresh",
    judge: SemanticJudge | None = None,
) -> Mapping[str, object]:
    preflight_report, pairs = preflight(p4c2_run=p4c2_run, sealed_sidecar=sealed_sidecar)
    if backend not in {"exact", "openai-compatible"}:
        raise ValueError("P4C-6 backend must be exact or openai-compatible")
    if run_mode not in {"fresh", "resume"}:
        raise ValueError("P4C-6 run mode must be fresh or resume")
    if backend == "openai-compatible" and judge is None:
        raise ValueError("openai-compatible evaluation requires explicit config and execute")
    if backend == "exact" and judge is not None:
        raise ValueError("exact evaluation does not accept a live judge")
    output = Path(output)
    _require_outside_runtime(output, Path(p4c2_run), "evaluation output")
    if run_mode == "fresh" and output.exists() and any(output.iterdir()):
        raise ValueError("fresh P4C-6 evaluation refuses a non-empty output directory")
    model = "normalized-exact-v1" if backend == "exact" else str(judge.model_id)
    binding = {
        "schema_version": EVALUATION_SCHEMA,
        "p4c2_binding": preflight_report["p4c2_binding"],
        "sealed_sidecar_sha256": preflight_report["sealed_sidecar_sha256"],
        "backend": backend,
        "judge_model": model,
    }
    binding_root = content_sha256(binding, ensure_ascii=False, allow_nan=False)
    output.mkdir(parents=True, exist_ok=True)
    binding_path = output / "evaluation_binding.json"
    if binding_path.exists():
        if _object(binding_path, "P4C-6 evaluation binding") != binding:
            raise ValueError("P4C-6 resume binding changed")
    else:
        atomic_json_write(binding_path, binding, ensure_ascii=False, allow_nan=False, indent=2, trailing_newline=True)
    outcome_path = output / "evaluation_outcomes.jsonl"
    outcomes, head = _load_outcomes(outcome_path, binding_root)
    if outcomes and run_mode != "resume":
        raise ValueError("existing P4C-6 outcomes require resume mode")
    if len(outcomes) > len(pairs):
        raise ValueError("P4C-6 outcome prefix exceeds sealed cases")
    for outcome, pair in zip(outcomes, pairs, strict=False):
        if outcome["case_id"] != pair.case_id or outcome["source"] != pair.source or outcome["mechanism"] != pair.mechanism:
            raise ValueError("P4C-6 outcome prefix differs from sealed cases")
    for pair in pairs[len(outcomes):]:
        if backend == "exact":
            control_correct = _norm(pair.control_hypothesis) == _norm(pair.reference)
            repaired_correct = _norm(pair.repaired_hypothesis) == _norm(pair.reference)
            calls = 0
        else:
            assert judge is not None
            control_correct = judge.verdict(question=pair.question, hypothesis=pair.control_hypothesis, reference=pair.reference)
            repaired_correct = judge.verdict(question=pair.question, hypothesis=pair.repaired_hypothesis, reference=pair.reference)
            calls = 2
        body: dict[str, object] = {
            "schema_version": OUTCOME_SCHEMA,
            "event_index": len(outcomes) + 1,
            "previous_hash": head,
            "binding_root": binding_root,
            "case_id": pair.case_id,
            "source": pair.source,
            "mechanism": pair.mechanism,
            "reference_sha256": content_sha256(pair.reference, ensure_ascii=False, allow_nan=False),
            "question_sha256": content_sha256(pair.question, ensure_ascii=False, allow_nan=False),
            "control_hypothesis_sha256": content_sha256(pair.control_hypothesis, ensure_ascii=False, allow_nan=False),
            "repaired_hypothesis_sha256": content_sha256(pair.repaired_hypothesis, ensure_ascii=False, allow_nan=False),
            "control_correct": control_correct,
            "repaired_correct": repaired_correct,
            "backend": backend,
            "judge_model": model,
            "external_call_count": calls,
        }
        body["event_hash"] = content_sha256(body, ensure_ascii=False, allow_nan=False)
        append_jsonl_fsync(outcome_path, body, ensure_ascii=False, allow_nan=False)
        outcomes.append(body)
        head = str(body["event_hash"])
    groups: dict[str, object] = {}
    for field in ("source", "mechanism"):
        values = sorted({str(row[field]) for row in outcomes})
        groups[field] = {value: _metric([row for row in outcomes if row[field] == value]) for value in values}
    report: dict[str, object] = {
        "schema_version": EVALUATION_SCHEMA,
        "status": "evaluation_complete",
        "evaluation_binding_sha256": _sha(binding_path),
        "binding_root": binding_root,
        "outcome_head": head,
        "outcome_count": len(outcomes),
        "outcome_sha256": _sha(outcome_path),
        "backend": backend,
        "judge_model": model,
        "external_call_count": sum(int(row["external_call_count"]) for row in outcomes),
        "runtime_mutated": False,
        "raw_references_emitted": False,
        "metrics": _metric(outcomes),
        "groups": groups,
        "claim_scope": "paired_sealed_answer_evaluation_not_router_feedback",
        "paper_role": "supplementary",
        "mainline_commit_authority": False,
    }
    atomic_json_write(output / "evaluation_report.json", report, ensure_ascii=False, allow_nan=False, indent=2, trailing_newline=True)
    return report


def build_plan(*, p4c2_run: Path, sidecar: Path, output: Path, backend: str) -> Mapping[str, object]:
    return {
        "schema_version": EVALUATION_SCHEMA,
        "mode": "plan",
        "stage": "P4C-6 independent sealed paired evaluation",
        "p4c2_run": str(p4c2_run),
        "sidecar": str(sidecar),
        "output": str(output),
        "backend": backend,
        "external_calls_authorized": False,
        "runtime_mutated": False,
        "reference_output_policy": "hashes_and_verdicts_only",
        "paper_role": "supplementary",
        "mainline_commit_authority": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--evaluate", action="store_true")
    mode.add_argument("--prepare-sidecar", action="store_true")
    parser.add_argument("--p4c2-run", type=Path, default=Path("artifacts/experiments/p4c2_live_efficacy_v1"))
    parser.add_argument("--sidecar", type=Path, default=Path("artifacts/sealed/p4c6_longmemeval_sidecar_v1.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/experiments/p4c6_sealed_evaluation_v1"))
    parser.add_argument("--longmemeval-data", type=Path, default=Path("data/external/longmemeval/input/longmemeval_s_cleaned.json"))
    parser.add_argument("--backend", choices=("exact", "openai-compatible"), default="exact")
    parser.add_argument("--llm-config", type=Path)
    parser.add_argument("--run-mode", choices=("fresh", "resume"), default="fresh")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not any((args.preflight, args.evaluate, args.prepare_sidecar)):
        print(json.dumps(build_plan(p4c2_run=args.p4c2_run, sidecar=args.sidecar, output=args.output, backend=args.backend), indent=2))
        return 0
    if args.prepare_sidecar:
        if args.backend != "exact" or args.llm_config is not None:
            raise ValueError("sidecar preparation is zero-call and rejects judge configuration")
        result = prepare_sidecar(p4c2_run=args.p4c2_run, longmemeval_data=args.longmemeval_data, output=args.sidecar)
    elif args.preflight:
        if args.backend == "openai-compatible" and args.llm_config is None:
            raise ValueError("semantic preflight requires explicit --llm-config")
        if args.llm_config is not None:
            OpenAICompatibleSemanticJudge(_object(args.llm_config, "semantic judge config"))
        result, _ = preflight(p4c2_run=args.p4c2_run, sealed_sidecar=args.sidecar)
        result = {**result, "backend": args.backend, "semantic_config_present": args.llm_config is not None}
    else:
        judge = None
        if args.backend == "openai-compatible":
            if args.llm_config is None:
                raise ValueError("semantic evaluation requires explicit --llm-config and --evaluate")
            judge = OpenAICompatibleSemanticJudge(_object(args.llm_config, "semantic judge config"))
        result = evaluate(
            p4c2_run=args.p4c2_run,
            sealed_sidecar=args.sidecar,
            output=args.output,
            backend=args.backend,
            run_mode=args.run_mode,
            judge=judge,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


__all__ = ["build_plan", "evaluate", "preflight", "prepare_sidecar"]


if __name__ == "__main__":
    raise SystemExit(main())
