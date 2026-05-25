"""Comparator baselines for CMD-Audit.

Non-CMD comparison systems over the failed baseline trace, separated from
CMD's core diagnosis pipeline (attribution, replay, ECS, post-repair).
"""

from .comparators import (
    BaselineConfigurationError,
    BaselineSuiteResult,
    ComparatorResult,
    LLMJudgeOutputError,
    LeakSafeMonitorError,
    MemoryBaselineRun,
    SubagentJudgeMonitorDecision,
    build_judge_prompt,
    parse_label_from_response,
    run_baseline_suite,
    run_evidence_recall_heuristic,
    run_llm_judge_baseline,
    run_memory_baselines,
    run_random_label_baseline,
    run_subagent_judge_baseline,
    run_subagent_judge_monitor,
    validate_evidence_pointers,
    validate_monitor_payload,
)
from .memory_probe import (
    MemoryProbeBaselineResult,
    MemoryProbeCaseResult,
    MemoryProbeCellResult,
    run_memory_probe_baselines,
    run_memory_probe_case,
)

__all__ = [
    "BaselineConfigurationError",
    "BaselineSuiteResult",
    "ComparatorResult",
    "LLMJudgeOutputError",
    "LeakSafeMonitorError",
    "MemoryBaselineRun",
    "MemoryProbeBaselineResult",
    "MemoryProbeCaseResult",
    "MemoryProbeCellResult",
    "SubagentJudgeMonitorDecision",
    "build_judge_prompt",
    "parse_label_from_response",
    "run_baseline_suite",
    "run_evidence_recall_heuristic",
    "run_llm_judge_baseline",
    "run_memory_baselines",
    "run_memory_probe_baselines",
    "run_memory_probe_case",
    "run_random_label_baseline",
    "run_subagent_judge_baseline",
    "run_subagent_judge_monitor",
    "validate_evidence_pointers",
    "validate_monitor_payload",
]
