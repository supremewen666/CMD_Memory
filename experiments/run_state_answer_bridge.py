"""Route A §7 (E1.5): the state-to-answer bridge.

The bridge validates the *metric*, not the artifact. It asks one question --
does deterministic state convergence buy improved answer recovery? -- and it
asks it before E2/E3 so a failed target metric kills the route cheaply.

Three things make this more than a mean difference.

**The estimate conditions on a transition, not on an arm.** §7.4's `I_f` is the
mean answer gain over cases where the frozen operator left `state_success` at 0
and the candidate reached 1. An all-case difference would mix in cases where the
state never moved, which is exactly what the bridge is trying to isolate.

**The specificity contrast can veto the wording even on a pass.** If the
candidate also answers better on state-*unchanged* cases, the gain is not
attributable to state convergence -- it is a better answerer. §7.4 requires
`S_f = I_f - U_f` to be reported and says a nonpositive contrast "prevents causal
or mediational wording even when the primary bridge passes", so `decision` and
`causal_wording_permitted` are two separate outputs here.

**Insufficient support is not a failure.** Below 30 state-improved cases across
10 families the answer is `BRIDGE_INSUFFICIENT_SUPPORT`, which §7.4 is explicit
is "not evidence that the state metric is invalid". Route A may then make a
state-only claim. Conflating it with FAIL would report a missing measurement as
a refuted hypothesis.

One preregistration gap is recorded rather than papered over. §7.4's primary
test requires "no domain has a preregistered material negative direction", but
`route_a_preregistration.json` registers only the bridge primary
(`answer_gain_conditioned_on_state_success_transition_0_to_1`) and carries no
materiality threshold. `MATERIAL_NEGATIVE_DIRECTION` below is that missing
parameter, set to §3.7's own MDE so the materiality scale is the spec's rather
than one invented here, and registered *now* -- before any bridge data exists and
while the LLM endpoint needed to produce answers is down. The artifact flags it
as a late registration so a reader can see it was not chosen after seeing a
direction.

LLM calls: this command needs model-generated answers for the deterministic
`answer_score`, so it is the one Route A command that is not zero-call. §7.2's
LLM *judge* scores are recorded as a secondary column and never enter the
decision.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.eval.route_a_statistics import (  # noqa: E402
    BOOTSTRAP_SAMPLES,
    MDE,
    family_blocked_lower_bound,
    sign_flip_p_value,
)

#: §13's artifact contract names this file, and `check_route_a_gates` reads §15's
#: bridge rung from it. The name is the contract's, not this command's.
OUTPUT = Path("artifacts/route_a/bridge/bridge_decision.json")

#: §7.3 primary. Named so §7.2's "judge scores never become the bridge target"
#: is a diff rather than a comment.
PRIMARY_METRIC = "answer_score"

#: §7.3 secondary.
SECONDARY_METRIC = "evidence_recall_from_text"

#: §7.4 support minimums.
MINIMUM_STATE_IMPROVED_CASES = 30
MINIMUM_STATE_IMPROVED_FAMILIES = 10

#: §7.4's "preregistered material negative direction", which the frozen
#: preregistration does not carry. Set to §3.7's MDE: a domain whose family-mean
#: improved-stratum gain is at or below -0.10 is in a material negative
#: direction. Registered before any bridge data exists (see the module docstring).
#:
#: No minimum family count guards it, so a single strongly negative family can
#: veto its domain. That asymmetry is deliberate -- it can only turn a PASS into
#: a FAIL, never the reverse.
MATERIAL_NEGATIVE_DIRECTION = -MDE

#: Recorded in the artifact so the late registration is visible.
LATE_REGISTRATION_NOTE = (
    "§7.4 requires a preregistered material negative direction; "
    "route_a_preregistration.json registers only the bridge primary and "
    "carries no materiality threshold. MATERIAL_NEGATIVE_DIRECTION = -MDE was "
    "registered in this command before any bridge data existed and while the "
    "answer-generating endpoint was unavailable, so it could not have been "
    "chosen after observing a direction."
)


class Stratum(str, Enum):
    """§7.4's strata, plus the third case the spec's two definitions imply."""

    IMPROVED = "state_improved"
    UNCHANGED = "state_unchanged"
    WORSENED = "state_worsened"


class BridgeInsufficientSupport(RuntimeError):
    """§7.4: fewer than 30 state-improved cases across 10 families.

    Not a bridge failure. Route A may make a state-only claim; it may not make
    an answer-recovery claim.
    """


@dataclass(frozen=True)
class Support:
    """Transition support, before any estimate is computed."""

    state_improved_cases: int
    state_improved_families: int
    state_unchanged_cases: int
    state_worsened_cases: int

    @property
    def sufficient(self) -> bool:
        return (
            self.state_improved_cases >= MINIMUM_STATE_IMPROVED_CASES
            and self.state_improved_families >= MINIMUM_STATE_IMPROVED_FAMILIES
        )


def classify_stratum(frozen_state: float, candidate_state: float) -> Stratum:
    """§7.4. A 1 -> 0 transition is worsened, not unchanged.

    §7.4 names only improved and unchanged, so a regression has to land
    somewhere. Folding it into `unchanged` would put a case whose state got
    *worse* into the specificity contrast's reference stratum and shift `U_f`.

    Both inputs must be exactly 0 or 1. `state_success` is a deterministic
    boolean check, so a fractional value cannot come from the state executor --
    it can only come from a malformed pairs file, and there is no reading of
    "0.5 -> 1" that §7.4 defines. Refusing beats guessing: with the domain
    enforced, the three strata are exhaustive, and a partial-credit column
    quietly landing in the improved stratum would inflate the very count the
    support gate exists to bound.
    """
    for name, value in (("frozen", frozen_state), ("candidate", candidate_state)):
        if value not in (0, 1):
            raise ValueError(
                f"state_success_{name} = {value!r}; §7.4's strata are defined "
                "over a deterministic boolean state check, so only 0 and 1 have "
                "a stratum. This indicates a malformed pairs file."
            )
    if frozen_state == candidate_state:
        return Stratum.UNCHANGED
    if frozen_state == 0 and candidate_state == 1:
        return Stratum.IMPROVED
    return Stratum.WORSENED


def _stratum(row) -> Stratum:
    return classify_stratum(
        row["state_success_frozen"], row["state_success_candidate"]
    )


def summarize_support(rows) -> Support:
    """Count each stratum. Only the improved stratum counts toward the gate."""
    improved_families = set()
    counts = collections.Counter()
    for row in rows:
        stratum = _stratum(row)
        counts[stratum] += 1
        if stratum is Stratum.IMPROVED:
            improved_families.add(row["family_id"])
    return Support(
        state_improved_cases=counts[Stratum.IMPROVED],
        state_improved_families=len(improved_families),
        state_unchanged_cases=counts[Stratum.UNCHANGED],
        state_worsened_cases=counts[Stratum.WORSENED],
    )


def _stratum_means(rows, stratum: Stratum) -> dict[str, float]:
    grouped: dict[str, list[float]] = collections.defaultdict(list)
    for row in rows:
        if _stratum(row) is stratum:
            grouped[row["family_id"]].append(float(row["answer_gain"]))
    return {family: sum(v) / len(v) for family, v in grouped.items()}


def family_improved_means(rows) -> tuple[tuple[str, float], ...]:
    """§7.4 `I_f`, for each family that has state-improved cases.

    A family with none is absent rather than 0.0: §7.4 says "for each family
    with state-improved cases", and emitting a zero would add a null family
    effect to the family-blocked interval.
    """
    means = _stratum_means(rows, Stratum.IMPROVED)
    return tuple((family, means[family]) for family in sorted(means))


def specificity_contrast(rows) -> tuple[tuple[str, float], ...]:
    """§7.4 `S_f = I_f - U_f`, over families containing *both* strata.

    A family missing either stratum is excluded. Treating an absent `U_f` as
    zero would report `I_f` as though it had been shown specific.
    """
    improved = _stratum_means(rows, Stratum.IMPROVED)
    unchanged = _stratum_means(rows, Stratum.UNCHANGED)
    shared = sorted(set(improved) & set(unchanged))
    return tuple((family, improved[family] - unchanged[family]) for family in shared)


def _domain_directions(rows) -> dict[str, float]:
    """Per-domain family-mean improved-stratum gain."""
    by_domain: dict[str, list[tuple[str, float]]] = collections.defaultdict(list)
    for row in rows:
        if _stratum(row) is Stratum.IMPROVED:
            by_domain[row["domain"]].append((row["family_id"], float(row["answer_gain"])))

    directions: dict[str, float] = {}
    for domain, entries in by_domain.items():
        grouped: dict[str, list[float]] = collections.defaultdict(list)
        for family, gain in entries:
            grouped[family].append(gain)
        family_means = [sum(v) / len(v) for v in grouped.values()]
        directions[domain] = sum(family_means) / len(family_means)
    return directions


def bridge_decision(rows, *, seed: int, samples: int = BOOTSTRAP_SAMPLES) -> dict:
    """§7.4's primary test, plus the wording permission the contrast governs.

    Raises `BridgeInsufficientSupport` before computing anything, so an
    underpowered bridge cannot report a point estimate that invites reading.
    """
    support = summarize_support(rows)
    if not support.sufficient:
        raise BridgeInsufficientSupport(
            f"{support.state_improved_cases} state-improved case(s) across "
            f"{support.state_improved_families} family(ies); §7.4 requires "
            f"{MINIMUM_STATE_IMPROVED_CASES} across "
            f"{MINIMUM_STATE_IMPROVED_FAMILIES}. This is not evidence against "
            "the state metric: Route A may make a state-only claim but not an "
            "answer-recovery claim."
        )

    improved = family_improved_means(rows)
    lower_bound = family_blocked_lower_bound(improved, samples=samples, seed=seed)
    p_value = sign_flip_p_value(improved, seed=seed)
    estimate = sum(effect for _family, effect in improved) / len(improved)

    directions = _domain_directions(rows)
    negative_domains = sorted(
        domain
        for domain, direction in directions.items()
        if direction <= MATERIAL_NEGATIVE_DIRECTION
    )

    contrast = specificity_contrast(rows)
    if contrast:
        contrast_estimate = sum(v for _f, v in contrast) / len(contrast)
        contrast_lower_bound = family_blocked_lower_bound(
            contrast, samples=samples, seed=seed
        )
    else:
        contrast_estimate = None
        contrast_lower_bound = None

    interval_pass = lower_bound > 0.0
    decision = "PASS" if interval_pass and not negative_domains else "FAIL"

    # §7.4: a nonpositive contrast blocks causal wording even on a pass. An
    # undefined contrast (no family carries both strata) is not positive
    # evidence either, so it blocks too.
    causal_permitted = bool(
        decision == "PASS"
        and contrast_estimate is not None
        and contrast_estimate > 0.0
    )

    return {
        "primary_metric": PRIMARY_METRIC,
        "secondary_metric": SECONDARY_METRIC,
        "decision": decision,
        "rule": (
            "one-sided family-blocked LB95(mean_f(I_f)) > 0 and no domain at or "
            f"below {MATERIAL_NEGATIVE_DIRECTION}"
        ),
        "primary_estimate": estimate,
        "primary_lower_bound": lower_bound,
        "primary_sign_flip_p": p_value,
        "interval_pass": interval_pass,
        "improved_family_count": len(improved),
        "state_improved_cases": support.state_improved_cases,
        "state_improved_families": support.state_improved_families,
        "state_unchanged_cases": support.state_unchanged_cases,
        "state_worsened_cases": support.state_worsened_cases,
        "domain_directions": directions,
        "negative_direction_domains": negative_domains,
        "material_negative_direction": MATERIAL_NEGATIVE_DIRECTION,
        "specificity_contrast_estimate": contrast_estimate,
        "specificity_contrast_lower_bound": contrast_lower_bound,
        "specificity_contrast_family_count": len(contrast),
        "causal_wording_permitted": causal_permitted,
        "causal_wording_note": (
            "§7.4: a nonpositive or undefined specificity contrast prevents "
            "causal or mediational wording even when the primary bridge passes."
        ),
        "judge_scores_note": (
            "§7.2: LLM judge scores are secondary and never the bridge target. "
            "Any judge column in the input is recorded, not decided on."
        ),
        "late_registration_note": LATE_REGISTRATION_NOTE,
        "bootstrap_samples": samples,
        "seed": seed,
    }


def load_pairs(path: Path) -> list[dict]:
    """Read the scored candidate/frozen pairs §7.2 asks to be recorded.

    Kept separate from the statistics so the decision is testable without an
    LLM: producing this file is the part that needs model-generated answers.
    """
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} does not exist. §7.2's per-case record (state_success and "
            "deterministic answer score under both the frozen and candidate "
            "operator) is produced by scoring D_dev with an answer-generating "
            "endpoint; this command consumes it."
        )
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{number}: {error}") from error
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Route A §7 (E1.5): state-to-answer bridge. Consumes scored "
            "candidate/frozen pairs and applies §7.4's primary test. Exits 2 on "
            "BRIDGE_INSUFFICIENT_SUPPORT, which is not a bridge failure."
        )
    )
    parser.add_argument(
        "--pairs",
        type=Path,
        default=Path("artifacts/route_a/bridge/bridge_pairs.jsonl"),
        help="per-case record from §7.2 (produced with an answer endpoint)",
    )
    parser.add_argument("--seed", type=int, default=24)
    parser.add_argument("--bootstrap-samples", type=int, default=BOOTSTRAP_SAMPLES)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    try:
        rows = load_pairs(args.pairs)
    except FileNotFoundError as error:
        print(f"BRIDGE_INPUT_UNAVAILABLE: {error}")
        return 1

    try:
        payload = bridge_decision(
            rows, seed=args.seed, samples=args.bootstrap_samples
        )
    except BridgeInsufficientSupport as error:
        print(f"BRIDGE_INSUFFICIENT_SUPPORT: {error}")
        return 2

    payload["pair_count"] = len(rows)
    payload["payload_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(f"decision = {payload['decision']}  ({payload['rule']})")
    print(f"  I_f estimate      = {payload['primary_estimate']:+.6f}")
    print(f"  LB95              = {payload['primary_lower_bound']:+.6f}")
    print(f"  sign-flip p       = {payload['primary_sign_flip_p']:.4f}")
    print(
        f"  support           = {payload['state_improved_cases']} improved cases "
        f"/ {payload['state_improved_families']} families"
    )
    if payload["specificity_contrast_estimate"] is None:
        print("  specificity S_f   = undefined (no family carries both strata)")
    else:
        print(
            f"  specificity S_f   = {payload['specificity_contrast_estimate']:+.6f} "
            f"over {payload['specificity_contrast_family_count']} families"
        )
    print(f"  causal wording    = {payload['causal_wording_permitted']}")
    for domain, direction in sorted(payload["domain_directions"].items()):
        flag = " MATERIAL NEGATIVE" if domain in payload["negative_direction_domains"] else ""
        print(f"  {domain:20} {direction:+.6f}{flag}")
    print(f"wrote {args.output}")
    return 0 if payload["decision"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
