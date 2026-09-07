"""Route A E-1: development intent adapter (BUILD SPEC §3.3).

Turns a burned development `ProbeCase` into the sealed `HiddenStateIntent` the
evaluator reads. Intent construction is an audited prerequisite, not a
filtering step: a case that cannot be converted fails its whole domain rather
than being silently dropped, and the constructibility rate must be 1.00 for a
domain to participate in E0, the bridge, variance estimation, or synthesis.

This adapter reads gold evidence and the injector's label, which is exactly
what makes it development-only. On dev data the item-ID templates are perfectly
label-determined (`m_prior`/`m_stale`/`m_reconciled`), so this adapter is close
to the injector's inverse — the reason tier-3 uses an independently implemented
injector and is the only source of external validity (§5.1).
"""

import re
from dataclasses import dataclass
from typing import Any, Iterable

from cmd_audit.counterfactual.repair_state import initial_state_from_runtime_case
from cmd_audit.eval.state_intent import (
    HiddenStateIntent,
    PerturbationIntent,
    RequiredItemIntent,
    runtime_case_from_probe_case,
)

__all__ = [
    "DEFAULT_RESOLUTIONS",
    "IntentConstructionError",
    "IntentCoverageReport",
    "family_id_for_case",
    "build_dev_intent",
    "build_dev_intents",
]

# A superseded or over-merged item is resolved by any disposition that takes it
# out of the active answer path. All four are accepted because the repair is
# judged on state, not on which legal mechanism reached it.
DEFAULT_RESOLUTIONS = ("demoted", "historical", "suppressed", "removed")

# Which non-gold item carries the competing claim, per injected fault.
#
# Content similarity cannot do this job. Measured across all three dev
# datasets, the true competitor's content overlap with gold ranges from 0.03
# (STALE `m_stale`, a long paraphrase sharing almost no wording) to 0.50
# (MemTrace `m_prior`), while genuinely neutral sibling items sit at 0.20 — the
# distributions cross, so no global threshold separates them. Identification
# therefore relies on the label plus the injector's ID convention, which §3.3
# permits for the sealed manifest and which makes this adapter transparently
# the injector's inverse on development data.
_COMPETITOR_ID_MARKERS = {
    "item_stale": ("prior", "stale", "old"),
    "item_conflict": ("prior", "stale", "old", "reconciled", "merged"),
    "granularity_error": ("summary",),
    "retrieval_error": ("kp", "distractor"),
}

# Faults whose memory state is already correct. In a safety/abstention case the
# gold item IS the scope statement ("no record of X"), every other item is
# unrelated but legitimate memory, and the failure happens downstream in
# generation. The correct state repair is to change nothing, so these are
# null-like: they belong to the answer-axis bridge as specificity controls, not
# to the state-repair endpoint. Suppressing the surrounding profile items would
# be collateral damage on valid memory, not a repair.
_PRESERVE_ONLY_LABELS = ("safety_error",)

# Expressibility classes recorded per case so no stratum is silent.
EXPRESSIBILITY_COMPETING_ITEM = "competing_item"
EXPRESSIBILITY_PRESERVE_ONLY = "preserve_only"
EXPRESSIBILITY_NULL = "null"


class IntentConstructionError(ValueError):
    """Raised when a case's gold and items cannot be joined into an intent."""


@dataclass(frozen=True)
class IntentCoverageReport:
    """The §3.3 audit record for one domain."""

    domain: str
    total_runtime_cases: int
    intents_constructed: int
    one_to_one_joins: int
    invalid_cases: tuple[tuple[str, str], ...]
    intent_constructibility_rate: float
    intents: tuple[HiddenStateIntent, ...]

    @property
    def eligible(self) -> bool:
        return self.intent_constructibility_rate >= 1.0

    def as_mapping(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "total_runtime_cases": self.total_runtime_cases,
            "intents_constructed": self.intents_constructed,
            "one_to_one_joins": self.one_to_one_joins,
            "invalid_cases": [
                {"case_id": case_id, "reason_code": reason}
                for case_id, reason in self.invalid_cases
            ],
            "intent_constructibility_rate": self.intent_constructibility_rate,
            "eligible": self.eligible,
        }


def family_id_for_case(case_id: str) -> str:
    """Group paraphrase/variant siblings under one family.

    Sibling variants must stay in the same fold, so the family key strips the
    per-variant suffix each builder appends: `-dimN` for STALE, `-qN` for
    MemFail, and the answer/context variant tail for MemTrace-B, whose IDs look
    like `memtraceb-<corpus>-<kp/scenario>-<variant>-<condition>`.
    """
    if case_id.startswith("memtraceb-"):
        parts = case_id.split("-")
        return "-".join(parts[:3]) if len(parts) >= 3 else case_id
    stripped = re.sub(r"-dim\d+$", "", case_id)
    stripped = re.sub(r"-q\d+$", "", stripped)
    return stripped


def _competitor_ids(
    label: str, gold_ids: list[str], item_ids: Iterable[str]
) -> tuple[str, ...]:
    """Non-gold items carrying a competing claim about the queried slot."""
    markers = _COMPETITOR_ID_MARKERS.get(label, ())
    if not markers:
        return ()
    return tuple(
        memory_id
        for memory_id in item_ids
        if memory_id not in gold_ids
        and any(marker in memory_id.lower() for marker in markers)
    )


def build_dev_intent(
    case: Any,
    *,
    token_budget: int,
    family_id: str | None = None,
) -> HiddenStateIntent:
    """Construct one hidden intent. Raises rather than returning a partial."""
    if not case.gold_evidence:
        raise IntentConstructionError(f"{case.case_id}: no gold evidence")

    items_by_id = {item.memory_id: item for item in case.extracted_memory}
    gold_ids: list[str] = []
    required: list[RequiredItemIntent] = []
    for evidence in case.gold_evidence:
        memory_id = evidence.source_memory_id
        if memory_id is None:
            raise IntentConstructionError(
                f"{case.case_id}: gold {evidence.evidence_id} has no source_memory_id"
            )
        if memory_id not in items_by_id:
            raise IntentConstructionError(
                f"{case.case_id}: gold {evidence.evidence_id} references absent "
                f"item {memory_id}"
            )
        if memory_id in gold_ids:
            continue
        gold_ids.append(memory_id)
        item = items_by_id[memory_id]
        # Casefold matching per the project convention in scoring/phrase.py.
        # Real memfail gold says "beanie" where the item says "Beanies".
        item_text = item.text.casefold()
        phrases = tuple(
            phrase
            for phrase in evidence.required_phrases
            if phrase.casefold() in item_text
        )
        if evidence.required_phrases and not phrases:
            raise IntentConstructionError(
                f"{case.case_id}: gold {evidence.evidence_id} phrases absent from "
                f"item {memory_id}"
            )
        required.append(
            RequiredItemIntent(
                source_memory_id=memory_id,
                required_phrases=phrases,
                allowed_dispositions=("active",),
            )
        )

    # An unlabeled case carries no injected fault, and a preserve-only case's
    # memory state is already correct; for both the right repair is to change
    # nothing, so both are scored under the null-case rule.
    label = case.perturbation_label or ""
    # A safety case is preserve-only only when the safety layer did not actually
    # withhold anything. When it did (`safety_filter_blocked`), gold is missing
    # from the context the generator saw, so the repair must restore it and
    # changing nothing is wrong. Both shapes exist on disk under one label:
    # memtrace's 496 cases block nothing and want abstention; memfail's 157
    # redact gold itself.
    redacted = bool(getattr(case, "safety_filter_blocked", False))
    preserve_only = label in _PRESERVE_ONLY_LABELS and not redacted
    null_case = case.perturbation_label is None or preserve_only

    runtime = runtime_case_from_probe_case(
        case,
        token_budget=token_budget,
        family_id=family_id or family_id_for_case(case.case_id),
    )
    baseline_state = initial_state_from_runtime_case(runtime)
    hashes_by_id = {item.item_id: item.provenance_hash for item in baseline_state.items}
    # Gold the recorded run never surfaced is absent from the initial state, so
    # `preserve_gold` already fails an untouched state and the repair must pull
    # the item in. No perturbation is needed: the fault IS the absence.
    absent_gold = tuple(
        memory_id for memory_id in gold_ids if memory_id not in hashes_by_id
    )

    perturbations: list[PerturbationIntent] = []
    if not null_case and not absent_gold:
        competitors = _competitor_ids(label, gold_ids, items_by_id)
        if not competitors:
            # Never fall through to zero perturbations when gold is already
            # visible: a no-op would then satisfy both endpoints vacuously and
            # score state_success = 1.
            raise IntentConstructionError(
                f"{case.case_id}: no competing item identifiable for label "
                f"{label} (unidentifiable_target)"
            )
        perturbations.extend(
            PerturbationIntent(
                target_item_id=memory_id,
                allowed_resolutions=DEFAULT_RESOLUTIONS,
                replacement_item_ids=tuple(gold_ids),
            )
            for memory_id in competitors
        )
    # Absent gold cannot be protected or hash-bound: there is nothing in the
    # initial state to damage, and the repair supplies the item, so binding the
    # pool item's hash would forbid the addition it is meant to require.
    present_gold = tuple(
        memory_id for memory_id in gold_ids if memory_id in hashes_by_id
    )
    return HiddenStateIntent(
        case_id=case.case_id,
        family_id=runtime.family_id,
        required_items=tuple(required),
        perturbations=tuple(perturbations),
        protected_item_ids=present_gold,
        allowed_added_item_ids=absent_gold,
        required_provenance_hashes=tuple(
            (memory_id, hashes_by_id[memory_id]) for memory_id in present_gold
        ),
        token_budget=token_budget,
        null_case=null_case,
    )


def build_dev_intents(
    cases: Iterable[Any],
    *,
    domain: str,
    token_budget: int,
) -> IntentCoverageReport:
    """Build every intent in a domain and report §3.3 coverage."""
    intents: list[HiddenStateIntent] = []
    invalid: list[tuple[str, str]] = []
    seen_case_ids: set[str] = set()
    duplicate = 0
    total = 0
    for case in cases:
        total += 1
        if case.case_id in seen_case_ids:
            duplicate += 1
            invalid.append((case.case_id, "duplicate_case_id"))
            continue
        seen_case_ids.add(case.case_id)
        try:
            intents.append(build_dev_intent(case, token_budget=token_budget))
        except IntentConstructionError as error:
            invalid.append((case.case_id, _reason_code(str(error))))
    constructed = len(intents)
    return IntentCoverageReport(
        domain=domain,
        total_runtime_cases=total,
        intents_constructed=constructed,
        one_to_one_joins=constructed - duplicate,
        invalid_cases=tuple(invalid),
        intent_constructibility_rate=(constructed / total) if total else 0.0,
        intents=tuple(intents),
    )


def _reason_code(message: str) -> str:
    if "absent item" in message:
        return "gold_item_absent"
    if "phrases absent" in message:
        return "gold_phrases_absent"
    if "no source_memory_id" in message:
        return "gold_without_source_memory_id"
    if "no gold evidence" in message:
        return "no_gold_evidence"
    return "unknown"
