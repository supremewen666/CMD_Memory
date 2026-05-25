#!/usr/bin/env python3
"""Write reproducibility metadata for issue 0023 at-scale LLM retest.

This utility records the intended run configuration. It does not execute the
retest, contact any model endpoint, or inspect artifacts.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def build_run_meta(
    *,
    agent_model: str,
    evaluator_model: str,
    verifier_model: str,
    agent_endpoint: str,
    evaluator_endpoint: str,
    temperature: float,
    tie_margin: float,
    use_hook: bool,
    on_the_fly_baseline_rescore: bool,
    random_state: int,
) -> str:
    command = (
        "run_full_real_suite("
        f"use_hook={use_hook}, "
        f"agent_generate={agent_model}, "
        f"evidence_scorer={evaluator_model}, "
        f"answer_verifier={verifier_model}, "
        f"on_the_fly_baseline_rescore={on_the_fly_baseline_rescore}, "
        f"tie_margin={tie_margin})"
    )
    lines = [
        "At-scale LLM retest run metadata",
        "",
        "issue: 0023",
        "dataset_version: v1.0 596-case real-data snapshot",
        f"agent_model: {agent_model}",
        f"agent_endpoint: {agent_endpoint}",
        f"evaluator_model: {evaluator_model}",
        f"evaluator_endpoint: {evaluator_endpoint}",
        f"answer_verifier_model: {verifier_model}",
        f"temperature: {temperature}",
        "top_p: provider-default",
        f"random_state: {random_state}",
        f"use_hook: {str(use_hook).lower()}",
        f"on_the_fly_baseline_rescore: {str(on_the_fly_baseline_rescore).lower()}",
        f"tie_margin: {tie_margin}",
        "label_stripped_replay_context: true",
        "phrase_match_shortcut_allowed: false",
        "",
        "intended_command:",
        command,
        "",
    ]
    return "\n".join(lines)


def write_run_meta(path: str | Path, text: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write issue 0023 at-scale retest run metadata"
    )
    parser.add_argument("--out", default="artifacts/at_scale_llm_retest.run_meta.txt")
    parser.add_argument("--agent-model", default="qwen2.5:7b")
    parser.add_argument("--evaluator-model", required=True)
    parser.add_argument("--verifier-model", default=None)
    parser.add_argument("--agent-endpoint", default="http://localhost:11434/v1")
    parser.add_argument("--evaluator-endpoint", required=True)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--tie-margin", type=float, default=0.0)
    parser.add_argument("--random-state", type=int, default=42)
    args = parser.parse_args(argv)

    text = build_run_meta(
        agent_model=args.agent_model,
        evaluator_model=args.evaluator_model,
        verifier_model=args.verifier_model or args.evaluator_model,
        agent_endpoint=args.agent_endpoint,
        evaluator_endpoint=args.evaluator_endpoint,
        temperature=args.temperature,
        tie_margin=args.tie_margin,
        use_hook=False,
        on_the_fly_baseline_rescore=True,
        random_state=args.random_state,
    )
    write_run_meta(args.out, text)
    print(f"wrote run metadata to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
