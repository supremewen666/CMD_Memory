"""Evidence-driven release gates."""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .provenance import detect_tamper
from .writers import write_text_artifact

_GATE_DECISION_VALUES = ("approved", "deferred", "rejected")

ATTRIBUTION_RELEASE_CRITERION_IDS = (
    "operator_recovery_gain_metrics",
    "repair_assessment_distribution",
    "step_level_attribution_metrics",
)

DECISION34_LEGACY_ARTIFACTS_DIR = Path("artifacts/legacy_phrase_match_2026_05_22")
STEP_LEVEL_METRIC_THRESHOLDS = {
    "step_attribution_coverage": 0.8,
    "identity_baseline_coverage": 1.0,
    "positive_credit_rate": 0.5,
}


# ── Data types ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GateCriterion:
    """Single criterion within a release gate check."""

    criterion_id: str
    description: str
    artifact_path: str
    threshold: str
    passed: bool
    evidence: str
    missing: str


@dataclass(frozen=True)
class GateResult:
    """Result of checking all criteria for a release gate."""

    gate_id: str
    criteria: tuple[GateCriterion, ...]
    all_passed: bool
    checked_at: str


@dataclass(frozen=True)
class GateReview:
    """HITL review decision for a release gate."""

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


# ── Attribution evidence gate check ─────────────────────────────────────


def check_attribution_release_gate(
    artifacts_dir: Path | None = None,
    sandbox_dir: Path | None = None,
) -> GateResult:
    """Check the attribution and repair evidence release criteria.

    Returns a GateResult with pass/fail per criterion. The final decision is HITL.
    """
    if artifacts_dir is None and sandbox_dir is None:
        artifacts_dir, sandbox_dir = _default_attribution_artifact_dirs()
    else:
        if artifacts_dir is None:
            artifacts_dir = Path("artifacts")
        if sandbox_dir is None:
            sandbox_dir = Path("artifacts/sandbox")

    criteria: list[GateCriterion] = []

    # Criterion 1: Operator execution produces positive recovery gain.
    criteria.append(
        _check_operator_recovery_metrics(artifacts_dir / "comparison_metrics.csv")
    )

    # Criterion 2: Repair assessment distribution
    criteria.append(_check_repair_distribution(sandbox_dir / "post_repair_table.csv"))

    # Criterion 3: Step-level attribution metrics
    criteria.append(_check_step_level_metrics(artifacts_dir / "step_level_metrics.csv"))

    all_passed = all(c.passed for c in criteria)
    checked_at = datetime.now(timezone.utc).isoformat()

    return GateResult(
        gate_id="attribution_evidence",
        criteria=tuple(criteria),
        all_passed=all_passed,
        checked_at=checked_at,
    )


def _default_attribution_artifact_dirs() -> tuple[Path, Path]:
    current = Path("artifacts")
    current_sandbox = current / "sandbox"
    if (current / "comparison_metrics.csv").exists():
        return current, current_sandbox

    legacy = DECISION34_LEGACY_ARTIFACTS_DIR
    if (legacy / "comparison_metrics.csv").exists():
        return legacy, legacy / "sandbox"

    return current, current_sandbox


# ── Runtime integration gate check ──────────────────────────────────────


def check_runtime_integration_gate(
    *,
    mem0_integrated: bool = False,
    letta_integrated: bool = False,
    audit_results: tuple = (),
) -> GateResult:
    """Check runtime integration evidence for distinct memory agents.

    Set *mem0_integrated* to ``True`` after Issue 0014 is complete.
    Set *letta_integrated* to ``True`` after Issue 0015 is complete.
    The gate requires two integrations (mem0 + Letta).
    """
    checked_at = datetime.now(timezone.utc).isoformat()
    adapter_count = (1 if mem0_integrated else 0) + (1 if letta_integrated else 0)
    passed = adapter_count >= 2
    integrations = []
    if mem0_integrated:
        integrations.append("mem0 (Issue 0014)")
    if letta_integrated:
        integrations.append("Letta (Issue 0015)")

    if adapter_count == 2:
        evidence = f"{adapter_count} adapter integration(s): {', '.join(integrations)}."
        missing = ""
    elif adapter_count == 1:
        evidence = (
            f"{adapter_count} adapter integration(s): {integrations[0]}. "
            "Second adapter required for gate."
        )
        missing = "Integrate second adapter target (Letta if mem0 done)."
    else:
        evidence = "0 adapter integrations; current harness operates standalone."
        missing = (
            "No Adapter Interface integrations exist. The runtime must integrate "
            "at least two distinct memory agents before release review."
        )
    adapter_criterion = GateCriterion(
        criterion_id="adapter_integration_count",
        description=(
            "At least two distinct memory agents integrated through "
            "the Adapter Interface without repair efficacy regression"
        ),
        artifact_path="cmd_audit/adapters/",
        threshold="adapter_count >= 2 AND no repair efficacy regression",
        passed=passed,
        evidence=evidence,
        missing=missing,
    )
    tamper_criterion = _check_provenance_tamper(audit_results)
    criteria = (adapter_criterion, tamper_criterion)
    return GateResult(
        gate_id="runtime_integration",
        criteria=criteria,
        all_passed=all(criterion.passed for criterion in criteria),
        checked_at=checked_at,
    )


def _check_provenance_tamper(audit_results: tuple) -> GateCriterion:
    checked = 0
    tampered: list[str] = []
    missing_source_text: list[str] = []

    for result in audit_results:
        attribution = getattr(result, "attribution", None)
        if attribution is None:
            continue
        session_key = hashlib.sha256(result.case_id.encode()).hexdigest()
        for edge in getattr(attribution, "distractor_provenance_edges", ()):
            source_text = getattr(edge, "source_text", "")
            edge_id = f"{result.case_id}:{edge.source_id}->{edge.target_id}"
            if not source_text:
                missing_source_text.append(edge_id)
                continue
            checked += 1
            if detect_tamper(edge, source_text, session_key):
                tampered.append(edge_id)

    passed = not tampered and not missing_source_text
    if not audit_results:
        passed = True
        evidence = "No audit results supplied; no distractor provenance edges to check."
        missing = ""
    elif tampered:
        evidence = f"Tamper detected on {len(tampered)} provenance edge(s)."
        missing = "; ".join(tampered)
    elif missing_source_text:
        evidence = (
            f"{len(missing_source_text)} provenance edge(s) lacked source_text for HMAC check."
        )
        missing = "; ".join(missing_source_text)
    else:
        evidence = f"{checked} distractor provenance edge(s) passed HMAC tamper checks."
        missing = ""

    return GateCriterion(
        criterion_id="provenance_hmac_tamper_free",
        description="Distractor provenance edges must pass HMAC tamper detection",
        artifact_path="AuditResult.attribution.distractor_provenance_edges",
        threshold="detect_tamper(edge, edge.source_text, session_key) is False for every edge",
        passed=passed,
        evidence=evidence,
        missing=missing,
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
    lines.append("Rationale:")
    lines.append(f"  {review.rationale}")
    if review.missing_evidence:
        lines.append("")
        lines.append("Missing evidence:")
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
            rows[name] = {k: float(v) for k, v in row.items() if k != "system_name"}
    return rows


def _read_repair_csv(path: Path) -> list[str]:
    """Read post_repair_table.csv, return post-repair assessment values."""
    if not path.exists():
        raise FileNotFoundError(f"Required artifact not found: {path}")
    assessments: list[str] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assessment_field = "repair_assessment"
        if reader.fieldnames and assessment_field not in reader.fieldnames:
            assessment_field = "targeted_assessment"
        for row in reader:
            assessments.append(row[assessment_field])
    return assessments


def _read_step_level_metrics_csv(path: Path) -> dict[str, float]:
    """Read step-level attribution metrics from a narrow or single-row wide CSV."""
    if not path.exists():
        raise FileNotFoundError(f"Required artifact not found: {path}")
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return {}
        rows = list(reader)
    if not rows:
        return {}

    fieldnames = set(reader.fieldnames or ())
    if {"metric_name", "value"}.issubset(fieldnames):
        return {
            row["metric_name"]: float(row["value"])
            for row in rows
            if row.get("metric_name") and row.get("value") not in (None, "")
        }

    first = rows[0]
    return {
        key: float(value)
        for key, value in first.items()
        if value not in (None, "") and key != "case_id"
    }


def _check_operator_recovery_metrics(comparison_path: Path) -> GateCriterion:
    description = "Executed repair operators produce positive recovery gain"
    threshold = (
        "triggered_cases > 0 AND positive_recovery_rate > 0 "
        "AND mean_recovery_gain > 0"
    )

    try:
        data = _read_comparison_csv(comparison_path)
        cmd = data["CMD-Audit"]
        triggered_cases = int(cmd["triggered_cases"])
        positive_rate = cmd["positive_recovery_rate"]
        mean_gain = cmd["mean_recovery_gain"]
        passed = triggered_cases > 0 and positive_rate > 0.0 and mean_gain > 0.0
        evidence = (
            f"triggered_cases={triggered_cases}; "
            f"positive_recovery_rate={positive_rate:.3f}; "
            f"mean_recovery_gain={mean_gain:.3f}"
        )
        missing_parts: list[str] = []
        if triggered_cases <= 0:
            missing_parts.append("no CMD-triggered operator executions")
        if positive_rate <= 0.0:
            missing_parts.append("positive_recovery_rate <= 0")
        if mean_gain <= 0.0:
            missing_parts.append("mean_recovery_gain <= 0")
        missing = "; ".join(missing_parts)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        passed = False
        evidence = f"Could not evaluate: {exc}"
        missing = str(exc)

    return GateCriterion(
        criterion_id="operator_recovery_gain_metrics",
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
                    f"recovered+partial ({recovered + partial}) <= failed ({failed})"
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


def _check_step_level_metrics(metrics_path: Path) -> GateCriterion:
    description = "Step-level attribution metrics support generation-point diagnosis"
    threshold = " AND ".join(
        f"{metric} >= {required:g}"
        for metric, required in STEP_LEVEL_METRIC_THRESHOLDS.items()
    )

    try:
        metrics = _read_step_level_metrics_csv(metrics_path)
        missing_metrics = tuple(
            metric
            for metric in STEP_LEVEL_METRIC_THRESHOLDS
            if metric not in metrics
        )
        violations = tuple(
            f"{metric}={metrics[metric]:.3f} < {required:.3f}"
            for metric, required in STEP_LEVEL_METRIC_THRESHOLDS.items()
            if metric in metrics and metrics[metric] < required
        )
        passed = not missing_metrics and not violations
        evidence = "; ".join(
            f"{metric}={metrics[metric]:.3f}"
            for metric in STEP_LEVEL_METRIC_THRESHOLDS
            if metric in metrics
        )
        if not evidence:
            evidence = "No step-level metrics found"
        missing_parts = []
        if missing_metrics:
            missing_parts.append(
                "missing metric(s): " + ", ".join(missing_metrics)
            )
        missing_parts.extend(violations)
        missing = "; ".join(missing_parts)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        passed = False
        evidence = f"Could not evaluate: {exc}"
        missing = str(exc)

    return GateCriterion(
        criterion_id="step_level_attribution_metrics",
        description=description,
        artifact_path=str(metrics_path),
        threshold=threshold,
        passed=passed,
        evidence=evidence,
        missing=missing,
    )
