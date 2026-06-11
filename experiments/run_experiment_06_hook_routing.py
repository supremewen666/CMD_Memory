#!/usr/bin/env python3
"""Experiment 6: hook Fill/Fix routing confusion matrix."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.eval.writers import write_csv_table
from cmd_audit.harness import _as_retrieved_items, _retrieved_memory_items
from cmd_audit.hook import post_retrieve_hook
from experiments.experiment_runner_common import DATA, OUT, load_cases_with_raw


FORMATION_LABELS = {
    "write_error",
    "compression_error",
    "premature_extraction_error",
    "ingestion_error",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DATA / "real_three_source_cases.json"))
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    rows = load_cases_with_raw(args.cases)
    if args.limit:
        rows = rows[: args.limit]
    counts: Counter[tuple[str, str]] = Counter()
    for entry in rows:
        raw_label = entry.raw.get("perturbation_label")
        gold_branch = "fill" if raw_label in FORMATION_LABELS else "fix"
        recall_set = _retrieved_memory_items(entry.case)
        decision = post_retrieve_hook(
            entry.case.query,
            _as_retrieved_items(recall_set),
        )
        counts[(gold_branch, decision.branch)] += 1

    out_rows = [
        {
            "gold_branch": gold,
            "predicted_branch": predicted,
            "count": str(counts[(gold, predicted)]),
        }
        for gold in ("fill", "fix")
        for predicted in ("fill", "fix")
    ]
    for row in out_rows:
        print(row)

    out_path = OUT / "experiment_hook_routing.csv"
    write_csv_table(out_path, ["gold_branch", "predicted_branch", "count"], out_rows)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
