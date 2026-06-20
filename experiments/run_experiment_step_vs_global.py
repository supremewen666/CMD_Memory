#!/usr/bin/env python3
"""Experiment 1 (UMem 5.2-analogue): step-level vs global-label attribution.

Thesis main table: per-hop credit (CMD step-level) distinguishes "hop2 failed
vs hop2 inherited failure from hop1," which global-label systems cannot
(they only emit a single label for the whole trajectory).

Runs multi-hop cases with per-hop gold fault annotations. Three systems:
  (a) Global-label baselines: evidence_recall, random_label, llm_judge
  (b) CMD step-level attribution: per-hop credit → primary_label + hop_index

Primary metrics:
  - Label correctness (predicted label vs gold step label)
  - Hop localization accuracy (CMD only, since baselines have no hop concept)

Requires a logprob-capable LLM endpoint.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.baselines.comparators import run_baseline_suite
from cmd_audit.core.labels import PIPELINE_STEP_ACTIONS
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.data_io import load_probe_cases
from cmd_audit.eval.writers import write_csv_table
from experiments.experiment_runner_common import (
    assert_g_eval_available,
    build_answer_verifier,
    load_raw_rows,
    run_mcts_for_case,
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "probe_cases"
OUT = ROOT / "artifacts" / "sandbox"


@dataclass
class SystemResult:
    system_name: str
    n_cases: int
    label_correct: int
    hop_correct: int | None  # None for global-label systems (no hop concept)

    @property
    def label_accuracy(self) -> float:
        return self.label_correct / self.n_cases if self.n_cases else 0.0

    @property
    def hop_accuracy(self) -> float | None:
        if self.hop_correct is None:
            return None
        return self.hop_correct / self.n_cases if self.n_cases else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "real_multihop_cases.json"))
    parser.add_argument("--limit", type=int, default=0, help="0 = all cases")
    parser.add_argument("--scorer-mode", default="g-eval-hybrid")
    parser.add_argument("--answer-mode", default="answer-rubric")
    parser.add_argument("--max-retries", type=int, default=1)
    args = parser.parse_args()

    agent_client = LLMClient(LLMClientConfig())
    assert_g_eval_available(agent_client, role="step-vs-global")
    answer_verifier = build_answer_verifier(
        agent_client,
        answer_mode=args.answer_mode,
        max_workers=1,
        max_retries=args.max_retries,
    )

    cases = [
        c
        for c in load_probe_cases(args.cases)
        if c.perturbation_label in PIPELINE_STEP_ACTIONS
    ]
    if args.limit:
        cases = cases[: args.limit]
    expected_hops = _load_expected_hops(args.cases)
    print(f"Loaded {len(cases)} multihop pipeline-action cases from {args.cases}")

    # Run CMD step-level attribution directly, bypassing the
    # hook's Fill/Fix gate. The hook routes retrieval_error cases to Fill (no
    # diagnosis) by design, but this experiment measures attribution over
    # all live step actions, so it must reach Tier 3 for every case.
    cmd_label_correct = 0
    cmd_hop_correct = 0
    # Per-case baseline predictions, keyed by comparator name.
    baseline_correct: dict[str, int] = {
        "evidence_recall": 0,
        "random_label": 0,
        "llm_judge": 0,
    }
    for case in cases:
        search = run_mcts_for_case(
            case,
            agent_client,
            answer_verifier,
            max_iterations=40,
        )
        if search.main_culprit:
            generation_point, action, _credit = search.main_culprit
            pred_label = getattr(action, "value", str(action))
            if pred_label == case.perturbation_label:
                cmd_label_correct += 1
            # generation_point is 0-based; the data's
            # expected_fault.hop_index is 1-based. The convention in
            # get_legal_actions (actions.py) is hop == generation_point + 1.
            expected_hop = expected_hops.get(case.case_id)
            if expected_hop is not None and generation_point + 1 == expected_hop:
                cmd_hop_correct += 1

        # Global-label baselines run independently of the hook/attribution path.
        suite = run_baseline_suite(case, llm_client=agent_client)
        for comparator in suite.comparator_results:
            name = comparator.comparator_name
            if name in baseline_correct and comparator.predicted_label == case.perturbation_label:
                baseline_correct[name] += 1

    cmd_result = SystemResult(
        "CMD-step-level", len(cases), cmd_label_correct, cmd_hop_correct
    )

    global_results = [
        SystemResult(name, len(cases), baseline_correct[name], hop_correct=None)
        for name in ("evidence_recall", "random_label", "llm_judge")
    ]

    print(f"\n{'System':30s} {'Label Acc':>10s}  {'Hop Acc':>10s}")
    print("-" * 52)
    print(
        f"{cmd_result.system_name:30s} {cmd_result.label_accuracy:>10.4f}  "
        f"{cmd_result.hop_accuracy or 0.0:>10.4f}"
    )
    for gr in global_results:
        print(
            f"{gr.system_name:30s} {gr.label_accuracy:>10.4f}  "
            f"{'N/A':>10s}  (no hop concept)"
        )

    OUT.mkdir(parents=True, exist_ok=True)
    out_path = OUT / "experiment_step_vs_global.csv"
    rows = [
        {
            "system_name": cmd_result.system_name,
            "label_accuracy": f"{cmd_result.label_accuracy:.4f}",
            "hop_accuracy": f"{cmd_result.hop_accuracy:.4f}"
            if cmd_result.hop_accuracy is not None
            else "N/A",
            "n_cases": cmd_result.n_cases,
        }
    ]
    for gr in global_results:
        rows.append(
            {
                "system_name": gr.system_name,
                "label_accuracy": f"{gr.label_accuracy:.4f}",
                "hop_accuracy": "N/A",
                "n_cases": gr.n_cases,
            }
        )
    write_csv_table(
        out_path, ["system_name", "label_accuracy", "hop_accuracy", "n_cases"], rows
    )
    print(f"\nWrote {out_path}")


def _load_expected_hops(path: str) -> dict[str, int]:
    """Load expected fault hop_index metadata from the multihop JSON."""
    out: dict[str, int] = {}
    for row in load_raw_rows(path):
        expected = row.get("expected_fault") or {}
        hop = expected.get("hop_index")
        if hop is not None:
            out[str(row["case_id"])] = int(hop)
    return out


if __name__ == "__main__":
    main()
