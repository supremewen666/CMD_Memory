"""Post-seal adapters to the official LoCoMo and LongMemEval evaluators."""
from __future__ import annotations

from collections import defaultdict
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Mapping

from cmd_audit.core.state_codec import atomic_json_write
from experiments.sealed_memory_benchmark import validate_seal


def _validate_prediction_seal(run_dir: Path) -> Mapping[str, object]:
    """Validate either a legacy arena seal or the v2 ECC causal seal."""

    raw = json.loads((Path(run_dir) / "prediction_seal.json").read_text(encoding="utf-8"))
    if raw.get("schema_version") in {
        "cmd-ecc-memory-benchmark-prediction-seal-v2",
        "cmd-ecc-memory-benchmark-prediction-seal-v3",
    }:
        from experiments.run_ecc_sealed_memory_benchmark import (
            validate_ecc_prediction_seal,
        )

        return validate_ecc_prediction_seal(run_dir)
    return validate_seal(run_dir)


def longmemeval_commands(
    *,
    run_dir: Path,
    official_root: Path,
    oracle: Path,
    judge_model: str,
) -> list[list[str]]:
    """Return the upstream ICLR-2025 evaluator command for every sealed arm."""
    seal = _validate_prediction_seal(run_dir)
    if seal.get("benchmark") != "longmemeval":
        raise ValueError("LongMemEval scorer requires a LongMemEval seal")
    script = Path(official_root) / "src" / "evaluation" / "evaluate_qa.py"
    if not script.is_file() or not Path(oracle).is_file():
        raise ValueError("official LongMemEval evaluator or oracle is missing")
    return [
        [sys.executable, str(script), judge_model, str(Path(run_dir) / "predictions" / f"{arm}.jsonl"), str(Path(oracle))]
        for arm in seal["arms"]
    ]


def run_longmemeval_official(
    *,
    run_dir: Path,
    official_root: Path,
    oracle: Path,
    judge_model: str,
) -> Mapping[str, object]:
    """Execute the official judge only after validating the prediction seal."""
    commands = longmemeval_commands(
        run_dir=run_dir,
        official_root=official_root,
        oracle=oracle,
        judge_model=judge_model,
    )
    rows = []
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=Path(official_root) / "src" / "evaluation",
            text=True,
            capture_output=True,
            check=False,
        )
        rows.append({
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        })
        if completed.returncode:
            raise RuntimeError(f"official LongMemEval scorer failed: {completed.stderr}")
    report = {
        "schema_version": "cmd-longmemeval-official-score-receipt-v1",
        "official_evaluator": True,
        "judge_model": judge_model,
        "prediction_seal": str(Path(run_dir) / "prediction_seal.json"),
        "executions": rows,
    }
    atomic_json_write(Path(run_dir) / "official_score_receipt.json", report, indent=2, trailing_newline=True)
    return report


def score_locomo_official(
    *,
    run_dir: Path,
    dataset: Path,
    official_root: Path,
) -> Mapping[str, object]:
    """Call upstream ``eval_question_answering`` on sealed predictions."""
    seal = _validate_prediction_seal(run_dir)
    if seal.get("benchmark") != "locomo":
        raise ValueError("LoCoMo scorer requires a LoCoMo seal")
    if _sha_file(dataset) != seal.get("dataset_sha256"):
        raise ValueError("LoCoMo dataset differs from the sealed prediction input")
    evaluation = Path(official_root) / "task_eval" / "evaluation.py"
    if not evaluation.is_file():
        raise ValueError("official LoCoMo task_eval/evaluation.py is missing")
    spec = importlib.util.spec_from_file_location("locomo_official_evaluation", evaluation)
    if spec is None or spec.loader is None:
        raise ValueError("cannot import official LoCoMo evaluator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    scorer = getattr(module, "eval_question_answering", None)
    if not callable(scorer):
        raise ValueError("official LoCoMo evaluator lacks eval_question_answering")
    samples = json.loads(Path(dataset).read_text(encoding="utf-8"))
    report_arms: dict[str, object] = {}
    for arm in seal["arms"]:
        predictions = _predictions(Path(run_dir) / "predictions" / f"{arm}.jsonl")
        rows = []
        categories = []
        for sample in samples:
            sample_id = str(sample["sample_id"])
            for index, qa in enumerate(sample["qa"]):
                case_id = f"{sample_id}:q{index:04d}"
                if case_id not in predictions:
                    continue
                rows.append({**qa, "prediction": predictions[case_id]})
                categories.append(int(qa["category"]))
        if len(rows) != int(seal["case_count"]):
            raise ValueError("LoCoMo official scoring coverage differs from seal")
        scores, _lengths, _recall = scorer(rows, "prediction")
        grouped: dict[int, list[float]] = defaultdict(list)
        for category, score in zip(categories, scores, strict=True):
            grouped[category].append(float(score))
        report_arms[str(arm)] = {
            "count": len(scores),
            "official_f1": sum(scores) / len(scores) if scores else None,
            "by_category": {
                str(category): {
                    "count": len(values),
                    "official_f1": sum(values) / len(values),
                }
                for category, values in sorted(grouped.items())
            },
        }
    report = {
        "schema_version": "cmd-locomo-official-score-v1",
        "official_evaluator": True,
        "prediction_seal": str(Path(run_dir) / "prediction_seal.json"),
        "category_mapping": {
            "1": "multi_hop",
            "2": "temporal",
            "3": "open_domain",
            "4": "single_hop",
            "5": "adversarial",
        },
        "arms": report_arms,
    }
    atomic_json_write(Path(run_dir) / "official_score_report.json", report, indent=2, trailing_newline=True)
    return report


def _predictions(path: Path) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        qid, hypothesis = row.get("question_id"), row.get("hypothesis")
        if not isinstance(qid, str) or not isinstance(hypothesis, str) or qid in rows:
            raise ValueError("invalid or duplicate sealed prediction")
        rows[qid] = hypothesis
    return rows


def _sha_file(path: Path) -> str:
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
