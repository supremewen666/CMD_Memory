#!/usr/bin/env python3
"""Replay V4 select/outcome/chain events with zero model calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping, Sequence

from cmd_audit.counterfactual.relation_graph import FrozenRelationGraph
from cmd_audit.repair.evolution_repository import EvolutionRepository
from cmd_audit.repair.neuro_symbolic_evolution import (
    EvolutionSelection,
    EvolutionUpdate,
    NeuroSymbolicEvolutionEngine,
    evolution_report,
)
from cmd_audit.repair.parametric_policy import (
    OutcomeObservation,
    PolicyContext,
    RepairIntent,
)
from cmd_audit.repair.repair_chain_governance import (
    ChainAttemptInput,
    ChainGovernanceDecision,
)


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _closed(
    value: object,
    keys: set[str],
    name: str,
) -> Mapping[str, object]:
    mapping = _mapping(value, name)
    if set(mapping) != keys:
        raise ValueError(f"{name} must be closed with exactly {sorted(keys)}")
    return mapping


def load_events(path: Path) -> tuple[Mapping[str, object], ...]:
    rows: list[Mapping[str, object]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSONL at line {line_number}") from error
        rows.append(_mapping(row, f"event line {line_number}"))
    if not rows:
        raise ValueError("evolution event stream is empty")
    return tuple(rows)


def replay_events(
    events: Sequence[Mapping[str, object]],
    *,
    repository: EvolutionRepository,
) -> dict[str, object]:
    engine = NeuroSymbolicEvolutionEngine(repository)
    selections: list[EvolutionSelection] = []
    updates: list[EvolutionUpdate] = []
    chain_decisions: list[ChainGovernanceDecision] = []
    by_selection: dict[str, EvolutionSelection] = {}
    for row in events:
        record_type = row.get("record_type")
        if record_type == "select":
            closed = _closed(
                row,
                {"record_type", "context", "graph", "intents"},
                "closed select record",
            )
            context = PolicyContext.from_mapping(
                _mapping(closed["context"], "policy context")
            )
            graph = FrozenRelationGraph.from_mapping(closed["graph"])
            raw_intents = closed["intents"]
            if not isinstance(raw_intents, list):
                raise ValueError("select intents must be a list")
            intents = tuple(
                RepairIntent.from_mapping(_mapping(item, "repair intent"))
                for item in raw_intents
            )
            selection = engine.select(context, graph=graph, intents=intents)
            selections.append(selection)
            by_selection[selection.decision.selection_id] = selection
            continue
        if record_type == "outcome":
            closed = _closed(
                row,
                {"record_type", "selection_id", "observations"},
                "closed outcome record",
            )
            selection_id = closed["selection_id"]
            if not isinstance(selection_id, str) or selection_id not in by_selection:
                raise ValueError("outcome references an unknown/future selection")
            raw_observations = closed["observations"]
            if not isinstance(raw_observations, list):
                raise ValueError("outcome observations must be a list")
            observations = tuple(
                OutcomeObservation.from_mapping(
                    _mapping(item, "outcome observation")
                )
                for item in raw_observations
            )
            updates.append(
                engine.record_outcomes(
                    by_selection[selection_id].decision,
                    observations,
                )
            )
            continue
        if record_type == "chain":
            closed = _closed(
                row,
                {"record_type", "attempt"},
                "closed chain record",
            )
            attempt_mapping = _mapping(closed["attempt"], "chain attempt")
            if set(attempt_mapping) != set(ChainAttemptInput.__dataclass_fields__):
                raise ValueError("chain attempt mapping is not closed")
            chain_decisions.append(
                engine.record_chain_attempt(ChainAttemptInput(**attempt_mapping))
            )
            continue
        raise ValueError(f"unknown evolution record_type: {record_type!r}")
    return evolution_report(
        repository=repository,
        selections=selections,
        updates=updates,
        chain_decisions=chain_decisions,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay governed neuro-symbolic memory evolution events"
    )
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    events = load_events(args.events)
    args.repository.parent.mkdir(parents=True, exist_ok=True)
    with EvolutionRepository(args.repository) as repository:
        report = replay_events(events, repository=repository)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
