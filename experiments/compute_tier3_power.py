"""Route A §4: the mechanical tier-3 sample size, from D_dev variance only.

This command answers one question -- how many families `D_confirm` needs -- and
it answers it *before* any artifact exists, from a variance estimate that
cannot see the thing it will later be used to test. That ordering is the whole
point. Sizing a confirmation from the observed effect, or from the variance of
the comparison you ended up making, turns the power calculation into a
restatement of the result.

§4.2 is specific about which variance:

    sigma_design = max over (domain x eligible-good-spec-pair) family SD

"Eligible good spec" is doing real work there. A spec that no-ops, or that
fails a hard gate, has near-zero paired variance against another such spec --
including it would *shrink* sigma_design and understate the required n. So the
filter runs first (hard gates, then the 90th-percentile dev `state_success`
cut), and the pairing runs only over what survives. §4.2 also forbids the
convenient fallback: if no domain supplies a finite eligible-pair SD, this
emits `INSUFFICIENT_VARIANCE_BASIS` and stops rather than widening to all
specs.

The hard gates are `state_success`'s non-efficacy components (§3.4):
`provenance_valid`, `budget_valid`, `trace_valid`. E0's state matrix records
only `state_success`, `collateral_count` and `logical_cost`, so the fitness
vectors are recomputed here rather than inferred -- a spec can reach
`state_success == 0` either by failing a gate or by simply not repairing the
case, and §4.2 excludes only the former.

Zero LLM calls: every input is deterministic state fitness.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import statistics
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.counterfactual.closed_grammar import (  # noqa: E402
    CLOSED_GRAMMAR_VERSION,
    canonical_closed_specs,
)
from cmd_audit.counterfactual.program_ir import (  # noqa: E402
    IR_GRAMMAR_VERSION,
    IdentityActionError,
    ProgramBoundsError,
)
from cmd_audit.counterfactual.repair_state import (  # noqa: E402
    initial_state_from_runtime_case,
)
from cmd_audit.counterfactual.state_executor import (  # noqa: E402
    ExecutionLimitError,
    execute_program,
)
from cmd_audit.eval.state_fitness import (  # noqa: E402
    STATE_FITNESS_VERSION,
    evaluate_state,
)
from cmd_audit.eval.state_intent import STATE_INTENT_SCHEMA_VERSION  # noqa: E402
from experiments.run_closed_grammar_enumeration import (  # noqa: E402
    DEFAULT_DOMAINS,
    DEFAULT_TOKEN_BUDGET,
    PROTOCOL_VERSION,
    build_dev_rows,
    code_revision,
)

OUTPUT = Path("artifacts/route_a/prereg/tier3_power.json")

#: §4.1, frozen. Named rather than inlined so a silent edit is a diff.
MDE = 0.10
POWER = 0.90
ONE_SIDED_ALPHA = 0.05
Z_ALPHA = 1.644854
Z_POWER = 1.281552
USABLE_FAMILY_RATE = 0.90
MINIMUM_FAMILIES = 30

#: §4.2 step 3.
GOOD_SPEC_PERCENTILE = 90


class InsufficientVarianceBasis(RuntimeError):
    """§4.2: no domain supplied a finite eligible-pair SD."""


def _fitness(program, runtime, intent):
    """Full fitness vector, or `None` when the program cannot run.

    A program that blows a bound is not gate-failing repair work -- it is not
    repair work at all -- so it is dropped from the variance basis rather than
    counted as a hard-gate failure.
    """
    state = initial_state_from_runtime_case(runtime)
    try:
        result = execute_program(program, runtime, state)
    except (ExecutionLimitError, ProgramBoundsError, IdentityActionError):
        return None
    return evaluate_state(result.state, intent)


def spec_case_table(rows) -> dict[str, dict[str, dict]]:
    """`spec -> case_id -> {state_success, hard_gates_pass, family_id, domain}`."""
    specs = canonical_closed_specs()
    table: dict[str, dict[str, dict]] = {}
    for spec in specs:
        name = spec.format()
        program = spec.canonical_program
        per_case: dict[str, dict] = {}
        for row in rows:
            vector = _fitness(program, row["runtime"], row["intent"])
            if vector is None:
                continue
            per_case[row["case_id"]] = {
                "state_success": vector.state_success,
                "hard_gates_pass": bool(
                    vector.provenance_valid
                    and vector.budget_valid
                    and vector.trace_valid
                ),
                "family_id": row["family_id"],
                "domain": row["domain"],
            }
        table[name] = per_case
    return table


def _percentile(values: list[float], percentile: float) -> float:
    """Nearest-rank percentile on a sorted copy.

    Written out rather than taken from `statistics.quantiles`, which
    interpolates and needs at least two points: a domain with one hard-passing
    spec must reach §4.2's "fewer than two eligible specs" branch rather than
    raise from inside the cut. Nearest rank also keeps the cut equal to one of
    the observed values, so the `>= cut` comparison always retains at least the
    spec that defined it.
    """
    if not values:
        raise ValueError("percentile of an empty sample")
    ordered = sorted(values)
    rank = math.ceil(percentile / 100 * len(ordered))
    return ordered[max(0, min(len(ordered), rank) - 1)]


def _family_means(per_case: dict[str, dict], case_ids) -> dict[str, float]:
    grouped: dict[str, list[int]] = collections.defaultdict(list)
    for case_id in case_ids:
        entry = per_case[case_id]
        grouped[entry["family_id"]].append(entry["state_success"])
    return {family: sum(v) / len(v) for family, v in grouped.items()}


def domain_design_sd(table, rows, domain: str) -> dict:
    """§4.2 steps 2-4 for one domain."""
    domain_cases = [row["case_id"] for row in rows if row["domain"] == domain]
    domain_case_set = set(domain_cases)

    hard_pass: dict[str, list[str]] = {}
    for name, per_case in table.items():
        covered = [c for c in domain_cases if c in per_case]
        if not covered:
            continue
        if all(per_case[c]["hard_gates_pass"] for c in covered):
            hard_pass[name] = covered

    if not hard_pass:
        return {
            "domain": domain,
            "eligible_spec_count": 0,
            "design_sd": None,
            "excluded_reason": "no spec passes every hard gate on this domain",
        }

    dev_success = {
        name: sum(table[name][c]["state_success"] for c in covered) / len(covered)
        for name, covered in hard_pass.items()
    }
    cut = _percentile(list(dev_success.values()), GOOD_SPEC_PERCENTILE)
    eligible = sorted(name for name, value in dev_success.items() if value >= cut)

    if len(eligible) < 2:
        return {
            "domain": domain,
            "eligible_spec_count": len(eligible),
            "eligible_specs": eligible,
            "good_spec_cut": cut,
            "hard_pass_spec_count": len(hard_pass),
            "design_sd": None,
            "excluded_reason": "fewer than two eligible specs (§4.2)",
        }

    pair_sds: list[dict] = []
    for left, right in combinations(eligible, 2):
        shared = [
            c
            for c in domain_cases
            if c in table[left] and c in table[right]
        ]
        if not shared:
            continue
        left_means = _family_means(table[left], shared)
        right_means = _family_means(table[right], shared)
        families = sorted(set(left_means) & set(right_means))
        differences = [left_means[f] - right_means[f] for f in families]
        if len(differences) < 2:
            continue
        sd = statistics.stdev(differences)
        if not math.isfinite(sd):
            continue
        pair_sds.append(
            {"left": left, "right": right, "families": len(families), "sd": sd}
        )

    if not pair_sds:
        return {
            "domain": domain,
            "eligible_spec_count": len(eligible),
            "eligible_specs": eligible,
            "good_spec_cut": cut,
            "hard_pass_spec_count": len(hard_pass),
            "design_sd": None,
            "excluded_reason": "no eligible pair yielded a finite family SD (§4.2)",
        }

    worst = max(pair_sds, key=lambda entry: entry["sd"])
    return {
        "domain": domain,
        "eligible_spec_count": len(eligible),
        "eligible_specs": eligible,
        "good_spec_cut": cut,
        "hard_pass_spec_count": len(hard_pass),
        "pair_count": len(pair_sds),
        "design_sd": worst["sd"],
        "max_sd_pair": [worst["left"], worst["right"]],
        "case_count": len(domain_case_set),
    }


def mechanical_sample_size(sigma_design: float) -> dict:
    """§4.3, verbatim. No rounding choices beyond the two ceilings."""
    n_raw = math.ceil((((Z_ALPHA + Z_POWER) * sigma_design) / MDE) ** 2)
    n_tier3 = max(MINIMUM_FAMILIES, math.ceil(n_raw / USABLE_FAMILY_RATE))
    return {
        "sigma_design": sigma_design,
        "n_raw": n_raw,
        "n_tier3": n_tier3,
        "formula": (
            "n_raw = ceil((((z_alpha + z_power) * sigma_design) / mde) ** 2); "
            "n_tier3 = max(minimum_families, ceil(n_raw / usable_family_rate))"
        ),
    }


def aggregate_design_sd(per_domain: list[dict]) -> tuple[float, dict]:
    """§4.2 step 5: the maximum over domains that supplied a finite SD.

    Split out of `compute` so the two rules it enforces are reachable without
    loading D_dev: the maximum (not the minimum -- taking the narrowest pair
    would understate the variance and so the required n) and the hard stop when
    no domain qualifies. §4.2 forbids widening to all-spec variance instead.
    """
    finite = [entry for entry in per_domain if entry["design_sd"] is not None]
    if not finite:
        raise InsufficientVarianceBasis(
            "no domain supplied a finite eligible-pair SD; §4.2 forbids "
            "falling back to all-spec variance"
        )
    source = max(finite, key=lambda entry: entry["design_sd"])
    return source["design_sd"], source


def compute(domains, token_budget: int) -> dict:
    rows, domain_report = build_dev_rows(domains, token_budget)
    if not rows:
        raise InsufficientVarianceBasis("no D_dev cases available")

    table = spec_case_table(rows)
    per_domain = [domain_design_sd(table, rows, domain) for domain in domains]
    sigma_design, sigma_source = aggregate_design_sd(per_domain)
    sizing = mechanical_sample_size(sigma_design)

    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "closed_grammar_version": CLOSED_GRAMMAR_VERSION,
        "ir_grammar_version": IR_GRAMMAR_VERSION,
        "state_fitness_version": STATE_FITNESS_VERSION,
        "state_intent_schema_version": STATE_INTENT_SCHEMA_VERSION,
        "code_revision": code_revision(),
        "runtime_uses_gold": False,
        "llm_calls": 0,
        "frozen_parameters": {
            "mde": MDE,
            "power": POWER,
            "one_sided_alpha": ONE_SIDED_ALPHA,
            "z_alpha": Z_ALPHA,
            "z_power": Z_POWER,
            "usable_family_rate": USABLE_FAMILY_RATE,
            "minimum_families": MINIMUM_FAMILIES,
            "good_spec_percentile": GOOD_SPEC_PERCENTILE,
        },
        "excluded_data": [
            "D_search",
            "D_select",
            "D_confirm",
            "the selected synthesized artifact",
            "observed artifact effect size",
        ],
        "domains": list(domains),
        "domain_report": domain_report,
        "per_domain_design_sd": per_domain,
        "sigma_design_source_domain": sigma_source["domain"],
        **sizing,
        "sizing_note": (
            "sigma_design is the maximum family SD over eligible good-spec "
            "pairs, not over all specs: a no-op or gate-failing spec has "
            "near-zero paired variance and would understate n. §4.3 forbids "
            "lowering power, raising the MDE, or reusing burned data to make "
            "n_tier3 fit."
        ),
    }
    payload["payload_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Route A §4: mechanical tier-3 sample size from D_dev variance. "
            "Computes sigma_design as the max family SD over eligible "
            "good-spec pairs (hard-gate pass, then the 90th-percentile dev "
            "state_success cut), then applies §4.3's two ceilings. Emits "
            "INSUFFICIENT_VARIANCE_BASIS and exits nonzero rather than "
            "falling back to all-spec variance."
        )
    )
    parser.add_argument("--domains", nargs="*", default=list(DEFAULT_DOMAINS))
    parser.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    try:
        payload = compute(args.domains, args.token_budget)
    except InsufficientVarianceBasis as error:
        print(f"INSUFFICIENT_VARIANCE_BASIS: {error}")
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(
        f"sigma_design = {payload['sigma_design']:.6f} "
        f"(from {payload['sigma_design_source_domain']})"
    )
    print(f"n_raw    = {payload['n_raw']}")
    print(f"n_tier3  = {payload['n_tier3']} families required for D_confirm")
    for entry in payload["per_domain_design_sd"]:
        if entry["design_sd"] is None:
            print(f"  {entry['domain']:20} excluded: {entry['excluded_reason']}")
        else:
            print(
                f"  {entry['domain']:20} sd={entry['design_sd']:.6f} "
                f"eligible={entry['eligible_spec_count']} "
                f"pairs={entry['pair_count']}"
            )
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
