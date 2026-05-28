#!/usr/bin/env python3
"""Offline calibration for the issue 0021 Pre-CMD Hook.

Pipeline:
  Step 1 - Build 5960 per-replay rows, train the 16-feature RPE Judge.
  Step 2 - Measure surrogate-vs-gold gap on the shared hold-out split.
  Step 3 - Grid-search global TOP_K and FALLBACK_THRESHOLD with F2.

The deployment hook remains zero-gold and zero-LLM. Subagent labels are an
offline option; phrase matching is available for deterministic dry runs.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cmd_audit.hook import constants as hook_constants
from cmd_audit.hook.rpe_judge import extract_features, score_replays
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.llm_scoring import SubagentScorer
from cmd_audit.core.models import ProbeCase, RetrievedItem
from cmd_audit.replays import run_v1_replay_portfolio
from cmd_audit.scoring import evidence_recall_from_text
from cmd_audit.surrogate_gap import (
    compute_surrogate_gap_summary,
    measure_surrogate_gaps,
)


@dataclass(frozen=True)
class TrainingSet:
    features: np.ndarray
    labels: np.ndarray
    replay_names: tuple[str, ...]
    case_ids: tuple[str, ...]
    positive_ratio: float


@dataclass(frozen=True)
class CalibrationResult:
    top_k: int
    fallback_threshold: float
    f2: float
    precision: float
    recall: float


def load_cases(path: str | Path) -> list[ProbeCase]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, dict):
        raw_cases = raw.get("cases", [])
    else:
        raw_cases = raw
    return [ProbeCase.from_mapping_v1(row) for row in raw_cases]


def retrieved_items_from(case: ProbeCase) -> tuple[RetrievedItem, ...]:
    memory_by_id = {item.memory_id: item for item in case.extracted_memory}
    return tuple(
        RetrievedItem(memory_id=mid, text=memory_by_id[mid].text)
        for mid in case.primary_baseline.retrieved_memory_ids
        if mid in memory_by_id
    )


def build_scorer(labeler: str, *, max_workers: int) -> Any:
    if labeler == "phrase":
        return None
    client = LLMClient(
        LLMClientConfig(
            model="qwen2.5:7b",
            temperature=0.0,
            max_retries=1,
        )
    )
    return SubagentScorer(
        client,
        max_workers=max_workers,
        fallback_scorer=evidence_recall_from_text,
    )


def build_training_set(
    cases: list[ProbeCase],
    *,
    scorer: Any,
) -> TrainingSet:
    rows: list[tuple[float, ...]] = []
    labels: list[int] = []
    replay_names: list[str] = []
    case_ids: list[str] = []

    for case in cases:
        retrieved_items = retrieved_items_from(case)
        replays = run_v1_replay_portfolio(case, scorer=scorer)
        gain_by_replay = {replay.replay_name: replay.recovery_gain for replay in replays}
        for replay_name in hook_constants.V1_REPLAY_NAME_ORDER:
            rows.append(extract_features(case.query, retrieved_items, replay_name))
            labels.append(1 if gain_by_replay.get(replay_name, 0.0) > 0.0 else 0)
            replay_names.append(replay_name)
            case_ids.append(case.case_id)

    X = np.asarray(rows, dtype=float)
    y = np.asarray(labels, dtype=int)
    positive_ratio = float(y.mean()) if len(y) else 0.0
    return TrainingSet(
        features=X,
        labels=y,
        replay_names=tuple(replay_names),
        case_ids=tuple(case_ids),
        positive_ratio=positive_ratio,
    )


def load_retest_recovery_gains(path: str | Path) -> dict[str, dict[str, float]]:
    """Load LLM-stack per-replay recovery gains from at-scale retest CSV."""
    gains: dict[str, dict[str, float]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"case_id", "replay_name", "recovery_gain"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(
                "retest CSV missing required columns: " + ", ".join(sorted(missing))
            )
        for row in reader:
            case_id = row["case_id"]
            replay_name = row["replay_name"]
            try:
                recovery_gain = float(row["recovery_gain"])
            except ValueError as exc:
                raise ValueError(
                    f"invalid recovery_gain for {case_id}/{replay_name}: "
                    f"{row['recovery_gain']!r}"
                ) from exc
            gains.setdefault(case_id, {})[replay_name] = recovery_gain
    return gains


def build_training_set_from_retest(
    cases: list[ProbeCase],
    retest_gains: dict[str, dict[str, float]],
) -> TrainingSet:
    """Build hook labels from persisted LLM retest gains."""
    rows: list[tuple[float, ...]] = []
    labels: list[int] = []
    replay_names: list[str] = []
    case_ids: list[str] = []
    missing: list[str] = []

    for case in cases:
        retrieved_items = retrieved_items_from(case)
        case_gains = retest_gains.get(case.case_id, {})
        for replay_name in hook_constants.V1_REPLAY_NAME_ORDER:
            if replay_name not in case_gains:
                missing.append(f"{case.case_id}/{replay_name}")
                continue
            rows.append(extract_features(case.query, retrieved_items, replay_name))
            labels.append(1 if case_gains[replay_name] > 0.0 else 0)
            replay_names.append(replay_name)
            case_ids.append(case.case_id)

    if missing:
        preview = ", ".join(missing[:10])
        suffix = "" if len(missing) <= 10 else f", ... (+{len(missing) - 10})"
        raise ValueError(f"retest CSV missing gains for: {preview}{suffix}")

    X = np.asarray(rows, dtype=float)
    y = np.asarray(labels, dtype=int)
    positive_ratio = float(y.mean()) if len(y) else 0.0
    return TrainingSet(
        features=X,
        labels=y,
        replay_names=tuple(replay_names),
        case_ids=tuple(case_ids),
        positive_ratio=positive_ratio,
    )


def train_rpe_judge(
    training_set: TrainingSet,
) -> tuple[tuple[float, ...], float, str]:
    """Train logistic regression, preferring sklearn when available."""
    try:
        from sklearn.linear_model import LogisticRegression  # type: ignore

        model = LogisticRegression(
            class_weight="balanced",
            random_state=42,
            max_iter=1000,
        )
        model.fit(training_set.features, training_set.labels)
        weights = tuple(float(x) for x in model.coef_[0].tolist())
        intercept = float(model.intercept_[0])
        return weights, intercept, "sklearn.linear_model.LogisticRegression"
    except ModuleNotFoundError:
        weights, intercept = _train_numpy_logistic_regression(
            training_set.features,
            training_set.labels,
            random_state=42,
            max_iter=1000,
        )
        return weights, intercept, "numpy_fallback_logistic_regression"


def _train_numpy_logistic_regression(
    X: np.ndarray,
    y: np.ndarray,
    *,
    random_state: int,
    max_iter: int,
) -> tuple[tuple[float, ...], float]:
    del random_state
    if X.shape[1] != 16:
        raise ValueError(f"expected 16 features, got {X.shape[1]}")
    if len(set(y.tolist())) < 2:
        return (0.0,) * 16, 0.0

    n = len(y)
    positives = max(int(y.sum()), 1)
    negatives = max(n - positives, 1)
    sample_weight = np.where(y == 1, n / (2 * positives), n / (2 * negatives))

    weights = np.zeros(X.shape[1], dtype=float)
    intercept = 0.0
    lr = 0.2
    l2 = 1e-4
    for _ in range(max_iter):
        logits = X @ weights + intercept
        probs = 1.0 / (1.0 + np.exp(-np.clip(logits, -50, 50)))
        error = (probs - y) * sample_weight
        grad_w = (X.T @ error) / n + l2 * weights
        grad_b = float(error.mean())
        weights -= lr * grad_w
        intercept -= lr * grad_b

    return tuple(float(x) for x in weights.tolist()), float(intercept)


def save_training_set(
    training_set: TrainingSet,
    path: str | Path,
) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        path,
        features=training_set.features,
        labels=training_set.labels,
        replay_names=np.asarray(training_set.replay_names),
        case_ids=np.asarray(training_set.case_ids),
    )


def calibrate_thresholds(
    holdout_cases: list[ProbeCase],
    *,
    weights: tuple[float, ...],
    intercept: float,
    grid_top_k: Iterable[int] = (2, 3, 4, 5),
) -> tuple[CalibrationResult, list[dict[str, str]]]:
    gold_by_case: dict[str, bool] = {}
    gain_by_case: dict[str, dict[str, float]] = {}
    for case in holdout_cases:
        replays = run_v1_replay_portfolio(case)
        gains = {replay.replay_name: replay.recovery_gain for replay in replays}
        gain_by_case[case.case_id] = gains
        gold_by_case[case.case_id] = any(gain > 0.0 for gain in gains.values())

    rows: list[dict[str, str]] = []
    best = CalibrationResult(
        top_k=hook_constants.TOP_K,
        fallback_threshold=hook_constants.FALLBACK_THRESHOLD,
        f2=-1.0,
        precision=0.0,
        recall=0.0,
    )

    with _patched_hook_constants(
        RPE_JUDGE_WEIGHTS=weights,
        RPE_JUDGE_INTERCEPT=intercept,
    ):
        for top_k in grid_top_k:
            for threshold in _threshold_grid():
                predictions: list[bool] = []
                gold: list[bool] = []
                with _patched_hook_constants(
                    TOP_K=top_k,
                    FALLBACK_THRESHOLD=threshold,
                ):
                    for case in holdout_cases:
                        scores = score_replays(case.query, retrieved_items_from(case))
                        ranked = sorted(
                            scores,
                            key=lambda s: (
                                -s.p_score,
                                hook_constants.V1_REPLAY_NAME_ORDER.index(s.replay_name),
                            ),
                        )
                        triggered = ranked[0].p_score >= threshold if ranked else False
                        selected = ranked[: min(top_k, len(ranked))] if triggered else []
                        predicted = any(
                            gain_by_case[case.case_id].get(score.replay_name, 0.0) > 0.0
                            for score in selected
                        )
                        predictions.append(predicted)
                        gold.append(gold_by_case[case.case_id])

                precision, recall, f2 = _precision_recall_fbeta(
                    gold, predictions, beta=2.0
                )
                rows.append(
                    {
                        "top_k": str(top_k),
                        "fallback_threshold": f"{threshold:.2f}",
                        "precision": f"{precision:.4f}",
                        "recall": f"{recall:.4f}",
                        "f2": f"{f2:.4f}",
                    }
                )
                if f2 > best.f2:
                    best = CalibrationResult(
                        top_k=top_k,
                        fallback_threshold=threshold,
                        f2=f2,
                        precision=precision,
                        recall=recall,
                    )

    return best, rows


def calibrate_thresholds_from_retest(
    holdout_cases: list[ProbeCase],
    *,
    retest_gains: dict[str, dict[str, float]],
    weights: tuple[float, ...],
    intercept: float,
    grid_top_k: Iterable[int] = (2, 3, 4, 5),
) -> tuple[CalibrationResult, list[dict[str, str]]]:
    """Grid-search hook thresholds against persisted LLM retest labels."""
    missing: list[str] = []
    for case in holdout_cases:
        case_gains = retest_gains.get(case.case_id, {})
        for replay_name in hook_constants.V1_REPLAY_NAME_ORDER:
            if replay_name not in case_gains:
                missing.append(f"{case.case_id}/{replay_name}")
    if missing:
        preview = ", ".join(missing[:10])
        suffix = "" if len(missing) <= 10 else f", ... (+{len(missing) - 10})"
        raise ValueError(f"retest CSV missing holdout gains for: {preview}{suffix}")

    rows: list[dict[str, str]] = []
    best = CalibrationResult(
        top_k=hook_constants.TOP_K,
        fallback_threshold=hook_constants.FALLBACK_THRESHOLD,
        f2=-1.0,
        precision=0.0,
        recall=0.0,
    )

    with _patched_hook_constants(
        RPE_JUDGE_WEIGHTS=weights,
        RPE_JUDGE_INTERCEPT=intercept,
    ):
        for top_k in grid_top_k:
            for threshold in _threshold_grid():
                predictions: list[bool] = []
                gold: list[bool] = []
                with _patched_hook_constants(
                    TOP_K=top_k,
                    FALLBACK_THRESHOLD=threshold,
                ):
                    for case in holdout_cases:
                        case_gains = retest_gains[case.case_id]
                        scores = score_replays(case.query, retrieved_items_from(case))
                        ranked = sorted(
                            scores,
                            key=lambda s: (
                                -s.p_score,
                                hook_constants.V1_REPLAY_NAME_ORDER.index(s.replay_name),
                            ),
                        )
                        triggered = ranked[0].p_score >= threshold if ranked else False
                        selected = ranked[: min(top_k, len(ranked))] if triggered else []
                        predicted = any(
                            case_gains.get(score.replay_name, 0.0) > 0.0
                            for score in selected
                        )
                        predictions.append(predicted)
                        gold.append(any(gain > 0.0 for gain in case_gains.values()))

                precision, recall, f2 = _precision_recall_fbeta(
                    gold, predictions, beta=2.0
                )
                rows.append(
                    {
                        "top_k": str(top_k),
                        "fallback_threshold": f"{threshold:.2f}",
                        "precision": f"{precision:.4f}",
                        "recall": f"{recall:.4f}",
                        "f2": f"{f2:.4f}",
                    }
                )
                if f2 > best.f2:
                    best = CalibrationResult(
                        top_k=top_k,
                        fallback_threshold=threshold,
                        f2=f2,
                        precision=precision,
                        recall=recall,
                    )

    return best, rows


def measure_and_write_surrogate_gap(
    holdout_cases: list[ProbeCase],
    output_path: str | Path,
) -> None:
    rows = measure_surrogate_gaps(holdout_cases)
    summary = compute_surrogate_gap_summary(rows)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "case_id",
                "label",
                "gold_recovery_gain",
                "surrogate_recovery_gain",
                "gap",
                "surrogate_found",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "case_id": row.case_id,
                    "label": row.label,
                    "gold_recovery_gain": f"{row.gold_recovery_gain:.4f}",
                    "surrogate_recovery_gain": f"{row.surrogate_recovery_gain:.4f}",
                    "gap": f"{row.gap:.4f}",
                    "surrogate_found": str(row.surrogate_found),
                }
            )
    summary_path = Path(output_path).with_suffix(".summary.md")
    summary_path.write_text(
        "\n".join(
            (
                "# Surrogate Gap Summary",
                "",
                f"- total_cases: {summary.total_cases}",
                f"- gold_dependent_cases: {summary.gold_dependent_cases}",
                f"- cases_with_surrogate: {summary.cases_with_surrogate}",
                f"- avg_gap: {summary.avg_gap:.4f}",
                f"- median_gap: {summary.median_gap:.4f}",
                f"- pct_surrogate_found: {summary.pct_surrogate_found:.4f}",
            )
        )
        + "\n",
        encoding="utf-8",
    )


def write_grid_search(rows: list[dict[str, str]], output_path: str | Path) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "top_k",
                "fallback_threshold",
                "precision",
                "recall",
                "f2",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def write_threshold_grid_artifacts(
    rows: list[dict[str, str]],
    artifact_root: str | Path,
) -> None:
    root = Path(artifact_root)
    write_grid_search(rows, root / "grid_search.csv")
    write_grid_search(rows, root / "threshold_grid.csv")


CONSTANTS_TEMPLATE = '''"""Offline-calibrated constants for the issue 0021 Pre-CMD Hook.

Generated by scripts/calibrate_hook.py.
"""

from __future__ import annotations

V1_REPLAY_NAME_ORDER: tuple[str, ...] = (
    "oracle_write",
    "oracle_compression",
    "verbatim_event_oracle",
    "oracle_retrieval",
    "injection_oracle",
    "evidence_given_reasoning",
    "oracle_route",
    "oracle_granularity",
    "graph_off",
    "safety_off",
)

TOP_K: int = {top_k}
FALLBACK_THRESHOLD: float = {fallback_threshold}

RPE_JUDGE_WEIGHTS: tuple[float, ...] = {weights!r}
RPE_JUDGE_INTERCEPT: float = {intercept!r}
'''


def write_hook_constants(
    output_path: str | Path,
    *,
    weights: tuple[float, ...],
    intercept: float,
    top_k: int,
    fallback_threshold: float,
) -> None:
    content = CONSTANTS_TEMPLATE.format(
        top_k=top_k,
        fallback_threshold=round(float(fallback_threshold), 6),
        weights=tuple(round(float(w), 10) for w in weights),
        intercept=round(float(intercept), 10),
    )
    Path(output_path).write_text(content, encoding="utf-8")


def write_calibration_report(
    output_path: str | Path,
    *,
    num_cases: int,
    train_cases: int,
    holdout_cases: int,
    training_set: TrainingSet,
    solver_name: str,
    best: CalibrationResult,
    elapsed_seconds: float,
    labeler: str,
) -> None:
    lines = [
        "# Hook Calibration Report",
        "",
        f"- total_cases: {num_cases}",
        f"- train_cases: {train_cases}",
        f"- holdout_cases: {holdout_cases}",
        f"- labeler: {labeler}",
        f"- solver: {solver_name}",
        f"- training_rows: {len(training_set.labels)}",
        f"- positive_ratio: {training_set.positive_ratio:.4f}",
        f"- selected_TOP_K: {best.top_k}",
        f"- selected_FALLBACK_THRESHOLD: {best.fallback_threshold:.2f}",
        f"- holdout_precision: {best.precision:.4f}",
        f"- holdout_recall: {best.recall:.4f}",
        f"- holdout_f2: {best.f2:.4f}",
        f"- elapsed_seconds: {elapsed_seconds:.2f}",
        "",
    ]
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")


def calibrate(
    cases_path: str | Path,
    *,
    output_path: str | Path,
    artifact_dir: str | Path,
    labeler: str,
    train_count: int,
    holdout_count: int,
    max_workers: int,
    dry_run: bool,
    retest_csv: str | Path | None = None,
) -> dict[str, Any]:
    started = time.time()
    cases = load_cases(cases_path)
    if len(cases) < train_count + holdout_count:
        raise ValueError(
            f"need at least {train_count + holdout_count} cases, got {len(cases)}"
        )

    train_cases = cases[:train_count]
    holdout_cases = cases[train_count: train_count + holdout_count]
    artifact_root = Path(artifact_dir)
    artifact_root.mkdir(parents=True, exist_ok=True)

    print(f"Step 0: loaded {len(cases)} cases")

    retest_gains: dict[str, dict[str, float]] | None = None
    if retest_csv is not None:
        retest_gains = load_retest_recovery_gains(retest_csv)
        print(
            "Step 1: building "
            f"{len(train_cases) * 10} per-replay training rows from LLM retest"
        )
        training_set = build_training_set_from_retest(train_cases, retest_gains)
        training_set_path = artifact_root / "training_set_llm.npz"
        report_labeler = "llm_retest"
    else:
        print(
            "Step 1: no --retest-csv provided; using legacy labeler path "
            f"({labeler})"
        )
        scorer = build_scorer(labeler, max_workers=max_workers)
        print(f"Step 1: building {len(train_cases) * 10} per-replay training rows")
        training_set = build_training_set(train_cases, scorer=scorer)
        training_set_path = artifact_root / "training_set_subagent.npz"
        report_labeler = labeler

    weights, intercept, solver_name = train_rpe_judge(training_set)
    save_training_set(training_set, training_set_path)
    print(
        "Step 1: trained RPE Judge "
        f"({solver_name}, positive_ratio={training_set.positive_ratio:.3f})"
    )

    print(f"Step 2: measuring surrogate gap on {len(holdout_cases)} hold-out cases")
    measure_and_write_surrogate_gap(
        holdout_cases,
        artifact_root / "surrogate_gap.csv",
    )

    print("Step 3: grid-searching TOP_K x FALLBACK_THRESHOLD")
    if retest_gains is not None:
        best, grid_rows = calibrate_thresholds_from_retest(
            holdout_cases,
            retest_gains=retest_gains,
            weights=weights,
            intercept=intercept,
        )
    else:
        best, grid_rows = calibrate_thresholds(
            holdout_cases,
            weights=weights,
            intercept=intercept,
        )
    write_threshold_grid_artifacts(grid_rows, artifact_root)

    elapsed = time.time() - started
    write_calibration_report(
        artifact_root / "calibration_report.md",
        num_cases=len(cases),
        train_cases=len(train_cases),
        holdout_cases=len(holdout_cases),
        training_set=training_set,
        solver_name=solver_name,
        best=best,
        elapsed_seconds=elapsed,
        labeler=report_labeler,
    )

    if dry_run:
        print("Step 3: dry run; constants file not written")
    else:
        write_hook_constants(
            output_path,
            weights=weights,
            intercept=intercept,
            top_k=best.top_k,
            fallback_threshold=best.fallback_threshold,
        )
        print(f"Step 3: wrote calibrated constants to {output_path}")

    return {
        "weights": weights,
        "intercept": intercept,
        "top_k": best.top_k,
        "fallback_threshold": best.fallback_threshold,
        "f2": best.f2,
        "solver": solver_name,
    }


@contextmanager
def _patched_hook_constants(**values):
    previous = {name: getattr(hook_constants, name) for name in values}
    for name, value in values.items():
        setattr(hook_constants, name, value)
    try:
        yield
    finally:
        for name, value in previous.items():
            setattr(hook_constants, name, value)


def _threshold_grid() -> list[float]:
    return [round(i * 0.05, 2) for i in range(21)]


def _precision_recall_fbeta(
    gold: list[bool],
    pred: list[bool],
    *,
    beta: float,
) -> tuple[float, float, float]:
    tp = sum(1 for g, p in zip(gold, pred) if g and p)
    fp = sum(1 for g, p in zip(gold, pred) if not g and p)
    fn = sum(1 for g, p in zip(gold, pred) if g and not p)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    if precision == 0.0 and recall == 0.0:
        return precision, recall, 0.0
    beta_sq = beta * beta
    fbeta = (1 + beta_sq) * precision * recall / (beta_sq * precision + recall)
    return precision, recall, fbeta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Calibrate issue 0021 Pre-CMD Hook constants"
    )
    parser.add_argument("--cases", required=True, help="path to probe-case JSON")
    parser.add_argument(
        "--output",
        default="cmd_audit/hook/constants.py",
        help="output path for calibrated hook constants",
    )
    parser.add_argument(
        "--artifact-dir",
        default="artifacts/hook_calibration",
        help="directory for training_set_llm.npz or fallback calibration reports",
    )
    parser.add_argument(
        "--labeler",
        choices=("phrase", "subagent"),
        default="phrase",
        help="offline label scorer; use subagent for paper calibration",
    )
    parser.add_argument(
        "--retest-csv",
        default=None,
        help=(
            "path to artifacts/at_scale_llm_retest.csv; when provided, labels "
            "come from recovery_gain > 0 and training_set_llm.npz is written"
        ),
    )
    parser.add_argument("--train-count", type=int, default=546)
    parser.add_argument("--holdout-count", type=int, default=50)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        calibrate(
            args.cases,
            output_path=args.output,
            artifact_dir=args.artifact_dir,
            labeler=args.labeler,
            retest_csv=args.retest_csv,
            train_count=args.train_count,
            holdout_count=args.holdout_count,
            max_workers=args.max_workers,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"calibration failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
