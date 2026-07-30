"""Shared, callback-driven runners for Experiments 24A and 24B.

Model execution is injected through callbacks so transition and leakage
semantics can be exercised without LLM calls.  Remote experiment launchers can
bind the same callbacks to the existing operator executor/discovery scan.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import csv
import hashlib
import json
from pathlib import Path
import random
from typing import Any, Callable, Iterable, Mapping, Sequence

from cmd_audit.eval.evolution_gates import (
    EvolutionGateResults,
    EvolutionProbeOutcome,
    FamilyNetGains,
    FamilySplitEntry,
    build_family_split,
    evaluate_evolution_gates,
    normalized_trapezoid,
    prior_same_family_counts,
    write_family_split,
)
from cmd_audit.repair.evolution import (
    RECOVERY_THRESHOLD,
    AnchorRegressionTracker,
    ArmCaseOutcome,
    EvolutionCoordinator,
    RetrievalDisplacementResult,
    RetrievalDisplacementTracker,
    make_ineligible_evidence,
    promotion_decision,
    retrieve_revisions,
)
from cmd_audit.repair.failure_memory import (
    FrozenPatternCatalog,
    _memory_fingerprint,
    bootstrap_frozen_pattern_catalog,
)
from cmd_audit.repair.operator_library import (
    AppendOnlyEvolutionStore,
    EVOLUTION_ARMS,
    LibraryVersion,
    RevisionAnchorSet,
    SkillRevisionRecord,
    TapeCandidate,
    build_experience_tape,
    canonical_json,
    hash_text,
)


@dataclass(frozen=True)
class ArmEvaluation:
    attempted_revision_ids: tuple[str, ...]
    executed_revision_gains: tuple[tuple[str, float, float], ...]
    recovered: bool
    recovery_gain: float
    library_rollouts: int
    discovery_rollouts: int


@dataclass(frozen=True)
class EvolutionRunResult:
    store: AppendOnlyEvolutionStore
    pattern_catalog: FrozenPatternCatalog
    family_split: tuple[FamilySplitEntry, ...]
    probe_outcomes: tuple[EvolutionProbeOutcome, ...]
    case_outcomes: tuple[dict[str, Any], ...]
    retrieval_displacement: tuple[tuple[str, RetrievalDisplacementResult], ...]
    gate_results: EvolutionGateResults | None
    leakage_assertions: tuple[tuple[str, bool], ...]


@dataclass(frozen=True)
class PrequentialRunResult:
    store: AppendOnlyEvolutionStore
    pattern_catalog: FrozenPatternCatalog
    family_split: tuple[FamilySplitEntry, ...]
    probe_outcomes: tuple[EvolutionProbeOutcome, ...]
    case_outcomes: tuple[dict[str, Any], ...]
    retrieval_displacement: tuple[tuple[str, RetrievalDisplacementResult], ...]
    checkpoint_labels: tuple[str, ...]
    represented_aulc_contrast: float
    prior_same_family_counts: tuple[int, ...]
    leakage_assertions: tuple[tuple[str, bool], ...]


CaseEvaluator = Callable[
    [
        Mapping[str, Any],
        str,
        LibraryVersion,
        Sequence[Any],
    ],
    ArmEvaluation,
]
ShadowDiscoverer = Callable[[Mapping[str, Any]], Sequence[TapeCandidate]]
ProbeEvaluator = Callable[
    [Mapping[str, Any], str, LibraryVersion, Sequence[Any]],
    bool,
]
DirectRevisionEvaluator = Callable[
    [Mapping[str, Any], str, SkillRevisionRecord],
    float,
]


class OfflineEvolutionRunner:
    """Round-based Experiment A over the fixed recurrent-family split."""

    def __init__(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        case_evaluator: CaseEvaluator,
        shadow_discoverer: ShadowDiscoverer,
        probe_evaluator: ProbeEvaluator,
        direct_revision_evaluator: DirectRevisionEvaluator | None = None,
        scorer_version: str,
        discovery_config: Mapping[str, Any],
        threshold: float = 0.1,
        seed: int = 24,
    ) -> None:
        self.rows = tuple(dict(item) for item in rows)
        self.case_evaluator = case_evaluator
        self.shadow_discoverer = shadow_discoverer
        self.probe_evaluator = probe_evaluator
        self.direct_revision_evaluator = direct_revision_evaluator
        self.scorer_version = scorer_version
        self.discovery_config_hash = hash_text(canonical_json(discovery_config))
        self.threshold = threshold
        self.seed = seed
        self.split = build_family_split(self.rows)
        self.split_by_family = {
            item.recurrent_family_id: item for item in self.split
        }
        self.catalog = bootstrap_frozen_pattern_catalog(self.rows)
        self.store = AppendOnlyEvolutionStore()
        for pattern in self.catalog.patterns:
            self.store.append_pattern(pattern)
        self.coordinator = EvolutionCoordinator(
            self.store,
            pattern_catalog_hash=self.catalog.catalog_hash,
        )
        self.rows_by_case_id = {
            str(row["case_id"]): row for row in self.rows
        }
        self.pre_repair_snapshot_hashes = {
            case_id: _pre_repair_snapshot_hash(row)
            for case_id, row in self.rows_by_case_id.items()
        }
        self.anchor_regressions = AnchorRegressionTracker()
        self.displacement_trackers = {
            arm: RetrievalDisplacementTracker()
            for arm in EVOLUTION_ARMS
            if arm != "no_update"
        }
        self.displacement_results: list[
            tuple[str, RetrievalDisplacementResult]
        ] = []
        self.probes_read_only = True
        self.initial_libraries_empty = all(
            not self.store.head(arm).active_revision_ids
            for arm in EVOLUTION_ARMS
        )

    def run(
        self,
        *,
        within_family_gains: Iterable[FamilyNetGains],
        bootstrap_samples: int = 10_000,
    ) -> EvolutionRunResult:
        probes: list[EvolutionProbeOutcome] = []
        cases: list[dict[str, Any]] = []
        update_case_ids: set[str] = set()
        self._evaluate_probes("L0", probes)
        rng = random.Random(self.seed)
        case_index = 0
        for round_index, variant_index in enumerate((0, 1, 2), start=1):
            round_rows = [
                row
                for row in self.rows
                if self._role(row) == "represented"
                and int(row["recurrent_variant_index"]) == variant_index
            ]
            rng.shuffle(round_rows)
            for row in round_rows:
                case_index += 1
                case_id = str(row["case_id"])
                if case_id in update_case_ids:
                    raise ValueError(f"update case repeated: {case_id}")
                update_case_ids.add(case_id)
                fingerprint, matched = self._matched_patterns(row)
                outcomes: dict[str, ArmCaseOutcome] = {}
                outcome_rows: dict[str, ArmEvaluation] = {}
                for arm in EVOLUTION_ARMS:
                    version = self.store.head(arm)
                    revisions = retrieve_revisions(
                        self.store,
                        version,
                        case_index=case_index,
                        matched_pattern_ids=matched,
                    )
                    evaluated = self.case_evaluator(
                        row, arm, version, revisions
                    )
                    outcome_rows[arm] = evaluated
                    outcomes[arm] = ArmCaseOutcome(
                        arm_id=arm,
                        case_id=case_id,
                        library_version_id=version.library_version_id,
                        attempted_revision_ids=evaluated.attempted_revision_ids,
                        executed_revision_gains=evaluated.executed_revision_gains,
                        recovered=evaluated.recovered,
                        recovery_gain=evaluated.recovery_gain,
                        library_rollouts=evaluated.library_rollouts,
                        discovery_rollouts=evaluated.discovery_rollouts,
                    )
                candidates = self.shadow_discoverer(row)
                tape, spec_records = build_experience_tape(
                    case_id=case_id,
                    pre_repair_snapshot_hash=_pre_repair_snapshot_hash(row),
                    discovery_config_hash=self.discovery_config_hash,
                    candidates=candidates,
                    scorer_version=self.scorer_version,
                    threshold=self.threshold,
                    created_after_all_arm_outcomes=True,
                )
                transition = self.coordinator.commit_case(
                    case_id=case_id,
                    case_index=case_index,
                    outcomes=outcomes,
                    tape=tape,
                    selected_spec_records=spec_records,
                    matched_pattern_ids=matched,
                    recurrent_family_id_audit_only=str(
                        row["recurrent_family_id"]
                    ),
                )
                self._record_library_recoveries(case_id, outcome_rows)
                self._promote_eligible_revisions(
                    effective_after_case_id=case_id
                )
                final_versions = {
                    arm: self.store.head(arm).library_version_id
                    for arm in EVOLUTION_ARMS
                }
                for arm, evaluated in outcome_rows.items():
                    cases.append(
                        {
                            "case_id": case_id,
                            "case_index": case_index,
                            "round": round_index,
                            "variant_index": variant_index,
                            "recurrent_family_id": row["recurrent_family_id"],
                            "arm_id": arm,
                            "library_version_before": dict(
                                transition.versions_before
                            )[arm],
                            "library_version_after": final_versions[arm],
                            "fingerprint": fingerprint,
                            **asdict(evaluated),
                        }
                    )
            self._evaluate_probes(f"L{round_index}", probes)
            self._run_checkpoint_audits(f"L{round_index}")
        gates = evaluate_evolution_gates(
            probes,
            within_family_gains=within_family_gains,
            bootstrap_samples=bootstrap_samples,
            bootstrap_seed=self.seed,
        )
        return EvolutionRunResult(
            store=self.store,
            pattern_catalog=self.catalog,
            family_split=self.split,
            probe_outcomes=tuple(probes),
            case_outcomes=tuple(cases),
            retrieval_displacement=tuple(self.displacement_results),
            gate_results=gates,
            leakage_assertions=self._common_leakage_assertions(cases),
        )

    def _evaluate_probes(
        self,
        checkpoint: str,
        sink: list[EvolutionProbeOutcome],
    ) -> None:
        state_before = self._store_state_fingerprint()
        for row in self.rows:
            role = self._role(row)
            variant = int(row["recurrent_variant_index"])
            if role == "represented" and variant not in (3, 4):
                continue
            fingerprint, matched = self._matched_patterns(row)
            del fingerprint
            if role == "unseen" and variant not in (0, 1, 2, 3, 4):
                continue
            # Probe evaluation is read-only and never calls the coordinator.
            for arm in EVOLUTION_ARMS:
                version = self.store.head(arm)
                revisions = retrieve_revisions(
                    self.store,
                    version,
                    case_index=10**12,
                    matched_pattern_ids=matched,
                )
                recovered = self.probe_evaluator(
                    row, arm, version, revisions
                )
                sink.append(
                    EvolutionProbeOutcome(
                        recurrent_family_id=str(row["recurrent_family_id"]),
                        recurrent_variant_index=variant,
                        probe_set=role,
                        checkpoint=checkpoint,
                        arm_id=arm,
                        recovered=bool(recovered),
                        run_seed=self.seed,
                    )
                )
        self.probes_read_only = (
            self.probes_read_only
            and state_before == self._store_state_fingerprint()
        )

    def _role(self, row: Mapping[str, Any]) -> str:
        return self.split_by_family[str(row["recurrent_family_id"])].role

    def _matched_patterns(
        self, row: Mapping[str, Any]
    ) -> tuple[str, tuple[str, ...]]:
        fingerprint = _memory_fingerprint(
            tuple(
                str(item.get("text") or "")
                for item in row.get("extracted_memory", ())
                if isinstance(item, Mapping)
            )
        )
        return fingerprint, tuple(
            item.pattern_id for item in self.catalog.match(fingerprint, top_k=5)
        )

    def _record_library_recoveries(
        self,
        case_id: str,
        outcomes: Mapping[str, ArmEvaluation],
    ) -> None:
        for arm, outcome in outcomes.items():
            tracker = self.displacement_trackers.get(arm)
            if tracker is None:
                continue
            working = tuple(
                revision_id
                for revision_id, gain, _cost in outcome.executed_revision_gains
                if float(gain) >= RECOVERY_THRESHOLD
            )
            if working:
                tracker.record_library_recovery(case_id, working)

    def _promote_eligible_revisions(
        self,
        *,
        effective_after_case_id: str,
    ) -> None:
        """Promote every currently eligible provisional revision.

        The first stable revision in a family has no incumbent comparison.
        Later challengers fail closed unless the direct executor can perform
        paired validation and incumbent-anchor replay.
        """
        for arm in EVOLUTION_ARMS:
            if arm == "no_update":
                continue
            while True:
                parent = self.store.head(arm)
                provisional = [
                    revision_id
                    for revision_id in parent.active_revision_ids
                    if revision_id not in parent.stable_revision_ids
                    and revision_id not in parent.retired_revision_ids
                ]
                promoted = False
                for revision_id in provisional:
                    raw = promotion_decision(
                        self.store,
                        revision_id,
                        paired_noninferior=True,
                        preserves_incumbent_anchors=True,
                    )
                    if not raw.eligible:
                        continue
                    incumbent = self._latest_stable_incumbent(
                        parent, revision_id
                    )
                    paired_noninferior = True
                    preserves_anchors = True
                    if incumbent is not None:
                        if self.direct_revision_evaluator is None:
                            continue
                        paired_noninferior = self._paired_noninferior(
                            revision_id,
                            incumbent,
                            raw.validation_case_ids,
                        )
                        preserves_anchors = self._preserves_incumbent_anchors(
                            revision_id,
                            incumbent,
                        )
                    decision = promotion_decision(
                        self.store,
                        revision_id,
                        paired_noninferior=paired_noninferior,
                        preserves_incumbent_anchors=preserves_anchors,
                    )
                    if not decision.eligible:
                        continue
                    self.coordinator.promote_revision(
                        revision_id,
                        paired_noninferior=paired_noninferior,
                        preserves_incumbent_anchors=preserves_anchors,
                        pre_repair_snapshot_hashes=(
                            self.pre_repair_snapshot_hashes
                        ),
                        effective_after_case_id=effective_after_case_id,
                    )
                    promoted = True
                    break
                if not promoted:
                    break

    def _latest_stable_incumbent(
        self,
        version: LibraryVersion,
        challenger_id: str,
    ) -> str | None:
        family_id = self.store.revisions[challenger_id].family_id
        for revision_id in reversed(version.stable_revision_ids):
            if self.store.revisions[revision_id].family_id == family_id:
                return revision_id
        return None

    def _direct_gain(self, revision_id: str, case_id: str) -> float:
        if self.direct_revision_evaluator is None:
            raise RuntimeError("direct revision evaluator is not configured")
        row = self.rows_by_case_id[case_id]
        revision = self.store.revisions[revision_id]
        expected = self.pre_repair_snapshot_hashes[case_id]
        if _pre_repair_snapshot_hash(row) != expected:
            raise ValueError(f"{case_id}: pre-repair snapshot drift")
        return float(
            self.direct_revision_evaluator(row, revision.arm_id, revision)
        )

    def _paired_noninferior(
        self,
        challenger_id: str,
        incumbent_id: str,
        case_ids: Sequence[str],
    ) -> bool:
        for case_id in case_ids:
            challenger_hit = (
                self._direct_gain(challenger_id, case_id)
                >= RECOVERY_THRESHOLD
            )
            incumbent_hit = (
                self._direct_gain(incumbent_id, case_id)
                >= RECOVERY_THRESHOLD
            )
            if incumbent_hit and not challenger_hit:
                return False
        return True

    def _preserves_incumbent_anchors(
        self,
        challenger_id: str,
        incumbent_id: str,
    ) -> bool:
        anchor = self._anchor_for_revision(incumbent_id)
        if anchor is None:
            return False
        case_ids = (anchor.producing_case_id,) + anchor.validation_case_ids
        if tuple(
            self.pre_repair_snapshot_hashes[case_id]
            for case_id in case_ids
        ) != anchor.pre_repair_snapshot_hashes:
            raise ValueError("incumbent anchor snapshot drift")
        return all(
            not required
            or self._direct_gain(challenger_id, case_id)
            >= RECOVERY_THRESHOLD
            for case_id, required in zip(
                case_ids, anchor.creation_outcome_vector
            )
        )

    def _anchor_for_revision(
        self, revision_id: str
    ) -> RevisionAnchorSet | None:
        return next(
            (
                anchor
                for anchor in self.store.anchor_sets.values()
                if anchor.stable_revision_id == revision_id
            ),
            None,
        )

    def _run_checkpoint_audits(self, checkpoint: str) -> None:
        self._audit_retrieval_displacement(checkpoint)
        if self.direct_revision_evaluator is None:
            return
        stable_ids = [
            revision_id
            for arm in EVOLUTION_ARMS
            if arm != "no_update"
            for revision_id in self.store.head(arm).stable_revision_ids
        ]
        for revision_id in stable_ids:
            anchor = self._anchor_for_revision(revision_id)
            if anchor is None:
                raise AssertionError(
                    f"stable revision lacks anchor set: {revision_id}"
                )
            case_ids = (anchor.producing_case_id,) + anchor.validation_case_ids
            if tuple(
                self.pre_repair_snapshot_hashes[case_id]
                for case_id in case_ids
            ) != anchor.pre_repair_snapshot_hashes:
                raise ValueError(
                    f"anchor snapshot drift: {anchor.anchor_set_id}"
                )
            gains = tuple(
                self._direct_gain(revision_id, case_id)
                for case_id in case_ids
            )
            replay_vector = tuple(
                gain >= RECOVERY_THRESHOLD for gain in gains
            )
            supporting: list[str] = []
            revision = self.store.revisions[revision_id]
            version = self.store.head(revision.arm_id)
            for case_id, gain in zip(case_ids, gains):
                row = self.rows_by_case_id[case_id]
                evidence = make_ineligible_evidence(
                    revision=revision,
                    case_id=case_id,
                    library_version_before_case=version.library_version_id,
                    evidence_kind="anchor_replay",
                    recovery_gain=gain,
                    rollout_cost=1.0,
                    recurrent_family_id_audit_only=str(
                        row["recurrent_family_id"]
                    ),
                )
                self.store.append_evidence(evidence)
                supporting.append(evidence.evidence_id)
            should_retire = self.anchor_regressions.record_checkpoint(
                revision_id,
                creation_vector=anchor.creation_outcome_vector,
                replay_vector=replay_vector,
            )
            if (
                should_retire
                and revision_id
                in self.store.head(revision.arm_id).stable_revision_ids
            ):
                self.coordinator.retire_revision(
                    revision_id,
                    reason_code="two_consecutive_anchor_regressions",
                    effective_after_case_id=f"checkpoint:{checkpoint}",
                    supporting_evidence_ids=supporting,
                )

    def _audit_retrieval_displacement(self, checkpoint: str) -> None:
        for arm, tracker in self.displacement_trackers.items():
            version = self.store.head(arm)
            for case_id in tracker.tracked_case_ids():
                row = self.rows_by_case_id[case_id]
                _fingerprint, matched = self._matched_patterns(row)
                revisions = retrieve_revisions(
                    self.store,
                    version,
                    case_index=10**12,
                    matched_pattern_ids=matched,
                )
                result = tracker.checkpoint(
                    case_id,
                    checkpoint=checkpoint,
                    retrieved_revision_ids=tuple(
                        item.revision_id for item in revisions
                    ),
                )
                self.displacement_results.append((arm, result))

    def _common_leakage_assertions(
        self,
        case_rows: Sequence[Mapping[str, Any]],
    ) -> tuple[tuple[str, bool], ...]:
        reads_pre_update = all(
            self.store.library_versions[
                str(row["library_version_before"])
            ].effective_after_case_id
            != str(row["case_id"])
            for row in case_rows
        )
        tape_reaches_all = all(
            all(
                any(
                    version.arm_id == arm
                    and version.tape_event_id == tape.tape_event_id
                    and version.effective_after_case_id == tape.case_id
                    for version in self.store.library_versions.values()
                )
                for arm in EVOLUTION_ARMS
            )
            for tape in self.store.tape_events.values()
        )
        pattern_frozen = all(
            version.pattern_catalog_hash == self.catalog.catalog_hash
            for version in self.store.library_versions.values()
        )
        return (
            ("case_reads_pre_update_version", reads_pre_update),
            ("heldout_and_unseen_are_read_only", self.probes_read_only),
            ("same_tape_reaches_all_arms", tape_reaches_all),
            (
                "no_update_active_set_empty",
                not self.store.head("no_update").active_revision_ids,
            ),
            ("pattern_catalog_frozen", pattern_frozen),
        )

    def _store_state_fingerprint(self) -> str:
        """Hash every append-only collection, not only the current heads."""
        payload = {
            "patterns": sorted(self.store.patterns),
            "specs": sorted(self.store.specs),
            "revisions": sorted(self.store.revisions),
            "tapes": sorted(self.store.tape_events),
            "evidence": sorted(self.store.evidence),
            "weights": sorted(self.store.weight_snapshots),
            "anchors": sorted(self.store.anchor_sets),
            "lifecycle": sorted(self.store.lifecycle_events),
            "versions": sorted(self.store.library_versions),
            "heads": {
                arm: self.store.head(arm).library_version_id
                for arm in EVOLUTION_ARMS
            },
        }
        return hashlib.sha256(
            canonical_json(payload).encode("utf-8")
        ).hexdigest()


class PrequentialEvolutionRunner(OfflineEvolutionRunner):
    """Strict evaluate-before-update Experiment B.

    A fresh instance always starts from empty Skill Libraries.  Construction
    fails unless both Experiment A Gates have passed.
    """

    def __init__(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        experiment_a_primary_passed: bool,
        experiment_a_safety_passed: bool,
        case_evaluator: CaseEvaluator,
        shadow_discoverer: ShadowDiscoverer,
        probe_evaluator: ProbeEvaluator,
        direct_revision_evaluator: DirectRevisionEvaluator | None = None,
        scorer_version: str,
        discovery_config: Mapping[str, Any],
        threshold: float = 0.1,
        seed: int = 24,
    ) -> None:
        if not experiment_a_primary_passed or not experiment_a_safety_passed:
            raise RuntimeError(
                "Experiment B is disabled until both Experiment A Gates pass"
            )
        self.experiment_a_primary_passed = bool(
            experiment_a_primary_passed
        )
        self.experiment_a_safety_passed = bool(
            experiment_a_safety_passed
        )
        super().__init__(
            rows,
            case_evaluator=case_evaluator,
            shadow_discoverer=shadow_discoverer,
            probe_evaluator=probe_evaluator,
            direct_revision_evaluator=direct_revision_evaluator,
            scorer_version=scorer_version,
            discovery_config=discovery_config,
            threshold=threshold,
            seed=seed,
        )

    def run_prequential(self) -> PrequentialRunResult:
        probes: list[EvolutionProbeOutcome] = []
        case_rows: list[dict[str, Any]] = []
        stream = [
            row
            for row in self.rows
            if self._role(row) == "represented"
            and int(row["recurrent_variant_index"]) in (0, 1, 2)
        ]
        random.Random(self.seed).shuffle(stream)
        targets = {
            max(1, (index * len(stream) + 7) // 8): index
            for index in range(1, 9)
        }
        labels = ["B0"]
        self._evaluate_probes("B0", probes)
        closed_case_ids: set[str] = set()
        ordered_families: list[str] = []
        for case_index, row in enumerate(stream, start=1):
            case_id = str(row["case_id"])
            if case_id in closed_case_ids:
                raise ValueError(f"prequential case repeated: {case_id}")
            fingerprint, matched = self._matched_patterns(row)
            outcomes: dict[str, ArmCaseOutcome] = {}
            evaluated_by_arm: dict[str, ArmEvaluation] = {}
            for arm in EVOLUTION_ARMS:
                version = self.store.head(arm)
                revisions = retrieve_revisions(
                    self.store,
                    version,
                    case_index=case_index,
                    matched_pattern_ids=matched,
                )
                evaluated = self.case_evaluator(row, arm, version, revisions)
                evaluated_by_arm[arm] = evaluated
                outcomes[arm] = ArmCaseOutcome(
                    arm_id=arm,
                    case_id=case_id,
                    library_version_id=version.library_version_id,
                    attempted_revision_ids=evaluated.attempted_revision_ids,
                    executed_revision_gains=evaluated.executed_revision_gains,
                    recovered=evaluated.recovered,
                    recovery_gain=evaluated.recovery_gain,
                    library_rollouts=evaluated.library_rollouts,
                    discovery_rollouts=evaluated.discovery_rollouts,
                )
            # The online numerator is now closed before delayed feedback exists.
            closed_case_ids.add(case_id)
            ordered_families.append(str(row["recurrent_family_id"]))
            candidates = self.shadow_discoverer(row)
            tape, records = build_experience_tape(
                case_id=case_id,
                pre_repair_snapshot_hash=_pre_repair_snapshot_hash(row),
                discovery_config_hash=self.discovery_config_hash,
                candidates=candidates,
                scorer_version=self.scorer_version,
                threshold=self.threshold,
                created_after_all_arm_outcomes=True,
            )
            transition = self.coordinator.commit_case(
                case_id=case_id,
                case_index=case_index,
                outcomes=outcomes,
                tape=tape,
                selected_spec_records=records,
                matched_pattern_ids=matched,
                recurrent_family_id_audit_only=str(
                    row["recurrent_family_id"]
                ),
            )
            self._record_library_recoveries(case_id, evaluated_by_arm)
            self._promote_eligible_revisions(
                effective_after_case_id=case_id
            )
            final_versions = {
                arm: self.store.head(arm).library_version_id
                for arm in EVOLUTION_ARMS
            }
            for arm, evaluated in evaluated_by_arm.items():
                case_rows.append(
                    {
                        "case_id": case_id,
                        "case_index": case_index,
                        "recurrent_family_id": row["recurrent_family_id"],
                        "prior_same_family_count": ordered_families[:-1].count(
                            str(row["recurrent_family_id"])
                        ),
                        "arm_id": arm,
                        "library_version_before": dict(
                            transition.versions_before
                        )[arm],
                        "library_version_after": final_versions[arm],
                        "fingerprint": fingerprint,
                        **asdict(evaluated),
                    }
                )
            if case_index in targets:
                label = f"B{targets[case_index]}"
                if label not in labels:
                    labels.append(label)
                    self._evaluate_probes(label, probes)
                    self._run_checkpoint_audits(label)
        if labels[-1] != "B8":
            labels.append("B8")
            self._evaluate_probes("B8", probes)
            self._run_checkpoint_audits("B8")
        contrast = represented_probe_aulc_contrast(probes, labels)
        return PrequentialRunResult(
            store=self.store,
            pattern_catalog=self.catalog,
            family_split=self.split,
            probe_outcomes=tuple(probes),
            case_outcomes=tuple(case_rows),
            retrieval_displacement=tuple(self.displacement_results),
            checkpoint_labels=tuple(labels),
            represented_aulc_contrast=contrast,
            prior_same_family_counts=prior_same_family_counts(ordered_families),
            leakage_assertions=(
                (
                    "experiment_a_gated",
                    bool(
                        self.experiment_a_primary_passed
                        and self.experiment_a_safety_passed
                    ),
                ),
                (
                    "starts_from_empty_libraries",
                    self.initial_libraries_empty,
                ),
                *self._common_leakage_assertions(case_rows),
                (
                    "producing_case_never_recounted",
                    len(closed_case_ids) == len(stream)
                    and not closed_case_ids.intersection(
                        str(row["case_id"])
                        for row in self.rows
                        if self._role(row) == "unseen"
                        or int(row["recurrent_variant_index"]) in (3, 4)
                    ),
                ),
            ),
        )


def represented_probe_aulc_contrast(
    outcomes: Sequence[EvolutionProbeOutcome],
    checkpoint_labels: Sequence[str],
) -> float:
    values: dict[tuple[str, str], list[float]] = {}
    for item in outcomes:
        if item.probe_set != "represented":
            continue
        values.setdefault((item.arm_id, item.checkpoint), []).append(
            float(item.recovered)
        )
    patterned = [
        sum(values[("patterned", checkpoint)])
        / len(values[("patterned", checkpoint)])
        for checkpoint in checkpoint_labels
    ]
    no_update = [
        sum(values[("no_update", checkpoint)])
        / len(values[("no_update", checkpoint)])
        for checkpoint in checkpoint_labels
    ]
    return normalized_trapezoid(patterned) - normalized_trapezoid(no_update)


def write_run_artifacts(
    result: EvolutionRunResult,
    output_dir: str | Path,
    *,
    run_manifest: Mapping[str, Any],
) -> Path:
    """Write contract artifacts; Parquet is used when pyarrow is available."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    result.store.write_jsonl_artifacts(root)
    write_family_split(root / "family_split.json", result.family_split)
    (root / "pattern_catalog.json").write_text(
        json.dumps(result.pattern_catalog.to_json(), indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    (root / "run_manifest.json").write_text(
        json.dumps(dict(run_manifest), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "leakage_assertions.json").write_text(
        json.dumps(dict(result.leakage_assertions), indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    _write_retrieval_displacement(root, result.retrieval_displacement)
    if result.gate_results is not None:
        (root / "gate_results.json").write_text(
            json.dumps(asdict(result.gate_results), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        with (root / "checkpoint_metrics.csv").open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.writer(handle)
            writer.writerow(("probe_set", "checkpoint", "arm_id", "recovery"))
            writer.writerows(result.gate_results.checkpoint_rates)
    _write_case_outcomes(root, result.case_outcomes)
    return root


def write_prequential_artifacts(
    result: PrequentialRunResult,
    output_dir: str | Path,
    *,
    run_manifest: Mapping[str, Any],
    permutation_contrasts: Sequence[float] = (),
    permutation_p: float | None = None,
) -> Path:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    result.store.write_jsonl_artifacts(root)
    write_family_split(root / "family_split.json", result.family_split)
    (root / "pattern_catalog.json").write_text(
        json.dumps(result.pattern_catalog.to_json(), indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    (root / "run_manifest.json").write_text(
        json.dumps(dict(run_manifest), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "leakage_assertions.json").write_text(
        json.dumps(dict(result.leakage_assertions), indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    _write_retrieval_displacement(root, result.retrieval_displacement)
    (root / "gate_results.json").write_text(
        json.dumps(
            {
                "represented_aulc_contrast": result.represented_aulc_contrast,
                "permutation_contrasts": list(permutation_contrasts),
                "permutation_p_one_sided": permutation_p,
                "claim_boundary": "verified-feedback prequential online simulation",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    rates: dict[tuple[str, str, str], list[float]] = {}
    for item in result.probe_outcomes:
        rates.setdefault(
            (item.probe_set, item.checkpoint, item.arm_id), []
        ).append(float(item.recovered))
    with (root / "checkpoint_metrics.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(("probe_set", "checkpoint", "arm_id", "recovery"))
        for key in sorted(rates):
            writer.writerow((*key, sum(rates[key]) / len(rates[key])))
    _write_case_outcomes(root, result.case_outcomes)
    return root


def _write_case_outcomes(
    root: Path,
    rows: Sequence[Mapping[str, Any]],
) -> None:
    try:
        import pandas as pd

        normalized = [
            {
                key: canonical_json(value)
                if isinstance(value, (dict, list, tuple))
                else value
                for key, value in row.items()
            }
            for row in rows
        ]
        pd.DataFrame(normalized).to_parquet(
            root / "case_outcomes.parquet",
            index=False,
        )
    except (ImportError, ModuleNotFoundError):
        # Keep a truthful artifact rather than writing CSV bytes under a
        # misleading .parquet extension.  Release runs install pandas+pyarrow.
        (root / "case_outcomes.jsonl").write_text(
            "".join(canonical_json(row) + "\n" for row in rows),
            encoding="utf-8",
        )


def _write_retrieval_displacement(
    root: Path,
    rows: Sequence[tuple[str, RetrievalDisplacementResult]],
) -> None:
    (root / "retrieval_displacement.jsonl").write_text(
        "".join(
            canonical_json({"arm_id": arm_id, **asdict(result)}) + "\n"
            for arm_id, result in rows
        ),
        encoding="utf-8",
    )


def _pre_repair_snapshot_hash(row: Mapping[str, Any]) -> str:
    prohibited = {
        "gold_answer",
        "gold_evidence",
        "expected_fault",
        "perturbation_label",
        "recurrent_family_id",
        "recurrent_variant_index",
    }
    payload = {
        key: value for key, value in row.items() if key not in prohibited
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
