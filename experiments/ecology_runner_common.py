"""Prequential selection-arm adapter for Exp24/Exp27 ecology experiments.

The runner owns ordering only.  Candidate providers own arm-specific library
or population state; mutable arms are updated after every arm has completed the
current case.  Frozen/no-update arms are never passed to the updater.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import random
from typing import Callable, Sequence

from cmd_audit.repair.skill_ecology import CompetitiveExecutor, CompetitiveResult, SkillCandidate, SkillEvaluator

SELECTION_ARMS = ("no_repair", "no_update", "random_skill", "fixed_library", "single_top1", "competitive_topk", "lamarckian", "darwinian_global", "darwinian_niche")
MUTABLE_ARMS = frozenset({"lamarckian", "darwinian_global", "darwinian_niche"})
_COMPETITIVE_ARMS = frozenset({"competitive_topk", "darwinian_global", "darwinian_niche"})

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
PostCaseUpdater = Callable[[str, EcologyCase, ArmSelectionOutcome], None]
StateFingerprint = Callable[[str], str]

class SkillEcologyExperimentRunner:
    """Evaluate frozen, single, random, competitive and evolution arms."""
    def __init__(self, cases: Sequence[EcologyCase], *, candidate_provider: CandidateProvider, evaluator: SkillEvaluator, post_case_updater: PostCaseUpdater | None = None, state_fingerprint: StateFingerprint | None = None, arms: Sequence[str] = SELECTION_ARMS, top_k: int = 3, recovery_threshold: float = 0.1, seed: int = 24) -> None:
        unknown = sorted(set(arms) - set(SELECTION_ARMS))
        if unknown:
            raise ValueError(f"unknown ecology arms: {unknown}")
        if len(set(arms)) != len(tuple(arms)):
            raise ValueError("duplicate ecology arm")
        self.cases, self.candidate_provider, self.evaluator = tuple(cases), candidate_provider, evaluator
        self.post_case_updater, self.state_fingerprint, self.arms = post_case_updater, state_fingerprint, tuple(arms)
        self.top_k, self.recovery_threshold, self.seed = int(top_k), float(recovery_threshold), int(seed)

    def run(self) -> EcologyRunResult:
        initial = self._fingerprints(); outcomes: list[ArmSelectionOutcome] = []
        for case in self.cases:
            case_outcomes = [self._evaluate_arm(arm, case, tuple(self.candidate_provider(arm, case))) for arm in self.arms]
            outcomes.extend(case_outcomes)
            if self.post_case_updater:
                for arm, outcome in zip(self.arms, case_outcomes):
                    if arm in MUTABLE_ARMS:
                        self.post_case_updater(arm, case, outcome)
                        outcomes[-len(case_outcomes) + self.arms.index(arm)] = ArmSelectionOutcome(outcome.arm_id, outcome.case_id, outcome.candidate_skill_ids, outcome.selected_skill_id, outcome.competitive_result, True)
        final = self._fingerprints(); first, last = dict(initial), dict(final)
        frozen_unchanged = self.state_fingerprint is None or all(first.get(arm) == last.get(arm) for arm in self.arms if arm not in MUTABLE_ARMS)
        return EcologyRunResult(tuple(outcomes), (("all_arm_outcomes_before_updates", True), ("frozen_arms_unchanged", frozen_unchanged)), initial, final)

    def _evaluate_arm(self, arm_id: str, case: EcologyCase, candidates: tuple[SkillCandidate, ...]) -> ArmSelectionOutcome:
        if arm_id == "no_repair" or not candidates:
            return ArmSelectionOutcome(arm_id, case.case_id, tuple(item.skill_id for item in candidates), None, None, False)
        selected = candidates
        if arm_id == "random_skill":
            selected = (candidates[random.Random(_case_seed(self.seed, arm_id, case.case_id)).randrange(len(candidates))],)
        elif arm_id not in _COMPETITIVE_ARMS:
            selected = candidates[:1]
        result = CompetitiveExecutor(top_k=self.top_k if arm_id in _COMPETITIVE_ARMS else 1, recovery_threshold=self.recovery_threshold).execute(case_id=case.case_id, failure_type=case.failure_type, base_context=case.base_context, candidates=selected, evaluator=self.evaluator)
        return ArmSelectionOutcome(arm_id, case.case_id, tuple(item.skill_id for item in selected[:self.top_k]), None if result.winner is None else result.winner.skill_id, result, False)

    def _fingerprints(self) -> tuple[tuple[str, str], ...]:
        return () if self.state_fingerprint is None else tuple((arm, str(self.state_fingerprint(arm))) for arm in self.arms)

def _case_seed(seed: int, arm_id: str, case_id: str) -> int:
    return int(hashlib.sha256(f"{seed}|{arm_id}|{case_id}".encode()).hexdigest(), 16)
