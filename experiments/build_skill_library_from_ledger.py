#!/usr/bin/env python3
"""Build the two-tier Failure Memory skill library from Exp18's recovered ledger.

Route C (see session decision): the tier-2 abstraction needs a *recovery* fitness
signal, not gold labels. Exp18 (``run_experiment_18_failure_memory_trajectory``)
already scored all 600 recurrent cases with real G-Eval and recorded, for every
recovered case, the action that actually recovered it plus its ledger fields. This
script replays those recovered ledgers in streaming (case_index) order through
``FailureMemorySkillLoop`` so the deterministic ``format_pattern`` template
distills one reusable pattern per step action -- no LLM endpoint required.

This is a mechanics-validation build: it proves the Hermes tier-2 wiring
(case ledger -> pattern -> self-check -> markdown) produces a non-empty skill
library and seed-reusable ``(gen_point, action)`` pairs. It does NOT measure
whether tier-2 patterns accelerate recurrent recovery (that is the live Exp19
job under real G-Eval).

Retrieval key follows the current code as-is (option 甲): keyword-signature
similarity over the case query. Wiring ``recurrent_family_id`` into the key is a
separate, testable step.

Usage:
    python -m experiments.build_skill_library_from_ledger \
        --ledger artifacts/sandbox/failure_memory_trajectory_detail.csv \
        --cases data/probe_cases/real_recurrent_cases.json \
        --out artifacts/sandbox/failure_memory_skill
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.labels import PIPELINE_STEP_ACTIONS
from cmd_audit.data_io import load_probe_cases
from cmd_audit.repair.failure_memory import (
    FailureMemorySkillLoop,
    MarkdownFailureMemoryStore,
)

_GEN_POINT_RE = re.compile(r"generation point (\d+)")


def _load_recovered_ledgers(path: Path) -> list[dict[str, str]]:
    """Return Exp18 detail rows that recovered, in streaming (case_index) order."""
    rows = [
        r
        for r in csv.DictReader(path.open(encoding="utf-8"))
        if r["recovered"].strip().lower() == "true"
    ]
    rows.sort(key=lambda r: int(r["case_index"]))
    return rows


def _hop_index_from_cause(cause: str) -> int:
    """Exp18 formats cause as '... at generation point N' (0-based gen_point).

    hop_index is gen_point + 1 (the convention in counterfactual/actions.py).
    """
    m = _GEN_POINT_RE.search(cause)
    if not m:
        raise ValueError(f"cannot parse generation point from cause: {cause!r}")
    return int(m.group(1)) + 1


def _memory_texts(case) -> tuple[str, ...]:
    """Texts of the recall set -- the recurrence identity of the failure.

    Uses the full retrieved memory (``extracted_memory``), which is what an
    online system actually has at the hook. We deliberately do NOT key off
    ``gold_evidence``: gold is not available online, and a validation sweep
    showed the full-recall fingerprint is in fact *more* discriminative
    (intra/inter family margin +0.58 vs +0.52 for the gold-linked variant).
    Fingerprinting reads recall content only -- no gold, no scoring signal.
    """
    return tuple(m.text for m in case.extracted_memory)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ledger",
        default="artifacts/sandbox/failure_memory_trajectory_detail.csv",
    )
    parser.add_argument("--cases", default="data/probe_cases/real_recurrent_cases.json")
    parser.add_argument("--out", default="artifacts/sandbox/failure_memory_skill")
    parser.add_argument("--threshold", type=int, default=3)
    args = parser.parse_args()

    ledger_path = Path(args.ledger)
    if not ledger_path.exists():
        raise SystemExit(f"ledger not found: {ledger_path} (run Exp18 first)")

    rows = _load_recovered_ledgers(ledger_path)
    if not rows:
        raise SystemExit("no recovered cases in ledger; nothing to abstract")

    # The detail CSV carries no query text; join it back from the case file so
    # the pattern fingerprint is built from the real memory content (option B).
    cases_by_id = {c.case_id: c for c in load_probe_cases(args.cases)}

    out_root = Path(args.out)
    markdown_store = MarkdownFailureMemoryStore(out_root)
    skill_loop = FailureMemorySkillLoop(markdown_store, threshold=args.threshold)

    replayed = 0
    abstractions = 0
    skipped_no_query = 0
    for r in rows:
        action = r["ledger_error_type"]
        if action not in PIPELINE_STEP_ACTIONS:
            continue
        case = cases_by_id.get(r["case_id"])
        if case is None:
            skipped_no_query += 1
            continue
        record = skill_loop.record_recovered_case(
            case_id=r["case_id"],
            query=case.query,
            hop_index=_hop_index_from_cause(r["ledger_cause"]),
            label=action,
            cause=r["ledger_cause"],
            corrected_memory=r["ledger_corrected_memory"],
            repair_guidance=r["ledger_repair_guidance"],
            memory_texts=_memory_texts(case),
            recovery_gain=float(r["ledger_recovery_gain"] or 0.0),
        )
        replayed += 1
        if record is not None:
            abstractions += 1

    print(f"recovered ledgers replayed: {replayed}")
    if skipped_no_query:
        print(f"skipped (case_id not in {args.cases}): {skipped_no_query}")
    print(f"pattern (re)writes triggered: {abstractions}")
    print(f"distinct patterns in library: {skill_loop.pattern_count}")
    print("\npatterns/:")
    for p in sorted(markdown_store.patterns_dir.glob("*.md")):
        print(f"  {p.name}")
    print(f"\nindex: {markdown_store.index_path}")


if __name__ == "__main__":
    main()
