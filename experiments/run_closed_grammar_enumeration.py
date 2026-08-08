#!/usr/bin/env python3
"""Route A E0: closed-grammar enumeration, execution matrix, cross-fitted headroom.

E0 asks whether the operators the live system already shipped can reach Route A's
target on their own. If they can, open synthesis has nothing to add and §6.3
stops the route before any search budget is spent -- so this command is a gate,
not a warm-up, and it is designed to be able to fail.

The headroom is measured the way §6.3 specifies, which matters more than the
number it produces:

    1. family-grouped 5-fold split (siblings never cross a fold)
    2. pick the best closed spec on 4 folds
    3. evaluate that frozen pick on the held-out 5th
    4. compare against the frozen best hand seed
    5. aggregate family-paired differences

Selecting and evaluating on the same cases would report the maximum of 22 noisy
draws as if it were one spec's effect. The globally-best-on-all-dev number is
still computed, but §6.3 requires it be reported descriptively, so it is written
under a separate key and never feeds the gate.

D_select is excluded throughout (§2.1). D_search is also excluded: E0 is a
development-triage stage, and spending D_search here would burn data E4 needs.

Writes the six §13 `e0/` artifacts. Exits nonzero when the §6.3 gate fails,
because a failed gate stops Route A rather than being a warning.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.models import ProbeCase
from cmd_audit.counterfactual.behavior_fingerprint import (
    PROBE_SUITE_VERSION,
    behavior_fingerprint,
    probe_suite_sha256,
)
from cmd_audit.counterfactual.closed_grammar import (
    CLOSED_GRAMMAR_VERSION,
    CLOSED_MAX_SEQUENCE_LENGTH,
    ClosedGrammarSpec,
    canonical_closed_specs,
    closed_grammar_manifest,
    count_canonical_closed_grammar,
    count_closed_grammar,
)
from cmd_audit.counterfactual.hand_seeds import (
    HAND_SEED_POPULATION_VERSION,
    HAND_SEEDS,
    HandSeed,
    hand_seed_manifest,
)
from cmd_audit.counterfactual.program_ir import (
    IR_GRAMMAR_VERSION,
    IdentityActionError,
    ProgramBoundsError,
    REGISTERED_BOUNDS,
    Program,
    canonical_ast_hash,
)
from cmd_audit.counterfactual.repair_state import initial_state_from_runtime_case
from cmd_audit.counterfactual.state_executor import (
    ExecutionLimitError,
    execute_program,
)
from cmd_audit.eval.dev_state_intents import build_dev_intents
from cmd_audit.eval.route_a_statistics import (
    assign_split_tier,
    crossfit_family_folds,
    family_blocked_lower_bound,
    family_paired_differences,
    sign_flip_p_value,
)
from cmd_audit.eval.state_fitness import STATE_FITNESS_VERSION, evaluate_state
from cmd_audit.eval.state_intent import (
    STATE_INTENT_SCHEMA_VERSION,
    runtime_case_from_probe_case,
)

OUTPUT_DIR = Path("artifacts/route_a/e0")
PROTOCOL_VERSION = "route-a-state-fitness-open-synthesis-v1"
DEFAULT_DOMAINS = ("memtrace_kp", "stale_item", "memfail")
DEFAULT_TOKEN_BUDGET = 100000

#: §6.3's development continuation gate.
GATE_MIN_ESTIMATE = 0.05

#: Fixed before the run. §6.3 does not name a fold seed, so it is frozen here
#: and published in the manifest; changing it after reading a result would be
#: selection on the fold assignment.
CROSSFIT_SEED = 20260808
BOOTSTRAP_SEED = 20260808


def code_revision() -> str:
    """Digest of the modules that define the grammar and the endpoint."""
    digest = hashlib.sha256()
    for name in (
        "cmd_audit/counterfactual/closed_grammar.py",
        "cmd_audit/counterfactual/hand_seeds.py",
        "cmd_audit/counterfactual/program_ir.py",
        "cmd_audit/counterfactual/state_executor.py",
        "cmd_audit/eval/state_fitness.py",
        "cmd_audit/eval/route_a_statistics.py",
    ):
        digest.update(Path(name).read_bytes())
    return digest.hexdigest()


def load_cases(domain: str) -> tuple[tuple[ProbeCase, ...], str]:
    path = Path(f"data/probe_cases/{domain}_cases.json")
    raw_bytes = path.read_bytes()
    payload = json.loads(raw_bytes)
    rows = payload if isinstance(payload, list) else payload.get("cases", payload)
    cases = tuple(ProbeCase.from_mapping(dict(row)) for row in rows)
    return cases, hashlib.sha256(raw_bytes).hexdigest()


def dependency_group(case: ProbeCase, family_id: str) -> str:
    """§2.1's split key: user, else source episode, else recurrence family."""
    for attribute in ("user_id", "source_episode_id"):
        value = getattr(case, attribute, None)
        if value:
            return str(value)
    return family_id


def _score(program: Program, runtime, intent) -> tuple[int, int, int]:
    """`(state_success, collateral, logical_cost)` for one program on one case.

    A program that blows a resource bound scores 0 rather than raising: failing
    closed is the registered behavior (§8.1), and an operator that cannot run on
    a case genuinely did not repair it.
    """
    state = initial_state_from_runtime_case(runtime)
    try:
        result = execute_program(program, runtime, state)
    except (ExecutionLimitError, ProgramBoundsError, IdentityActionError):
        return 0, 0, 0
    fitness = evaluate_state(result.state, intent)
    return fitness.state_success, fitness.collateral_count, result.logical_cost


def build_dev_rows(domains, token_budget: int) -> tuple[list[dict], dict]:
    """Every D_dev case with its runtime surface and sealed intent.

    Returns the rows plus a per-domain report, so an under-supported domain is
    visible in the manifest instead of silently shrinking the sample.
    """
    rows: list[dict] = []
    report: dict[str, object] = {}
    for domain in domains:
        cases, data_sha256 = load_cases(domain)
        coverage = build_dev_intents(cases, domain=domain, token_budget=token_budget)
        by_id = {intent.case_id: intent for intent in coverage.intents}
        kept = 0
        tiers: collections.Counter = collections.Counter()
        for case in cases:
            intent = by_id.get(case.case_id)
            if intent is None:
                continue
            group = dependency_group(case, intent.family_id)
            tier = assign_split_tier(group)
            tiers[tier] += 1
            if tier != "D_dev":
                continue
            rows.append(
                {
                    "case_id": case.case_id,
                    "family_id": intent.family_id,
                    "domain": domain,
                    "dependency_group": group,
                    "runtime": runtime_case_from_probe_case(
                        case, token_budget=token_budget, family_id=intent.family_id
                    ),
                    "intent": intent,
                }
            )
            kept += 1
        report[domain] = {
            "data_sha256": data_sha256,
            "intent_constructibility_rate": coverage.as_mapping().get(
                "intent_constructibility_rate"
            ),
            "cases_total": len(cases),
            "split_tiers": dict(sorted(tiers.items())),
            "d_dev_cases": kept,
        }
    return rows, report


def score_arm(name: str, program: Program, rows: list[dict]) -> list[dict]:
    """One arm's per-case outcomes."""
    scored: list[dict] = []
    for row in rows:
        success, collateral, cost = _score(program, row["runtime"], row["intent"])
        scored.append(
            {
                "arm": name,
                "case_id": row["case_id"],
                "family_id": row["family_id"],
                "domain": row["domain"],
                "state_success": success,
                "collateral_count": collateral,
                "logical_cost": cost,
            }
        )
    return scored


def _family_mean(outcomes: list[dict], families: set[str]) -> float:
    """Family-level mean success, so a large family cannot outvote a small one."""
    by_family: dict[str, list[int]] = {}
    for row in outcomes:
        if row["family_id"] not in families:
            continue
        by_family.setdefault(row["family_id"], []).append(row["state_success"])
    if not by_family:
        return 0.0
    means = [sum(values) / len(values) for values in by_family.values()]
    return sum(means) / len(means)


def _rank_key(name: str, outcomes: list[dict], families: set[str]) -> tuple:
    """§10.1's tie-break hierarchy, reused so E0 and E4 rank the same way."""
    relevant = [row for row in outcomes if row["family_id"] in families]
    collateral = sum(row["collateral_count"] for row in relevant)
    cost = sum(row["logical_cost"] for row in relevant)
    return (-_family_mean(outcomes, families), collateral, cost, name)


def crossfit_headroom(
    closed: dict[str, list[dict]],
    hand: dict[str, list[dict]],
    rows: list[dict],
    *,
    folds: int,
) -> dict[str, object]:
    """§6.3. Select on training folds, evaluate held-out, aggregate per family."""
    families = sorted({row["family_id"] for row in rows})
    assignment = crossfit_family_folds(families, folds=folds, seed=CROSSFIT_SEED)
    hand_names = sorted(hand)
    closed_names = sorted(closed)

    per_case: dict[tuple[str, str], dict[str, float]] = {}
    fold_picks: list[dict[str, object]] = []
    for fold in range(folds):
        train = {f for f, index in assignment.items() if index != fold}
        held = {f for f, index in assignment.items() if index == fold}
        if not train or not held:
            raise ValueError(f"fold {fold} has an empty side")
        # Selection reads training folds only (§14.4).
        best_closed = min(
            closed_names, key=lambda name: _rank_key(name, closed[name], train)
        )
        best_hand = min(
            hand_names, key=lambda name: _rank_key(name, hand[name], train)
        )
        fold_picks.append(
            {
                "fold": fold,
                "train_families": len(train),
                "held_out_families": len(held),
                "selected_closed_spec": best_closed,
                "selected_hand_seed": best_hand,
            }
        )
        for row in closed[best_closed]:
            if row["family_id"] in held:
                per_case.setdefault(
                    (row["family_id"], row["case_id"]), {}
                )["closed"] = float(row["state_success"])
        for row in hand[best_hand]:
            if row["family_id"] in held:
                per_case.setdefault(
                    (row["family_id"], row["case_id"]), {}
                )["hand"] = float(row["state_success"])

    paired_rows = [
        {
            "family_id": family,
            "case_id": case_id,
            "closed": values["closed"],
            "hand": values["hand"],
        }
        for (family, case_id), values in sorted(per_case.items())
        if "closed" in values and "hand" in values
    ]
    differences = family_paired_differences(
        paired_rows, artifact_key="closed", baseline_key="hand"
    )
    estimate = (
        sum(value for _, value in differences) / len(differences)
        if differences
        else 0.0
    )
    lower_bound = family_blocked_lower_bound(differences, seed=BOOTSTRAP_SEED)
    p_value = sign_flip_p_value(differences, seed=BOOTSTRAP_SEED)
    return {
        "folds": folds,
        "fold_seed": CROSSFIT_SEED,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "family_count": len(families),
        "fold_selections": fold_picks,
        "paired_case_count": len(paired_rows),
        "paired_family_count": len(differences),
        "headroom_grammar_estimate": round(estimate, 6),
        "family_blocked_lb95": round(lower_bound, 6),
        "sign_flip_p_value": round(p_value, 6),
        "family_differences": [
            [family, round(value, 6)] for family, value in differences
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domains", nargs="*", default=list(DEFAULT_DOMAINS))
    parser.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)
    parser.add_argument(
        "--max-sequence-length", type=int, default=CLOSED_MAX_SEQUENCE_LENGTH
    )
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    header = {
        "protocol_version": PROTOCOL_VERSION,
        "closed_grammar_version": CLOSED_GRAMMAR_VERSION,
        "hand_seed_population_version": HAND_SEED_POPULATION_VERSION,
        "ir_grammar_version": IR_GRAMMAR_VERSION,
        "state_fitness_version": STATE_FITNESS_VERSION,
        "state_intent_schema_version": STATE_INTENT_SCHEMA_VERSION,
        "probe_suite_version": PROBE_SUITE_VERSION,
        "probe_suite_sha256": probe_suite_sha256(),
        "code_revision": code_revision(),
        "runtime_uses_gold": False,
        "llm_calls": 0,
        "seed": CROSSFIT_SEED,
    }

    # -- enumeration (§6.1) ----------------------------------------------
    specs = canonical_closed_specs(args.max_sequence_length)
    raw = count_closed_grammar(args.max_sequence_length)
    analytic = count_canonical_closed_grammar(args.max_sequence_length)
    if len(specs) != analytic:
        print(f"FAIL: {len(specs)} canonical specs, analytic count is {analytic}")
        return 1

    fingerprints = {spec.content_hash(): behavior_fingerprint(spec.program) for spec in specs}
    behavior_classes = set(fingerprints.values())

    grammar = closed_grammar_manifest(args.max_sequence_length)
    grammar.update(header)
    grammar["behaviorally_unique_count"] = len(behavior_classes)
    grammar["hand_seed_population"] = hand_seed_manifest()
    (args.output_dir / "closed_grammar_manifest.json").write_text(
        json.dumps(grammar, indent=2, sort_keys=True) + "\n"
    )
    with (args.output_dir / "closed_specs.jsonl").open("w") as out:
        for index, spec in enumerate(specs):
            out.write(
                json.dumps(
                    {"index": index, **spec.as_mapping()}, sort_keys=True
                )
                + "\n"
            )
    with (args.output_dir / "closed_behavior_fingerprints.jsonl").open("w") as out:
        for spec in specs:
            out.write(
                json.dumps(
                    {
                        "content_hash": spec.content_hash(),
                        "canonical_ast_hash": spec.canonical_ast_hash(),
                        "behavior_fingerprint": fingerprints[spec.content_hash()],
                        "stages": [a.value for a in spec.canonical_actions],
                    },
                    sort_keys=True,
                )
                + "\n"
            )
        # §9.1's population enters the pre-search envelope alongside the closed
        # grammar, so its fingerprints are written to the same file E0b reads.
        for seed in HAND_SEEDS:
            out.write(
                json.dumps(
                    {
                        "content_hash": f"hand-seed:{seed.name}",
                        "canonical_ast_hash": seed.canonical_ast_hash(),
                        "behavior_fingerprint": behavior_fingerprint(seed.program),
                        "stages": [f"hand_seed:{seed.name}"],
                    },
                    sort_keys=True,
                )
                + "\n"
            )
    print(
        f"enumerated {raw} raw -> {len(specs)} canonical -> "
        f"{len(behavior_classes)} behavior classes"
    )

    # -- dev data --------------------------------------------------------
    rows, domain_report = build_dev_rows(args.domains, args.token_budget)
    families = {row["family_id"] for row in rows}
    print(f"scoring {len(rows)} D_dev cases across {len(families)} families")
    if len(families) < args.folds:
        print(
            f"FAIL: {len(families)} D_dev families is fewer than "
            f"{args.folds} folds; E0 cannot cross-fit"
        )
        (args.output_dir / "closed_spec_count.json").write_text(
            json.dumps(
                {
                    **header,
                    "raw_generated_count": raw,
                    "canonical_unique_count": len(specs),
                    "behaviorally_unique_count": len(behavior_classes),
                    "status": "insufficient_dev_families",
                    "d_dev_families": len(families),
                    "domains": domain_report,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        return 1

    # -- execution matrix (§6.2) -----------------------------------------
    started = time.perf_counter()
    closed_outcomes: dict[str, list[dict]] = {}
    hand_outcomes: dict[str, list[dict]] = {}
    matrix_path = args.output_dir / "closed_state_matrix.jsonl"
    with matrix_path.open("w") as matrix:
        for spec in specs:
            name = spec.format()
            outcomes = score_arm(name, spec.program, rows)
            closed_outcomes[name] = outcomes
            for row in outcomes:
                matrix.write(
                    json.dumps({"arm_kind": "closed", **row}, sort_keys=True) + "\n"
                )
        for seed in HAND_SEEDS:
            outcomes = score_arm(seed.name, seed.program, rows)
            hand_outcomes[seed.name] = outcomes
            for row in outcomes:
                matrix.write(
                    json.dumps({"arm_kind": "hand_seed", **row}, sort_keys=True) + "\n"
                )
    elapsed = time.perf_counter() - started
    executable = sum(
        1
        for name in closed_outcomes
        if any(row["logical_cost"] for row in closed_outcomes[name])
    )
    no_op = len(specs) - executable
    print(
        f"scored {len(specs)} closed specs + {len(HAND_SEEDS)} hand seeds "
        f"in {elapsed:.1f}s ({no_op} closed specs never acted)"
    )

    # -- headroom (§6.3) --------------------------------------------------
    crossfit = crossfit_headroom(
        closed_outcomes, hand_outcomes, rows, folds=args.folds
    )
    best_closed_global = min(
        sorted(closed_outcomes),
        key=lambda name: _rank_key(name, closed_outcomes[name], families),
    )
    best_hand_global = min(
        sorted(hand_outcomes),
        key=lambda name: _rank_key(name, hand_outcomes[name], families),
    )
    estimate = crossfit["headroom_grammar_estimate"]
    lower_bound = crossfit["family_blocked_lb95"]
    gate_pass = estimate >= GATE_MIN_ESTIMATE and lower_bound > 0

    crossfit.update(header)
    crossfit["gate"] = {
        "rule": "estimate >= 0.05 and one-sided family-blocked LB95 > 0",
        "min_estimate": GATE_MIN_ESTIMATE,
        "estimate_pass": estimate >= GATE_MIN_ESTIMATE,
        "lower_bound_pass": lower_bound > 0,
        "decision": "GO" if gate_pass else "STOP",
        "wording_note": (
            "§6.3: an intentionally strict development triage rule, not the "
            "power-calibrated confirmatory test. Its conjunction with a "
            "point-estimate threshold has lower pass probability than the "
            "nominal interval power in §4 and must not be described as a "
            "90%-powered gate."
        ),
    }
    crossfit["descriptive_global"] = {
        "note": (
            "§6.3: reported descriptively, not as the cross-fitted primary "
            "estimate. Selected and evaluated on the same cases."
        ),
        "best_closed_spec_all_dev": best_closed_global,
        "best_closed_spec_family_mean": round(
            _family_mean(closed_outcomes[best_closed_global], families), 6
        ),
        "best_hand_seed_all_dev": best_hand_global,
        "best_hand_seed_family_mean": round(
            _family_mean(hand_outcomes[best_hand_global], families), 6
        ),
    }
    crossfit["domains"] = domain_report
    (args.output_dir / "closed_crossfit_results.json").write_text(
        json.dumps(crossfit, indent=2, sort_keys=True) + "\n"
    )

    counts = dict(header)
    counts.update(
        {
            "raw_generated_count": raw,
            "canonical_unique_count": len(specs),
            "analytic_canonical_count": analytic,
            "executable_count": executable,
            "behaviorally_unique_count": len(behavior_classes),
            "invalid_or_no_op_count": no_op,
            "max_sequence_length": args.max_sequence_length,
            "d_dev_case_count": len(rows),
            "d_dev_family_count": len(families),
            "exact_best_hand_seed": best_hand_global,
            "exact_best_closed_spec": best_closed_global,
            "family_blocked_held_out_difference": estimate,
            "gate_decision": crossfit["gate"]["decision"],
            "scoring_seconds": round(elapsed, 3),
        }
    )
    (args.output_dir / "closed_spec_count.json").write_text(
        json.dumps(counts, indent=2, sort_keys=True) + "\n"
    )

    print(
        f"\nheadroom_grammar = {estimate:+.4f} "
        f"(LB95 {lower_bound:+.4f}, p={crossfit['sign_flip_p_value']:.4f}, "
        f"{crossfit['paired_family_count']} families)"
    )
    print(f"best closed spec (descriptive, all-dev): {best_closed_global}")
    print(f"best hand seed  (descriptive, all-dev): {best_hand_global}")
    if gate_pass:
        print("\nE0 GATE: GO -- synthesis search is authorized (§6.3)")
        print(f"wrote artifacts under {args.output_dir}")
        return 0
    print(
        "\nE0 GATE: STOP -- §6.3's development continuation gate failed. "
        "Route A stops under the current time-box; the exact grammar envelope "
        "remains a paper result and no synthesis search is authorized."
    )
    print(f"wrote artifacts under {args.output_dir}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
