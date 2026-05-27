#!/usr/bin/env python3
"""Run issue 0023 at-scale LLM retest and write the long replay CSV.

This is the paper-grade entry point for the Decision 34 LLM stack.  It runs all
10 V1 replays without the hook, uses an OpenAI-compatible agent endpoint plus
LLM evidence/answer verifiers, rescoring the baseline on the fly so replay
recovery gains and baseline scores share the same evaluator.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))

from cmd_audit.attribution import assign_attribution_v1
from cmd_audit.harness import (
    AuditResult,
    _apply_dual_axis_recovery_gain,
    _score_baseline_with_agent,
    write_attribution_table,
    write_comparison_metrics_table,
    write_confusion_matrix_table,
)
from cmd_audit.llm_client import LLMClient, LLMClientConfig
from cmd_audit.llm_scoring import (
    AnswerVerifier,
    RUBRIC_MAX_SCORE,
    RubricScorer,
    SubagentScorer,
    _continuous_verify,
)
from cmd_audit.models import GoldEvidence, ProbeCase, load_real_cases_by_source
from cmd_audit.provenance import ProvenanceTracker, get_graph_distractor_edges
from cmd_audit.replays import ReplayResult, run_v1_replay_portfolio
from baselines.comparators import run_baseline_suite
from scripts.write_at_scale_retest_run_meta import build_run_meta, write_run_meta


AGENT_SYSTEM_PROMPT = """\
You are a memory-augmented QA agent. Answer the user query using only the
provided context. If the context contains enough information, give the concise
answer. Do not mention hidden labels or the evaluation protocol."""


@dataclass(frozen=True)
class RetestCaseResult:
    source: str
    case: ProbeCase
    audit: AuditResult
    elapsed_seconds: float
    failure_reason: str


def build_agent_generate(client: LLMClient):
    def agent_generate(query: str, context: str) -> str:
        prompt = "\n\n".join(
            (
                "CONTEXT:",
                context or "(empty)",
                "QUERY:",
                query,
                "ANSWER:",
            )
        )
        return client.generate(prompt, system=AGENT_SYSTEM_PROMPT)

    return agent_generate


def build_evidence_scorer(
    client: LLMClient,
    *,
    scorer_mode: str,
    max_workers: int,
    max_retries: int,
):
    if scorer_mode == "g-eval-strict":
        return _build_strict_g_eval_scorer(client)
    if scorer_mode == "binary":
        return SubagentScorer(
            client,
            max_workers=max_workers,
            max_retries=max_retries,
        )
    rubric = RubricScorer(
        client,
        max_workers=max_workers,
        max_retries=max_retries,
    )
    if scorer_mode == "rubric":
        return rubric
    if scorer_mode in {"g-eval", "g-eval-hybrid", "rubric-continuous"}:
        return rubric.score_continuous
    raise ValueError(f"unknown scorer mode: {scorer_mode}")


def build_answer_verifier(
    client: LLMClient,
    *,
    answer_mode: str,
    max_workers: int,
    max_retries: int,
):
    if answer_mode == "g-eval-strict":
        return _build_rubric_answer_callable(_build_strict_g_eval_scorer(client))
    if answer_mode == "binary":
        return AnswerVerifier(client, max_retries=max_retries)
    rubric = RubricScorer(
        client,
        max_workers=max_workers,
        max_retries=max_retries,
    )
    if answer_mode == "rubric":
        return _build_rubric_answer_callable(rubric)
    if answer_mode in {"g-eval", "g-eval-hybrid", "rubric-continuous"}:
        return _build_rubric_answer_callable(rubric.score_continuous)
    raise ValueError(f"unknown answer mode: {answer_mode}")


def _build_rubric_answer_callable(scorer):
    def score_answer(answer: str, gold_answer: str) -> float:
        evidence = (
            GoldEvidence(
                evidence_id="gold_answer",
                text=gold_answer,
                source_memory_id=None,
            ),
        )
        return float(scorer(evidence, answer))

    return score_answer


def _build_strict_g_eval_scorer(client: LLMClient):
    def score(gold_evidence, text: str) -> float:
        if not gold_evidence or not text:
            return 0.0
        scores: list[float] = []
        for evidence in gold_evidence:
            expected = _continuous_verify(client, evidence.text, text)
            if expected is None:
                raise RuntimeError(
                    "G-Eval logprob scoring failed: endpoint did not return "
                    "parseable score-token top_logprobs. Check vLLM logprobs "
                    "support or run with --scorer-mode rubric-continuous to "
                    "allow fallback."
                )
            scores.append(expected / RUBRIC_MAX_SCORE)
        return sum(scores) / len(scores)

    return score


def assert_g_eval_available(client: LLMClient, *, role: str) -> None:
    expected = _continuous_verify(
        client,
        "Paris is in France.",
        "Paris is in France.",
    )
    if expected is None:
        raise RuntimeError(
            f"{role} endpoint did not return parseable G-Eval logprobs. "
            "Do not run paper retest in this state."
        )


def load_labeled_cases(
    input_dir: str | Path,
    *,
    case_ids: set[str] | None = None,
) -> list[tuple[str, ProbeCase]]:
    rows: list[tuple[str, ProbeCase]] = []
    for source, cases in load_real_cases_by_source(input_dir).items():
        if source == "null_label":
            continue
        for case in cases:
            if case.perturbation_label is not None and (
                case_ids is None or case.case_id in case_ids
            ):
                rows.append((source, case))
    return rows


def load_case_ids(path: str | Path) -> set[str]:
    text = Path(path).read_text(encoding="utf-8")
    if Path(path).suffix == ".json":
        payload = json.loads(text)
        rows = payload.get("cases", []) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ValueError("--case-ids JSON must contain an array or cases array")
        return {
            str(row["case_id"] if isinstance(row, dict) else row)
            for row in rows
            if (isinstance(row, dict) and row.get("case_id")) or isinstance(row, str)
        }
    return {line.strip() for line in text.splitlines() if line.strip()}


def run_one_case(
    source: str,
    case: ProbeCase,
    *,
    scorer: Any,
    agent_generate,
    answer_verifier: Any,
    tie_margin: float,
    top_k: int,
) -> RetestCaseResult:
    start = time.perf_counter()
    baseline_suite = run_baseline_suite(case)
    baseline = case.primary_baseline
    tracker = ProvenanceTracker(case.case_id)
    baseline_evidence_score_llm, baseline_answer_score_llm = _score_baseline_with_agent(
        case,
        agent_generate=agent_generate,
        scorer=scorer,
        answer_verifier=answer_verifier,
        enabled=True,
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

    distractor_edges = ()
    graph_off_replay = _find_replay(replays, "graph_off")
    if graph_off_replay is not None:
        distractor_edges = get_graph_distractor_edges(case, graph_off_replay)

    attribution = None
    failure_reason = ""
    try:
        attribution = assign_attribution_v1(
            replays,
            has_ingestion_trace=case.has_ingestion_trace,
            positive_gain_threshold=0.0,
            tie_margin=tie_margin,
            top_k=top_k,
            distractor_edges=distractor_edges,
        )
    except ValueError:
        top_gain = max((replay.recovery_gain for replay in replays), default=0.0)
        failure_reason = "zero_gain" if top_gain == 0.0 else "negative_gain"

    audit = AuditResult(
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
    return RetestCaseResult(
        source=source,
        case=case,
        audit=audit,
        elapsed_seconds=time.perf_counter() - start,
        failure_reason=failure_reason,
    )


def write_retest_csv(path: str | Path, results: list[RetestCaseResult]) -> None:
    fieldnames = [
        "case_id",
        "source",
        "gold_label",
        "replay_name",
        "recovery_gain",
        "evidence_score",
        "answer_score",
        "baseline_evidence_score_llm",
        "baseline_answer_score_llm",
        "predicted_label",
        "top_replay",
        "top2_labels",
        "top_k_labels",
        "attribution_failed",
        "failure_reason",
        "elapsed_seconds",
        "answer",
    ]
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            attribution = result.audit.attribution
            for replay in result.audit.replays:
                writer.writerow(
                    {
                        "case_id": result.case.case_id,
                        "source": result.source,
                        "gold_label": result.case.perturbation_label or "",
                        "replay_name": replay.replay_name,
                        "recovery_gain": f"{replay.recovery_gain:.6f}",
                        "evidence_score": f"{replay.evidence_score:.6f}",
                        "answer_score": f"{replay.answer_score:.6f}",
                        "baseline_evidence_score_llm": _fmt_optional(
                            result.audit.baseline_evidence_score_llm
                        ),
                        "baseline_answer_score_llm": _fmt_optional(
                            result.audit.baseline_answer_score_llm
                        ),
                        "predicted_label": (
                            attribution.predicted_label if attribution else ""
                        ),
                        "top_replay": attribution.top_replay if attribution else "",
                        "top2_labels": (
                            "|".join(attribution.top2_labels) if attribution else ""
                        ),
                        "top_k_labels": (
                            "|".join(attribution.top_k_labels) if attribution else ""
                        ),
                        "attribution_failed": "1" if attribution is None else "0",
                        "failure_reason": result.failure_reason,
                        "elapsed_seconds": f"{result.elapsed_seconds:.6f}",
                        "answer": replay.answer,
                    }
                )


def _find_replay(
    replays: tuple[ReplayResult, ...], replay_name: str
) -> ReplayResult | None:
    for replay in replays:
        if replay.replay_name == replay_name:
            return replay
    return None


def _fmt_optional(value: float | None) -> str:
    return "" if value is None else f"{value:.6f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run issue 0023 LLM retest")
    parser.add_argument("--input-dir", default="data/probe_cases")
    parser.add_argument("--out-dir", default="artifacts/issue_0023_llm_retest")
    parser.add_argument("--retest-csv", default="artifacts/at_scale_llm_retest.csv")
    parser.add_argument(
        "--run-meta", default="artifacts/at_scale_llm_retest.run_meta.txt"
    )
    parser.add_argument(
        "--agent-base-url",
        default=None,
        help="OpenAI-compatible /v1 endpoint; defaults to LLM_BASE_URL",
    )
    parser.add_argument("--agent-model", default=None, help="defaults to LLM_MODEL")
    parser.add_argument("--evaluator-base-url", default=None)
    parser.add_argument("--evaluator-model", default=None)
    parser.add_argument("--verifier-base-url", default=None)
    parser.add_argument("--verifier-model", default=None)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument(
        "--scorer-mode",
        choices=(
            "g-eval",
            "g-eval-hybrid",
            "g-eval-strict",
            "binary",
            "rubric",
            "rubric-continuous",
        ),
        default="g-eval-hybrid",
        help=(
            "evidence scorer for recovery_gain. g-eval/g-eval-hybrid use "
            "score-token top_logprobs when parseable and fall back to discrete "
            "rubric JSON; g-eval-strict aborts unless logprobs are parseable; "
            "binary reproduces PRESENT/ABSENT; rubric is discrete 0..4/4"
        ),
    )
    parser.add_argument(
        "--answer-mode",
        choices=(
            "g-eval",
            "g-eval-hybrid",
            "g-eval-strict",
            "binary",
            "rubric",
            "rubric-continuous",
        ),
        default="g-eval-hybrid",
        help=(
            "answer-axis scorer for baseline_answer_score_llm and "
            "evidence_given_reasoning. g-eval/g-eval-hybrid keep the "
            "answer-axis continuous with discrete-rubric fallback."
        ),
    )
    parser.add_argument("--tie-margin", type=float, default=0.0)
    parser.add_argument("--top-k", type=int, default=2)
    parser.add_argument(
        "--case-ids",
        default=None,
        help="optional JSON/TXT case-id list; JSON may be researcher_labeled_subset.json",
    )
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    env_defaults = LLMClientConfig()
    agent_base_url = args.agent_base_url or env_defaults.base_url
    agent_model = args.agent_model or env_defaults.model
    evaluator_base_url = args.evaluator_base_url or agent_base_url
    evaluator_model = args.evaluator_model or agent_model
    verifier_base_url = args.verifier_base_url or evaluator_base_url
    verifier_model = args.verifier_model or evaluator_model

    case_ids = load_case_ids(args.case_ids) if args.case_ids else None
    rows = load_labeled_cases(args.input_dir, case_ids=case_ids)
    if args.max_cases is not None:
        rows = rows[: args.max_cases]
    if args.dry_run:
        print(f"cases={len(rows)}")
        print(f"agent={agent_model} @ {agent_base_url}")
        print(f"evaluator={evaluator_model} @ {evaluator_base_url}")
        print(f"verifier={verifier_model} @ {verifier_base_url}")
        print(f"scorer_mode={args.scorer_mode}")
        print(f"answer_mode={args.answer_mode}")
        return 0

    agent_client = LLMClient(
        LLMClientConfig(
            base_url=agent_base_url,
            model=agent_model,
            timeout_seconds=args.timeout,
            max_retries=args.max_retries,
            temperature=0.0,
        )
    )
    evaluator_client = LLMClient(
        LLMClientConfig(
            base_url=evaluator_base_url,
            model=evaluator_model,
            timeout_seconds=args.timeout,
            max_retries=args.max_retries,
            temperature=0.0,
        )
    )
    verifier_client = LLMClient(
        LLMClientConfig(
            base_url=verifier_base_url,
            model=verifier_model,
            timeout_seconds=args.timeout,
            max_retries=args.max_retries,
            temperature=0.0,
        )
    )
    agent_generate = build_agent_generate(agent_client)
    scorer = build_evidence_scorer(
        evaluator_client,
        scorer_mode=args.scorer_mode,
        max_workers=args.max_workers,
        max_retries=args.max_retries,
    )
    answer_verifier = build_answer_verifier(
        verifier_client,
        answer_mode=args.answer_mode,
        max_workers=args.max_workers,
        max_retries=args.max_retries,
    )
    if args.scorer_mode == "g-eval-strict":
        assert_g_eval_available(evaluator_client, role="evaluator")
    if args.answer_mode == "g-eval-strict":
        assert_g_eval_available(verifier_client, role="verifier")

    results: list[RetestCaseResult] = []
    for index, (source, case) in enumerate(rows, start=1):
        result = run_one_case(
            source,
            case,
            scorer=scorer,
            agent_generate=agent_generate,
            answer_verifier=answer_verifier,
            tie_margin=args.tie_margin,
            top_k=args.top_k,
        )
        results.append(result)
        status = (
            result.audit.attribution.predicted_label
            if result.audit.attribution is not None
            else f"AttributionFailed/{result.failure_reason}"
        )
        print(f"[{index}/{len(rows)}] {case.case_id} {status}")

    audits = [result.audit for result in results]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_retest_csv(args.retest_csv, results)
    write_attribution_table(audits, out_dir / "attribution_table.csv")
    write_comparison_metrics_table(audits, out_dir / "comparison_metrics.csv")
    write_confusion_matrix_table(audits, out_dir / "attribution_confusion_matrix.csv")
    write_run_meta(
        args.run_meta,
        build_run_meta(
            agent_model=agent_model,
            evaluator_model=evaluator_model,
            verifier_model=verifier_model,
            agent_endpoint=agent_base_url,
            evaluator_endpoint=evaluator_base_url,
            temperature=0.0,
            tie_margin=args.tie_margin,
            use_hook=False,
            on_the_fly_baseline_rescore=True,
            random_state=42,
        ),
    )
    print(f"wrote {len(results)} cases to {args.retest_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
