"""Data cleaning pipeline for raw CMD probe cases from HuggingFace datasets.

Cleaning stages:
  1. Merge raw JSON files from data/raw_cases/
  2. Deduplicate by casefolded query hash
  3. Quality filters (length, language, noise patterns)
  4. CMD relevance scoring
  5. Stratified sampling to target 150-250 cases
  6. Output cleaned cases to data/cleaned_cases/

Usage:
    python -m experiments.clean_datasets
    python -m experiments.clean_datasets --target 200
"""

from __future__ import annotations

import json
import re
import hashlib
import random
from collections import Counter
from pathlib import Path

RAW_DIR = Path("data/raw_cases")
CLEAN_DIR = Path("data/cleaned_cases")
CLEAN_OUTPUT = CLEAN_DIR / "cleaned_cases.json"
CLEAN_REPORT = CLEAN_DIR / "cleaning_report.txt"

# Per-source sampling caps after cleaning (each source gets 150-250).
SOURCE_CAPS = {
    "memoryarena": 200,
    "longmemeval": 200,
    "toolbench": 200,
}

# Noise patterns that indicate low-quality or non-memory-relevant cases.
NOISE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\\begin\{("
        + "|".join(
            [
                "equation",
                "align",
                "array",
                "matrix",
                "cases",
                "gather",
                "split",
                "proof",
                "theorem",
                "lemma",
                "definition",
            ]
        )
        + r")\}",
        re.IGNORECASE,
    ),
    re.compile(r"\\frac\{"),  # LaTeX math fractions
    re.compile(r"\\sum\b"),  # LaTeX sums
    re.compile(r"\\int\b"),  # LaTeX integrals
    re.compile(r"^\s*\{[^}]*\}\s*$"),  # Pure JSON-like objects as queries
    re.compile(r"^\s*[\[\(].*[\]\)]\s*$"),  # Parenthetical-only queries
]

# Language markers for non-English content to filter.
NON_ENGLISH_MARKERS = [
    re.compile(r"[一-鿿]{4,}"),  # Chinese (>3 chars)
    re.compile(r"[぀-ゟ゠-ヿ]{4,}"),  # Japanese kana
    re.compile(r"[가-힯]{4,}"),  # Korean
]


def load_raw_files() -> dict[str, list[dict]]:
    """Load all raw JSON files from data/raw_cases/."""
    raw_dir = RAW_DIR
    if not raw_dir.exists():
        raise FileNotFoundError(f"Raw data directory not found: {raw_dir}")

    all_cases: dict[str, list[dict]] = {}
    for fpath in sorted(raw_dir.glob("*.json")):
        source = fpath.stem.replace("_raw", "")
        data = json.loads(fpath.read_text(encoding="utf-8"))
        if isinstance(data, list):
            all_cases[source] = data
        print(f"  Loaded {source}: {len(data)} cases from {fpath.name}")

    return all_cases


# ═══════════════════════════════════════════════════════════════════════════
# Stage 1: Merge
# ═══════════════════════════════════════════════════════════════════════════


def merge_sources(raw_sources: dict[str, list[dict]]) -> list[dict]:
    """Merge all sources into a single list, normalizing keys."""
    merged: list[dict] = []
    for source, cases in raw_sources.items():
        for c in cases:
            merged.append(_normalize_case(c, source))
    print(f"  Merged total: {len(merged)}")
    return merged


# ═══════════════════════════════════════════════════════════════════════════
# Stage 2: Deduplicate
# ═══════════════════════════════════════════════════════════════════════════


def deduplicate(cases: list[dict]) -> list[dict]:
    """Remove cases with identical casefolded queries."""
    seen: set[str] = set()
    unique: list[dict] = []
    duplicates = 0
    for c in cases:
        q_key = _query_hash(c.get("query", ""))
        if q_key not in seen:
            seen.add(q_key)
            unique.append(c)
        else:
            duplicates += 1
    print(f"  Dedup: {len(unique)} unique, {duplicates} duplicates removed")
    return unique


# ═══════════════════════════════════════════════════════════════════════════
# Stage 3: Quality filters
# ═══════════════════════════════════════════════════════════════════════════


def quality_filter(cases: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """Apply quality and relevance filters.

    Returns (filtered_cases, rejection_counts).
    """
    rejected: dict[str, int] = Counter()
    kept: list[dict] = []

    for c in cases:
        reason = _reject_reason(c)
        if reason:
            rejected[reason] += 1
        else:
            kept.append(c)

    print(f"  Quality filter: {len(kept)} kept, {sum(rejected.values())} removed")
    for reason, count in rejected.most_common():
        print(f"    {reason}: {count}")
    return kept, dict(rejected)


def _reject_reason(case: dict) -> str | None:
    """Return rejection reason string, or None if case passes all filters."""
    query = case.get("query", "").strip()
    answer = case.get("gold_answer", "").strip()

    # Length bounds
    if len(query) < 12:
        return "query_too_short"
    if len(query) > 3000:
        return "query_too_long"
    # Allow longer answers for MemoryArena multi-step tasks
    if answer:
        if len(answer) > 5000:
            return "answer_too_long"
    else:
        # Cases without gold_answer are kept (ToolBench), flagged in metadata
        pass

    # LaTeX / math paper noise (MemoryArena formal_reasoning data)
    for pattern in NOISE_PATTERNS:
        if pattern.search(query):
            return "math_latex_noise"

    # Non-English content
    for pattern in NON_ENGLISH_MARKERS:
        if pattern.search(query):
            return "non_english_content"

    # Pure URL / file path queries
    if re.match(r"^\s*(https?://|/[\w/]+)\s*$", query):
        return "url_or_path_only"

    # Queries that are just numbers or single words
    if re.match(r"^\s*\d+\s*$", query):
        return "number_only"
    if len(query.split()) == 1 and len(query) < 20:
        return "single_word_short"

    return None


# ═══════════════════════════════════════════════════════════════════════════
# Stage 4: CMD relevance scoring
# ═══════════════════════════════════════════════════════════════════════════

# Keywords that suggest a case is memory-relevant.
MEMORY_KEYWORDS = [
    "remember",
    "recall",
    "retrieve",
    "memory",
    "previous",
    "earlier",
    "mentioned",
    "told",
    "said",
    "stored",
    "saved",
    "recorded",
    "history",
    "past",
    "log",
    "archive",
    "session",
    "conversation",
    "yesterday",
    "last week",
    "last month",
    "before",
    "prior",
    "what did",
    "when did",
    "where did",
    "who did",
    "which",
    "how many",
    "how much",
    "how long",
    "what was",
    "what were",
]

TOOL_KEYWORDS = [
    "api",
    "search",
    "find",
    "lookup",
    "query",
    "tool",
    "function",
    "call",
    "endpoint",
    "request",
    "fetch",
    "get",
    "retrieve data",
]


def _score_cmd_relevance(case: dict) -> float:
    """Score a case's relevance for CMD memory-failure probing (0.0–1.0).

    Higher score = more likely to expose memory operation failures.
    """
    query = case.get("query", "").casefold()
    source = case.get("source", "")
    score = 0.0

    # Memory keyword density
    mem_hits = sum(1 for kw in MEMORY_KEYWORDS if kw in query)
    score += min(mem_hits * 0.12, 0.4)

    # Source bonus: LongMemEval is purpose-built for memory evaluation
    if "longmemeval" in source:
        score += 0.3
    elif "memoryarena" in source:
        score += 0.2

    # Tool keywords (relevant for route_error / ingestion_error)
    tool_hits = sum(1 for kw in TOOL_KEYWORDS if kw in query)
    if tool_hits:
        score += min(tool_hits * 0.08, 0.2)

    # Haystack sessions indicate rich memory context
    if case.get("haystack_session_count", 0) > 0:
        score += min(case["haystack_session_count"] * 0.02, 0.2)

    # Question-type bonus: temporal, multi-session, user-specific are harder
    qtype = case.get("question_type", "")
    if qtype in ("temporal-reasoning", "multi-session-user", "single-session-user"):
        score += 0.1

    return min(score, 1.0)


# ═══════════════════════════════════════════════════════════════════════════
# Stage 5: Stratified sampling
# ═══════════════════════════════════════════════════════════════════════════


def stratified_sample(
    cases: list[dict],
    target_total: int,
    caps: dict[str, int] | None = None,
) -> list[dict]:
    """Sample cases with per-source caps, sub-source diversity, and CMD relevance.

    Sampling strategy:
    1. Group by source prefix.
    2. Within each source, distribute cap across sub-sources proportionally.
    3. Within each sub-source, sort by CMD relevance score (descending), take top.
    4. If total < target_total, fill with next-best across all sources.
    """
    caps = caps or SOURCE_CAPS
    random.seed(42)

    # Group by source prefix, then by sub-source
    by_source: dict[str, list[dict]] = {}
    for c in cases:
        src = c.get("source", "").split("/")[0]
        by_source.setdefault(src, []).append(c)

    sampled: list[dict] = []
    remaining: list[dict] = []

    for src, src_cases in sorted(by_source.items()):
        cap = caps.get(src, 30)

        # Distribute cap across sub-sources
        by_sub: dict[str, list[dict]] = {}
        for c in src_cases:
            sub = c.get("source", src)
            by_sub.setdefault(sub, []).append(c)

        # Sort each sub-source by relevance
        for sub in by_sub:
            by_sub[sub].sort(key=_score_cmd_relevance, reverse=True)

        # Allocate cap proportionally across sub-sources (min 5 per sub-source)
        n_subs = len(by_sub)
        if n_subs == 0:
            continue
        min_per_sub = max(3, cap // (n_subs * 2))  # at least 3 per sub-source
        remaining_cap = cap - min_per_sub * n_subs

        for sub, sub_cases in sorted(by_sub.items()):
            alloc = min_per_sub + (remaining_cap // n_subs if n_subs > 0 else 0)
            take = sub_cases[:alloc]
            rest = sub_cases[alloc:]
            sampled.extend(take)
            remaining.extend(rest)
            print(f"  {sub}: {len(sub_cases)} -> sampled {len(take)}")

    # Fill to target if below
    if len(sampled) < target_total:
        remaining.sort(key=_score_cmd_relevance, reverse=True)
        needed = target_total - len(sampled)
        extra = remaining[:needed]
        sampled.extend(extra)
        print(f"  Fill: added {len(extra)} cross-source cases to reach target")

    print(f"  Final sample: {len(sampled)} cases")
    return sampled


# ═══════════════════════════════════════════════════════════════════════════
# Stage 6: Output & report
# ═══════════════════════════════════════════════════════════════════════════


def write_output(cases: list[dict], output_path: Path) -> None:
    """Write cleaned cases as JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(cases, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nCleaned cases written to {output_path}")


def write_report(
    cases: list[dict],
    report_path: Path,
    *,
    stages: dict[str, int],
    rejection_counts: dict[str, int],
) -> None:
    """Write a human-readable cleaning report."""
    lines = [
        "CMD Probe Case Cleaning Report",
        "=" * 60,
        "",
        "Stage counts:",
    ]
    for stage, count in stages.items():
        lines.append(f"  {stage}: {count}")

    lines.append("")
    lines.append(f"Final cleaned cases: {len(cases)}")

    # Source distribution
    src_dist = Counter(c.get("source", "").split("/")[0] for c in cases)
    lines.append("")
    lines.append("Source distribution:")
    for src, count in src_dist.most_common():
        pct = count / len(cases) * 100
        lines.append(f"  {src}: {count} ({pct:.1f}%)")

    # Sub-source distribution
    sub_src_dist = Counter(c.get("source", "") for c in cases)
    lines.append("")
    lines.append("Sub-source distribution:")
    for src, count in sub_src_dist.most_common():
        lines.append(f"  {src}: {count}")

    # Rejection reasons
    if rejection_counts:
        lines.append("")
        lines.append("Rejection reasons:")
        for reason, count in sorted(rejection_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {reason}: {count}")

    # Query length stats
    q_lens = [len(c.get("query", "")) for c in cases]
    lines.append("")
    lines.append("Query length stats:")
    lines.append(f"  Min: {min(q_lens)}")
    lines.append(f"  Max: {max(q_lens)}")
    lines.append(f"  Mean: {sum(q_lens) / len(q_lens):.1f}")
    lines.append(f"  Median: {sorted(q_lens)[len(q_lens) // 2]}")

    # CMD relevance score distribution
    scores = [_score_cmd_relevance(c) for c in cases]
    lines.append("")
    lines.append("CMD relevance score distribution:")
    lines.append(f"  Min: {min(scores):.3f}")
    lines.append(f"  Max: {max(scores):.3f}")
    lines.append(f"  Mean: {sum(scores) / len(scores):.3f}")
    lines.append(f"  Median: {sorted(scores)[len(scores) // 2]:.3f}")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report written to {report_path}")


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _query_hash(query: str) -> str:
    return hashlib.sha256(query.casefold().strip().encode()).hexdigest()


def _normalize_case(raw: dict, source: str) -> dict:
    """Normalize a raw case dict to the canonical schema."""
    return {
        "case_id": raw.get("case_id", ""),
        "source": raw.get("source", source),
        "source_item_id": str(raw.get("source_item_id", "")),
        "sub_index": int(raw.get("sub_index", 0)),
        "query": str(raw.get("query", "")).strip(),
        "gold_answer": str(raw.get("gold_answer", "")).strip(),
        "question_type": raw.get("question_type", ""),
        "question_date": raw.get("question_date", ""),
        "haystack_session_count": int(raw.get("haystack_session_count", 0)),
        "answer_session_ids": raw.get("answer_session_ids", []),
        "relevant_apis": raw.get("relevant_apis", []),
        "item_metadata": raw.get("item_metadata", {}),
        # Only include haystack_sessions in trimmed form (keep first 2)
        "haystack_sessions": (raw.get("haystack_sessions") or [])[:2],
        "haystack_dates": (raw.get("haystack_dates") or [])[:10],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Main pipeline
# ═══════════════════════════════════════════════════════════════════════════


def clean_all(target: int = 540) -> list[dict]:
    """Run the full cleaning pipeline.

    Args:
        target: Target number of cleaned cases (150–250 range).
    """
    print("=" * 60)
    print("CMD Probe Case Cleaning Pipeline")
    print("=" * 60)

    # 1. Load
    print("\n[1/5] Loading raw files...")
    raw_sources = load_raw_files()

    # 2. Merge
    print("\n[2/5] Merging...")
    merged = merge_sources(raw_sources)

    # 3. Deduplicate
    print("\n[3/5] Deduplicating...")
    deduped = deduplicate(merged)

    # 4. Quality filter
    print("\n[4/5] Quality filtering...")
    filtered, rejection_counts = quality_filter(deduped)

    # 5. Sample
    print(f"\n[5/5] Stratified sampling (target={target})...")
    sampled = stratified_sample(filtered, target)

    # Save
    write_output(sampled, CLEAN_OUTPUT)

    stages = {
        "raw_total": sum(len(v) for v in raw_sources.values()),
        "merged": len(merged),
        "deduplicated": len(deduped),
        "quality_filtered": len(filtered),
        "final_sampled": len(sampled),
    }
    write_report(
        sampled,
        CLEAN_REPORT,
        stages=stages,
        rejection_counts=rejection_counts,
    )

    print("\nDone.")
    return sampled


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Clean CMD probe case datasets")
    parser.add_argument(
        "--target",
        type=int,
        default=200,
        help="Target number of cleaned cases (default: 200)",
    )
    args = parser.parse_args()
    clean_all(target=args.target)
