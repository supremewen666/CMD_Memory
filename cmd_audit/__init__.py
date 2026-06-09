"""CMD-Audit public API.

Paper-facing surface: entry points, core types, label registry, eval helpers,
and repair pipeline. Internal helpers live in subpackages.
"""

# ── Entry points ──────────────────────────────────────────────────────────────
from .harness import (
    AuditResult,
    run_case,
    run_cases,
    run_real_suite,
    run_replay_baseline_case,
)

# ── Core types ────────────────────────────────────────────────────────────────
from .core.models import (
    Citation,
    MemoryItem,
    ProbeCase,
    ProvenanceEdge,
)
from .attribution import (
    AttributionResult,
    assign_attribution,
    assign_replay_baseline_attribution,
)

# ── Label registry ────────────────────────────────────────────────────────────
from .core.labels import (
    ITEM_LABELS,
    MONITOR_ANOMALY_REASON_VALUES,
    PIPELINE_LABELS,
    PIPELINE_LABEL_ORDER,
    PIPELINE_STEP_ACTIONS,
    REPLAY_TO_LABEL,
    LabelValidationError,
    MonitorAnomalyReasonError,
    validate_item_label,
    validate_label,
    validate_monitor_anomaly_reason,
)
from .core import PhraseMatchShortcutWarning

# ── Data I/O ──────────────────────────────────────────────────────────────────
from .data_io import (
    load_probe_cases,
    load_probe_cases_v1,
)

# ── Eval ──────────────────────────────────────────────────────────────────────
from .eval import (
    DiagnosisMetrics,
    DiagnosisPrediction,
    ProvenanceTracker,
    SurrogateGapRow,
    SurrogateGapSummary,
    bootstrap_metric,
    compute_diagnosis_metrics,
    measure_surrogate_gap,
    measure_surrogate_gaps,
    compute_surrogate_gap_summary,
    write_attribution_table,
    write_confusion_matrix_table,
    write_post_repair_table,
    write_retrieval_metrics_table,
    write_retrieval_trace_table,
    write_step_level_metrics_table,
)

# ── Repair pipeline ───────────────────────────────────────────────────────────
from .repair import (
    ECSDraft,
    FailureMemoryDiagnosis,
    FailureMemoryOutcome,
    FailureMemoryRecord,
    FailureMemorySkill,
    FailureMemoryStore,
    MarkdownFailureMemoryStore,
    PatternValidationResult,
    PostRepairResult,
    RepairAction,
    RepairClaimLedger,
    RepairComparisonRow,
    RepairExecutor,
    RepairExecutorResult,
    RepairOrchestrator,
    RepairOrchestratorResult,
    RepairedContext,
    RecurrenceSummary,
    TargetedRepairAction,
    UnsupportedActionError,
    build_failure_memory_context,
    build_repair_claim_ledger,
    build_repaired_context,
    classify_repair_assessment,
    compute_memory_top_terms,
    compute_recurrence_summary,
    compute_repair_success_summary,
    draft_ecs,
    draft_ecs_for_label,
    get_targeted_repair_action,
    make_repair_comparison,
    run_hard_case_update_baseline,
    run_post_repair_context_replay,
    run_recurrence_comparisons,
    step_level_record_from_mcts_result,
    validate_sandbox_path,
    write_recurrence_comparison_table,
)

# ── Scoring ───────────────────────────────────────────────────────────────────
from .scoring import SubagentScorer

# ── Baselines ─────────────────────────────────────────────────────────────────
from .baselines.comparators import (
    BaselineSuiteResult,
    run_baseline_suite,
)

# ── Hook (public contract) ────────────────────────────────────────────────────
from .hook import HookDecision, ConfidenceFactors, post_retrieve_hook

# ── Replays (paper-facing subset) ─────────────────────────────────────────────
from .replays import (
    ReplayResult,
    run_replay_portfolio,
)

__all__ = [
    # entry points
    "AuditResult", "run_case", "run_cases", "run_real_suite",
    "run_replay_baseline_case",
    # core types
    "Citation", "MemoryItem", "ProbeCase", "ProvenanceEdge",
    "AttributionResult", "assign_attribution", "assign_replay_baseline_attribution",
    # label registry
    "ITEM_LABELS", "MONITOR_ANOMALY_REASON_VALUES", "PIPELINE_LABELS",
    "PIPELINE_LABEL_ORDER", "PIPELINE_STEP_ACTIONS", "REPLAY_TO_LABEL",
    "LabelValidationError", "MonitorAnomalyReasonError",
    "validate_item_label", "validate_label", "validate_monitor_anomaly_reason",
    "PhraseMatchShortcutWarning",
    # data I/O
    "load_probe_cases", "load_probe_cases_v1",
    # eval
    "DiagnosisMetrics", "DiagnosisPrediction", "ProvenanceTracker",
    "SurrogateGapRow", "SurrogateGapSummary", "bootstrap_metric",
    "compute_diagnosis_metrics", "measure_surrogate_gap", "measure_surrogate_gaps",
    "compute_surrogate_gap_summary", "write_attribution_table",
    "write_confusion_matrix_table", "write_post_repair_table",
    "write_retrieval_metrics_table", "write_retrieval_trace_table",
    "write_step_level_metrics_table",
    # repair
    "ECSDraft", "FailureMemoryDiagnosis", "FailureMemoryOutcome",
    "FailureMemoryRecord", "FailureMemorySkill", "FailureMemoryStore",
    "MarkdownFailureMemoryStore", "PatternValidationResult", "PostRepairResult",
    "RepairAction", "RepairClaimLedger", "RepairComparisonRow", "RepairExecutor",
    "RepairExecutorResult", "RepairOrchestrator", "RepairOrchestratorResult",
    "RepairedContext", "RecurrenceSummary", "TargetedRepairAction",
    "UnsupportedActionError", "build_failure_memory_context",
    "build_repair_claim_ledger", "build_repaired_context",
    "classify_repair_assessment", "compute_memory_top_terms",
    "compute_recurrence_summary", "compute_repair_success_summary",
    "draft_ecs", "draft_ecs_for_label", "get_targeted_repair_action",
    "make_repair_comparison", "run_hard_case_update_baseline",
    "run_post_repair_context_replay", "run_recurrence_comparisons",
    "step_level_record_from_mcts_result", "validate_sandbox_path",
    "write_recurrence_comparison_table",
    # scoring
    "SubagentScorer",
    # baselines
    "BaselineSuiteResult", "run_baseline_suite",
    # hook
    "PreCmdDecision",
    # replays
    "ReplayResult", "run_replay_portfolio",
]
