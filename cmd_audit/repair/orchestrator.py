"""RepairOrchestrator — Issue 0020-A.

Iteration controller: walks close_deltas, calls RepairExecutor for each,
stops at first recovered or exhausts the list.
"""

from __future__ import annotations

from dataclasses import dataclass

from .failure_memory import (
    FailureMemoryStore,
    build_failure_memory_context,
)
from ..core.labels import LabelValidationError
from .post_repair import draft_ecs_for_label
from .executor import RepairExecutor, RepairExecutorResult


class AttributionFailed(Exception):
    """Sentinel: all replays and surrogate paths produced zero recovery gain.

    Records feedback for hook threshold recalibration. Does not store to
    Failure Memory. Adapter may log as observation.

    Decision 32, Point 12: 3-tier handling — (b) surrogate also zero.
    """

    def __init__(self, case_id: str, reason: str = ""):
        self.case_id = case_id
        self.reason = reason
        super().__init__(f"Attribution failed for {case_id}: {reason}")


@dataclass(frozen=True)
class RepairOrchestratorResult:
    """Result of iterative repair orchestration."""

    case_id: str
    final_assessment: str
    final_answer_score: float
    final_evidence_score: float
    attempts: tuple[RepairExecutorResult, ...]
    recovered: bool
    exhausted: bool
    labels_tried: tuple[str, ...]
    labels_skipped: tuple[tuple[str, str], ...] = ()
    skipped_reason: str = ""


class RepairOrchestrator:
    """Iterative repair controller.

    Walks attribution.close_deltas (labels with recovery gain > threshold),
    calls RepairExecutor for each, stops at first recovered or exhausts.
    """

    def __init__(
        self,
        executor: RepairExecutor | None = None,
        fm_store: FailureMemoryStore | None = None,
    ) -> None:
        self._executor = executor if executor is not None else RepairExecutor()
        self._fm_store = fm_store

    def run(
        self,
        *,
        attribution,
        case,
        adapter,
        audit_result=None,
        fm_context: str = "",
        close_deltas_threshold: float = 0.0,
    ) -> RepairOrchestratorResult:
        """Run iterative repair over close_deltas labels."""
        if not attribution.close_deltas:
            return RepairOrchestratorResult(
                case_id=case.case_id,
                final_assessment="skipped",
                final_answer_score=0.0,
                final_evidence_score=0.0,
                attempts=(),
                recovered=False,
                exhausted=False,
                labels_tried=(),
                skipped_reason="v0_attribution_no_close_deltas",
            )

        if not fm_context and self._fm_store is not None:
            records = self._fm_store.retrieve(
                query=case.query,
                label=attribution.predicted_label,
            )
            fm_context = build_failure_memory_context(records)

        labels_to_try: list[str] = []
        if attribution.predicted_label:
            labels_to_try.append(attribution.predicted_label)

        for label, delta in attribution.close_deltas:
            if label not in labels_to_try:
                if attribution.recovery_gain - delta > close_deltas_threshold:
                    labels_to_try.append(label)

        attempts: list[RepairExecutorResult] = []
        labels_tried: list[str] = []
        labels_skipped: list[tuple[str, str]] = []

        for label in labels_to_try:
            try:
                ecs_draft = draft_ecs_for_label(case, audit_result, label)
            except LabelValidationError as exc:
                labels_skipped.append((label, str(exc)))
                continue
            labels_tried.append(label)

            result = self._executor.run(
                ecs_draft=ecs_draft,
                adapter=adapter,
                case=case,
                fm_context=fm_context,
            )
            attempts.append(result)

            if result.assessment == "recovered":
                return RepairOrchestratorResult(
                    case_id=case.case_id,
                    final_assessment="recovered",
                    final_answer_score=result.post_repair_answer_score,
                    final_evidence_score=result.post_repair_evidence_score,
                    attempts=tuple(attempts),
                    recovered=True,
                    exhausted=False,
                    labels_tried=tuple(labels_tried),
                    labels_skipped=tuple(labels_skipped),
                )

        final = attempts[-1] if attempts else None
        return RepairOrchestratorResult(
            case_id=case.case_id,
            final_assessment=final.assessment if final else "failed",
            final_answer_score=final.post_repair_answer_score if final else 0.0,
            final_evidence_score=final.post_repair_evidence_score if final else 0.0,
            attempts=tuple(attempts),
            recovered=False,
            exhausted=True,
            labels_tried=tuple(labels_tried),
            labels_skipped=tuple(labels_skipped),
        )
