"""Seeded Darwinian evolution over immutable typed ``OperatorSpec`` values."""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import math
import random
from typing import Mapping, Sequence

from ..counterfactual.actions import PipelineAction
from ..counterfactual.operators import OperatorSpec


SELECTION_MODES = ("global_truncation", "niche_elite")
_MUTABLE_ACTIONS = tuple(
    action for action in PipelineAction if action != PipelineAction.IDENTITY
)


@dataclass(frozen=True)
class FitnessObservation:
    case_id: str
    recovery_gain: float | None
    execution_cost: float
    niche: str | None = None

    @property
    def finite(self) -> bool:
        if self.recovery_gain is None:
            return False
        try:
            return math.isfinite(float(self.recovery_gain))
        except (TypeError, ValueError):
            return False


@dataclass(frozen=True)
class OperatorIndividual:
    individual_id: str
    operator: OperatorSpec
    generation: int
    parent_ids: tuple[str, ...]
    birth_operation: str
    niche: str | None = None
    observations: tuple[FitnessObservation, ...] = ()

    @property
    def spec_hash(self) -> str:
        return self.operator.content_hash()

    @property
    def aggregate_fitness(self) -> float:
        values = [
            float(item.recovery_gain)
            for item in self.observations
            if item.finite
        ]
        return sum(values) / len(values) if values else -math.inf

    @property
    def aggregate_cost(self) -> float:
        values = [
            float(item.execution_cost)
            for item in self.observations
            if item.finite and math.isfinite(float(item.execution_cost))
        ]
        return sum(values) / len(values) if values else math.inf

    def with_observations(
        self,
        observations: Sequence[FitnessObservation],
    ) -> "OperatorIndividual":
        niche = self.niche
        if niche is None:
            niches = sorted(
                {
                    item.niche
                    for item in observations
                    if item.niche is not None
                }
            )
            if len(niches) == 1:
                niche = niches[0]
        return replace(
            self,
            niche=niche,
            observations=self.observations + tuple(observations),
        )


@dataclass(frozen=True)
class OffspringResult:
    operator: OperatorSpec | None
    operation: str
    rejection_reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.operator is not None and not self.rejection_reason


@dataclass(frozen=True)
class RejectedOffspring:
    operation: str
    parent_ids: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class EvolutionAudit:
    seed: int
    selection_mode: str
    selected_survivor_ids: tuple[str, ...]
    child_ids: tuple[str, ...]
    rejected_offspring: tuple[RejectedOffspring, ...]


@dataclass(frozen=True)
class DarwinianPopulation:
    population_id: str
    generation: int
    capacity: int
    members: tuple[OperatorIndividual, ...]

    @classmethod
    def from_specs(
        cls,
        specs: Sequence[OperatorSpec],
        *,
        capacity: int,
        niches: Mapping[str, str] | None = None,
    ) -> "DarwinianPopulation":
        if capacity < 0:
            raise ValueError("capacity must be >= 0")
        unique: dict[str, OperatorSpec] = {}
        for spec in specs:
            if not spec.steps:
                continue
            unique.setdefault(spec.content_hash(), spec)
        members = tuple(
            _make_individual(
                operator=spec,
                generation=0,
                parent_ids=(),
                birth_operation="founder",
                niche=(niches or {}).get(spec_hash),
            )
            for spec_hash, spec in sorted(unique.items())
        )[:capacity]
        return cls(
            population_id=_population_id(0, capacity, members),
            generation=0,
            capacity=capacity,
            members=members,
        )

    def evolve(
        self,
        observations: Mapping[str, Sequence[FitnessObservation]],
        *,
        seed: int,
        selection_mode: str = "global_truncation",
        survivor_fraction: float = 0.5,
    ) -> tuple["DarwinianPopulation", EvolutionAudit]:
        if selection_mode not in SELECTION_MODES:
            raise ValueError(f"unknown selection_mode: {selection_mode}")
        if not 0 < survivor_fraction <= 1:
            raise ValueError("survivor_fraction must be in (0, 1]")
        rng = random.Random(seed)
        evaluated = tuple(
            member.with_observations(observations.get(member.individual_id, ()))
            for member in self.members
        )
        if not evaluated or self.capacity == 0:
            population = DarwinianPopulation(
                population_id=_population_id(
                    self.generation + 1,
                    self.capacity,
                    (),
                ),
                generation=self.generation + 1,
                capacity=self.capacity,
                members=(),
            )
            return population, EvolutionAudit(
                seed=seed,
                selection_mode=selection_mode,
                selected_survivor_ids=(),
                child_ids=(),
                rejected_offspring=(
                    RejectedOffspring(
                        "selection",
                        (),
                        "empty_population",
                    ),
                ),
            )
        survivor_capacity = max(
            1,
            min(
                self.capacity,
                math.ceil(self.capacity * survivor_fraction),
            ),
        )
        survivors = select_individuals(
            evaluated,
            capacity=survivor_capacity,
            mode=selection_mode,
        )
        rejected: list[RejectedOffspring] = []
        if not survivors:
            rejected.append(
                RejectedOffspring("selection", (), "no_finite_survivors")
            )
        next_members = list(survivors)
        seen_hashes = {item.spec_hash for item in next_members}
        child_ids: list[str] = []
        attempts = 0
        max_attempts = max(20, self.capacity * 40)
        while survivors and len(next_members) < self.capacity and attempts < max_attempts:
            attempts += 1
            parents: tuple[OperatorIndividual, ...]
            if len(survivors) >= 2 and rng.random() < 0.5:
                parents = tuple(rng.sample(list(survivors), 2))
                result = crossover_operators(
                    parents[0].operator,
                    parents[1].operator,
                    rng=rng,
                )
            else:
                parents = (rng.choice(list(survivors)),)
                result = mutate_operator(parents[0].operator, rng=rng)
            parent_ids = tuple(parent.individual_id for parent in parents)
            if not result.accepted:
                rejected.append(
                    RejectedOffspring(
                        result.operation,
                        parent_ids,
                        result.rejection_reason or "invalid_offspring",
                    )
                )
                continue
            assert result.operator is not None
            if not result.operator.steps:
                rejected.append(
                    RejectedOffspring(
                        result.operation,
                        parent_ids,
                        "empty_operator",
                    )
                )
                continue
            spec_hash = result.operator.content_hash()
            if spec_hash in seen_hashes:
                rejected.append(
                    RejectedOffspring(
                        result.operation,
                        parent_ids,
                        "duplicate_genotype",
                    )
                )
                continue
            child_niche = (
                parents[0].niche
                if all(parent.niche == parents[0].niche for parent in parents)
                else None
            )
            child = _make_individual(
                operator=result.operator,
                generation=self.generation + 1,
                parent_ids=parent_ids,
                birth_operation=result.operation,
                niche=child_niche,
            )
            seen_hashes.add(spec_hash)
            next_members.append(child)
            child_ids.append(child.individual_id)
        population = DarwinianPopulation(
            population_id=_population_id(
                self.generation + 1,
                self.capacity,
                tuple(next_members),
            ),
            generation=self.generation + 1,
            capacity=self.capacity,
            members=tuple(next_members),
        )
        return population, EvolutionAudit(
            seed=seed,
            selection_mode=selection_mode,
            selected_survivor_ids=tuple(
                item.individual_id for item in survivors
            ),
            child_ids=tuple(child_ids),
            rejected_offspring=tuple(rejected),
        )


def mutate_operator(
    parent: OperatorSpec,
    *,
    rng: random.Random,
    max_steps: int = 4,
) -> OffspringResult:
    """Choose one valid, distinct typed mutation with a caller-owned RNG."""
    if not parent.steps:
        return OffspringResult(None, "mutation", "empty_parent")
    candidates: dict[str, tuple[str, OperatorSpec]] = {}
    actions_by_gp = parent.action_by_generation_point()

    for generation_point, current_action in sorted(actions_by_gp.items()):
        for action in _MUTABLE_ACTIONS:
            if action == current_action:
                continue
            mutated = dict(actions_by_gp)
            mutated[generation_point] = action
            _add_candidate(
                candidates,
                f"replace_action_gp{generation_point}",
                _operator_from_actions(mutated, parent),
                parent,
            )
    if len(actions_by_gp) < max_steps:
        unused = next(
            gp for gp in range(max_steps) if gp not in actions_by_gp
        )
        for action in _MUTABLE_ACTIONS:
            mutated = dict(actions_by_gp)
            mutated[unused] = action
            _add_candidate(
                candidates,
                f"insert_step_gp{unused}",
                _operator_from_actions(mutated, parent),
                parent,
            )
    if len(actions_by_gp) > 1:
        for generation_point in sorted(actions_by_gp):
            mutated = dict(actions_by_gp)
            del mutated[generation_point]
            _add_candidate(
                candidates,
                f"delete_step_gp{generation_point}",
                _operator_from_actions(mutated, parent),
                parent,
            )
        points = sorted(actions_by_gp)
        for index, left in enumerate(points):
            for right in points[index + 1 :]:
                mutated = dict(actions_by_gp)
                mutated[left], mutated[right] = mutated[right], mutated[left]
                _add_candidate(
                    candidates,
                    f"swap_actions_gp{left}_gp{right}",
                    _operator_from_actions(mutated, parent),
                    parent,
                )
    for memory_id, weight in parent.item_signal_hints:
        for delta in (-0.25, 0.25):
            hints = parent.item_signal_hints_dict()
            hints[memory_id] = max(-2.0, min(2.0, weight + delta))
            child = OperatorSpec(
                steps=parent.steps,
                item_signal_hints=tuple(sorted(hints.items())),
            )
            _add_candidate(
                candidates,
                f"perturb_hint_{memory_id}",
                child,
                parent,
            )
    if not candidates:
        return OffspringResult(None, "mutation", "no_valid_distinct_child")
    ordered = sorted(candidates.items())
    _hash, (operation, child) = ordered[rng.randrange(len(ordered))]
    return OffspringResult(child, operation)


def crossover_operators(
    parent_a: OperatorSpec,
    parent_b: OperatorSpec,
    *,
    rng: random.Random,
) -> OffspringResult:
    """One-point crossover, rejecting empty, invalid, parent-copy children."""
    if not parent_a.steps or not parent_b.steps:
        return OffspringResult(None, "crossover", "empty_parent")
    steps_a = sorted(parent_a.steps, key=lambda item: item.generation_point)
    steps_b = sorted(parent_b.steps, key=lambda item: item.generation_point)
    parent_hashes = {parent_a.content_hash(), parent_b.content_hash()}
    candidates: dict[str, OperatorSpec] = {}
    for cut_a in range(len(steps_a) + 1):
        for cut_b in range(len(steps_b) + 1):
            combined = steps_a[:cut_a] + steps_b[cut_b:]
            if not combined:
                continue
            points = [item.generation_point for item in combined]
            if len(points) != len(set(points)):
                continue
            try:
                child = OperatorSpec.from_actions(
                    (
                        (item.generation_point, item.action)
                        for item in combined
                    ),
                    item_signal_hints=dict(
                        parent_a.item_signal_hints
                        + parent_b.item_signal_hints
                    ),
                )
            except ValueError:
                continue
            child_hash = child.content_hash()
            if child_hash in parent_hashes or not child.steps:
                continue
            candidates.setdefault(child_hash, child)
    if not candidates:
        return OffspringResult(
            None,
            "crossover",
            "no_valid_distinct_child",
        )
    ordered = sorted(candidates.items())
    _hash, child = ordered[rng.randrange(len(ordered))]
    return OffspringResult(child, "crossover")


def select_individuals(
    individuals: Sequence[OperatorIndividual],
    *,
    capacity: int,
    mode: str,
) -> tuple[OperatorIndividual, ...]:
    """Select finite-fitness individuals globally or with niche preservation."""
    if capacity < 0:
        raise ValueError("capacity must be >= 0")
    if mode not in SELECTION_MODES:
        raise ValueError(f"unknown selection mode: {mode}")
    valid = [
        item
        for item in individuals
        if math.isfinite(item.aggregate_fitness)
    ]
    ordered = sorted(valid, key=_fitness_order)
    if mode == "global_truncation":
        return tuple(ordered[:capacity])
    best_by_niche: dict[str, OperatorIndividual] = {}
    for individual in ordered:
        niche = individual.niche or "__unassigned__"
        best_by_niche.setdefault(niche, individual)
    elites = sorted(best_by_niche.values(), key=_fitness_order)
    selected = elites[:capacity]
    selected_ids = {item.individual_id for item in selected}
    for individual in ordered:
        if len(selected) >= capacity:
            break
        if individual.individual_id not in selected_ids:
            selected.append(individual)
            selected_ids.add(individual.individual_id)
    return tuple(selected)


def _operator_from_actions(
    actions_by_gp: Mapping[int, PipelineAction],
    parent: OperatorSpec,
) -> OperatorSpec:
    return OperatorSpec.from_actions(
        sorted(actions_by_gp.items()),
        item_signal_hints=parent.item_signal_hints_dict(),
    )


def _add_candidate(
    candidates: dict[str, tuple[str, OperatorSpec]],
    operation: str,
    child: OperatorSpec,
    parent: OperatorSpec,
) -> None:
    if child.steps and child.content_hash() != parent.content_hash():
        candidates.setdefault(child.content_hash(), (operation, child))


def _make_individual(
    *,
    operator: OperatorSpec,
    generation: int,
    parent_ids: tuple[str, ...],
    birth_operation: str,
    niche: str | None,
) -> OperatorIndividual:
    payload = "|".join(
        (
            operator.content_hash(),
            str(generation),
            ",".join(parent_ids),
            birth_operation,
            niche or "",
        )
    )
    individual_id = "individual-" + hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()
    return OperatorIndividual(
        individual_id=individual_id,
        operator=operator,
        generation=generation,
        parent_ids=parent_ids,
        birth_operation=birth_operation,
        niche=niche,
    )


def _population_id(
    generation: int,
    capacity: int,
    members: Sequence[OperatorIndividual],
) -> str:
    payload = "|".join(
        (
            str(generation),
            str(capacity),
            ",".join(item.individual_id for item in members),
        )
    )
    return "population-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _fitness_order(
    individual: OperatorIndividual,
) -> tuple[float, float, str]:
    return (
        -individual.aggregate_fitness,
        individual.aggregate_cost,
        individual.individual_id,
    )

