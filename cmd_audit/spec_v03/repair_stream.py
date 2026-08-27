"""Public semantic episode -> intervention -> repair/shadow compiler.

Only explicitly versioned intervention records are synthetic.  Public event
payloads and sealed query answers/evidence are copied from the acquired source
files without paraphrasing or answer rewriting.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import csv
import hashlib
import json
from pathlib import Path
import random
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .contracts import DecisionView, EvaluatorOnly, canonical_sha256


STREAM_SCHEMA = "cmd-spec-v03-repair-stream-v1"
PROCESS_TEMPLATES = ("drop", "duplicate", "reorder", "truncate", "wrong_index", "wrong_scope", "stale_cache")
STATE_TEMPLATES = ("explicit_supersede", "implicit_invalidation", "dependent_invalidation")
POISON_TEMPLATES = ("untrusted_injection", "authority_crossing", "sleeper_trigger")
ALL_TEMPLATES = ("clean",) + PROCESS_TEMPLATES + STATE_TEMPLATES + POISON_TEMPLATES
_SOURCE_LOG_ROOT_CACHE: dict[tuple[str, ...], str] = {}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _event_id(dataset: str, episode: str, source_ref: str, payload: Mapping[str, Any]) -> str:
    return f"event-{canonical_sha256({'dataset': dataset, 'episode': episode, 'source_ref': source_ref, 'payload': payload})}"


@dataclass(frozen=True)
class PublicEvent:
    event_id: str
    source_ref: str
    ordinal: int
    timestamp: str | None
    actor_scope: str | None
    payload: Mapping[str, Any]
    payload_sha256: str
    source_payload_sha256: str
    synthetic: bool = False

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class PublicQuery:
    query_id: str
    query: str
    answer: object
    evidence: object
    source_ref: str


@dataclass(frozen=True)
class PublicEpisode:
    episode_id: str
    source_dataset_id: str
    family_id: str
    source_path: str
    source_sha256: str
    immutable_events: tuple[PublicEvent, ...]
    sealed_queries: tuple[PublicQuery, ...]
    capabilities: tuple[str, ...]
    source_metadata: Mapping[str, str]

    @property
    def clean_root(self) -> str:
        return root_of(self.immutable_events)

    def public_mapping(self) -> dict[str, object]:
        return {
            "schema_version": STREAM_SCHEMA,
            "episode_id": self.episode_id,
            "source_dataset_id": self.source_dataset_id,
            "family_id": self.family_id,
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "immutable_events": [event.to_mapping() for event in self.immutable_events],
            "capabilities": list(self.capabilities),
            "source_metadata": dict(self.source_metadata),
        }

    def evaluator_mapping(self) -> dict[str, object]:
        return {"episode_id": self.episode_id, "queries": [asdict(query) for query in self.sealed_queries]}


def root_of(events: Sequence[PublicEvent]) -> str:
    return canonical_sha256([event.to_mapping() for event in events])


def _make_event(dataset: str, episode: str, source_ref: str, ordinal: int, payload: Mapping[str, Any], *, timestamp: str | None = None, actor_scope: str | None = None, source_payload_sha256: str | None = None, synthetic: bool = False) -> PublicEvent:
    normalized = dict(payload)
    digest = canonical_sha256(normalized)
    return PublicEvent(
        _event_id(dataset, episode, source_ref, normalized), source_ref, ordinal, timestamp, actor_scope,
        normalized, digest, source_payload_sha256 or digest, synthetic,
    )


def _parse_json_cell(value: str) -> object:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def normalize_locomo(path: Path) -> Iterator[PublicEpisode]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("LoCoMo root must be a list")
    source_sha = _sha256_file(path)
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("sample_id"), str) or not isinstance(item.get("conversation"), dict):
            raise ValueError("unsupported LoCoMo item schema")
        episode_id = f"locomo:{item['sample_id']}"
        conversation = item["conversation"]
        events: list[PublicEvent] = []
        ordinal = 0
        for key in sorted((name for name in conversation if name.startswith("session_") and not name.endswith("_date_time")), key=lambda name: int(name.split("_")[1])):
            turns = conversation[key]
            timestamp = conversation.get(f"{key}_date_time")
            if not isinstance(turns, list) or not isinstance(timestamp, str):
                raise ValueError(f"unsupported LoCoMo {episode_id} {key}")
            for turn in turns:
                if not isinstance(turn, dict) or not isinstance(turn.get("dia_id"), str):
                    raise ValueError("LoCoMo turn missing dia_id")
                events.append(_make_event("locomo", episode_id, str(turn["dia_id"]), ordinal, turn, timestamp=timestamp, actor_scope=str(turn.get("speaker", "")) or None))
                ordinal += 1
        queries = tuple(
            PublicQuery(f"{episode_id}:qa:{index}", str(row["question"]), row["answer"], row.get("evidence", ()), f"qa:{index}")
            for index, row in enumerate(item.get("qa", ()))
            if isinstance(row, dict) and isinstance(row.get("question"), str) and row.get("answer") is not None
        )
        if not events or not queries:
            raise ValueError(f"LoCoMo {episode_id} lacks events or source queries")
        yield PublicEpisode(episode_id, "locomo", "locomo-conversation", path.as_posix(), source_sha, tuple(events), queries, ("process", "poison"), {"sample_id": item["sample_id"]})


def normalize_halumem(path: Path) -> Iterator[PublicEpisode]:
    source_sha = _sha256_file(path)
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("uuid"), str) or not isinstance(row.get("sessions"), list):
                raise ValueError(f"unsupported HaluMem row {line_number}")
            episode_id = f"halumem:{row['uuid']}"
            events: list[PublicEvent] = []
            queries: list[PublicQuery] = []
            ordinal = 0
            state_capable = False
            for session_index, session in enumerate(row["sessions"]):
                if not isinstance(session, dict):
                    raise ValueError("HaluMem session must be an object")
                for memory in session.get("memory_points", ()):
                    if not isinstance(memory, dict) or not isinstance(memory.get("index"), int):
                        raise ValueError("HaluMem memory point lacks source index")
                    source_ref = f"session:{session_index}:memory:{memory['index']}"
                    events.append(_make_event("halumem", episode_id, source_ref, ordinal, memory, timestamp=memory.get("timestamp") if isinstance(memory.get("timestamp"), str) else None, actor_scope=str(memory.get("memory_source", "")) or None))
                    state_capable = state_capable or bool(memory.get("original_memories")) or str(memory.get("is_update", "")).casefold() == "true"
                    ordinal += 1
                for turn_index, turn in enumerate(session.get("dialogue", ())):
                    if not isinstance(turn, dict) or not isinstance(turn.get("content"), str):
                        raise ValueError("HaluMem dialogue turn lacks content")
                    source_ref = f"session:{session_index}:dialogue:{turn_index}"
                    events.append(_make_event("halumem", episode_id, source_ref, ordinal, turn, timestamp=turn.get("timestamp") if isinstance(turn.get("timestamp"), str) else None, actor_scope=str(turn.get("role", "")) or None))
                    ordinal += 1
                for question_index, question in enumerate(session.get("questions", ())):
                    if isinstance(question, dict) and isinstance(question.get("question"), str):
                        queries.append(PublicQuery(f"{episode_id}:q:{session_index}:{question_index}", question["question"], question.get("answer"), question.get("evidence", ()), f"session:{session_index}:question:{question_index}"))
            if not events or not queries:
                raise ValueError(f"HaluMem {episode_id} lacks events or source queries")
            capabilities = ["process", "poison"]
            if state_capable:
                capabilities.append("state")
            yield PublicEpisode(episode_id, "halumem", path.stem.casefold(), path.as_posix(), source_sha, tuple(events), tuple(queries), tuple(capabilities), {"uuid": row["uuid"]})


def normalize_memfail(path: Path) -> Iterator[PublicEpisode]:
    source_sha = _sha256_file(path)
    with path.open(newline="", encoding="utf-8") as handle:
        for row_index, row in enumerate(csv.DictReader(handle)):
            if not isinstance(row, dict):
                raise ValueError("MemFail CSV row is invalid")
            question = row.get("question") or row.get("graded_question")
            answer = row.get("ground_truth_answer") or row.get("correct_choice")
            episode_id = f"memfail:{path.stem}:{row_index}"
            event_fields = [key for key in ("preference_facts", "entity_facts", "fact_1", "fact_2", "fact_3", "fact_4", "chain_1", "chain_2", "chain_3", "chain_4", "chain_5", "question_context") if row.get(key)]
            if not event_fields:
                raise ValueError(f"MemFail {episode_id} has no source fact fields")
            events = tuple(_make_event("memfail", episode_id, field, ordinal, {"field": field, "value": _parse_json_cell(row[field])}, actor_scope=row.get("entity") or row.get("preference_category")) for ordinal, field in enumerate(event_fields))
            evidence = {field: row[field] for field in event_fields}
            if path.stem == "persona_dataset":
                raw_questions = _parse_json_cell(row.get("questions", ""))
                if not isinstance(raw_questions, list):
                    raise ValueError(f"MemFail {episode_id} persona questions must be a JSON list")
                queries = tuple(PublicQuery(f"{episode_id}:q:{index}", str(item["text"]), item["ground_truth_answer"], {"distractor": item.get("distractor"), "is_misleading": item.get("is_misleading")}, f"questions:{index}") for index, item in enumerate(raw_questions) if isinstance(item, dict) and isinstance(item.get("text"), str) and item.get("ground_truth_answer") is not None)
            else:
                if not question or not answer:
                    raise ValueError(f"MemFail {path.name}:{row_index} has no source query/answer")
                queries = (PublicQuery(f"{episode_id}:q", str(question), answer, evidence, "row"),)
            if not queries:
                raise ValueError(f"MemFail {episode_id} has no supported source queries")
            yield PublicEpisode(episode_id, "memfail", path.stem, path.as_posix(), source_sha, events, queries, ("process", "poison"), {"source_row": str(row_index), "template": path.stem})


def normalize_memtrace(path: Path) -> PublicEpisode:
    raw = json.loads(path.read_text(encoding="utf-8"))
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict) or not all(isinstance(data.get(key), list) for key in ("nodes", "edges", "operations", "sessions", "annotations")):
        raise ValueError(f"unsupported MemTraceBench graph: {path}")
    episode_id = f"memtrace:{raw.get('graph_id', path.stem)}"
    source_sha = _sha256_file(path)
    records: list[tuple[str, Mapping[str, Any]]] = []
    for collection in ("sessions", "operations", "nodes", "edges"):
        for index, row in enumerate(data[collection]):
            if isinstance(row, dict):
                records.append((f"{collection}:{index}", row))
    records.sort(key=lambda row: (str(row[1].get("created_at", "")), row[0]))
    events = tuple(_make_event("memtracebench", episode_id, source_ref, index, row, timestamp=str(row.get("created_at", "")) or None, actor_scope=str(row.get("user_id", "")) or None) for index, (source_ref, row) in enumerate(records))
    queries = tuple(PublicQuery(f"{episode_id}:annotation:{index}", str(row["query"]), row.get("golden_answers"), row.get("source_evidence", ()), str(row.get("query_id", index))) for index, row in enumerate(data["annotations"]) if isinstance(row, dict) and isinstance(row.get("query"), str))
    if not events or not queries:
        raise ValueError(f"MemTraceBench {episode_id} lacks events or annotations")
    return PublicEpisode(episode_id, "memtracebench", path.parent.name, path.as_posix(), source_sha, events, queries, ("process", "poison"), {"graph_id": str(raw.get("graph_id", "")), "user_id": str(raw.get("user_id", ""))})


def iter_public_episodes(source: str, root: Path) -> Iterator[PublicEpisode]:
    source = source.casefold()
    if source == "locomo":
        yield from normalize_locomo(root / "LoCoMo/locomo10.json")
    elif source == "halumem":
        for path in sorted((root / "HaluMem").glob("*.jsonl")):
            yield from normalize_halumem(path)
    elif source == "memfail":
        for path in sorted((root / "MemFail").glob("*.csv")):
            yield from normalize_memfail(path)
    elif source == "memtracebench":
        for path in sorted((root / "MemTraceBench").rglob("*.json")):
            yield normalize_memtrace(path)
    else:
        raise ValueError(f"unsupported public source: {source}")


@dataclass(frozen=True)
class InterventionSpec:
    intervention_id: str
    version: str
    incident_type: str
    constructor_family: str
    template_id: str
    seed: int
    target_event_id: str | None
    insertion_index: int
    read_set: tuple[str, ...]
    write_set: tuple[str, ...]
    before_root: str
    after_root: str
    expected_effect: Mapping[str, object]
    content_sha256: str

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


def _event_supports_state(event: PublicEvent) -> bool:
    return bool(event.payload.get("original_memories")) or str(event.payload.get("is_update", "")).casefold() == "true"


def supported_templates(episode: PublicEpisode) -> dict[str, str]:
    result = {"clean": "supported"}
    if len(episode.immutable_events) >= 2 and "process" in episode.capabilities:
        result.update({template: "supported" for template in PROCESS_TEMPLATES})
    else:
        result.update({template: "unsupported: requires two source events and process capability" for template in PROCESS_TEMPLATES})
    if "state" in episode.capabilities and any(_event_supports_state(event) for event in episode.immutable_events):
        result.update({template: "supported" for template in STATE_TEMPLATES})
    else:
        result.update({template: "unsupported: source has no explicit update/lineage semantics" for template in STATE_TEMPLATES})
    if "poison" in episode.capabilities:
        result.update({template: "supported" for template in POISON_TEMPLATES})
    else:
        result.update({template: "unsupported: source is not an event stream" for template in POISON_TEMPLATES})
    return result


@dataclass(frozen=True)
class MemoryState:
    """Materialized state, deliberately distinct from immutable source/audit logs."""

    immutable_source_log: tuple[PublicEvent, ...]
    audit_log: tuple[PublicEvent, ...]
    projection_order: tuple[str, ...]
    projection_index: tuple[tuple[str, int], ...]
    scope_projection: tuple[tuple[str, str | None], ...]
    cache_event_ids: tuple[str, ...]
    supersession_edges: tuple[tuple[str, str], ...]
    quarantine_set: tuple[str, ...]

    @property
    def root(self) -> str:
        source_ids = tuple(event.event_id for event in self.immutable_source_log)
        source_root = _SOURCE_LOG_ROOT_CACHE.get(source_ids)
        if source_root is None:
            source_root = root_of(self.immutable_source_log)
            _SOURCE_LOG_ROOT_CACHE[source_ids] = source_root
        return canonical_sha256({
            "immutable_source_log_root": source_root,
            "audit_log": [event.to_mapping() for event in self.audit_log],
            "projection_order": self.projection_order,
            "projection_index": self.projection_index,
            "scope_projection": self.scope_projection,
            "cache_event_ids": self.cache_event_ids,
            "supersession_edges": self.supersession_edges,
            "quarantine_set": self.quarantine_set,
        })

    def clone(self) -> "MemoryState":
        # The state and all nested events are immutable; a value copy is a
        # correct copy-on-write starting point for every shadow candidate.
        return self


def clean_memory_state(episode: PublicEpisode) -> MemoryState:
    ids = tuple(event.event_id for event in episode.immutable_events)
    return MemoryState(episode.immutable_events, (), ids, tuple((event_id, index) for index, event_id in enumerate(ids)), tuple((event.event_id, event.actor_scope) for event in episode.immutable_events), (), (), ())


def _synthetic_event(episode: PublicEpisode, metadata: Mapping[str, object], ordinal: int, authority: str) -> PublicEvent:
    # ``synthetic`` is an internal event-record flag.  Its payload carries
    # only domain facts that a state operator may inspect, never constructor
    # identity, template, or hidden target id.
    payload = dict(metadata)
    return _make_event(episode.source_dataset_id, episode.episode_id, f"audit:opaque:{canonical_sha256(metadata)}", ordinal, payload, actor_scope=authority, synthetic=True)


def _reindex(order: Sequence[str]) -> tuple[tuple[str, int], ...]:
    return tuple((event_id, index) for index, event_id in enumerate(order))


def _target_for(episode: PublicEpisode, template_id: str, seed: int) -> tuple[int, PublicEvent]:
    rng = random.Random(f"{episode.episode_id}:{template_id}:{seed}")
    indices = list(range(len(episode.immutable_events)))
    if template_id in STATE_TEMPLATES:
        indices = [index for index, event in enumerate(episode.immutable_events) if _event_supports_state(event)]
    if not indices:
        raise ValueError("intervention requires a source-derived target event")
    index = indices[rng.randrange(len(indices))]
    return index, episode.immutable_events[index]


def apply_intervention_state(episode: PublicEpisode, template_id: str, seed: int) -> tuple[MemoryState, str | None, int, Mapping[str, object]]:
    clean = clean_memory_state(episode)
    if template_id == "clean":
        return clean, None, len(clean.projection_order), {"kind": "identity"}
    index, target = _target_for(episode, template_id, seed)
    order = list(clean.projection_order)
    scopes = dict(clean.scope_projection)
    if template_id == "drop":
        order.remove(target.event_id)
    elif template_id == "duplicate":
        order.insert(index + 1, target.event_id)
    elif template_id == "reorder":
        other = (index + 1) % len(order)
        order[index], order[other] = order[other], order[index]
    elif template_id == "wrong_index":
        index_map = list(clean.projection_index)
        other = (index + 1) % len(index_map)
        index_map[index], index_map[other] = index_map[other], index_map[index]
        return MemoryState(clean.immutable_source_log, clean.audit_log, clean.projection_order, tuple(index_map), clean.scope_projection, clean.cache_event_ids, (), ()), target.event_id, index, {"kind": template_id, "target": target.event_id}
    elif template_id == "truncate":
        order = order[: max(1, index + 1)]
    elif template_id == "wrong_scope":
        scopes[target.event_id] = "wrong-scope"
    elif template_id == "stale_cache":
        return MemoryState(clean.immutable_source_log, clean.audit_log, clean.projection_order, clean.projection_index, clean.scope_projection, (target.event_id,), (), ()), target.event_id, index, {"kind": template_id, "target": target.event_id}
    elif template_id in STATE_TEMPLATES:
        update = _synthetic_event(episode, {"lineage_source_ref": target.source_ref, "supersedes_source_ref": target.source_ref}, len(episode.immutable_events), "source-derived")
        order.append(update.event_id)
        scopes[update.event_id] = update.actor_scope
        return MemoryState(clean.immutable_source_log, (update,), tuple(order), _reindex(order), tuple((event_id, scopes[event_id]) for event_id in order), (), (), ()), target.event_id, len(order) - 1, {"kind": template_id, "target": target.event_id, "new_event_id": update.event_id}
    elif template_id in POISON_TEMPLATES:
        injected = _synthetic_event(episode, {"provenance_scope": "external_memory_input"}, len(episode.immutable_events), "untrusted")
        order.append(injected.event_id)
        scopes[injected.event_id] = injected.actor_scope
        return MemoryState(clean.immutable_source_log, (injected,), tuple(order), _reindex(order), tuple((event_id, scopes[event_id]) for event_id in order), (), (), ()), target.event_id, len(order) - 1, {"kind": template_id, "target": target.event_id, "injected_event_id": injected.event_id, "trigger": "on_query" if template_id == "sleeper_trigger" else None}
    else:
        raise ValueError(f"unsupported intervention template: {template_id}")
    return MemoryState(clean.immutable_source_log, clean.audit_log, tuple(order), _reindex(order), tuple((event_id, scopes[event_id]) for event_id in clean.projection_order), clean.cache_event_ids, (), ()), target.event_id, index, {"kind": template_id, "target": target.event_id}


def build_intervention(episode: PublicEpisode, template_id: str, *, seed: int) -> InterventionSpec:
    status = supported_templates(episode).get(template_id)
    if status != "supported":
        raise ValueError(f"{episode.episode_id}: {template_id} is {status}")
    corrupted, target, insertion, effect = apply_intervention_state(episode, template_id, seed)
    incident = "clean" if template_id == "clean" else "process_fault" if template_id in PROCESS_TEMPLATES else "state_drift" if template_id in STATE_TEMPLATES else "poison"
    writes = tuple(sorted({target} if target else set()))
    fields = {"incident_type": incident, "constructor_family": "clean" if incident == "clean" else incident, "template_id": template_id, "seed": seed, "target_event_id": target, "insertion_index": insertion, "read_set": tuple(event.event_id for event in episode.immutable_events), "write_set": writes, "before_root": clean_memory_state(episode).root, "after_root": corrupted.root, "expected_effect": effect}
    digest = canonical_sha256({"episode_id": episode.episode_id, **fields})
    return InterventionSpec(f"intervention-{digest}", STREAM_SCHEMA, **fields, content_sha256=digest)


@dataclass(frozen=True)
class RepairCase:
    case_id: str
    lineage_id: str
    source_episode_id: str
    family_id: str
    clean_events: tuple[PublicEvent, ...]
    clean_state: MemoryState
    corrupt_state: MemoryState
    intervention: InterventionSpec
    decision_view: DecisionView
    evaluator_only: EvaluatorOnly

    def public_mapping(self) -> dict[str, object]:
        # Imported lazily to keep the compiler's type definitions independent
        # from the disk codec while still forcing every serving case through it.
        from .runtime_bundle import serialize

        return serialize(
            case_id=self.case_id,
            source_dataset_id=self.decision_view.source_dataset_id,
            source_episode_id=self.source_episode_id,
            family_id=self.family_id,
            lineage_id=self.lineage_id,
            source_event_ids=tuple(event.event_id for event in self.clean_events),
            decision_view=self.decision_view,
            memory_state=self.corrupt_state,
        )

    def evaluator_mapping(self) -> dict[str, object]:
        return {"case_id": self.case_id, "intervention": self.intervention.to_mapping(), "evaluator_only": asdict(self.evaluator_only)}


def compile_repair_case(episode: PublicEpisode, intervention: InterventionSpec) -> RepairCase:
    clean = clean_memory_state(episode)
    corrupted, _target, _insertion, _effect = apply_intervention_state(episode, intervention.template_id, intervention.seed)
    if corrupted.root != intervention.after_root:
        raise ValueError("intervention replay root mismatch")
    case_id = f"case-{canonical_sha256({'episode': episode.episode_id, 'intervention': intervention.content_sha256})}"
    lineage = f"lineage-{canonical_sha256({'episode': episode.episode_id})}"
    def runtime_event(event: PublicEvent) -> dict[str, object]:
        content: object = event.payload if not event.synthetic else {"content": "externally supplied memory event"}
        return {"event_id": event.event_id, "timestamp": event.timestamp, "actor_scope": event.actor_scope, "content": content, "authority": event.actor_scope or "source", "provenance": {"source_payload_sha256": event.source_payload_sha256}}
    event_log = tuple(corrupted.immutable_source_log + corrupted.audit_log)
    observable = {"event_count": len(event_log), "projection_size": len(corrupted.projection_order), "index_size": len(corrupted.projection_index), "cache_size": len(corrupted.cache_event_ids), "supersession_edge_count": len(corrupted.supersession_edges), "quarantine_count": len(corrupted.quarantine_set)}
    semantic_family = f"{episode.source_dataset_id}:{episode.episode_id}"
    decision = DecisionView(case_id, episode.source_dataset_id, episode.episode_id, semantic_family, lineage, len(event_log), {"event_log": [runtime_event(event) for event in event_log], "current_state": {"projection_order": list(corrupted.projection_order), "projection_index": list(corrupted.projection_index), "scope_projection": list(corrupted.scope_projection), "cache_event_ids": list(corrupted.cache_event_ids), "supersession_edges": list(corrupted.supersession_edges), "quarantine_set": list(corrupted.quarantine_set), "state_root": corrupted.root}, "observable_telemetry": observable, "predicted_syndrome": {"class": "unknown", "confidence": 0.0}}, {"source_sha256": episode.source_sha256}, ("sealed_fields_omitted",))
    legal = legal_operator_ids(intervention, corrupted)
    expected = expected_repaired_state(clean, corrupted, intervention)
    evaluator = EvaluatorOnly(intervention.incident_type, intervention.target_event_id, legal, None, {"expected_state_root": expected.root, "invariant": "projection and lineage contract", "safety": "quarantine untrusted audit events", "locality_max": 2})
    return RepairCase(case_id, lineage, episode.episode_id, semantic_family, episode.immutable_events, clean, corrupted, intervention, decision, evaluator)


@dataclass(frozen=True)
class OperatorSpec:
    operator_id: str
    incident_types: tuple[str, ...]
    template_ids: tuple[str, ...]
    precondition: str
    read_contract: str
    write_contract: str
    invariant_contract: str
    safety_contract: str
    locality_bound: int
    rollback_action: str


def operator_catalog() -> tuple[OperatorSpec, ...]:
    return (
        OperatorSpec("noop_abstain", ("clean", "process_fault", "state_drift", "poison"), ALL_TEMPLATES, "always", "none", "none", "preserve before root", "no mutation", 0, "no mutation"),
        OperatorSpec("process_restore", ("process_fault",), ("drop", "duplicate", "truncate"), "process target exists", "target and source event stream", "projection", "exact clean root", "preserve source payloads", 2, "restore before root"),
        OperatorSpec("process_replay_order", ("process_fault",), ("reorder",), "typed: swapped neighboring source events", "event order", "projection order", "exact clean root", "preserve source payloads", 2, "restore before root"),
        OperatorSpec("process_rebuild_index", ("process_fault",), ("wrong_index",), "index target exists", "event index", "index projection", "exact clean root", "preserve source payloads", 2, "restore before root"),
        OperatorSpec("process_scope_repair", ("process_fault",), ("wrong_scope",), "scope target exists", "actor scope", "scope projection", "exact clean root", "preserve source payloads", 1, "restore before root"),
        OperatorSpec("process_cache_invalidate", ("process_fault",), ("stale_cache",), "cache synthetic event exists", "cache entry", "cache projection", "exact clean root", "remove stale cache only", 1, "restore before root"),
        OperatorSpec("state_supersede_lineage", ("state_drift",), STATE_TEMPLATES, "source-derived lineage exists", "source lineage", "state projection", "exact clean root", "remove only synthetic invalidation", 2, "restore before root"),
        OperatorSpec("poison_quarantine_audit", ("poison",), POISON_TEMPLATES, "untrusted synthetic event exists", "untrusted event", "quarantine projection", "exact clean root", "remove untrusted event and retain audit", 1, "restore before root"),
    )


def legal_operator_ids(intervention: InterventionSpec, state: MemoryState) -> tuple[str, ...]:
    return tuple(spec.operator_id for spec in operator_catalog() if _mask_reason(spec, intervention, state) is None)


@dataclass(frozen=True)
class ShadowOutcome:
    case_id: str
    operator_id: str
    legal: bool
    mask_reason: str | None
    executed: bool
    root_corrected: bool
    invariants_passed: bool
    safety_passed: bool
    locality_cost: int
    committed: bool
    rolled_back: bool
    before_root: str
    after_root: str
    utility: float


@dataclass(frozen=True)
class EvaluatorOracleTransform:
    """Sealed full-mechanism upper-bound action, never a runtime skill."""

    action_id: str
    root_corrected: bool
    invariants_passed: bool
    safety_passed: bool
    locality_cost: int
    after_root: str
    utility: float


@dataclass(frozen=True)
class ShadowOutcomeMatrix:
    case_id: str
    entries: tuple[ShadowOutcome, ...]
    candidate_set_oracle: str | None
    library_oracle: str | None
    mechanism_oracle: str | None
    candidate_member_ids: tuple[str, ...]
    library_member_ids: tuple[str, ...]
    mechanism_member_ids: tuple[str, ...]
    evaluator_oracle_transform: EvaluatorOracleTransform
    matrix_sha256: str


def _source_ids(state: MemoryState) -> tuple[str, ...]:
    return tuple(event.event_id for event in state.immutable_source_log)


def _source_scope(state: MemoryState, event_id: str) -> str | None:
    return next(event.actor_scope for event in state.immutable_source_log if event.event_id == event_id)


def _replace_state(
    before: MemoryState,
    *,
    projection_order: tuple[str, ...] | None = None,
    projection_index: tuple[tuple[str, int], ...] | None = None,
    scope_projection: tuple[tuple[str, str | None], ...] | None = None,
    cache_event_ids: tuple[str, ...] | None = None,
    supersession_edges: tuple[tuple[str, str], ...] | None = None,
    quarantine_set: tuple[str, ...] | None = None,
) -> MemoryState:
    return MemoryState(
        before.immutable_source_log,
        before.audit_log,
        before.projection_order if projection_order is None else projection_order,
        before.projection_index if projection_index is None else projection_index,
        before.scope_projection if scope_projection is None else scope_projection,
        before.cache_event_ids if cache_event_ids is None else cache_event_ids,
        before.supersession_edges if supersession_edges is None else supersession_edges,
        before.quarantine_set if quarantine_set is None else quarantine_set,
    )


def _typed_precondition(spec: OperatorSpec, state: MemoryState) -> bool:
    """Runtime-only predicate over the materialized state, without sealed truth."""
    if spec.operator_id == "noop_abstain":
        return True
    source_ids = _source_ids(state)
    order = state.projection_order
    if spec.operator_id == "process_restore":
        source_set, order_set = set(source_ids), set(order)
        missing_one = len(order) == len(source_ids) - 1 and order_set < source_set
        duplicate_one = len(order) == len(source_ids) + 1 and any(count > 1 for count in Counter(order).values())
        truncated = len(order) < len(source_ids) and order == source_ids[:len(order)]
        return missing_one or duplicate_one or truncated
    if spec.operator_id == "process_replay_order":
        return len(order) == len(source_ids) and order != source_ids and set(order) == set(source_ids)
    if spec.operator_id == "process_rebuild_index":
        return order == source_ids and state.projection_index != _reindex(order)
    if spec.operator_id == "process_scope_repair":
        scopes = dict(state.scope_projection)
        return sum(
            scopes.get(event.event_id) != event.actor_scope
            for event in state.immutable_source_log
        ) == 1
    if spec.operator_id == "process_cache_invalidate":
        return bool(state.cache_event_ids)
    if spec.operator_id == "state_supersede_lineage":
        return _state_lineage_pair(state) is not None
    if spec.operator_id == "poison_quarantine_audit":
        order_ids, quarantined = set(order), set(state.quarantine_set)
        return sum(
            event.actor_scope == "untrusted" and event.event_id in order_ids and event.event_id not in quarantined
            for event in state.audit_log
        ) == 1
    return False


def _mask_reason(spec: OperatorSpec, intervention: InterventionSpec, state: MemoryState) -> str | None:
    if intervention.incident_type not in spec.incident_types:
        return "incident type incompatible"
    if intervention.template_id not in spec.template_ids:
        return "typed template precondition mismatch"
    if not _typed_precondition(spec, state):
        return "typed state precondition failed"
    return None


def expected_repaired_state(clean: MemoryState, corrupt: MemoryState, intervention: InterventionSpec) -> MemoryState:
    effect = intervention.expected_effect
    if intervention.incident_type == "process_fault":
        return MemoryState(clean.immutable_source_log, corrupt.audit_log, clean.projection_order, clean.projection_index, clean.scope_projection, clean.cache_event_ids, corrupt.supersession_edges, corrupt.quarantine_set)
    if intervention.incident_type == "state_drift":
        new_event_id = str(effect["new_event_id"])
        target = str(intervention.target_event_id)
        order = tuple(event_id for event_id in corrupt.projection_order if event_id != target)
        return MemoryState(clean.immutable_source_log, corrupt.audit_log, order, _reindex(order), corrupt.scope_projection, (), ((target, new_event_id),), corrupt.quarantine_set)
    if intervention.incident_type == "poison":
        injected = str(effect["injected_event_id"])
        order = tuple(event_id for event_id in corrupt.projection_order if event_id != injected)
        return MemoryState(clean.immutable_source_log, corrupt.audit_log, order, _reindex(order), corrupt.scope_projection, (), corrupt.supersession_edges, (injected,))
    return clean


def _locality(case: RepairCase, before: MemoryState, after: MemoryState) -> int:
    """Count the repair surface, not denormalized projection fallout.

    A truncation may make many entries temporarily absent from a materialized
    view, but replaying that pipeline stage is still a patch to its one target
    surface.  Source/audit identities remain immutable throughout.
    """
    if before == after:
        return 0
    if case.intervention.incident_type == "state_drift":
        return 2  # old current pointer plus the superseding source-derived event
    return 1


def _state_lineage_pair(state: MemoryState) -> tuple[str, str] | None:
    updates = [
        event for event in state.audit_log
        if event.actor_scope == "source-derived" and event.event_id in state.projection_order
    ]
    if len(updates) != 1:
        return None
    source_ref = updates[0].payload.get("supersedes_source_ref")
    if not isinstance(source_ref, str):
        return None
    old = [event.event_id for event in state.immutable_source_log if event.source_ref == source_ref]
    if len(old) != 1 or old[0] not in state.projection_order:
        return None
    return old[0], updates[0].event_id


def execute_operator(state: MemoryState, spec: OperatorSpec) -> MemoryState:
    """Pure runtime repair: only corrupt materialized state plus operator contract."""
    before = state.clone()
    if spec.operator_id == "noop_abstain":
        return before
    source_ids = _source_ids(before)
    if spec.operator_id == "process_restore":
        order = list(before.projection_order)
        order_counts = Counter(order)
        missing = [event_id for event_id in source_ids if order_counts[event_id] == 0]
        duplicated = [event_id for event_id in source_ids if order_counts[event_id] > 1]
        if len(missing) == 1 and len(order) == len(source_ids) - 1:
            order.insert(source_ids.index(missing[0]), missing[0])
        elif len(duplicated) == 1 and len(order) == len(source_ids) + 1:
            order.pop(max(index for index, event_id in enumerate(order) if event_id == duplicated[0]))
        elif len(order) < len(source_ids) and tuple(order) == source_ids[:len(order)]:
            order.extend(source_ids[len(order):])
        else:
            return before
        repaired_order = tuple(order)
        return _replace_state(before, projection_order=repaired_order, projection_index=_reindex(repaired_order))
    if spec.operator_id == "process_replay_order":
        order = list(before.projection_order)
        if len(order) != len(source_ids):
            return before
        mismatches = [index for index, pair in enumerate(zip(order, source_ids)) if pair[0] != pair[1]]
        if len(mismatches) == 2:
            left, right = mismatches
            adjacent = right == left + 1 or (left == 0 and right == len(order) - 1)
            if adjacent and order[left] == source_ids[right] and order[right] == source_ids[left]:
                order[left], order[right] = order[right], order[left]
                repaired_order = tuple(order)
                return _replace_state(before, projection_order=repaired_order, projection_index=_reindex(repaired_order))
        return before
    if spec.operator_id == "process_rebuild_index":
        return _replace_state(before, projection_index=_reindex(before.projection_order))
    if spec.operator_id == "process_scope_repair":
        scopes = dict(before.scope_projection)
        mismatches = [
            event.event_id for event in before.immutable_source_log
            if scopes.get(event.event_id) != event.actor_scope
        ]
        if len(mismatches) != 1:
            return before
        target = mismatches[0]
        scopes[target] = _source_scope(before, target)
        return _replace_state(before, scope_projection=tuple((event_id, scopes[event_id]) for event_id in before.projection_order))
    if spec.operator_id == "process_cache_invalidate":
        return _replace_state(before, cache_event_ids=())
    if spec.operator_id == "state_supersede_lineage":
        lineage = _state_lineage_pair(before)
        if lineage is None:
            return before
        old, update = lineage
        order = tuple(event_id for event_id in before.projection_order if event_id != old)
        edges = before.supersession_edges + ((old, update),)
        return _replace_state(before, projection_order=order, projection_index=_reindex(order), supersession_edges=edges)
    if spec.operator_id == "poison_quarantine_audit":
        injected = [
            event.event_id for event in before.audit_log
            if event.actor_scope == "untrusted" and event.event_id in before.projection_order
        ]
        if len(injected) != 1:
            return before
        order = tuple(event_id for event_id in before.projection_order if event_id != injected[0])
        quarantine = tuple(sorted(set(before.quarantine_set + (injected[0],))))
        return _replace_state(before, projection_order=order, projection_index=_reindex(order), quarantine_set=quarantine)
    return before


def execute_copy_on_write(case: RepairCase, spec: OperatorSpec) -> MemoryState:
    """Thin case adapter; execution itself is isolated from sealed sidecar truth."""
    return execute_operator(case.corrupt_state.clone(), spec)


def execute_shadow(case: RepairCase, spec: OperatorSpec) -> ShadowOutcome:
    before = case.corrupt_state.clone()
    before_root = before.root
    reason = _mask_reason(spec, case.intervention, before)
    if reason is not None:
        return ShadowOutcome(case.case_id, spec.operator_id, False, reason, False, False, False, False, 0, False, False, before_root, before_root, -1.0)
    after = execute_copy_on_write(case, spec)
    expected = expected_repaired_state(case.clean_state, before, case.intervention)
    locality = _locality(case, before, after)
    root_corrected = after.root == expected.root
    invariants = root_corrected and after.immutable_source_log == before.immutable_source_log
    safety = (case.intervention.incident_type != "poison" or bool(after.quarantine_set)) and all(event in after.audit_log for event in before.audit_log)
    committed = root_corrected and invariants and safety and locality <= spec.locality_bound
    final = after if committed else before
    return ShadowOutcome(case.case_id, spec.operator_id, True, None, True, root_corrected, invariants, safety, locality, committed, not committed, before_root, final.root, 1.0 if committed else -1.0)


def _runtime_compatible_ids(case: RepairCase) -> tuple[str, ...]:
    return tuple(
        spec.operator_id
        for spec in operator_catalog()
        if _typed_precondition(spec, case.corrupt_state)
    )


def _candidate_available_ids(case: RepairCase, library_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Deterministic retrieval gate over runtime-visible state, never evaluator truth."""
    if not library_ids:
        return ()
    digest = canonical_sha256({
        "case_id": case.decision_view.case_id,
        "state_root": case.decision_view.observation["current_state"]["state_root"],
        "catalog": "cmd-spec-v03-typed-candidate-v1",
    })
    return (library_ids[int(digest[:8], 16) % len(library_ids)],)


def _mechanism_oracle_id(case: RepairCase) -> str:
    return f"oracle_transform:{case.intervention.incident_type}"


def _evaluate_mechanism_oracle(case: RepairCase) -> EvaluatorOracleTransform:
    """Score the evaluator's full-state transform outside runtime execution."""
    expected = expected_repaired_state(case.clean_state, case.corrupt_state, case.intervention)
    return EvaluatorOracleTransform(
        _mechanism_oracle_id(case),
        True,
        expected.immutable_source_log == case.corrupt_state.immutable_source_log,
        case.intervention.incident_type != "poison" or bool(expected.quarantine_set),
        _locality(case, case.corrupt_state, expected),
        expected.root,
        2.0,
    )


def build_shadow_matrix(case: RepairCase) -> ShadowOutcomeMatrix:
    entries = tuple(execute_shadow(case, spec) for spec in operator_catalog())
    if len(entries) != len(operator_catalog()):
        raise AssertionError("shadow matrix must record every catalog candidate")
    by_id = {entry.operator_id: entry for entry in entries}
    library_ids = _runtime_compatible_ids(case)
    candidate_ids = _candidate_available_ids(case, library_ids)
    oracle_id = _mechanism_oracle_id(case)
    mechanism_ids = tuple((*library_ids, oracle_id))
    oracle_transform = _evaluate_mechanism_oracle(case)
    candidate = max(candidate_ids, key=lambda action_id: by_id[action_id].utility) if candidate_ids else None
    library = max(library_ids, key=lambda action_id: by_id[action_id].utility) if library_ids else None
    # This sealed evaluator transform is deliberately outside the frozen
    # runtime library.  It bounds the mechanism, rather than impersonating a
    # retrievable skill.
    mechanism = oracle_id
    body = {
        "case_id": case.case_id,
        "entries": [asdict(entry) for entry in entries],
        "candidate_set_oracle": candidate,
        "library_oracle": library,
        "mechanism_oracle": mechanism,
        "candidate_member_ids": candidate_ids,
        "library_member_ids": library_ids,
        "mechanism_member_ids": mechanism_ids,
        "evaluator_oracle_transform": asdict(oracle_transform),
    }
    return ShadowOutcomeMatrix(case.case_id, entries, candidate, library, mechanism, candidate_ids, library_ids, mechanism_ids, oracle_transform, canonical_sha256(body))
