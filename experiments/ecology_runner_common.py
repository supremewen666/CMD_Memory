"""Prequential selection-arm adapter for Exp24/Exp27 ecology experiments.

The runner owns ordering only.  Candidate providers own arm-specific library
or population state; mutable arms are updated after every arm has completed the
current case.  Frozen/no-update arms are never passed to the updater.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import random
from typing import Callable, Mapping, Sequence

from cmd_audit.repair.skill_ecology import (
    CompetitiveExecutor,
    CompetitiveResult,
    SkillCandidate,
    SkillEvaluator,
)


SELECTION_ARMS = (
    "no_repair",
    "no_update",
    "random_skill",
    "fixed_library",
    "single_top1",
    "competitive_topk",
    "lamarckian",
    "darwinian_global",
    "darwinian_niche",
)
MUTABLE_ARMS = frozenset(
    {"lamarckian", "darwinian_global", "darwinian_niche"}
)
_COMPETITIVE_ARMS = frozenset(
    {"competitive_topk", "darwinian_global", "darwinian_niche"}
)


@dataclass(frozen=True)
class EcologyCase:
    case_id: str
    failure_type: str
    base_context: str


@dataclass(frozen=True)
class ArmSelectionOutcome:
    arm_id: str
    case_id: str
    candidate_skill_ids: tuple[str, ...]
    selected_skill_id: str | None
    competitive_result: CompetitiveResult | None
    updated_after_case: bool


@dataclass(frozen=True)
class EcologyRunResult:
    outcomes: tuple[ArmSelectionOutcome, ...]
    leakage_assertions: tuple[tuple[str, bool], ...]
    initial_state_fingerprints: tuple[tuple[str, str], ...]
    final_state_fingerprints: tuple[tuple[str, str], ...]


CandidateProvider = Callable[[str, EcologyCase], Sequence[SkillCandidate]]
PostCaseUpdater = Callable[
    [str, EcologyCase, ArmSelectionOutcome],
    None,
]
StateFingerprint = Callable[[str], str]


class SkillEcologyExperimentRunner:
    """Evaluate frozen, single, random, competitive and evolution arms."""

    def __init__(
        self,
        cases: Sequence[EcologyCase],
        *,
        candidate_provider: CandidateProvider,
        evaluator: SkillEvaluator,
        post_case_updater: PostCaseUpdater | None = None,
        state_fingerprint: StateFingerprint | None = None,
        arms: Sequence[str] = SELECTION_ARMS,
        top_k: int = 3,
        recovery_threshold: float = 0.1,
        seed: int = 24,
    ) -> None:
        unknown = sorted(set(arms) - set(SELECTION_ARMS))
        if unknown:
            raise ValueError(f"unknown ecology arms: {unknown}")
        if len(set(arms)) != len(tuple(arms)):
            raise ValueError("duplicate ecology arm")
        self.cases = tuple(cases)
        self.candidate_provider = candidate_provider
        self.evaluator = evaluator
        self.post_case_updater = post_case_updater
        self.state_fingerprint = state_fingerprint
        self.arms = tuple(arms)
        self.top_k = int(top_k)
        self.recovery_threshold = float(recovery_threshold)
        self.seed = int(seed)

    def run(self) -> EcologyRunResult:
        initial = self._fingerprints()
        outcomes: list[ArmSelectionOutcome] = []
        all_outcomes_before_updates = True
        for case in self.cases:
            pending_updates: list[tuple[str, ArmSelectionOutcome]] = []
            case_outcomes: list[ArmSelectionOutcome] = []
            for arm_id in self.arms:
                candidates = tuple(self.candidate_provider(arm_id, case))
                outcome = self._evaluate_arm(arm_id, case, candidates)
                case_outcomes.append(outcome)
                if arm_id in MUTABLE_ARMS:
                    pending_updates.append((arm_id, outcome))
            outcomes.extend(case_outcomes)
            if len(case_outcomes) != len(self.arms):
                all_outcomes_before_updates = False
            if self.post_case_updater is not None:
                for arm_id, outcome in pending_updates:
                    self.post_case_updater(arm_id, case, outcome)
                    outcomes[-len(case_outcomes) + self.arms.index(arm_id)] = (
                        ArmSelectionOutcome(
                            arm_id=outcome.arm_id,
                            case_id=outcome.case_id,
                            candidate_skill_ids=outcome.candidate_skill_ids,
                            selected_skill_id=outcome.selected_skill_id,
                            competitive_result=outcome.competitive_result,
                            updated_after_case=True,
                        )
                    )
        final = self._fingerprints()
        initial_map = dict(initial)
        final_map = dict(final)
        frozen_unchanged = all(
            initial_map.get(arm_id) == final_map.get(arm_id)
            for arm_id in self.arms
            if arm_id not in MUTABLE_ARMS
        ) if self.state_fingerprint is not None else True
        return EcologyRunResult(
            outcomes=tuple(outcomes),
            leakage_assertions=(
                ("all_arm_outcomes_before_updates", all_outcomes_before_updates),
                ("frozen_arms_unchanged", frozen_unchanged),
            ),
            initial_state_fingerprints=initial,
            final_state_fingerprints=final,
        )

    def _evaluate_arm(
        self,
        arm_id: str,
        case: EcologyCase,
        candidates: tuple[SkillCandidate, ...],
    ) -> ArmSelectionOutcome:
        if arm_id == "no_repair" or not candidates:
            return ArmSelectionOutcome(
                arm_id=arm_id,
                case_id=case.case_id,
                candidate_skill_ids=tuple(item.skill_id for item in candidates),
                selected_skill_id=None,
                competitive_result=None,
                updated_after_case=False,
            )
        selected_candidates = candidates
        if arm_id == "random_skill":
            rng = random.Random(_case_seed(self.seed, arm_id, case.case_id))
            selected_candidates = (candidates[rng.randrange(len(candidates))],)
        elif arm_id not in _COMPETITIVE_ARMS:
            selected_candidates = candidates[:1]
        competitive = CompetitiveExecutor(
            top_k=self.top_k if arm_id in _COMPETITIVE_ARMS else 1,
            recovery_threshold=self.recovery_threshold,
        ).execute(
            case_id=case.case_id,
            failure_type=case.failure_type,
            base_context=case.base_context,
            candidates=selected_candidates,
            evaluator=self.evaluator,
        )
        return ArmSelectionOutcome(
            arm_id=arm_id,
            case_id=case.case_id,
            candidate_skill_ids=tuple(
                item.skill_id for item in selected_candidates[: self.top_k]
            ),
            selected_skill_id=(
                competitive.winner.skill_id
                if competitive.winner is not None
                else None
            ),
            competitive_result=competitive,
            updated_after_case=False,
        )

    def _fingerprints(self) -> tuple[tuple[str, str], ...]:
        if self.state_fingerprint is None:
            return ()
        return tuple(
            (arm_id, str(self.state_fingerprint(arm_id)))
            for arm_id in self.arms
        )


def _case_seed(seed: int, arm_id: str, case_id: str) -> int:
    payload = f"{seed}|{arm_id}|{case_id}".encode("utf-8")
    return int(hashlib.sha256(payload).hexdigest(), 16)

