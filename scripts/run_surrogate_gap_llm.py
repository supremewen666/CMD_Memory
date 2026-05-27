#!/usr/bin/env python3
"""Run issue 0036 surrogate-gap measurement under the LLM stack."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cmd_audit.llm_client import LLMClient, LLMClientConfig
from cmd_audit.llm_scoring import SubagentScorer
from cmd_audit.models import ProbeCase, load_real_cases_by_source
from cmd_audit.surrogate_gap import SurrogateGapRow, measure_surrogate_gaps
from scripts.run_at_scale_llm_retest import build_agent_generate


def load_case_index(input_dir: str | Path) -> dict[str, ProbeCase]:
    index: dict[str, ProbeCase] = {}
    for cases in load_real_cases_by_source(input_dir).values():
        for case in cases:
            index[case.case_id] = case
    return index


def load_holdout_case_ids(path: str | Path) -> list[str]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("cases", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("holdout JSON must contain an array or cases array")
    return [
        str(row["case_id"] if isinstance(row, dict) else row)
        for row in rows
        if (isinstance(row, dict) and row.get("case_id")) or isinstance(row, str)
    ]


def write_rows(path: str | Path, rows: tuple[SurrogateGapRow, ...]) -> None:
    fieldnames = [
        "case_id",
        "label",
        "gold_recovery_gain",
        "surrogate_recovery_gain",
        "gap",
        "surrogate_found",
    ]
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "case_id": row.case_id,
                    "label": row.label,
                    "gold_recovery_gain": f"{row.gold_recovery_gain:.6f}",
                    "surrogate_recovery_gain": f"{row.surrogate_recovery_gain:.6f}",
                    "gap": f"{row.gap:.6f}",
                    "surrogate_found": str(row.surrogate_found).lower(),
                }
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run issue 0036 surrogate-gap LLM")
    parser.add_argument("--input-dir", default="data/probe_cases")
    parser.add_argument("--holdout", default="artifacts/surrogate_gap_holdout.json")
    parser.add_argument("--out", default="artifacts/surrogate_gap_llm.csv")
    parser.add_argument("--agent-base-url", default=None)
    parser.add_argument("--agent-model", default=None)
    parser.add_argument("--evaluator-base-url", default=None)
    parser.add_argument("--evaluator-model", default=None)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    env_defaults = LLMClientConfig()
    agent_base_url = args.agent_base_url or env_defaults.base_url
    agent_model = args.agent_model or env_defaults.model
    evaluator_base_url = args.evaluator_base_url or agent_base_url
    evaluator_model = args.evaluator_model or agent_model

    ids = load_holdout_case_ids(args.holdout)
    if args.max_cases is not None:
        ids = ids[: args.max_cases]
    case_index = load_case_index(args.input_dir)
    cases = [case_index[case_id] for case_id in ids if case_id in case_index]
    if len(cases) != len(ids):
        missing = sorted(set(ids) - set(case_index))
        raise ValueError(f"holdout contains unknown case ids: {', '.join(missing[:10])}")

    if args.dry_run:
        print(f"cases={len(cases)}")
        print(f"agent={agent_model} @ {agent_base_url}")
        print(f"evaluator={evaluator_model} @ {evaluator_base_url}")
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
    rows = measure_surrogate_gaps(
        cases,
        agent_generate=build_agent_generate(agent_client),
        scorer=SubagentScorer(
            evaluator_client,
            max_workers=args.max_workers,
            max_retries=args.max_retries,
        ),
    )
    write_rows(args.out, rows)
    print(f"wrote {len(rows)} surrogate-gap rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
