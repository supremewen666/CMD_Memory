"""Stage 8A transfer plan: procedural content, evidence, and residuals split."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping, Sequence

from cmd_audit.repair.ghost_ecology import ObservableResidualGHOSTRouter

from .contracts import SkillEvidenceState, SkillSpec, canonical_sha256


STAGE8A_ARMS = (
    "no_repair", "random_legal", "skill_content_only", "reset_online",
    "frozen_source", "niche_shuffled", "mean_only", "reset_prefix",
    "source_prefix", "oracle_legal_operator",
)


@dataclass(frozen=True)
class Stage8AResidualTransferPlan:
    source_model_id: str
    target_model_id: str
    source_residual_snapshot: Mapping[str, object]
    source_residual_snapshot_sha256: str
    skill_content_sha256s: tuple[str, ...]
    source_evidence_state_sha256s: tuple[str, ...]
    prefix_split: str
    scored_split: str
    arms: tuple[str, ...] = STAGE8A_ARMS

    def __post_init__(self) -> None:
        if self.prefix_split == self.scored_split or not self.prefix_split or not self.scored_split:
            raise ValueError("Stage 8A requires a disjoint non-empty target prefix and scored split")
        if self.arms != STAGE8A_ARMS:
            raise ValueError("Stage 8A arms must use the canonical matrix")
        restored = ObservableResidualGHOSTRouter.from_snapshot(self.source_residual_snapshot)
        if restored.snapshot["snapshot_sha256"] != self.source_residual_snapshot_sha256:
            raise ValueError("source residual snapshot hash mismatch")

    @classmethod
    def create(
        cls, *, source_model_id: str, target_model_id: str,
        source_residual_snapshot: Mapping[str, object], skill_content: Sequence[SkillSpec],
        source_evidence: Sequence[SkillEvidenceState], prefix_split: str, scored_split: str,
    ) -> "Stage8AResidualTransferPlan":
        return cls(
            source_model_id, target_model_id, source_residual_snapshot,
            str(source_residual_snapshot["snapshot_sha256"]),
            tuple(sorted(skill.content_sha256 for skill in skill_content)),
            tuple(sorted(state.evidence_state_sha256 for state in source_evidence)),
            prefix_split, scored_split,
        )

    def to_mapping(self) -> dict[str, object]:
        body = asdict(self)
        body["plan_sha256"] = canonical_sha256(body)
        body["transfer_rules"] = {
            "skill_content_only": "transfer SkillSpec only; reset residual and SkillEvidenceState",
            "frozen_source": "transfer residual snapshot; do not update on scored split",
            "reset_online": "reset residual; update only from target selected-action receipts",
            "prefix_arms": "only target prefix updates residual; no new skills or evidence transfer",
        }
        return body
