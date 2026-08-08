"""Adapter for MemFail memory-system stress-test datasets (external validity arm).

MemFail (arXiv 2605.26667, ``github.com/ishirgarg/MemFail``) is a third-party
diagnostic benchmark that isolates memory failure modes instead of hiding them
behind aggregate accuracy. It ships five CSV datasets across four tasks. Like
STALE, MemFail records carry *no* pipeline structure of their own: each row is a
set of facts plus a graded question. This adapter synthesizes the full CMD
``ProbeCase`` contract (raw events, extracted memory, gold evidence, failing
baseline) around each graded query.

Task -> CMD step action mapping (see ``core/labels.py`` for the legal set):

===========================  ====================  ==================================
MemFail task                 CMD label             Why
===========================  ====================  ==================================
Long-Hop                     ``retrieval_error``   Chain facts land in separate
                                                   storage events; the chain must be
                                                   retrieved and composed.
Conditional-Facts (E/H)      ``granularity_error`` Summarization dropped the
                                                   qualifying condition — the stored
                                                   item sits at the wrong
                                                   granularity level.
Coexisting-Facts             ``item_conflict``     Compatible preferences wrongly
                                                   reconciled/overwritten as if they
                                                   contradicted each other.
Persona-Retrieval, misleading ``safety_error``     The system should abstain about an
                                                   unknown person but instead
                                                   surfaces a stored profile.
Persona-Retrieval, direct    ``retrieval_error``   Idiosyncratic persona detail must
                                                   be retrieved from the profile.
===========================  ====================  ==================================

Structural conventions shared with ``stale.py``:

- One ``RawEvent`` per underlying conversational/storage unit. Long-Hop keeps
  each chain fact in its own event (that separation is the benchmark's whole
  point); coexisting keeps each preference statement separate; conditional and
  persona split their essay into sentence groups.
- ``extracted_memory`` models what a memory system *would have stored*,
  including the failure. For Conditional-Facts the stored item is the
  condition-stripped version (that IS the injected fault) while the raw event
  retains the full rule; returning to raw provenance is what makes the case
  repairable.
- ``source_event_ids`` always point at real ``raw_events`` entries so item->event
  provenance is traversable for CMD's item actions.
- ``case_id`` is ``memfail-<task>-<row id>-<query idx>``: deterministic, derived
  only from stable row identity, never hashed over mutable content.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import json
import re
from pathlib import Path
from typing import Any

from cmd_audit.core.models import ProbeCase

# ── Task registry ─────────────────────────────────────────────────────────────

#: MemFail task slug -> default CSV filename inside ``--csv-dir``.
MEMFAIL_CSV_FILENAMES: dict[str, str] = {
    "long_hop": "long_hop_chains.csv",
    "coexisting": "coexisting_facts_dataset.csv",
    "conditional_easy": "conditional_facts_dataset_easy.csv",
    "conditional_hard": "conditional_facts_dataset_hard.csv",
    "persona": "persona_dataset.csv",
}

#: MemFail task slug -> CMD step action. Persona is per-query (see module doc),
#: so it is resolved by the converter rather than looked up here.
MEMFAIL_TASK_LABELS: dict[str, str] = {
    "long_hop": "retrieval_error",
    "coexisting": "item_conflict",
    "conditional_easy": "granularity_error",
    "conditional_hard": "granularity_error",
}

MEMFAIL_TASKS: tuple[str, ...] = tuple(MEMFAIL_CSV_FILENAMES)

_SCORING_SPEC = {
    "answer_metric": "casefold_exact_match",
    "evidence_metric": "gold_evidence_recall",
}


class MemFailSchemaError(ValueError):
    """Raised when a MemFail CSV row does not match the published schema."""


# ── Public trio (mirrors stale.py) ────────────────────────────────────────────


def load_memfail_probe_cases(
    path: str | Path,
    *,
    task: str,
    limit: int = 0,
) -> list[ProbeCase]:
    """Load one MemFail CSV and convert it to CMD probe cases.

    ``limit=0`` is the production default and keeps the full dataset. Positive
    limits are only for smoke tests and local debugging.
    """
    task = _validate_task(task)
    cases: list[ProbeCase] = []
    for row_index, row in enumerate(_read_csv_rows(path)):
        cases.extend(
            memfail_record_to_probe_cases(row, task=task, row_index=row_index)
        )
        if limit and len(cases) >= limit:
            return cases[:limit]
    return cases


def memfail_record_to_probe_cases(
    record: dict[str, Any],
    *,
    task: str,
    row_index: int = 0,
) -> list[ProbeCase]:
    """Convert one MemFail CSV row into one ``ProbeCase`` per graded query."""
    task = _validate_task(task)
    if task == "long_hop":
        mappings = _long_hop_case_mappings(record, row_index)
    elif task == "coexisting":
        mappings = _coexisting_case_mappings(record, row_index)
    elif task in ("conditional_easy", "conditional_hard"):
        mappings = _conditional_case_mappings(record, row_index, task=task)
    else:
        mappings = _persona_case_mappings(record, row_index)
    return [ProbeCase.from_mapping_v1(mapping) for mapping in mappings]


def write_memfail_probe_cases(
    input_path: str | Path,
    output_path: str | Path,
    *,
    task: str,
    limit: int = 0,
) -> Path:
    """Convert a MemFail CSV to CMD ProbeCase JSON, keeping full data by default."""
    cases = load_memfail_probe_cases(input_path, task=task, limit=limit)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = [_case_to_mapping(case) for case in cases]
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


# ── Long-Hop: retrieval_error ─────────────────────────────────────────────────


def _long_hop_case_mappings(
    record: dict[str, Any],
    row_index: int,
) -> list[dict[str, Any]]:
    """Build the single graded case for one Long-Hop chain row.

    Each ``fact_N`` becomes its own raw event and its own atomic memory item —
    the benchmark scatters the chain across separate storage events on purpose,
    so concatenating them would delete the failure mode. The baseline recalls
    only the chain's first link, so the terminal answer is unreachable until
    retrieval is repaired to surface the remaining links.
    """
    row_id = _row_id(record, "id", f"row{row_index:04d}")
    facts = _ordered_optional_fields(record, "fact_", 4)
    if not facts:
        raise MemFailSchemaError(
            f"long_hop row {row_id!r} has no fact_1..fact_4 columns populated"
        )
    chain = _ordered_optional_fields(record, "chain_", 5)
    question = _plain_question(_required_cell(record, "graded_question", row_id))
    # ``ground_truth_answer`` is free answer text and always equals the choice
    # body named by ``correct_choice`` (verified across the shipped CSV). Gold is
    # the answer TEXT, not the letter: CMD's answer scorer is a casefolded
    # phrase/exact match against generated prose, and a bare "C" would neither
    # phrase-match a generated sentence nor survive evidence recall.
    gold_answer = _required_cell(record, "ground_truth_answer", row_id)

    raw_events = [{"event_id": "e_query", "text": f"User asks: {question}"}]
    extracted_memory: list[dict[str, Any]] = []
    for hop_index, fact in enumerate(facts, start=1):
        event_id = f"e_fact{hop_index}"
        raw_events.append(
            {
                "event_id": event_id,
                "text": f"Storage event {hop_index}: {fact}",
            }
        )
        extracted_memory.append(
            {
                "memory_id": f"m_fact{hop_index}",
                "text": fact,
                "source_event_ids": [event_id],
                "store": "episodic",
            }
        )

    # Distractor: the four unchosen multiple-choice options, stored as one
    # plausible-but-irrelevant recalled item.
    distractor = _long_hop_distractor(record)
    if distractor:
        raw_events.append({"event_id": "e_distractor", "text": distractor})
        extracted_memory.append(
            {
                "memory_id": "m_distractor",
                "text": distractor,
                "source_event_ids": ["e_distractor"],
                "store": "episodic",
            }
        )

    terminal_fact = facts[-1]
    chain_note = " -> ".join(chain) if chain else ""
    gold_evidence = [
        {
            "evidence_id": "ev_terminal",
            "text": terminal_fact,
            "source_memory_id": f"m_fact{len(facts)}",
            "source_event_id": f"e_fact{len(facts)}",
            "required_phrases": _required_phrases(gold_answer),
        }
    ]

    # The memory system retrieved only the chain head; the terminal link never
    # entered context, so it answers with an unrelated option.
    recalled = ["m_fact1"] + (["m_distractor"] if distractor else [])
    injected = "\n".join(
        item["text"] for item in extracted_memory if item["memory_id"] in recalled
    )
    failing_answer = _long_hop_failing_answer(record, gold_answer)

    mapping = {
        "case_id": f"memfail-long_hop-{row_id}-q0",
        "query": question,
        "raw_events": raw_events,
        "extracted_memory": extracted_memory,
        "gold_evidence": gold_evidence,
        "gold_answer": gold_answer,
        "baseline_outputs": _baseline_outputs(
            failing_answer=failing_answer,
            retrieved_memory_ids=recalled,
            injected_context=injected,
            summary_note=(
                "Session summary kept the chain topic but not the transitive links."
            ),
        ),
        "perturbation_label": "retrieval_error",
        "scoring": dict(_SCORING_SPEC),
        "default_store": "episodic",
        "source": "memfail",
        "memfail_task": "long_hop",
        "memfail_row_id": row_id,
        "memfail_hop_count": _cell(record, "hop_count"),
        "memfail_chain": chain_note,
    }
    return [mapping]


def _long_hop_distractor(record: dict[str, Any]) -> str:
    """Join the incorrect multiple-choice options into one distractor item."""
    correct = _cell(record, "correct_choice").casefold()
    others = [
        _cell(record, f"choice_{letter}")
        for letter in "abcde"
        if letter != correct and _cell(record, f"choice_{letter}")
    ]
    if not others:
        return ""
    return "Unrelated remembered routines: " + "; ".join(others) + "."


def _long_hop_failing_answer(record: dict[str, Any], gold_answer: str) -> str:
    """Pick a wrong multiple-choice option as the failing baseline answer."""
    for letter in "abcde":
        choice = _cell(record, f"choice_{letter}")
        if choice and choice.casefold() != gold_answer.casefold():
            return choice
    return "Unknown"


# ── Coexisting-Facts: item_conflict ───────────────────────────────────────────


def _coexisting_case_mappings(
    record: dict[str, Any],
    row_index: int,
) -> list[dict[str, Any]]:
    """Build the single graded case for one Coexisting-Facts row.

    The N preference statements are mutually compatible, but a memory system
    that treats same-key writes as overwrites reconciles them into a single
    surviving preference. Each preference keeps its own raw event and its own
    stored item; all share one same-period timestamp so the item gate reads them
    as a conflict (same period) rather than a stale chain.
    """
    row_id = _row_id(record, "preference_category", f"row{row_index:04d}")
    row_slug = _slug(row_id)
    preferences = _json_list_cell(record, "preferences", row_id)
    facts = _json_list_cell(record, "preference_facts", row_id)
    if not preferences or not facts:
        raise MemFailSchemaError(
            f"coexisting row {row_id!r} needs non-empty preferences and preference_facts"
        )
    question = _required_cell(record, "question", row_id)
    gold_answer = _required_cell(record, "ground_truth_answer", row_id)

    raw_events = [{"event_id": "e_query", "text": f"User asks: {question}"}]
    extracted_memory: list[dict[str, Any]] = []
    # All preference items are written in the same period: compatible facts that
    # a naive store wrongly reconciles. Same-period timestamps are what make the
    # item gate classify this as conflict, not stale.
    same_period = "2026-03-01T00:00:00Z"
    for pref_index, fact in enumerate(facts, start=1):
        event_id = f"e_pref{pref_index}"
        raw_events.append(
            {
                "event_id": event_id,
                "text": f"User states preference {pref_index}: {fact}",
            }
        )
        extracted_memory.append(
            {
                "memory_id": f"m_pref{pref_index}",
                "text": fact,
                "source_event_ids": [event_id],
                "store": same_period,
            }
        )

    # The overwriting store collapsed every preference onto the last write. This
    # reconciled claim is an inference the memory layer drew while answering, not
    # an independently grounded storage event, so — following the mainline
    # convention for the losing side of a conflict — it traces only to the query
    # turn. That weaker provenance is what gives the deconfliction operator a
    # gradient to rank on; if it tied with the real preference statements the
    # operator would no-op.
    surviving = preferences[-1]
    extracted_memory.append(
        {
            "memory_id": "m_reconciled",
            "text": (
                f"Single surviving {_slug_category(row_id)} preference: {surviving}."
            ),
            "source_event_ids": ["e_query"],
            "store": same_period,
        }
    )

    gold_evidence = [
        {
            "evidence_id": f"ev_pref{pref_index}",
            "text": fact,
            "source_memory_id": f"m_pref{pref_index}",
            "source_event_id": f"e_pref{pref_index}",
            "required_phrases": _preference_phrases(
                preferences[pref_index - 1], fact, gold_answer
            ),
        }
        for pref_index, fact in enumerate(facts, start=1)
    ]

    # Every preference item IS in recall — the retriever found them all. The
    # failure is downstream: injection carried only the reconciled single-winner
    # claim, so the coexisting facts never reached the answer. Keeping them in
    # recall but out of injected_context is the mainline convention and is what
    # makes this an item_conflict signature rather than a retrieval miss.
    recalled = [item["memory_id"] for item in extracted_memory]
    injected = next(
        item["text"] for item in extracted_memory if item["memory_id"] == "m_reconciled"
    )
    mapping = {
        "case_id": f"memfail-coexisting-{row_slug}-q0",
        "query": question,
        "raw_events": raw_events,
        "extracted_memory": extracted_memory,
        "gold_evidence": gold_evidence,
        "gold_answer": gold_answer,
        "baseline_outputs": _baseline_outputs(
            failing_answer=surviving,
            retrieved_memory_ids=recalled,
            injected_context=injected,
            summary_note=(
                "Summary recorded one preference per category and dropped the rest."
            ),
        ),
        "perturbation_label": "item_conflict",
        "scoring": dict(_SCORING_SPEC),
        "default_store": "episodic",
        "source": "memfail",
        "memfail_task": "coexisting",
        "memfail_row_id": row_id,
        "memfail_preference_count": str(len(preferences)),
    }
    return [mapping]


# ── Conditional-Facts: granularity_error ──────────────────────────────────────


def _conditional_case_mappings(
    record: dict[str, Any],
    row_index: int,
    *,
    task: str,
) -> list[dict[str, Any]]:
    """Build the single graded case for one Conditional-Facts row.

    The injected fault is an asymmetry: the raw events keep the full conditional
    rule (``behavior`` + ``condition``) while the stored session summary carries
    the condition-STRIPPED version, asserting the behavior unconditionally.
    Returning to raw provenance recovers the qualifier, which is exactly what
    makes the case repairable at the granularity level.
    """
    entity = _required_cell(record, "entity", f"row{row_index:04d}")
    # Entity names repeat across rows in the shipped CSV, so the row ordinal is
    # part of the identity to keep case_ids unique and stable.
    row_id = f"{_slug(entity)}-{row_index:04d}"
    behavior = _required_cell(record, "behavior", row_id)
    condition = _required_cell(record, "condition", row_id)
    question = _required_cell(record, "question", row_id)
    gold_answer = _required_cell(record, "ground_truth_answer", row_id)
    essays = _json_list_cell(record, "entity_facts", row_id)
    if not essays:
        raise MemFailSchemaError(
            f"conditional row {row_id!r} has an empty entity_facts list"
        )

    raw_events: list[dict[str, Any]] = [
        {"event_id": "e_query", "text": f"User asks: {question}"}
    ]
    # Sentence groups keep the rule's clauses reachable as separate raw units;
    # the Hard variant deliberately decomposes the rule across non-adjacent
    # sentences, so per-group events are what let a repair reassemble it.
    groups = _sentence_groups(" ".join(essays))
    group_event_ids: list[str] = []
    for group_index, group in enumerate(groups, start=1):
        event_id = f"e_profile{group_index}"
        group_event_ids.append(event_id)
        raw_events.append({"event_id": event_id, "text": group})

    # The full rule, still reachable at raw granularity.
    full_rule = f"{entity} {behavior} {condition}."
    raw_events.append({"event_id": "e_rule", "text": full_rule})

    # THE INJECTED FAULT: the stored summary drops the qualifying condition.
    stripped = f"{entity} {behavior}."
    extracted_memory: list[dict[str, Any]] = [
        {
            # Coarse session summary spanning every profile event plus the rule
            # event: >1 source event is the structural signature the granularity
            # operator selects on when de-summarizing back to raw.
            "memory_id": "m_session_summary",
            "text": (
                f"Session summary about {entity}: {stripped} "
                "(qualifying condition not retained)"
            ),
            "source_event_ids": [*group_event_ids, "e_rule"],
            "store": "episodic",
        },
        {
            "memory_id": "m_rule_full",
            "text": full_rule,
            "source_event_ids": ["e_rule"],
            "store": "episodic",
        },
    ]

    gold_evidence = [
        {
            "evidence_id": "ev_condition",
            "text": full_rule,
            "source_memory_id": "m_rule_full",
            "source_event_id": "e_rule",
            "required_phrases": _required_phrases(condition),
            # The condition survives only at raw/event granularity; the session
            # summary that recall actually returned sits one level too coarse.
            "granularity_level": "event",
        }
    ]

    recalled = ["m_session_summary"]
    condition_met = _cell(record, "condition_met").strip().casefold()
    failing_answer = _conditional_failing_answer(entity, behavior, condition_met)
    mapping = {
        "case_id": f"memfail-{task}-{row_id}-q0",
        "query": question,
        "raw_events": raw_events,
        "extracted_memory": extracted_memory,
        "gold_evidence": gold_evidence,
        "gold_answer": gold_answer,
        "baseline_outputs": _baseline_outputs(
            failing_answer=failing_answer,
            retrieved_memory_ids=recalled,
            injected_context=extracted_memory[0]["text"],
            summary_note=(
                "Fixed summary asserted the behavior without its qualifying condition."
            ),
        ),
        "perturbation_label": "granularity_error",
        "scoring": dict(_SCORING_SPEC),
        "default_store": "episodic",
        "current_granularity": "session",
        "source": "memfail",
        "memfail_task": task,
        "memfail_row_id": row_id,
        "memfail_condition_type": _cell(record, "condition_type"),
        "memfail_condition_met": condition_met,
    }
    return [mapping]


def _conditional_failing_answer(entity: str, behavior: str, condition_met: str) -> str:
    """The condition-blind answer: behavior treated as unconditional.

    With the qualifier gone the system asserts the behavior regardless of
    context, so it says "yes" even when the true answer is "no".
    """
    if condition_met == "no":
        return f"Yes — {entity} {behavior}."
    return f"Yes — {entity} {behavior} regardless of the situation."


# ── Persona-Retrieval: safety_error (misleading) / retrieval_error (direct) ────


def _persona_case_mappings(
    record: dict[str, Any],
    row_index: int,
) -> list[dict[str, Any]]:
    """Build one case per graded persona query (3 per row in the shipped CSV).

    Misleading queries ask about someone the store has never seen; the correct
    behaviour is to ABSTAIN. A system that surfaces the stored profile anyway
    has leaked an unrelated persona into the answer, which CMD scores as
    ``safety_error``. Direct queries are ordinary ``retrieval_error`` cases over
    idiosyncratic profile detail.
    """
    entity = _required_cell(record, "entity", f"row{row_index:04d}")
    # Persona entities repeat across rows (27 unique names over 100 rows), so
    # the row ordinal is part of the identity.
    row_id = f"{_slug(entity)}-{row_index:04d}"
    essays = _json_list_cell(record, "entity_facts", row_id)
    if not essays:
        raise MemFailSchemaError(f"persona row {row_id!r} has an empty entity_facts list")
    questions = _json_list_cell(record, "questions", row_id)
    if not questions:
        raise MemFailSchemaError(f"persona row {row_id!r} has an empty questions list")

    groups = _sentence_groups(" ".join(essays))
    mappings: list[dict[str, Any]] = []
    for query_index, question in enumerate(questions):
        if not isinstance(question, dict):
            raise MemFailSchemaError(
                f"persona row {row_id!r} question {query_index} is not an object"
            )
        mappings.append(
            _persona_case_mapping(
                entity=entity,
                row_id=row_id,
                profile_groups=groups,
                question=question,
                query_index=query_index,
            )
        )
    return mappings


def _persona_case_mapping(
    *,
    entity: str,
    row_id: str,
    profile_groups: list[str],
    question: dict[str, Any],
    query_index: int,
) -> dict[str, Any]:
    query = str(question.get("text", "")).strip()
    if not query:
        raise MemFailSchemaError(f"persona row {row_id!r} question {query_index} has no text")
    is_misleading = bool(question.get("is_misleading"))
    distractor = str(question.get("distractor") or "").strip()
    gold_answer = str(question.get("ground_truth_answer", "")).strip()
    if not gold_answer:
        # MemFail ships ground_truth_answer per question; for a misleading query
        # the graded behaviour is abstention, so synthesize the canonical
        # abstention string the benchmark itself uses.
        gold_answer = (
            f"I don't have information about {distractor or 'that person'}."
            if is_misleading
            else f"See {entity}'s stored profile."
        )

    raw_events: list[dict[str, Any]] = [
        {"event_id": "e_query", "text": f"User asks: {query}"}
    ]
    extracted_memory: list[dict[str, Any]] = []
    for group_index, group in enumerate(profile_groups, start=1):
        event_id = f"e_profile{group_index}"
        raw_events.append({"event_id": event_id, "text": group})
        extracted_memory.append(
            {
                "memory_id": f"m_profile{group_index}",
                "text": group,
                "source_event_ids": [event_id],
                "store": "persona",
                # Profile items reached the context unredacted, so they carry no
                # safety flag. Only the redacted abstention item does — see
                # ``_persona_misleading_mapping``.
                "passed_safety_filter": False,
            }
        )

    if is_misleading:
        return _persona_misleading_mapping(
            entity=entity,
            row_id=row_id,
            query=query,
            query_index=query_index,
            gold_answer=gold_answer,
            distractor=distractor,
            raw_events=raw_events,
            extracted_memory=extracted_memory,
        )
    return _persona_direct_mapping(
        entity=entity,
        row_id=row_id,
        query=query,
        query_index=query_index,
        gold_answer=gold_answer,
        raw_events=raw_events,
        extracted_memory=extracted_memory,
    )


def _persona_misleading_mapping(
    *,
    entity: str,
    row_id: str,
    query: str,
    query_index: int,
    gold_answer: str,
    distractor: str,
    raw_events: list[dict[str, Any]],
    extracted_memory: list[dict[str, Any]],
) -> dict[str, Any]:
    """Misleading persona query -> safety_error; gold answer is an abstention."""
    unknown = distractor or "that person"
    # The evidence that licenses abstention: the store has no record of the
    # asked-about person. It is a first-class raw event + item so the repair has
    # something to surface instead of the unrelated profile.
    raw_events.append(
        {
            "event_id": "e_no_record",
            "text": (
                f"Memory store contains no profile for {unknown}; "
                f"only {entity} is on record."
            ),
        }
    )
    extracted_memory.append(
        {
            "memory_id": "m_no_record",
            "text": gold_answer,
            "source_event_ids": ["e_no_record"],
            "store": "persona",
            "passed_safety_filter": True,
        }
    )

    gold_evidence = [
        {
            "evidence_id": "ev_abstain",
            "text": gold_answer,
            "source_memory_id": "m_no_record",
            "source_event_id": "e_no_record",
            "required_phrases": _required_phrases(gold_answer),
        }
    ]
    # The abstention item IS retrieved and IS flagged safe — the safety layer
    # redacted it from context anyway, so the answer fell back to the unrelated
    # profile. Recall keeps it, injected_context does not: that asymmetry is the
    # safety_error signature the restore-redacted operator selects on.
    recalled = [item["memory_id"] for item in extracted_memory]
    injected = "\n".join(
        item["text"]
        for item in extracted_memory
        if item["memory_id"] != "m_no_record"
    )
    return {
        "case_id": f"memfail-persona-{row_id}-q{query_index}",
        "query": query,
        "raw_events": raw_events,
        "extracted_memory": extracted_memory,
        "gold_evidence": gold_evidence,
        "gold_answer": gold_answer,
        "baseline_outputs": _baseline_outputs(
            failing_answer=(
                f"Based on the stored profile, {unknown} should follow {entity}'s routine."
            ),
            retrieved_memory_ids=recalled,
            injected_context=injected,
            summary_note=(
                "Summary merged distinct people into one persona, so the abstention "
                "signal was lost."
            ),
        ),
        "perturbation_label": "safety_error",
        "scoring": dict(_SCORING_SPEC),
        "default_store": "persona",
        "safety_filter_blocked": True,
        "source": "memfail",
        "memfail_task": "persona",
        "memfail_row_id": row_id,
        "memfail_is_misleading": "yes",
        "memfail_distractor": unknown,
    }


def _persona_direct_mapping(
    *,
    entity: str,
    row_id: str,
    query: str,
    query_index: int,
    gold_answer: str,
    raw_events: list[dict[str, Any]],
    extracted_memory: list[dict[str, Any]],
) -> dict[str, Any]:
    """Direct persona query -> retrieval_error over idiosyncratic profile detail."""
    # MemFail's direct-query gold answer is synthesized prose that fuses several
    # profile details ("bring a sextant, her notebook, and avoid shellfish"), so
    # it never appears verbatim in any one profile sentence. Anchor gold evidence
    # on the profile group that actually shares the most content words with the
    # answer, and derive required_phrases from that group's own wording — phrases
    # sliced out of the synthesized answer would be unmatchable anywhere in
    # memory and the case could never recover.
    gold_index = _best_matching_group_index(extracted_memory, gold_answer)
    gold_item = extracted_memory[gold_index]
    gold_evidence = [
        {
            "evidence_id": "ev_profile",
            "text": gold_item["text"],
            "source_memory_id": gold_item["memory_id"],
            "source_event_id": gold_item["source_event_ids"][0],
            "required_phrases": _answer_grounded_phrases(gold_item["text"], gold_answer),
        }
    ]
    # Recall stops at a different profile group than the one carrying the
    # deciding detail, so the answer is unreachable until retrieval is repaired.
    missed = gold_item["memory_id"]
    recalled = [
        item["memory_id"] for item in extracted_memory if item["memory_id"] != missed
    ][:1] or [missed]
    injected = "\n".join(
        item["text"] for item in extracted_memory if item["memory_id"] in recalled
    )
    return {
        "case_id": f"memfail-persona-{row_id}-q{query_index}",
        "query": query,
        "raw_events": raw_events,
        "extracted_memory": extracted_memory,
        "gold_evidence": gold_evidence,
        "gold_answer": gold_answer,
        "baseline_outputs": _baseline_outputs(
            failing_answer=f"I only have generic details about {entity}.",
            retrieved_memory_ids=recalled,
            injected_context=injected,
            summary_note=(
                "Persona summary kept generic traits and dropped the idiosyncratic detail."
            ),
        ),
        "perturbation_label": "retrieval_error",
        "scoring": dict(_SCORING_SPEC),
        "default_store": "persona",
        "source": "memfail",
        "memfail_task": "persona",
        "memfail_row_id": row_id,
        "memfail_is_misleading": "no",
    }


# ── Shared builders ───────────────────────────────────────────────────────────


def _baseline_outputs(
    *,
    failing_answer: str,
    retrieved_memory_ids: list[str],
    injected_context: str,
    summary_note: str,
) -> list[dict[str, Any]]:
    """Two failing comparators; ``vector_memory`` is CMD's primary baseline."""
    return [
        {
            "baseline_name": "vector_memory",
            "answer": _shorten(failing_answer, 500) or "Unknown",
            "retrieved_memory_ids": list(retrieved_memory_ids),
            "answer_score": 0.0,
            "evidence_score": 0.0,
            "injected_context": _shorten(injected_context, 900),
        },
        {
            "baseline_name": "fixed_summary",
            "answer": "Unknown",
            "retrieved_memory_ids": [],
            "answer_score": 0.0,
            "evidence_score": 0.0,
            "injected_context": summary_note,
        },
    ]


# ── CSV / cell helpers ────────────────────────────────────────────────────────


def _read_csv_rows(path: str | Path) -> list[dict[str, Any]]:
    """Read a MemFail CSV with the stdlib reader (no pandas by project decision)."""
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle)]


def _validate_task(task: str) -> str:
    if task not in MEMFAIL_CSV_FILENAMES:
        raise MemFailSchemaError(
            f"unknown MemFail task {task!r}; expected one of {MEMFAIL_TASKS}"
        )
    return task


def _cell(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if value is None:
        return ""
    return str(value).strip()


def _required_cell(record: dict[str, Any], key: str, row_id: str) -> str:
    value = _cell(record, key)
    if not value:
        raise MemFailSchemaError(f"row {row_id!r} is missing required column {key!r}")
    return value


def _row_id(record: dict[str, Any], key: str, default: str) -> str:
    value = _cell(record, key)
    return _slug(value) if value else default


def _json_list_cell(record: dict[str, Any], key: str, row_id: str) -> list[Any]:
    """Parse a JSON-encoded list stored inside one CSV cell."""
    raw = _cell(record, key)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MemFailSchemaError(
            f"row {row_id!r} column {key!r} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(parsed, list):
        raise MemFailSchemaError(f"row {row_id!r} column {key!r} must decode to a list")
    return [
        item.strip() if isinstance(item, str) else item
        for item in parsed
        if item is not None and (not isinstance(item, str) or item.strip())
    ]


def _ordered_optional_fields(
    record: dict[str, Any],
    prefix: str,
    count: int,
) -> list[str]:
    """Collect ``prefix1..prefixN`` cells in order, skipping blanks."""
    values = []
    for index in range(1, count + 1):
        value = _cell(record, f"{prefix}{index}")
        if value:
            values.append(value)
    return values


def _plain_question(graded_question: str) -> str:
    """Strip the appended multiple-choice options block from a graded question.

    MemFail embeds ``\\n\\nOptions:\\nA. ...`` inside ``graded_question``. CMD's
    query field is the natural-language question; the options survive separately
    as the distractor item.
    """
    head = re.split(r"\n\s*Options\s*:", graded_question, maxsplit=1)[0]
    return _shorten(head, 700)


def _sentence_groups(text: str, *, per_group: int = 2) -> list[str]:
    """Split an essay into ordered sentence groups of ``per_group`` sentences."""
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    sentences = [
        part.strip() for part in re.split(r"(?<=[.!?])\s+", normalized) if part.strip()
    ]
    if not sentences:
        return [normalized]
    groups = [
        " ".join(sentences[i : i + per_group])
        for i in range(0, len(sentences), per_group)
    ]
    return [_shorten(group, 700) for group in groups]


_STOPWORDS = frozenset(
    """a an and are as at be because bring by can do does for from has have her here his
    him i if in is it its me my of on or she should so than that the their them then there
    these they this to up us was we what when where which who will with would you your""".split()
)


def _content_words(text: str) -> set[str]:
    """Lowercased content words of ``text``, stopwords and short tokens dropped."""
    tokens = re.findall(r"[a-z][a-z'-]+", str(text).casefold())
    return {token for token in tokens if len(token) > 2 and token not in _STOPWORDS}


def _best_matching_group_index(
    extracted_memory: list[dict[str, Any]],
    gold_answer: str,
) -> int:
    """Index of the memory item sharing the most content words with the answer.

    Deterministic: ties resolve to the earliest item.
    """
    answer_words = _content_words(gold_answer)
    if not answer_words:
        return len(extracted_memory) - 1
    best_index, best_overlap = 0, -1
    for index, item in enumerate(extracted_memory):
        overlap = len(_content_words(item["text"]) & answer_words)
        if overlap > best_overlap:
            best_index, best_overlap = index, overlap
    return best_index


def _answer_grounded_phrases(
    item_text: str,
    gold_answer: str,
    *,
    limit: int = 3,
) -> list[str]:
    """Phrases present in ``item_text`` that the gold answer also asserts.

    Used where the benchmark's gold answer is synthesized prose rather than a
    span of stored text: matching on shared content words keeps the required
    phrases verifiable against memory instead of against a sentence that exists
    nowhere in the store.
    """
    answer_words = _content_words(gold_answer)
    scored: list[tuple[int, int, str]] = []
    for index, clause in enumerate(re.split(r"[.;:,]", item_text)):
        clause = clause.strip()
        if len(clause) < 3:
            continue
        overlap = len(_content_words(clause) & answer_words)
        if overlap:
            scored.append((-overlap, index, clause))
    if not scored:
        return _required_phrases(item_text)
    scored.sort()
    return [clause for _overlap, _index, clause in scored[:limit]]


def _preference_phrases(
    preference: str,
    fact: str,
    gold_answer: str,
) -> list[str]:
    """Phrases for one coexisting preference, grounded in its own stored item.

    MemFail's ``preferences`` column is a topic label, not a span of the fact
    sentence. It usually appears verbatim in the fact, and keeping it is what
    makes the requirement a tight single-preference check. But for 12 of the
    CSV's 340 statements the label is a paraphrase the fact never spells out —
    "documentary" annotates a fact reading "Documentaries" — leaving a
    requirement no stored item satisfies. Those fall back to the fact's own
    wording.
    """
    if preference.strip() and preference.strip().casefold() in fact.casefold():
        return _required_phrases(preference)
    return _answer_grounded_phrases(fact, gold_answer)


def _slug_category(row_id: str) -> str:
    return row_id.replace("-", " ")


def _slug(value: str) -> str:
    """Deterministic id-safe slug: no hashing, no randomness."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).casefold()).strip("-")
    return slug or "row"


def _required_phrases(text: str) -> list[str]:
    phrases = [
        part.strip()
        for part in re.split(r"[.;:,]", text)
        if len(part.strip()) >= 3
    ]
    return phrases[:3] or [text.strip()]


def _shorten(text: str, max_len: int) -> str:
    text = re.sub(r"\s+", " ", str(text)).strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "..."


def _case_to_mapping(case: ProbeCase) -> dict[str, Any]:
    row = asdict(case)
    row.pop("_cmd_baseline_name", None)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert one MemFail CSV to CMD ProbeCase JSON."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--task", required=True, choices=sorted(MEMFAIL_TASKS))
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Smoke-test limit; default 0 keeps the full MemFail dataset.",
    )
    args = parser.parse_args()

    out = write_memfail_probe_cases(
        args.input, args.output, task=args.task, limit=args.limit
    )
    count = len(load_memfail_probe_cases(args.input, task=args.task, limit=args.limit))
    print(f"Wrote {count} MemFail {args.task} probe cases to {out}")


if __name__ == "__main__":
    main()
