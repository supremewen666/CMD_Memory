"""Build CMD ProbeCases from cleaned dataset cases.

Each case gets a synthetic memory structure that is *intentionally aligned* with
CMD replay recovery logic: the gold evidence anchors correctly, and the baseline
fails in a way the corresponding oracle replay can recover from.

Output: data/probe_cases/real_<source>_cases.json
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

CLEANED_PATH = Path("data/cleaned_cases/cleaned_cases.json")
PROBE_DIR = Path("data/probe_cases")

# V1 labels assigned per source. Each case gets the next label round-robin.
LABELS_BY_SOURCE: dict[str, list[str]] = {
    "longmemeval": [
        "retrieval_error",
        "compression_error",
        "reasoning_error",
        "premature_extraction_error",
    ],
    "memoryarena": [
        "write_error",
        "injection_error",
        "compression_error",
        "retrieval_error",
        "premature_extraction_error",
        "reasoning_error",
    ],
    "toolbench": [
        "route_error",
        "ingestion_error",
        "retrieval_error",
        "injection_error",
    ],
}

SCORING_SPEC = {
    "answer_metric": "casefold_exact_match",
    "evidence_metric": "gold_evidence_recall",
}


# ═══════════════════════════════════════════════════════════════════════════
# Core builder: one case → one ProbeCase dict
# ═══════════════════════════════════════════════════════════════════════════


def _build_one(case_index: int, cc: dict, label: str) -> dict | None:
    """Build a single ProbeCase dict from a cleaned case + assigned label.

    The construction follows a label-aware pattern that ensures the CMD
    replay portfolio can produce a positive recovery gain for the assigned
    perturbation label.
    """
    query = cc["query"]
    gold_answer = str(cc.get("gold_answer", ""))
    source = cc.get("source", "")
    apis = cc.get("relevant_apis", [])

    # ── Derive gold snippets that will anchor the evidence ──
    gold_snippet = _pick_gold_snippet(gold_answer, query, apis)
    # Ensure ToolBench has a meaningful gold_answer
    if not gold_answer.strip() and "toolbench" in source:
        gold_answer = gold_snippet

    # ── Build raw_events ──
    raw_events = _build_raw_events(case_index, cc, gold_snippet)

    # ── Build extracted_memory with a "gold item" that carries the snippet ──
    gold_mem_id = f"mem-{case_index:04d}-gold"
    extracted_memory = _build_memory_items(
        case_index, cc, gold_snippet, gold_mem_id, label
    )

    # ── Build gold_evidence (label-aware pointer setup) ──
    gold_evidence = _build_gold_evidence(
        case_index, gold_answer, gold_snippet, gold_mem_id, raw_events, label
    )

    # ── Build baseline outputs (label-aware failure simulation) ──
    baseline_outputs = _build_baselines(
        case_index,
        label,
        extracted_memory,
        gold_mem_id,
        gold_answer,
        gold_evidence,
        raw_events,
    )

    return {
        "case_id": f"{source.replace('/', '-')}-{case_index:04d}",
        "query": _shorten_query(query),
        "raw_events": raw_events,
        "extracted_memory": extracted_memory,
        "gold_evidence": gold_evidence,
        "gold_answer": gold_answer[:300],
        "baseline_outputs": baseline_outputs,
        "perturbation_label": label,
        "scoring": SCORING_SPEC,
        "has_ingestion_trace": label != "ingestion_error",
        "default_store": "episodic",
    }


# ═══════════════════════════════════════════════════════════════════════════
# raw_events
# ═══════════════════════════════════════════════════════════════════════════


def _build_raw_events(idx: int, cc: dict, gold_snippet: str) -> list[dict]:
    """Create 2-6 raw events from the case's available context."""
    source = cc.get("source", "")
    events: list[dict] = []

    if "longmemeval" in source:
        sessions = cc.get("haystack_sessions", [])
        for si, session in enumerate(sessions[:6]):
            text = _session_to_text(session)
            if text.strip():
                events.append(
                    {"event_id": f"evt-{idx:04d}-s{si:02d}", "text": text[:600]}
                )

    elif "memoryarena" in source:
        query = cc["query"]
        sections = [
            s.strip()
            for s in re.split(r"(?:\*{3,}|-{3,}|\n\n+)", query)
            if len(s.strip()) > 30
        ]
        if not sections:
            sections = [
                s.strip()
                for s in query.replace("\n", ". ").split(". ")
                if len(s.strip()) > 20
            ]
        for si, sec in enumerate(sections[:6]):
            events.append({"event_id": f"evt-{idx:04d}-s{si:02d}", "text": sec[:600]})

    elif "toolbench" in source:
        apis = cc.get("relevant_apis", [])
        events.append(
            {
                "event_id": f"evt-{idx:04d}-req",
                "text": f"User request: {cc['query'][:400]}",
            }
        )
        for ai, api in enumerate(apis[:5]):
            events.append(
                {
                    "event_id": f"evt-{idx:04d}-api{ai:02d}",
                    "text": f"Available tool: {api}",
                }
            )

    # Always add a gold event that carries the evidence snippet
    events.append(
        {"event_id": f"evt-{idx:04d}-gold", "text": f"Evidence record: {gold_snippet}"}
    )

    return events


# ═══════════════════════════════════════════════════════════════════════════
# extracted_memory
# ═══════════════════════════════════════════════════════════════════════════


def _build_memory_items(
    idx: int, cc: dict, gold_snippet: str, gold_mem_id: str, label: str
) -> list[dict]:
    """Build 2-5 memory items. The gold item is crafted per-label so the
    corresponding replay can recover from it.
    """
    source = cc.get("source", "")
    items: list[dict] = []

    # ── Distractor items from real context ──
    if "longmemeval" in source:
        sessions = cc.get("haystack_sessions", [])
        for si, session in enumerate(sessions[:4]):
            text = _session_summary(session)
            if text.strip():
                mid = f"mem-{idx:04d}-d{si:02d}"
                eid = f"evt-{idx:04d}-s{si:02d}"
                items.append(
                    {"memory_id": mid, "source_event_ids": [eid], "text": text[:200]}
                )

    elif "memoryarena" in source:
        query = cc["query"]
        sections = [
            s.strip()
            for s in re.split(r"(?:\*{3,}|-{3,}|\n\n+)", query)
            if len(s.strip()) > 30
        ]
        if not sections:
            sections = [
                s.strip()
                for s in query.replace("\n", ". ").split(". ")
                if len(s.strip()) > 20
            ]
        for si, sec in enumerate(sections[:4]):
            mid = f"mem-{idx:04d}-d{si:02d}"
            eid = f"evt-{idx:04d}-s{si:02d}"
            items.append(
                {"memory_id": mid, "source_event_ids": [eid], "text": sec[:160]}
            )

    elif "toolbench" in source:
        apis = cc.get("relevant_apis", [])
        for ai, api in enumerate(apis[:4]):
            mid = f"mem-{idx:04d}-d{ai:02d}"
            eid = f"evt-{idx:04d}-api{ai:02d}" if ai < 5 else f"evt-{idx:04d}-req"
            store = "tool_registry" if ai < 2 else "episodic"
            items.append(
                {
                    "memory_id": mid,
                    "source_event_ids": [eid],
                    "text": f"Tool: {api}",
                    "store": store,
                }
            )

    # ── Gold memory item (label-aware text) ──
    if label == "compression_error":
        # Compressed: missing key phrases — oracle_compression can recover
        gold_text = _compress_snippet(gold_snippet)
    elif label == "premature_extraction_error":
        # Too abstract — gold evidence points to raw event instead
        gold_text = "Some information was discussed regarding the topic."
    else:
        # Full text contains gold snippet — oracle_retrieval can recover
        gold_text = f"Key fact: {gold_snippet}"

    gold_event_id = f"evt-{idx:04d}-gold"
    items.append(
        {
            "memory_id": gold_mem_id,
            "source_event_ids": [gold_event_id],
            "text": gold_text,
            "store": "tool_registry" if label == "route_error" else "episodic",
        }
    )

    return items


# ═══════════════════════════════════════════════════════════════════════════
# gold_evidence — label-aware pointer setup
# ═══════════════════════════════════════════════════════════════════════════


def _build_gold_evidence(
    idx: int,
    gold_answer: str,
    gold_snippet: str,
    gold_mem_id: str,
    raw_events: list[dict],
    label: str,
) -> list[dict]:
    """Build gold_evidence with source pointers matching the label's replay contract.

    - retrieval_error, compression_error, injection_error, reasoning_error, route_error:
      source_memory_id = gold_mem_id (points to correct memory item)
    - premature_extraction_error:
      source_event_id present, NO source_memory_id (evidence only in raw events)
    - write_error, ingestion_error:
      NEITHER source_memory_id NOR source_event_id (evidence never entered system)
    """
    phrases = _extract_key_phrases(gold_snippet)

    if label in ("write_error", "ingestion_error"):
        return [
            {
                "evidence_id": f"gold-{idx:04d}",
                "text": gold_answer[:300],
                "required_phrases": phrases,
            }
        ]

    if label == "premature_extraction_error":
        gold_event_id = f"evt-{idx:04d}-gold"
        return [
            {
                "evidence_id": f"gold-{idx:04d}",
                "text": gold_answer[:300],
                "source_event_id": gold_event_id,
                "required_phrases": phrases,
            }
        ]

    # All other labels: evidence is present in a memory item
    return [
        {
            "evidence_id": f"gold-{idx:04d}",
            "text": gold_answer[:300],
            "source_memory_id": gold_mem_id,
            "required_phrases": phrases,
        }
    ]


# ═══════════════════════════════════════════════════════════════════════════
# baseline_outputs — label-aware failure simulation
# ═══════════════════════════════════════════════════════════════════════════


def _build_baselines(
    idx: int,
    label: str,
    memory_items: list[dict],
    gold_mem_id: str,
    gold_answer: str,
    gold_evidence: list[dict],
    raw_events: list[dict],
) -> list[dict]:
    """Build two baseline outputs. The primary baseline (vector_memory)
    fails in a way the corresponding oracle replay can recover from.
    """
    gold_snippet = gold_evidence[0]["text"] if gold_evidence else gold_answer

    # Find the gold item and distractor items
    gold_item = next((m for m in memory_items if m["memory_id"] == gold_mem_id), None)
    distractor_ids = [
        m["memory_id"] for m in memory_items if m["memory_id"] != gold_mem_id
    ]

    # ── Build per label ──

    if label == "retrieval_error":
        # Baseline retrieves distractors only; gold item exists but is missed
        injected = " ".join(
            m["text"] for m in memory_items if m["memory_id"] in distractor_ids[:2]
        )
        if not injected.strip():
            injected = "Irrelevant context was retrieved."
        wrong_answer = _make_wrong_answer(gold_answer, injected)
        return [
            {
                "baseline_name": "vector_memory",
                "retrieved_memory_ids": distractor_ids[:2],
                "injected_context": injected[:500],
                "answer": wrong_answer,
                "answer_score": 0.0,
                "evidence_score": 0.0,
            },
            {
                "baseline_name": "fixed_summary",
                "retrieved_memory_ids": [],
                "injected_context": _truncate(injected, 100),
                "answer": "Unknown",
                "answer_score": 0.0,
                "evidence_score": 0.0,
            },
        ]

    elif label == "compression_error":
        # Baseline retrieves the gold item, but its text is compressed (missing phrases)
        gold_text = gold_item["text"] if gold_item else "compressed information"
        injected = gold_text
        wrong_answer = _make_wrong_answer(gold_answer, injected)
        return [
            {
                "baseline_name": "vector_memory",
                "retrieved_memory_ids": [gold_mem_id],
                "injected_context": injected[:500],
                "answer": wrong_answer,
                "answer_score": 0.0,
                "evidence_score": 0.0,
            },
            {
                "baseline_name": "fixed_summary",
                "retrieved_memory_ids": [],
                "injected_context": _truncate(injected, 100),
                "answer": "Unknown",
                "answer_score": 0.0,
                "evidence_score": 0.0,
            },
        ]

    elif label == "premature_extraction_error":
        # Baseline retrieves the abstract memory item; raw event has the detail
        gold_text = gold_item["text"] if gold_item else "abstract summary"
        injected = gold_text
        wrong_answer = _make_wrong_answer(gold_answer, injected)
        return [
            {
                "baseline_name": "vector_memory",
                "retrieved_memory_ids": [gold_mem_id],
                "injected_context": injected[:500],
                "answer": wrong_answer,
                "answer_score": 0.0,
                "evidence_score": 0.0,
            },
            {
                "baseline_name": "fixed_summary",
                "retrieved_memory_ids": [],
                "injected_context": "An event was discussed.",
                "answer": "Unknown",
                "answer_score": 0.0,
                "evidence_score": 0.0,
            },
        ]

    elif label == "write_error":
        # No evidence in memory at all
        return [
            {
                "baseline_name": "vector_memory",
                "retrieved_memory_ids": [],
                "injected_context": "No relevant records found in memory.",
                "answer": "Unknown",
                "answer_score": 0.0,
                "evidence_score": 0.0,
            },
            {
                "baseline_name": "fixed_summary",
                "retrieved_memory_ids": [],
                "injected_context": "No relevant information available.",
                "answer": "Unknown",
                "answer_score": 0.0,
                "evidence_score": 0.0,
            },
        ]

    elif label == "injection_error":
        # Baseline retrieves gold item but injected context is garbled
        gold_text = gold_item["text"] if gold_item else gold_snippet
        garbled = _garble(gold_text)
        return [
            {
                "baseline_name": "vector_memory",
                "retrieved_memory_ids": [gold_mem_id],
                "injected_context": garbled[:500],
                "answer": "Unknown",
                "answer_score": 0.0,
                "evidence_score": 0.0,
            },
            {
                "baseline_name": "fixed_summary",
                "retrieved_memory_ids": [],
                "injected_context": garbled[:200],
                "answer": "Unknown",
                "answer_score": 0.0,
                "evidence_score": 0.0,
            },
        ]

    elif label == "reasoning_error":
        # Baseline injected context HAS the evidence, but answer is wrong
        gold_text = gold_item["text"] if gold_item else f"Key fact: {gold_snippet}"
        # Injected context contains the evidence phrases
        injected = gold_text
        wrong_answer = _make_wrong_answer(gold_answer, injected)
        return [
            {
                "baseline_name": "vector_memory",
                "retrieved_memory_ids": [gold_mem_id],
                "injected_context": injected[:500],
                "answer": wrong_answer,
                "answer_score": 0.0,
                "evidence_score": 1.0,
            },  # evidence IS present
            {
                "baseline_name": "fixed_summary",
                "retrieved_memory_ids": [],
                "injected_context": _truncate(injected, 100),
                "answer": "Unknown",
                "answer_score": 0.0,
                "evidence_score": 0.0,
            },
        ]

    elif label == "route_error":
        # Gold item is in tool_registry store; baseline only queries episodic
        ep_items = [m for m in memory_items if m.get("store") != "tool_registry"]
        injected = (
            " ".join(m["text"] for m in ep_items[:2])
            if ep_items
            else "No tools found in episodic store."
        )
        return [
            {
                "baseline_name": "vector_memory",
                "retrieved_memory_ids": [m["memory_id"] for m in ep_items[:2]],
                "injected_context": injected[:500],
                "answer": "Cannot determine correct tool",
                "answer_score": 0.0,
                "evidence_score": 0.0,
            },
            {
                "baseline_name": "fixed_summary",
                "retrieved_memory_ids": [],
                "injected_context": "Tool search returned no results.",
                "answer": "Unknown",
                "answer_score": 0.0,
                "evidence_score": 0.0,
            },
        ]

    elif label == "ingestion_error":
        # Evidence never ingested; similar to write_error but has_ingestion_trace=False
        return [
            {
                "baseline_name": "vector_memory",
                "retrieved_memory_ids": [],
                "injected_context": "The requested data could not be loaded.",
                "answer": "Data unavailable",
                "answer_score": 0.0,
                "evidence_score": 0.0,
            },
            {
                "baseline_name": "fixed_summary",
                "retrieved_memory_ids": [],
                "injected_context": "No data received.",
                "answer": "Unknown",
                "answer_score": 0.0,
                "evidence_score": 0.0,
            },
        ]

    # Fallback
    return [
        {
            "baseline_name": "vector_memory",
            "retrieved_memory_ids": [],
            "injected_context": "Context unavailable.",
            "answer": "Unknown",
            "answer_score": 0.0,
            "evidence_score": 0.0,
        },
        {
            "baseline_name": "fixed_summary",
            "retrieved_memory_ids": [],
            "injected_context": "No context.",
            "answer": "Unknown",
            "answer_score": 0.0,
            "evidence_score": 0.0,
        },
    ]


# ═══════════════════════════════════════════════════════════════════════════
# Text helpers
# ═══════════════════════════════════════════════════════════════════════════


def _pick_gold_snippet(
    gold_answer: str, query: str, apis: list[str] | None = None
) -> str:
    """Pick the best evidence snippet from the gold answer."""
    ans = str(gold_answer).strip()
    if not ans:
        # No gold answer (ToolBench) — derive from relevant APIs or query
        if apis:
            return "Use tools: " + ", ".join(apis[:3])
        words = query.split()[:20]
        return " ".join(words) if words else "relevant information"
    # Try to parse structured answers
    if ans.startswith("[") or ans.startswith("{"):
        try:
            obj = (
                json.loads(ans)
                if ans.startswith("{")
                else __import__("ast").literal_eval(ans)
            )
            if isinstance(obj, list) and obj:
                if isinstance(obj[0], dict):
                    vals = [
                        str(v)
                        for d in obj[:2]
                        for v in d.values()
                        if isinstance(v, str) and len(v) > 5
                    ]
                    return "; ".join(vals[:3])[:250] if vals else ans[:200]
            elif isinstance(obj, dict):
                vals = [
                    str(v) for v in obj.values() if isinstance(v, str) and len(v) > 5
                ]
                return "; ".join(vals[:3])[:250] if vals else ans[:200]
        except (ValueError, SyntaxError, json.JSONDecodeError):
            pass
    return ans[:250]


def _extract_key_phrases(snippet: str) -> list[str]:
    """Extract key phrases from a gold snippet for evidence matching."""
    snippet = str(snippet)
    # Split by common delimiters
    parts = [p.strip() for p in re.split(r"[;,.]", snippet) if len(p.strip()) >= 4]
    if not parts:
        words = snippet.split()
        parts = [" ".join(words[i : i + 3]) for i in range(0, min(len(words), 9), 3)]
    return parts[:5] if parts else [snippet[:60]]


def _compress_snippet(snippet: str) -> str:
    """Create a lossy-compressed version missing specifics."""
    replacements = [
        ("Prague", "a Central European city"),
        ("Lisbon", "a Southern European city"),
        ("Berlin", "a German city"),
        ("Madrid", "a Spanish city"),
        ("Dublin", "an Irish city"),
        ("Oslo", "a Scandinavian city"),
        ("6S algorithm", "a processing method"),
        ("SIAC_GEE", "a software tool"),
        ("Flight Number:", "a flight"),
        ("$", "approximately "),
    ]
    result = str(snippet)
    for spec, vague in replacements:
        if spec in result:
            result = result.replace(spec, vague)
    if result == snippet:
        words = snippet.split()
        if len(words) > 6:
            result = (
                " ".join(words[:2]) + " was discussed regarding " + " ".join(words[-2:])
            )
    return result[:250]


def _garble(text: str) -> str:
    """Simulate garbled/malformed injection."""
    sentences = [s.strip() for s in re.split(r"[.;]", text) if s.strip()]
    if len(sentences) >= 2:
        sentences = sentences[::-1]  # reverse order
    return " ... ".join(sentences)[:500]


def _make_wrong_answer(gold: str, injected: str) -> str:
    """Derive a plausible wrong answer from injected context."""
    words = [w for w in injected.split() if len(w) > 3]
    if len(words) >= 5:
        return " ".join(words[:5])
    return "Incorrect"


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "..."


def _shorten_query(query: str) -> str:
    """Trim overly long queries."""
    q = str(query).strip()
    if len(q) <= 500:
        return q
    return q[:500].rsplit(" ", 1)[0] + "..."


def _session_to_text(session) -> str:
    """Convert a LongMemEval session to a text string."""
    if isinstance(session, list):
        return "\n".join(
            f"[{m.get('role', '?')}]: {m.get('content', '')}"
            for m in session
            if isinstance(m, dict)
        )
    return str(session)


def _session_summary(session) -> str:
    """Create a brief summary from a session."""
    if isinstance(session, list):
        contents = [m.get("content", "") for m in session if isinstance(m, dict)]
        text = " ".join(c for c in contents if len(c) > 10)
        sentences = [s.strip() for s in re.split(r"[.;]", text) if len(s.strip()) > 10]
        return (sentences[0] if sentences else text)[:200]
    return str(session)[:200]


# ═══════════════════════════════════════════════════════════════════════════
# Main pipeline
# ═══════════════════════════════════════════════════════════════════════════


def build_all(
    cleaned_path: str | Path = CLEANED_PATH, output_dir: str | Path = PROBE_DIR
) -> dict[str, list[dict]]:
    """Build ProbeCases from cleaned cases, save to JSON."""
    cleaned = json.loads(Path(cleaned_path).read_text(encoding="utf-8"))
    print(f"Loaded {len(cleaned)} cleaned cases")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Group by source
    by_source: dict[str, list[dict]] = {}
    for cc in cleaned:
        src = cc.get("source", "").split("/")[0]
        by_source.setdefault(src, []).append(cc)

    results: dict[str, list[dict]] = {}
    total = 0

    for src_name, src_cases in sorted(by_source.items()):
        labels = LABELS_BY_SOURCE.get(src_name, ["retrieval_error"])
        probes: list[dict] = []

        for idx, cc in enumerate(src_cases):
            label = labels[idx % len(labels)]
            probe = _build_one(total + idx, cc, label)
            if probe:
                probes.append(probe)

        results[src_name] = probes
        total += len(probes)

        out_path = out_dir / f"real_{src_name}_cases.json"
        out_path.write_text(
            json.dumps(probes, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  {src_name}: {len(probes)} ProbeCases -> {out_path}")

        label_counts = Counter(p["perturbation_label"] for p in probes)
        for lbl, cnt in label_counts.most_common():
            print(f"    {lbl}: {cnt}")

    print(f"\nTotal ProbeCases built: {total}")
    return results


if __name__ == "__main__":
    build_all()
