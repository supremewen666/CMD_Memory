"""Comparator baselines for CMD-Audit.

Non-CMD comparison systems over the failed baseline trace:
  - evidence_recall_heuristic: deterministic observational diagnosis
  - subagent_judge: post-hoc trace analysis (wraps evidence_recall)
  - random_label: statistical sanity check
  - llm_judge: LLM-as-Judge baseline (Issue 0019 Phase A)
  - subagent_judge_monitor: leak-safe replay trigger

These are intentionally separate from CMD replay attribution. They are
comparison systems, not the source of Operation-Level Attribution.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any
import logging

from cmd_audit.labels import (
    V0_PIPELINE_LABEL_ORDER,
    V1_PIPELINE_LABEL_ORDER,
    validate_monitor_anomaly_reason,
    validate_v1_label,
)
from cmd_audit.models import BaselineOutput, ProbeCase
from cmd_audit.scoring import evidence_recall_from_memory_ids, evidence_recall_from_text


REQUIRED_MEMORY_BASELINES = ("fixed_summary", "vector_memory")
FORBIDDEN_MONITOR_FIELDS = frozenset(
    {
        "label",
        "labels",
        "final_label",
        "predicted_label",
        "diagnosis_label",
        "cmd_label",
        "attribution",
        "attribution_label",
        "replay_label",
        "top2_labels",
        "ecs",
        "error_cause_solution",
        "repair_guidance",
        "corrected_memory",
        "memory_write",
        "memory_writes",
        "gold_answer",
        "gold_evidence",
        "raw_events",
        "extracted_memory",
        "baseline_outputs",
        "full_trace",
        "full_failed_trace",
        "failed_trace",
    }
)


class BaselineConfigurationError(ValueError):
    """Raised when a probe case lacks required baseline outputs."""


class LeakSafeMonitorError(ValueError):
    """Raised when the monitor attempts to expose forbidden diagnosis payloads."""


# ---------------------------------------------------------------------------
# Opaque-id & leak-safe validation
# ---------------------------------------------------------------------------


def _is_opaque_id(value: str) -> bool:
    """An opaque ID is a short, whitespace-free token with no content-bearing separators."""
    return (
        bool(value)
        and " " not in value
        and ":" not in value
        and "\n" not in value
        and len(value) <= 128
    )


def validate_evidence_pointers(pointers: tuple[str, ...]) -> tuple[str, ...]:
    """Reject evidence pointers that are not opaque IDs."""
    for ptr in pointers:
        if not _is_opaque_id(ptr):
            raise LeakSafeMonitorError(
                f"evidence pointer must be an opaque ID, got {ptr!r}"
            )
    return pointers


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryBaselineRun:
    baseline_name: str
    answer: str
    retrieved_memory_ids: tuple[str, ...]
    answer_score: float
    evidence_score: float
    injected_context: str

    @classmethod
    def from_output(cls, output: BaselineOutput) -> "MemoryBaselineRun":
        return cls(
            baseline_name=output.baseline_name,
            answer=output.answer,
            retrieved_memory_ids=output.retrieved_memory_ids,
            answer_score=output.answer_score,
            evidence_score=output.evidence_score,
            injected_context=output.injected_context,
        )

    @property
    def failed(self) -> bool:
        return self.answer_score < 1.0


@dataclass(frozen=True)
class ComparatorResult:
    comparator_name: str
    predicted_label: str
    top2_labels: tuple[str, ...]
    explanation: str
    cost_per_diagnosis: float
    uses_counterfactual_replay: bool = False

    def __post_init__(self) -> None:
        validate_v1_label(self.predicted_label)
        for label in self.top2_labels:
            validate_v1_label(label)


@dataclass(frozen=True)
class SubagentJudgeMonitorDecision:
    should_trigger_replay: bool
    risk_score: float
    anomaly_reason: str
    evidence_pointers: tuple[str, ...]
    trace_summary: str
    cost_per_decision: float = 0.2

    def __post_init__(self) -> None:
        validate_monitor_anomaly_reason(self.anomaly_reason)
        validate_evidence_pointers(self.evidence_pointers)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "should_trigger_replay": self.should_trigger_replay,
            "risk_score": self.risk_score,
            "anomaly_reason": self.anomaly_reason,
            "evidence_pointers": list(self.evidence_pointers),
            "trace_summary": self.trace_summary,
            "cost_per_decision": self.cost_per_decision,
        }
        validate_monitor_payload(payload)
        return payload


@dataclass(frozen=True)
class BaselineSuiteResult:
    case_id: str
    memory_baselines: tuple[MemoryBaselineRun, ...]
    evidence_recall_heuristic: ComparatorResult
    subagent_judge: ComparatorResult
    random_label: ComparatorResult
    llm_judge: ComparatorResult
    monitor: SubagentJudgeMonitorDecision

    @property
    def comparator_results(self) -> tuple[ComparatorResult, ...]:
        return (
            self.evidence_recall_heuristic,
            self.subagent_judge,
            self.random_label,
            self.llm_judge,
        )


# ---------------------------------------------------------------------------
# LLM-as-Judge prompt building & parsing (Issue 0019 Phase A)
# ---------------------------------------------------------------------------

_LABEL_DESCRIPTIONS: dict[str, str] = {
    "write_error": "The agent failed to write correct memory items from raw events — "
    "evidence never entered the memory store.",
    "compression_error": "The agent summarized or compressed memory and lost key evidence "
    "that was present in the original memory items.",
    "premature_extraction_error": "The agent extracted memory too early, before all "
    "relevant raw events had been processed — evidence existed in raw events but was "
    "never moved into extracted memory.",
    "retrieval_error": "The correct evidence exists in the memory store, but the agent "
    "failed to retrieve it when answering the query.",
    "injection_error": "The correct evidence was retrieved but not properly injected "
    "into the agent's reasoning context.",
    "reasoning_error": "The correct evidence was present in the agent's context, but "
    "the agent still produced a wrong answer — a reasoning failure.",
    "ingestion_error": "The upstream ingestion path failed to pass required evidence "
    "into the memory pipeline.",
    "route_error": "The evidence exists, but it was stored or routed through a tier "
    "the baseline retrieval path did not query.",
    "granularity_error": "The memory was represented at a granularity that dropped "
    "or obscured required evidence.",
    "graph_error": "Graph expansion introduced distractors that masked directly "
    "matched evidence.",
    "safety_error": "A safety filter blocked valid evidence required for the answer.",
}

_LABEL_LIST = "\n".join(
    f"  - {label}: {_LABEL_DESCRIPTIONS[label]}" for label in V1_PIPELINE_LABEL_ORDER
)

_PROMPT_TEMPLATE = """\
You are an expert memory-system diagnostician. Your task is to analyze a failed
agent run and determine which stage of the memory pipeline most likely caused
the failure.

## Query
{query}

## Raw Events
{raw_events}

## Extracted Memory
{extracted_memory}

## Baseline Agent Output
Answer: {baseline_answer}
Answer Score: {baseline_answer_score}
Evidence Score: {baseline_evidence_score}
Retrieved Memory Items: {retrieved_count}

## Memory Pipeline Failure Labels

{label_list}

## Instructions

Based ONLY on the observable artifacts above, determine which memory pipeline
stage most likely failed. Consider:
1. Did the raw events contain the necessary information?
2. Was that information preserved in extracted memory?
3. Was it retrieved?
4. Was it in context?
5. Did the agent still answer wrong despite having the evidence?

Output your diagnosis in this exact format:
LABEL: <exactly one label from the list above>
EXPLANATION: <1-2 sentence explanation citing specific observations from the trace>"""


class LLMJudgeOutputError(ValueError):
    """Raised when the LLM response cannot be parsed into a valid diagnosis."""


def build_judge_prompt(case: ProbeCase) -> str:
    """Build an LLM-as-Judge prompt from observable post-hoc trace artifacts only.

    The prompt includes: query, raw events, extracted memory, baseline answer
    and scores. It must NOT include gold_label, ptype, gold_evidence, or gold_answer.
    """
    baseline = case.primary_baseline

    raw_events_text = "\n".join(
        f"- [{e.event_id}] {e.text}" for e in case.raw_events
    )
    extracted_memory_text = "\n".join(
        f"- [{m.memory_id}] {m.text}" for m in case.extracted_memory
    )

    return _PROMPT_TEMPLATE.format(
        query=case.query,
        raw_events=raw_events_text,
        extracted_memory=extracted_memory_text,
        baseline_answer=baseline.answer,
        baseline_answer_score=f"{baseline.answer_score:.2f}",
        baseline_evidence_score=f"{baseline.evidence_score:.2f}",
        retrieved_count=str(len(baseline.retrieved_memory_ids)),
        label_list=_LABEL_LIST,
    )


def parse_label_from_response(response: str) -> tuple[str, str]:
    """Extract ``(predicted_label, explanation)`` from an LLM response.

    Expects a response with ``LABEL: <label>`` and ``EXPLANATION: <text>`` lines.
    Raises :exc:`LLMJudgeOutputError` if parsing fails or the label is invalid.
    """
    lines = response.strip().splitlines()
    label_line: str | None = None
    explanation_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.upper().startswith("LABEL:"):
            label_line = stripped
        elif stripped.upper().startswith("EXPLANATION:"):
            explanation_lines.append(stripped.split(":", 1)[1].strip())
        elif explanation_lines:
            explanation_lines.append(stripped)

    if label_line is None:
        raise LLMJudgeOutputError(
            f"LLM response missing LABEL: line. Response: {response!r}"
        )

    raw_label = label_line.split(":", 1)[1].strip()

    try:
        predicted_label = validate_v1_label(raw_label)
    except Exception as exc:
        raise LLMJudgeOutputError(
            f"Invalid label {raw_label!r} in LLM response. Response: {response!r}"
        ) from exc

    explanation = " ".join(explanation_lines).strip()
    if not explanation:
        explanation = f"LLM judge predicted {predicted_label} (no explanation provided)"

    return predicted_label, explanation


# ---------------------------------------------------------------------------
# Comparator runners
# ---------------------------------------------------------------------------


def run_baseline_suite(
    case: ProbeCase,
    *,
    llm_client: Any = None,
) -> BaselineSuiteResult:
    """Run non-CMD comparator and monitor layer.

    These outputs are intentionally separate from CMD replay attribution. They
    are comparison systems over the failed baseline trace, not the source of the
    final Operation-Level Attribution.

    Args:
        case: The probe case to run baselines for.
        llm_client: Optional LLMClient instance. When None (default),
            ``run_llm_judge_baseline`` falls back to the evidence_recall_heuristic
            result with ``comparator_name="llm_judge"``.
    """

    memory_baselines = run_memory_baselines(case)
    comparison_baseline = _select_comparison_baseline(case)
    return BaselineSuiteResult(
        case_id=case.case_id,
        memory_baselines=memory_baselines,
        evidence_recall_heuristic=run_evidence_recall_heuristic(
            case, comparison_baseline
        ),
        subagent_judge=run_subagent_judge_baseline(case, comparison_baseline),
        random_label=run_random_label_baseline(case),
        llm_judge=run_llm_judge_baseline(
            case, comparison_baseline, llm_client=llm_client
        ),
        monitor=run_subagent_judge_monitor(case, comparison_baseline),
    )


def run_memory_baselines(
    case: ProbeCase,
    required_baselines: tuple[str, ...] = REQUIRED_MEMORY_BASELINES,
) -> tuple[MemoryBaselineRun, ...]:
    by_name: dict[str, BaselineOutput] = {}
    for output in case.baseline_outputs:
        if output.baseline_name in by_name:
            raise BaselineConfigurationError(
                f"{case.case_id}: duplicate baseline {output.baseline_name!r}"
            )
        by_name[output.baseline_name] = output

    missing = [name for name in required_baselines if name not in by_name]
    if missing:
        raise BaselineConfigurationError(
            f"{case.case_id}: missing required baseline output(s): {', '.join(missing)}"
        )

    ordered = [by_name[name] for name in required_baselines]
    ordered.extend(
        output
        for output in case.baseline_outputs
        if output.baseline_name not in set(required_baselines)
    )
    return tuple(MemoryBaselineRun.from_output(output) for output in ordered)


def run_evidence_recall_heuristic(
    case: ProbeCase,
    baseline: BaselineOutput | None = None,
) -> ComparatorResult:
    baseline = baseline or _select_comparison_baseline(case)
    predicted_label, rationale = _observational_label(case, baseline)
    return ComparatorResult(
        comparator_name="evidence_recall",
        predicted_label=predicted_label,
        top2_labels=(predicted_label,),
        explanation=rationale,
        cost_per_diagnosis=0.05,
        uses_counterfactual_replay=False,
    )


def run_subagent_judge_baseline(
    case: ProbeCase,
    baseline: BaselineOutput | None = None,
) -> ComparatorResult:
    baseline = baseline or _select_comparison_baseline(case)
    heuristic = run_evidence_recall_heuristic(case, baseline)
    explanation = (
        "post-hoc observational explanation over failed trace: "
        f"{baseline.baseline_name} reached answer_score={baseline.answer_score:.3f} "
        f"and evidence_score={baseline.evidence_score:.3f} after retrieving "
        f"{len(baseline.retrieved_memory_ids)} memory item(s). "
        f"The judge proposes {heuristic.predicted_label} as a comparator label, "
        "without running counterfactual replay."
    )
    return ComparatorResult(
        comparator_name="subagent_judge",
        predicted_label=heuristic.predicted_label,
        top2_labels=heuristic.top2_labels,
        explanation=explanation,
        cost_per_diagnosis=1.0,
        uses_counterfactual_replay=False,
    )


def run_random_label_baseline(case: ProbeCase) -> ComparatorResult:
    digest = hashlib.sha256(case.case_id.encode("utf-8")).digest()
    first_index = digest[0] % len(V0_PIPELINE_LABEL_ORDER)
    second_index = (
        first_index + 1 + digest[1] % (len(V0_PIPELINE_LABEL_ORDER) - 1)
    ) % len(V0_PIPELINE_LABEL_ORDER)
    labels = (
        V0_PIPELINE_LABEL_ORDER[first_index],
        V0_PIPELINE_LABEL_ORDER[second_index],
    )
    return ComparatorResult(
        comparator_name="random_label",
        predicted_label=labels[0],
        top2_labels=labels,
        explanation="deterministic random V0 label baseline for attribution sanity checks",
        cost_per_diagnosis=0.01,
        uses_counterfactual_replay=False,
    )


_logger = logging.getLogger(__name__)


def run_llm_judge_baseline(
    case: ProbeCase,
    baseline: BaselineOutput | None = None,
    *,
    llm_client: Any = None,
) -> ComparatorResult:
    """Run LLM-as-Judge baseline (Issue 0019 Phase A).

    When ``llm_client`` is None (default), falls back to the
    ``evidence_recall_heuristic`` result with ``comparator_name="llm_judge"``
    so the comparator still appears in metrics tables for structure validation.
    """
    baseline = baseline or _select_comparison_baseline(case)

    if llm_client is None:
        heuristic = run_evidence_recall_heuristic(case, baseline)
        return ComparatorResult(
            comparator_name="llm_judge",
            predicted_label=heuristic.predicted_label,
            top2_labels=heuristic.top2_labels,
            explanation="LLM judge unavailable — falling back to evidence_recall heuristic",
            cost_per_diagnosis=0.5,
            uses_counterfactual_replay=False,
        )

    try:
        prompt = build_judge_prompt(case)
        response = llm_client.generate(prompt)
        predicted_label, explanation = parse_label_from_response(response)
    except LLMJudgeOutputError as exc:
        _logger.warning("LLM judge output parse failed for %s: %s", case.case_id, exc)
        heuristic = run_evidence_recall_heuristic(case, baseline)
        return ComparatorResult(
            comparator_name="llm_judge",
            predicted_label=heuristic.predicted_label,
            top2_labels=heuristic.top2_labels,
            explanation=f"LLM judge parse error — fallback: {explanation}",
            cost_per_diagnosis=0.5,
            uses_counterfactual_replay=False,
        )
    except Exception as exc:
        _logger.warning("LLM judge failed for %s: %s", case.case_id, exc)
        heuristic = run_evidence_recall_heuristic(case, baseline)
        return ComparatorResult(
            comparator_name="llm_judge",
            predicted_label=heuristic.predicted_label,
            top2_labels=heuristic.top2_labels,
            explanation=f"LLM judge error: {exc}",
            cost_per_diagnosis=0.5,
            uses_counterfactual_replay=False,
        )

    return ComparatorResult(
        comparator_name="llm_judge",
        predicted_label=predicted_label,
        top2_labels=(predicted_label,),
        explanation=explanation,
        cost_per_diagnosis=0.5,
        uses_counterfactual_replay=False,
    )


def run_subagent_judge_monitor(
    case: ProbeCase,
    baseline: BaselineOutput | None = None,
    *,
    trigger_threshold: float = 0.5,
) -> SubagentJudgeMonitorDecision:
    baseline = baseline or _select_comparison_baseline(case)
    risk_score = 0.0
    if baseline.answer_score < 1.0:
        risk_score += 0.5
    if baseline.evidence_score < 1.0:
        risk_score += 0.4
    if not baseline.retrieved_memory_ids:
        risk_score += 0.1

    risk_score = min(risk_score, 1.0)

    if not baseline.retrieved_memory_ids:
        anomaly_reason = "retrieved_context_incomplete"
    elif baseline.evidence_score < 1.0:
        anomaly_reason = "evidence_recall_low"
    elif baseline.answer_score < 1.0:
        anomaly_reason = "answer_vs_evidence_mismatch"
    else:
        anomaly_reason = "confidence_anomaly"

    evidence_pointers = tuple(baseline.retrieved_memory_ids)

    decision = SubagentJudgeMonitorDecision(
        should_trigger_replay=risk_score >= trigger_threshold,
        risk_score=risk_score,
        anomaly_reason=anomaly_reason,
        evidence_pointers=evidence_pointers,
        trace_summary=(
            f"{baseline.baseline_name}: answer_score={baseline.answer_score:.3f}; "
            f"evidence_score={baseline.evidence_score:.3f}; "
            f"retrieved_count={len(baseline.retrieved_memory_ids)}"
        ),
    )
    decision.to_payload()
    return decision


def validate_monitor_payload(payload: dict[str, Any]) -> dict[str, Any]:
    _reject_forbidden_monitor_fields(payload)
    return payload


def _reject_forbidden_monitor_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in FORBIDDEN_MONITOR_FIELDS:
                raise LeakSafeMonitorError(
                    f"Subagent Judge Monitor payload cannot contain {key!r}"
                )
            _reject_forbidden_monitor_fields(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_forbidden_monitor_fields(nested)


def _select_comparison_baseline(case: ProbeCase) -> BaselineOutput:
    return case.primary_baseline


def _observational_label(case: ProbeCase, baseline: BaselineOutput) -> tuple[str, str]:
    retrieved_recall = evidence_recall_from_memory_ids(
        case, baseline.retrieved_memory_ids
    )
    extracted_recall = evidence_recall_from_memory_ids(
        case,
        tuple(item.memory_id for item in case.extracted_memory),
    )
    context_recall = evidence_recall_from_text(
        case.gold_evidence, baseline.injected_context
    )
    raw_event_text = "\n".join(event.text for event in case.raw_events)
    raw_event_recall = evidence_recall_from_text(case.gold_evidence, raw_event_text)
    has_gold_memory_pointer = any(
        evidence.source_memory_id is not None for evidence in case.gold_evidence
    )

    if retrieved_recall >= 1.0 and context_recall < 1.0:
        return (
            "injection_error",
            "retrieved evidence ids were present, but the injected context did not recall them",
        )
    if context_recall >= 1.0 and baseline.answer_score < 1.0:
        return (
            "reasoning_error",
            "baseline context recalled the evidence, but the answer still failed scoring",
        )
    if extracted_recall >= 1.0 and retrieved_recall < 1.0:
        return (
            "retrieval_error",
            "gold evidence exists in extracted memory, but the baseline did not retrieve it",
        )
    if raw_event_recall >= 1.0 and extracted_recall < 1.0:
        if has_gold_memory_pointer:
            return (
                "compression_error",
                "raw events contain the evidence, but extracted memory no longer recalls it",
            )
        return (
            "premature_extraction_error",
            "raw events contain the evidence, but no recoverable extracted memory points to it",
        )
    return (
        "write_error",
        "the failed trace does not expose recoverable evidence in extracted memory",
    )
