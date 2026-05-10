"""Evidence-driven version gates — Issue 0010."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .labels import V0_PIPELINE_LABEL_ORDER
from .writers import write_text_artifact

_GATE_DECISION_VALUES = ("approved", "deferred", "rejected")

V0V1_CRITERION_IDS = (
    "macro_f1_exceeds_baselines",
    "confusion_diagonal_dominance",
    "accuracy_top2_exceeds_baselines",
    "repair_assessment_distribution",
)


# ── Data types ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GateCriterion:
    """Single criterion within a version gate check."""

    criterion_id: str
    description: str
    artifact_path: str
    threshold: str
    passed: bool
    evidence: str
    missing: str


@dataclass(frozen=True)
class GateResult:
    """Result of checking all criteria for a version gate."""

    gate_id: str
    criteria: tuple[GateCriterion, ...]
    all_passed: bool
    checked_at: str


@dataclass(frozen=True)
class GateReview:
    """HITL review decision for a version gate."""

    gate_id: str
    reviewer: str
    decision: str
    rationale: str
    missing_evidence: str
    reviewed_at: str

    def __post_init__(self) -> None:
        if self.decision not in _GATE_DECISION_VALUES:
            raise ValueError(
                f"GateReview decision must be one of {_GATE_DECISION_VALUES}, "
                f"got {self.decision!r}"
            )


# ── V0→V1 gate check ───────────────────────────────────────────────────


def check_v0_to_v1_gate(
    artifacts_dir: Path | None = None,
    sandbox_dir: Path | None = None,
) -> GateResult:
    """Check all four V0→V1 evidence gate criteria.

    Returns a GateResult with pass/fail per criterion. The final decision is HITL.
    """
    if artifacts_dir is None:
        artifacts_dir = Path("artifacts")
    if sandbox_dir is None:
        sandbox_dir = Path("artifacts/sandbox")

    criteria: list[GateCriterion] = []

    # Criterion 1: Macro F1 exceeds baselines
    criteria.append(_check_macro_f1(artifacts_dir / "comparison_metrics.csv"))

    # Criterion 2: Confusion matrix diagonal dominance
    criteria.append(
        _check_confusion_diagonal(artifacts_dir / "attribution_confusion_matrix.csv")
    )

    # Criterion 3: Attribution accuracy and top-2 exceed baselines
    criteria.append(
        _check_accuracy_top2(artifacts_dir / "comparison_metrics.csv")
    )

    # Criterion 4: Repair assessment distribution
    criteria.append(_check_repair_distribution(sandbox_dir / "post_repair_table.csv"))

    all_passed = all(c.passed for c in criteria)
    checked_at = datetime.now(timezone.utc).isoformat()

    return GateResult(
        gate_id="V0→V1",
        criteria=tuple(criteria),
        all_passed=all_passed,
        checked_at=checked_at,
    )


# ── V1→V2 gate check ───────────────────────────────────────────────────


def check_v1_to_v2_gate() -> GateResult:
    """Check V1→V2 gate: at least two distinct memory agents integrated.

    V0 has no adapter integrations, so this always returns not-met.
    """
    checked_at = datetime.now(timezone.utc).isoformat()
    criterion = GateCriterion(
        criterion_id="adapter_integration_count",
        description=(
            "At least two distinct memory agents integrated through "
            "the Adapter Interface without macro F1 regression"
        ),
        artifact_path="(none — adapter integrations do not exist in V0)",
        threshold="adapter_count >= 2 AND no macro F1 regression",
        passed=False,
        evidence="0 adapter integrations; V0 operates as standalone harness.",
        missing=(
            "No Adapter Interface integrations exist. V1 must integrate "
            "at least two distinct memory agents before V1→V2 gate review."
        ),
    )
    return GateResult(
        gate_id="V1→V2",
        criteria=(criterion,),
        all_passed=False,
        checked_at=checked_at,
    )


# ── Gate status output ──────────────────────────────────────────────────


def write_gate_status(
    result: GateResult,
    output_path: Path,
    sandbox_root: str | Path | None = None,
) -> Path:
    """Write a human-readable gate status document to *output_path*.

    The output path must satisfy the sandbox write boundary.
    Returns the path that was written.
    """
    lines: list[str] = []
    lines.append(f"CMD {result.gate_id} Gate Status — {result.checked_at[:10]}")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"All criteria passed: {result.all_passed}")
    lines.append("")

    for i, c in enumerate(result.criteria, 1):
        status = "PASS" if c.passed else "FAIL"
        lines.append(f"Criterion {i}: {c.criterion_id} [{status}]")
        lines.append(f"  Description: {c.description}")
        lines.append(f"  Artifact:    {c.artifact_path}")
        lines.append(f"  Threshold:   {c.threshold}")
        lines.append(f"  Evidence:    {c.evidence}")
        if c.missing:
            lines.append(f"  Missing:     {c.missing}")
        lines.append("")

    lines.append("---")
    lines.append("Final decision: HITL review required.")
    lines.append(f"Checked at: {result.checked_at}")
    lines.append("")

    return write_text_artifact(output_path, lines, sandbox_root=sandbox_root)


def write_gate_review(
    review: GateReview,
    output_path: Path,
    sandbox_root: str | Path | None = None,
) -> Path:
    """Write a dated HITL gate review note to *output_path*.

    The output path must satisfy the sandbox write boundary.
    Returns the path that was written.
    """
    lines: list[str] = []
    lines.append(f"CMD {review.gate_id} Gate Review — {review.reviewed_at[:10]}")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Reviewer:   {review.reviewer}")
    lines.append(f"Decision:   {review.decision}")
    lines.append(f"Reviewed:   {review.reviewed_at}")
    lines.append("")
    lines.append(f"Rationale:")
    lines.append(f"  {review.rationale}")
    if review.missing_evidence:
        lines.append("")
        lines.append(f"Missing evidence:")
        lines.append(f"  {review.missing_evidence}")

    return write_text_artifact(output_path, lines, sandbox_root=sandbox_root)


# ── Internal helpers ────────────────────────────────────────────────────


def _read_comparison_csv(path: Path) -> dict[str, dict[str, float]]:
    """Read comparison_metrics.csv, return {system_name: {column: value}}."""
    if not path.exists():
        raise FileNotFoundError(f"Required artifact not found: {path}")
    rows: dict[str, dict[str, float]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row["system_name"]
            rows[name] = {
                k: float(v) for k, v in row.items() if k != "system_name"
            }
    return rows


def _read_confusion_csv(path: Path) -> dict[str, dict[str, int]]:
    """Read attribution_confusion_matrix.csv, return {gold_label: {pred_label: count}}."""
    if not path.exists():
        raise FileNotFoundError(f"Required artifact not found: {path}")
    matrix: dict[str, dict[str, int]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            gold = row["gold_label"]
            matrix[gold] = {k: int(v) for k, v in row.items() if k != "gold_label"}
    return matrix


def _read_repair_csv(path: Path) -> list[str]:
    """Read post_repair_table.csv, return list of repair_assessment values."""
    if not path.exists():
        raise FileNotFoundError(f"Required artifact not found: {path}")
    assessments: list[str] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            assessments.append(row["repair_assessment"])
    return assessments


def _check_macro_f1(comparison_path: Path) -> GateCriterion:
    description = "CMD macro F1 exceeds all comparator baselines"
    threshold = "CMD-Audit macro_f1 > evidence_recall AND subagent_judge AND random_label"

    try:
        data = _read_comparison_csv(comparison_path)
        cmd_macro_f1 = data["CMD-Audit"]["macro_f1"]
        baseline_f1s = {
            name: data[name]["macro_f1"]
            for name in ("evidence_recall", "subagent_judge", "random_label")
        }
        max_baseline = max(baseline_f1s.values())
        passed = cmd_macro_f1 > max_baseline

        evidence_parts = [f"CMD-Audit macro_f1={cmd_macro_f1:.3f}"]
        for name, val in baseline_f1s.items():
            evidence_parts.append(f"{name}={val:.3f}")
        evidence = "; ".join(evidence_parts)

        missing = ""
        if not passed:
            missing = (
                f"CMD-Audit macro_f1 ({cmd_macro_f1:.3f}) does not exceed "
                f"best baseline ({max_baseline:.3f})"
            )
    except (FileNotFoundError, KeyError) as exc:
        passed = False
        evidence = f"Could not evaluate: {exc}"
        missing = str(exc)

    return GateCriterion(
        criterion_id="macro_f1_exceeds_baselines",
        description=description,
        artifact_path=str(comparison_path),
        threshold=threshold,
        passed=passed,
        evidence=evidence,
        missing=missing,
    )


def _check_confusion_diagonal(confusion_path: Path) -> GateCriterion:
    description = "Confusion matrix diagonal dominance for all six V0 labels"
    threshold = "For each V0 label row: diagonal > sum of off-diagonal entries"

    try:
        data = _read_confusion_csv(confusion_path)
        v0_labels = list(V0_PIPELINE_LABEL_ORDER)
        violations: list[str] = []

        for label in v0_labels:
            if label not in data:
                violations.append(f"{label}: missing from confusion matrix")
                continue
            row = data[label]
            diagonal = row.get(label, 0)
            off_diagonal_sum = sum(v for k, v in row.items() if k != label)
            if diagonal <= off_diagonal_sum:
                violations.append(
                    f"{label}: diagonal={diagonal} <= off_diagonal_sum={off_diagonal_sum}"
                )

        passed = len(violations) == 0
        if passed:
            evidence = (
                f"All {len(v0_labels)} V0 labels have diagonal > off-diagonal sum"
            )
        else:
            evidence = "; ".join(violations)
        missing = "" if passed else evidence

    except (FileNotFoundError, KeyError) as exc:
        passed = False
        evidence = f"Could not evaluate: {exc}"
        missing = str(exc)

    return GateCriterion(
        criterion_id="confusion_diagonal_dominance",
        description=description,
        artifact_path=str(confusion_path),
        threshold=threshold,
        passed=passed,
        evidence=evidence,
        missing=missing,
    )


def _check_accuracy_top2(comparison_path: Path) -> GateCriterion:
    description = "CMD outperforms all baselines on attribution accuracy and top-2 accuracy"
    threshold = (
        "CMD-Audit attribution_accuracy > all baselines AND "
        "CMD-Audit top2_accuracy > all baselines"
    )

    try:
        data = _read_comparison_csv(comparison_path)
        cmd = data["CMD-Audit"]
        baselines = ("evidence_recall", "subagent_judge", "random_label")

        cmd_acc = cmd["attribution_accuracy"]
        cmd_top2 = cmd["top2_accuracy"]
        max_baseline_acc = max(data[name]["attribution_accuracy"] for name in baselines)
        max_baseline_top2 = max(data[name]["top2_accuracy"] for name in baselines)

        acc_ok = cmd_acc > max_baseline_acc
        top2_ok = cmd_top2 > max_baseline_top2
        passed = acc_ok and top2_ok

        evidence_parts = [
            f"CMD-Audit attribution_accuracy={cmd_acc:.3f} "
            f"(best baseline={max_baseline_acc:.3f})",
            f"CMD-Audit top2_accuracy={cmd_top2:.3f} "
            f"(best baseline={max_baseline_top2:.3f})",
        ]
        evidence = "; ".join(evidence_parts)

        missing_parts = []
        if not acc_ok:
            missing_parts.append(
                f"CMD attribution_accuracy ({cmd_acc:.3f}) <= "
                f"best baseline ({max_baseline_acc:.3f})"
            )
        if not top2_ok:
            missing_parts.append(
                f"CMD top2_accuracy ({cmd_top2:.3f}) <= "
                f"best baseline ({max_baseline_top2:.3f})"
            )
        missing = "; ".join(missing_parts)

    except (FileNotFoundError, KeyError) as exc:
        passed = False
        evidence = f"Could not evaluate: {exc}"
        missing = str(exc)

    return GateCriterion(
        criterion_id="accuracy_top2_exceeds_baselines",
        description=description,
        artifact_path=str(comparison_path),
        threshold=threshold,
        passed=passed,
        evidence=evidence,
        missing=missing,
    )


def _check_repair_distribution(repair_path: Path) -> GateCriterion:
    description = "Post-repair assessment distribution supports repair-validity claim"
    threshold = "recovered_rate >= 0.5 AND recovered + partial > failed"

    try:
        assessments = _read_repair_csv(repair_path)
        total = len(assessments)
        if total == 0:
            passed = False
            evidence = "No repair assessments found"
            missing = "Post-repair table is empty"
        else:
            recovered = sum(1 for a in assessments if a == "recovered")
            partial = sum(1 for a in assessments if a == "partial")
            failed = sum(1 for a in assessments if a == "failed")

            recovered_rate = recovered / total
            majority_improves = (recovered + partial) > failed
            passed = recovered_rate >= 0.5 and majority_improves

            evidence = (
                f"{total} cases: recovered={recovered}, partial={partial}, "
                f"failed={failed} (recovered_rate={recovered_rate:.3f})"
            )

            missing_parts = []
            if recovered_rate < 0.5:
                missing_parts.append(
                    f"recovered_rate={recovered_rate:.3f} < 0.5 threshold"
                )
            if not majority_improves:
                missing_parts.append(
                    f"recovered+partial ({recovered + partial}) <= "
                    f"failed ({failed})"
                )
            missing = "; ".join(missing_parts)

    except (FileNotFoundError, KeyError) as exc:
        passed = False
        evidence = f"Could not evaluate: {exc}"
        missing = str(exc)

    return GateCriterion(
        criterion_id="repair_assessment_distribution",
        description=description,
        artifact_path=str(repair_path),
        threshold=threshold,
        passed=passed,
        evidence=evidence,
        missing=missing,
    )
