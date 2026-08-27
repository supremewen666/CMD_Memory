"""Executable, result-free Stage 6 ecology and Stage 8 transfer arms.

This module deliberately compiles *arm state* rather than model outcomes.  It
uses only runtime-visible bundles and a frozen event order.  Candidate discovery
and sealed-library access are explicit capabilities so an absent oracle remains
an auditable ``UNSUPPORTED`` arm instead of a fabricated result.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping, Protocol, Sequence
import random

from cmd_audit.repair.ghost_ecology import (
    FailureDeposit,
    ObservableResidualGHOSTRouter,
    SkillRevision,
    content_sha256,
)

from .contracts import canonical_sha256
from .ecology_runtime import EcologyRuntime
from .experiment_matrix import STAGE8A_VARIANTS, STAGE8B_VARIANTS
from .prequential_executor import RuntimeOrderManifest
from .repair_stream import MemoryState, execute_operator, operator_catalog
from .runtime_bundle import RuntimeBundle
from .syndrome_runtime import audit_structural_telemetry


STAGE6_ARMS = (
    "no_skill", "seed_frozen", "add_only", "add_dedup", "add_revision",
    "add_revision_retirement", "full_ecology", "random_key_ecology",
    "oracle_library",
)
STAGE8A_ARMS = STAGE8A_VARIANTS
STAGE8B_ARMS = STAGE8B_VARIANTS
_COMPLETE = "READY_NO_MODEL_RESULTS"
_UNSUPPORTED = "UNSUPPORTED"


class SkillCandidateProvider(Protocol):
    """Discovery capability. Returned revisions must be typed operator programs."""

    def candidates(
        self, bundle: RuntimeBundle, *, event_index: int, failure: FailureDeposit,
    ) -> Sequence[SkillRevision]:
        ...


class SealedLibraryOracle(Protocol):
    """Evaluator-side capability, injected only for oracle arms."""

    def library(self, bundle: RuntimeBundle, *, event_index: int) -> Sequence[SkillRevision]:
        ...

    def legal_operator(self, bundle: RuntimeBundle, *, event_index: int) -> str:
        ...


@dataclass(frozen=True)
class ArmSnapshot:
    """Closed, hashed arm state with no score, accuracy, or model output field."""

    stage: str
    arm: str
    status: str
    reason: str | None
    order_manifest_sha256: str
    router_snapshot_sha256: str | None
    ecology_snapshot_sha256: str | None
    skill_content_sha256s: tuple[str, ...]
    evidence_state_sha256s: tuple[str, ...]
    residual_snapshot_sha256: str | None
    transitions: tuple[tuple[str, str], ...]
    snapshot_sha256: str

    @classmethod
    def create(
        cls, *, stage: str, arm: str, status: str, reason: str | None,
        order_manifest_sha256: str, router_snapshot_sha256: str | None,
        ecology_snapshot_sha256: str | None, skill_content_sha256s: Sequence[str],
        evidence_state_sha256s: Sequence[str], residual_snapshot_sha256: str | None,
        transitions: Sequence[tuple[str, str]],
    ) -> "ArmSnapshot":
        body = {
            "stage": stage, "arm": arm, "status": status, "reason": reason,
            "order_manifest_sha256": order_manifest_sha256,
            "router_snapshot_sha256": router_snapshot_sha256,
            "ecology_snapshot_sha256": ecology_snapshot_sha256,
            "skill_content_sha256s": tuple(sorted(skill_content_sha256s)),
            "evidence_state_sha256s": tuple(sorted(evidence_state_sha256s)),
            "residual_snapshot_sha256": residual_snapshot_sha256,
            "transitions": tuple(transitions),
        }
        return cls(**body, snapshot_sha256=canonical_sha256(body))

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


def _unsupported(stage: str, arm: str, order: RuntimeOrderManifest, reason: str) -> ArmSnapshot:
    return ArmSnapshot.create(
        stage=stage, arm=arm, status=_UNSUPPORTED, reason=reason,
        order_manifest_sha256=order.source_content_sha256, router_snapshot_sha256=None,
        ecology_snapshot_sha256=None, skill_content_sha256s=(), evidence_state_sha256s=(),
        residual_snapshot_sha256=None, transitions=(),
    )


def _validate_inputs(bundles: Sequence[RuntimeBundle], order: RuntimeOrderManifest) -> dict[str, RuntimeBundle]:
    order.verify()
    by_id = {bundle.case_id: bundle for bundle in bundles}
    if not by_id or len(by_id) != len(bundles) or set(by_id) != {row.case_id for row in order.rows}:
        raise ValueError("bundles must exactly match the frozen runtime order")
    return by_id


def _failure(bundle: RuntimeBundle) -> FailureDeposit:
    return FailureDeposit(
        failure_id="stage6-" + content_sha256({"case_id": bundle.case_id, "root": bundle.memory_state.root}),
        case_id=bundle.case_id,
        family_id_audit_only=bundle.family_id,
        failure_memory_sha256=content_sha256({"decision": bundle.decision_view.content_sha256}),
        features=(("event_count", float(len(bundle.memory_state.immutable_source_log) + len(bundle.memory_state.audit_log))),),
        context_sha256=bundle.memory_state.root,
        provenance_sha256=bundle.decision_view.content_sha256,
    )


def _typed_catalog_spec(candidate: SkillRevision):
    """Validate portable program structure without importing outcome evidence."""
    program = candidate.program
    if candidate.state != "stable" or program.get("kind") != "cmd-spec-v03-operator":
        raise ValueError("candidate must be a stable typed operator program")
    operator_id = program.get("operator_id")
    if not isinstance(operator_id, str):
        raise ValueError("candidate typed program lacks operator_id")
    spec = next((row for row in operator_catalog() if row.operator_id == operator_id), None)
    if spec is None or program.get("write_contract") != spec.write_contract:
        raise ValueError("candidate operator program does not match the typed catalog")
    return spec


def _typed_replay_gate(candidate: SkillRevision, bundle: RuntimeBundle) -> None:
    """Accept only an executable typed operator whose shadow replay is safe/local."""
    spec = _typed_catalog_spec(candidate)
    before = bundle.memory_state
    after = execute_operator(before, spec)
    if after == before or after.immutable_source_log != before.immutable_source_log or after.audit_log != before.audit_log:
        raise ValueError("candidate fails safety/locality shadow replay")
    changed = sum(
        getattr(before, field) != getattr(after, field)
        for field in ("projection_order", "projection_index", "scope_projection", "cache_event_ids", "supersession_edges", "quarantine_set")
    )
    after_observation = dict(bundle.decision_view.observation)
    current = dict(after_observation["current_state"])
    current["state_root"] = after.root
    after_observation["current_state"] = current
    after_decision = type(bundle.decision_view)(
        case_id=bundle.decision_view.case_id,
        source_dataset_id=bundle.decision_view.source_dataset_id,
        source_episode_id=bundle.decision_view.source_episode_id,
        family_id=bundle.decision_view.family_id,
        lineage_id=bundle.decision_view.lineage_id,
        event_index=bundle.decision_view.event_index,
        observation=after_observation,
        provenance=bundle.decision_view.provenance,
        unsupported_fields=bundle.decision_view.unsupported_fields,
    )
    if changed > spec.locality_bound or audit_structural_telemetry(after_decision, after).classification != "clean":
        raise ValueError("candidate fails safety/locality replay gate")


def _seed(ecology: EcologyRuntime, skills: Sequence[SkillRevision], *, event_index: int, transitions: list[tuple[str, str]]) -> None:
    for skill in sorted(skills, key=lambda row: row.skill_revision_id):
        _typed_catalog_spec(skill)
        if skill.skill_revision_id not in ecology.skills:
            ecology.seed_frozen_skill(skill, event_index=event_index)
            transitions.append((skill.skill_revision_id, "seed@t" + str(event_index)))


def _apply_candidate(
    ecology: EcologyRuntime, candidate: SkillRevision, bundle: RuntimeBundle, failure: FailureDeposit,
    *, event_index: int, arm: str, transitions: list[tuple[str, str]],
) -> None:
    _typed_replay_gate(candidate, bundle)
    if candidate.producing_failure_id != failure.failure_id:
        raise ValueError("candidate must bind the supplied FailureMemory deposit")
    ecology.deposit_failure(failure, event_index=event_index)
    known = ecology.skills
    duplicate = next((record for record in known.values() if record.content.program_sha256 == candidate.program_sha256), None)
    if duplicate is not None and arm in {"add_dedup", "full_ecology", "random_key_ecology"}:
        transitions.append((candidate.skill_revision_id, "dedup:" + duplicate.content.skill_revision_id))
        return
    parent_ids = tuple(candidate.parent_revision_ids)
    if arm in {"add_revision", "add_revision_retirement", "full_ecology", "random_key_ecology"} and parent_ids:
        parent = parent_ids[0]
        if parent not in known:
            raise ValueError("revision candidate names an unknown parent")
        ecology.supersede(parent, candidate, event_index=event_index)
        transitions.append((candidate.skill_revision_id, "supersede@t+1:" + parent))
    else:
        ecology.birth(candidate, event_index=event_index)
        transitions.append((candidate.skill_revision_id, "birth@t+1"))
    if arm == "add_revision_retirement":
        ecology.retire(candidate.skill_revision_id, reason="stage6-replay-retirement", event_index=event_index)
        transitions.append((candidate.skill_revision_id, "retire@t+1"))


class EcologyTransferExecutor:
    """Compile Stage 6/8 arm states. No method invokes a language model."""

    def __init__(self, *, model_id: str = "unconfigured", seed: int = 0) -> None:
        self.model_id = model_id
        self.seed = seed

    def run_stage6(
        self, arm: str, bundles: Sequence[RuntimeBundle], order: RuntimeOrderManifest, *,
        candidate_provider: SkillCandidateProvider | None = None,
        sealed_library_oracle: SealedLibraryOracle | None = None,
        frozen_library: Sequence[SkillRevision] = (),
    ) -> ArmSnapshot:
        if arm not in STAGE6_ARMS:
            raise ValueError("unsupported Stage 6 arm")
        by_id = _validate_inputs(bundles, order)
        if arm == "oracle_library" and sealed_library_oracle is None:
            return _unsupported("stage6", arm, order, "sealed_library_oracle_missing")
        if arm == "seed_frozen" and not frozen_library:
            return _unsupported("stage6", arm, order, "frozen_library_missing")
        if arm not in {"no_skill", "seed_frozen", "oracle_library"} and candidate_provider is None:
            return _unsupported("stage6", arm, order, "skill_candidate_provider_missing")

        router = ObservableResidualGHOSTRouter(seed=self.seed, allow_development_proxy=True)
        ecology = EcologyRuntime(router, model_id=self.model_id)
        transitions: list[tuple[str, str]] = []
        if arm == "seed_frozen":
            _seed(ecology, frozen_library, event_index=0, transitions=transitions)
        elif arm in {"add_revision", "add_revision_retirement"} and frozen_library:
            _seed(ecology, frozen_library, event_index=0, transitions=transitions)
        for row in order.rows:
            bundle = by_id[row.case_id]
            if arm == "oracle_library":
                assert sealed_library_oracle is not None
                supplied = sealed_library_oracle.library(bundle, event_index=row.event_index)
                for skill in supplied:
                    _typed_catalog_spec(skill)
                _seed(ecology, supplied, event_index=row.event_index, transitions=transitions)
            elif arm not in {"no_skill", "seed_frozen"} and candidate_provider is not None:
                failure = _failure(bundle)
                candidates = tuple(candidate_provider.candidates(bundle, event_index=row.event_index, failure=failure))
                if arm == "random_key_ecology":
                    rng = random.Random(int(content_sha256({"seed": self.seed, "case": bundle.case_id})[:16], 16))
                    candidates = tuple(sorted(candidates, key=lambda _: rng.random()))
                for candidate in candidates:
                    _apply_candidate(ecology, candidate, bundle, failure, event_index=row.event_index, arm=arm, transitions=transitions)
        snapshot = ecology.snapshot
        content = tuple(record.content.program_sha256 for record in ecology.skills.values())
        evidence = tuple(record.evidence.evidence_state_sha256 for record in ecology.skills.values())
        return ArmSnapshot.create(
            stage="stage6", arm=arm, status=_COMPLETE, reason=None,
            order_manifest_sha256=order.source_content_sha256,
            router_snapshot_sha256=str(router.snapshot["snapshot_sha256"]),
            ecology_snapshot_sha256=str(snapshot["snapshot_sha256"]),
            skill_content_sha256s=content, evidence_state_sha256s=evidence,
            residual_snapshot_sha256=str(router.snapshot["snapshot_sha256"]), transitions=transitions,
        )

    def run_stage8a(
        self, arm: str, bundles: Sequence[RuntimeBundle], order: RuntimeOrderManifest, *,
        source_residual_snapshot: Mapping[str, object] | None = None,
        source_skill_library: Sequence[SkillRevision] = (),
        target_prefix_snapshot: Mapping[str, object] | None = None,
        sealed_library_oracle: SealedLibraryOracle | None = None,
    ) -> ArmSnapshot:
        if arm not in STAGE8A_ARMS:
            raise ValueError("unsupported Stage 8A arm")
        by_id = _validate_inputs(bundles, order)
        content_arms = {"skill_content_only", "reset_online", "frozen_source", "niche_shuffled", "mean_only", "reset_prefix", "source_prefix"}
        residual_arms = {"reset_online", "frozen_source", "niche_shuffled", "mean_only", "reset_prefix", "source_prefix"}
        if arm in content_arms and not source_skill_library:
            return _unsupported("stage8a", arm, order, "source_skill_library_missing")
        if arm in residual_arms and source_residual_snapshot is None:
            return _unsupported("stage8a", arm, order, "source_residual_snapshot_missing")
        if arm in {"reset_prefix", "source_prefix"} and target_prefix_snapshot is None:
            return _unsupported("stage8a", arm, order, "target_prefix_snapshot_missing")
        if arm == "oracle_legal_operator" and sealed_library_oracle is None:
            return _unsupported("stage8a", arm, order, "sealed_library_oracle_missing")

        content = ()
        if arm in content_arms:
            for skill in source_skill_library:
                _typed_catalog_spec(skill)
            content = tuple(skill.program_sha256 for skill in source_skill_library)
        if arm == "no_repair":
            transition, router = "no_content_no_residual_no_evidence", None
        elif arm == "random_legal":
            transition, router = "legal_mask_only_no_content_no_residual_no_evidence", None
        elif arm == "skill_content_only":
            transition, router = "source_content_only_reset_residual_evidence", None
        elif arm == "oracle_legal_operator":
            assert sealed_library_oracle is not None
            # The oracle may be called only over the frozen order. Its output is
            # validated against a typed shadow replay and is never retained.
            for row in order.rows:
                bundle = by_id[row.case_id]
                operator_id = sealed_library_oracle.legal_operator(bundle, event_index=row.event_index)
                spec = next((item for item in operator_catalog() if item.operator_id == operator_id), None)
                if spec is None or execute_operator(bundle.memory_state, spec) == bundle.memory_state:
                    raise ValueError("sealed oracle selected a non-legal typed operator")
            transition, router = "sealed_oracle_legal_operator_no_transfer", None
        elif arm in {"reset_prefix", "source_prefix"}:
            assert target_prefix_snapshot is not None
            router = ObservableResidualGHOSTRouter.from_snapshot(target_prefix_snapshot)
            transition = "target_prefix_from_reset_residual" if arm == "reset_prefix" else "target_prefix_from_source_residual"
        else:
            assert source_residual_snapshot is not None
            source = ObservableResidualGHOSTRouter.from_snapshot(source_residual_snapshot)
            if arm == "frozen_source":
                router = source
                transition = "source_content_source_residual_reset_evidence"
            else:
                # reset_online, niche_shuffled, and mean_only each construct an
                # isolated posterior with the same frozen router configuration.
                raw = dict(source.snapshot)
                payload = {key: value for key, value in raw.items() if key != "snapshot_sha256"}
                stats = list(payload["stats"])
                if arm == "niche_shuffled":
                    rng = random.Random(self.seed)
                    skill_ids = sorted({str(key[-1]) for key, _precision, _natural in stats if len(key) > 1})
                    shuffled_ids = list(skill_ids)
                    rng.shuffle(shuffled_ids)
                    replacement = dict(zip(skill_ids, shuffled_ids, strict=True))
                    payload["stats"] = [
                        [list(key[:-1]) + [replacement.get(str(key[-1]), str(key[-1]))], precision, natural]
                        for key, precision, natural in stats
                    ]
                    transition = "source_content_niche_shuffled_residual_reset_evidence"
                elif arm == "mean_only":
                    grouped: dict[str, list[float]] = {}
                    for key, precision, natural in stats:
                        grouped.setdefault(str(key[0]), []).append(float(natural) / float(precision))
                    payload["stats"] = [
                        [key, precision, float(precision) * sum(grouped[str(key[0])]) / len(grouped[str(key[0])])]
                        for key, precision, _natural in stats
                    ]
                    transition = "source_content_mean_residual_reset_evidence"
                else:
                    payload["stats"] = []
                    transition = "source_content_reset_online_residual_evidence"
                payload["snapshot_sha256"] = content_sha256(payload)
                router = ObservableResidualGHOSTRouter.from_snapshot(payload)
        snapshot = None if router is None else router.snapshot
        return ArmSnapshot.create(
            stage="stage8a", arm=arm, status=_COMPLETE, reason=None,
            order_manifest_sha256=order.source_content_sha256,
            router_snapshot_sha256=None if snapshot is None else str(snapshot["snapshot_sha256"]), ecology_snapshot_sha256=None,
            skill_content_sha256s=content, evidence_state_sha256s=(),
            residual_snapshot_sha256=None if snapshot is None else str(snapshot["snapshot_sha256"]), transitions=((arm, transition),),
        )

    def run_stage8b(
        self, arm: str, bundles: Sequence[RuntimeBundle], order: RuntimeOrderManifest, *,
        seed_library: Sequence[SkillRevision] = (), source_library: Sequence[SkillRevision] = (),
        target_candidate_provider: SkillCandidateProvider | None = None,
        sealed_library_oracle: SealedLibraryOracle | None = None,
    ) -> ArmSnapshot:
        if arm not in STAGE8B_ARMS:
            raise ValueError("unsupported Stage 8B arm")
        by_id = _validate_inputs(bundles, order)
        first = by_id[order.rows[0].case_id]
        if arm == "seed_only":
            library = tuple(seed_library)
        elif arm == "source_skills":
            library = tuple(source_library)
        elif arm == "target_native_skills":
            if target_candidate_provider is None:
                return _unsupported("stage8b", arm, order, "skill_candidate_provider_missing")
            library = tuple(target_candidate_provider.candidates(first, event_index=0, failure=_failure(first)))
        else:
            if sealed_library_oracle is None:
                return _unsupported("stage8b", arm, order, "sealed_library_oracle_missing")
            library = tuple(sealed_library_oracle.library(first, event_index=0))
        if not library:
            return _unsupported("stage8b", arm, order, "library_missing")
        for skill in library:
            _typed_catalog_spec(skill)
            if arm == "target_native_skills":
                _typed_replay_gate(skill, first)
        # Stage 8B carries content only. Evidence and residual posterior are reset.
        content = tuple(sorted(skill.program_sha256 for skill in library))
        return ArmSnapshot.create(
            stage="stage8b", arm=arm, status=_COMPLETE, reason=None,
            order_manifest_sha256=order.source_content_sha256, router_snapshot_sha256=None,
            ecology_snapshot_sha256=None, skill_content_sha256s=content,
            evidence_state_sha256s=(), residual_snapshot_sha256=None,
            transitions=tuple((skill.skill_revision_id, "content_only") for skill in sorted(library, key=lambda row: row.skill_revision_id)),
        )
