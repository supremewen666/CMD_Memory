#!/usr/bin/env python3
"""Reconstructed deepseek-v4-pro-max perturbation-label annotator.

Original-provenance note:
The original deepseek labeling prompt and run script for the V1.0 596-case
dataset were not preserved in the repository. This script is the Decision 34
R4-prov reconstruction target. When executed against the same cleaned cases, its
outputs must be compared to ``data/probe_cases/real_*_cases.json`` and the
agreement percentage must be written to
``artifacts/deepseek_label_reproducibility.txt`` before the large-N sanity
number is cited.

Exact reconstructed prompt template:

System:
You are a memory pipeline failure analyst. Given a failed memory-augmented
agent case, classify the failure into exactly one of these 11 operation labels:

- write_error: Evidence never written to memory.
- compression_error: Memory compressed such that evidence lost.
- premature_extraction_error: Evidence lost during extraction before retrieval;
  raw events still contain it.
- retrieval_error: Correct memory exists but not retrieved.
- injection_error: Memory retrieved but injected with format/order errors.
- reasoning_error: Evidence correctly injected but reasoning over it failed.
- ingestion_error: Evidence never reached the agent; ingestion pipeline failed
  before add().
- route_error: Correct memory stored in wrong tier/store, baseline retrieval
  missed it.
- granularity_error: Memory expressed at sub-optimal granularity level,
  obscuring evidence.
- graph_error: Graph expansion introduced distractors that masked correct
  evidence.
- safety_error: Safety filter blocked valid evidence.

Return one label name from the list. No explanation.

User:
Case ID: {case_id}
Query: {query}
Gold answer: {gold_answer}
Gold evidence: {gold_evidence}
Extracted memory summary: {extracted_memory_summary}
Baseline retrieval IDs: {baseline_retrieval_ids}
Baseline injected context: {baseline_injected_context}
Baseline answer: {baseline_answer}
Has ingestion trace: {has_ingestion_trace}

Output: one label name from the list. No explanation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cmd_audit.labels import V1_PIPELINE_LABELS
from cmd_audit.llm_client import LLMClient, LLMClientConfig


DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com/v1"

LABEL_DEFINITIONS: dict[str, str] = {
    "write_error": "Evidence never written to memory.",
    "compression_error": "Memory compressed such that evidence lost.",
    "premature_extraction_error": (
        "Evidence lost during extraction before retrieval; raw events still contain it."
    ),
    "retrieval_error": "Correct memory exists but not retrieved.",
    "injection_error": "Memory retrieved but injected with format/order errors.",
    "reasoning_error": "Evidence correctly injected but reasoning over it failed.",
    "ingestion_error": (
        "Evidence never reached the agent; ingestion pipeline failed before add()."
    ),
    "route_error": "Correct memory stored in wrong tier/store, baseline retrieval missed it.",
    "granularity_error": (
        "Memory expressed at sub-optimal granularity level, obscuring evidence."
    ),
    "graph_error": "Graph expansion introduced distractors that masked correct evidence.",
    "safety_error": "Safety filter blocked valid evidence.",
}

SYSTEM_PROMPT = """You are a memory pipeline failure analyst. Given a failed memory-augmented
agent case, classify the failure into exactly one of these 11 operation labels:

- write_error: Evidence never written to memory.
- compression_error: Memory compressed such that evidence lost.
- premature_extraction_error: Evidence lost during extraction before retrieval; raw events still contain it.
- retrieval_error: Correct memory exists but not retrieved.
- injection_error: Memory retrieved but injected with format/order errors.
- reasoning_error: Evidence correctly injected but reasoning over it failed.
- ingestion_error: Evidence never reached the agent; ingestion pipeline failed before add().
- route_error: Correct memory stored in wrong tier/store, baseline retrieval missed it.
- granularity_error: Memory expressed at sub-optimal granularity level, obscuring evidence.
- graph_error: Graph expansion introduced distractors that masked correct evidence.
- safety_error: Safety filter blocked valid evidence.

Return one label name from the list. No explanation.
"""


def load_case_rows(path: str | Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = payload.get("cases", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError(f"{path} must contain a JSON array or cases array")
    return [row for row in rows if isinstance(row, dict)]


def load_existing_labels(paths: Iterable[str | Path]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for path in paths:
        for row in load_case_rows(path):
            case_id = row.get("case_id")
            label = row.get("perturbation_label")
            if case_id and label:
                labels[str(case_id)] = str(label)
    return labels


def build_user_prompt(row: dict[str, Any]) -> str:
    baseline = _primary_baseline(row)
    memory_summary = _memory_summary(row)
    if not memory_summary:
        memory_summary = _cleaned_case_context_summary(row)
    return "\n".join(
        (
            f"Case ID: {_value(row, 'case_id')}",
            f"Query: {_value(row, 'query')}",
            f"Gold answer: {_value(row, 'gold_answer')}",
            "Gold evidence: " + _gold_evidence_summary(row),
            "Extracted memory summary: " + memory_summary,
            "Baseline retrieval IDs: "
            + (_baseline_retrieval_ids(baseline) or "<not available>"),
            "Baseline injected context: "
            + (_value(baseline, "injected_context") or "<not available>"),
            "Baseline answer: " + (_value(baseline, "answer") or "<not available>"),
            f"Has ingestion trace: {row.get('has_ingestion_trace', '<unknown>')}",
            "",
            "Output: one label name from the list. No explanation.",
        )
    )


def parse_label_response(text: str) -> str:
    stripped = text.strip()
    try:
        raw = json.loads(stripped)
    except json.JSONDecodeError:
        raw = None
    if isinstance(raw, dict):
        for key in ("perturbation_label", "label", "suggested_label"):
            if key in raw:
                stripped = str(raw[key]).strip()
                break
    match = re.search(
        r"\b(" + "|".join(re.escape(label) for label in V1_PIPELINE_LABELS) + r")\b",
        stripped,
    )
    if match is None:
        raise ValueError(f"deepseek response did not contain a valid label: {text}")
    return match.group(1)


def annotate_rows(
    rows: list[dict[str, Any]],
    *,
    client: LLMClient,
    max_cases: int | None = None,
) -> list[dict[str, str]]:
    selected = rows if max_cases is None else rows[:max_cases]
    outputs: list[dict[str, str]] = []
    for row in selected:
        case_id = str(row.get("case_id", ""))
        if not case_id:
            raise ValueError("case row missing case_id")
        response = client.generate(build_user_prompt(row), system=SYSTEM_PROMPT)
        outputs.append(
            {
                "case_id": case_id,
                "perturbation_label": parse_label_response(response),
            }
        )
    return outputs


def compare_annotations(
    annotations: Iterable[dict[str, str]],
    existing_labels: dict[str, str],
) -> dict[str, Any]:
    total = 0
    matched = 0
    missing: list[str] = []
    mismatches: list[dict[str, str]] = []
    for row in annotations:
        case_id = str(row.get("case_id", ""))
        label = str(row.get("perturbation_label", ""))
        expected = existing_labels.get(case_id)
        if expected is None:
            missing.append(case_id)
            continue
        total += 1
        if label == expected:
            matched += 1
        else:
            mismatches.append(
                {
                    "case_id": case_id,
                    "expected": expected,
                    "observed": label,
                }
            )
    agreement = matched / total if total else 0.0
    return {
        "compared": total,
        "matched": matched,
        "agreement": agreement,
        "missing_case_ids": missing,
        "mismatches": mismatches,
    }


def write_reproducibility_report(
    path: str | Path,
    comparison: dict[str, Any],
    *,
    model: str,
    base_url: str,
    temperature: float,
    top_p: str,
    elapsed_seconds: float,
    total_calls: int,
    run_status: str = "completed",
    estimated_cost_usd: str = "not_recorded",
    notes: str = "",
) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    agreement = float(comparison["agreement"])
    threshold_status = "pass" if agreement >= 0.95 else "fail"
    lines = [
        "Deepseek Label Reproducibility Report",
        "",
        "Original labeling script was not preserved; this is the reconstructed "
        "Decision 34 R4-prov check.",
        f"run_status: {run_status}",
        f"model: {model}",
        f"base_url: {base_url}",
        f"temperature: {temperature}",
        f"top_p: {top_p}",
        f"total_annotation_calls: {total_calls}",
        f"elapsed_seconds: {elapsed_seconds:.2f}",
        f"estimated_cost_usd: {estimated_cost_usd}",
        f"compared: {comparison['compared']}",
        f"matched: {comparison['matched']}",
        f"agreement: {agreement:.6f}",
        "required_agreement_threshold: 0.950000",
        f"threshold_status: {threshold_status}",
        "",
        "drift_notes:",
        "- Possible causes of drift: provider non-determinism, prompt edits, "
        "model/version changes, dataset changes, or reconstructed prompt mismatch.",
        "",
        "honesty_note:",
        "- The original deepseek labeling script was not preserved. The checked-in "
        "script is a reconstructed annotator definition and must be run against "
        "the target corpus before citing the 596-case scale sanity number.",
        "",
        "mismatches:",
    ]
    if notes:
        lines.insert(lines.index("honesty_note:"), f"- {notes}")
    mismatches = comparison.get("mismatches", [])
    if not mismatches:
        lines.append("- none")
    else:
        for row in mismatches[:100]:
            lines.append(
                f"- {row['case_id']}: expected={row['expected']} observed={row['observed']}"
            )
        if len(mismatches) > 100:
            lines.append(f"- ... {len(mismatches) - 100} additional mismatches omitted")
    missing = comparison.get("missing_case_ids", [])
    if missing:
        lines.extend(("", "missing_case_ids:", *[f"- {case_id}" for case_id in missing]))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _primary_baseline(row: dict[str, Any]) -> dict[str, Any]:
    baselines = row.get("baseline_outputs")
    if isinstance(baselines, dict):
        vector = baselines.get("vector_memory")
        if isinstance(vector, dict):
            return vector
    if isinstance(baselines, list):
        for baseline in baselines:
            if (
                isinstance(baseline, dict)
                and baseline.get("baseline_name") == "vector_memory"
            ):
                return baseline
        for baseline in baselines:
            if isinstance(baseline, dict):
                return baseline
    primary = row.get("primary_baseline")
    if isinstance(primary, dict):
        return primary
    return {}


def _value(row: dict[str, Any], key: str) -> str:
    value = row.get(key, "")
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _gold_evidence_summary(row: dict[str, Any]) -> str:
    evidence = row.get("gold_evidence", [])
    if not isinstance(evidence, list):
        return _value(row, "gold_evidence")
    parts: list[str] = []
    for item in evidence[:8]:
        if isinstance(item, dict):
            parts.append(_value(item, "text") or json.dumps(item, ensure_ascii=False))
        else:
            parts.append(str(item))
    return " | ".join(parts)


def _memory_summary(row: dict[str, Any]) -> str:
    memory = row.get("extracted_memory", [])
    if not isinstance(memory, list):
        return _value(row, "extracted_memory")
    parts: list[str] = []
    for item in memory[:12]:
        if isinstance(item, dict):
            memory_id = _value(item, "memory_id")
            store = _value(item, "store")
            granularity = _value(item, "granularity")
            text = _value(item, "text")
            parts.append(f"{memory_id} [{store}/{granularity}]: {text}")
        else:
            parts.append(str(item))
    return " | ".join(parts)


def _cleaned_case_context_summary(row: dict[str, Any]) -> str:
    sessions = row.get("haystack_sessions", [])
    if not isinstance(sessions, list):
        return ""
    parts: list[str] = []
    for session_index, session in enumerate(sessions[:4]):
        if not isinstance(session, list):
            continue
        turns: list[str] = []
        for turn in session[:6]:
            if not isinstance(turn, dict):
                continue
            role = _value(turn, "role") or "unknown"
            content = _value(turn, "content")
            if len(content) > 240:
                content = content[:237] + "..."
            turns.append(f"{role}: {content}")
        if turns:
            parts.append(f"session_{session_index}: " + " / ".join(turns))
    if not parts:
        return ""
    metadata = [
        f"source={_value(row, 'source')}",
        f"answer_session_ids={_value(row, 'answer_session_ids')}",
        f"haystack_session_count={_value(row, 'haystack_session_count')}",
    ]
    return " ; ".join(metadata + parts)


def _baseline_retrieval_ids(baseline: dict[str, Any]) -> str:
    ids = baseline.get("retrieved_memory_ids", [])
    if isinstance(ids, list):
        return ", ".join(str(item) for item in ids)
    return str(ids)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Annotate perturbation labels with deepseek-v4-pro-max"
    )
    parser.add_argument("--input", default="data/cleaned_cases/cleaned_cases.json")
    parser.add_argument("--output", default="artifacts/deepseek_label_annotations.json")
    parser.add_argument(
        "--existing-labels",
        nargs="*",
        default=[
            "data/probe_cases/real_longmemeval_cases.json",
            "data/probe_cases/real_memoryarena_cases.json",
            "data/probe_cases/real_toolbench_cases.json",
        ],
    )
    parser.add_argument("--report", default="artifacts/deepseek_label_reproducibility.txt")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("DEEPSEEK_BASE_URL", DEEPSEEK_DEFAULT_BASE_URL),
    )
    parser.add_argument("--model", default="deepseek-v4-pro-max")
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", default="provider-default")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--annotations",
        default=None,
        help="Existing annotation JSON to compare without making API calls.",
    )
    parser.add_argument("--compare-only", action="store_true")
    args = parser.parse_args(argv)

    rows = load_case_rows(args.input)
    if args.max_cases is not None:
        rows = rows[: args.max_cases]
    if args.dry_run:
        if rows:
            print(SYSTEM_PROMPT)
            print("---")
            print(build_user_prompt(rows[0]))
        print(f"dry_run_cases={len(rows)}")
        return 0

    if args.compare_only:
        annotation_path = args.annotations or args.output
        annotations = load_case_rows(annotation_path)
        existing = load_existing_labels(args.existing_labels)
        comparison = compare_annotations(annotations, existing)
        write_reproducibility_report(
            args.report,
            comparison,
            model=args.model,
            base_url=args.base_url,
            temperature=args.temperature,
            top_p=args.top_p,
            elapsed_seconds=0.0,
            total_calls=len(annotations),
            run_status="compare_only",
            notes=f"Compared existing annotations from {annotation_path}; no API calls made.",
        )
        print(
            f"compared {comparison['compared']} labels from {annotation_path}; "
            f"agreement={comparison['agreement']:.4f}"
        )
        return 0

    api_key = os.environ.get(args.api_key_env) or LLMClientConfig().api_key
    if _requires_api_key(args.base_url) and not api_key:
        raise SystemExit(
            f"missing API key: set {args.api_key_env} or LLM_API_KEY before "
            "calling the official DeepSeek endpoint"
        )

    config = LLMClientConfig(
        base_url=args.base_url,
        model=args.model,
        timeout_seconds=args.timeout,
        api_key=api_key,
        temperature=args.temperature,
        max_retries=1,
    )
    client = LLMClient(config)
    started = time.time()
    annotations = annotate_rows(rows, client=client)
    elapsed = time.time() - started

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(annotations, indent=2, ensure_ascii=False) + "\n")

    existing = load_existing_labels(args.existing_labels)
    comparison = compare_annotations(annotations, existing)
    write_reproducibility_report(
        args.report,
        comparison,
        model=args.model,
        base_url=config.base_url,
        temperature=args.temperature,
        top_p=args.top_p,
        elapsed_seconds=elapsed,
        total_calls=len(annotations),
    )
    print(
        f"wrote {len(annotations)} labels to {out}; "
        f"agreement={comparison['agreement']:.4f}"
    )
    return 0


def _requires_api_key(base_url: str) -> bool:
    return "deepseek.com" in base_url.lower()


if __name__ == "__main__":
    raise SystemExit(main())
