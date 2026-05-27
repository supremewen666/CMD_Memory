"""Public CMD-Audit V0 harness entry points."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .attribution import AttributionResult, assign_attribution, assign_attribution_v1
from baselines.comparators import BaselineSuiteResult, run_baseline_suite
from .metrics import DiagnosisPrediction, compute_diagnosis_metrics
from .models import ProbeCase, RetrievedItem, load_all_real_cases
from .post_repair import (
    ECSDraft,
    PostRepairResult,
    RepairedContext,
    build_repaired_context,
    draft_ecs,
    run_hard_case_update_baseline,
    run_post_repair_context_replay,
)
from .provenance import (
    ProvenanceTracker,
    get_graph_distractor_edges,
)
from .hook import PreCmdDecision, post_retrieve_hook
from .repairs import (
    RepairComparisonRow,
    make_repair_comparison,
    write_repair_success_table,
)
from .replays import (
    AgentGenerate,
    EvidenceScorer,
    ReplayResult,
    run_v0_replay_portfolio,
    run_v1_replay_portfolio,
    run_v1_replay_portfolio_subset,
)
from .scoring import answer_score, evidence_recall_from_text
from .writers import (
    write_attribution_table,
    write_confusion_matrix_table,
    write_csv_table,
    write_post_repair_table,
    write_provenance_completeness_summary,
)


@dataclass(frozen=True)
class AuditResult:
    case_id: str
    perturbation_label: str
    baseline_name: str
    baseline_answer_score: float
    baseline_evidence_score: float
    replays: tuple[ReplayResult, ...]
    attribution: AttributionResult | None
    baseline_suite: BaselineSuiteResult
    baseline_evidence_score_llm: float | None = None
    baseline_answer_score_llm: float | None = None
    hook_stage: str = ""  # 0020-F/0021: empty_ctx | rpe_top_k | rpe_below_threshold | ""
    selected_replays: tuple[str, ...] = ()  # 0020-F/0021: hook-selected top-k replays
    per_replay_scores: tuple = ()  # 0020-F/0021: ReplayScore per replay

    @property
    def attribution_correct(self) -> bool | None:
        if self.perturbation_label is None:
            return None
        if self.attribution is None:
            return None
        return self.attribution.predicted_label == self.perturbation_label

    @property
    def replay(self) -> ReplayResult:
        if self.attribution is None:
            raise ValueError(f"{self.case_id}: no attribution is available")
        return self.replay_by_name(self.attribution.top_replay)

    @property
    def diagnosis_cost(self) -> float:
        return self.baseline_suite.monitor.cost_per_decision + sum(
            replay.cost_units for replay in self.replays
        )

    def replay_by_name(self, replay_name: str) -> ReplayResult:
        for replay in self.replays:
            if replay.replay_name == replay_name:
                return replay
        raise KeyError(f"{self.case_id}: replay {replay_name!r} did not run")


@dataclass(frozen=True)
class FullAuditResult:
    """Complete CMD-Audit pipeline result including Post-Repair Context Replay."""

    audit: AuditResult
    ecs_draft: ECSDraft
    repaired_context: RepairedContext
    post_repair: PostRepairResult
    hard_case_baseline: PostRepairResult


def run_case_full(case: ProbeCase) -> FullAuditResult:
    """Run the complete V0 pipeline: attribution -> ECS -> repair -> post-repair replay."""
    audit = run_case(case)
    ecs_draft = draft_ecs(case, audit)
    repaired_context = build_repaired_context(case, ecs_draft)
    post_repair = run_post_repair_context_replay(case, repaired_context)
    hard_case_baseline = run_hard_case_update_baseline(case)
    return FullAuditResult(
        audit=audit,
        ecs_draft=ecs_draft,
        repaired_context=repaired_context,
        post_repair=post_repair,
        hard_case_baseline=hard_case_baseline,
    )


def run_case(
    case: ProbeCase,
    *,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: Any = None,
    on_the_fly_baseline_rescore: bool = False,
) -> AuditResult:
    baseline_suite = run_baseline_suite(case)
    baseline = case.primary_baseline
    baseline_evidence_score_llm, baseline_answer_score_llm = _score_baseline_with_agent(
        case,
        agent_generate=agent_generate,
        scorer=scorer,
        answer_verifier=answer_verifier,
        enabled=on_the_fly_baseline_rescore,
    )
    replays = run_v0_replay_portfolio(
        case,
        scorer=scorer,
        agent_generate=agent_generate,
        answer_verifier=answer_verifier,
    )
    replays = _apply_dual_axis_recovery_gain(
        replays,
        baseline_evidence_llm=(
            baseline_evidence_score_llm
            if baseline_evidence_score_llm is not None
            else baseline.evidence_score
        ),
        baseline_answer_llm=(
            baseline_answer_score_llm
            if baseline_answer_score_llm is not None
            else baseline.answer_score
        ),
    )
    attribution = assign_attribution(
        replays,
        positive_gain_threshold=0.0,
    )
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
        baseline_answer_score_llm=baseline_answer_score_llm,
    )


def run_cases(cases: list[ProbeCase], **kwargs) -> list[AuditResult]:
    return [run_case(case, **kwargs) for case in cases]


def run_cases_full(cases: list[ProbeCase]) -> list[FullAuditResult]:
    return [run_case_full(case) for case in cases]


def _score_baseline_with_agent(
    case: ProbeCase,
    *,
    agent_generate: AgentGenerate | None,
    scorer: EvidenceScorer | None,
    answer_verifier: Any = None,
    enabled: bool,
) -> tuple[float | None, float | None]:
    if not enabled or agent_generate is None:
        return None, None
    baseline_context = _baseline_agent_context(case)
    answer = agent_generate(case.query, baseline_context)
    evidence_score = (
        scorer(case.gold_evidence, answer)
        if scorer is not None
        else evidence_recall_from_text(case.gold_evidence, answer)
    )
    answer_llm_score = _score_answer_with_verifier(
        answer_verifier,
        answer,
        case.gold_answer,
    )
    return evidence_score, answer_llm_score


def _score_baseline_evidence_with_agent(
    case: ProbeCase,
    *,
    agent_generate: AgentGenerate | None,
    scorer: EvidenceScorer | None,
    enabled: bool,
) -> float | None:
    """Backward-compatible evidence-only baseline scorer."""
    evidence_score, _ = _score_baseline_with_agent(
        case,
        agent_generate=agent_generate,
        scorer=scorer,
        answer_verifier=None,
        enabled=enabled,
    )
    return evidence_score


def _score_answer_with_verifier(
    answer_verifier: Any,
    answer: str,
    gold_answer: str,
) -> float:
    """Score answer equivalence using a verifier when provided."""
    from .llm_scoring import score_answer_with_verifier

    return score_answer_with_verifier(answer_verifier, answer, gold_answer)


def _baseline_agent_context(case: ProbeCase) -> str:
    baseline_context = case.primary_baseline.injected_context
    if baseline_context:
        return baseline_context
    memory_by_id = {item.memory_id: item for item in case.extracted_memory}
    return "\n".join(
        memory_by_id[mid].text
        for mid in case.primary_baseline.retrieved_memory_ids
        if mid in memory_by_id
    )


def _derive_store_sets(
    case: ProbeCase,
) -> tuple[frozenset[str], frozenset[str]]:
    """Derive (gold_stores, queried_stores) for shadow-replay disambiguation.

    ``gold_stores``    — every store the gold evidence's source memory lives in.
    ``queried_stores`` — every store the baseline retrieval pulled from.

    Both are frozensets of store names; missing source_memory_id entries are
    skipped (they could not be located in any store anyway).
    """
    memory_by_id = {item.memory_id: item for item in case.extracted_memory}
    gold = {
        memory_by_id[ev.source_memory_id].store
        for ev in case.gold_evidence
        if ev.source_memory_id and ev.source_memory_id in memory_by_id
    }
    queried = {
        memory_by_id[mid].store
        for mid in case.primary_baseline.retrieved_memory_ids
        if mid in memory_by_id
    }
    return frozenset(gold), frozenset(queried)


def _apply_dual_axis_recovery_gain(
    replays: tuple[ReplayResult, ...],
    *,
    baseline_evidence_llm: float | None,
    baseline_answer_llm: float | None,
) -> tuple[ReplayResult, ...]:
    out: list[ReplayResult] = []
    for replay in replays:
        if replay.replay_name == "evidence_given_reasoning":
            ref = baseline_answer_llm
            score = replay.answer_score
        else:
            ref = baseline_evidence_llm
            score = replay.evidence_score
        if ref is None:
            out.append(replay)
        else:
            out.append(replace(replay, recovery_gain=score - ref))
    return tuple(out)


def _with_llm_baseline_recovery_gain(
    replays: tuple[ReplayResult, ...],
    baseline_evidence_score_llm: float | None,
) -> tuple[ReplayResult, ...]:
    """Backward-compatible evidence-axis wrapper."""
    return _apply_dual_axis_recovery_gain(
        replays,
        baseline_evidence_llm=baseline_evidence_score_llm,
        baseline_answer_llm=None,
    )


def write_repair_success_table_from_full(
    results: list[FullAuditResult],
    output_path: str | Path,
    *,
    sandbox_root: str | Path | None = None,
) -> list[RepairComparisonRow]:
    """Build repair comparison rows from full pipeline results and write the table."""
    rows = [make_repair_comparison(fr) for fr in results]
    write_repair_success_table(rows, output_path, sandbox_root=sandbox_root)
    return rows


def diagnosis_predictions(result: AuditResult) -> tuple[DiagnosisPrediction, ...]:
    predicted_label = None
    top2_labels: tuple[str, ...] = ()
    if result.attribution is not None:
        predicted_label = result.attribution.predicted_label
        top2_labels = result.attribution.top2_labels
    predictions = [
        DiagnosisPrediction(
            system_name="CMD-Audit",
            case_id=result.case_id,
            gold_label=result.perturbation_label,
            predicted_label=predicted_label,
            top2_labels=top2_labels,
            cost_per_diagnosis=result.diagnosis_cost,
        )
    ]
    for comparator in result.baseline_suite.comparator_results:
        predictions.append(
            DiagnosisPrediction(
                system_name=comparator.comparator_name,
                case_id=result.case_id,
                gold_label=result.perturbation_label,
                predicted_label=comparator.predicted_label,
                top2_labels=comparator.top2_labels,
                cost_per_diagnosis=comparator.cost_per_diagnosis,
            )
        )
    return tuple(predictions)


def write_comparison_metrics_table(
    results: list[AuditResult],
    output_path: str | Path,
    *,
    memory_probe_best_accuracy: float | None = None,
) -> None:
    predictions = [
        prediction for result in results for prediction in diagnosis_predictions(result)
    ]
    metrics = compute_diagnosis_metrics(predictions)

    fieldnames = [
        "system_name",
        "attribution_accuracy",
        "macro_f1",
        "top2_accuracy",
        "cost_per_diagnosis",
    ]
    if memory_probe_best_accuracy is not None:
        fieldnames.append("memory_probe_best_accuracy")

    rows = [
        {
            "system_name": row.system_name,
            "attribution_accuracy": f"{row.attribution_accuracy:.3f}",
            "macro_f1": f"{row.macro_f1:.3f}",
            "top2_accuracy": f"{row.top2_accuracy:.3f}",
            "cost_per_diagnosis": f"{row.cost_per_diagnosis:.3f}",
            **(
                {"memory_probe_best_accuracy": f"{memory_probe_best_accuracy:.3f}"}
                if memory_probe_best_accuracy is not None
                else {}
            ),
        }
        for system_name in sorted(metrics)
        for row in [metrics[system_name]]
    ]

    total_replays = sum(len(result.replays) for result in results)
    replays_with_prov = sum(
        sum(1 for replay in result.replays if replay.provenance_edges)
        for result in results
    )
    provenance_completeness = (
        replays_with_prov / total_replays if total_replays > 0 else 0.0
    )
    fieldnames.append("provenance_completeness")
    rows.append(
        {
            "system_name": "CMD-Audit",
            "attribution_accuracy": "",
            "macro_f1": f"{provenance_completeness:.3f}",
            "top2_accuracy": "",
            "cost_per_diagnosis": "",
            **(
                {"memory_probe_best_accuracy": f"{memory_probe_best_accuracy:.3f}"}
                if memory_probe_best_accuracy is not None
                else {}
            ),
            "provenance_completeness": f"{replays_with_prov}/{total_replays}",
        }
    )

    write_csv_table(output_path, fieldnames, rows)


# ── V1 Pipeline ────────────────────────────────────────────────────────


def run_case_v1(
    case: ProbeCase,
    *,
    top_k: int = 2,
    tie_margin: float = 0.0,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: Any = None,
    on_the_fly_baseline_rescore: bool = False,
) -> AuditResult:
    """Run the V1 pipeline: 10-replay portfolio + V1 attribution."""
    baseline_suite = run_baseline_suite(case)
    baseline = case.primary_baseline
    tracker = ProvenanceTracker(case.case_id)
    baseline_evidence_score_llm, baseline_answer_score_llm = _score_baseline_with_agent(
        case,
        agent_generate=agent_generate,
        scorer=scorer,
        answer_verifier=answer_verifier,
        enabled=on_the_fly_baseline_rescore,
    )
    replays = run_v1_replay_portfolio(
        case,
        tracker=tracker,
        scorer=scorer,
        agent_generate=agent_generate,
        answer_verifier=answer_verifier,
    )
    replays = _apply_dual_axis_recovery_gain(
        replays,
        baseline_evidence_llm=(
            baseline_evidence_score_llm
            if baseline_evidence_score_llm is not None
            else baseline.evidence_score
        ),
        baseline_answer_llm=(
            baseline_answer_score_llm
            if baseline_answer_score_llm is not None
            else baseline.answer_score
        ),
    )

    graph_off_replay = None
    for r in replays:
        if r.replay_name == "graph_off":
            graph_off_replay = r
            break
    distractor_edges = ()
    if graph_off_replay is not None:
        distractor_edges = get_graph_distractor_edges(case, graph_off_replay)

    gold_stores, queried_stores = _derive_store_sets(case)
    attribution = assign_attribution_v1(
        replays,
        has_ingestion_trace=case.has_ingestion_trace,
        positive_gain_threshold=0.0,
        tie_margin=tie_margin,
        top_k=top_k,
        distractor_edges=distractor_edges,
        gold_stores=gold_stores,
        queried_stores=queried_stores,
        default_store=case.default_store,
    )
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
        baseline_answer_score_llm=baseline_answer_score_llm,
    )


def run_cases_v1(cases: list[ProbeCase], *, top_k: int = 2, **kwargs) -> list[AuditResult]:
    return [run_case_v1(case, top_k=top_k, **kwargs) for case in cases]


def run_case_full_v1(
    case: ProbeCase,
    *,
    top_k: int = 2,
    tie_margin: float = 0.0,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    evidence_scorer: EvidenceScorer | None = None,
    answer_verifier=None,
    partial_threshold: float = 0.5,
    on_the_fly_baseline_rescore: bool = False,
) -> FullAuditResult:
    """Run the complete V1 pipeline: attribution -> ECS -> repair -> post-repair replay."""
    audit = run_case_v1(
        case,
        top_k=top_k,
        tie_margin=tie_margin,
        scorer=scorer,
        agent_generate=agent_generate,
        answer_verifier=answer_verifier,
        on_the_fly_baseline_rescore=on_the_fly_baseline_rescore,
    )
    ecs_draft = draft_ecs(case, audit)
    repaired_context = build_repaired_context(case, ecs_draft)
    post_repair_scorer = evidence_scorer or scorer
    post_repair = run_post_repair_context_replay(
        case,
        repaired_context,
        agent_generate=agent_generate,
        evidence_scorer=post_repair_scorer,
        answer_verifier=answer_verifier,
        partial_threshold=partial_threshold,
    )
    hard_case_baseline = run_hard_case_update_baseline(
        case,
        agent_generate=agent_generate,
        evidence_scorer=post_repair_scorer,
        answer_verifier=answer_verifier,
        partial_threshold=partial_threshold,
    )
    return FullAuditResult(
        audit=audit,
        ecs_draft=ecs_draft,
        repaired_context=repaired_context,
        post_repair=post_repair,
        hard_case_baseline=hard_case_baseline,
    )


def run_cases_full_v1(
    cases: list[ProbeCase], *, top_k: int = 2, **kwargs
) -> list[FullAuditResult]:
    return [run_case_full_v1(case, top_k=top_k, **kwargs) for case in cases]


# ── V1 Hook Pipeline (issue 0021) ───────────────────────────────────────


def run_case_v1_with_hook(
    case: ProbeCase,
    *,
    adapter_name: str = "",
    agent_confidence: float | None = None,
    mode: str = "online",
    tie_margin: float = 0.0,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: Any = None,
    on_the_fly_baseline_rescore: bool = False,
) -> AuditResult:
    """Run V1 pipeline with the issue 0021 two-stage Pre-CMD Hook.

    When the hook skips, returns an AuditResult with ``attribution=None``.
    When it triggers, only ``decision.selected_replays`` are run.
    """
    del agent_confidence
    baseline_suite = run_baseline_suite(case)
    baseline = case.primary_baseline
    baseline_evidence_score_llm, baseline_answer_score_llm = _score_baseline_with_agent(
        case,
        agent_generate=agent_generate,
        scorer=scorer,
        answer_verifier=answer_verifier,
        enabled=on_the_fly_baseline_rescore,
    )

    # Build RetrievedItem bridge
    memory_by_id = {item.memory_id: item for item in case.extracted_memory}
    retrieved_items = tuple(
        RetrievedItem(memory_id=mid, text=memory_by_id[mid].text)
        for mid in baseline.retrieved_memory_ids
        if mid in memory_by_id
    )

    # Pre-CMD Hook: zero-gold gating decision
    decision = post_retrieve_hook(
        case.query,
        retrieved_items,
        adapter_name=adapter_name,
        mode=mode,
    )

    if not decision.trigger_cmd:
        return AuditResult(
            case_id=case.case_id,
            perturbation_label=case.perturbation_label,
            baseline_name=baseline.baseline_name,
            baseline_answer_score=baseline.answer_score,
            baseline_evidence_score=baseline.evidence_score,
            replays=(),
            attribution=None,
            baseline_suite=baseline_suite,
            baseline_evidence_score_llm=baseline_evidence_score_llm,
            baseline_answer_score_llm=baseline_answer_score_llm,
            hook_stage=decision.stage,
            selected_replays=decision.selected_replays,
            per_replay_scores=decision.per_replay_scores,
        )

    tracker = ProvenanceTracker(case.case_id)
    replays = run_v1_replay_portfolio_subset(
        case,
        decision.selected_replays,
        tracker=tracker,
        scorer=scorer,
        agent_generate=agent_generate,
        answer_verifier=answer_verifier,
    )
    replays = _apply_dual_axis_recovery_gain(
        replays,
        baseline_evidence_llm=(
            baseline_evidence_score_llm
            if baseline_evidence_score_llm is not None
            else baseline.evidence_score
        ),
        baseline_answer_llm=(
            baseline_answer_score_llm
            if baseline_answer_score_llm is not None
            else baseline.answer_score
        ),
    )

    distractor_edges = ()
    if "graph_off" in decision.selected_replays:
        for r in replays:
            if r.replay_name == "graph_off":
                distractor_edges = get_graph_distractor_edges(case, r)
                break

    gold_stores, queried_stores = _derive_store_sets(case)
    try:
        attribution = assign_attribution_v1(
            replays,
            has_ingestion_trace=case.has_ingestion_trace,
            positive_gain_threshold=0.0,
            tie_margin=tie_margin,
            top_k=2,
            distractor_edges=distractor_edges,
            gold_stores=gold_stores,
            queried_stores=queried_stores,
            default_store=case.default_store,
        )
    except ValueError:
        attribution = None

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
        baseline_answer_score_llm=baseline_answer_score_llm,
        hook_stage=decision.stage,
        selected_replays=decision.selected_replays,
        per_replay_scores=decision.per_replay_scores,
    )


def run_cases_v1_with_hook(
    cases: list[ProbeCase], **kwargs
) -> list[AuditResult]:
    """Batch wrapper for ``run_case_v1_with_hook``."""
    return [run_case_v1_with_hook(case, **kwargs) for case in cases]


# ── V1 Hook + Repair Integration (issue 0020-C) ─────────────────────────


def run_case_v1_with_hook_and_repair(
    case: ProbeCase,
    *,
    adapter,
    fm_context: str = "",
    close_deltas_threshold: float = 0.0,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    repair_llm_client=None,
    require_llm_repair_action: bool = False,
    **hook_kwargs,
) -> dict:
    """Run V1 pipeline with Pre-CMD Hook + RepairOrchestrator (online mode).

    Integrates the full pipeline: hook → attribution → RepairOrchestrator →
    iterative repair → result with all attempts.

    Args:
        case: The probe case.
        adapter: CMD-Skill Adapter with supported_actions and apply_repair.
        fm_context: Failure Memory diagnostic context.
        close_deltas_threshold: Minimum gain for close_deltas inclusion.
        **hook_kwargs: Passed to post_retrieve_hook.

    Returns:
        Dict with audit_result, orchestrator_result, and metadata.
    """
    from .repair_orchestrator import RepairOrchestrator

    # Step 1: Run hook + attribution
    audit = run_case_v1_with_hook(
        case, scorer=scorer, agent_generate=agent_generate, **hook_kwargs
    )

    # Step 2: If attribution failed or hook skipped, return early
    if audit.attribution is None:
        return {
            "case_id": case.case_id,
            "audit": audit,
            "orchestrator_result": None,
            "repaired": False,
        }

    # Step 3: Run RepairOrchestrator for iterative repair
    from .repair_executor import RepairExecutor

    orchestrator = RepairOrchestrator(
        executor=RepairExecutor(
            llm_client=repair_llm_client,
            require_llm_action=require_llm_repair_action,
        )
    )
    orch_result = orchestrator.run(
        attribution=audit.attribution,
        case=case,
        adapter=adapter,
        audit_result=audit,
        fm_context=fm_context,
        close_deltas_threshold=close_deltas_threshold,
    )

    return {
        "case_id": case.case_id,
        "audit": audit,
        "orchestrator_result": orch_result,
        "repaired": orch_result.recovered,
    }


# ── Full Real-Data Suite (issue 0016) ─────────────────────────────────────


def run_full_real_suite(
    *,
    out_dir: str | Path = "artifacts/sandbox",
    use_hook: bool = True,
    scorer: EvidenceScorer | None = None,
    evidence_scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier=None,
    tie_margin: float = 0.0,
    on_the_fly_baseline_rescore: bool = False,
) -> list[AuditResult]:
    """Run V1 pipeline on all 601 real-data cases and produce artifacts.

    Loads the full 596+5 real probe case suite, runs the V1 pipeline
    (with the Pre-CMD Hook by default), and writes attribution table, comparison
    metrics, and confusion matrix to *out_dir*.
    """
    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)

    cases = load_all_real_cases()
    effective_scorer = evidence_scorer or scorer
    full_results: list[FullAuditResult] | None = None

    if use_hook:
        results = run_cases_v1_with_hook(
            cases,
            scorer=effective_scorer,
            agent_generate=agent_generate,
            answer_verifier=answer_verifier,
            tie_margin=tie_margin,
            on_the_fly_baseline_rescore=on_the_fly_baseline_rescore,
        )
    else:
        full_results = run_cases_full_v1(
            cases,
            scorer=effective_scorer,
            evidence_scorer=effective_scorer,
            agent_generate=agent_generate,
            answer_verifier=answer_verifier,
            tie_margin=tie_margin,
            on_the_fly_baseline_rescore=on_the_fly_baseline_rescore,
        )
        results = [full.audit for full in full_results]

    att_path = dest / "attribution_table.csv"
    metrics_path = dest / "comparison_metrics.csv"
    confusion_path = dest / "attribution_confusion_matrix.csv"
    provenance_path = dest / "provenance_completeness.csv"

    write_attribution_table(results, att_path)
    write_comparison_metrics_table(results, metrics_path)
    write_confusion_matrix_table(results, confusion_path)
    write_provenance_completeness_summary(results, provenance_path)
    if full_results is not None:
        write_post_repair_table(full_results, dest / "post_repair_table.csv")
        try:
            write_repair_success_table_from_full(
                full_results,
                dest / "repair_success_table.csv",
            )
        except (AttributeError, KeyError, ValueError):
            # V1 labels without legacy repair-comparison rows still get the
            # post-repair table, which is the Decision 34 required artifact.
            pass

    labeled = sum(1 for r in results if r.perturbation_label is not None)
    null_labeled = len(results) - labeled
    n_triggered = sum(1 for r in results if r.attribution is not None)

    print(
        f"Real-data suite: {len(results)} cases ({labeled} labeled, "
        f"{null_labeled} null-label), {n_triggered} CMD-triggered"
    )
    print(f"Artifacts written to {dest}/")

    return results
