"""Route A §15: the execution chain, enforced (BUILD SPEC §12.3).

§12.3 asks `run_remaining_experiments.sh` to add Route A commands "only after
their predecessor artifacts pass". The decision is here rather than in shell for
two reasons: it is testable, and it reads the artifacts themselves rather than
trusting an operator to have run the stages in order.

The chain is **cumulative**. §15's five gates are written as a ladder, and a
later gate refuses whenever an earlier one refused, even when its own inputs look
fine. That is not a stylistic choice -- it is the only version that holds in the
live situation. E0 returned STOP; E0b then ran (a registered *measurement* of the
pre-search envelope, not a search) and froze its envelope. A gate that checked
only "E0b complete and envelope frozen" would permit E3, which is the stage that
spends §16's 3x150 proposal budget. So `e0_gate_decision == "GO"` keeps binding
all the way down.

Two rules are counted rather than checked for existence, because "the file is
there" is the wrong question:

  * E3 seed winners must be *exactly* three (§16 fixes the seed count). Fewer
    means an incomplete search; more means a seed was re-run after seeing a
    result.
  * E5 must have *zero* prior recorded runs. §16 allows exactly one confirmation
    read, and the confirmation command has no memory of earlier invocations, so
    this gate is the only thing that can enforce it.

Zero LLM calls: every input is a file on disk.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROUTE_A = Path("artifacts/route_a")

#: §13's artifact contract, root-relative. These are the paths the gate globs and
#: the paths the commands write, so they are named once rather than spelled at
#: each end. A gate reading a path no command writes globs an empty directory
#: forever: every stage is refused for a reason naming a missing artifact, which
#: is indistinguishable from a genuine refusal and would survive any test that
#: only asserts "refused".
E0_COUNT_ARTIFACT = "e0/closed_spec_count.json"
E0_CROSSFIT_ARTIFACT = "e0/closed_crossfit_results.json"
BRIDGE_ARTIFACT = "bridge/bridge_decision.json"
TIER3_BUILD_ARTIFACT = "tier3/tier3_build_manifest.json"
PRESEARCH_ENVELOPE_ARTIFACT = "e0b/presearch_envelope_manifest.json"
FREEZE_ARTIFACT = "selection/artifact_freeze_manifest.json"

#: §13 nests each seed's winner under `synthesis/seed_<seed>/`. The glob matches
#: the winner file, not the directory: a seed that ran and aborted leaves a
#: proposal ledger behind, and counting directories would let it satisfy §10.1's
#: "exactly three seed winners enter D_select".
E3_WINNER_GLOB = "synthesis/seed_*/winner.json"

#: §11.2 writes the confirmation's mechanical decision here. Its presence is how
#: a prior read becomes visible to §16's "confirmation reads = exactly 1".
FINAL_DECISION_ARTIFACT = "tier3/final_decision.json"

#: §16 fixes E3 at three seeds.
REQUIRED_E3_SEEDS = 3

#: §16: exactly one confirmation read, so zero may have happened before it.
MAX_PRIOR_E5_RUNS = 0

#: Every stage a Route A command belongs to. A stage absent from this tuple would
#: be an ungated command, which is what §12.3 asks to prevent.
STAGES = ("e0", "bridge", "e2_and_e0b", "e0b", "e3", "e4", "e5")


@dataclass(frozen=True)
class ChainState:
    """What the artifacts on disk say about each §15 precondition."""

    e_minus_1_tests_pass: bool
    e0_gate_decision: str
    bridge_decision_recorded: bool
    tier3_frozen: bool
    e0b_complete: bool
    presearch_envelope_frozen: bool
    e3_seed_winner_count: int
    artifact_freeze_manifest_valid: bool
    e5_runs_recorded: int


@dataclass(frozen=True)
class Gate:
    """One §15 rung: the stages it permits, and why it did or did not."""

    name: str
    stages: tuple[str, ...]
    permits: bool
    reason: str


def evaluate_chain(state: ChainState) -> tuple[Gate, ...]:
    """§15's ladder. Each rung requires the one above it to have permitted."""
    gates: list[Gate] = []

    tests_ok = state.e_minus_1_tests_pass
    gates.append(
        Gate(
            name="e_minus_1",
            stages=("e0", "bridge"),
            permits=tests_ok,
            reason=(
                "E-1 tests pass"
                if tests_ok
                else "E-1 tests do not pass; the frozen evaluator is the basis of "
                "every later number"
            ),
        )
    )

    e0_go = state.e0_gate_decision == "GO"
    e0b_ok = tests_ok and e0_go and state.bridge_decision_recorded and state.tier3_frozen
    gates.append(
        Gate(
            name="e2_and_e0b",
            stages=("e2_and_e0b", "e0b"),
            permits=e0b_ok,
            reason=_first_reason(
                [
                    (not tests_ok, "blocked upstream: e_minus_1 refused"),
                    (
                        not e0_go,
                        "E0 gate decision is "
                        f"{state.e0_gate_decision!r}, not 'GO' (§6.3 stops the "
                        "route)",
                    ),
                    (
                        not state.bridge_decision_recorded,
                        "no bridge decision recorded (§7)",
                    ),
                    (not state.tier3_frozen, "tier-3 dataset is not frozen (§5)"),
                ],
                "E0 GO, bridge decision recorded, tier-3 frozen",
            ),
        )
    )

    e3_ok = e0b_ok and state.e0b_complete and state.presearch_envelope_frozen
    gates.append(
        Gate(
            name="e3",
            stages=("e3",),
            permits=e3_ok,
            reason=_first_reason(
                [
                    (not e0b_ok, "blocked upstream: e2_and_e0b refused"),
                    (not state.e0b_complete, "E0b is not complete (§6.4)"),
                    (
                        not state.presearch_envelope_frozen,
                        "pre-search envelope is not frozen (§6.4)",
                    ),
                ],
                "E0b complete and pre-search envelope frozen",
            ),
        )
    )

    winners_ok = state.e3_seed_winner_count == REQUIRED_E3_SEEDS
    e4_ok = e3_ok and winners_ok
    gates.append(
        Gate(
            name="e4",
            stages=("e4",),
            permits=e4_ok,
            reason=_first_reason(
                [
                    (not e3_ok, "blocked upstream: e3 refused"),
                    (
                        not winners_ok,
                        f"{state.e3_seed_winner_count} E3 seed winners, "
                        f"expected exactly {REQUIRED_E3_SEEDS} (§16)",
                    ),
                ],
                f"{REQUIRED_E3_SEEDS} E3 seed winners exist",
            ),
        )
    )

    unread = state.e5_runs_recorded <= MAX_PRIOR_E5_RUNS
    e5_ok = e4_ok and state.artifact_freeze_manifest_valid and unread
    gates.append(
        Gate(
            name="e5",
            stages=("e5",),
            permits=e5_ok,
            reason=_first_reason(
                [
                    (not e4_ok, "blocked upstream: e4 refused"),
                    (
                        not state.artifact_freeze_manifest_valid,
                        "artifact freeze manifest is not valid (§10)",
                    ),
                    (
                        not unread,
                        f"the confirmation set was already read "
                        f"{state.e5_runs_recorded} time(s); §16 allows exactly one",
                    ),
                ],
                "freeze manifest valid and the confirmation set is unread",
            ),
        )
    )

    return tuple(gates)


def gate_for_stage(gates: tuple[Gate, ...], stage: str) -> Gate:
    """The gate governing one stage.

    Raises on an unknown stage rather than returning a default, so a typo in
    `--stage` cannot exit 0 and let an ungated command run.
    """
    for gate in gates:
        if stage in gate.stages:
            return gate
    raise KeyError(f"no gate governs stage {stage!r}; known stages: {STAGES}")


def _first_reason(conditions: list[tuple[bool, str]], passing: str) -> str:
    for failed, reason in conditions:
        if failed:
            return reason
    return passing


def _load(path: Path) -> dict | None:
    if not path.is_file() or path.stat().st_size == 0:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def read_chain_state(root: Path = ROUTE_A, *, tests_pass: bool) -> ChainState:
    """Build a `ChainState` from the artifacts under `root`.

    `tests_pass` is passed in rather than discovered: this command must not
    shell out to pytest, and the caller (the runner script) has just run it.
    """
    e0 = _load(root / E0_COUNT_ARTIFACT) or {}
    crossfit = _load(root / E0_CROSSFIT_ARTIFACT) or {}
    decision = str(
        e0.get("gate_decision") or crossfit.get("gate", {}).get("decision") or "MISSING"
    )

    bridge = _load(root / BRIDGE_ARTIFACT) or {}
    tier3 = _load(root / TIER3_BUILD_ARTIFACT) or {}
    envelope = _load(root / PRESEARCH_ENVELOPE_ARTIFACT) or {}
    freeze = _load(root / FREEZE_ARTIFACT) or {}

    e3_winners = sorted(root.glob(E3_WINNER_GLOB))
    e5_runs = [path for path in (root / FINAL_DECISION_ARTIFACT,) if path.is_file()]

    return ChainState(
        e_minus_1_tests_pass=tests_pass,
        e0_gate_decision=decision,
        bridge_decision_recorded=bool(bridge.get("decision")),
        tier3_frozen=tier3.get("status") == "frozen",
        e0b_complete=bool(envelope.get("union_behavior_class_count")),
        presearch_envelope_frozen=envelope.get("status") == "frozen",
        e3_seed_winner_count=len(e3_winners),
        artifact_freeze_manifest_valid=freeze.get("status") == "frozen",
        e5_runs_recorded=len(e5_runs),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Route A §15 gate chain. Exits 0 when the named stage is permitted "
            "by every rung above it, 1 otherwise. The chain is cumulative: an "
            "E0 STOP refuses E3, E4 and E5 regardless of their own inputs."
        )
    )
    parser.add_argument("--stage", required=True, choices=sorted(STAGES))
    parser.add_argument("--root", type=Path, default=ROUTE_A)
    parser.add_argument(
        "--tests-pass",
        action="store_true",
        help="assert the E-1 suite passed; the caller runs it",
    )
    args = parser.parse_args()

    state = read_chain_state(args.root, tests_pass=args.tests_pass)
    gates = evaluate_chain(state)
    gate = gate_for_stage(gates, args.stage)

    verdict = "PERMIT" if gate.permits else "REFUSE"
    print(f"[route-a gate] {args.stage}: {verdict} ({gate.reason})")
    if not gate.permits:
        for other in gates:
            print(
                f"  {other.name:12} "
                f"{'permit' if other.permits else 'refuse'}: {other.reason}"
            )
    return 0 if gate.permits else 1


if __name__ == "__main__":
    raise SystemExit(main())
