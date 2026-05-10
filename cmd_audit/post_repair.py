"""Post-Repair Context Replay — Issue 0005 (Cycles 5, 12, 13, 15)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .labels import OUT_OF_SCOPE_ITEM_LABELS, validate_v0_label
from .models import ProbeCase
from .scoring import answer_score, evidence_recall_from_text

REPAIR_ASSESSMENT_VALUES = ("recovered", "partial", "failed")

# Natural-language phrases that re-declare forbidden item labels.
_FORBIDDEN_NL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bitem[_\s]?(is\s+)?wrong\b",
        r"\bitem[_\s]?(is\s+)?stale\b",
        r"\bitem[_\s]?(is\s+)?conflict(ed|ing)?\b",
        r"\bitem[_\s]?(is\s+)?poisoned\b",
        r"\bcompression[_\s]?distorted\b",
    )
)


class ECSCauseValidationError(ValueError):
    """Raised when ECS cause contains forbidden item label names or equivalents."""


def _validate_ecs_cause(cause: str) -> str:
    """Reject ECS cause text that uses forbidden item label names or NL equivalents."""
    lowered = cause.casefold()
    for label in OUT_OF_SCOPE_ITEM_LABELS:
        if label in lowered:
            raise ECSCauseValidationError(
                f"ECS cause must not use forbidden item label {label!r}; "
                f"describe item state instead (e.g., 'stored preference was outdated')"
            )
    for pattern in _FORBIDDEN_NL_PATTERNS:
        if pattern.search(lowered):
            raise ECSCauseValidationError(
                f"ECS cause contains natural-language equivalent of a forbidden "
                f"item label; use descriptive state language instead"
            )
    return cause


def classify_repair_assessment(answer_score: float, evidence_score: float) -> str:
    """Classify Post-Repair Context Replay outcome into three-value assessment.

    - ``recovered``: answer fully matches (answer_score == 1.0).
    - ``partial``: evidence recovered but answer still wrong.
    - ``failed``: neither evidence nor answer recovered.
    """
    if answer_score == 1.0:
        return "recovered"
    if evidence_score == 1.0:
        return "partial"
    return "failed"


# ── Data types ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ECSDraft:
    """Error-Cause-Solution record drafted from attribution."""

    case_id: str
    predicted_label: str
    cause: str
    corrected_memory: str
    repair_guidance: str
    repaired_evidence_block: str

    def __post_init__(self) -> None:
        validate_v0_label(self.predicted_label)
        _validate_ecs_cause(self.cause)


@dataclass(frozen=True)
class RepairedContext:
    """Context rebuilt from ECS for Post-Repair Context Replay."""

    case_id: str
    corrected_memory: str
    repair_guidance: str
    repaired_evidence_block: str
    original_query: str


@dataclass(frozen=True)
class PostRepairResult:
    """Result of a single Post-Repair Context Replay run."""

    case_id: str
    repair_assessment: str
    post_repair_answer_score: float
    post_repair_evidence_score: float
    token_cost: float
    regression_risk: float
    had_repair_regression: bool


# ── Pipeline steps ────────────────────────────────────────────────────


def draft_ecs(case: ProbeCase, audit_result) -> ECSDraft:
    """Draft an ECS record from CMD-Audit attribution results.

    This is a V0 rule-based draft: the predicted label and top replay drive
    cause text, corrected memory, and repair guidance selection.
    """
    attribution = audit_result.attribution
    replay = audit_result.replay

    cause, corrected_memory, repair_guidance = _ecs_for_label(
        case, attribution.predicted_label, replay
    )

    return ECSDraft(
        case_id=case.case_id,
        predicted_label=attribution.predicted_label,
        cause=cause,
        corrected_memory=corrected_memory,
        repair_guidance=repair_guidance,
        repaired_evidence_block=replay.evidence_block,
    )


def build_repaired_context(case: ProbeCase, ecs_draft: ECSDraft) -> RepairedContext:
    """Build a repaired context from ECS draft for Post-Repair Context Replay."""
    return RepairedContext(
        case_id=case.case_id,
        corrected_memory=ecs_draft.corrected_memory,
        repair_guidance=ecs_draft.repair_guidance,
        repaired_evidence_block=ecs_draft.repaired_evidence_block,
        original_query=case.query,
    )


def run_post_repair_context_replay(
    case: ProbeCase, repaired_context: RepairedContext
) -> PostRepairResult:
    """Rerun the original failed query with repaired context.

    Does NOT inject the gold answer. Evidence and answer are scored from the
    repaired context content alone.
    """
    combined = _combine_context(repaired_context)
    evidence_score = evidence_recall_from_text(case.gold_evidence, combined)
    gold_in_context = case.gold_answer.casefold() in combined.casefold()
    post_answer_score = 1.0 if gold_in_context else 0.0
    assessment = classify_repair_assessment(post_answer_score, evidence_score)

    token_cost = _estimate_token_cost(combined, repaired_context.original_query)
    regression_risk = _estimate_regression_risk(case, repaired_context)

    return PostRepairResult(
        case_id=case.case_id,
        repair_assessment=assessment,
        post_repair_answer_score=post_answer_score,
        post_repair_evidence_score=evidence_score,
        token_cost=token_cost,
        regression_risk=regression_risk,
        had_repair_regression=regression_risk > 0.5,
    )


def run_hard_case_update_baseline(case: ProbeCase) -> PostRepairResult:
    """Run a generic hard-case update: inject all extracted memory as context.

    This is a comparison baseline, not CMD-guided repair. It measures whether
    simply adding more retrieved context (without CMD attribution) suffices.
    """
    all_memory = "\n".join(item.text for item in case.extracted_memory)
    ctx = RepairedContext(
        case_id=case.case_id,
        corrected_memory=all_memory,
        repair_guidance="Hard-case update: all extracted memory injected as context.",
        repaired_evidence_block=all_memory,
        original_query=case.query,
    )
    return run_post_repair_context_replay(case, ctx)


# ── Sandbox write boundary (Cycle 15) ─────────────────────────────────


def validate_sandbox_path(output_path: str | Path, sandbox_root: str | Path | None = None) -> Path:
    """Reject writes to paths outside the replay-local sandbox.

    Default sandbox is ``artifacts/sandbox/``.
    """
    sandbox = Path(sandbox_root if sandbox_root is not None else "artifacts/sandbox")
    target = Path(output_path).resolve()
    sandbox_resolved = sandbox.resolve()

    try:
        target.relative_to(sandbox_resolved)
    except ValueError:
        raise ValueError(
            f"CMD-Audit write rejected: {target} is outside the replay-local "
            f"sandbox {sandbox_resolved}. Only sandbox writes are permitted."
        )
    return target


# ── ECS rule helpers ──────────────────────────────────────────────────


def _ecs_for_label(case, predicted_label: str, replay) -> tuple[str, str, str]:
    """Return (cause, corrected_memory, repair_guidance) for a predicted label."""
    from .repairs import get_targeted_repair_action  # lazy import to avoid circular dependency

    action = get_targeted_repair_action(predicted_label)
    return (action.cause, replay.evidence_block, action.repair_guidance)


def _combine_context(ctx: RepairedContext) -> str:
    return "\n".join(
        (
            ctx.corrected_memory,
            ctx.repair_guidance,
            ctx.repaired_evidence_block,
        )
    )


def _estimate_token_cost(context_text: str, query: str) -> float:
    """Simple character-based token cost estimator (~4 chars per token)."""
    return (len(context_text) + len(query)) / 4.0


def _estimate_regression_risk(case, ctx: RepairedContext) -> float:
    """Estimate regression risk by checking if original baseline evidence is still present."""
    baseline = case.primary_baseline
    original_context = baseline.injected_context
    combined = _combine_context(ctx)
    if not original_context:
        return 0.0
    original_terms = set(original_context.casefold().split())
    repaired_terms = set(combined.casefold().split())
    if not original_terms:
        return 0.0
    overlap = len(original_terms & repaired_terms) / len(original_terms)
    return max(0.0, min(1.0, 1.0 - overlap))
