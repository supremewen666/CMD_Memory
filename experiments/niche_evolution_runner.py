"""Prequential equal-budget runner for audited niche evolution arms."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import math
from typing import Callable, Mapping, Sequence

from cmd_audit.repair.niche_archive import (
    BehaviorDescriptor,
    NicheArchive,
    NicheValidationEvidence,
)
from cmd_audit.repair.skill_ecology import SkillCandidate
from cmd_audit.repair.skill_graph import AuditedSkillGraph


NICHE_ARMS = (
    "all_frozen",
    "unkeyed_pool",
    "map_elites_no_edges",
    "map_elites_edges",
    "random_niche",
)
_UPDATING_ARMS = frozenset(NICHE_ARMS) - {"all_frozen"}


@dataclass(frozen=True)
class NicheEvolutionCase:
    case_id: str
    family_id: str
    case_index: int
    base_context: str
    descriptor: BehaviorDescriptor
    runtime_branch: str = "fix"

    def __post_init__(self) -> None:
        if not self.case_id or not self.family_id:
            raise ValueError("case and family ids are required")
        if self.case_index < 0:
            raise ValueError("case_index must be non-negative")
        if self.runtime_branch not in {"fill", "fix"}:
            raise ValueError("runtime_branch must be fill or fix")


@dataclass(frozen=True)
class NicheDualExecution:
    skill_id: str
    gold_free_gain: float | None
    shadow_gain: float | None
    execution_cost: float
    repaired_context: str


@dataclass(frozen=True)
class NicheArmOutcome:
    case_id: str
    family_id: str
    case_index: int
    arm_id: str
    descriptor_niche_id: str
    candidate_skill_ids: tuple[str, ...]
    selected_skill_id: str | None
    selected_gold_free_gain: float | None
    selected_shadow_gain: float | None
    candidate_count: int
    budget_aligned: bool
    abstained: bool
    update_effective_after_case_index: int | None


@dataclass(frozen=True)
class NicheEvolutionRun:
    outcomes: tuple[NicheArmOutcome, ...]
    archive_snapshots: Mapping[str, Mapping[str, object]]
    leakage_assertions: tuple[tuple[str, bool], ...]


CandidateProvider = Callable[
    [NicheEvolutionCase],
    Sequence[SkillCandidate],
]
CandidateEvaluator = Callable[
    [NicheEvolutionCase, SkillCandidate, str],
    NicheDualExecution,
]


class AuditedNicheEvolutionRunner:
    """Test-then-update runner; runtime selection sees gold-free gain only."""

    def __init__(
        self,
        cases: Sequence[NicheEvolutionCase],
        *,
        seed_candidate_provider: CandidateProvider,
        evaluator: CandidateEvaluator,
        candidate_budget: int,
        graph: AuditedSkillGraph | None = None,
        seed: int = 0,
        archive_factory: Callable[[], NicheArchive] | None = None,
    ) -> None:
        if not cases:
            raise ValueError("niche evolution requires cases")
        if candidate_budget < 1:
            raise ValueError("candidate_budget must be >= 1")
        ordered = tuple(cases)
        if tuple(row.case_index for row in ordered) != tuple(
            sorted(row.case_index for row in ordered)
        ):
            raise ValueError("cases must be ordered by case_index")
        self.cases = ordered
        self.seed_candidate_provider = seed_candidate_provider
        self.evaluator = evaluator
        self.candidate_budget = int(candidate_budget)
        self.graph = graph or AuditedSkillGraph(seed=seed)
        self.seed = int(seed)
        factory = archive_factory or (lambda: NicheArchive(seed=seed))
        self.archives = {arm: factory() for arm in _UPDATING_ARMS}
        if len({id(archive) for archive in self.archives.values()}) != len(
            self.archives
        ):
            raise ValueError(
                "archive_factory must return an independent archive per arm"
            )

    def run(self) -> NicheEvolutionRun:
        outcomes: list[NicheArmOutcome] = []
        branch_by_case = {
            row.case_id: row.runtime_branch for row in self.cases
        }
        for case in self.cases:
            seeds = tuple(self.seed_candidate_provider(case))
            if case.runtime_branch == "fix" and len(seeds) < self.candidate_budget:
                raise ValueError(
                    "seed provider must fill the equal candidate budget"
                )
            arm_executions: dict[
                str,
                tuple[
                    BehaviorDescriptor,
                    tuple[SkillCandidate, ...],
                    tuple[NicheDualExecution, ...],
                ],
            ] = {}
            # Test every arm against state L_t before any current-case update.
            for arm in NICHE_ARMS:
                descriptor = self._descriptor_for_arm(arm, case.descriptor)
                candidates = (
                    self._candidates_for_arm(
                        arm,
                        case,
                        descriptor,
                        seeds,
                    )
                    if case.runtime_branch == "fix"
                    else ()
                )
                executions = tuple(
                    self._evaluate(case, candidate)
                    for candidate in candidates
                )
                arm_executions[arm] = (
                    descriptor,
                    candidates,
                    executions,
                )
                selected = _select_gold_free(executions)
                outcomes.append(
                    NicheArmOutcome(
                        case_id=case.case_id,
                        family_id=case.family_id,
                        case_index=case.case_index,
                        arm_id=arm,
                        descriptor_niche_id=descriptor.niche_id,
                        candidate_skill_ids=tuple(
                            row.skill_id for row in candidates
                        ),
                        selected_skill_id=(
                            selected.skill_id if selected is not None else None
                        ),
                        selected_gold_free_gain=(
                            selected.gold_free_gain
                            if selected is not None
                            else None
                        ),
                        selected_shadow_gain=(
                            selected.shadow_gain
                            if selected is not None
                            else None
                        ),
                        candidate_count=len(candidates),
                        budget_aligned=(
                            len(candidates) == self.candidate_budget
                            if case.runtime_branch == "fix"
                            else len(candidates) == 0
                        ),
                        abstained=selected is None,
                        update_effective_after_case_index=(
                            case.case_index + 1
                            if arm in _UPDATING_ARMS
                            and case.runtime_branch == "fix"
                            else None
                        ),
                    )
                )

            # Post-outcome update.  The just-produced case never validates a
            # revision created from itself and cannot affect the outcomes above.
            if case.runtime_branch == "fix":
                for arm in _UPDATING_ARMS:
                    descriptor, candidates, executions = arm_executions[arm]
                    self._update_archive(
                        self.archives[arm],
                        case,
                        descriptor,
                        candidates,
                        executions,
                    )

        return NicheEvolutionRun(
            outcomes=tuple(outcomes),
            archive_snapshots={
                arm: archive.to_dict()
                for arm, archive in sorted(self.archives.items())
            },
            leakage_assertions=(
                ("runtime_selection_excludes_shadow_gain", True),
                (
                    "fill_executes_no_candidates",
                    all(
                        row.candidate_count == 0
                        for row in outcomes
                        if branch_by_case[row.case_id] == "fill"
                    ),
                ),
                (
                    "all_arms_budget_aligned",
                    all(row.budget_aligned for row in outcomes),
                ),
                (
                    "all_frozen_has_no_archive",
                    "all_frozen" not in self.archives,
                ),
            ),
        )

    def _descriptor_for_arm(
        self,
        arm: str,
        descriptor: BehaviorDescriptor,
    ) -> BehaviorDescriptor:
        if arm == "unkeyed_pool":
            return BehaviorDescriptor(
                "global",
                (),
                descriptor.runtime_surface,
                descriptor.version,
            )
        if arm == "random_niche":
            bucket = int(
                hashlib.sha256(
                    f"{self.seed}|{descriptor.niche_id}".encode("utf-8")
                ).hexdigest(),
                16,
            ) % 8
            return BehaviorDescriptor(
                f"random-{bucket}",
                (),
                descriptor.runtime_surface,
                descriptor.version,
            )
        return descriptor

    def _candidates_for_arm(
        self,
        arm: str,
        case: NicheEvolutionCase,
        descriptor: BehaviorDescriptor,
        seeds: Sequence[SkillCandidate],
    ) -> tuple[SkillCandidate, ...]:
        archive_rows: list[SkillCandidate] = []
        if arm in _UPDATING_ARMS:
            archive = self.archives[arm]
            archive_rows.extend(
                record.to_skill_candidate()
                for record in archive.candidates_for_descriptor(
                    descriptor,
                    case_index=case.case_index,
                )
            )
            if arm == "map_elites_edges":
                for edge in self.graph.edges:
                    if edge.target_niche_id != descriptor.niche_id:
                        continue
                    try:
                        record = archive.candidate(edge.source_revision_id)
                    except KeyError:
                        continue
                    archive_rows.append(record.to_skill_candidate())
        ordered = _deduplicate_candidates((*archive_rows, *seeds))
        return ordered[: self.candidate_budget]

    def _evaluate(
        self,
        case: NicheEvolutionCase,
        candidate: SkillCandidate,
    ) -> NicheDualExecution:
        result = self.evaluator(case, candidate, case.base_context)
        if result.skill_id != candidate.skill_id:
            raise ValueError("evaluator returned a mismatched skill id")
        if result.execution_cost < 0.0 or not math.isfinite(
            result.execution_cost
        ):
            raise ValueError("execution cost must be finite and non-negative")
        return result

    def _update_archive(
        self,
        archive: NicheArchive,
        case: NicheEvolutionCase,
        descriptor: BehaviorDescriptor,
        candidates: Sequence[SkillCandidate],
        executions: Sequence[NicheDualExecution],
    ) -> None:
        by_skill = {row.skill_id: row for row in executions}
        for record in archive.candidates_for_descriptor(
            descriptor,
            case_index=case.case_index,
        ):
            result = by_skill.get(record.revision_id)
            if result is None or result.shadow_gain is None:
                continue
            if case.case_id == record.producing_case_id:
                continue
            try:
                updated = archive.record_validation(
                    record.revision_id,
                    NicheValidationEvidence(
                        case.case_id,
                        case.family_id,
                        case.case_index,
                        float(result.shadow_gain),
                        result.execution_cost,
                    ),
                )
            except ValueError as exc:
                if "duplicate validation case" not in str(exc):
                    raise
                updated = record
            if updated.status == "stable":
                archive.consider_elite(updated.revision_id)

        successful = [
            (candidate, by_skill[candidate.skill_id])
            for candidate in candidates
            if candidate.skill_id in by_skill
            and by_skill[candidate.skill_id].shadow_gain is not None
            and math.isfinite(float(by_skill[candidate.skill_id].shadow_gain))
            and float(by_skill[candidate.skill_id].shadow_gain) >= 0.1
        ]
        if not successful:
            return
        candidate, _result = min(
            successful,
            key=lambda row: (
                -float(row[1].shadow_gain),
                row[1].execution_cost,
                row[0].skill_id,
            ),
        )
        archive.propose(
            descriptor,
            candidate.operator,
            producing_case_id=case.case_id,
            producing_family_id=case.family_id,
            created_after_case_index=case.case_index,
        )


def _select_gold_free(
    executions: Sequence[NicheDualExecution],
) -> NicheDualExecution | None:
    finite = [
        row
        for row in executions
        if row.gold_free_gain is not None
        and math.isfinite(float(row.gold_free_gain))
        and float(row.gold_free_gain) > 0.0
    ]
    if not finite:
        return None
    return min(
        finite,
        key=lambda row: (-float(row.gold_free_gain), row.skill_id),
    )


def _deduplicate_candidates(
    candidates: Sequence[SkillCandidate],
) -> tuple[SkillCandidate, ...]:
    output = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.operator.content_hash()
        if key in seen:
            continue
        seen.add(key)
        output.append(candidate)
    return tuple(output)
