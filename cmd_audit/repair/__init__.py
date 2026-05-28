"""Repair subpackage — ECS drafting, repair execution, and failure memory.

Subpackage structure (Decision 35 Day 4):
- ``ecs.py`` — ECSDraft dataclass and validation
- ``post_repair.py`` — Post-Repair Context Replay
- ``actions.py`` — RepairAction taxonomy and tool schema
- ``executor.py`` — single-repair execution
- ``orchestrator.py`` — iterative repair loop
- ``failure_memory.py`` — recurrence store
"""

from .actions import (
    REPAIR_ACTION_BY_LABEL,
    REPAIR_ACTION_SYSTEM_PROMPT,
    REPAIR_ACTION_TOOL_DEFINITION,
    REPAIR_ACTION_TYPES,
    RepairAction,
    RepairActionOutputError,
    RepairActionResult,
    RepairActionTypeError,
    RepairClaimLedger,
    RepairComparisonRow,
    TargetedRepairAction,
    UnsupportedActionError,
    build_repair_action_prompt,
    build_repair_claim_ledger,
    compute_repair_success_summary,
    get_targeted_repair_action,
    get_targeted_repair_action_v1,
    make_repair_comparison,
    parse_repair_action_response,
    validate_repair_action_type,
    write_repair_success_table,
)
from .ecs import ECSDraft, ECSCauseValidationError
from .executor import RepairExecutor, RepairExecutorResult
from .failure_memory import (
    FailureMemoryRecord,
    FailureMemoryStore,
    FailureMemoryStoreV1,
    RecurrenceComparisonRow,
    RecurrenceSummary,
    build_failure_memory_context,
    build_failure_memory_context_v1,
    build_repair_context,
    compute_memory_top_terms,
    compute_recurrence_summary,
    run_recurrence_comparison,
    run_recurrence_comparisons,
    write_recurrence_comparison_table,
)
from .orchestrator import AttributionFailed, RepairOrchestrator, RepairOrchestratorResult
from .post_repair import (
    REPAIR_ASSESSMENT_VALUES,
    AgentGenerate,
    AnswerVerifierCallable,
    EvidenceScorer,
    PostRepairResult,
    RepairedContext,
    build_repaired_context,
    classify_repair_assessment,
    draft_ecs,
    draft_ecs_for_label,
    run_hard_case_update_baseline,
    run_post_repair_context_replay,
    validate_sandbox_path,
)

__all__ = [
    # ecs
    "ECSDraft",
    "ECSCauseValidationError",
    # post_repair
    "REPAIR_ASSESSMENT_VALUES",
    "AgentGenerate",
    "AnswerVerifierCallable",
    "EvidenceScorer",
    "PostRepairResult",
    "RepairedContext",
    "build_repaired_context",
    "classify_repair_assessment",
    "draft_ecs",
    "draft_ecs_for_label",
    "run_hard_case_update_baseline",
    "run_post_repair_context_replay",
    # actions
    "REPAIR_ACTION_TYPES",
    "RepairAction",
    "TargetedRepairAction",
    "compare_repair_actions",
    "repair_action_from_label",
    # executor
    "RepairExecutor",
    "RepairExecutorResult",
    # orchestrator
    "RepairOrchestrator",
    "RepairOrchestratorResult",
    # failure_memory
    "FailureMemoryRecord",
    "FailureMemoryStore",
    "FailureMemoryStoreV1",
    "RecurrenceComparisonRow",
    "RecurrenceSummary",
    "build_failure_memory_context",
    "build_failure_memory_context_v1",
]
