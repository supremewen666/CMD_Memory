#!/usr/bin/env python3
"""Write the post-Decision-34 LLM-stack artifact MANIFEST.

The script only records artifact provenance for files that already exist. It
does not run the at-scale retest or regenerate artifacts.
"""

from __future__ import annotations

import argparse
from pathlib import Path


DROPPED_ARTIFACTS = (
    "sandbox/recurrence_comparison.csv",
)


def build_manifest(
    artifact_dir: str | Path,
    *,
    dataset_version: str,
    dataset_hash: str,
    git_sha: str,
    evaluator_model: str,
) -> str:
    root = Path(artifact_dir)
    files = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "MANIFEST.txt"
    )
    lines = [
        "Decision 34 LLM-stack artifact manifest",
        "",
        f"dataset_version: {dataset_version}",
        f"dataset_hash: {dataset_hash}",
        f"git_sha: {git_sha}",
        "",
        "Scoring stack:",
        "- agent_generate: qwen2.5-7b",
        f"- evaluator_scorer: {evaluator_model}",
        f"- answer_verifier: {evaluator_model}",
        "- on_the_fly_baseline_rescore: true",
        "- tie_margin: 0.0",
        "- replay_context: label-stripped",
        "- phrase_match_shortcut_allowed: false",
        "",
        "Semantic shifts:",
        "- attribution_confusion_matrix*.csv cell values use tie_margin=0.0.",
        "- sandbox/repair_label_summary.csv recovered/partial counts use AnswerVerifier.",
        "- sandbox/recurrence_comparison.csv is dropped; Experiment 1 supersedes it.",
        "",
        "Dropped legacy-only artifacts:",
    ]
    lines.extend(f"- {name}" for name in DROPPED_ARTIFACTS)
    lines.extend(("", "Artifact files:"))
    if files:
        lines.extend(f"- {name}: post-D34 LLM-stack artifact" for name in files)
    else:
        lines.append("- none present yet")
    lines.append("")
    return "\n".join(lines)


def write_manifest(path: str | Path, text: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write post-Decision-34 artifacts/MANIFEST.txt"
    )
    parser.add_argument("--artifact-dir", default="artifacts")
    parser.add_argument("--out", default="artifacts/MANIFEST.txt")
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--dataset-hash", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--evaluator-model", required=True)
    args = parser.parse_args(argv)

    text = build_manifest(
        args.artifact_dir,
        dataset_version=args.dataset_version,
        dataset_hash=args.dataset_hash,
        git_sha=args.git_sha,
        evaluator_model=args.evaluator_model,
    )
    write_manifest(args.out, text)
    print(f"wrote LLM-stack artifact manifest to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
