#!/usr/bin/env python3
"""Build issue 0028 hook efficacy supplementary artifacts.

This is inference-only post-processing over an already-produced issue 0023
``at_scale_llm_retest.csv``. It does not train the RPE judge, run replays, or
call LLMs.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cmd_audit.hook import V1_REPLAY_NAME_ORDER, post_retrieve_hook
from cmd_audit.core.models import ProbeCase, RetrievedItem
from cmd_audit.data_io import load_real_cases_by_source


HookDecisionFn = Callable[[str, tuple[RetrievedItem, ...]], tuple[str, ...]]


@dataclass(frozen=True)
class HookEfficacyRow:
    case_id: str
    source: str
    top_replay: str
    selected_replays: tuple[str, ...]
    recall_hit: bool
    cost_reduction: float


def load_retest_by_case(path: str | Path) -> dict[str, list[dict[str, str]]]:
    by_case: dict[str, list[dict[str, str]]] = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"case_id", "replay_name", "recovery_gain"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(
                "retest CSV missing required columns: " + ", ".join(sorted(missing))
            )
        for row in reader:
            by_case[row["case_id"]].append(row)
    return by_case


def load_case_index(input_dir: str | Path) -> dict[str, tuple[str, ProbeCase]]:
    index: dict[str, tuple[str, ProbeCase]] = {}
    for source, cases in load_real_cases_by_source(input_dir).items():
        for case in cases:
            index[case.case_id] = (source, case)
    return index


def build_hook_efficacy_rows(
    retest_by_case: dict[str, list[dict[str, str]]],
    case_index: dict[str, tuple[str, ProbeCase]],
    *,
    decision_fn: HookDecisionFn | None = None,
) -> list[HookEfficacyRow]:
    decide = decision_fn or _selected_replays_from_hook
    rows: list[HookEfficacyRow] = []
    missing: list[str] = []
    total_replays = len(V1_REPLAY_NAME_ORDER)
    for case_id, retest_rows in sorted(retest_by_case.items()):
        case_entry = case_index.get(case_id)
        if case_entry is None:
            missing.append(case_id)
            continue
        source, case = case_entry
        top_replay = max(retest_rows, key=lambda row: float(row["recovery_gain"]))[
            "replay_name"
        ]
        retrieved_items = _retrieved_items(case)
        selected = decide(case.query, retrieved_items)
        cost_reduction = 1.0 - (len(selected) / total_replays)
        rows.append(
            HookEfficacyRow(
                case_id=case_id,
                source=source,
                top_replay=top_replay,
                selected_replays=selected,
                recall_hit=top_replay in selected,
                cost_reduction=cost_reduction,
            )
        )
    if missing:
        preview = ", ".join(missing[:10])
        suffix = "..." if len(missing) > 10 else ""
        raise ValueError(f"retest CSV contains unknown case_ids: {preview}{suffix}")
    return rows


def summarize(rows: list[HookEfficacyRow]) -> dict[str, float]:
    if not rows:
        return {"n": 0.0, "recall": 0.0, "cost_reduction": 0.0}
    return {
        "n": float(len(rows)),
        "recall": sum(row.recall_hit for row in rows) / len(rows),
        "cost_reduction": sum(row.cost_reduction for row in rows) / len(rows),
    }


def write_rows(path: str | Path, rows: list[HookEfficacyRow]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_id",
                "source",
                "top_replay",
                "selected_replays",
                "recall_hit",
                "cost_reduction",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "case_id": row.case_id,
                    "source": row.source,
                    "top_replay": row.top_replay,
                    "selected_replays": "|".join(row.selected_replays),
                    "recall_hit": str(row.recall_hit).lower(),
                    "cost_reduction": f"{row.cost_reduction:.6f}",
                }
            )


def write_summary(path: str | Path, summary: dict[str, float]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "Hook efficacy supplementary summary",
        "",
        "Role: supplementary selector efficiency only; not a paper headline claim.",
        f"n: {int(summary['n'])}",
        f"recall: {summary['recall']:.6f}",
        f"cost_reduction: {summary['cost_reduction']:.6f}",
        "",
    ]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def _selected_replays_from_hook(
    query: str,
    retrieved_items: tuple[RetrievedItem, ...],
) -> tuple[str, ...]:
    return post_retrieve_hook(query, retrieved_items).selected_replays


def _retrieved_items(case: ProbeCase) -> tuple[RetrievedItem, ...]:
    memory_by_id = {item.memory_id: item for item in case.extracted_memory}
    return tuple(
        RetrievedItem(memory_id=mid, text=memory_by_id[mid].text)
        for mid in case.primary_baseline.retrieved_memory_ids
        if mid in memory_by_id
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build issue 0028 hook efficacy supplementary table"
    )
    parser.add_argument("--retest-csv", default="artifacts/at_scale_llm_retest.csv")
    parser.add_argument("--input-dir", default="data/probe_cases")
    parser.add_argument("--out", default="artifacts/hook_efficacy_supplementary.csv")
    parser.add_argument("--summary", default="artifacts/hook_efficacy_summary.txt")
    args = parser.parse_args(argv)

    rows = build_hook_efficacy_rows(
        load_retest_by_case(args.retest_csv),
        load_case_index(args.input_dir),
    )
    write_rows(args.out, rows)
    write_summary(args.summary, summarize(rows))
    print(f"wrote hook efficacy for {len(rows)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
