"""One auditable execution surface for CMD experiment Stages 5 through 9.

The runner coordinates the stage-specific executors without weakening their
capability boundaries. Missing discovery, feedback, transfer, oracle, or
industry capabilities remain explicit ``UNSUPPORTED`` results.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Mapping, Sequence

from cmd_audit.repair.ghost_ecology import SkillRevision

from .backbone_provider import BackboneProvider
from .contracts import canonical_sha256
from .ecology_transfer_executor import (
    EcologyTransferExecutor,
    SealedLibraryOracle,
    SkillCandidateProvider,
    STAGE6_ARMS,
    STAGE8A_ARMS,
    STAGE8B_ARMS,
)
from .experiment_matrix import STAGE5_VARIANTS, STAGE7_VARIANTS, STAGE9_VARIANTS
from .governance_system_executor import (
    ExecutionOrder,
    GovernanceSystemExecutor,
    ProposalProvider,
    SealedOracleProvider as GovernanceOracleProvider,
)
from .industry_adapters import IndustryAdapter, ResourceUsage as SystemBudget
from .prequential_executor import RuntimeOrderManifest
from .runtime_bundle import RuntimeBundle
from .stage5_executor import (
    DelayedFeedbackProvider,
    SealedOracleProvider as Stage5OracleProvider,
    Stage5ExecutionConfig,
    Stage5Executor,
)


STAGE59_SCHEMA = "cmd-spec-v03-stage59-runner-v1"
STAGE59_STAGES = ("stage5", "stage6", "stage7", "stage8a", "stage8b", "stage9")


@dataclass(frozen=True)
class Stage59Config:
    run_id: str
    model_id: str
    seed: int
    track: str = "controlled_a1"
    stages: tuple[str, ...] = STAGE59_STAGES
    development_non_model: bool = False
    schema_version: str = STAGE59_SCHEMA

    def __post_init__(self) -> None:
        if not self.run_id or not self.model_id:
            raise ValueError("Stage 5-9 config requires run and model IDs")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError("Stage 5-9 seed must be a non-negative integer")
        if self.track not in {"controlled_a1", "controlled_a2", "native"}:
            raise ValueError("unsupported Stage 5-9 track")
        if not self.stages or len(set(self.stages)) != len(self.stages) or not set(self.stages) <= set(STAGE59_STAGES):
            raise ValueError("Stage 5-9 stages must be a unique supported subset")
        if self.schema_version != STAGE59_SCHEMA:
            raise ValueError("unsupported Stage 5-9 runner schema")


@dataclass
class Stage59Capabilities:
    """Explicit runtime capabilities; evaluator powers are never inferred."""

    backbone_provider: BackboneProvider | None = None
    feedback_provider: DelayedFeedbackProvider | None = None
    stage5_oracle: Stage5OracleProvider | None = None
    candidate_provider: SkillCandidateProvider | None = None
    library_oracle: SealedLibraryOracle | None = None
    proposal_provider: ProposalProvider | None = None
    governance_oracle: GovernanceOracleProvider | None = None
    industry_adapters: Mapping[str, IndustryAdapter] = field(default_factory=dict)
    frozen_library: tuple[SkillRevision, ...] = ()
    seed_library: tuple[SkillRevision, ...] = ()
    source_library: tuple[SkillRevision, ...] = ()
    source_residual_snapshot: Mapping[str, object] | None = None
    target_prefix_snapshot: Mapping[str, object] | None = None


@dataclass(frozen=True)
class Stage59Report:
    schema_version: str
    config: Mapping[str, object]
    order_manifest_sha256: str
    results: Mapping[str, object]
    unsupported_capabilities: tuple[str, ...]
    report_sha256: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "config": dict(self.config),
            "order_manifest_sha256": self.order_manifest_sha256,
            "results": _jsonable(self.results),
            "unsupported_capabilities": list(self.unsupported_capabilities),
            "report_sha256": self.report_sha256,
        }


def _jsonable(value: object) -> object:
    if hasattr(value, "to_mapping"):
        return _jsonable(value.to_mapping())  # type: ignore[union-attr]
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(nested) for key, nested in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(nested) for nested in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"Stage 5-9 report cannot serialize {type(value).__name__}")


def _unsupported(stage: str, reason: str) -> dict[str, object]:
    body = {"stage": stage, "status": "UNSUPPORTED", "reason": reason}
    return {**body, "snapshot_sha256": canonical_sha256(body)}


class Stage59Runner:
    def __init__(self, config: Stage59Config, capabilities: Stage59Capabilities) -> None:
        self.config = config
        self.capabilities = capabilities

    def run(
        self,
        bundles: Sequence[RuntimeBundle],
        order: RuntimeOrderManifest,
        *,
        system_budget: SystemBudget,
    ) -> Stage59Report:
        order.verify()
        by_id = {bundle.case_id: bundle for bundle in bundles}
        if not by_id or len(by_id) != len(bundles) or set(by_id) != {row.case_id for row in order.rows}:
            raise ValueError("Stage 5-9 bundles must exactly match the frozen order")

        results: dict[str, object] = {}
        unsupported: set[str] = set()
        capabilities = self.capabilities

        if "stage5" in self.config.stages:
            if capabilities.backbone_provider is None or capabilities.feedback_provider is None:
                reason = "backbone_provider_missing" if capabilities.backbone_provider is None else "delayed_feedback_provider_missing"
                results["stage5"] = _unsupported("stage5", reason)
                unsupported.add(reason)
            else:
                stage5 = Stage5Executor(
                    Stage5ExecutionConfig(self.config.run_id + ":stage5", self.config.model_id, self.config.seed),
                    capabilities.backbone_provider,
                    capabilities.feedback_provider,
                    sealed_oracle_provider=capabilities.stage5_oracle,
                ).run(bundles, order)
                results["stage5"] = _jsonable(stage5)

        ecology = EcologyTransferExecutor(model_id=self.config.model_id, seed=self.config.seed)
        if "stage6" in self.config.stages:
            snapshots = []
            for arm in STAGE6_ARMS:
                snapshot = ecology.run_stage6(
                    arm,
                    bundles,
                    order,
                    candidate_provider=capabilities.candidate_provider,
                    sealed_library_oracle=capabilities.library_oracle,
                    frozen_library=capabilities.frozen_library,
                )
                snapshots.append(snapshot.to_mapping())
                if snapshot.status == "UNSUPPORTED" and snapshot.reason:
                    unsupported.add(snapshot.reason)
            results["stage6"] = snapshots

        governance = GovernanceSystemExecutor()
        if "stage7" in self.config.stages:
            records = []
            for row in order.rows:
                execution_order = ExecutionOrder(row.event_index, row.cas_interleaving == "conflicting")
                for arm in STAGE7_VARIANTS:
                    record = governance.execute_stage7(
                        by_id[row.case_id], execution_order, capabilities.proposal_provider,
                        variant=arm, run_id=self.config.run_id + ":stage7", track=self.config.track,
                        budget=system_budget, oracle_provider=capabilities.governance_oracle,
                    )
                    records.append(record.to_mapping())
                    if record.status == "UNSUPPORTED" and record.abstain_reason:
                        unsupported.add(record.abstain_reason)
            results["stage7"] = records

        if "stage8a" in self.config.stages:
            snapshots = []
            for arm in STAGE8A_ARMS:
                snapshot = ecology.run_stage8a(
                    arm,
                    bundles,
                    order,
                    source_residual_snapshot=capabilities.source_residual_snapshot,
                    source_skill_library=capabilities.source_library,
                    target_prefix_snapshot=capabilities.target_prefix_snapshot,
                    sealed_library_oracle=capabilities.library_oracle,
                )
                snapshots.append(snapshot.to_mapping())
                if snapshot.status == "UNSUPPORTED" and snapshot.reason:
                    unsupported.add(snapshot.reason)
            results["stage8a"] = snapshots

        if "stage8b" in self.config.stages:
            snapshots = []
            for arm in STAGE8B_ARMS:
                snapshot = ecology.run_stage8b(
                    arm,
                    bundles,
                    order,
                    seed_library=capabilities.seed_library,
                    source_library=capabilities.source_library,
                    target_candidate_provider=capabilities.candidate_provider,
                    sealed_library_oracle=capabilities.library_oracle,
                )
                snapshots.append(snapshot.to_mapping())
                if snapshot.status == "UNSUPPORTED" and snapshot.reason:
                    unsupported.add(snapshot.reason)
            results["stage8b"] = snapshots

        if "stage9" in self.config.stages:
            records = []
            for row in order.rows:
                execution_order = ExecutionOrder(row.event_index, row.cas_interleaving == "conflicting")
                for system_id in STAGE9_VARIANTS:
                    record = governance.execute_stage9(
                        by_id[row.case_id], execution_order, system_id=system_id,
                        run_id=self.config.run_id + ":stage9", track=self.config.track,
                        budget=system_budget, cmd_provider=capabilities.proposal_provider,
                        oracle_provider=capabilities.governance_oracle,
                        industry_adapters=capabilities.industry_adapters,
                    )
                    records.append(record.to_mapping())
                    if record.adapter_status == "UNSUPPORTED" and record.adapter_reason:
                        unsupported.add(record.adapter_reason)
            results["stage9"] = records

        config_mapping = asdict(self.config)
        body = {
            "schema_version": STAGE59_SCHEMA,
            "config": config_mapping,
            "order_manifest_sha256": order.source_content_sha256,
            "results": _jsonable(results),
            "unsupported_capabilities": sorted(unsupported),
        }
        return Stage59Report(
            schema_version=STAGE59_SCHEMA,
            config=config_mapping,
            order_manifest_sha256=order.source_content_sha256,
            results=results,
            unsupported_capabilities=tuple(sorted(unsupported)),
            report_sha256=canonical_sha256(body),
        )

