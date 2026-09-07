"""Route A E-1: sealed state evaluator, `STATE_FITNESS_V1` (BUILD SPEC §3.4-§3.6).

This is the fitness function that removes the LLM judge from the selection
loop. It is deterministic and costs zero LLM calls: the probe builders wrote
`gold_evidence.source_memory_id` and `required_phrases`, so item-level ground
truth already exists constructively and repair quality can be measured as
state distance rather than answer quality.

`state_success` is the single primary endpoint. Component values are secondary
diagnostics; per §3.7 no component may replace `state_success` after data
inspection.

The seal has two parts. First, `HiddenStateIntent` never reaches a synthesized
program — only `evaluate_state` joins state and intent. Second, a synthesis run
receives `BatchFitnessFeedback` aggregates, never `StateFitnessVector`, so the
proposer cannot read per-case failures, matched gold locations, or case IDs.
"""

from dataclasses import dataclass

from cmd_audit.counterfactual.repair_state import RepairState
from cmd_audit.eval.state_intent import HiddenStateIntent

__all__ = [
    "STATE_FITNESS_VERSION",
    "StateFitnessVector",
    "BatchFitnessFeedback",
    "evaluate_state",
    "summarize_batch_fitness",
]

STATE_FITNESS_VERSION = "state-fitness-v1"


@dataclass(frozen=True)
class StateFitnessVector:
    case_id: str
    preserve_gold: float
    resolve_perturbation: float
    collateral_count: int
    provenance_valid: bool
    budget_valid: bool
    trace_valid: bool
    state_success: int
    failure_reasons: tuple[str, ...]


@dataclass(frozen=True)
class BatchFitnessFeedback:
    """The only channel from the evaluator to a synthesis proposer (§3.6)."""

    batch_id: str
    cases: int
    families: int
    preserve_gold_rate: float
    resolution_rate: float
    mean_collateral: float
    provenance_pass_rate: float
    budget_pass_rate: float
    state_success_rate: float


def _score_preserve_gold(
    state: RepairState, intent: HiddenStateIntent
) -> tuple[float, list[str]]:
    """Required items must survive with content and disposition intact."""
    if not intent.required_items:
        return 1.0, []
    by_id = {item.item_id: item for item in state.items}
    reasons: list[str] = []
    passed = 0
    for required in intent.required_items:
        item = by_id.get(required.source_memory_id)
        if item is None:
            reasons.append(f"gold_missing:{required.source_memory_id}")
            continue
        # Casefold per the project convention in scoring/phrase.py, which the
        # intent adapter also uses when admitting a phrase.
        item_text = item.text.casefold()
        if any(
            phrase.casefold() not in item_text
            for phrase in required.required_phrases
        ):
            reasons.append(f"gold_content:{required.source_memory_id}")
            continue
        if item.disposition not in required.allowed_dispositions:
            reasons.append(f"gold_disposition:{required.source_memory_id}")
            continue
        passed += 1
    return passed / len(intent.required_items), reasons


def _score_resolution(
    state: RepairState, intent: HiddenStateIntent
) -> tuple[float, list[str]]:
    """A perturbation resolves only under a disposition the intent allows.

    An untouched perturbed item stays `active`, so abstention scores 0 here
    even though it passes every contract gate — this is what stops
    `abstain_preserve` from weakly dominating active operators.
    """
    if not intent.perturbations:
        return 1.0, []
    by_id = {item.item_id: item for item in state.items}
    reasons: list[str] = []
    resolved = 0
    for perturbation in intent.perturbations:
        item = by_id.get(perturbation.target_item_id)
        if item is None:
            # The target is gone. That resolves the fault if the intent allows
            # outright removal, or if a named replacement now carries the slot.
            replaced = any(
                replacement in by_id
                and by_id[replacement].disposition == "active"
                for replacement in perturbation.replacement_item_ids
            )
            if "removed" in perturbation.allowed_resolutions or replaced:
                resolved += 1
            else:
                reasons.append(f"perturbation_removed:{perturbation.target_item_id}")
            continue
        # The target is still in state, so only its own disposition can resolve
        # it. A replacement being present is not sufficient: real intents name
        # the gold item as the replacement and gold is active from the start,
        # so crediting that would make every no-op a success.
        if item.disposition in perturbation.allowed_resolutions:
            resolved += 1
            continue
    rate = resolved / len(intent.perturbations)
    if rate < 1.0:
        reasons.append("perturbation_unresolved")
    return rate, reasons


def _score_collateral(
    state: RepairState, intent: HiddenStateIntent
) -> tuple[int, list[str]]:
    """Protected items damaged, plus additions the intent did not authorize."""
    reasons: list[str] = []
    count = 0
    by_id = {item.item_id: item for item in state.items}
    protected_hashes = dict(intent.required_provenance_hashes)
    for protected_id in intent.protected_item_ids:
        item = by_id.get(protected_id)
        if item is None:
            count += 1
            reasons.append(f"protected_removed:{protected_id}")
            continue
        expected_hash = protected_hashes.get(protected_id)
        if expected_hash is not None and item.provenance_hash != expected_hash:
            count += 1
            reasons.append(f"protected_rewritten:{protected_id}")
            continue
        if any(
            event.action == "rewrite" and protected_id in event.matched_item_ids
            for event in state.trace
        ):
            count += 1
            reasons.append(f"protected_rewritten:{protected_id}")
    known_ids = {
        required.source_memory_id for required in intent.required_items
    } | {
        perturbation.target_item_id for perturbation in intent.perturbations
    } | set(intent.protected_item_ids) | set(intent.allowed_added_item_ids)
    for event in state.trace:
        if event.action != "add":
            continue
        for added_id in event.matched_item_ids:
            if added_id in intent.allowed_added_item_ids or added_id in known_ids:
                continue
            count += 1
            reasons.append(f"unauthorized_addition:{added_id}")
    return count, reasons


def _score_provenance(
    state: RepairState, intent: HiddenStateIntent
) -> tuple[bool, list[str]]:
    by_id = {item.item_id: item for item in state.items}
    reasons: list[str] = []
    for item_id, expected_hash in intent.required_provenance_hashes:
        item = by_id.get(item_id)
        if item is None:
            reasons.append(f"provenance_missing:{item_id}")
            continue
        if item.provenance_hash != expected_hash:
            reasons.append(f"provenance_mismatch:{item_id}")
    return not reasons, reasons


def _score_trace(state: RepairState) -> tuple[bool, list[str]]:
    """Every state difference must be explained by a legal trace event.

    A trace is valid when it is either empty with no modification recorded, or
    chained: each event's `before_hash` equals the previous event's
    `after_hash`, and the final `after_hash` equals the state's own hash. A
    state whose items were altered without a corresponding event fails.
    """
    if not state.trace:
        # An empty trace is only consistent with a pristine state. Any
        # disposition already moved off "active" is an unexplained difference.
        untraced = tuple(
            item.item_id for item in state.items if item.disposition != "active"
        )
        if untraced:
            return False, [f"untraced_difference:{untraced[0]}"]
        return True, []
    reasons: list[str] = []
    for previous, current in zip(state.trace, state.trace[1:]):
        if current.before_hash != previous.after_hash:
            reasons.append(f"trace_broken:{current.operator_node_id}")
    if state.trace[-1].after_hash != state.state_hash:
        reasons.append("trace_head_mismatch")
    return not reasons, reasons


def _null_case_untouched(state: RepairState) -> tuple[bool, list[str]]:
    if state.trace:
        return False, ["null_case_modified"]
    if any(item.disposition != "active" for item in state.items):
        return False, ["null_case_modified"]
    return True, []


def evaluate_state(
    state: RepairState,
    intent: HiddenStateIntent,
) -> StateFitnessVector:
    """`STATE_FITNESS_V1`. Deterministic, zero LLM calls."""
    preserve_gold, gold_reasons = _score_preserve_gold(state, intent)
    resolution, resolution_reasons = _score_resolution(state, intent)
    collateral_count, collateral_reasons = _score_collateral(state, intent)
    provenance_valid, provenance_reasons = _score_provenance(state, intent)
    trace_valid, trace_reasons = _score_trace(state)
    budget_valid = state.token_count <= intent.token_budget

    reasons = list(gold_reasons)
    reasons.extend(resolution_reasons)
    reasons.extend(collateral_reasons)
    reasons.extend(provenance_reasons)
    reasons.extend(trace_reasons)
    if not budget_valid:
        reasons.append("budget_exceeded")

    hard_gates_pass = (
        collateral_count == 0 and provenance_valid and budget_valid and trace_valid
    )
    if intent.null_case:
        untouched, null_reasons = _null_case_untouched(state)
        reasons.extend(null_reasons)
        state_success = 1 if (untouched and hard_gates_pass) else 0
    else:
        state_success = (
            1
            if (
                preserve_gold == 1.0
                and resolution == 1.0
                and hard_gates_pass
            )
            else 0
        )

    # Deduplicate while preserving first-seen order so verdicts are stable.
    ordered_reasons = tuple(dict.fromkeys(reasons))
    return StateFitnessVector(
        case_id=state.case_id,
        preserve_gold=preserve_gold,
        resolve_perturbation=resolution,
        collateral_count=collateral_count,
        provenance_valid=provenance_valid,
        budget_valid=budget_valid,
        trace_valid=trace_valid,
        state_success=state_success,
        failure_reasons=ordered_reasons,
    )


def summarize_batch_fitness(
    batch_id: str,
    vectors: tuple[StateFitnessVector, ...],
    *,
    families: tuple[str, ...],
) -> BatchFitnessFeedback:
    """Collapse per-case verdicts into the only feedback a proposer may see."""
    if not vectors:
        raise ValueError("batch feedback requires at least one case")
    count = len(vectors)
    return BatchFitnessFeedback(
        batch_id=batch_id,
        cases=count,
        families=len(set(families)),
        preserve_gold_rate=sum(v.preserve_gold for v in vectors) / count,
        resolution_rate=sum(v.resolve_perturbation for v in vectors) / count,
        mean_collateral=sum(v.collateral_count for v in vectors) / count,
        provenance_pass_rate=sum(1 for v in vectors if v.provenance_valid) / count,
        budget_pass_rate=sum(1 for v in vectors if v.budget_valid) / count,
        state_success_rate=sum(v.state_success for v in vectors) / count,
    )
