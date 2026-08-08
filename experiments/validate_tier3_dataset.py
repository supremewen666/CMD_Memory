"""Route A §5.4: the tier-3 dataset validity audit, run before sealing.

Injector v2 is a separate implementation project (§5.1) and its output is
inaccessible until after E4. This command is the gate between the two: it reads
the four files §5.3 names and decides SEAL or REFUSE. It is the only place the
runtime and sealed sides are compared, so if it passes something through, no
later stage will catch it.

The audit is a conjunction of independent checks, each of which can only refuse.
Two design points are worth stating.

**Every check is refusal-shaped, and absence never passes.** A missing
`dependency_groups` key is a refusal, not a pass -- absence of a declaration is
not evidence of absence of dependencies. Likewise an unassigned family split, a
missing attestation, and an empty check list (`all([])` is `True`, which would
seal a dataset nothing looked at).

**The shortcut probe is the one check that can pass for the wrong reason.** §5.4
asks that "label/intent equivalence checks on runtime metadata have no
deterministic shortcut". A fixed probe list cannot prove that no shortcut
exists; it can only show that the obvious ones do not win. So the probe rates are
reported on a pass as well as a failure, and the report is explicit that it
bounds rather than establishes the claim. §5.4 also fixes what may be done with
this report: it "is descriptive and cannot be used to tune the selected
artifact".

Zero LLM calls: every input is a file on disk.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cmd_audit.eval.state_intent import (  # noqa: E402
    FORBIDDEN_RUNTIME_FIELDS,
    STATE_INTENT_SCHEMA_VERSION,
    TEMPLATE_HINT_MARKERS,
)

TIER3_DIR = Path("artifacts/route_a/tier3")
OUTPUT = TIER3_DIR / "tier3_validity_report.json"

RUNTIME_CASES = "tier3_runtime_cases.jsonl"
HIDDEN_INTENTS = "tier3_hidden_intents.jsonl"
FAMILY_MANIFEST = "tier3_family_manifest.json"
BUILD_MANIFEST = "tier3_build_manifest.json"

#: §5.4 item 5. A probe that selects the intent's target above this rate makes
#: the repair findable without predicate reasoning. Preregistered.
SHORTCUT_MAX_HIT_RATE = 0.50

#: The positional and structural rules a program could use *instead of* reading
#: state. Each maps a case's runtime items to one guessed target item ID.
SHORTCUT_PROBES: tuple[str, ...] = (
    "lowest_rank",
    "highest_rank",
    "first_listed",
    "last_listed",
    "longest_text",
    "shortest_text",
    "most_source_events",
    "only_unretrieved",
)

#: §5.1's may-not-read list, as substrings. An attestation naming any of these is
#: a self-reported independence break.
FORBIDDEN_ATTESTATION_RESOURCES = (
    "build_memtrace_kp_cases",
    "build_memfail_cases",
    "stale builder",
    "stale adapter",
    "operators.py",
    "closed_grammar",
    "synthesis output",
    "d_select",
)


class DatasetValidityError(RuntimeError):
    """The dataset could not be read at all."""


@dataclass(frozen=True)
class ValidityCheck:
    """One §5.4 requirement's verdict."""

    name: str
    passed: bool
    detail: str
    measurements: dict[str, float] = field(default_factory=dict)


def check_intent_pairing(cases: list[dict], intents: list[dict]) -> ValidityCheck:
    """§5.4 item 1: exactly one matching hidden intent per runtime case."""
    case_ids = [case.get("case_id") for case in cases]
    intent_counts = collections.Counter(intent.get("case_id") for intent in intents)

    duplicated = sorted(cid for cid, count in intent_counts.items() if count > 1)
    unpaired = sorted(cid for cid in case_ids if intent_counts.get(cid, 0) == 0)
    orphans = sorted(set(intent_counts) - set(case_ids))

    intent_family = {intent.get("case_id"): intent.get("family_id") for intent in intents}
    mismatched = sorted(
        case["case_id"]
        for case in cases
        if case["case_id"] in intent_family
        and case.get("family_id") != intent_family[case["case_id"]]
    )

    problems = []
    if unpaired:
        problems.append(f"{len(unpaired)} case(s) with no intent: {unpaired[:5]}")
    if orphans:
        problems.append(f"{len(orphans)} intent(s) with no case: {orphans[:5]}")
    if duplicated:
        problems.append(f"{len(duplicated)} case(s) with >1 intent: {duplicated[:5]}")
    if mismatched:
        problems.append(f"{len(mismatched)} pair(s) disagree on family_id: {mismatched[:5]}")

    return ValidityCheck(
        name="intent_pairing",
        passed=not problems,
        detail="; ".join(problems) or f"{len(cases)} cases paired one-to-one",
        measurements={"case_count": float(len(cases)), "intent_count": float(len(intents))},
    )


def check_intent_identity_scope(cases: list[dict], intents: list[dict]) -> ValidityCheck:
    """§5.4 item 2: hidden intent refers only to runtime item/event identities.

    Unpaired cases are skipped rather than reported: `check_intent_pairing` owns
    that defect, and raising here would mask which requirement failed.
    """
    by_case = {case.get("case_id"): case for case in cases}
    violations: list[str] = []

    for intent in intents:
        case = by_case.get(intent.get("case_id"))
        if case is None:
            continue
        known = {item.get("item_id") for item in case.get("items", ())}
        named: set[str] = set()
        named.update(
            required.get("source_memory_id")
            for required in intent.get("required_items", ())
        )
        for perturbation in intent.get("perturbations", ()):
            named.add(perturbation.get("target_item_id"))
            named.update(perturbation.get("replacement_item_ids", ()))
        named.update(intent.get("protected_item_ids", ()))
        named.update(intent.get("allowed_added_item_ids", ()))
        for item_id, _ in intent.get("required_provenance_hashes", ()):
            named.add(item_id)

        unknown = sorted(str(value) for value in named - known if value is not None)
        if unknown:
            violations.append(f"{intent.get('case_id')}: {unknown[:5]}")

    return ValidityCheck(
        name="intent_identity_scope",
        passed=not violations,
        detail=(
            f"{len(violations)} intent(s) name non-runtime identities: {violations[:5]}"
            if violations
            else "every intent names only runtime item identities"
        ),
    )


def _walk(value: object):
    """Yield every mapping nested anywhere inside `value`."""
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _walk(nested)


def check_forbidden_fields(cases: list[dict]) -> ValidityCheck:
    """§5.4 item 6 / §3.1: no forbidden field reaches the runtime schema.

    Checked at every nesting depth. A top-level-only check would miss
    `target_item_id` buried inside an item, which is the same leak with one more
    level of indirection.
    """
    found: dict[str, list[str]] = collections.defaultdict(list)
    for case in cases:
        case_id = str(case.get("case_id"))
        for mapping in _walk(case):
            for forbidden in FORBIDDEN_RUNTIME_FIELDS:
                if forbidden in mapping:
                    found[forbidden].append(case_id)

    return ValidityCheck(
        name="forbidden_fields",
        passed=not found,
        detail=(
            "; ".join(
                f"{field_name} in {len(case_ids)} case(s) e.g. {case_ids[0]}"
                for field_name, case_ids in sorted(found.items())
            )
            or f"none of {len(FORBIDDEN_RUNTIME_FIELDS)} forbidden fields present"
        ),
    )


def check_template_hints(cases: list[dict]) -> ValidityCheck:
    """§5.4 item 6 / §5.3: no injector template hint survives into runtime.

    IDs are checked as well as text: §5.3 requires opaque identifiers, so a hint
    in an item_id is as much a leak as one in the item's text.
    """
    found: dict[str, list[str]] = collections.defaultdict(list)
    for case in cases:
        case_id = str(case.get("case_id"))
        for mapping in _walk(case):
            for key in ("item_id", "text", "event_id", "query"):
                value = mapping.get(key)
                if not isinstance(value, str):
                    continue
                for marker in TEMPLATE_HINT_MARKERS:
                    if marker in value:
                        found[marker].append(f"{case_id}.{key}")

    return ValidityCheck(
        name="template_hints",
        passed=not found,
        detail=(
            "; ".join(
                f"{marker!r} in {len(sites)} place(s) e.g. {sites[0]}"
                for marker, sites in sorted(found.items())
            )
            or f"none of {len(TEMPLATE_HINT_MARKERS)} template markers present"
        ),
    )


def check_family_counts(cases: list[dict], *, n_tier3: int) -> ValidityCheck:
    """§5.4 item 7: family counts meet `n_tier3`.

    The requirement is on families, not cases: the confirmatory statistics are
    family-blocked, so 300 cases in 3 families supply three units, not 300.
    """
    families = {case.get("family_id") for case in cases}
    families.discard(None)
    passed = len(families) >= n_tier3
    return ValidityCheck(
        name="family_counts",
        passed=passed,
        detail=(
            f"{len(families)} families >= n_tier3 {n_tier3}"
            if passed
            else f"{len(families)} families, n_tier3 requires {n_tier3}"
        ),
        measurements={"family_count": float(len(families)), "n_tier3": float(n_tier3)},
    )


def check_dependency_groups(family_manifest: dict) -> ValidityCheck:
    """§5.4 item 8: source/sibling groups do not cross D_confirm boundaries.

    An absent `dependency_groups` key is a refusal. Absence of a declaration is
    not evidence of absence of dependencies, and treating it as a pass would let
    a dataset satisfy this requirement by omitting it.
    """
    if "dependency_groups" not in family_manifest:
        return ValidityCheck(
            name="dependency_groups",
            passed=False,
            detail=(
                "family manifest declares no dependency_groups; §5.4 requires "
                "them to be declared so the boundary can be checked"
            ),
        )

    split_by_family = family_manifest.get("split_by_family") or {}
    crossing: list[str] = []
    unassigned: list[str] = []
    for group in family_manifest["dependency_groups"]:
        group_id = str(group.get("group_id"))
        splits = set()
        for family_id in group.get("family_ids", ()):
            split = split_by_family.get(family_id)
            if split is None:
                unassigned.append(f"{group_id}:{family_id}")
            else:
                splits.add(split)
        if len(splits) > 1:
            crossing.append(f"{group_id} spans {sorted(splits)}")

    problems = []
    if crossing:
        problems.append(f"{len(crossing)} group(s) cross a split: {crossing[:5]}")
    if unassigned:
        problems.append(f"{len(unassigned)} family(ies) have no recorded split: {unassigned[:5]}")

    return ValidityCheck(
        name="dependency_groups",
        passed=not problems,
        detail="; ".join(problems)
        or f"{len(family_manifest['dependency_groups'])} group(s) within one split each",
    )


def _probe_target(probe: str, items: list[dict]) -> str | None:
    """The item ID a positional/structural rule would guess, or None."""
    if not items:
        return None
    if probe == "lowest_rank":
        return min(items, key=lambda item: item.get("rank", 0)).get("item_id")
    if probe == "highest_rank":
        return max(items, key=lambda item: item.get("rank", 0)).get("item_id")
    if probe == "first_listed":
        return items[0].get("item_id")
    if probe == "last_listed":
        return items[-1].get("item_id")
    if probe == "longest_text":
        return max(items, key=lambda item: len(item.get("text", ""))).get("item_id")
    if probe == "shortest_text":
        return min(items, key=lambda item: len(item.get("text", ""))).get("item_id")
    if probe == "most_source_events":
        return max(items, key=lambda item: len(item.get("source_event_ids", ()))).get("item_id")
    if probe == "only_unretrieved":
        unretrieved = [item for item in items if not item.get("retrieved", True)]
        return unretrieved[0].get("item_id") if len(unretrieved) == 1 else None
    raise ValueError(f"unregistered probe: {probe}")


def check_no_deterministic_shortcut(
    cases: list[dict], intents: list[dict]
) -> ValidityCheck:
    """§5.4 item 5: no deterministic shortcut from runtime metadata.

    Each probe is a rule a program could use *instead of* reading state. If one
    of them picks the intent's perturbation target more often than
    `SHORTCUT_MAX_HIT_RATE`, the repair is findable by position and the dataset
    does not test predicate reasoning.

    This bounds rather than establishes the claim: a fixed probe list cannot
    prove no shortcut exists. Rates are reported on a pass too, so a near-miss
    is visible.
    """
    targets: dict[str, set[str]] = {}
    for intent in intents:
        case_targets = {
            perturbation.get("target_item_id")
            for perturbation in intent.get("perturbations", ())
        }
        targets[intent.get("case_id")] = {t for t in case_targets if t is not None}

    hits: dict[str, int] = {probe: 0 for probe in SHORTCUT_PROBES}
    scored = 0
    for case in cases:
        case_targets = targets.get(case.get("case_id"))
        if not case_targets:
            continue
        scored += 1
        items = list(case.get("items", ()))
        for probe in SHORTCUT_PROBES:
            if _probe_target(probe, items) in case_targets:
                hits[probe] += 1

    if not scored:
        return ValidityCheck(
            name="no_deterministic_shortcut",
            passed=False,
            detail="no case had a perturbation target to probe against",
        )

    rates = {probe: hits[probe] / scored for probe in SHORTCUT_PROBES}
    worst = max(rates, key=lambda probe: rates[probe])
    passed = rates[worst] <= SHORTCUT_MAX_HIT_RATE
    return ValidityCheck(
        name="no_deterministic_shortcut",
        passed=passed,
        detail=(
            f"probe {worst!r} selects the target at {rates[worst]:.2f} "
            f"(> {SHORTCUT_MAX_HIT_RATE:.2f}) over {scored} cases"
            if not passed
            else f"worst probe {worst!r} at {rates[worst]:.2f} over {scored} cases; "
            f"bounds rather than establishes absence of a shortcut"
        ),
        measurements={probe: rates[probe] for probe in SHORTCUT_PROBES},
    )


def check_injector_attestation(build_manifest: dict) -> ValidityCheck:
    """§5.1's attestation and §5.4 item 3's injector unit tests."""
    attestation = build_manifest.get("attestation")
    if not attestation:
        return ValidityCheck(
            name="injector_attestation",
            passed=False,
            detail="build manifest carries no §5.1 attestation",
        )

    resources = [str(value) for value in attestation.get("resources_read", ())]
    if not resources:
        return ValidityCheck(
            name="injector_attestation",
            passed=False,
            detail="attestation lists no resources read",
        )

    breaches = sorted(
        {
            forbidden
            for forbidden in FORBIDDEN_ATTESTATION_RESOURCES
            for resource in resources
            if forbidden in resource.lower()
        }
    )
    if breaches:
        matched = [
            resource
            for resource in resources
            if any(forbidden in resource.lower() for forbidden in breaches)
        ]
        return ValidityCheck(
            name="injector_attestation",
            passed=False,
            detail=(
                f"attestation declares resources §5.1 forbids: {matched}"
            ),
        )

    if not attestation.get("injector_unit_tests_pass"):
        return ValidityCheck(
            name="injector_attestation",
            passed=False,
            detail=(
                "attestation does not record passing injector unit tests; §5.4 "
                "item 3 requires them to establish the intended corruption"
            ),
        )

    return ValidityCheck(
        name="injector_attestation",
        passed=True,
        detail=f"{len(resources)} declared resource(s), all outside §5.1's exclusions",
    )


def check_schema_versions(cases: list[dict], intents: list[dict]) -> ValidityCheck:
    """The schemas §5.2 freezes must be the ones the evaluator implements."""
    intent_versions = {
        intent.get("schema_version", STATE_INTENT_SCHEMA_VERSION) for intent in intents
    }
    surfaces = {case.get("runtime_surface") for case in cases}
    unexpected_intent = sorted(v for v in intent_versions if v != STATE_INTENT_SCHEMA_VERSION)
    unexpected_surface = sorted(str(s) for s in surfaces if s != "route-a-runtime-v1")

    problems = []
    if unexpected_intent:
        problems.append(f"intent schema_version(s) {unexpected_intent}")
    if unexpected_surface:
        problems.append(f"runtime_surface value(s) {unexpected_surface}")

    return ValidityCheck(
        name="schema_versions",
        passed=not problems,
        detail="; ".join(problems)
        or f"intent {STATE_INTENT_SCHEMA_VERSION}, runtime route-a-runtime-v1",
    )


def seal_decision(checks: list[ValidityCheck]) -> str:
    """§5.4 "before sealing": every check must pass.

    An empty list refuses. `all([])` is `True`, so the conjunction on its own
    would seal a dataset that nothing ever looked at.
    """
    if not checks:
        return "REFUSE"
    return "SEAL" if all(check.passed for check in checks) else "REFUSE"


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise DatasetValidityError(f"{path}:{number}: {error}") from error
    return rows


def load_tier3_dataset(root: Path) -> tuple[list[dict], list[dict], dict, dict]:
    """Read §5.3's four files, or raise naming what is missing."""
    missing = [
        name
        for name in (RUNTIME_CASES, HIDDEN_INTENTS, FAMILY_MANIFEST, BUILD_MANIFEST)
        if not (root / name).is_file()
    ]
    if missing:
        raise DatasetValidityError(
            f"tier-3 dataset incomplete under {root}: missing {missing}. "
            "Injector v2 (§5.1) has not produced this dataset."
        )
    return (
        _read_jsonl(root / RUNTIME_CASES),
        _read_jsonl(root / HIDDEN_INTENTS),
        json.loads((root / FAMILY_MANIFEST).read_text(encoding="utf-8")),
        json.loads((root / BUILD_MANIFEST).read_text(encoding="utf-8")),
    )


def audit(root: Path, *, n_tier3: int) -> dict:
    cases, intents, family_manifest, build_manifest = load_tier3_dataset(root)

    checks = [
        check_intent_pairing(cases, intents),
        check_intent_identity_scope(cases, intents),
        check_forbidden_fields(cases),
        check_template_hints(cases),
        check_schema_versions(cases, intents),
        check_family_counts(cases, n_tier3=n_tier3),
        check_dependency_groups(family_manifest),
        check_no_deterministic_shortcut(cases, intents),
        check_injector_attestation(build_manifest),
    ]

    payload = {
        "state_intent_schema_version": STATE_INTENT_SCHEMA_VERSION,
        "llm_calls": 0,
        "runtime_uses_gold": False,
        "n_tier3": n_tier3,
        "case_count": len(cases),
        "intent_count": len(intents),
        "shortcut_max_hit_rate": SHORTCUT_MAX_HIT_RATE,
        "shortcut_probes": list(SHORTCUT_PROBES),
        "checks": [
            {
                "name": check.name,
                "passed": check.passed,
                "detail": check.detail,
                "measurements": check.measurements,
            }
            for check in checks
        ],
        "decision": seal_decision(checks),
        "report_use_note": (
            "§5.4: the challenge-rate and shortcut-probe report is descriptive "
            "and cannot be used to tune the selected artifact. The probe list "
            "bounds rather than establishes the absence of a shortcut."
        ),
    }
    payload["payload_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Route A §5.4: tier-3 dataset validity audit. Reads §5.3's four "
            "files and decides SEAL or REFUSE. Exits nonzero on REFUSE or when "
            "the dataset is absent. Zero LLM calls."
        )
    )
    parser.add_argument("--root", type=Path, default=TIER3_DIR)
    parser.add_argument(
        "--n-tier3",
        type=int,
        default=None,
        help="required family count; defaults to the §4 power artifact's n_tier3",
    )
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    n_tier3 = args.n_tier3
    if n_tier3 is None:
        power = Path("artifacts/route_a/prereg/tier3_power.json")
        if not power.is_file():
            print(
                "MISSING_POWER_ARTIFACT: run experiments.compute_tier3_power "
                "first, or pass --n-tier3 explicitly"
            )
            return 1
        n_tier3 = int(json.loads(power.read_text(encoding="utf-8"))["n_tier3"])

    try:
        payload = audit(args.root, n_tier3=n_tier3)
    except DatasetValidityError as error:
        print(f"DATASET_UNAVAILABLE: {error}")
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    for check in payload["checks"]:
        mark = "pass" if check["passed"] else "FAIL"
        print(f"  {check['name']:28} {mark}: {check['detail']}")
    print(f"decision = {payload['decision']} (n_tier3 = {n_tier3})")
    print(f"wrote {args.output}")
    return 0 if payload["decision"] == "SEAL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
