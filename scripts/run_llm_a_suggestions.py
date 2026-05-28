#!/usr/bin/env python3
"""Populate LLM-A candidate labels for the 130-case researcher subset.

This script calls an OpenAI-compatible endpoint only when run without
``--dry-run``. It is not executed by the build/test workflow.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cmd_audit.core.labels import PIPELINE_LABELS
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.core.models import ProbeCase, load_real_cases_by_source


SYSTEM_PROMPT = """You are a memory pipeline failure analyst.

Classify each failed memory-augmented agent case into exactly one active
pipeline label:

- write_error: evidence never written to memory.
- compression_error: memory compressed such that evidence was lost.
- premature_extraction_error: raw events contain evidence but extracted memory lost it.
- retrieval_error: correct memory exists but was not retrieved.
- injection_error: memory retrieved but injected with format/order errors.
- reasoning_error: evidence was injected but reasoning over it failed.
- ingestion_error: evidence never reached the agent before add/search.
- route_error: correct memory stored in the wrong tier/store.
- granularity_error: memory has the wrong granularity level.
- graph_error: graph expansion introduced distractors.
- safety_error: safety filter blocked valid evidence.

Return JSON only:
{"suggested_label": "<one label>", "rationale": "<1-2 sentences>"}
"""


def load_case_index(input_dir: str | Path) -> dict[str, ProbeCase]:
    by_id: dict[str, ProbeCase] = {}
    for cases in load_real_cases_by_source(input_dir).values():
        for case in cases:
            by_id[case.case_id] = case
    return by_id


def build_user_prompt(case: ProbeCase) -> str:
    memory_summary = "\n".join(
        f"- {item.memory_id} [{item.store}/{item.granularity}]: {item.text}"
        for item in case.extracted_memory[:12]
    )
    baseline = case.primary_baseline
    evidence = "\n".join(f"- {ev.text}" for ev in case.gold_evidence)
    return "\n".join(
        (
            f"Case ID: {case.case_id}",
            f"Query: {case.query}",
            f"Gold answer: {case.gold_answer}",
            f"Gold evidence:\n{evidence}",
            f"Extracted memory:\n{memory_summary}",
            "Baseline retrieval IDs: "
            + ", ".join(baseline.retrieved_memory_ids),
            f"Baseline injected context: {baseline.injected_context}",
            f"Baseline answer: {baseline.answer}",
            f"Has ingestion trace: {case.has_ingestion_trace}",
        )
    )


def parse_suggestion(text: str) -> tuple[str, str]:
    try:
        raw = json.loads(text)
        label = str(raw["suggested_label"])
        rationale = str(raw.get("rationale", ""))
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"LLM-A response is not valid suggestion JSON: {text}") from exc
    if label not in PIPELINE_LABELS:
        raise ValueError(f"invalid LLM-A suggested label: {label}")
    rationale = re.sub(r"\s+", " ", rationale).strip()
    return label, rationale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run llama-3.3-70b-instruct LLM-A suggestions"
    )
    parser.add_argument("--subset", default="data/probe_cases/researcher_labeled_subset.json")
    parser.add_argument("--input-dir", default="data/probe_cases")
    parser.add_argument("--output", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default="llama-3.3-70b-instruct")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    subset_path = Path(args.subset)
    payload = json.loads(subset_path.read_text(encoding="utf-8"))
    case_index = load_case_index(args.input_dir)
    rows = payload.get("cases", [])
    if args.max_cases is not None:
        rows = rows[: args.max_cases]

    if args.dry_run:
        for row in rows[:1]:
            case = case_index[row["case_id"]]
            print(SYSTEM_PROMPT)
            print("---")
            print(build_user_prompt(case))
        print(f"dry_run_cases={len(rows)}")
        return 0

    config = LLMClientConfig(
        base_url=args.base_url or LLMClientConfig().base_url,
        model=args.model,
        timeout_seconds=args.timeout,
        temperature=0.0,
        max_retries=1,
    )
    client = LLMClient(config)

    case_ids = {row["case_id"] for row in rows}
    for row in payload.get("cases", []):
        if row["case_id"] not in case_ids:
            continue
        case = case_index[row["case_id"]]
        response = client.generate(build_user_prompt(case), system=SYSTEM_PROMPT)
        label, rationale = parse_suggestion(response)
        row["llm_a_suggestion"] = label
        row["llm_a_rationale"] = rationale

    out = Path(args.output) if args.output else subset_path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote LLM-A suggestions to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
