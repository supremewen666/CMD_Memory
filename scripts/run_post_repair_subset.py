#!/usr/bin/env python3
"""Run Post-Repair Context Replay on a non-11-label subset.

The default subset is the issue 0027 repair-focused 4-label set:
retrieval/compression/premature_extraction/reasoning.  Use ``--label-set
headline-8`` for the Decision 34 headline labels while still excluding the
V1 completeness-only labels granularity/graph/safety.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))

from cmd_audit.harness import (
    run_case_full_v1,
    write_repair_success_table_from_full,
)
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.core.models import ProbeCase, load_real_cases_by_source
from cmd_audit.writers import write_post_repair_table, write_text_artifact
from scripts.run_at_scale_llm_retest import (
    build_agent_generate,
    build_answer_verifier,
    build_evidence_scorer,
    load_case_ids,
)


REPAIR_4_LABELS = (
    "retrieval_error",
    "compression_error",
    "premature_extraction_error",
    "reasoning_error",
)

HEADLINE_8_LABELS = (
    "write_error",
    "compression_error",
    "premature_extraction_error",
    "retrieval_error",
    "injection_error",
    "reasoning_error",
    "route_error",
    "ingestion_error",
)


def load_filtered_cases(
    input_dir: str | Path,
    *,
    labels: set[str],
    case_ids: set[str] | None = None,
    max_cases: int | None = None,
) -> list[ProbeCase]:
    cases: list[ProbeCase] = []
    for source, source_cases in load_real_cases_by_source(input_dir).items():
        if source == "null_label":
            continue
        for case in source_cases:
            if case.perturbation_label not in labels:
                continue
            if case_ids is not None and case.case_id not in case_ids:
                continue
            cases.append(case)
    return cases[:max_cases] if max_cases is not None else cases


def parse_labels(args) -> set[str]:
    if args.labels:
        return {label.strip() for label in args.labels.split(",") if label.strip()}
    if args.label_set == "repair-4":
        return set(REPAIR_4_LABELS)
    if args.label_set == "headline-8":
        return set(HEADLINE_8_LABELS)
    raise ValueError(f"unknown label set: {args.label_set}")


def write_summary(path: str | Path, full_results, labels: set[str]) -> None:
    total = len(full_results)
    recovered = sum(
        result.post_repair.repair_assessment == "recovered"
        for result in full_results
    )
    partial = sum(
        result.post_repair.repair_assessment == "partial"
        for result in full_results
    )
    failed = sum(
        result.post_repair.repair_assessment == "failed"
        for result in full_results
    )
    lines = [
        "Post-Repair subset summary",
        "",
        "label_scope: " + ",".join(sorted(labels)),
        f"n: {total}",
        f"recovered: {recovered}",
        f"partial: {partial}",
        f"failed: {failed}",
        f"recovered_rate: {recovered / total:.6f}" if total else "recovered_rate: 0.000000",
        "",
        "Note: granularity_error / graph_error / safety_error are excluded unless "
        "explicitly requested via --labels.",
    ]
    write_text_artifact(path, lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run LLM-stack Post-Repair Context Replay on a label subset"
    )
    parser.add_argument("--input-dir", default="data/probe_cases")
    parser.add_argument("--out-dir", default="artifacts/post_repair_subset")
    parser.add_argument(
        "--label-set",
        choices=("repair-4", "headline-8"),
        default="repair-4",
    )
    parser.add_argument(
        "--labels",
        default=None,
        help="comma-separated explicit labels; overrides --label-set",
    )
    parser.add_argument(
        "--case-ids",
        default=None,
        help="optional JSON/TXT case-id list, e.g. experiment_01_inspected_ecs.json",
    )
    parser.add_argument("--agent-base-url", default=None)
    parser.add_argument("--agent-model", default=None)
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
    )
    parser.add_argument("--tie-margin", type=float, default=0.0)
    parser.add_argument("--top-k", type=int, default=2)
    parser.add_argument("--partial-threshold", type=float, default=0.5)
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    labels = parse_labels(args)
    case_ids = load_case_ids(args.case_ids) if args.case_ids else None
    cases = load_filtered_cases(
        args.input_dir,
        labels=labels,
        case_ids=case_ids,
        max_cases=args.max_cases,
    )

    env_defaults = LLMClientConfig()
    agent_base_url = args.agent_base_url or env_defaults.base_url
    agent_model = args.agent_model or env_defaults.model
    evaluator_base_url = args.evaluator_base_url or agent_base_url
    evaluator_model = args.evaluator_model or agent_model
    verifier_base_url = args.verifier_base_url or evaluator_base_url
    verifier_model = args.verifier_model or evaluator_model

    if args.dry_run:
        print(f"cases={len(cases)}")
        print("labels=" + ",".join(sorted(labels)))
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

    full_results = []
    for index, case in enumerate(cases, start=1):
        result = run_case_full_v1(
            case,
            top_k=args.top_k,
            tie_margin=args.tie_margin,
            scorer=scorer,
            evidence_scorer=scorer,
            agent_generate=agent_generate,
            answer_verifier=answer_verifier,
            partial_threshold=args.partial_threshold,
            on_the_fly_baseline_rescore=True,
        )
        full_results.append(result)
        print(
            f"[{index}/{len(cases)}] {case.case_id} "
            f"{case.perturbation_label} -> {result.post_repair.repair_assessment}"
        )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_post_repair_table(full_results, out_dir / "post_repair_table.csv")
    write_summary(out_dir / "post_repair_summary.txt", full_results, labels)
    try:
        write_repair_success_table_from_full(
            full_results,
            out_dir / "repair_success_table.csv",
        )
    except (AttributeError, KeyError, ValueError):
        pass
    print(f"wrote Post-Repair subset artifacts to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
