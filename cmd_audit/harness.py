"""Public CMD-Audit harness entry points."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .attribution import AttributionResult, assign_replay_baseline_attribution
from cmd_audit.baselines.comparators import BaselineSuiteResult, run_baseline_suite
from .core.models import ProbeCase, RetrievedItem, MemoryItem
from .data_io import load_all_real_cases
from .repair import (
    ECSDraft,
    FailureMemoryStore,
    PostRepairResult,
    RepairedContext,
    build_repaired_context,
    draft_ecs,
    run_hard_case_update_baseline,
    run_post_repair_context_replay,
)
from .eval import (
    ProvenanceTracker,
    get_graph_distractor_edges,
)
from .hook import HookDecision, post_retrieve_hook
from .hook import post_retrieve_hook as post_retrieve_hook
from .repair import (
    RepairComparisonRow,
    make_repair_comparison,
    write_repair_success_table,
)
from .replays import (
    AgentGenerate,
    EvidenceScorer,
    ReplayResult,
    run_replay_portfolio,
    run_replay_portfolio_subset,
)
from .scoring import answer_score, evidence_recall_from_text
from .eval import (
    write_attribution_table,
    write_csv_table,
    write_post_repair_table,
    write_provenance_completeness_summary,
    write_step_level_metrics_table,
)

from .item_gate import (
    ItemGateResult,
    item_signal_hints_from_result,
    run_item_gate_for_recall_set,
)
from .counterfactual import (
    OperatorSpec,
    PipelineAction,
    SearchResult,
    attribute_single_point,
    evaluate_operator_spec,
)

_SKILL_OPERATOR_ACCEPT_THRESHOLD = 0.0


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
    hook_stage: str = ""
    selected_replays: tuple[str, ...] = ()
    per_replay_scores: tuple = ()
    # Full ECS + post-repair pipeline fields (populated when
    # ``run_case(post_repair=True)`` runs; otherwise None).
    ecs_draft: ECSDraft | None = None
    repaired_context: RepairedContext | None = None
    post_repair: PostRepairResult | None = None
    hard_case_baseline: PostRepairResult | None = None
    # Iterative-repair fields (populated when
    # ``run_case(repair=adapter)`` runs).
    orchestrator_result: Any = None
    repaired: bool = False

    # Two-branch runtime fields.
    hook_decision: HookDecision | None = None  # Two-branch gate result (fill/fix)
    item_gate_result: ItemGateResult | None = None  # Tier 2 item gate result
    attribution_result: SearchResult | None = None  # Tier 3 step-level attribution result
    runtime_branch: str = ""  # "fill" | "fix" | "offline_replay"

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


def run_case(
    case: ProbeCase,
    *,
    hook: bool | str | None = None,
    repair: Any = None,
    post_repair: bool = False,
    scorer: EvidenceScorer | None = None,
    evidence_scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: Any = None,
    on_the_fly_baseline_rescore: bool = False,
    top_k: int = 2,
    tie_margin: float = 0.0,
    partial_threshold: float = 0.5,
    mode: str = "online",
    fm_context: str = "",
    close_deltas_threshold: float = 0.0,
    repair_llm_client: Any = None,
    require_llm_repair_action: bool = False,
    failure_memory_store: FailureMemoryStore | None = None,
) -> AuditResult:
    """Run the CMD-Audit pipeline for a single case.

    Produces a single :class:`AuditResult` over the current two-branch runtime.
    Optional stages are gated by keyword, all writing into the same result object:

    * ``hook`` — run the retrieval confidence gate before attribution. Fill
      returns without diagnosis; Fix enters item gate and step-level attribution.
    * ``repair`` — a CMD-Skill adapter; runs the RepairOrchestrator after
      attribution (implies ``hook``). Populates ``orchestrator_result`` / ``repaired``.
    * ``post_repair`` — run the full ECS + Post-Repair Context Replay pipeline.
      Populates ``ecs_draft`` / ``repaired_context`` / ``post_repair`` /
      ``hard_case_baseline``. Mutually exclusive with ``repair``.
    """
    if post_repair and repair is not None:
        raise ValueError("run_case: post_repair and repair are mutually exclusive")
    if repair is not None and not hook:
        hook = True  # the repair pipeline depends on the hook decision
    if hook is False:
        raise ValueError(
            "run_case(hook=False) is not a live runtime path. "
            "Use run_replay_baseline_case() for offline replay-baseline attribution."
        )

    if repair is not None:
        return _run_repair(
            case,
            adapter=repair,
            hook=hook,
            fm_context=fm_context,
            close_deltas_threshold=close_deltas_threshold,
            scorer=scorer,
            agent_generate=agent_generate,
            answer_verifier=answer_verifier,
            on_the_fly_baseline_rescore=on_the_fly_baseline_rescore,
            tie_margin=tie_margin,
            mode=mode,
            repair_llm_client=repair_llm_client,
            require_llm_repair_action=require_llm_repair_action,
            failure_memory_store=failure_memory_store,
        )
    if post_repair:
        return _run_full(
            case,
            top_k=top_k,
            tie_margin=tie_margin,
            scorer=scorer,
            evidence_scorer=evidence_scorer,
            agent_generate=agent_generate,
            answer_verifier=answer_verifier,
            partial_threshold=partial_threshold,
            on_the_fly_baseline_rescore=on_the_fly_baseline_rescore,
            failure_memory_store=failure_memory_store,
        )
    if hook:
        adapter_name = hook if isinstance(hook, str) else ""
        return _run_with_hook(
            case,
            adapter_name=adapter_name,
            mode=mode,
            tie_margin=tie_margin,
            scorer=scorer,
            agent_generate=agent_generate,
            answer_verifier=answer_verifier,
            on_the_fly_baseline_rescore=on_the_fly_baseline_rescore,
            failure_memory_store=failure_memory_store,
        )
    return _run_with_hook(
        case,
        adapter_name="",
        mode=mode,
        tie_margin=tie_margin,
        scorer=scorer,
        agent_generate=agent_generate,
        answer_verifier=answer_verifier,
        on_the_fly_baseline_rescore=on_the_fly_baseline_rescore,
        failure_memory_store=failure_memory_store,
    )


def run_cases(cases: list[ProbeCase], **kwargs) -> list[AuditResult]:
    """Run :func:`run_case` over a list of cases with shared keyword arguments."""
    return [run_case(case, **kwargs) for case in cases]


def run_replay_baseline_case(
    case: ProbeCase,
    *,
    top_k: int = 2,
    tie_margin: float = 0.0,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: Any = None,
    on_the_fly_baseline_rescore: bool = False,
) -> AuditResult:
    """Run the offline replay-baseline attribution path.

    This is not the live CMD runtime. It exists for baseline experiments that
    still compare replay-portfolio deltas against the current 5 step actions.
    """
    return _run_replay_baseline(
        case,
        top_k=top_k,
        tie_margin=tie_margin,
        scorer=scorer,
        agent_generate=agent_generate,
        answer_verifier=answer_verifier,
        on_the_fly_baseline_rescore=on_the_fly_baseline_rescore,
    )


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
    from .scoring import score_answer_with_verifier

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
    results: list[AuditResult],
    output_path: str | Path,
    *,
    sandbox_root: str | Path | None = None,
) -> list[RepairComparisonRow]:
    """Build repair comparison rows from full pipeline results and write the table."""
    rows = [make_repair_comparison(fr) for fr in results]
    write_repair_success_table(rows, output_path, sandbox_root=sandbox_root)
    return rows


def write_comparison_metrics_table(
    results: list[AuditResult],
    output_path: str | Path,
    *,
    memory_probe_best_accuracy: float | None = None,
) -> None:
    fieldnames = [
        "system_name",
        "cases",
        "triggered_cases",
        "positive_recovery_rate",
        "mean_recovery_gain",
        "cost_per_diagnosis",
        "provenance_completeness",
    ]
    if memory_probe_best_accuracy is not None:
        fieldnames.append("memory_probe_best_accuracy")

    attributed_results = [result for result in results if result.attribution is not None]
    recovery_gains = [
        float(result.attribution.recovery_gain) for result in attributed_results
    ]
    total_replays = sum(len(result.replays) for result in results)
    replays_with_prov = sum(
        sum(1 for replay in result.replays if replay.provenance_edges)
        for result in results
    )
    provenance_completeness = (
        replays_with_prov / total_replays if total_replays > 0 else 0.0
    )

    rows: list[dict[str, str]] = [
        {
            "system_name": "CMD-Audit",
            "cases": str(len(results)),
            "triggered_cases": str(len(attributed_results)),
            "positive_recovery_rate": f"{_positive_rate(recovery_gains):.3f}",
            "mean_recovery_gain": f"{_mean(recovery_gains):.3f}",
            "cost_per_diagnosis": f"{_mean([r.diagnosis_cost for r in results]):.3f}",
            "provenance_completeness": f"{provenance_completeness:.3f}",
            **(
                {"memory_probe_best_accuracy": f"{memory_probe_best_accuracy:.3f}"}
                if memory_probe_best_accuracy is not None
                else {}
            ),
        }
    ]

    comparator_costs: dict[str, list[float]] = {}
    for result in results:
        for comparator in result.baseline_suite.comparator_results:
            comparator_costs.setdefault(comparator.comparator_name, []).append(
                comparator.cost_per_diagnosis
            )

    for comparator_name in sorted(comparator_costs):
        rows.append(
            {
                "system_name": comparator_name,
                "cases": str(len(comparator_costs[comparator_name])),
                "triggered_cases": "0",
                "positive_recovery_rate": "0.000",
                "mean_recovery_gain": "0.000",
                "cost_per_diagnosis": f"{_mean(comparator_costs[comparator_name]):.3f}",
                "provenance_completeness": "0.000",
                **(
                    {
                        "memory_probe_best_accuracy": (
                            f"{memory_probe_best_accuracy:.3f}"
                        )
                    }
                    if memory_probe_best_accuracy is not None
                    else {}
                ),
            }
        )

    write_csv_table(output_path, fieldnames, rows)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _positive_rate(values: list[float]) -> float:
    return sum(1 for value in values if value > 0.0) / len(values) if values else 0.0


# ── Private pipeline helpers ────────────────────────────────────────────


def _run_replay_baseline(
    case: ProbeCase,
    *,
    top_k: int = 2,
    tie_margin: float = 0.0,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: Any = None,
    on_the_fly_baseline_rescore: bool = False,
) -> AuditResult:
    """Run the offline replay portfolio baseline (no live hook/runtime)."""
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
    replays = run_replay_portfolio(
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

    attribution = assign_replay_baseline_attribution(
        replays,
        positive_gain_threshold=0.0,
        tie_margin=tie_margin,
        top_k=top_k,
        distractor_edges=distractor_edges,
    )
    if attribution.attribution_failed:
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
        runtime_branch="offline_replay",
    )


def _run_full(
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
    failure_memory_store: FailureMemoryStore | None = None,
) -> AuditResult:
    """Run live runtime attribution -> ECS -> post-repair replay."""
    del top_k
    audit = _run_with_hook(
        case,
        tie_margin=tie_margin,
        scorer=scorer,
        agent_generate=agent_generate,
        answer_verifier=answer_verifier,
        on_the_fly_baseline_rescore=on_the_fly_baseline_rescore,
        failure_memory_store=failure_memory_store,
    )
    if audit.attribution is None:
        return audit
    guidance_getter = (
        getattr(failure_memory_store, "get_repair_guidance", None)
        if failure_memory_store is not None
        else None
    )
    ecs_operator_metadata = (
        guidance_getter(case.query, audit.attribution.predicted_label)
        if callable(guidance_getter)
        else ""
    )
    ecs_draft = draft_ecs(
        case,
        audit,
        operator_metadata=ecs_operator_metadata or None,
    )
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
    return replace(
        audit,
        ecs_draft=ecs_draft,
        repaired_context=repaired_context,
        post_repair=post_repair,
        hard_case_baseline=hard_case_baseline,
    )


# ── Two-Branch Runtime ──────────────────────────────────────────────────


def _run_with_hook(
    case: ProbeCase,
    *,
    adapter_name: str = "",
    mode: str = "online",
    tie_margin: float = 0.0,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: Any = None,
    on_the_fly_baseline_rescore: bool = False,
    failure_memory_store: FailureMemoryStore | None = None,
) -> AuditResult:
    """Run hook → Fill/Fix → item gate → step-level attribution for one case."""
    baseline_suite = run_baseline_suite(case)
    baseline = case.primary_baseline
    baseline_evidence_score_llm, baseline_answer_score_llm = _score_baseline_with_agent(
        case,
        agent_generate=agent_generate,
        scorer=scorer,
        answer_verifier=answer_verifier,
        enabled=on_the_fly_baseline_rescore,
    )

    recall_set = _retrieved_memory_items(case)
    retrieved_items = _as_retrieved_items(recall_set)
    decision = post_retrieve_hook(
        case.query,
        retrieved_items,
        failure_memory_store=failure_memory_store,
    )

    if decision.branch == "fill":
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
            hook_stage="fill",
            hook_decision=decision,
            runtime_branch="fill",
        )

    item_gate_result = None
    item_signal_hints: dict[str, float] = {}
    attribution = None
    mcts_client = _agent_generate_client(case.query, agent_generate)
    memory_texts = tuple(item.text for item in recall_set)
    if recall_set and mcts_client is not None:
        item_gate_result = run_item_gate_for_recall_set(
            mcts_client,
            recall_set,
            case.query,
            failure_memory_store=failure_memory_store,
        )
        item_signal_hints = item_signal_hints_from_result(item_gate_result)

    attribution_result = None
    mcts_max_depth = max(1, min(3, len(recall_set) or 1))
    base_context = _initial_mcts_context(case, recall_set)
    intervention_config = {
        "candidate_items": case.extracted_memory,
        "raw_events": case.raw_events,
        "item_signal_hints": item_signal_hints,
    }
    if attribution is None and mcts_client is not None:
        attribution_result = _try_failure_memory_operator_specs(
            mcts_client,
            base_context,
            recall_set,
            case.gold_answer,
            case.query,
            mcts_max_depth,
            intervention_config,
            answer_verifier,
            failure_memory_store,
            memory_texts=memory_texts,
        )
        attribution = _attribution_from_mcts(attribution_result)

    if attribution is None and mcts_client is not None:
        attribution_result = attribute_single_point(
            mcts_client,
            base_context,
            recall_set,
            case.gold_evidence,
            case.gold_answer,
            max_iterations=10,
            max_depth=mcts_max_depth,
            answer_verifier=answer_verifier,
            baseline_answer_score=(
                baseline_answer_score_llm
                if baseline_answer_score_llm is not None
                else baseline.answer_score
            ),
            intervention_config=intervention_config,
            action_priors=_failure_memory_action_priors(
                failure_memory_store,
                case.query,
                max_depth=mcts_max_depth,
                memory_texts=memory_texts,
            ),
        )
        add_attribution_result = (
            getattr(failure_memory_store, "add_attribution_result", None)
            if failure_memory_store is not None
            else None
        )
        if callable(add_attribution_result):
            add_attribution_result(
                case.query,
                attribution_result,
                memory_texts=memory_texts,
            )
        attribution = _attribution_from_mcts(attribution_result)
    if attribution is None:
        attribution = _attribution_from_structural_gap(case)

    return AuditResult(
        case_id=case.case_id,
        perturbation_label=case.perturbation_label,
        baseline_name=baseline.baseline_name,
        baseline_answer_score=baseline.answer_score,
        baseline_evidence_score=baseline.evidence_score,
        replays=(),
        attribution=attribution,
        baseline_suite=baseline_suite,
        baseline_evidence_score_llm=baseline_evidence_score_llm,
        baseline_answer_score_llm=baseline_answer_score_llm,
        hook_stage="fix",
        hook_decision=decision,
        item_gate_result=item_gate_result,
        attribution_result=attribution_result,
        runtime_branch="fix",
    )


def _retrieved_memory_items(case: ProbeCase) -> tuple[MemoryItem, ...]:
    memory_by_id = {item.memory_id: item for item in case.extracted_memory}
    return tuple(
        memory_by_id[mid]
        for mid in case.primary_baseline.retrieved_memory_ids
        if mid in memory_by_id
    )


def _as_retrieved_items(items: tuple[MemoryItem, ...]) -> tuple[RetrievedItem, ...]:
    return tuple(
        RetrievedItem(memory_id=item.memory_id, text=item.text) for item in items
    )


def _initial_mcts_context(case: ProbeCase, recall_set: tuple[MemoryItem, ...]) -> str:
    context = case.primary_baseline.injected_context
    if not context:
        context = "\n".join(item.text for item in recall_set)
    return f"Query: {case.query}\n\nRetrieved Memory:\n{context}"


class _AgentGenerateClient:
    def __init__(self, query: str, agent_generate: AgentGenerate) -> None:
        self._query = query
        self._agent_generate = agent_generate

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        context = prompt if system is None else f"{system}\n\n{prompt}"
        return self._agent_generate(self._query, context)

    def generate_with_logprobs(
        self,
        prompt: str,
        *,
        system: str | None = None,
        top_logprobs: int = 10,
    ):
        generator = self._agent_generate
        if hasattr(generator, "generate_with_logprobs"):
            return generator.generate_with_logprobs(
                prompt,
                system=system,
                top_logprobs=top_logprobs,
            )
        raise AttributeError("agent_generate does not expose generate_with_logprobs")


def _agent_generate_client(
    query: str, agent_generate: AgentGenerate | None
) -> _AgentGenerateClient | None:
    if agent_generate is None:
        return None
    return _AgentGenerateClient(query, agent_generate)


def _try_failure_memory_operator_specs(
    client: Any,
    initial_context: str,
    recall_set: tuple[MemoryItem, ...],
    gold_answer: str,
    query: str,
    max_depth: int,
    intervention_config: dict[str, Any],
    answer_verifier: Any,
    failure_memory_store: FailureMemoryStore | None,
    *,
    memory_texts: tuple[str, ...],
    top_k: int = 2,
    accept_threshold: float = _SKILL_OPERATOR_ACCEPT_THRESHOLD,
) -> SearchResult | None:
    """Try matched Failure Memory operator skills before exhaustive search.

    The operator construction path is gold-free; ``gold_answer`` is used only
    for the recovery gate. If no matched operator improves over the identity
    backbone, callers fall back to single-point attribution.
    """
    if failure_memory_store is None:
        return None
    retrieve_operator_specs = getattr(
        failure_memory_store,
        "retrieve_operator_specs",
        None,
    )
    if not callable(retrieve_operator_specs):
        return None
    operator_specs, _source_count = retrieve_operator_specs(
        query,
        max_depth=max_depth,
        top_k=top_k,
        memory_texts=memory_texts,
    )
    if not operator_specs:
        return None

    identity = evaluate_operator_spec(
        client,
        initial_context,
        recall_set,
        OperatorSpec(),
        max_depth=max_depth,
        gold_answer=gold_answer,
        answer_verifier=answer_verifier,
        intervention_config=intervention_config,
    )
    if not identity.successful:
        return None

    best_spec: OperatorSpec | None = None
    best_gain = accept_threshold
    for spec in operator_specs:
        result = evaluate_operator_spec(
            client,
            initial_context,
            recall_set,
            spec,
            max_depth=max_depth,
            gold_answer=gold_answer,
            answer_verifier=answer_verifier,
            intervention_config=intervention_config,
        )
        if not result.successful:
            continue
        net_gain = result.score - identity.score
        if net_gain > best_gain:
            best_gain = net_gain
            best_spec = spec

    if best_spec is None or not best_spec.steps:
        return None
    return _search_result_from_operator_spec(best_spec, best_gain)


def _failure_memory_action_priors(
    failure_memory_store: Any,
    query: str,
    *,
    max_depth: int,
    memory_texts: tuple[str, ...],
) -> dict[int, dict[str, float]] | None:
    if failure_memory_store is None:
        return None
    get_priors = getattr(failure_memory_store, "get_mcts_action_priors", None)
    if not callable(get_priors):
        return None
    return {
        hop: get_priors(
            query,
            hop_index=hop,
            memory_texts=memory_texts,
        )
        for hop in range(1, max_depth + 1)
    }


def _search_result_from_operator_spec(
    operator_spec: OperatorSpec,
    recovery_gain: float,
) -> SearchResult:
    """Project an accepted operator skill into the existing SearchResult shape."""
    action_credits: dict[int, dict[PipelineAction, float]] = {}
    for step in operator_spec.steps:
        credits = action_credits.setdefault(
            step.generation_point,
            {PipelineAction.IDENTITY: 0.0},
        )
        credits[step.action] = recovery_gain

    main = operator_spec.steps[0]
    return SearchResult(
        best_action_sequence=tuple(step.action for step in operator_spec.steps),
        main_culprit=(main.generation_point, main.action, recovery_gain),
        action_credits=action_credits,
        iterations_completed=len(operator_spec.steps),
        nodes_explored=len(operator_spec.steps) + 1,
        terminal_rollouts=1,
        early_stops=1,
        search_time_seconds=0.0,
        avg_rollout_time=0.0,
    )


def _attribution_from_mcts(attribution_result: SearchResult | None) -> AttributionResult | None:
    if attribution_result is None or attribution_result.primary_attribution_label is None:
        return None

    credits: list[tuple[str, float]] = []
    for action_credits in attribution_result.action_credits.values():
        for action, credit in action_credits.items():
            if action == PipelineAction.IDENTITY:
                continue
            credits.append((action.value, credit))
    credits.sort(key=lambda item: item[1], reverse=True)
    if not credits or credits[0][1] <= 0.0:
        return None

    label = credits[0][0]
    top2 = tuple(item[0] for item in credits[:2])
    return AttributionResult(
        predicted_label=label,
        top_replay=label,
        recovery_gain=credits[0][1],
        top2_labels=top2,
        is_ambiguous=len(credits) > 1 and credits[1][1] == credits[0][1],
        top_k_labels=tuple(item[0] for item in credits[:3]),
        close_deltas=tuple((item[0], credits[0][1] - item[1]) for item in credits),
    )


def _attribution_from_structural_gap(case: ProbeCase) -> AttributionResult | None:
    """Fallback attribution for deterministic evidence-boundary gaps.

    Step-level attribution needs a generator/verifier pair to score terminal recovery. In unit and
    trace-only runs, we can still attribute clear retrieval/injection boundary
    failures without falling back to the legacy replay portfolio.
    """
    baseline = case.primary_baseline
    retrieved_ids = frozenset(baseline.retrieved_memory_ids)
    gold_source_ids = frozenset(
        evidence.source_memory_id
        for evidence in case.gold_evidence
        if evidence.source_memory_id
    )

    label = None
    if gold_source_ids and not gold_source_ids.issubset(retrieved_ids):
        label = "retrieval_error"
    elif gold_source_ids:
        memory_by_id = {item.memory_id: item for item in case.extracted_memory}
        retrieved_gold_text = "\n".join(
            memory_by_id[mid].text for mid in gold_source_ids if mid in memory_by_id
        )
        source_score = evidence_recall_from_text(case.gold_evidence, retrieved_gold_text)
        injected_score = evidence_recall_from_text(
            case.gold_evidence, baseline.injected_context
        )
        if source_score > injected_score:
            label = "injection_error"

    if label is None:
        return None

    return AttributionResult(
        predicted_label=label,
        top_replay=label,
        recovery_gain=max(0.0, 1.0 - baseline.evidence_score),
        top2_labels=(label,),
        is_ambiguous=False,
        top_k_labels=(label,),
        close_deltas=((label, 0.0),),
    )


# ── Hook + Repair Integration ──────────────────────────────────────────


def _run_repair(
    case: ProbeCase,
    *,
    adapter,
    hook: bool | str = True,
    fm_context: str = "",
    close_deltas_threshold: float = 0.0,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
    answer_verifier: Any = None,
    on_the_fly_baseline_rescore: bool = False,
    tie_margin: float = 0.0,
    mode: str = "online",
    repair_llm_client=None,
    require_llm_repair_action: bool = False,
    failure_memory_store: FailureMemoryStore | None = None,
) -> AuditResult:
    """Run Pre-CMD Hook + attribution + RepairOrchestrator, folded into one result.

    Populates ``orchestrator_result`` and ``repaired`` on the returned
    :class:`AuditResult`. When the hook skips or attribution fails, returns the
    audit with ``orchestrator_result=None`` and ``repaired=False``.
    """
    from .repair import RepairOrchestrator

    # Step 1: Run hook + attribution
    adapter_name = hook if isinstance(hook, str) else ""
    audit = _run_with_hook(
        case,
        adapter_name=adapter_name,
        mode=mode,
        tie_margin=tie_margin,
        scorer=scorer,
        agent_generate=agent_generate,
        answer_verifier=answer_verifier,
        on_the_fly_baseline_rescore=on_the_fly_baseline_rescore,
        failure_memory_store=failure_memory_store,
    )

    # Step 2: If attribution failed or hook skipped, return early
    if audit.attribution is None:
        return audit

    # Step 3: Run RepairOrchestrator for iterative repair
    from .repair import RepairExecutor

    orchestrator = RepairOrchestrator(
        executor=RepairExecutor(
            llm_client=repair_llm_client,
            require_llm_action=require_llm_repair_action,
        ),
        fm_store=failure_memory_store,
    )
    orch_result = orchestrator.run(
        attribution=audit.attribution,
        case=case,
        adapter=adapter,
        audit_result=audit,
        fm_context=fm_context,
        close_deltas_threshold=close_deltas_threshold,
    )

    return replace(
        audit,
        orchestrator_result=orch_result,
        repaired=orch_result.recovered,
    )


# ── Full Real-Data Suite (issue 0016) ─────────────────────────────────────


def run_real_suite(
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
    """Run the pipeline on all 601 real-data cases and produce artifacts.

    Loads the full 596+5 real probe case suite, runs the pipeline
    (with the Pre-CMD Hook by default), and writes attribution table, comparison
    operator recovery metrics, step-level metrics, and provenance to *out_dir*.
    """
    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)

    cases = load_all_real_cases()
    effective_scorer = evidence_scorer or scorer
    ran_full = not use_hook

    if use_hook:
        results = run_cases(
            cases,
            hook=True,
            scorer=effective_scorer,
            agent_generate=agent_generate,
            answer_verifier=answer_verifier,
            tie_margin=tie_margin,
            on_the_fly_baseline_rescore=on_the_fly_baseline_rescore,
        )
    else:
        results = run_cases(
            cases,
            post_repair=True,
            scorer=effective_scorer,
            evidence_scorer=effective_scorer,
            agent_generate=agent_generate,
            answer_verifier=answer_verifier,
            tie_margin=tie_margin,
            on_the_fly_baseline_rescore=on_the_fly_baseline_rescore,
        )

    att_path = dest / "attribution_table.csv"
    metrics_path = dest / "comparison_metrics.csv"
    provenance_path = dest / "provenance_completeness.csv"

    write_attribution_table(results, att_path)
    write_comparison_metrics_table(results, metrics_path)
    write_step_level_metrics_table(results, dest / "step_level_metrics.csv")
    write_provenance_completeness_summary(results, provenance_path)
    if ran_full:
        write_post_repair_table(results, dest / "post_repair_table.csv")
        try:
            write_repair_success_table_from_full(
                results,
                dest / "repair_success_table.csv",
            )
        except (AttributeError, KeyError, ValueError):
            # Labels without repair-comparison rows still get the
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
