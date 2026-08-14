"""Live typed-intent execution and post-selection shadow scoring for V4."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from cmd_audit.core.models import ProbeCase
from cmd_audit.counterfactual.relation_graph import FrozenRelationGraph
from cmd_audit.counterfactual.repair_state import initial_state_from_runtime_case
from cmd_audit.counterfactual.successor_state_executor import execute_program
from cmd_audit.eval.state_intent import (
    RuntimeEvent,
    RuntimeMemoryItem,
    RuntimeRepairCase,
)
from cmd_audit.repair.parametric_policy import (
    PolicyContext,
    RepairIntent,
    compile_intent,
)
from cmd_audit.repair.repair_chain_governance import ChainAttemptInput
from cmd_audit.scoring import score_answer_with_verifier
from experiments.v4_prequential_runner import (
    CASE_SCHEMA_VERSION,
    V4CandidateOutcome,
    V4PrequentialCase,
)


LIVE_INPUT_SCHEMA_VERSION = "cmd-v4-live-materialization-input-v1"
_SOURCE_KEYS = {
    "schema_version",
    "case_id",
    "family_id",
    "probe_set",
    "context",
    "graph",
    "runtime_case",
    "intents",
    "legacy_intent_id",
    "chain_pairs",
    "probe_case",
}


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def runtime_case_from_mapping(value: object) -> RuntimeRepairCase:
    mapping = _mapping(value, "runtime_case")
    expected = {
        "case_id",
        "family_id",
        "query",
        "token_budget",
        "runtime_surface",
        "items",
        "raw_events",
    }
    if set(mapping) != expected:
        raise ValueError("runtime_case mapping is not closed")
    raw_items = mapping["items"]
    raw_events = mapping["raw_events"]
    if not isinstance(raw_items, list) or not isinstance(raw_events, list):
        raise ValueError("runtime_case items/raw_events must be lists")
    items: list[RuntimeMemoryItem] = []
    for raw in raw_items:
        item = _mapping(raw, "runtime item")
        if set(item) != {
            "item_id",
            "text",
            "source_event_ids",
            "store",
            "rank",
            "retrieved",
        }:
            raise ValueError("runtime item mapping is not closed")
        items.append(
            RuntimeMemoryItem(
                item_id=item["item_id"],
                text=item["text"],
                source_event_ids=tuple(item["source_event_ids"]),
                store=item["store"],
                rank=item["rank"],
                retrieved=item["retrieved"],
            )
        )
    events: list[RuntimeEvent] = []
    for raw in raw_events:
        event = _mapping(raw, "runtime event")
        if set(event) != {"event_id", "text"}:
            raise ValueError("runtime event mapping is not closed")
        events.append(RuntimeEvent(event["event_id"], event["text"]))
    return RuntimeRepairCase(
        case_id=mapping["case_id"],
        family_id=mapping["family_id"],
        query=mapping["query"],
        token_budget=mapping["token_budget"],
        runtime_surface=mapping["runtime_surface"],
        items=tuple(items),
        raw_events=tuple(events),
    )


@dataclass(frozen=True)
class FrozenLiveInput:
    """Fully parsed, zero-model-call view of one prepared GPU input row."""

    case_id: str
    family_id: str
    probe_set: str
    context: PolicyContext
    graph: FrozenRelationGraph
    runtime_case: RuntimeRepairCase
    intents: tuple[RepairIntent, ...]
    legacy_intent_id: str
    chain_pairs: tuple[tuple[str, str], ...]
    probe_case: ProbeCase


def validate_live_input(source: Mapping[str, object]) -> FrozenLiveInput:
    """Fail closed before a prepared row can initialize an answerer or judge."""
    if (
        set(source) != _SOURCE_KEYS
        or source.get("schema_version") != LIVE_INPUT_SCHEMA_VERSION
    ):
        raise ValueError("live V4 source mapping is not closed or versioned")
    case_id = source["case_id"]
    family_id = source["family_id"]
    probe_set = source["probe_set"]
    if not isinstance(case_id, str) or not case_id:
        raise ValueError("live V4 case_id must be a non-empty string")
    if not isinstance(family_id, str) or not family_id:
        raise ValueError("live V4 family_id must be a non-empty string")
    if probe_set not in {"represented", "unseen"}:
        raise ValueError("live V4 probe_set must be represented or unseen")
    graph = FrozenRelationGraph.from_mapping(source["graph"])
    runtime = runtime_case_from_mapping(source["runtime_case"])
    context = PolicyContext.from_mapping(_mapping(source["context"], "context"))
    probe = ProbeCase.from_mapping(dict(_mapping(source["probe_case"], "probe_case")))
    raw_intents = source["intents"]
    if not isinstance(raw_intents, list) or not raw_intents:
        raise ValueError("live V4 intents must be a non-empty list")
    intents = tuple(
        RepairIntent.from_mapping(_mapping(row, "repair intent")) for row in raw_intents
    )
    intent_ids = {intent.intent_id for intent in intents}
    if len(intent_ids) != len(intents):
        raise ValueError("live V4 intent IDs must be unique")
    if {
        graph.case_id,
        runtime.case_id,
        context.case_id,
        probe.case_id,
    } != {case_id}:
        raise ValueError("live materialization case identities disagree")
    graph.assert_matches(
        case=runtime,
        item_ids=tuple(item.item_id for item in runtime.items if item.retrieved),
        expected_graph_sha256=graph.graph_sha256,
        expected_protocol_manifest_sha256=graph.protocol_manifest_sha256,
    )
    for intent in intents:
        compile_intent(intent, graph=graph)
    legacy_intent_id = source["legacy_intent_id"]
    if legacy_intent_id not in intent_ids:
        raise ValueError("legacy_intent_id must identify one frozen intent")
    raw_pairs = source["chain_pairs"]
    if not isinstance(raw_pairs, list):
        raise ValueError("chain_pairs must be a list")
    pairs: list[tuple[str, str]] = []
    for raw_pair in raw_pairs:
        if (
            not isinstance(raw_pair, list)
            or len(raw_pair) != 2
            or not all(isinstance(value, str) for value in raw_pair)
        ):
            raise ValueError("chain pair must be [first_intent_id, second_intent_id]")
        first_id, second_id = raw_pair
        if (
            first_id == second_id
            or first_id not in intent_ids
            or second_id not in intent_ids
        ):
            raise ValueError("chain pair must reference two distinct frozen intents")
        pairs.append((first_id, second_id))
    if len(set(pairs)) != len(pairs):
        raise ValueError("chain pairs must be unique")
    return FrozenLiveInput(
        case_id=case_id,
        family_id=family_id,
        probe_set=probe_set,
        context=context,
        graph=graph,
        runtime_case=runtime,
        intents=intents,
        legacy_intent_id=legacy_intent_id,
        chain_pairs=tuple(pairs),
        probe_case=probe,
    )


def _changed_item_ids(state: object) -> set[str]:
    changed: set[str] = set()
    for event in state.trace:
        if event.before_hash != event.after_hash:
            changed.update(event.matched_item_ids)
    return changed


class V4LiveMaterializer:
    """Execute exact graph-bound programs, then expose outcomes to shadow only."""

    def __init__(
        self,
        *,
        answer_client: Any | None = None,
        answer_verifier: Any | None = None,
        locality_penalty: float = 1.0,
        change_penalty: float = 0.05,
    ) -> None:
        if answer_client is None or answer_verifier is None:
            from experiments.experiment_runner_common import (
                assert_g_eval_available,
                assert_live_llm_env_configured,
                build_answer_verifier,
                build_clients,
            )

            assert_live_llm_env_configured()
            built_answer, judge = build_clients()
            assert_g_eval_available(judge, role="v4-shadow-judge")
            answer_client = built_answer if answer_client is None else answer_client
            answer_verifier = (
                build_answer_verifier(judge, answer_mode="answer-rubric")
                if answer_verifier is None
                else answer_verifier
            )
        self.answer_client = answer_client
        self.answer_verifier = answer_verifier
        self.locality_penalty = float(locality_penalty)
        self.change_penalty = float(change_penalty)
        if not all(
            math.isfinite(value)
            for value in (self.locality_penalty, self.change_penalty)
        ):
            raise ValueError("live materializer penalties must be finite")

    def materialize(self, source: Mapping[str, object], lane: str) -> dict[str, object]:
        if lane not in {"gpu0", "gpu1", "single_gpu"}:
            raise ValueError(
                "live V4 materialization lane must be gpu0, gpu1, or single_gpu"
            )
        frozen = validate_live_input(source)
        case_id = frozen.case_id
        graph = frozen.graph
        runtime = frozen.runtime_case
        context = frozen.context
        probe = frozen.probe_case
        intents = frozen.intents
        programs = {
            intent.intent_id: compile_intent(intent, graph=graph) for intent in intents
        }
        initial = initial_state_from_runtime_case(runtime)
        outcomes: list[V4CandidateOutcome] = []
        states: dict[str, object] = {}
        for intent in intents:
            result = execute_program(
                programs[intent.intent_id],
                runtime,
                initial,
                graph=graph,
                expected_graph_sha256=graph.graph_sha256,
                expected_protocol_manifest_sha256=graph.protocol_manifest_sha256,
            )
            states[intent.intent_id] = result.state
            outcomes.append(
                self._score_state(intent.intent_id, probe, runtime, result.state)
            )
        chain_attempts = self._materialize_chains(
            source,
            probe=probe,
            runtime=runtime,
            graph=graph,
            programs=programs,
            intents=intents,
            states=states,
            outcomes=outcomes,
            initial=initial,
            context=context,
        )
        final = V4PrequentialCase(
            case_id=case_id,
            family_id=source["family_id"],
            probe_set=source["probe_set"],
            context=context,
            graph=graph,
            intents=intents,
            legacy_intent_id=source["legacy_intent_id"],
            candidate_outcomes=tuple(outcomes),
            chain_attempts=chain_attempts,
        )
        result = final.to_mapping()
        if result["schema_version"] != CASE_SCHEMA_VERSION:
            raise AssertionError(
                "live materializer emitted an unregistered case schema"
            )
        return result

    def _score_state(
        self,
        intent_id: str,
        probe: ProbeCase,
        runtime: RuntimeRepairCase,
        state: object,
    ) -> V4CandidateOutcome:
        changed = _changed_item_ids(state)
        locality = len(changed) / max(1, len(state.items))
        valid = state.token_count <= runtime.token_budget
        if not valid:
            return V4CandidateOutcome(
                intent_id, 0.0, locality, len(changed), False, True
            )
        answer = self.answer_client.generate(
            f"Query: {runtime.query}\n\nRetrieved Memory:\n{state.rendered_context}\n\nAnswer the query from memory.",
            system="Use only the supplied retrieved memory. Give a concise answer.",
        )
        score = score_answer_with_verifier(
            self.answer_verifier, answer, probe.gold_answer
        )
        recovery = float(score) - float(probe.primary_baseline.answer_score)
        return V4CandidateOutcome(
            intent_id, recovery, locality, len(changed), True, False
        )

    def _materialize_chains(
        self,
        source: Mapping[str, object],
        *,
        probe: ProbeCase,
        runtime: RuntimeRepairCase,
        graph: FrozenRelationGraph,
        programs: Mapping[str, object],
        intents: tuple[RepairIntent, ...],
        states: Mapping[str, object],
        outcomes: list[V4CandidateOutcome],
        initial: object,
        context: PolicyContext,
    ) -> tuple[ChainAttemptInput, ...]:
        raw_pairs = source["chain_pairs"]
        if not isinstance(raw_pairs, list):
            raise ValueError("chain_pairs must be a list")
        intent_by_id = {row.intent_id: row for row in intents}
        outcome_by_id = {row.intent_id: row for row in outcomes}
        attempts: list[ChainAttemptInput] = []
        for offset, raw_pair in enumerate(raw_pairs, 2):
            if (
                not isinstance(raw_pair, list)
                or len(raw_pair) != 2
                or not all(isinstance(value, str) for value in raw_pair)
            ):
                raise ValueError(
                    "chain pair must be [first_intent_id, second_intent_id]"
                )
            first_id, second_id = raw_pair
            if (
                first_id == second_id
                or first_id not in intent_by_id
                or second_id not in intent_by_id
            ):
                raise ValueError(
                    "chain pair must reference two distinct frozen intents"
                )
            first_state = states[first_id]
            chained = execute_program(
                programs[second_id],
                runtime,
                first_state,
                graph=graph,
                expected_graph_sha256=graph.graph_sha256,
                expected_protocol_manifest_sha256=graph.protocol_manifest_sha256,
            ).state
            chain_shadow = self._score_state(
                f"chain:{first_id}:{second_id}", probe, runtime, chained
            )
            first_outcome = outcome_by_id[first_id]
            second_outcome = outcome_by_id[second_id]
            attempts.append(
                ChainAttemptInput(
                    case_id=runtime.case_id,
                    family_id=source["family_id"],
                    event_index=context.event_index + offset,
                    first_strategy_id=intent_by_id[first_id].strategy_id,
                    second_strategy_id=intent_by_id[second_id].strategy_id,
                    first_utility=self._utility(first_outcome),
                    second_utility=self._utility(second_outcome),
                    chain_utility=self._utility(chain_shadow),
                    materialized_intermediate=first_state.state_hash
                    != initial.state_hash,
                    changed_item_count=chain_shadow.changed_item_count,
                    locality_cost=chain_shadow.locality_cost,
                    valid=chain_shadow.valid,
                    rolled_back=chain_shadow.rolled_back,
                    typed_conflict=False,
                    anchor_regression=False,
                    first_intent_id=first_id,
                    second_intent_id=second_id,
                )
            )
        return tuple(attempts)

    def _utility(self, outcome: V4CandidateOutcome) -> float:
        if not outcome.valid or outcome.rolled_back:
            return 0.0
        return (
            outcome.recovery_gain
            - self.locality_penalty * outcome.locality_cost
            - self.change_penalty * outcome.changed_item_count
        )


_DEFAULT_MATERIALIZER: V4LiveMaterializer | None = None


def live_backend(source: Mapping[str, object], lane: str) -> dict[str, object]:
    global _DEFAULT_MATERIALIZER
    if _DEFAULT_MATERIALIZER is None:
        _DEFAULT_MATERIALIZER = V4LiveMaterializer()
    return _DEFAULT_MATERIALIZER.materialize(source, lane)


__all__ = [
    "FrozenLiveInput",
    "LIVE_INPUT_SCHEMA_VERSION",
    "V4LiveMaterializer",
    "live_backend",
    "runtime_case_from_mapping",
    "validate_live_input",
]
