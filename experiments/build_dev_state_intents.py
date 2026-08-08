#!/usr/bin/env python3
"""Route A E-1: build development hidden intents and audit constructibility.

BUILD SPEC §3.3 makes intent construction an audited prerequisite, not a
filtering step: every domain must report `intent_constructibility_rate == 1.00`
before it may participate in E0, the bridge, design-variance estimation, or
synthesis. Cases may never be silently dropped.

Writes `artifacts/route_a/prereg/state_fitness_manifest.json` (§13).
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.core.models import ProbeCase
from cmd_audit.counterfactual.repair_state import (
    add_item,
    apply_disposition,
    initial_state_from_runtime_case,
)
from cmd_audit.eval.dev_state_intents import build_dev_intents
from cmd_audit.eval.state_fitness import STATE_FITNESS_VERSION, evaluate_state
from cmd_audit.eval.state_intent import (
    STATE_INTENT_SCHEMA_VERSION,
    runtime_case_from_probe_case,
)

DEFAULT_DOMAINS = ("memtrace_kp", "stale_item", "memfail")
DEFAULT_TOKEN_BUDGET = 100000
OUTPUT = Path("artifacts/route_a/prereg/state_fitness_manifest.json")


def load_cases(domain: str) -> tuple[tuple[ProbeCase, ...], str]:
    path = Path(f"data/probe_cases/{domain}_cases.json")
    raw_bytes = path.read_bytes()
    payload = json.loads(raw_bytes)
    rows = payload if isinstance(payload, list) else payload.get("cases", payload)
    cases = tuple(ProbeCase.from_mapping(dict(row)) for row in rows)
    return cases, hashlib.sha256(raw_bytes).hexdigest()


ARMS = (
    "no_op",
    "hand_seed",
    "add_only",
    "suppress_only",
    "context_stuffing",
    "suppress_gold",
)


def probe_arms(cases, intents, *, token_budget: int) -> dict[str, float]:
    """Sanity arms proving the fitness discriminates before any search runs.

    A fitness where the no-op already wins cannot rank operators, so these
    rates are a stop-loss check rather than a result: `no_op` must sit below
    `hand_seed`, and the damaging arms must be at zero. `add_only` and
    `suppress_only` are the two halves of `hand_seed`; both being below it is
    what shows the two repair mechanisms are separately necessary.
    """
    by_id = {intent.case_id: intent for intent in intents}
    counts: collections.Counter = collections.Counter()
    scored = 0
    for case in cases:
        intent = by_id.get(case.case_id)
        if intent is None:
            continue
        scored += 1
        runtime = runtime_case_from_probe_case(
            case, token_budget=token_budget, family_id=intent.family_id
        )
        baseline = initial_state_from_runtime_case(runtime)
        pool = {item.item_id: item for item in runtime.items if not item.retrieved}
        counts["no_op"] += evaluate_state(baseline, intent).state_success

        def suppress(state):
            for perturbation in intent.perturbations:
                state = apply_disposition(
                    state,
                    item_ids=(perturbation.target_item_id,),
                    disposition="suppressed",
                    operator_node_id="hand_seed",
                    predicate_id="p_target",
                )
            return state

        def restore(state):
            for item_id in intent.allowed_added_item_ids:
                source = pool.get(item_id)
                if source is None:
                    continue
                state = add_item(
                    state,
                    item_id=item_id,
                    text=source.text,
                    source_event_ids=source.source_event_ids,
                    store=source.store,
                    operator_node_id="hand_seed",
                    predicate_id="p_restore",
                )
            return state

        counts["hand_seed"] += evaluate_state(
            restore(suppress(baseline)), intent
        ).state_success
        counts["add_only"] += evaluate_state(
            restore(baseline), intent
        ).state_success
        counts["suppress_only"] += evaluate_state(
            suppress(baseline), intent
        ).state_success

        # §14.2: the same-budget stuffing baseline must not score success.
        # Synthetic text so the arm stuffs even when the pool is empty.
        stuffed = baseline
        for index in range(6):
            stuffed = add_item(
                stuffed,
                item_id=f"i_stuff_{index}",
                text=f"{case.query} possibly relates to every stored memory.",
                operator_node_id="n_stuff",
                predicate_id="p_stuff",
            )
        counts["context_stuffing"] += evaluate_state(stuffed, intent).state_success

        if intent.protected_item_ids:
            damaged = apply_disposition(
                baseline,
                item_ids=(intent.protected_item_ids[0],),
                disposition="suppressed",
                operator_node_id="wrong_target",
                predicate_id="p_gold",
            )
            counts["suppress_gold"] += evaluate_state(damaged, intent).state_success
    if not scored:
        return {}
    return {arm: counts[arm] / scored for arm in ARMS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domains", nargs="*", default=list(DEFAULT_DOMAINS))
    parser.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    domains: list[dict] = []
    for domain in args.domains:
        cases, data_sha256 = load_cases(domain)
        report = build_dev_intents(
            cases, domain=domain, token_budget=args.token_budget
        )
        entry = report.as_mapping()
        entry["data_sha256"] = data_sha256
        entry["families"] = len({intent.family_id for intent in report.intents})
        entry["null_cases"] = sum(1 for i in report.intents if i.null_case)
        entry["perturbations_per_case"] = dict(
            sorted(
                collections.Counter(
                    len(i.perturbations) for i in report.intents
                ).items()
            )
        )
        entry["invalid_reason_codes"] = dict(
            collections.Counter(reason for _, reason in report.invalid_cases)
        )
        entry["probe_arms"] = probe_arms(
            cases, report.intents, token_budget=args.token_budget
        )
        domains.append(entry)

    manifest = {
        "protocol_version": "route-a-e-1",
        "state_fitness_version": STATE_FITNESS_VERSION,
        "state_intent_schema_version": STATE_INTENT_SCHEMA_VERSION,
        "token_budget": args.token_budget,
        "runtime_uses_gold": False,
        "llm_calls": 0,
        "domains": domains,
        "eligible_domains": [d["domain"] for d in domains if d["eligible"]],
        "ineligible_domains": [d["domain"] for d in domains if not d["eligible"]],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    for entry in domains:
        arms = entry["probe_arms"]
        print(
            f"{entry['domain']:14s} n={entry['total_runtime_cases']:5d} "
            f"rate={entry['intent_constructibility_rate']:.4f} "
            f"eligible={entry['eligible']!s:5s} "
            + " ".join(f"{arm}={arms.get(arm, 0):.3f}" for arm in ARMS)
        )
    print(f"\nwrote {args.output}")
    if manifest["ineligible_domains"]:
        print(f"INELIGIBLE: {manifest['ineligible_domains']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
