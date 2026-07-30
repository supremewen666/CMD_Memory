"""Lifecycle, retrieval, and atomic transitions for Skill evolution.

This module intentionally contains no model calls.  Experiment runners provide
case evaluation and shadow-discovery callbacks; the state transition remains
deterministic and can therefore be tested with zero-LLM fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from statistics import median
from typing import Mapping, Sequence

from .operator_library import (
    AppendOnlyEvolutionStore,
    EVOLUTION_ARMS,
    ExperienceTapeEvent,
    LifecycleEvent,
    LibraryVersion,
    OperatorSpecRecord,
    OperatorWeightSnapshot,
    RevisionAnchorSet,
    RevisionEvidenceEvent,
    SkillRevisionRecord,
    content_id,
    patterned_family_id,
    unkeyed_family_id,
)


RECOVERY_THRESHOLD = 0.1
EXPLOITATION_SLOTS = 3
EXPLORATION_SLOTS = 2
MAX_SKILL_ATTEMPTS = 5


@dataclass(frozen=True)
class ArmCaseOutcome:
    arm_id: str
    case_id: str
    library_version_id: str
    attempted_revision_ids: tuple[str, ...]
    executed_revision_gains: tuple[tuple[str, float, float], ...] = ()
    recovered: bool = False
    recovery_gain: float = 0.0
    library_rollouts: int = 0
    discovery_rollouts: int = 0


@dataclass(frozen=True)
class PromotionDecision:
    eligible: bool
    reason: str
    supporting_evidence_ids: tuple[str, ...] = ()
    validation_case_ids: tuple[str, ...] = ()
    family_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class TransitionResult:
    versions_before: tuple[tuple[str, str], ...]
    versions_after: tuple[tuple[str, str], ...]
    tape_event_id: str | None


def operator_weight(
    success_count: int,
    failure_count: int,
    *,
    quantile: float = 0.05,
) -> float:
    """Return ``Q_quantile(Beta(1+s, 1+f))`` using deterministic bisection."""
    if success_count < 0 or failure_count < 0:
        raise ValueError("success_count and failure_count must be non-negative")
    if not 0.0 < quantile < 1.0:
        raise ValueError("quantile must be between zero and one")
    alpha = 1.0 + success_count
    beta = 1.0 + failure_count
    low, high = 0.0, 1.0
    for _ in range(80):
        middle = (low + high) / 2.0
        if _regularized_beta(middle, alpha, beta) < quantile:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def derive_weight_snapshot(
    store: AppendOnlyEvolutionStore,
    *,
    arm_id: str,
    revision_id: str,
    library_version: str,
) -> OperatorWeightSnapshot:
    """Derive a replayable arm-local posterior from eligible evidence."""
    eligible = [
        item
        for item in store.evidence_for(arm_id, revision_id)
        if item.evidence_kind == "runtime_execution"
        and item.eligible_for_weight
    ]
    # Fail closed if a hand-built ledger violates the one-case rule.
    if len({item.case_id for item in eligible}) != len(eligible):
        raise ValueError("duplicate posterior evidence for revision/case")
    successes = sum(item.binary_success for item in eligible)
    failures = len(eligible) - successes
    payload = {
        "arm_id": arm_id,
        "revision_id": revision_id,
        "library_version": library_version,
        "success_count": successes,
        "failure_count": failures,
        "supporting_evidence_ids": [item.evidence_id for item in eligible],
    }
    return OperatorWeightSnapshot(
        snapshot_id=content_id("weight", payload),
        arm_id=arm_id,
        revision_id=revision_id,
        library_version=library_version,
        success_count=successes,
        failure_count=failures,
        beta_alpha=1 + successes,
        beta_beta=1 + failures,
        weight=operator_weight(successes, failures),
        supporting_evidence_ids=tuple(item.evidence_id for item in eligible),
    )


def make_runtime_evidence(
    *,
    revision: SkillRevisionRecord,
    case_id: str,
    library_version_before_case: str,
    recovery_gain: float,
    rollout_cost: float,
    recurrent_family_id_audit_only: str | None,
) -> RevisionEvidenceEvent:
    """Build the only evidence kind allowed to update Operator Weight."""
    if case_id == revision.producing_case_id:
        raise ValueError("producing case cannot validate its own revision")
    payload = {
        "arm_id": revision.arm_id,
        "revision_id": revision.revision_id,
        "case_id": case_id,
        "library_version_before_case": library_version_before_case,
        "evidence_kind": "runtime_execution",
        "recovery_gain": float(recovery_gain),
        "rollout_cost": float(rollout_cost),
    }
    return RevisionEvidenceEvent(
        evidence_id=content_id("evidence", payload),
        arm_id=revision.arm_id,
        revision_id=revision.revision_id,
        case_id=case_id,
        library_version_before_case=library_version_before_case,
        evidence_kind="runtime_execution",
        recovery_gain=float(recovery_gain),
        binary_success=float(recovery_gain) >= RECOVERY_THRESHOLD,
        rollout_cost=float(rollout_cost),
        eligible_for_weight=True,
        eligible_for_stable_validation=True,
        recurrent_family_id_audit_only=recurrent_family_id_audit_only,
    )


def make_ineligible_evidence(
    *,
    revision: SkillRevisionRecord,
    case_id: str,
    library_version_before_case: str,
    evidence_kind: str,
    recovery_gain: float,
    rollout_cost: float,
    recurrent_family_id_audit_only: str | None = None,
) -> RevisionEvidenceEvent:
    if evidence_kind not in {
        "producing",
        "shadow_verification",
        "anchor_replay",
        "heldout_probe",
    }:
        raise ValueError(f"not an ineligible evidence kind: {evidence_kind}")
    payload = {
        "arm_id": revision.arm_id,
        "revision_id": revision.revision_id,
        "case_id": case_id,
        "library_version_before_case": library_version_before_case,
        "evidence_kind": evidence_kind,
        "recovery_gain": float(recovery_gain),
        "rollout_cost": float(rollout_cost),
    }
    return RevisionEvidenceEvent(
        evidence_id=content_id("evidence", payload),
        arm_id=revision.arm_id,
        revision_id=revision.revision_id,
        case_id=case_id,
        library_version_before_case=library_version_before_case,
        evidence_kind=evidence_kind,
        recovery_gain=float(recovery_gain),
        binary_success=float(recovery_gain) >= RECOVERY_THRESHOLD,
        rollout_cost=float(rollout_cost),
        eligible_for_weight=False,
        eligible_for_stable_validation=False,
        recurrent_family_id_audit_only=recurrent_family_id_audit_only,
    )


def retrieve_revisions(
    store: AppendOnlyEvolutionStore,
    version: LibraryVersion,
    *,
    case_index: int,
    matched_pattern_ids: Sequence[str] = (),
) -> tuple[SkillRevisionRecord, ...]:
    """Apply patterned/unkeyed eligibility and the deterministic 3+2 quota."""
    if version.arm_id == "no_update":
        return ()
    candidates = [
        store.revisions[revision_id]
        for revision_id in version.active_revision_ids
        if revision_id not in version.retired_revision_ids
        and store.revisions[revision_id].created_after_case_index < case_index
    ]
    if version.arm_id == "patterned":
        allowed = set(matched_pattern_ids)
        candidates = [
            item for item in candidates if allowed.intersection(item.pattern_ids)
        ]

    stable_ids = set(version.stable_revision_ids)
    stable = [item for item in candidates if item.revision_id in stable_ids]
    provisional = [item for item in candidates if item.revision_id not in stable_ids]

    def validation_rows(revision_id: str) -> list[RevisionEvidenceEvent]:
        return [
            item
            for item in store.evidence_for(version.arm_id, revision_id)
            if item.evidence_kind == "runtime_execution"
            and item.eligible_for_stable_validation
        ]

    def exploitation_key(item: SkillRevisionRecord) -> tuple[float, float, float, str]:
        snapshot = store.latest_weight(version, item.revision_id)
        rows = validation_rows(item.revision_id)
        weight = snapshot.weight if snapshot is not None else operator_weight(0, 0)
        gains = [row.recovery_gain for row in rows]
        costs = [row.rollout_cost for row in rows]
        return (
            -weight,
            -median(gains) if gains else 0.0,
            median(costs) if costs else math.inf,
            item.revision_id,
        )

    def exploration_key(item: SkillRevisionRecord) -> tuple[int, int, str]:
        return (
            len(validation_rows(item.revision_id)),
            item.created_after_case_index,
            item.revision_id,
        )

    stable.sort(key=exploitation_key)
    provisional.sort(key=exploration_key)
    selected = stable[:EXPLOITATION_SLOTS] + provisional[:EXPLORATION_SLOTS]
    if len(stable) < EXPLOITATION_SLOTS:
        selected.extend(
            provisional[
                EXPLORATION_SLOTS:
                EXPLORATION_SLOTS + (EXPLOITATION_SLOTS - len(stable))
            ]
        )
    if len(provisional) < EXPLORATION_SLOTS:
        selected.extend(
            stable[
                EXPLOITATION_SLOTS:
                EXPLOITATION_SLOTS + (EXPLORATION_SLOTS - len(provisional))
            ]
        )
    deduplicated = tuple(dict.fromkeys(item.revision_id for item in selected))
    if len(deduplicated) > MAX_SKILL_ATTEMPTS:
        raise AssertionError("Skill attempt quota exceeded")
    return tuple(store.revisions[item] for item in deduplicated)


def retrieve_same_library_random_k(
    store: AppendOnlyEvolutionStore,
    version: LibraryVersion,
    *,
    case_index: int,
    k: int,
    seed: int,
    case_id: str,
) -> tuple[SkillRevisionRecord, ...]:
    """Same-library random-k control independent of live realized hit rank."""
    if k < 0 or k > MAX_SKILL_ATTEMPTS:
        raise ValueError("random-k must be between zero and five")
    candidates = [
        store.revisions[revision_id]
        for revision_id in version.active_revision_ids
        if revision_id not in version.retired_revision_ids
        and store.revisions[revision_id].created_after_case_index < case_index
    ]
    candidates.sort(key=lambda item: item.revision_id)
    keyed = sorted(
        candidates,
        key=lambda item: hashlib.sha256(
            f"{seed}\0{case_id}\0{item.revision_id}".encode("utf-8")
        ).hexdigest(),
    )
    return tuple(keyed[:k])


def promotion_decision(
    store: AppendOnlyEvolutionStore,
    revision_id: str,
    *,
    paired_noninferior: bool,
    preserves_incumbent_anchors: bool,
) -> PromotionDecision:
    revision = store.revisions[revision_id]
    rows = [
        item
        for item in store.evidence_for(revision.arm_id, revision_id)
        if item.evidence_kind == "runtime_execution"
        and item.eligible_for_stable_validation
        and item.binary_success
        and item.case_id != revision.producing_case_id
    ]
    unique: list[RevisionEvidenceEvent] = []
    seen_cases: set[str] = set()
    for item in rows:
        if item.case_id not in seen_cases:
            unique.append(item)
            seen_cases.add(item.case_id)
    if len(unique) < 3:
        return PromotionDecision(False, "needs_three_successful_cases")
    selected: tuple[RevisionEvidenceEvent, ...] | None = None
    for end in range(3, len(unique) + 1):
        prefix = tuple(unique[:end])
        families = {
            item.recurrent_family_id_audit_only
            for item in prefix
            if item.recurrent_family_id_audit_only
        }
        if len(families) >= 2:
            selected = prefix[:3] if len({
                item.recurrent_family_id_audit_only for item in prefix[:3]
            }) >= 2 else _earliest_cross_family_three(prefix)
            break
    if selected is None:
        return PromotionDecision(False, "needs_two_recurrence_families")
    if not paired_noninferior:
        return PromotionDecision(False, "paired_noninferiority_failed")
    if not preserves_incumbent_anchors:
        return PromotionDecision(False, "incumbent_anchor_regression")
    return PromotionDecision(
        True,
        "eligible",
        tuple(item.evidence_id for item in selected),
        tuple(item.case_id for item in selected),
        tuple(
            dict.fromkeys(
                item.recurrent_family_id_audit_only
                for item in selected
                if item.recurrent_family_id_audit_only
            )
        ),
    )


def build_revision_anchor_set(
    *,
    store: AppendOnlyEvolutionStore,
    revision_id: str,
    decision: PromotionDecision,
    pre_repair_snapshot_hashes: Mapping[str, str],
    created_at_library_version: str,
) -> RevisionAnchorSet:
    if not decision.eligible or len(decision.validation_case_ids) != 3:
        raise ValueError("anchor creation requires an eligible promotion")
    revision = store.revisions[revision_id]
    case_ids = (revision.producing_case_id,) + decision.validation_case_ids
    try:
        hashes = tuple(pre_repair_snapshot_hashes[item] for item in case_ids)
    except KeyError as exc:
        raise ValueError(f"missing pre-repair snapshot for {exc.args[0]}") from exc
    payload = {
        "stable_revision_id": revision_id,
        "case_ids": case_ids,
        "family_ids": decision.family_ids,
        "creation_outcome_vector": (True, True, True, True),
        "pre_repair_snapshot_hashes": hashes,
        "created_at_library_version": created_at_library_version,
    }
    return RevisionAnchorSet(
        anchor_set_id=content_id("anchor", payload),
        stable_revision_id=revision_id,
        producing_case_id=revision.producing_case_id,
        validation_case_ids=(
            decision.validation_case_ids[0],
            decision.validation_case_ids[1],
            decision.validation_case_ids[2],
        ),
        family_ids_audit_only=decision.family_ids,
        creation_outcome_vector=(True, True, True, True),
        pre_repair_snapshot_hashes=(
            hashes[0], hashes[1], hashes[2], hashes[3]
        ),
        created_at_library_version=created_at_library_version,
    )


def should_retire_provisional(
    store: AppendOnlyEvolutionStore,
    revision_id: str,
) -> bool:
    revision = store.revisions[revision_id]
    rows = [
        item
        for item in store.evidence_for(revision.arm_id, revision_id)
        if item.evidence_kind == "runtime_execution"
        and item.eligible_for_stable_validation
        and item.case_id != revision.producing_case_id
    ]
    return (
        sum(item.binary_success for item in rows) == 0
        and sum(not item.binary_success for item in rows) >= 3
    )


@dataclass
class AnchorRegressionTracker:
    """Track consecutive direct-anchor checkpoint regressions per revision."""

    consecutive: dict[str, int]

    def __init__(self) -> None:
        self.consecutive = {}

    def record_checkpoint(
        self,
        revision_id: str,
        *,
        creation_vector: Sequence[bool],
        replay_vector: Sequence[bool],
    ) -> bool:
        if len(creation_vector) != 4 or len(replay_vector) != 4:
            raise ValueError("anchor checkpoint vectors must contain four cases")
        regressed = any(
            created and not replayed
            for created, replayed in zip(creation_vector, replay_vector)
        )
        self.consecutive[revision_id] = (
            self.consecutive.get(revision_id, 0) + 1 if regressed else 0
        )
        return self.consecutive[revision_id] >= 2


@dataclass(frozen=True)
class RetrievalDisplacementResult:
    case_id: str
    checkpoint: str
    previously_working_revision_ids: tuple[str, ...]
    retrieved_revision_ids: tuple[str, ...]
    retained_within_top_n: bool
    displaced_revision_ids: tuple[str, ...]


class RetrievalDisplacementTracker:
    """Audit forgetting as loss of a working revision from fixed top-N.

    The caller supplies library-stage retrieval results only.  Discovery is
    intentionally absent from this API, so it cannot mask displacement.
    """

    def __init__(self, *, top_n: int = MAX_SKILL_ATTEMPTS) -> None:
        if top_n < 1:
            raise ValueError("top_n must be positive")
        self.top_n = top_n
        self._working: dict[str, tuple[str, ...]] = {}
        self.results: list[RetrievalDisplacementResult] = []

    def record_library_recovery(
        self,
        case_id: str,
        working_revision_ids: Sequence[str],
    ) -> None:
        values = tuple(dict.fromkeys(working_revision_ids))
        if not values:
            raise ValueError("a library recovery needs a working revision")
        self._working.setdefault(case_id, values)

    def tracked_case_ids(self) -> tuple[str, ...]:
        """Return deterministic IDs eligible for library-only checkpoint replay."""
        return tuple(self._working)

    def checkpoint(
        self,
        case_id: str,
        *,
        checkpoint: str,
        retrieved_revision_ids: Sequence[str],
    ) -> RetrievalDisplacementResult:
        if case_id not in self._working:
            raise ValueError(f"{case_id}: no prior library recovery")
        retrieved = tuple(dict.fromkeys(retrieved_revision_ids))[: self.top_n]
        working = self._working[case_id]
        displaced = tuple(item for item in working if item not in retrieved)
        result = RetrievalDisplacementResult(
            case_id=case_id,
            checkpoint=checkpoint,
            previously_working_revision_ids=working,
            retrieved_revision_ids=retrieved,
            retained_within_top_n=len(displaced) < len(working),
            displaced_revision_ids=displaced,
        )
        self.results.append(result)
        return result


def replay_anchor_set(
    anchor: RevisionAnchorSet,
    execute_revision,
) -> tuple[bool, bool, bool, bool]:
    """Directly execute the anchored revision on four frozen snapshots.

    ``execute_revision(revision_id, case_id, snapshot_hash)`` is deliberately
    the only callback.  Retrieval candidates and discovery are not accepted,
    which makes executable regression distinct from retrieval displacement.
    """
    case_ids = (anchor.producing_case_id,) + anchor.validation_case_ids
    results = tuple(
        bool(
            execute_revision(
                anchor.stable_revision_id,
                case_id,
                snapshot_hash,
            )
        )
        for case_id, snapshot_hash in zip(
            case_ids, anchor.pre_repair_snapshot_hashes
        )
    )
    return results[0], results[1], results[2], results[3]


class EvolutionCoordinator:
    """Apply one evaluate-before-update transaction to three isolated arms."""

    def __init__(
        self,
        store: AppendOnlyEvolutionStore,
        *,
        pattern_catalog_hash: str,
    ) -> None:
        self.store = store
        self.pattern_catalog_hash = pattern_catalog_hash
        for arm in EVOLUTION_ARMS:
            if not store._version_order[arm]:
                store.create_empty_library(
                    arm, pattern_catalog_hash=pattern_catalog_hash
                )

    def commit_case(
        self,
        *,
        case_id: str,
        case_index: int,
        outcomes: Mapping[str, ArmCaseOutcome],
        tape: ExperienceTapeEvent | None,
        selected_spec_records: Sequence[OperatorSpecRecord] = (),
        matched_pattern_ids: Sequence[str] = (),
        recurrent_family_id_audit_only: str | None = None,
        read_only_probe: bool = False,
    ) -> TransitionResult:
        """Persist evidence, then commit updates visible only to the next case."""
        if set(outcomes) != set(EVOLUTION_ARMS):
            raise ValueError("all three arm outcomes are required")
        versions = {arm: self.store.head(arm) for arm in EVOLUTION_ARMS}
        for arm, outcome in outcomes.items():
            if outcome.arm_id != arm or outcome.case_id != case_id:
                raise ValueError("outcome arm/case mismatch")
            if outcome.library_version_id != versions[arm].library_version_id:
                raise ValueError("case did not read the frozen pre-update version")
            if len(outcome.attempted_revision_ids) > MAX_SKILL_ATTEMPTS:
                raise ValueError("more than five Skill attempts")
        if read_only_probe:
            if tape is not None or selected_spec_records:
                raise ValueError("held-out/unseen probes cannot create tape or state")
            return TransitionResult(
                versions_before=tuple(
                    (arm, version.library_version_id)
                    for arm, version in versions.items()
                ),
                versions_after=tuple(
                    (arm, version.library_version_id)
                    for arm, version in versions.items()
                ),
                tape_event_id=None,
            )
        if tape is None or tape.case_id != case_id:
            raise ValueError("update-producing case requires its shared tape")
        if not tape.created_after_all_arm_outcomes:
            raise ValueError("tape was created before all outcomes")
        if tuple(item.spec_hash for item in selected_spec_records) != tape.selected_specs:
            raise ValueError("selected tape spec records do not match the event")
        self.store.append_tape(tape)
        for record in selected_spec_records:
            self.store.append_spec(record)

        after: list[tuple[str, str]] = []
        for arm in EVOLUTION_ARMS:
            parent = versions[arm]
            outcome = outcomes[arm]
            active = list(parent.active_revision_ids)
            stable = list(parent.stable_revision_ids)
            retired = list(parent.retired_revision_ids)
            lifecycle_ids = list(parent.lifecycle_event_ids)

            for revision_id, gain, cost in outcome.executed_revision_gains:
                if revision_id not in outcome.attempted_revision_ids:
                    raise ValueError("execution evidence names an unattempted revision")
                revision = self.store.revisions[revision_id]
                evidence = make_runtime_evidence(
                    revision=revision,
                    case_id=case_id,
                    library_version_before_case=parent.library_version_id,
                    recovery_gain=gain,
                    rollout_cost=cost,
                    recurrent_family_id_audit_only=recurrent_family_id_audit_only,
                )
                self.store.append_evidence(evidence)

            if arm != "no_update":
                self._append_tape_revisions(
                    arm_id=arm,
                    parent=parent,
                    case_id=case_id,
                    case_index=case_index,
                    tape=tape,
                    spec_records=selected_spec_records,
                    pattern_ids=matched_pattern_ids,
                    active=active,
                    lifecycle_ids=lifecycle_ids,
                    recurrent_family_id_audit_only=recurrent_family_id_audit_only,
                )
                for revision_id in tuple(active):
                    if (
                        revision_id not in stable
                        and should_retire_provisional(self.store, revision_id)
                    ):
                        active.remove(revision_id)
                        retired.append(revision_id)
                        event_id = content_id(
                            "lifecycle",
                            [arm, revision_id, "retired", case_id],
                        )
                        lifecycle_ids.append(event_id)

            pending_weights: list[OperatorWeightSnapshot] = []
            for revision_id in active:
                snapshot = derive_weight_snapshot(
                    self.store,
                    arm_id=arm,
                    revision_id=revision_id,
                    library_version=parent.library_version_id,
                )
                pending_weights.append(snapshot)
            version = self.store.commit_library_version(
                arm_id=arm,
                parent_version_id=parent.library_version_id,
                effective_after_case_id=case_id,
                pattern_catalog_hash=self.pattern_catalog_hash,
                active_revision_ids=active,
                stable_revision_ids=stable,
                retired_revision_ids=retired,
                weight_snapshot_ids=tuple(
                    item.snapshot_id for item in pending_weights
                ),
                lifecycle_event_ids=lifecycle_ids,
                tape_event_id=tape.tape_event_id,
            )
            for snapshot in pending_weights:
                self.store.append_weight_snapshot(snapshot)
            new_lifecycle_ids = set(lifecycle_ids) - set(parent.lifecycle_event_ids)
            for event_id in new_lifecycle_ids:
                if event_id in self.store.lifecycle_events:
                    continue
                revision_id = next(
                    item
                    for item in set(active + retired)
                    if event_id == content_id(
                        "lifecycle",
                        [
                            arm,
                            item,
                            (
                                "retired"
                                if item in retired
                                else "provisional_active"
                            ),
                            case_id,
                        ],
                    )
                )
                retired_now = revision_id in retired
                self.store.append_lifecycle_event(
                    LifecycleEvent(
                        event_id=event_id,
                        arm_id=arm,
                        revision_id=revision_id,
                        from_state=(
                            "provisional_active" if retired_now else "candidate"
                        ),
                        to_state=(
                            "retired" if retired_now else "provisional_active"
                        ),
                        reason_code=(
                            "three_failures_zero_success"
                            if retired_now
                            else "successful_shadow_discovery"
                        ),
                        supporting_evidence_ids=tuple(
                            item.evidence_id
                            for item in self.store.evidence_for(arm, revision_id)
                        ),
                        effective_library_version=version.library_version_id,
                    )
                )
            after.append((arm, version.library_version_id))
        return TransitionResult(
            versions_before=tuple(
                (arm, versions[arm].library_version_id) for arm in EVOLUTION_ARMS
            ),
            versions_after=tuple(after),
            tape_event_id=tape.tape_event_id,
        )

    def promote_revision(
        self,
        revision_id: str,
        *,
        paired_noninferior: bool,
        preserves_incumbent_anchors: bool,
        pre_repair_snapshot_hashes: Mapping[str, str],
        effective_after_case_id: str,
    ) -> tuple[LibraryVersion, RevisionAnchorSet]:
        """Promote one provisional revision and create its immutable anchors."""
        revision = self.store.revisions[revision_id]
        parent = self.store.head(revision.arm_id)
        if revision_id not in parent.active_revision_ids:
            raise ValueError("only an active provisional revision can promote")
        if revision_id in parent.stable_revision_ids:
            raise ValueError("revision is already stable")
        decision = promotion_decision(
            self.store,
            revision_id,
            paired_noninferior=paired_noninferior,
            preserves_incumbent_anchors=preserves_incumbent_anchors,
        )
        if not decision.eligible:
            raise ValueError(f"promotion blocked: {decision.reason}")
        event_id = content_id(
            "lifecycle",
            [
                revision.arm_id,
                revision_id,
                "stable",
                effective_after_case_id,
                decision.supporting_evidence_ids,
            ],
        )
        version = self.store.commit_library_version(
            arm_id=revision.arm_id,
            parent_version_id=parent.library_version_id,
            effective_after_case_id=effective_after_case_id,
            pattern_catalog_hash=self.pattern_catalog_hash,
            active_revision_ids=parent.active_revision_ids,
            stable_revision_ids=parent.stable_revision_ids + (revision_id,),
            retired_revision_ids=parent.retired_revision_ids,
            weight_snapshot_ids=parent.weight_snapshot_ids,
            lifecycle_event_ids=parent.lifecycle_event_ids + (event_id,),
            tape_event_id=None,
        )
        self.store.append_lifecycle_event(
            LifecycleEvent(
                event_id=event_id,
                arm_id=revision.arm_id,
                revision_id=revision_id,
                from_state="provisional_active",
                to_state="stable",
                reason_code="cross_family_validation_passed",
                supporting_evidence_ids=decision.supporting_evidence_ids,
                effective_library_version=version.library_version_id,
            )
        )
        anchor = build_revision_anchor_set(
            store=self.store,
            revision_id=revision_id,
            decision=decision,
            pre_repair_snapshot_hashes=pre_repair_snapshot_hashes,
            created_at_library_version=version.library_version_id,
        )
        self.store.append_anchor_set(anchor)
        return version, anchor

    def retire_revision(
        self,
        revision_id: str,
        *,
        reason_code: str,
        effective_after_case_id: str,
        supporting_evidence_ids: Sequence[str] = (),
    ) -> LibraryVersion:
        """Soft-retire a provisional or stable revision without deleting it."""
        revision = self.store.revisions[revision_id]
        parent = self.store.head(revision.arm_id)
        if revision_id not in parent.active_revision_ids:
            raise ValueError("only an active revision can retire")
        from_state = (
            "stable"
            if revision_id in parent.stable_revision_ids
            else "provisional_active"
        )
        event_id = content_id(
            "lifecycle",
            [
                revision.arm_id,
                revision_id,
                "retired",
                reason_code,
                effective_after_case_id,
            ],
        )
        version = self.store.commit_library_version(
            arm_id=revision.arm_id,
            parent_version_id=parent.library_version_id,
            effective_after_case_id=effective_after_case_id,
            pattern_catalog_hash=self.pattern_catalog_hash,
            active_revision_ids=tuple(
                item for item in parent.active_revision_ids if item != revision_id
            ),
            stable_revision_ids=tuple(
                item for item in parent.stable_revision_ids if item != revision_id
            ),
            retired_revision_ids=parent.retired_revision_ids + (revision_id,),
            weight_snapshot_ids=parent.weight_snapshot_ids,
            lifecycle_event_ids=parent.lifecycle_event_ids + (event_id,),
            tape_event_id=None,
        )
        self.store.append_lifecycle_event(
            LifecycleEvent(
                event_id=event_id,
                arm_id=revision.arm_id,
                revision_id=revision_id,
                from_state=from_state,
                to_state="retired",
                reason_code=reason_code,
                supporting_evidence_ids=tuple(supporting_evidence_ids),
                effective_library_version=version.library_version_id,
            )
        )
        return version

    def _append_tape_revisions(
        self,
        *,
        arm_id: str,
        parent: LibraryVersion,
        case_id: str,
        case_index: int,
        tape: ExperienceTapeEvent,
        spec_records: Sequence[OperatorSpecRecord],
        pattern_ids: Sequence[str],
        active: list[str],
        lifecycle_ids: list[str],
        recurrent_family_id_audit_only: str | None,
    ) -> None:
        by_hash = {item.spec_hash: item for item in spec_records}
        existing = {
            revision.spec_hash: revision
            for revision in self.store.revisions.values()
            if revision.arm_id == arm_id
        }
        for spec_hash in tape.selected_specs:
            spec_record = by_hash[spec_hash]
            revision = existing.get(spec_hash)
            if revision is None:
                if arm_id == "patterned":
                    if not pattern_ids:
                        raise ValueError("patterned tape update needs a matched Pattern")
                    family_id = patterned_family_id(
                        pattern_ids[0], spec_record.operator_shape_signature
                    )
                    revision_patterns = tuple(dict.fromkeys(pattern_ids))
                else:
                    family_id = unkeyed_family_id(
                        spec_record.operator_shape_signature
                    )
                    revision_patterns = ()
                revision_payload = {
                    "arm_id": arm_id,
                    "family_id": family_id,
                    "pattern_ids": revision_patterns,
                    "spec_hash": spec_hash,
                    "parent_revision_id": None,
                    "derivation_kind": "discovered",
                    "producing_case_id": case_id,
                    "producing_tape_event_id": tape.tape_event_id,
                    "creation_library_version": parent.library_version_id,
                    "created_after_case_index": case_index,
                }
                revision = SkillRevisionRecord(
                    revision_id=content_id("revision", revision_payload),
                    **revision_payload,
                )
                self.store.append_revision(revision)
                existing[spec_hash] = revision
                active.append(revision.revision_id)
                lifecycle_ids.append(
                    content_id(
                        "lifecycle",
                        [arm_id, revision.revision_id, "provisional_active", case_id],
                    )
                )
                evidence_kind = "producing"
            else:
                evidence_kind = "shadow_verification"
            manifest_entry = next(
                item
                for item in tape.candidate_manifest
                if item.spec_hash == spec_hash and item.selected_rank is not None
            )
            self.store.append_evidence(
                make_ineligible_evidence(
                    revision=revision,
                    case_id=case_id,
                    library_version_before_case=parent.library_version_id,
                    evidence_kind=evidence_kind,
                    recovery_gain=manifest_entry.recovery_gain,
                    rollout_cost=manifest_entry.rollout_cost,
                    recurrent_family_id_audit_only=recurrent_family_id_audit_only,
                )
            )


def _earliest_cross_family_three(
    rows: Sequence[RevisionEvidenceEvent],
) -> tuple[RevisionEvidenceEvent, RevisionEvidenceEvent, RevisionEvidenceEvent]:
    for first in range(len(rows)):
        for second in range(first + 1, len(rows)):
            for third in range(second + 1, len(rows)):
                selected = (rows[first], rows[second], rows[third])
                families = {
                    item.recurrent_family_id_audit_only
                    for item in selected
                    if item.recurrent_family_id_audit_only
                }
                if len(families) >= 2:
                    return selected
    raise ValueError("no cross-family triple")


def paired_skill_dominance(
    paired_outcomes: Sequence[tuple[bool, bool, float, float]],
    *,
    preserves_incumbent_anchors: bool,
) -> bool:
    """Return the conservative challenger-over-incumbent dominance decision.

    Rows are ``(challenger_hit, incumbent_hit, challenger_cost,
    incumbent_cost)`` under the same budget.
    """
    if not preserves_incumbent_anchors:
        return False
    challenger_only = sum(
        challenger and not incumbent
        for challenger, incumbent, _challenger_cost, _incumbent_cost
        in paired_outcomes
    )
    incumbent_only = sum(
        incumbent and not challenger
        for challenger, incumbent, _challenger_cost, _incumbent_cost
        in paired_outcomes
    )
    shared = [
        (challenger_cost, incumbent_cost)
        for challenger, incumbent, challenger_cost, incumbent_cost
        in paired_outcomes
        if challenger and incumbent
    ]
    no_higher_shared_cost = all(
        challenger_cost <= incumbent_cost
        for challenger_cost, incumbent_cost in shared
    )
    return (
        challenger_only >= 3
        and incumbent_only == 0
        and no_higher_shared_cost
    )


def _regularized_beta(x: float, alpha: float, beta: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_term = (
        math.lgamma(alpha + beta)
        - math.lgamma(alpha)
        - math.lgamma(beta)
        + alpha * math.log(x)
        + beta * math.log1p(-x)
    )
    scale = math.exp(log_term)
    if x < (alpha + 1.0) / (alpha + beta + 2.0):
        return scale * _beta_continued_fraction(alpha, beta, x) / alpha
    return 1.0 - (
        scale * _beta_continued_fraction(beta, alpha, 1.0 - x) / beta
    )


def _beta_continued_fraction(alpha: float, beta: float, x: float) -> float:
    tiny = 1e-300
    qab = alpha + beta
    qap = alpha + 1.0
    qam = alpha - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    result = d
    for index in range(1, 201):
        twice = 2 * index
        aa = index * (beta - index) * x / (
            (qam + twice) * (alpha + twice)
        )
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        result *= d * c
        aa = -(alpha + index) * (qab + index) * x / (
            (alpha + twice) * (qap + twice)
        )
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        result *= delta
        if abs(delta - 1.0) < 3e-14:
            break
    return result
