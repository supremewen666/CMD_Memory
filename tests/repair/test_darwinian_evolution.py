from __future__ import annotations

import math
import random

from cmd_audit.counterfactual.actions import PipelineAction
from cmd_audit.counterfactual.operators import OperatorSpec
from cmd_audit.repair.darwinian import (
    DarwinianPopulation,
    FitnessObservation,
    crossover_operators,
    mutate_operator,
    select_individuals,
)


def _spec(action: PipelineAction) -> OperatorSpec:
    return OperatorSpec.single(0, action)


def _observed(individual, gain, niche):
    return individual.with_observations(
        (
            FitnessObservation(
                case_id=f"case-{individual.individual_id[-4:]}",
                recovery_gain=gain,
                execution_cost=1.0,
                niche=niche,
            ),
        )
    )


def test_empty_population_is_a_reproducible_observable_state():
    population = DarwinianPopulation.from_specs((), capacity=4)
    evolved, audit = population.evolve({}, seed=7)
    assert evolved.members == ()
    assert evolved.generation == 1
    assert audit.rejected_offspring[0].reason == "empty_population"


def test_mutation_is_seeded_valid_and_parent_immutable():
    parent = _spec(PipelineAction.RETRIEVAL_ERROR)
    parent_hash = parent.content_hash()
    left = mutate_operator(parent, rng=random.Random(11))
    right = mutate_operator(parent, rng=random.Random(11))
    assert left.accepted and right.accepted
    assert left.operator is not None and right.operator is not None
    assert left.operator.content_hash() == right.operator.content_hash()
    assert left.operator.steps
    assert left.operator.content_hash() != parent_hash
    assert parent.content_hash() == parent_hash


def test_invalid_or_parent_copy_crossover_is_rejected():
    left = _spec(PipelineAction.RETRIEVAL_ERROR)
    right = _spec(PipelineAction.INJECTION_ERROR)
    result = crossover_operators(left, right, rng=random.Random(3))
    assert not result.accepted
    assert result.rejection_reason == "no_valid_distinct_child"


def test_global_and_niche_selection_exclude_nonfinite_fitness():
    specs = (
        _spec(PipelineAction.RETRIEVAL_ERROR),
        _spec(PipelineAction.INJECTION_ERROR),
        _spec(PipelineAction.GRANULARITY_ERROR),
    )
    population = DarwinianPopulation.from_specs(specs, capacity=3)
    a, b, c = population.members
    observed = (
        _observed(a, 0.9, "retrieval"),
        _observed(b, 0.8, "retrieval"),
        _observed(c, math.nan, "injection"),
    )
    global_selected = select_individuals(
        observed,
        capacity=2,
        mode="global_truncation",
    )
    assert [item.individual_id for item in global_selected] == [
        a.individual_id,
        b.individual_id,
    ]

    c_finite = _observed(c, 0.2, "injection")
    niche_selected = select_individuals(
        (observed[0], observed[1], c_finite),
        capacity=2,
        mode="niche_elite",
    )
    assert {item.niche for item in niche_selected} == {
        "retrieval",
        "injection",
    }


def test_population_evolution_same_seed_reproduces_genealogy_and_children():
    specs = (
        _spec(PipelineAction.RETRIEVAL_ERROR),
        _spec(PipelineAction.INJECTION_ERROR),
    )
    population = DarwinianPopulation.from_specs(specs, capacity=5)
    observations = {
        member.individual_id: (
            FitnessObservation(
                case_id="case-1",
                recovery_gain=0.4,
                execution_cost=1.0,
                niche="structural",
            ),
        )
        for member in population.members
    }
    original = tuple(
        (item.individual_id, item.operator.content_hash(), item.observations)
        for item in population.members
    )
    left, left_audit = population.evolve(
        observations,
        seed=99,
        selection_mode="global_truncation",
    )
    right, right_audit = population.evolve(
        observations,
        seed=99,
        selection_mode="global_truncation",
    )
    assert left == right
    assert left_audit == right_audit
    assert left_audit.child_ids
    assert len({item.spec_hash for item in left.members}) == len(left.members)
    assert original == tuple(
        (item.individual_id, item.operator.content_hash(), item.observations)
        for item in population.members
    )
    children = [item for item in left.members if item.parent_ids]
    assert children
    assert all(item.generation == 1 for item in children)


def test_population_evolves_multiple_generations_with_valid_genealogy():
    population = DarwinianPopulation.from_specs(
        (
            _spec(PipelineAction.RETRIEVAL_ERROR),
            _spec(PipelineAction.INJECTION_ERROR),
            _spec(PipelineAction.GRANULARITY_ERROR),
        ),
        capacity=6,
    )
    known_ids = {member.individual_id for member in population.members}
    for generation in range(1, 4):
        observations = {
            member.individual_id: (
                FitnessObservation(
                    case_id=f"g{generation}-{index}",
                    recovery_gain=0.2 + index / 100,
                    execution_cost=1.0,
                    niche="structural",
                ),
            )
            for index, member in enumerate(population.members)
        }
        population, audit = population.evolve(
            observations,
            seed=100 + generation,
        )
        assert population.generation == generation
        assert len(population.members) == population.capacity
        assert len({member.spec_hash for member in population.members}) == len(
            population.members
        )
        for member in population.members:
            assert set(member.parent_ids) <= known_ids
        known_ids.update(member.individual_id for member in population.members)
        assert set(audit.child_ids) <= known_ids
