"""The §15 execution chain, as a gate (BUILD SPEC §12.3).

§12.3 asks `run_remaining_experiments.sh` to "add gated Route A commands only
after their predecessor artifacts pass". Shell is not testable here, so the
decision lives in Python and the script calls it.

The load-bearing property is that the chain is *cumulative*: a later gate must
refuse when an earlier one refused, even when its own inputs look fine. E0
returned STOP, so the E3 gate has to refuse regardless of E0b being complete and
its envelope frozen -- and E3 is the stage that spends 3x150 proposals. A gate
that only checked its own predecessor artifact would permit it.
"""

import unittest

from experiments.check_route_a_gates import (
    STAGES,
    ChainState,
    evaluate_chain,
    gate_for_stage,
)


def _state(**overrides) -> ChainState:
    """A chain state where everything passes, so each test varies one thing."""
    defaults = dict(
        e_minus_1_tests_pass=True,
        e0_gate_decision="GO",
        bridge_decision_recorded=True,
        tier3_frozen=True,
        e0b_complete=True,
        presearch_envelope_frozen=True,
        e3_seed_winner_count=3,
        artifact_freeze_manifest_valid=True,
        e5_runs_recorded=0,
    )
    defaults.update(overrides)
    return ChainState(**defaults)


class HappyPathTest(unittest.TestCase):
    def test_a_fully_passing_chain_permits_every_stage(self) -> None:
        gates = evaluate_chain(_state())
        self.assertTrue(all(gate.permits for gate in gates), gates)

    def test_the_chain_covers_every_named_stage(self) -> None:
        """A stage with no gate is an ungated command, which is the thing §12.3
        is asking to prevent."""
        gated = {stage for gate in evaluate_chain(_state()) for stage in gate.stages}
        self.assertEqual(gated, set(STAGES))


class FirstGateTest(unittest.TestCase):
    def test_failing_e_minus_1_tests_refuse_e0_and_the_bridge(self) -> None:
        gates = evaluate_chain(_state(e_minus_1_tests_pass=False))
        self.assertFalse(gate_for_stage(gates, "e0").permits)
        self.assertFalse(gate_for_stage(gates, "bridge").permits)

    def test_failing_e_minus_1_tests_refuse_everything_downstream(self) -> None:
        """The frozen evaluator is the basis of every later number, so a red
        E-1 suite cannot leave a later stage permitted."""
        gates = evaluate_chain(_state(e_minus_1_tests_pass=False))
        self.assertTrue(all(not gate.permits for gate in gates), gates)


class CumulativeRefusalTest(unittest.TestCase):
    """The property that makes this a chain rather than four independent checks."""

    def test_an_e0_stop_refuses_e3_even_when_e0b_is_complete(self) -> None:
        """This is the live situation: E0 returned STOP, E0b ran and froze its
        envelope. E3 must still be refused -- it is the stage that spends the
        proposal budget."""
        gates = evaluate_chain(
            _state(
                e0_gate_decision="STOP",
                e0b_complete=True,
                presearch_envelope_frozen=True,
            )
        )
        self.assertFalse(gate_for_stage(gates, "e3").permits)

    def test_an_e0_stop_refuses_e4_and_e5_even_with_three_winners(self) -> None:
        """Seed winners on disk from an earlier attempt must not re-open the
        route: §16 forbids a post-failure rescue."""
        gates = evaluate_chain(
            _state(
                e0_gate_decision="STOP",
                e3_seed_winner_count=3,
                artifact_freeze_manifest_valid=True,
            )
        )
        self.assertFalse(gate_for_stage(gates, "e4").permits)
        self.assertFalse(gate_for_stage(gates, "e5").permits)

    def test_a_refused_gate_names_the_upstream_stage_not_its_own_inputs(self) -> None:
        """Reporting "envelope not frozen" when the envelope *is* frozen would
        send a reader to fix the wrong thing."""
        gates = evaluate_chain(_state(e0_gate_decision="STOP"))
        reason = gate_for_stage(gates, "e3").reason
        self.assertIn("e2_and_e0b", reason)

    def test_a_missing_bridge_decision_refuses_e0b(self) -> None:
        gates = evaluate_chain(_state(bridge_decision_recorded=False))
        self.assertFalse(gate_for_stage(gates, "e0b").permits)

    def test_an_unfrozen_tier3_dataset_refuses_e0b(self) -> None:
        gates = evaluate_chain(_state(tier3_frozen=False))
        self.assertFalse(gate_for_stage(gates, "e0b").permits)


class ThirdGateTest(unittest.TestCase):
    def test_an_incomplete_e0b_refuses_e3(self) -> None:
        gates = evaluate_chain(_state(e0b_complete=False))
        self.assertFalse(gate_for_stage(gates, "e3").permits)

    def test_an_unfrozen_envelope_refuses_e3(self) -> None:
        """§6.4: E3 novelty is measured against the pre-search envelope, so an
        envelope that can still change would let the novelty claim be tuned."""
        gates = evaluate_chain(_state(presearch_envelope_frozen=False))
        self.assertFalse(gate_for_stage(gates, "e3").permits)


class FourthGateTest(unittest.TestCase):
    def test_two_seed_winners_refuse_e4(self) -> None:
        """§16 fixes E3 at exactly three seeds; selecting from two would change
        the selection's reference set."""
        gates = evaluate_chain(_state(e3_seed_winner_count=2))
        self.assertFalse(gate_for_stage(gates, "e4").permits)

    def test_four_seed_winners_refuse_e4(self) -> None:
        """More than three is as much a protocol deviation as fewer: it means a
        seed was re-run after seeing a result."""
        gates = evaluate_chain(_state(e3_seed_winner_count=4))
        self.assertFalse(gate_for_stage(gates, "e4").permits)


class FifthGateTest(unittest.TestCase):
    def test_an_invalid_freeze_manifest_refuses_e5(self) -> None:
        gates = evaluate_chain(_state(artifact_freeze_manifest_valid=False))
        self.assertFalse(gate_for_stage(gates, "e5").permits)

    def test_a_second_confirmation_read_is_refused(self) -> None:
        """§16: exactly one confirmation read. The gate is the only thing that
        can enforce it, because the command itself has no memory of prior runs.
        """
        gates = evaluate_chain(_state(e5_runs_recorded=1))
        self.assertFalse(gate_for_stage(gates, "e5").permits)

    def test_the_second_read_refusal_says_it_was_already_read(self) -> None:
        gates = evaluate_chain(_state(e5_runs_recorded=1))
        self.assertIn("already", gate_for_stage(gates, "e5").reason.lower())


class StageLookupTest(unittest.TestCase):
    def test_an_unknown_stage_is_an_error_not_a_permit(self) -> None:
        """A typo'd `--stage` must not exit 0 and let an ungated command run."""
        with self.assertRaises(KeyError):
            gate_for_stage(evaluate_chain(_state()), "e9")


if __name__ == "__main__":
    unittest.main()
