"""MAP-Elites-style, niche-local archive for executable repair skills."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from statistics import fmean, median
import hashlib
import json
import math
import random
from typing import Iterable, Literal, Mapping, Sequence

from cmd_audit.counterfactual.operators import OperatorSpec

from .reasoning_template import StructuredReasoningTemplate
from .skill_ecology import SkillCandidate


CandidateStatus = Literal["provisional", "stable", "retired"]
_VALID_STATUSES = frozenset({"provisional", "stable", "retired"})
_LABEL_LIKE_TOKENS = frozenset(
    {
        "retrieval_error",
        "injection_error",
        "granularity_error",
        "safety_error",
        "item_stale",
        "item_conflict",
        "item_wrong",
        "item_poisoned",
        "item_compression_distorted",
        "perturbation_label",
        "perturbation_type",
        "recurrence_family_id",
        "gold_answer",
        "gold_evidence",
    }
)


class SemanticClusterVocabulary:
    """Frozen, dev-prefix-only vocabulary for descriptor construction.

    The vocabulary is a protocol artifact, not an online learner.  Once
    :meth:`freeze` is called, both new tokens and outcome-like tokens are
    rejected.  Keeping this object small and serialisable makes its hash easy
    to place in an experiment manifest.
    """

    SCHEMA_VERSION = "cmd-semantic-cluster-vocabulary-v1"

    def __init__(self, tokens: Sequence[str], *, source: str = "dev-prefix") -> None:
        if source != "dev-prefix":
            raise ValueError("semantic-cluster vocabulary must come from dev-prefix")
        cleaned = tuple(sorted({str(token).strip() for token in tokens if str(token).strip()}))
        if any(any(marker in token.casefold() for marker in _LABEL_LIKE_TOKENS) for token in cleaned):
            raise ValueError("semantic-cluster vocabulary contains outcome/label metadata")
        self._tokens = cleaned
        self.source = source
        self.frozen = False

    @property
    def tokens(self) -> tuple[str, ...]:
        return self._tokens

    @property
    def vocabulary_sha256(self) -> str:
        payload = {"schema_version": self.SCHEMA_VERSION, "source": self.source, "tokens": self._tokens}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @property
    def schema_hash(self) -> str:
        return self.vocabulary_sha256

    def freeze(self) -> "SemanticClusterVocabulary":
        self.frozen = True
        return self

    def validate(self, token: str, *, post_outcome: bool = False) -> str:
        value = str(token).strip()
        if post_outcome:
            raise ValueError("post-outcome semantic-cluster labels are forbidden")
        if value not in self._tokens:
            raise ValueError("semantic-cluster vocabulary is frozen; token is not registered")
        return value

    def to_manifest(self) -> dict[str, object]:
        return {"schema_version": self.SCHEMA_VERSION, "source": self.source,
                "tokens": list(self._tokens), "vocabulary_sha256": self.vocabulary_sha256}


@dataclass(frozen=True)
class BehaviorDescriptor:
    memory_fingerprint_cluster: str
    signal_signature: tuple[str, ...]
    runtime_surface: str
    version: str = "sigil-descriptor-v1"

    def __post_init__(self) -> None:
        if not self.memory_fingerprint_cluster or not self.runtime_surface:
            raise ValueError("descriptor cluster and runtime surface are required")
        if not self.version:
            raise ValueError("descriptor version is required")
        values = (
            self.memory_fingerprint_cluster,
            self.runtime_surface,
            *self.signal_signature,
        )
        for value in values:
            normalized = str(value).casefold()
            if any(token in normalized for token in _LABEL_LIKE_TOKENS):
                raise ValueError(
                    "behavior descriptor contains label/evaluation metadata"
                )
        object.__setattr__(
            self,
            "signal_signature",
            tuple(sorted({str(value) for value in self.signal_signature})),
        )

    @property
    def niche_id(self) -> str:
        return "niche-" + hashlib.sha256(
            json.dumps(
                {
                    "cluster": self.memory_fingerprint_cluster,
                    "signals": self.signal_signature,
                    "surface": self.runtime_surface,
                    "version": self.version,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @classmethod
    def unknown(
        cls,
        *,
        runtime_surface: str,
        version: str = "sigil-descriptor-v1",
    ) -> "BehaviorDescriptor":
        return cls("unknown", (), runtime_surface, version)

    @classmethod
    def from_semantic_cluster(
        cls,
        cluster: str,
        *,
        vocabulary: SemanticClusterVocabulary,
        signal_signature: Sequence[str] = (),
        runtime_surface: str,
        version: str = "sigil-descriptor-v1",
        post_outcome: bool = False,
    ) -> "BehaviorDescriptor":
        """Construct a descriptor only from the frozen dev-prefix vocabulary."""
        return cls(vocabulary.validate(cluster, post_outcome=post_outcome), tuple(signal_signature), runtime_surface, version)


@dataclass(frozen=True)
class NicheValidationEvidence:
    case_id: str
    family_id: str
    case_index: int
    recovery_gain: float
    execution_cost: float

    def __post_init__(self) -> None:
        if not self.case_id or not self.family_id:
            raise ValueError("case_id and family_id are required")
        if self.case_index < 0:
            raise ValueError("case_index must be non-negative")
        if not math.isfinite(self.recovery_gain):
            raise ValueError("recovery_gain must be finite")
        if not math.isfinite(self.execution_cost) or self.execution_cost < 0.0:
            raise ValueError("execution_cost must be finite and non-negative")


@dataclass
class NicheCandidateRecord:
    revision_id: str
    descriptor: BehaviorDescriptor
    operator: OperatorSpec
    reasoning_template: StructuredReasoningTemplate
    producing_case_id: str
    producing_family_id: str
    parent_revision_id: str | None
    created_after_case_index: int
    effective_after_case_index: int
    status: CandidateStatus = "provisional"
    evidence: list[NicheValidationEvidence] = field(default_factory=list)
    anchor_case_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.revision_id or not self.producing_case_id:
            raise ValueError("revision and producing case ids are required")
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"invalid candidate status: {self.status}")
        if self.effective_after_case_index <= self.created_after_case_index:
            raise ValueError(
                "candidate must become effective after its producing case"
            )

    @property
    def successful_evidence(self) -> tuple[NicheValidationEvidence, ...]:
        return tuple(row for row in self.evidence if row.recovery_gain >= 0.1)

    @property
    def median_gain(self) -> float:
        return (
            median(row.recovery_gain for row in self.evidence)
            if self.evidence
            else float("-inf")
        )

    @property
    def median_cost(self) -> float:
        return (
            median(row.execution_cost for row in self.evidence)
            if self.evidence
            else float("inf")
        )

    def to_skill_candidate(self) -> SkillCandidate:
        return SkillCandidate(self.revision_id, self.operator)


@dataclass(frozen=True)
class ArchiveTransition:
    niche_id: str
    challenger_revision_id: str
    incumbent_revision_id: str | None
    decision: str
    paired_cases: int
    challenger_only_recoveries: int
    mean_difference: float | None
    lower_bound: float | None


class NicheArchive:
    """Versioned niche archive with local competition and rollback."""

    def __init__(
        self,
        *,
        success_threshold: float = 0.1,
        confidence: float = 0.95,
        bootstrap_samples: int = 2000,
        seed: int = 0,
    ) -> None:
        if success_threshold < 0.0:
            raise ValueError("success_threshold must be non-negative")
        if not 0.0 < confidence < 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if bootstrap_samples < 100:
            raise ValueError("bootstrap_samples must be >= 100")
        self.success_threshold = float(success_threshold)
        self.confidence = float(confidence)
        self.bootstrap_samples = int(bootstrap_samples)
        self.seed = int(seed)
        self._candidates: dict[str, NicheCandidateRecord] = {}
        self._elite_by_niche: dict[str, str] = {}
        self._history: list[
            tuple[
                dict[str, NicheCandidateRecord],
                dict[str, str],
            ]
        ] = []

    def propose(
        self,
        descriptor: BehaviorDescriptor,
        operator: OperatorSpec,
        *,
        producing_case_id: str,
        producing_family_id: str,
        created_after_case_index: int,
        reasoning_template: StructuredReasoningTemplate | None = None,
        parent_revision_id: str | None = None,
    ) -> NicheCandidateRecord:
        if not operator.steps and not operator.item_signal_hints:
            raise ValueError("identity operators cannot enter a niche")
        for existing in self._candidates.values():
            if (
                existing.descriptor.niche_id == descriptor.niche_id
                and existing.operator.content_hash() == operator.content_hash()
                and existing.status != "retired"
            ):
                return deepcopy(existing)
        revision_id = "niche-revision-" + hashlib.sha256(
            json.dumps(
                {
                    "niche_id": descriptor.niche_id,
                    "operator": operator.to_dict(),
                    "producing_case_id": producing_case_id,
                    "parent_revision_id": parent_revision_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if revision_id in self._candidates:
            return deepcopy(self._candidates[revision_id])
        self._checkpoint()
        record = NicheCandidateRecord(
            revision_id=revision_id,
            descriptor=descriptor,
            operator=operator,
            reasoning_template=(
                reasoning_template or StructuredReasoningTemplate()
            ),
            producing_case_id=str(producing_case_id),
            producing_family_id=str(producing_family_id),
            parent_revision_id=parent_revision_id,
            created_after_case_index=int(created_after_case_index),
            effective_after_case_index=int(created_after_case_index) + 1,
        )
        self._candidates[revision_id] = record
        return deepcopy(record)

    def record_validation(
        self,
        revision_id: str,
        evidence: NicheValidationEvidence,
    ) -> NicheCandidateRecord:
        record = self._require_candidate(revision_id)
        if record.status == "retired":
            raise ValueError("retired candidate cannot receive validation")
        if evidence.case_id == record.producing_case_id:
            raise ValueError("producing case cannot validate its own revision")
        if evidence.case_index < record.effective_after_case_index:
            raise ValueError("validation precedes effective-after boundary")
        if any(row.case_id == evidence.case_id for row in record.evidence):
            raise ValueError("duplicate validation case")
        self._checkpoint()
        record.evidence.append(evidence)
        successful = sorted(
            (
                row
                for row in record.evidence
                if row.recovery_gain >= self.success_threshold
            ),
            key=lambda row: (row.case_index, row.case_id),
        )
        families = {row.family_id for row in successful}
        if (
            record.status == "provisional"
            and len(successful) >= 3
            and len(families) >= 2
        ):
            record.status = "stable"
            record.anchor_case_ids = (
                record.producing_case_id,
                *(row.case_id for row in successful[:3]),
            )
        return deepcopy(record)

    def consider_elite(self, revision_id: str) -> ArchiveTransition:
        challenger = self._require_candidate(revision_id)
        niche_id = challenger.descriptor.niche_id
        incumbent_id = self._elite_by_niche.get(niche_id)
        if challenger.status != "stable":
            return ArchiveTransition(
                niche_id,
                revision_id,
                incumbent_id,
                "challenger_not_stable",
                0,
                0,
                None,
                None,
            )
        if incumbent_id is None:
            self._checkpoint()
            self._elite_by_niche[niche_id] = revision_id
            return ArchiveTransition(
                niche_id,
                revision_id,
                None,
                "install_first_elite",
                0,
                0,
                None,
                None,
            )
        if incumbent_id == revision_id:
            return ArchiveTransition(
                niche_id,
                revision_id,
                incumbent_id,
                "already_elite",
                0,
                0,
                None,
                None,
            )
        incumbent = self._require_candidate(incumbent_id)
        if incumbent.descriptor.niche_id != niche_id:
            raise RuntimeError("cross-niche incumbent corruption")
        transition = self._paired_replacement(challenger, incumbent)
        if transition.decision == "replace_elite":
            self._checkpoint()
            incumbent.status = "retired"
            self._elite_by_niche[niche_id] = revision_id
        return transition

    def elite(
        self,
        descriptor: BehaviorDescriptor,
        *,
        case_index: int | None = None,
    ) -> NicheCandidateRecord | None:
        revision_id = self._elite_by_niche.get(descriptor.niche_id)
        if revision_id is None:
            return None
        record = self._candidates[revision_id]
        if (
            case_index is not None
            and case_index < record.effective_after_case_index
        ):
            return None
        return deepcopy(record)

    def candidates(self) -> tuple[NicheCandidateRecord, ...]:
        return tuple(
            deepcopy(self._candidates[key]) for key in sorted(self._candidates)
        )

    def candidate(self, revision_id: str) -> NicheCandidateRecord:
        return deepcopy(self._require_candidate(revision_id))

    def candidates_for_descriptor(
        self,
        descriptor: BehaviorDescriptor,
        *,
        case_index: int,
        include_provisional: bool = True,
    ) -> tuple[NicheCandidateRecord, ...]:
        allowed = {"stable"}
        if include_provisional:
            allowed.add("provisional")
        return tuple(
            deepcopy(record)
            for record in sorted(
                self._candidates.values(),
                key=lambda value: (
                    value.status != "stable",
                    -value.median_gain,
                    value.median_cost,
                    value.revision_id,
                ),
            )
            if record.descriptor.niche_id == descriptor.niche_id
            and record.status in allowed
            and record.effective_after_case_index <= case_index
        )

    def rollback(self) -> None:
        if not self._history:
            raise RuntimeError("no archive transition to roll back")
        self._candidates, self._elite_by_niche = self._history.pop()

    def to_dict(self) -> dict[str, object]:
        return {
            "version": "sigil-niche-archive-v1",
            "config": {
                "success_threshold": self.success_threshold,
                "confidence": self.confidence,
                "bootstrap_samples": self.bootstrap_samples,
                "seed": self.seed,
            },
            "elite_by_niche": dict(sorted(self._elite_by_niche.items())),
            "candidates": [
                {
                    "revision_id": record.revision_id,
                    "descriptor": asdict(record.descriptor),
                    "operator": record.operator.to_dict(),
                    "reasoning_template": record.reasoning_template.to_dict(),
                    "producing_case_id": record.producing_case_id,
                    "producing_family_id": record.producing_family_id,
                    "parent_revision_id": record.parent_revision_id,
                    "created_after_case_index": record.created_after_case_index,
                    "effective_after_case_index": (
                        record.effective_after_case_index
                    ),
                    "status": record.status,
                    "evidence": [
                        asdict(value) for value in record.evidence
                    ],
                    "anchor_case_ids": record.anchor_case_ids,
                }
                for record in self.candidates()
            ],
        }

    def _paired_replacement(
        self,
        challenger: NicheCandidateRecord,
        incumbent: NicheCandidateRecord,
    ) -> ArchiveTransition:
        challenger_by_case = {
            row.case_id: row for row in challenger.evidence
        }
        incumbent_by_case = {
            row.case_id: row for row in incumbent.evidence
        }
        case_ids = tuple(
            sorted(set(challenger_by_case) & set(incumbent_by_case))
        )
        if not case_ids:
            return ArchiveTransition(
                challenger.descriptor.niche_id,
                challenger.revision_id,
                incumbent.revision_id,
                "no_paired_cases",
                0,
                0,
                None,
                None,
            )
        if any(
            anchor not in challenger_by_case
            or challenger_by_case[anchor].recovery_gain
            < self.success_threshold
            for anchor in incumbent.anchor_case_ids
        ):
            return ArchiveTransition(
                challenger.descriptor.niche_id,
                challenger.revision_id,
                incumbent.revision_id,
                "anchor_regression",
                len(case_ids),
                0,
                None,
                None,
            )
        paired = [
            (
                challenger_by_case[case_id],
                incumbent_by_case[case_id],
            )
            for case_id in case_ids
        ]
        differences = tuple(
            left.recovery_gain - right.recovery_gain
            for left, right in paired
        )
        challenger_only = sum(
            left.recovery_gain >= self.success_threshold
            and right.recovery_gain < self.success_threshold
            for left, right in paired
        )
        shared_recoveries = [
            (left.execution_cost, right.execution_cost)
            for left, right in paired
            if left.recovery_gain >= self.success_threshold
            and right.recovery_gain >= self.success_threshold
        ]
        cost_ok = (
            not shared_recoveries
            or median(left for left, _right in shared_recoveries)
            <= median(right for _left, right in shared_recoveries)
        )
        lower = _family_blocked_lower(
            tuple(
                (
                    challenger_by_case[case_id].family_id,
                    challenger_by_case[case_id].recovery_gain
                    - incumbent_by_case[case_id].recovery_gain,
                )
                for case_id in case_ids
            ),
            confidence=self.confidence,
            samples=self.bootstrap_samples,
            seed=self.seed,
        )
        mean_difference = fmean(differences)
        decision = (
            "replace_elite"
            if mean_difference > 0.0
            and lower > 0.0
            and challenger_only >= 3
            and cost_ok
            else "retain_incumbent"
        )
        return ArchiveTransition(
            challenger.descriptor.niche_id,
            challenger.revision_id,
            incumbent.revision_id,
            decision,
            len(case_ids),
            challenger_only,
            mean_difference,
            lower,
        )

    def _require_candidate(self, revision_id: str) -> NicheCandidateRecord:
        try:
            return self._candidates[str(revision_id)]
        except KeyError as exc:
            raise KeyError(f"unknown niche revision: {revision_id}") from exc

    def _checkpoint(self) -> None:
        self._history.append(
            (deepcopy(self._candidates), deepcopy(self._elite_by_niche))
        )


def _family_blocked_lower(
    values: Sequence[tuple[str, float]],
    *,
    confidence: float,
    samples: int,
    seed: int,
) -> float:
    by_family: dict[str, list[float]] = {}
    for family_id, value in values:
        by_family.setdefault(family_id, []).append(float(value))
    family_means = tuple(
        fmean(by_family[key]) for key in sorted(by_family)
    )
    rng = random.Random(seed)
    draws = sorted(
        fmean(
            family_means[rng.randrange(len(family_means))]
            for _ in family_means
        )
        for _ in range(samples)
    )
    index = max(
        0,
        min(
            len(draws) - 1,
            int((1.0 - confidence) * len(draws)),
        ),
    )
    return draws[index]
