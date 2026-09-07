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

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from experiments.check_route_a_gates import (
    BRIDGE_ARTIFACT,
    FINAL_DECISION_ARTIFACT,
    FREEZE_ARTIFACT,
    PRESEARCH_ENVELOPE_ARTIFACT,
    STAGES,
    TIER3_BUILD_ARTIFACT,
    ChainState,
    evaluate_chain,
    gate_for_stage,
    read_chain_state,
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


class ChainStateFromDiskTest(unittest.TestCase):
    """`read_chain_state` against §13's artifact contract.

    The paths are the load-bearing part and nothing else pins them. A gate that
    globs a path §13 does not define reads an empty directory forever: the state
    it builds is all-negative, so every stage is refused for a reason that names
    a missing artifact rather than the real one, and no test would notice because
    refusing is also the correct answer today. The failure mode is a gate that
    can never permit E3 even once E0 returns GO -- and its symptom is
    indistinguishable from the E0 STOP that is currently refusing.

    So each precondition is written at the §13 path and read back. A rename on
    either side breaks these.
    """

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)

    def _write(self, relative: str, payload: dict) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_an_empty_root_reports_every_precondition_unmet(self) -> None:
        state = read_chain_state(self.root, tests_pass=True)
        self.assertEqual(state.e0_gate_decision, "MISSING")
        self.assertFalse(state.bridge_decision_recorded)
        self.assertFalse(state.tier3_frozen)
        self.assertFalse(state.presearch_envelope_frozen)
        self.assertEqual(state.e3_seed_winner_count, 0)
        self.assertFalse(state.artifact_freeze_manifest_valid)
        self.assertEqual(state.e5_runs_recorded, 0)

    def test_tests_pass_is_supplied_by_the_caller_not_discovered(self) -> None:
        """The command must not shell out to pytest, so this is an input."""
        self.assertFalse(read_chain_state(self.root, tests_pass=False).e_minus_1_tests_pass)
        self.assertTrue(read_chain_state(self.root, tests_pass=True).e_minus_1_tests_pass)

    def test_the_e0_decision_is_read_from_the_spec_count_artifact(self) -> None:
        self._write("e0/closed_spec_count.json", {"gate_decision": "GO"})
        self.assertEqual(read_chain_state(self.root, tests_pass=True).e0_gate_decision, "GO")

    def test_the_e0_decision_falls_back_to_the_crossfit_artifact(self) -> None:
        """E0 records its decision in two places; either one is authoritative."""
        self._write("e0/closed_crossfit_results.json", {"gate": {"decision": "STOP"}})
        self.assertEqual(read_chain_state(self.root, tests_pass=True).e0_gate_decision, "STOP")

    def test_the_bridge_decision_is_read_from_the_contract_path(self) -> None:
        self._write(BRIDGE_ARTIFACT, {"decision": "PASS"})
        self.assertTrue(read_chain_state(self.root, tests_pass=True).bridge_decision_recorded)

    def test_a_bridge_artifact_without_a_decision_does_not_count(self) -> None:
        """An insufficient-support run writes no decision. §7.4 says that is not
        a bridge failure, but it is also not a recorded decision, and §15's rung
        asks for one."""
        self._write(BRIDGE_ARTIFACT, {"support": "insufficient"})
        self.assertFalse(read_chain_state(self.root, tests_pass=True).bridge_decision_recorded)

    def test_the_tier3_commitment_is_read_from_the_build_manifest(self) -> None:
        self._write(TIER3_BUILD_ARTIFACT, {"status": "frozen"})
        self.assertTrue(read_chain_state(self.root, tests_pass=True).tier3_frozen)

    def test_an_unfrozen_tier3_manifest_does_not_count(self) -> None:
        self._write(TIER3_BUILD_ARTIFACT, {"status": "draft"})
        self.assertFalse(read_chain_state(self.root, tests_pass=True).tier3_frozen)

    def test_the_presearch_envelope_status_is_read_from_the_e0b_manifest(self) -> None:
        self._write(
            PRESEARCH_ENVELOPE_ARTIFACT,
            {"status": "frozen", "union_behavior_class_count": 12},
        )
        state = read_chain_state(self.root, tests_pass=True)
        self.assertTrue(state.presearch_envelope_frozen)
        self.assertTrue(state.e0b_complete)

    def test_a_pending_envelope_is_neither_frozen_nor_complete(self) -> None:
        """E0b writes `pending_closed_grammar` with a null union when E0's
        fingerprints are absent. A union it did not compute must not read as a
        frozen baseline that E5 will later compare against."""
        self._write(
            PRESEARCH_ENVELOPE_ARTIFACT,
            {"status": "pending_closed_grammar", "union_behavior_class_count": None},
        )
        state = read_chain_state(self.root, tests_pass=True)
        self.assertFalse(state.presearch_envelope_frozen)
        self.assertFalse(state.e0b_complete)

    def test_seed_winners_are_counted_at_the_contract_path(self) -> None:
        """§13 puts each seed's winner at `synthesis/seed_<seed>/winner.json`."""
        for seed in (11, 22, 33):
            self._write(f"synthesis/seed_{seed}/winner.json", {"seed": seed})
        self.assertEqual(read_chain_state(self.root, tests_pass=True).e3_seed_winner_count, 3)

    def test_a_proposal_ledger_without_a_winner_is_not_counted(self) -> None:
        """A seed that ran and produced no winner has not produced one. Counting
        the directory instead of the winner would let an aborted seed satisfy
        §10.1's "exactly three seed winners enter D_select"."""
        for seed in (11, 22):
            self._write(f"synthesis/seed_{seed}/winner.json", {"seed": seed})
        ledger = self.root / "synthesis/seed_33/proposal_ledger.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text("{}\n", encoding="utf-8")
        self.assertEqual(read_chain_state(self.root, tests_pass=True).e3_seed_winner_count, 2)

    def test_the_freeze_manifest_is_read_from_the_selection_directory(self) -> None:
        self._write(FREEZE_ARTIFACT, {"status": "frozen"})
        self.assertTrue(read_chain_state(self.root, tests_pass=True).artifact_freeze_manifest_valid)

    def test_confirmation_reads_are_counted_from_the_tier3_decision(self) -> None:
        """§16 allows exactly one, so a prior one has to be visible. §13 puts the
        confirmation's own output at `tier3/final_decision.json`."""
        self._write(FINAL_DECISION_ARTIFACT, {"decision": "NOT_CONFIRMED"})
        self.assertEqual(read_chain_state(self.root, tests_pass=True).e5_runs_recorded, 1)

    def test_a_disk_state_at_every_contract_path_permits_e5(self) -> None:
        """The paths as a set. Each test above pins one; if any of them read a
        path the writers do not use, the chain can never reach the last rung, and
        this is the test that would catch it.
        """
        self._write("e0/closed_spec_count.json", {"gate_decision": "GO"})
        self._write(BRIDGE_ARTIFACT, {"decision": "PASS"})
        self._write(TIER3_BUILD_ARTIFACT, {"status": "frozen"})
        self._write(
            PRESEARCH_ENVELOPE_ARTIFACT,
            {"status": "frozen", "union_behavior_class_count": 12},
        )
        for seed in (11, 22, 33):
            self._write(f"synthesis/seed_{seed}/winner.json", {"seed": seed})
        self._write(FREEZE_ARTIFACT, {"status": "frozen"})

        gates = evaluate_chain(read_chain_state(self.root, tests_pass=True))
        for stage in STAGES:
            self.assertTrue(
                gate_for_stage(gates, stage).permits,
                f"{stage}: {gate_for_stage(gates, stage).reason}",
            )


if __name__ == "__main__":
    unittest.main()
