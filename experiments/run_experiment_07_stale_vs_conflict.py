#!/usr/bin/env python3
"""Experiment 7: stale vs conflict timestamp-direction separation."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.data_io import load_probe_cases
from cmd_audit.eval.writers import write_csv_table
from cmd_audit.item_gate import ItemGateStatus, run_item_gate
from experiments.experiment_runner_common import DATA, OUT, target_item_for_case
from experiments.experiment_runner_common import assert_g_eval_available


TARGET_LABELS = ("item_stale", "item_conflict")
STATUS_TO_LABEL = {
    ItemGateStatus.ITEM_STALE: "item_stale",
    ItemGateStatus.ITEM_CONFLICT: "item_conflict",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "real_three_source_cases.json"))
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    client = LLMClient(LLMClientConfig())
    assert_g_eval_available(client, role="stale-vs-conflict")
    cases = [
        case
        for case in load_probe_cases(args.cases)
        if case.perturbation_label in TARGET_LABELS
    ]
    if args.limit:
        cases = cases[: args.limit]

    counts: Counter[tuple[str, str]] = Counter()
    for case in cases:
        target = target_item_for_case(case)
        predicted = ""
        if target is not None:
            result = run_item_gate(
                client,
                target,
                case.extracted_memory,
                case.query,
                enable_collision=True,
                enable_loo=False,
            )
            predicted = STATUS_TO_LABEL.get(result.status, "")
        counts[(case.perturbation_label, predicted)] += 1

    out_rows = [
        {
            "gold_label": gold,
            "predicted_label": predicted,
            "count": str(counts[(gold, predicted)]),
        }
        for gold in TARGET_LABELS
        for predicted in TARGET_LABELS
    ]
    for row in out_rows:
        print(row)

    out_path = OUT / "experiment_stale_vs_conflict.csv"
    write_csv_table(out_path, ["gold_label", "predicted_label", "count"], out_rows)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
