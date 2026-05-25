"""Download 150-250 cases from three HuggingFace datasets for CMD probe suite scaling.

Sources:
  1. ZexueHe/memoryarena — multi-session agent tasks (bundled_shopping, group_travel,
     progressive_search). Each item has multiple QA sub-pairs against a memory context.
  2. xiaowu0162/longmemeval-cleaned — memory-focused QA with haystack sessions.
  3. tuandunghcmut/toolbench-v1 — tool-use conversations (sampled for relevance).

Output: data/raw_cases/raw_cases.json — a unified list of raw case dicts ready for
cleaning and eventual ProbeCase construction.
"""

from __future__ import annotations

import json
import hashlib
import random
import time
from pathlib import Path

RAW_DIR = Path("data/raw_cases")
RAW_OUTPUT = RAW_DIR / "raw_cases.json"
TARGET_MIN = 150
TARGET_MAX = 250

# Per-source sampling targets.
PER_SOURCE_TARGET = {
    "memoryarena": 220,
    "longmemeval": 220,
    "toolbench": 220,
}

# Max questions to extract per MemoryArena item (first N questions are
# usually context-independent memory lookups; later questions often chain).
MAX_QUESTIONS_PER_ARENA_ITEM = 5


# ═══════════════════════════════════════════════════════════════════════════
# Stage 1: MemoryArena (JSONL via raw HTTP)
# ═══════════════════════════════════════════════════════════════════════════


def _download_memoryarena_jsonl(rel_path: str) -> list[dict]:
    """Download a single MemoryArena JSONL file."""
    import urllib.request

    url = f"https://huggingface.co/datasets/ZexueHe/memoryarena/resolve/main/{rel_path}"
    req = urllib.request.Request(url, headers={"User-Agent": "CMD-probe-builder/0.1"})
    resp = urllib.request.urlopen(req, timeout=60)
    lines = resp.read().decode("utf-8").strip().split("\n")
    return [json.loads(line) for line in lines if line.strip()]


def _extract_memoryarena_cases() -> list[dict]:
    """Extract individual QA pairs from MemoryArena task items.

    Each MemoryArena item has a ``questions`` list and an ``answers`` list.
    We flatten each question-answer pair into one case dict.
    """
    sources = [
        ("bundled_shopping/data.jsonl", "memoryarena/bundled_shopping"),
        ("group_travel_planner/data.jsonl", "memoryarena/group_travel_planner"),
        ("progressive_search/data.jsonl", "memoryarena/progressive_search"),
    ]

    cases: list[dict] = []
    for rel_path, src_name in sources:
        try:
            items = _download_memoryarena_jsonl(rel_path)
        except Exception as exc:
            print(f"  [WARN] Failed to download {rel_path}: {exc}")
            continue
        print(f"  {src_name}: {len(items)} items")

        for item in items:
            questions = item.get("questions", [])
            answers = item.get("answers", [])
            if not isinstance(questions, list):
                questions = [str(questions)]
            if not isinstance(answers, list):
                answers = [str(answers)]

            item_meta = {
                k: v for k, v in item.items() if k not in ("questions", "answers")
            }

            for idx, (q, a) in enumerate(zip(questions, answers)):
                if idx >= MAX_QUESTIONS_PER_ARENA_ITEM:
                    break
                q_str = str(q).strip()
                a_str = str(a).strip()
                if not q_str or not a_str:
                    continue
                # Skip overly long questions (math papers, not suitable for memory probes)
                if len(q_str) > 3000:
                    continue

                case_id = _make_case_id(src_name, item.get("id", idx), idx)
                cases.append(
                    {
                        "case_id": case_id,
                        "source": src_name,
                        "source_item_id": str(item.get("id", idx)),
                        "sub_index": idx,
                        "query": q_str,
                        "gold_answer": a_str,
                        "item_metadata": item_meta,
                    }
                )

        print(f"    -> {len(cases)} QA pairs so far")

    return cases


# ═══════════════════════════════════════════════════════════════════════════
# Stage 2: LongMemEval (JSON via raw HTTP)
# ═══════════════════════════════════════════════════════════════════════════


def _download_longmemeval_json(fname: str) -> list[dict]:
    """Download a LongMemEval JSON file."""
    import urllib.request

    url = f"https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/{fname}"
    req = urllib.request.Request(url, headers={"User-Agent": "CMD-probe-builder/0.1"})
    resp = urllib.request.urlopen(req, timeout=120)
    return json.loads(resp.read())


def _extract_longmemeval_cases() -> list[dict]:
    """Extract cases from LongMemEval cleaned + oracle files.

    Each LongMemEval item is already a single QA case with haystack sessions
    as context.  We prefer the S (single-session) split for cleaner memory
    attribution boundaries.
    """
    files = [
        ("longmemeval_s_cleaned.json", "longmemeval/single"),
        ("longmemeval_oracle.json", "longmemeval/oracle"),
    ]
    # M split is large and often times out; skip for now.

    cases: list[dict] = []
    for fname, src_name in files:
        try:
            items = _download_longmemeval_json(fname)
        except Exception as exc:
            print(f"  [WARN] Failed to download {fname}: {exc}")
            continue
        print(f"  {src_name}: {len(items)} items")

        for item in items:
            q = str(item.get("question", "")).strip()
            a = str(item.get("answer", "")).strip()
            if not q or not a:
                continue
            # Skip overly short questions (likely trivial)
            if len(q) < 15:
                continue

            case_id = _make_case_id(src_name, item.get("question_id", ""), 0)
            cases.append(
                {
                    "case_id": case_id,
                    "source": src_name,
                    "source_item_id": str(item.get("question_id", "")),
                    "sub_index": 0,
                    "query": q,
                    "gold_answer": a,
                    "question_type": item.get("question_type", ""),
                    "question_date": item.get("question_date", ""),
                    "haystack_session_count": len(item.get("haystack_sessions", [])),
                    "answer_session_ids": item.get("answer_session_ids", []),
                    "haystack_sessions": item.get("haystack_sessions", []),
                    "haystack_dates": item.get("haystack_dates", []),
                }
            )

        print(f"    -> {len(cases)} QA pairs so far")

    return cases


# ═══════════════════════════════════════════════════════════════════════════
# Stage 3: ToolBench (parquet via datasets library)
# ═══════════════════════════════════════════════════════════════════════════


def _extract_toolbench_cases() -> list[dict]:
    """Extract ToolBench benchmark instructions + training sample.

    Benchmark g1/g2/g3 instructions are downloaded in full (~500 rows total).
    Additionally, a small sample from the training data is included for
    conversation-based memory cases.
    """
    cases: list[dict] = []

    # ── Benchmark instructions (full download) ──
    import urllib.request
    import io
    import pyarrow.parquet as pq

    splits = [
        ("g1_instruction", "benchmark/g1_instruction-00000-of-00001.parquet"),
        ("g2_instruction", "benchmark/g2_instruction-00000-of-00001.parquet"),
        ("g3_instruction", "benchmark/g3_instruction-00000-of-00001.parquet"),
    ]

    for split, parquet_file in splits:
        try:
            url = f"https://huggingface.co/datasets/tuandunghcmut/toolbench-v1/resolve/main/{parquet_file}"
            req = urllib.request.Request(
                url, headers={"User-Agent": "CMD-probe-builder/0.1"}
            )
            resp = urllib.request.urlopen(req, timeout=60)
            table = pq.read_table(io.BytesIO(resp.read()))
        except Exception as exc:
            print(f"  [WARN] Failed to download {parquet_file}: {exc}")
            continue

        for i in range(len(table)):
            row = table.slice(i, 1).to_pydict()
            query = str(
                row.get("query", [""])[0]
                if isinstance(row.get("query"), list)
                else row.get("query", "")
            ).strip()
            if not query or len(query) < 20:
                continue

            qid = str(
                row.get("query_id", [str(i)])[0]
                if isinstance(row.get("query_id"), list)
                else row.get("query_id", i)
            )

            # Parse relevant_apis (JSON string of [[tool, api], ...])
            relevant = row.get("relevant_apis", ["[]"])
            rel_str = relevant[0] if isinstance(relevant, list) else str(relevant)
            try:
                apis = json.loads(rel_str) if isinstance(rel_str, str) else rel_str
            except json.JSONDecodeError:
                apis = []
            api_names: list[str] = []
            if isinstance(apis, list):
                for entry in apis:
                    if isinstance(entry, list) and len(entry) >= 2:
                        api_names.append(f"{entry[0]}/{entry[1]}")

            api_raw = row.get("api_list", [""])
            api_str = str(api_raw[0] if isinstance(api_raw, list) else api_raw)[:500]

            cases.append(
                {
                    "case_id": _make_case_id(f"toolbench/{split}", qid, 0),
                    "source": f"toolbench/{split}",
                    "source_item_id": str(qid),
                    "sub_index": 0,
                    "query": query,
                    "gold_answer": "",
                    "relevant_apis": api_names,
                    "api_list_str": api_str,
                }
            )

        print(
            f"  toolbench/{split}: {len([c for c in cases if split in c['source']])} cases"
        )

    # ── Training data sample (50 conversations) ──
    try:
        from datasets import load_dataset

        ds = load_dataset(
            "tuandunghcmut/toolbench-v1", "default", split="train", streaming=True
        )
        count = 0
        for row in ds:
            conversations = row.get("conversations", [])
            if not conversations:
                continue

            # Extract the user's first substantive query as the case query
            user_msgs = [
                m.get("value", "")
                for m in conversations
                if m.get("from") == "user" and len(str(m.get("value", ""))) > 30
            ]
            if not user_msgs:
                continue

            query = str(user_msgs[0]).strip()
            if len(query) < 30:
                continue

            # Full conversation as context
            conv_text = "\n".join(
                f"[{m.get('from', 'unknown')}]: {str(m.get('value', ''))[:500]}"
                for m in conversations[:10]
            )

            cases.append(
                {
                    "case_id": _make_case_id(
                        "toolbench/train", row.get("id", count), 0
                    ),
                    "source": "toolbench/train",
                    "source_item_id": str(row.get("id", count)),
                    "sub_index": 0,
                    "query": query,
                    "gold_answer": "",
                    "relevant_apis": [],
                    "conversation_context": conv_text,
                }
            )
            count += 1
            if count >= 80:
                break
        print(f"  toolbench/train: {count} conversations sampled")
    except Exception as exc:
        print(f"  [WARN] ToolBench training data unavailable: {exc}")

    return cases


# ═══════════════════════════════════════════════════════════════════════════
# Stage 4: Basic cleaning & sampling
# ═══════════════════════════════════════════════════════════════════════════


def _basic_clean(cases: list[dict], max_per_source: int) -> list[dict]:
    """Deduplicate, filter, and sample cases.

    Cleaning rules:
    1. Remove exact-duplicate queries (by casefolded query hash).
    2. Remove cases with empty query or gold_answer.
    3. Remove cases with queries shorter than 15 chars or longer than 2000.
    4. Sample evenly within each source, capped at max_per_source.
    """
    # Dedup by query content
    seen: set[str] = set()
    deduped: list[dict] = []
    for c in cases:
        q_key = c["query"].casefold().strip()
        if q_key not in seen:
            seen.add(q_key)
            deduped.append(c)
    print(
        f"  After dedup: {len(deduped)} (removed {len(cases) - len(deduped)} duplicates)"
    )

    # Length filter
    filtered: list[dict] = []
    for c in deduped:
        q_len = len(c["query"])
        a_len = len(c.get("gold_answer", ""))
        if 15 <= q_len <= 2000 and (not c["gold_answer"] or a_len >= 1):
            filtered.append(c)
    print(
        f"  After length filter: {len(filtered)} (removed {len(deduped) - len(filtered)})"
    )

    # Sample per source using PER_SOURCE_TARGET caps
    by_source: dict[str, list[dict]] = {}
    for c in filtered:
        src_key = c["source"].split("/")[0]
        by_source.setdefault(src_key, []).append(c)

    sampled: list[dict] = []
    for src_key, src_cases in sorted(by_source.items()):
        random.seed(42)
        random.shuffle(src_cases)
        cap = PER_SOURCE_TARGET.get(src_key, max_per_source)
        take = min(len(src_cases), cap)
        sampled.extend(src_cases[:take])
        print(f"  {src_key}: {len(src_cases)} available -> sampled {take}")

    return sampled


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _make_case_id(source: str, item_id: str | int, sub_idx: int) -> str:
    raw = f"{source}:{item_id}:{sub_idx}:{time.monotonic()}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return f"{source.replace('/', '-')}-{item_id}-{sub_idx}-{digest}"


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════


def download_all(
    output_path: str | Path | None = None,
    *,
    target_per_source: int = 80,
) -> list[dict]:
    """Download and extract cases from all three sources.

    Returns a unified list of raw case dicts.  Also writes JSON if
    *output_path* is provided.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(output_path) if output_path else RAW_OUTPUT

    print("=" * 60)
    print("Stage 1: MemoryArena")
    ma_cases = _extract_memoryarena_cases()
    print(f"  Total MemoryArena QA pairs: {len(ma_cases)}")

    print("\nStage 2: LongMemEval")
    lm_cases = _extract_longmemeval_cases()
    print(f"  Total LongMemEval QA pairs: {len(lm_cases)}")

    print("\nStage 3: ToolBench")
    tb_cases = _extract_toolbench_cases()
    print(f"  Total ToolBench QA pairs: {len(tb_cases)}")

    all_cases = ma_cases + lm_cases + tb_cases
    print(f"\nAll raw cases: {len(all_cases)}")

    print("\nStage 4: Basic cleaning & sampling")
    cleaned = _basic_clean(all_cases, max_per_source=target_per_source)
    print(f"Final: {len(cleaned)} cases")

    out.write_text(json.dumps(cleaned, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved to {out}")

    return cleaned


if __name__ == "__main__":
    download_all()
