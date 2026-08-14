#!/usr/bin/env python3
"""Build a provenance-bound public benchmark adaptation for prospective GHOST.

The builder consumes official LoCoMo and Mem2ActBench releases.  It does not
claim that their rows are fresh deployment outcomes.  Each source row is
converted into a CMD ``ProbeCase`` and receives one explicitly disclosed,
deterministic stale/conflicting memory.  The resulting CPU bundle stops before
relation measurement and intent proposal, so running this command performs no
model or API calls.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Iterable, Mapping, Sequence

from cmd_audit.core.models import ProbeCase
from cmd_audit.eval.dev_state_intents import build_dev_intent
from cmd_audit.eval.state_intent import runtime_case_from_probe_case
from experiments.build_v4_evolution_dataset import (
    BUILDER_VERSION,
    DATASET_MANIFEST_SCHEMA_VERSION,
    NORMALIZATION_VERSION,
    OUTPUT_FILES,
    RUNTIME_ROW_SCHEMA_VERSION,
    SHADOW_ROW_SCHEMA_VERSION,
    SOURCE_MANIFEST_SCHEMA_VERSION,
    SPLIT_MANIFEST_SCHEMA_VERSION,
    SPLIT_POLICY_VERSION,
    _relation_requests,
    _write_gzip,
    _write_json,
    _write_json_gzip,
    _write_jsonl_gzip,
    canonical_bytes,
    canonical_sha256,
    file_sha256,
    hidden_intent_mapping,
    runtime_case_mapping,
)


PUBLIC_MANIFEST_SCHEMA = "cmd-ghost-public-source-provenance-v1"
PARTITION_MANIFEST_SCHEMA = "cmd-ghost-four-partition-manifest-v1"
BUILD_REPORT_SCHEMA = "cmd-ghost-public-dataset-build-report-v1"
DEFAULT_SEED = 20260814
DEFAULT_TOKEN_BUDGET = 12_000
PARTITIONS = ("ghost_dev", "ghost_cal", "ghost_test_rep", "ghost_test_new")
_SPACE = re.compile(r"\s+")
_SLUG = re.compile(r"[^a-z0-9]+")
_TEMPLATE_MARKERS = ("M_new:", "M_old:", "target_item", "gold_item", "corrupted_item")


@dataclass(frozen=True)
class PublicCase:
    case_id: str
    family_id: str
    domain: str
    source_record_id: str
    probe: Mapping[str, object]


@dataclass(frozen=True)
class SplitPlan:
    locomo_dev_families: int = 16
    locomo_cal_families: int = 10
    locomo_new_families: int = 10
    locomo_dev_per_family: int = 7
    locomo_rep_per_family: int = 3
    locomo_eval_per_family: int = 10
    mem2act_dev_families: int = 20
    mem2act_cal_families: int = 15
    mem2act_new_families: int = 60
    mem2act_dev_per_family: int = 5
    mem2act_rep_per_family: int = 1
    mem2act_cal_per_family: int = 3
    mem2act_new_per_family: int = 1


def _clean_text(value: object) -> str:
    text = _SPACE.sub(" ", str(value).strip())
    for marker in _TEMPLATE_MARKERS:
        text = text.replace(marker, marker.replace("_", " ").replace(":", ""))
    return text


def _answer_text(value: object) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _clean_text(value)


def _slug(value: object) -> str:
    text = _SLUG.sub("-", str(value).casefold()).strip("-")
    return text[:48] or "unknown"


def _opaque(prefix: str, *parts: str) -> str:
    payload = "\0".join((prefix, *parts)).encode()
    return f"{prefix}:{hashlib.sha256(payload).hexdigest()}"


def _stable_order(values: Iterable[str], *, seed: int, namespace: str) -> list[str]:
    return sorted(
        values,
        key=lambda value: hashlib.sha256(
            f"{seed}\0{namespace}\0{value}".encode()
        ).hexdigest(),
    )


def _probe_mapping(
    *,
    case_id: str,
    query: str,
    current_text: str,
    prior_text: str,
    current_event_ids: Sequence[str],
    current_events: Sequence[Mapping[str, str]],
    gold_answer: str,
    baseline_answer: str,
    evidence_phrase: str,
) -> dict[str, object]:
    baseline_answer = baseline_answer or "insufficient evidence"
    prior_event_id = f"{case_id}:prior-event"
    events = [
        {
            "event_id": prior_event_id,
            "text": "An earlier assistant memory recorded a conflicting value.",
        },
        *current_events,
    ]
    mapping: dict[str, object] = {
        "case_id": case_id,
        "query": _clean_text(query),
        "raw_events": events,
        "extracted_memory": [
            {
                "memory_id": "memory_prior",
                "text": _clean_text(prior_text),
                "source_event_ids": [prior_event_id],
                "store": "2025-01-01T00:00:00Z",
                "is_graph_expanded": False,
                "passed_safety_filter": False,
                "provenance": [],
            },
            {
                "memory_id": "memory_current",
                "text": _clean_text(current_text),
                "source_event_ids": list(current_event_ids),
                "store": "2025-01-02T00:00:00Z",
                "is_graph_expanded": False,
                "passed_safety_filter": False,
                "provenance": [],
            },
        ],
        "gold_evidence": [
            {
                "evidence_id": f"{case_id}:evidence",
                "text": _clean_text(current_text),
                "source_memory_id": "memory_current",
                "source_event_id": current_event_ids[0],
                "required_phrases": [_clean_text(evidence_phrase)],
                "granularity_level": "event",
            }
        ],
        "gold_answer": gold_answer,
        "baseline_outputs": [
            {
                "baseline_name": "vector_memory",
                "answer": baseline_answer,
                "retrieved_memory_ids": ["memory_prior", "memory_current"],
                "answer_score": 0.0,
                "evidence_score": 1.0,
                "injected_context": "",
            }
        ],
        "perturbation_label": "item_conflict",
        "scoring": {
            "answer_metric": "casefold_exact_match",
            "evidence_metric": "gold_evidence_recall",
        },
        "has_ingestion_trace": True,
        "default_store": "episodic",
        "granularity_levels": ["raw", "event", "session", "persona", "procedure", "graph"],
        "current_granularity": "event",
        "safety_filter_blocked": False,
    }
    ProbeCase.from_mapping(mapping)
    return mapping


def _locomo_cases(path: Path) -> list[PublicCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) != 10:
        raise ValueError("LoCoMo source must contain the official 10 conversations")
    output: list[PublicCase] = []
    for conversation in payload:
        if not isinstance(conversation, Mapping):
            raise ValueError("LoCoMo conversation must be a mapping")
        sample_id = _clean_text(conversation.get("sample_id", ""))
        qa_rows = conversation.get("qa")
        raw_conversation = conversation.get("conversation")
        if not sample_id or not isinstance(qa_rows, list) or not isinstance(raw_conversation, Mapping):
            raise ValueError("LoCoMo conversation has an invalid shape")
        turns: dict[str, str] = {}
        for key, raw_session in raw_conversation.items():
            if not re.fullmatch(r"session_\d+", str(key)) or not isinstance(raw_session, list):
                continue
            for turn in raw_session:
                if not isinstance(turn, Mapping):
                    continue
                dia_id = _clean_text(turn.get("dia_id", ""))
                text = _clean_text(turn.get("text", ""))
                speaker = _clean_text(turn.get("speaker", "speaker"))
                if dia_id and text:
                    turns[dia_id] = f"{speaker}: {text}"
        by_family: dict[str, list[tuple[int, Mapping[str, object]]]] = defaultdict(list)
        for index, qa in enumerate(qa_rows):
            if isinstance(qa, Mapping):
                category = _clean_text(qa.get("category", "unknown"))
                by_family[f"locomo::{sample_id}::category-{category}"].append((index, qa))
        for family_id, members in sorted(by_family.items()):
            answers = [_answer_text(qa.get("answer", "")) for _, qa in members]
            for member_offset, (index, qa) in enumerate(members):
                query = _clean_text(qa.get("question", ""))
                answer = answers[member_offset]
                if not query or not answer:
                    continue
                evidence_ids = [
                    _clean_text(value)
                    for value in qa.get("evidence", [])
                    if _clean_text(value) in turns
                ]
                evidence_texts = [turns[value] for value in evidence_ids]
                if not evidence_ids:
                    fallback_id, fallback_text = next(iter(turns.items()))
                    evidence_ids = [fallback_id]
                    evidence_texts = [fallback_text]
                wrong = next(
                    (candidate for candidate in answers[member_offset + 1 :] + answers[:member_offset] if candidate != answer),
                    "insufficient evidence",
                )
                case_id = f"ghost-locomo-{_slug(sample_id)}-c{_slug(qa.get('category'))}-{index:04d}"
                current_text = (
                    f"Current conversation evidence for the queried detail: {' '.join(evidence_texts)} "
                    f"The grounded answer from this record is {answer}."
                )
                prior_text = (
                    f"Earlier assistant memory for the same queried detail gave the value {wrong}."
                )
                current_events = [
                    {"event_id": value, "text": turns[value]} for value in evidence_ids
                ]
                domain = (
                    "locomo_factual"
                    if str(qa.get("category")) in {"1", "2", "3"}
                    else "locomo_inferential"
                )
                output.append(
                    PublicCase(
                        case_id=case_id,
                        family_id=family_id,
                        domain=domain,
                        source_record_id=f"{sample_id}:qa:{index}",
                        probe=_probe_mapping(
                            case_id=case_id,
                            query=query,
                            current_text=current_text,
                            prior_text=prior_text,
                            current_event_ids=evidence_ids,
                            current_events=current_events,
                            gold_answer=answer,
                            baseline_answer=wrong,
                            evidence_phrase=answer,
                        ),
                    )
                )
    return output


def _load_jsonl(path: Path) -> list[Mapping[str, object]]:
    rows: list[Mapping[str, object]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, Mapping):
            raise ValueError(f"JSONL row {number} must be a mapping")
        rows.append(value)
    return rows


def _mem2act_cases(path: Path) -> list[PublicCase]:
    rows = _load_jsonl(path)
    if len(rows) != 400:
        raise ValueError("Mem2ActBench source must contain the official 400 QA rows")
    by_tool: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        schema = row.get("target_tool_schema")
        if isinstance(schema, Mapping):
            by_tool[_clean_text(schema.get("name", "unknown"))].append(row)
    output: list[PublicCase] = []
    all_rows = sorted(rows, key=lambda row: str(row.get("qa_id")))
    for position, row in enumerate(all_rows):
        qa_id = _clean_text(row.get("qa_id", ""))
        query = _clean_text(row.get("query", ""))
        schema = row.get("target_tool_schema")
        tool_call = row.get("tool_call")
        if not qa_id or not query or not isinstance(schema, Mapping) or not isinstance(tool_call, Mapping):
            continue
        tool_name = _clean_text(schema.get("name", "unknown"))
        family_id = f"mem2act::{_slug(tool_name)}"
        gold_answer = _answer_text(tool_call)
        siblings = by_tool[tool_name]
        wrong_row = next(
            (
                candidate
                for candidate in siblings
                if candidate.get("qa_id") != row.get("qa_id")
                and candidate.get("tool_call") != tool_call
            ),
            all_rows[(position + 1) % len(all_rows)],
        )
        wrong_answer = _answer_text(wrong_row.get("tool_call", {"name": "unknown", "arguments": {}}))
        chain = row.get("evolution_chain")
        if not isinstance(chain, list):
            chain = []
        source_texts = [
            _clean_text(item.get("source_text") or item.get("fact"))
            for item in chain
            if isinstance(item, Mapping) and _clean_text(item.get("source_text") or item.get("fact"))
        ]
        if not source_texts:
            source_texts = [f"The user request is: {query}"]
        current_event_ids = [f"{qa_id}:source:{index}" for index in range(len(source_texts))]
        current_events = [
            {"event_id": event_id, "text": text}
            for event_id, text in zip(current_event_ids, source_texts, strict=True)
        ]
        schema_text = _answer_text(schema)
        current_text = (
            f"Current user memory: {' '.join(source_texts)} Applicable tool contract: {schema_text}. "
            f"The registered tool name is {tool_name}."
        )
        prior_text = f"Earlier assistant memory for the same request proposed {wrong_answer}."
        case_id = f"ghost-mem2act-{_slug(qa_id)}"
        output.append(
            PublicCase(
                case_id=case_id,
                family_id=family_id,
                domain="mem2act_action",
                source_record_id=qa_id,
                probe=_probe_mapping(
                    case_id=case_id,
                    query=query,
                    current_text=current_text,
                    prior_text=prior_text,
                    current_event_ids=current_event_ids,
                    current_events=current_events,
                    gold_answer=gold_answer,
                    baseline_answer=wrong_answer,
                    evidence_phrase=tool_name,
                ),
            )
        )
    return output


def _family_groups(cases: Sequence[PublicCase]) -> dict[str, list[PublicCase]]:
    groups: dict[str, list[PublicCase]] = defaultdict(list)
    for case in cases:
        groups[case.family_id].append(case)
    return {key: sorted(value, key=lambda row: row.case_id) for key, value in groups.items()}


def _take_family_block(
    groups: Mapping[str, Sequence[PublicCase]],
    families: Sequence[str],
    *,
    per_family: int,
    offset: int = 0,
) -> list[PublicCase]:
    return [
        case
        for family in families
        for case in groups[family][offset : offset + per_family]
    ]


def select_partitions(
    locomo: Sequence[PublicCase],
    mem2act: Sequence[PublicCase],
    *,
    seed: int,
    plan: SplitPlan = SplitPlan(),
) -> dict[str, list[PublicCase]]:
    locomo_groups = _family_groups(locomo)
    locomo_eligible = [
        family
        for family, rows in locomo_groups.items()
        if len(rows) >= max(
            plan.locomo_dev_per_family + plan.locomo_rep_per_family,
            plan.locomo_eval_per_family,
        )
    ]
    required_locomo = (
        plan.locomo_dev_families
        + plan.locomo_cal_families
        + plan.locomo_new_families
    )
    locomo_order = _stable_order(locomo_eligible, seed=seed, namespace="locomo-family")
    if len(locomo_order) < required_locomo:
        raise ValueError(
            f"only {len(locomo_order)} eligible LoCoMo families; need {required_locomo}"
        )
    loc_dev = locomo_order[: plan.locomo_dev_families]
    loc_cal = locomo_order[
        plan.locomo_dev_families : plan.locomo_dev_families + plan.locomo_cal_families
    ]
    loc_new = locomo_order[
        plan.locomo_dev_families + plan.locomo_cal_families : required_locomo
    ]

    mem_groups = _family_groups(mem2act)
    mem_multi = [family for family, rows in mem_groups.items() if len(rows) >= 2]
    mem_multi.sort(
        key=lambda family: (
            -len(mem_groups[family]),
            hashlib.sha256(f"{seed}\0mem2act-family\0{family}".encode()).hexdigest(),
        )
    )
    required_multi = plan.mem2act_dev_families + plan.mem2act_cal_families
    if len(mem_multi) < required_multi:
        raise ValueError(
            f"only {len(mem_multi)} repeated Mem2Act families; need {required_multi}"
        )
    mem_dev = mem_multi[: plan.mem2act_dev_families]
    mem_cal = mem_multi[
        plan.mem2act_dev_families : required_multi
    ]
    reserved = set(mem_dev) | set(mem_cal)
    mem_new_candidates = _stable_order(
        set(mem_groups) - reserved,
        seed=seed,
        namespace="mem2act-new-family",
    )
    if len(mem_new_candidates) < plan.mem2act_new_families:
        raise ValueError("not enough unseen Mem2Act families")
    mem_new = mem_new_candidates[: plan.mem2act_new_families]

    dev = _take_family_block(
        locomo_groups,
        loc_dev,
        per_family=plan.locomo_dev_per_family,
    )
    rep = _take_family_block(
        locomo_groups,
        loc_dev,
        per_family=plan.locomo_rep_per_family,
        offset=plan.locomo_dev_per_family,
    )
    cal = _take_family_block(
        locomo_groups,
        loc_cal,
        per_family=plan.locomo_eval_per_family,
    )
    new = _take_family_block(
        locomo_groups,
        loc_new,
        per_family=plan.locomo_eval_per_family,
    )

    for family in mem_dev:
        rows = mem_groups[family]
        dev.extend(rows[: min(plan.mem2act_dev_per_family, len(rows) - 1)])
        rep.extend(rows[-plan.mem2act_rep_per_family :])
    for family in mem_cal:
        cal.extend(mem_groups[family][: plan.mem2act_cal_per_family])
    for family in mem_new:
        new.extend(mem_groups[family][: plan.mem2act_new_per_family])

    partitions = {
        "ghost_dev": sorted(dev, key=lambda row: row.case_id),
        "ghost_cal": sorted(cal, key=lambda row: row.case_id),
        "ghost_test_rep": sorted(rep, key=lambda row: row.case_id),
        "ghost_test_new": sorted(new, key=lambda row: row.case_id),
    }
    _validate_partitions(partitions)
    return partitions


def _validate_partitions(partitions: Mapping[str, Sequence[PublicCase]]) -> None:
    if set(partitions) != set(PARTITIONS) or any(not partitions[name] for name in PARTITIONS):
        raise ValueError("all four partitions must be non-empty")
    case_sets = {name: {row.case_id for row in rows} for name, rows in partitions.items()}
    if sum(len(values) for values in case_sets.values()) != len(set().union(*case_sets.values())):
        raise ValueError("case IDs cross partition boundaries")
    families = {
        name: {row.family_id for row in rows} for name, rows in partitions.items()
    }
    if families["ghost_dev"] & families["ghost_cal"]:
        raise ValueError("ghost_dev and ghost_cal must be family-disjoint")
    if not families["ghost_test_rep"] <= families["ghost_dev"]:
        raise ValueError("ghost_test_rep must reuse only ghost_dev families")
    if families["ghost_test_new"] & (
        families["ghost_dev"] | families["ghost_cal"] | families["ghost_test_rep"]
    ):
        raise ValueError("ghost_test_new families must be unseen")


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def build_public_dataset(
    *,
    locomo_path: Path,
    mem2act_path: Path,
    output_dir: Path,
    seed: int = DEFAULT_SEED,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    plan: SplitPlan = SplitPlan(),
) -> dict[str, object]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("refusing to overwrite non-empty output directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw_sources"
    source_dir = output_dir / "probe_sources"
    dataset_dir = output_dir / "cpu_dataset"
    partition_dir = output_dir / "partitions"
    for directory in (raw_dir, source_dir, dataset_dir, partition_dir):
        directory.mkdir(parents=True, exist_ok=True)

    locomo_raw = raw_dir / "locomo10.json"
    mem2act_raw = raw_dir / "mem2act_qa.jsonl"
    shutil.copyfile(locomo_path, locomo_raw)
    shutil.copyfile(mem2act_path, mem2act_raw)
    locomo = _locomo_cases(locomo_raw)
    mem2act = _mem2act_cases(mem2act_raw)
    partitions = select_partitions(locomo, mem2act, seed=seed, plan=plan)
    ordered = [case for partition in PARTITIONS for case in partitions[partition]]
    case_to_partition = {
        case.case_id: partition
        for partition, rows in partitions.items()
        for case in rows
    }

    source_rows: dict[str, list[Mapping[str, object]]] = {
        "locomo_factual": [],
        "locomo_inferential": [],
        "mem2act_action": [],
    }
    for case in ordered:
        source_rows[case.domain].append(case.probe)
    sources: list[dict[str, object]] = []
    for domain, rows in source_rows.items():
        path = source_dir / f"{domain}.json"
        _write_json(path, rows)
        sources.append(
            {
                "domain": domain,
                "source_file": _portable_path(path),
                "source_sha256": file_sha256(path),
                "source_byte_size": path.stat().st_size,
                "source_case_count": len(rows),
                "selected_case_count": len(rows),
                "selected_case_ids_sha256": canonical_sha256(
                    [str(row["case_id"]) for row in rows]
                ),
            }
        )

    runtime_rows: list[dict[str, object]] = []
    shadow_rows: list[dict[str, object]] = []
    relation_rows: list[dict[str, object]] = []
    assignments: list[dict[str, object]] = []
    for position, case in enumerate(ordered, 1):
        probe = ProbeCase.from_mapping(dict(case.probe))
        source_hash = canonical_sha256(case.probe)
        runtime_family = _opaque("runtime", case.case_id)
        runtime = runtime_case_from_probe_case(
            probe,
            token_budget=token_budget,
            family_id=runtime_family,
            reject_template_hints=True,
        )
        hidden = build_dev_intent(
            probe,
            token_budget=token_budget,
            family_id=case.family_id,
        )
        partition = case_to_partition[case.case_id]
        probe_set = "unseen" if partition == "ghost_test_new" else "represented"
        dependency_group = _opaque("dependency", case.family_id)
        runtime_rows.append(
            {
                "schema_version": RUNTIME_ROW_SCHEMA_VERSION,
                "case_id": case.case_id,
                "source_case_sha256": source_hash,
                "runtime_case": runtime_case_mapping(runtime),
            }
        )
        shadow_rows.append(
            {
                "schema_version": SHADOW_ROW_SCHEMA_VERSION,
                "case_id": case.case_id,
                "family_id": case.family_id,
                "dependency_group": dependency_group,
                "probe_set": probe_set,
                "stream_role": partition,
                "source_case_sha256": source_hash,
                "probe_case": dict(case.probe),
                "hidden_intent": hidden_intent_mapping(hidden),
            }
        )
        relation_rows.extend(_relation_requests(runtime))
        assignments.append(
            {
                "case_id": case.case_id,
                "domain": case.domain,
                "family_id": case.family_id,
                "dependency_group": dependency_group,
                "probe_set": probe_set,
                "stream_role": partition,
                "stream_position": position,
                "selection_event_index": position * 100,
                "member_index": position,
            }
        )

    source_manifest = {
        "schema_version": SOURCE_MANIFEST_SCHEMA_VERSION,
        "builder_version": BUILDER_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "sources": sources,
    }
    split_counts = Counter(str(row["probe_set"]) for row in assignments)
    role_counts = Counter(str(row["stream_role"]) for row in assignments)
    split_manifest = {
        "schema_version": SPLIT_MANIFEST_SCHEMA_VERSION,
        "split_policy_version": SPLIT_POLICY_VERSION,
        "seed": seed,
        "family_is_evaluation_only": True,
        "unseen_updates_authorized": False,
        "case_count": len(assignments),
        "family_count": len({str(row["family_id"]) for row in assignments}),
        "dependency_group_count": len(
            {str(row["dependency_group"]) for row in assignments}
        ),
        "probe_set_counts": dict(sorted(split_counts.items())),
        "stream_role_counts": dict(sorted(role_counts.items())),
        "assignments": assignments,
    }
    _write_jsonl_gzip(dataset_dir / "runtime_cases.jsonl.gz", runtime_rows)
    _write_jsonl_gzip(dataset_dir / "shadow_cases.jsonl.gz", shadow_rows)
    _write_jsonl_gzip(dataset_dir / "relation_requests.jsonl.gz", relation_rows)
    _write_json_gzip(dataset_dir / "split_manifest.json.gz", split_manifest)
    _write_json(dataset_dir / "source_manifest.json", source_manifest)

    for partition, rows in partitions.items():
        _write_text(
            partition_dir / f"{partition}.txt",
            "".join(f"{row.case_id}\n" for row in rows),
        )
    partition_hashes = {
        partition: file_sha256(partition_dir / f"{partition}.txt")
        for partition in PARTITIONS
    }
    partition_manifest = {
        "schema_version": PARTITION_MANIFEST_SCHEMA,
        "seed": seed,
        "policy": {
            "case_disjoint": True,
            "dev_cal_family_disjoint": True,
            "test_rep_reuses_dev_families_only": True,
            "test_new_family_disjoint": True,
            "split_before_model_calls": True,
        },
        "counts": {name: len(partitions[name]) for name in PARTITIONS},
        "family_counts": {
            name: len({row.family_id for row in partitions[name]})
            for name in PARTITIONS
        },
        "file_sha256": partition_hashes,
    }
    _write_json(output_dir / "partition_manifest.json", partition_manifest)

    file_hashes = {name: file_sha256(dataset_dir / name) for name in OUTPUT_FILES}
    domain_counts = Counter(str(row["domain"]) for row in assignments)
    domain_family_counts = {
        domain: len(
            {
                str(row["family_id"])
                for row in assignments
                if row["domain"] == domain
            }
        )
        for domain in source_rows
    }
    domain_probe_set_counts = {
        domain: dict(
            sorted(
                Counter(
                    str(row["probe_set"])
                    for row in assignments
                    if row["domain"] == domain
                ).items()
            )
        )
        for domain in source_rows
    }
    dataset_manifest: dict[str, object] = {
        "schema_version": DATASET_MANIFEST_SCHEMA_VERSION,
        "builder_version": BUILDER_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "split_policy_version": SPLIT_POLICY_VERSION,
        "build_status": "relation_instrument_pending",
        "runtime_uses_gold": False,
        "relation_requests_use_gold": False,
        "semantic_edges_from_labels": False,
        "public_benchmark_adaptation": True,
        "synthetic_conflict_injection": True,
        "natural_deployment_failures": False,
        "seed": seed,
        "token_budget": token_budget,
        "case_count": len(assignments),
        "family_count": split_manifest["family_count"],
        "dependency_group_count": split_manifest["dependency_group_count"],
        "relation_request_count": len(relation_rows),
        "domain_case_counts": dict(sorted(domain_counts.items())),
        "domain_family_counts": domain_family_counts,
        "domain_dependency_group_counts": domain_family_counts,
        "domain_probe_set_counts": domain_probe_set_counts,
        "probe_set_counts": dict(sorted(split_counts.items())),
        "source_manifest_sha256": file_hashes["source_manifest.json"],
        "split_manifest_sha256": file_hashes["split_manifest.json.gz"],
        "ghost_partition_manifest_sha256": file_sha256(
            output_dir / "partition_manifest.json"
        ),
        "file_sha256": file_hashes,
        "next_required_artifact": "frozen_relation_verdicts_and_complete_intent_proposals",
    }
    dataset_manifest["dataset_sha256"] = canonical_sha256(dataset_manifest)
    _write_json(dataset_dir / "dataset_manifest.json", dataset_manifest)

    provenance = {
        "schema_version": PUBLIC_MANIFEST_SCHEMA,
        "independent_source": False,
        "confirmatory_attestation_eligible": False,
        "reason": (
            "public benchmark rows were selected and transformed by the project; "
            "they are not post-freeze independent deployment observations"
        ),
        "transformation": {
            "synthetic_conflict_injection": True,
            "model_calls": 0,
            "split_before_relation_and_intent_models": True,
            "family_key_uses_source_semantics": True,
        },
        "upstreams": [
            {
                "dataset": "LoCoMo",
                "url": "https://github.com/snap-research/locomo",
                "revision": "3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376",
                "upstream_path": "data/locomo10.json",
                "license": "CC-BY-NC-4.0",
                "file_sha256": file_sha256(locomo_raw),
                "row_count": 10,
            },
            {
                "dataset": "Mem2ActBench",
                "url": "https://github.com/Cantaloupe-M/Mem2ActBench",
                "revision": "b00726940b5abbe9bd324bdd7a2cb272f5c62a29",
                "upstream_path": "Mem2ActBench/qa_dataset.jsonl",
                "license": "NO-LICENSE-FILE-FOUND-RESEARCH-USE-ONLY",
                "file_sha256": file_sha256(mem2act_raw),
                "row_count": 400,
            },
        ],
    }
    _write_json(output_dir / "source_provenance.json", provenance)
    _write_gzip(
        output_dir / "cases.selected.jsonl.gz",
        b"".join(canonical_bytes(case.probe) + b"\n" for case in ordered),
    )
    report: dict[str, object] = {
        "schema_version": BUILD_REPORT_SCHEMA,
        "status": "PASS_CPU_DATASET_READY",
        "case_count": len(ordered),
        "family_count": len({case.family_id for case in ordered}),
        "partition_counts": partition_manifest["counts"],
        "partition_family_counts": partition_manifest["family_counts"],
        "domain_case_counts": dict(sorted(domain_counts.items())),
        "relation_request_count": len(relation_rows),
        "candidate_budget_for_next_stage": 4,
        "model_calls": 0,
        "prepared_cases_ready": False,
        "next_step": "run prepare_v4_live_cases with frozen models",
        "dataset_sha256": dataset_manifest["dataset_sha256"],
        "source_provenance_sha256": file_sha256(output_dir / "source_provenance.json"),
        "partition_manifest_sha256": file_sha256(output_dir / "partition_manifest.json"),
    }
    report["report_sha256"] = canonical_sha256(report)
    _write_json(output_dir / "build_report.json", report)
    _write_text(
        output_dir / "README.md",
        "# GHOST public benchmark adaptation\n\n"
        f"This directory contains {len(ordered)} family-blocked cases derived from fixed "
        "LoCoMo and Mem2ActBench source files. The rows are real public benchmark "
        "content with a deterministic synthetic conflict injected for repair "
        "evaluation. They are not fresh deployment observations and are not "
        "eligible for an independent-source confirmatory attestation.\n\n"
        "- `raw_sources/`: byte-preserved upstream files.\n"
        "- `probe_sources/`: selected CMD ProbeCase rows in three data domains.\n"
        "- `cpu_dataset/`: V4-compatible runtime/shadow/relation package.\n"
        "- `partitions/`: frozen case-id access lists.\n"
        "- `source_provenance.json`: upstream revisions, hashes, licenses, and "
        "claim boundary.\n"
        "- `partition_manifest.json`: split rules, counts, and hashes.\n\n"
        "The next stage is model-calling relation measurement and intent proposal. "
        "Do not authorize a confirmatory live test from this directory alone.\n",
    )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--locomo", type=Path, required=True)
    parser.add_argument("--mem2act", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)
    args = parser.parse_args(argv)
    try:
        report = build_public_dataset(
            locomo_path=args.locomo,
            mem2act_path=args.mem2act,
            output_dir=args.output_dir,
            seed=args.seed,
            token_budget=args.token_budget,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(f"REFUSE: {type(error).__name__}: {error}")
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_SEED",
    "PublicCase",
    "SplitPlan",
    "build_public_dataset",
    "main",
    "select_partitions",
]
