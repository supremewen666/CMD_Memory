"""Harness entry points for running CMD-Audit through the mem0 and Letta adapter paths."""

from __future__ import annotations

from typing import Callable

from cmd_audit.attribution import assign_attribution_v1
from baselines import run_baseline_suite
from cmd_audit.harness import (
    AuditResult,
    _score_baseline_evidence_with_agent,
    _with_llm_baseline_recovery_gain,
)
from cmd_audit.models import ProbeCase
from cmd_audit.provenance import ProvenanceTracker, get_graph_distractor_edges
from cmd_audit.replays import AgentGenerate, EvidenceScorer, ReplayResult

from .base import LettaTrace, Mem0Trace
from .letta_adapter import LettaAdapter
from .letta_replays import run_letta_replay_portfolio
from .mem0_adapter import Mem0Adapter
from .mem0_replays import run_mem0_replay_portfolio


def _run_case_with_adapter(
    case: ProbeCase,
    adapter,
    run_portfolio: Callable[..., tuple[ReplayResult, ...]],
    *,
    top_k: int = 2,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    on_the_fly_baseline_rescore: bool = False,
) -> AuditResult:
    """Run V1 pipeline through an adapter path (shared runner)."""
    baseline_suite = run_baseline_suite(case)
    baseline_evidence_score_llm = _score_baseline_evidence_with_agent(
        case,
        agent_generate=agent_generate,
        scorer=scorer,
        enabled=on_the_fly_baseline_rescore,
    )
    tracker = ProvenanceTracker(case.case_id)
    replays = run_portfolio(
        case,
        adapter,
        tracker=tracker,
        scorer=scorer,
        agent_generate=agent_generate,
    )
    replays = _with_llm_baseline_recovery_gain(replays, baseline_evidence_score_llm)

    distractor_edges = ()
    for r in replays:
        if r.replay_name == "graph_off":
            distractor_edges = get_graph_distractor_edges(case, r)
            break

    attribution = assign_attribution_v1(
        replays,
        has_ingestion_trace=case.has_ingestion_trace,
        positive_gain_threshold=(
            -1e-12 if baseline_evidence_score_llm is not None else 0.0
        ),
        top_k=top_k,
        distractor_edges=distractor_edges,
    )
    baseline = case.primary_baseline
    return AuditResult(
        case_id=case.case_id,
        perturbation_label=case.perturbation_label,
        baseline_name=baseline.baseline_name,
        baseline_answer_score=baseline.answer_score,
        baseline_evidence_score=baseline.evidence_score,
        replays=replays,
        attribution=attribution,
        baseline_suite=baseline_suite,
        baseline_evidence_score_llm=baseline_evidence_score_llm,
    )


# ── mem0 adapter path ───────────────────────────────────────────────────


def run_case_with_mem0(
    case: ProbeCase,
    trace: Mem0Trace,
    *,
    top_k: int = 2,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    on_the_fly_baseline_rescore: bool = False,
) -> AuditResult:
    """Run V1 pipeline through the mem0 adapter path."""
    adapter = Mem0Adapter(
        trace, case.gold_evidence, case.extracted_memory, case.raw_events
    )
    return _run_case_with_adapter(
        case,
        adapter,
        run_mem0_replay_portfolio,
        top_k=top_k,
        scorer=scorer,
        agent_generate=agent_generate,
        on_the_fly_baseline_rescore=on_the_fly_baseline_rescore,
    )


def run_cases_with_mem0(
    cases: list[ProbeCase],
    traces: dict[str, Mem0Trace],
    *,
    top_k: int = 2,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    on_the_fly_baseline_rescore: bool = False,
) -> list[AuditResult]:
    """Run V1 pipeline through mem0 adapter path for multiple cases."""
    return [
        run_case_with_mem0(
            case,
            traces[case.case_id],
            top_k=top_k,
            scorer=scorer,
            agent_generate=agent_generate,
            on_the_fly_baseline_rescore=on_the_fly_baseline_rescore,
        )
        for case in cases
    ]


# ── Letta adapter path ──────────────────────────────────────────────────


def run_case_with_letta(
    case: ProbeCase,
    trace: LettaTrace,
    *,
    top_k: int = 2,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    on_the_fly_baseline_rescore: bool = False,
) -> AuditResult:
    """Run V1 pipeline through the Letta adapter path."""
    adapter = LettaAdapter(
        trace, case.gold_evidence, case.extracted_memory, case.raw_events
    )
    return _run_case_with_adapter(
        case,
        adapter,
        run_letta_replay_portfolio,
        top_k=top_k,
        scorer=scorer,
        agent_generate=agent_generate,
        on_the_fly_baseline_rescore=on_the_fly_baseline_rescore,
    )


def run_cases_with_letta(
    cases: list[ProbeCase],
    traces: dict[str, LettaTrace],
    *,
    top_k: int = 2,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    on_the_fly_baseline_rescore: bool = False,
) -> list[AuditResult]:
    """Run V1 pipeline through Letta adapter path for multiple cases."""
    return [
        run_case_with_letta(
            case,
            traces[case.case_id],
            top_k=top_k,
            scorer=scorer,
            agent_generate=agent_generate,
            on_the_fly_baseline_rescore=on_the_fly_baseline_rescore,
        )
        for case in cases
    ]
