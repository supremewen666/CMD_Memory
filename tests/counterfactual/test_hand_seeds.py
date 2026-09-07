"""Frozen hand-written seed population (BUILD SPEC §6.3, §9.1).

The population has two jobs: it supplies §6.3's `best_hand_seed` baseline and
§9.1's initial E3 population. Both break in the same way -- a seed that reads the
hidden intent would make the headroom comparison measure oracle access instead of
grammar reach -- so gold-freeness is the first thing tested here.
"""


import unittest

from cmd_audit.counterfactual import hand_seeds as hand_seeds_module
from cmd_audit.counterfactual.behavior_fingerprint import behavior_fingerprint
from cmd_audit.counterfactual.hand_seeds import (
    HAND_SEED_POPULATION_VERSION,
    HAND_SEEDS,
    HandSeed,
    hand_seed_manifest,
)
from cmd_audit.counterfactual.program_ir import (
    REGISTERED_BOUNDS,
    Sequence,
    canonicalize,
    check_resource_bounds,
)
from cmd_audit.eval.state_intent import FORBIDDEN_RUNTIME_FIELDS


class GoldFreedomTest(unittest.TestCase):
    """§6.3's baseline must be a gold-free program, not the oracle arm."""

    def test_the_module_does_not_import_the_intent_or_gold_surface(self) -> None:
        """The `hand_seed` arm in build_dev_state_intents.py reads
        HiddenStateIntent, which makes it an upper bound rather than a baseline.
        This population must not be able to do the same.

        Checked against the module namespace rather than its source text: the
        docstring names `HiddenStateIntent` in order to explain why it is
        excluded, and a substring scan cannot tell prose from an import.
        """
        for forbidden in ("HiddenStateIntent", "GoldEvidence", "ProbeCase"):
            with self.subTest(symbol=forbidden):
                self.assertFalse(
                    hasattr(hand_seeds_module, forbidden),
                    f"{forbidden} is bound in the hand-seed module namespace",
                )

    def test_no_seed_program_can_reach_a_gold_field(self) -> None:
        """Executable structure only: a seed is predicates and actions, so there
        is no channel through which a gold or intent value could be read."""
        for seed in HAND_SEEDS:
            with self.subTest(seed=seed.name):
                self.assertEqual(
                    set(seed.as_mapping()),
                    {"name", "canonical_ast_hash", "rationale", "program"},
                )

    def test_no_seed_program_serializes_a_forbidden_runtime_field(self) -> None:
        for seed in HAND_SEEDS:
            payload = str(seed.as_mapping())
            for field in FORBIDDEN_RUNTIME_FIELDS:
                with self.subTest(seed=seed.name, field=field):
                    self.assertNotIn(field, payload)

    def test_manifest_declares_intent_independence(self) -> None:
        self.assertFalse(hand_seed_manifest()["reads_hidden_intent"])


class PopulationTest(unittest.TestCase):
    """§9.1 the initial population is frozen and behaviorally deduplicated."""

    def test_population_version_is_frozen(self) -> None:
        self.assertEqual(HAND_SEED_POPULATION_VERSION, "route-a-hand-seeds-v1")

    def test_abstain_preserve_is_a_member_and_is_the_null_program(self) -> None:
        """§9.1 names it explicitly as a population member."""
        by_name = {seed.name: seed for seed in HAND_SEEDS}
        self.assertIn("abstain-preserve", by_name)
        self.assertEqual(by_name["abstain-preserve"].program, Sequence(()))

    def test_every_seed_is_behaviorally_unique(self) -> None:
        """§9.1 admits "every behaviorally unique hand-written seed", so a
        collapsed pair would mean the population double-counts one behavior."""
        fingerprints = [behavior_fingerprint(seed.program) for seed in HAND_SEEDS]
        self.assertEqual(len(set(fingerprints)), len(fingerprints))

    def test_every_seed_has_a_distinct_canonical_ast(self) -> None:
        hashes = [seed.canonical_ast_hash() for seed in HAND_SEEDS]
        self.assertEqual(len(set(hashes)), len(hashes))

    def test_every_seed_is_within_the_registered_bounds(self) -> None:
        for seed in HAND_SEEDS:
            with self.subTest(seed=seed.name):
                check_resource_bounds(
                    canonicalize(seed.program), bounds=REGISTERED_BOUNDS
                )

    def test_every_seed_records_why_it_exists(self) -> None:
        """A seed without a stated reason cannot be audited for tuning."""
        for seed in HAND_SEEDS:
            with self.subTest(seed=seed.name):
                self.assertTrue(seed.rationale.strip())

    def test_the_population_contains_an_order_reversed_pair(self) -> None:
        """Composition order is a real dimension of the space, so the population
        must contain both orders of at least one pair; otherwise an order effect
        cannot be observed and is silently assumed absent."""
        names = {seed.name for seed in HAND_SEEDS}
        self.assertIn("fill-then-deconflict", names)
        self.assertIn("deconflict-then-fill", names)
        by_name = {seed.name: seed for seed in HAND_SEEDS}
        self.assertNotEqual(
            by_name["fill-then-deconflict"].canonical_ast_hash(),
            by_name["deconflict-then-fill"].canonical_ast_hash(),
        )

    def test_the_audit_rejects_a_seed_outside_the_registered_bounds(self) -> None:
        """§6.1's audit is the reason a bad seed cannot silently shrink §6.3's
        baseline, so it has to actually reject one."""
        from cmd_audit.counterfactual.hand_seeds import audit_seed_population
        from cmd_audit.counterfactual.program_ir import (
            Action,
            ActionKind,
            If,
            Predicate,
            PredicateKind,
            ProgramBoundsError,
            Sequence,
        )

        too_many_actions = Sequence(
            tuple(
                If(
                    predicate=Predicate(
                        kind=PredicateKind.SIMILARITY_ABOVE, threshold=value
                    ),
                    action=Action(ActionKind.DEMOTE),
                )
                for value in (0.25, 0.5, 0.75, 0.25, 0.5)
            )
        )
        with self.assertRaises(ProgramBoundsError):
            audit_seed_population(
                (HandSeed(name="oversized", program=too_many_actions, rationale="x"),)
            )

    def test_the_audit_rejects_a_duplicated_seed(self) -> None:
        """Two seeds with one AST would make the population double-count."""
        from cmd_audit.counterfactual.hand_seeds import audit_seed_population

        seed = HAND_SEEDS[1]
        with self.assertRaises(ValueError):
            audit_seed_population((seed, HandSeed(name="copy", program=seed.program, rationale="x")))

    def test_manifest_runs_the_audit_rather_than_only_defining_it(self) -> None:
        """An audit the manifest never calls protects nothing, and the frozen
        population passes either way, so the wiring needs its own test."""
        from unittest import mock

        seed = HAND_SEEDS[1]
        bad = (seed, HandSeed(name="copy", program=seed.program, rationale="x"))
        with mock.patch.object(hand_seeds_module, "HAND_SEEDS", bad):
            with self.assertRaises(ValueError):
                hand_seed_manifest()

    def test_manifest_lists_every_seed(self) -> None:
        manifest = hand_seed_manifest()
        self.assertEqual(manifest["seed_count"], len(HAND_SEEDS))
        self.assertEqual(len(manifest["seeds"]), len(HAND_SEEDS))

    def test_manifest_is_json_serializable_without_nan(self) -> None:
        import json

        json.dumps(hand_seed_manifest(), allow_nan=False, sort_keys=True)


if __name__ == "__main__":
    unittest.main()
