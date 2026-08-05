#!/usr/bin/env python3
"""Apply the frozen mechanical gates to niche-evolution JSONL outcomes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cmd_audit.eval.niche_gates import (
    NicheConfirmatoryOutcome,
    evaluate_niche_confirmation,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--gstar", choices=("G2", "G3"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--bootstrap-seed", type=int, default=0)
    args = parser.parse_args()

    rows = []
    with args.input.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if value.get("record_type") not in {
                None,
                "niche_confirmatory_outcome",
            }:
                continue
            try:
                rows.append(
                    NicheConfirmatoryOutcome(
                        **{
                            key: item
                            for key, item in value.items()
                            if key != "record_type"
                        }
                    )
                )
            except TypeError as exc:
                raise ValueError(
                    f"{args.input}:{line_number}: invalid outcome"
                ) from exc

    decision = evaluate_niche_confirmation(
        rows,
        gstar=args.gstar,
        bootstrap_samples=args.bootstrap_samples,
        bootstrap_seed=args.bootstrap_seed,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            decision.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[RESULT] final_decision={decision.final_decision}")
    print(f"[RESULT] primary_passed={int(decision.primary_passed)}")
    print(f"[RESULT] graph_claim_passed={int(decision.graph_claim_passed)}")
    print(f"[RESULT] output={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
