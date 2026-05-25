#!/usr/bin/env python3
"""Batch Mem0Trace generation + mem0 adapter run on all 596 cases.

Zero LLM, zero new deps.  Everything sourced from existing ProbeCase fields:

  add_inputs     = extracted_memory[*].text
  search_query   = case.query
  search_results = primary_baseline.retrieved_memory_ids -> MemoryItems
  store_checksum = SHA-256(sort(add_inputs))
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.adapters.base import Mem0Trace
from cmd_audit.adapters.harness import run_case_with_mem0
from cmd_audit.harness import run_case_v1
from cmd_audit.models import load_probe_cases_v1

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "probe_cases"
TRACES_OUT = ROOT / "data" / "probe_cases" / "mem0_596_traces.json"
PARITY_OUT = ROOT / "artifacts" / "mem0_adapter_parity.csv"

DATASETS = [
    ("real_memoryarena_cases.json", "MemoryArena"),
    ("real_longmemeval_cases.json", "LongMemEval"),
    ("real_toolbench_cases.json", "ToolBench"),
]


def build_mem0_trace(case) -> Mem0Trace:
    """Map ProbeCase -> Mem0Trace using existing data only."""
    memory_by_id = {item.memory_id: item for item in case.extracted_memory}

    # add_inputs: what the memory system stored (= extracted_memory texts)
    add_inputs = tuple(item.text for item in case.extracted_memory)

    # search_results: what the baseline retrieved
    baseline = case.primary_baseline
    search_results = tuple(
        memory_by_id[mid]
        for mid in baseline.retrieved_memory_ids
        if mid in memory_by_id
    )

    # SHA-256 checksum
    sorted_facts = sorted(add_inputs)
    checksum = hashlib.sha256("|".join(sorted_facts).encode()).hexdigest()

    return Mem0Trace(
        case_id=case.case_id,
        add_inputs=add_inputs,
        search_query=case.query,
        search_results=search_results,
        store_checksum=checksum,
    )


def main() -> None:
    all_traces: dict[str, dict] = {}
    all_parity_rows: list[dict] = []

    for fname, label in DATASETS:
        cases = load_probe_cases_v1(str(DATA / fname))
        print(f"{'='*60}")
        print(f"  {label}: {len(cases)} cases")
        print(f"{'='*60}")

        # Build traces
        traces: dict[str, Mem0Trace] = {}
        for case in cases:
            trace = build_mem0_trace(case)
            traces[case.case_id] = trace
            all_traces[case.case_id] = {
                "case_id": case.case_id,
                "add_inputs": list(trace.add_inputs),
                "search_query": trace.search_query,
                "search_results": [
                    {
                        "memory_id": item.memory_id,
                        "text": item.text,
                        "source_event_ids": list(item.source_event_ids),
                        "store": item.store,
                        "is_graph_expanded": item.is_graph_expanded,
                    }
                    for item in trace.search_results
                ],
                "store_checksum": trace.store_checksum,
            }

        # ── Run both paths ──
        total = len(cases)
        standalone_correct = 0
        mem0_correct = 0
        parity_match = 0
        top2_overlap = 0

        for i, case in enumerate(cases):
            trace_obj = traces[case.case_id]

            # Path A: standalone
            s_result = run_case_v1(case)
            s_label = s_result.attribution.predicted_label
            s_top2 = set(s_result.attribution.top2_labels)
            s_correct = s_label == case.perturbation_label

            # Path B: mem0 adapter
            m_result = run_case_with_mem0(case, trace_obj)
            m_label = m_result.attribution.predicted_label
            m_top2 = set(m_result.attribution.top2_labels)
            m_correct = m_label == case.perturbation_label

            if s_correct:
                standalone_correct += 1
            if m_correct:
                mem0_correct += 1
            if s_label == m_label:
                parity_match += 1
            if s_top2 & m_top2:
                top2_overlap += 1

            all_parity_rows.append({
                "case_id": case.case_id,
                "dataset": label,
                "perturbation_label": case.perturbation_label,
                "standalone_label": s_label,
                "mem0_label": m_label,
                "standalone_correct": s_correct,
                "mem0_correct": m_correct,
                "label_match": s_label == m_label,
                "top2_overlap": bool(s_top2 & m_top2),
                "standalone_top2": "|".join(sorted(s_top2)),
                "mem0_top2": "|".join(sorted(m_top2)),
            })

            if (i + 1) % 50 == 0:
                print(f"  [{i+1}/{total}] "
                      f"parity={parity_match}/{i+1} "
                      f"s_acc={standalone_correct/(i+1):.3f} "
                      f"m_acc={mem0_correct/(i+1):.3f}")

        # Per-dataset summary
        print(f"\n  --- {label} Summary ---")
        print(f"  Standalone accuracy:     {standalone_correct}/{total} = {standalone_correct/total:.4f}")
        print(f"  Mem0 adapter accuracy:   {mem0_correct}/{total} = {mem0_correct/total:.4f}")
        print(f"  Label parity (top-1):    {parity_match}/{total} = {parity_match/total:.4f}")
        print(f"  Top-2 overlap:           {top2_overlap}/{total} = {top2_overlap/total:.4f}")

        # Per-label parity
        per_label: dict[str, dict] = {}
        for row in all_parity_rows[-total:]:
            lb = row["perturbation_label"]
            per_label.setdefault(lb, {"t": 0, "m": 0, "p": 0, "t2": 0})
            per_label[lb]["t"] += 1
            if row["label_match"]:
                per_label[lb]["p"] += 1
            if row["top2_overlap"]:
                per_label[lb]["t2"] += 1
        print(f"\n  Per-label parity:")
        for lb in sorted(per_label):
            d = per_label[lb]
            print(f"    {lb:28s} par={d['p']}/{d['t']} ({d['p']/d['t']:.3f})  "
                  f"top2={d['t2']}/{d['t']} ({d['t2']/d['t']:.3f})")
        print()

    # ── Write traces ──
    with open(TRACES_OUT, "w") as fh:
        json.dump(list(all_traces.values()), fh, indent=2, ensure_ascii=False)
    print(f"Traces written: {TRACES_OUT} ({len(all_traces)} entries)")

    # ── Write parity CSV ──
    import csv
    with open(PARITY_OUT, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(all_parity_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_parity_rows)
    print(f"Parity table:   {PARITY_OUT}")

    # ── Combined summary ──
    total_all = len(all_parity_rows)
    par_all = sum(1 for r in all_parity_rows if r["label_match"])
    t2_all = sum(1 for r in all_parity_rows if r["top2_overlap"])
    s_all = sum(1 for r in all_parity_rows if r["standalone_correct"])
    m_all = sum(1 for r in all_parity_rows if r["mem0_correct"])
    print(f"\n{'='*60}")
    print(f"  COMBINED: {total_all} cases")
    print(f"{'='*60}")
    print(f"  Standalone accuracy:     {s_all}/{total_all} = {s_all/total_all:.4f}")
    print(f"  Mem0 adapter accuracy:   {m_all}/{total_all} = {m_all/total_all:.4f}")
    print(f"  Label parity (top-1):    {par_all}/{total_all} = {par_all/total_all:.4f}")
    print(f"  Top-2 overlap:           {t2_all}/{total_all} = {t2_all/total_all:.4f}")

    # Mismatch analysis
    mismatches = [r for r in all_parity_rows if not r["label_match"]]
    if mismatches:
        print(f"\n  === Mismatch cases ({len(mismatches)}) ===")
        for r in mismatches[:20]:
            print(f"  {r['case_id']}: {r['perturbation_label']} -> "
                  f"S={r['standalone_label']} M={r['mem0_label']} "
                  f"S_correct={r['standalone_correct']} M_correct={r['mem0_correct']}")
    else:
        print(f"\n  No mismatches. Full parity.")


if __name__ == "__main__":
    main()
