"""Executable prequential coordinator for frozen CMD runtime cases.

The executor owns event order and delayed settlement.  Each repair case gets an
independent versioned memory store, while every case in a run shares the same
pipeline, Mix GHOST router, and skill ecology.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Mapping, Protocol, Sequence

from cmd_audit.repair.ghost_ecology import SkillRevision

from .contracts import DecisionView, canonical_sha256
from .ecology_runtime import EcologyRuntime
from .repair_stream import MemoryState, execute_operator, operator_catalog
from .router_stage5 import BackbonePrediction
from .runtime_bundle import RuntimeBundle
from .runtime_pipeline import RuntimePipeline, build_legal_candidates
from .syndrome_runtime import decode_ecc_syndrome
from .system_runtime import MaturedObservation, PendingRepairOutcome, PrequentialCMDRuntime


EXECUTION_SCHEMA = "cmd-spec-v03-prequential-execution-v1"
ORDER_SCHEMA = "cmd-spec-v03-runtime-order-v1"


class PredictionProvider(Protocol):
    """The only model-facing capability required by the executor."""

    def predict(
        self,
        decision: DecisionView,
        state: MemoryState,
        candidates: Sequence[SkillRevision],
    ) -> BackbonePrediction:
        ...


class MaturityProvider(Protocol):
    """Observable delayed telemetry channel, separate from routing."""

    def observe(self, pending: PendingRepairOutcome, *, event_index: int) -> MaturedObservation:
        ...


@dataclass(frozen=True)
class StructuralDevelopmentMaturityProvider:
    """Development-only receipt channel with no recurrence look-ahead."""

    mode: str = "DEVELOPMENT_STRUCTURAL_ONLY"

    def observe(self, pending: PendingRepairOutcome, *, event_index: int) -> MaturedObservation:
        return MaturedObservation(pending.pending_id, event_index)


@dataclass(frozen=True)
class RuntimeOrderRow:
    case_id: str
    event_index: int
    regime: str
    receipt_matures_at: int
    cas_interleaving: str

    def __post_init__(self) -> None:
        if not self.case_id or not self.regime:
            raise ValueError("runtime order row requires case and regime")
        if self.event_index < 0 or self.receipt_matures_at <= self.event_index:
            raise ValueError("receipt maturity must be later than selection")
        if self.cas_interleaving not in {"benign", "conflicting"}:
            raise ValueError("unsupported CAS interleaving")


@dataclass(frozen=True)
class RuntimeOrderManifest:
    seed: int
    schedule: str
    rows: tuple[RuntimeOrderRow, ...]
    source_content_sha256: str
    schema_version: str = ORDER_SCHEMA

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "RuntimeOrderManifest":
        required = {"seed", "schedule", "rows", "content_sha256"}
        if set(value) != required or not isinstance(value.get("rows"), list):
            raise ValueError("event order manifest uses an unsupported schema")
        rows: list[RuntimeOrderRow] = []
        row_fields = {"case_id", "event_index", "regime", "receipt_matures_at", "cas_interleaving"}
        for raw in value["rows"]:
            if not isinstance(raw, Mapping) or set(raw) != row_fields:
                raise ValueError("event order row uses an unsupported schema")
            rows.append(RuntimeOrderRow(**raw))  # type: ignore[arg-type]
        manifest = cls(
            seed=value["seed"],  # type: ignore[arg-type]
            schedule=value["schedule"],  # type: ignore[arg-type]
            rows=tuple(rows),
            source_content_sha256=value["content_sha256"],  # type: ignore[arg-type]
        )
        manifest.verify()
        return manifest

    def verify(self) -> None:
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError("runtime order seed is invalid")
        indexes = tuple(row.event_index for row in self.rows)
        if indexes != tuple(range(len(self.rows))):
            raise ValueError("runtime order event indexes must be contiguous and ordered")
        if len({row.case_id for row in self.rows}) != len(self.rows):
            raise ValueError("runtime order must be a strict case permutation")
        body = {"seed": self.seed, "schedule": self.schedule, "rows": [asdict(row) for row in self.rows]}
        if canonical_sha256(body) != self.source_content_sha256:
            raise ValueError("runtime order content hash mismatch")


@dataclass(frozen=True)
class ExecutionConfig:
    run_id: str
    model_id: str
    router_name: str
    development: bool
    enable_cas_interleaving: bool = True
    schema_version: str = EXECUTION_SCHEMA

    def __post_init__(self) -> None:
        if not self.run_id or not self.model_id:
            raise ValueError("execution config requires run and model IDs")
        if self.router_name not in {"mix_ghost", "ghost_hierarchy"}:
            raise ValueError("execution router is unsupported")
        if self.schema_version != EXECUTION_SCHEMA:
            raise ValueError("execution config schema is unsupported")


@dataclass(frozen=True)
class ExecutionRecord:
    event_index: int
    case_id: str
    family_id: str
    diagnosed_classification: str
    selected_skill_revision_id: str | None
    selected_operator_id: str | None
    prediction_sha256: str | None
    committed: bool
    conflicted: bool
    gates_accepted: bool
    pending_id: str | None
    receipt_matures_at: int | None
    settled_receipt_ids_before_route: tuple[str, ...]
    abstained: bool
    abstain_reason: str | None


@dataclass(frozen=True)
class ExecutionReport:
    schema_version: str
    config: Mapping[str, object]
    order_manifest_sha256: str
    records: tuple[ExecutionRecord, ...]
    censored_selection_ids: tuple[str, ...]
    router_snapshot_sha256: str
    ecology_head_sha256: str
    provider_usage: Mapping[str, object]
    provider_call_audit_sha256s: tuple[str, ...]
    status: str
    report_sha256: str

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class _PendingBinding:
    runtime: PrequentialCMDRuntime
    pending: PendingRepairOutcome


class PrequentialExperimentExecutor:
    """Run one outcome-independent order without evaluator data access."""

    def __init__(
        self,
        config: ExecutionConfig,
        prediction_provider: PredictionProvider,
        *,
        maturity_provider: MaturityProvider | None = None,
    ) -> None:
        if maturity_provider is None:
            if not config.development:
                raise ValueError("confirmatory execution requires an external maturity provider")
            maturity_provider = StructuralDevelopmentMaturityProvider()
        if not config.development and isinstance(maturity_provider, StructuralDevelopmentMaturityProvider):
            raise ValueError("structural-only maturity feedback is development-only")
        self.config = config
        self.prediction_provider = prediction_provider
        self.maturity_provider = maturity_provider

    def run(
        self,
        cases: Sequence[RuntimeBundle],
        order: RuntimeOrderManifest,
    ) -> ExecutionReport:
        order.verify()
        case_map = {case.case_id: case for case in cases}
        if len(case_map) != len(cases) or set(case_map) != {row.case_id for row in order.rows}:
            raise ValueError("runtime cases and event order are not the same strict permutation")

        pipeline = RuntimePipeline(router_name=self.config.router_name, model_id=self.config.model_id)
        ecology = EcologyRuntime(pipeline.router, model_id=self.config.model_id)
        pending: list[_PendingBinding] = []
        records: list[ExecutionRecord] = []

        for row in order.rows:
            for binding in tuple(pending):
                if binding.pending.matures_at_event_index <= row.event_index:
                    observation = self.maturity_provider.observe(binding.pending, event_index=row.event_index)
                    if observation.pending_id != binding.pending.pending_id:
                        raise ValueError("maturity provider changed pending identity")
                    binding.runtime.finalize_matured(
                        observation.pending_id,
                        observation.observed_event_index,
                        recurrence_after_commit=observation.recurrence_after_commit,
                        safety_violation=observation.safety_violation,
                        integrity_violation=observation.integrity_violation,
                        provenance=observation.provenance,
                    )
                    pending.remove(binding)

            case = case_map[row.case_id]
            decision = replace(case.decision_view, event_index=row.event_index)
            runtime = PrequentialCMDRuntime(case.memory_state, pipeline=pipeline, ecology=ecology)
            syndrome = decode_ecc_syndrome(decision, case.memory_state)
            prediction: BackbonePrediction | None = None
            if not syndrome.abstains:
                candidate_log = build_legal_candidates(case.memory_state, syndrome)
                skills = tuple(pipeline.frozen_skill(skill_id) for skill_id in candidate_log.skill_revision_ids)
                prediction = self.prediction_provider.predict(decision, case.memory_state, skills)

            before_commit = None
            if self.config.enable_cas_interleaving and row.cas_interleaving == "conflicting" and prediction is not None:
                operator_by_skill = {
                    skill.skill_revision_id: skill.skill_id.removeprefix("runtime:")
                    for skill in pipeline.frozen_skill_library
                }
                operator_id = operator_by_skill[prediction.selected_skill_revision_id]
                spec = next(spec for spec in operator_catalog() if spec.operator_id == operator_id)

                def concurrent_writer(store: object, *, selected_spec: object = spec) -> None:
                    current = store.state  # type: ignore[attr-defined]
                    store.replace_current(execute_operator(current, selected_spec))  # type: ignore[attr-defined,arg-type]

                before_commit = concurrent_writer

            outcome = runtime.process(
                decision,
                prediction,
                observed_after_event_index=row.receipt_matures_at,
                before_commit=before_commit,
            )
            if outcome.pending is not None:
                pending.append(_PendingBinding(runtime, outcome.pending))
            records.append(ExecutionRecord(
                event_index=row.event_index,
                case_id=case.case_id,
                family_id=case.family_id,
                diagnosed_classification=outcome.decision.syndrome.descriptor.classification,
                selected_skill_revision_id=outcome.decision.selected_skill_revision_id,
                selected_operator_id=outcome.decision.selected_operator_id,
                prediction_sha256=None if prediction is None else prediction.prediction_sha256,
                committed=bool(outcome.commit and outcome.commit.committed),
                conflicted=bool(outcome.commit and outcome.commit.conflicted),
                gates_accepted=bool(outcome.gates and outcome.gates.accepted),
                pending_id=None if outcome.pending is None else outcome.pending.pending_id,
                receipt_matures_at=None if outcome.pending is None else outcome.pending.matures_at_event_index,
                settled_receipt_ids_before_route=tuple(row.receipt_id for row in outcome.settlements),
                abstained=outcome.abstain is not None,
                abstain_reason=outcome.decision.abstain_reason,
            ))

        horizon = len(order.rows) - 1
        censored = ecology.right_censor(horizon) if pending else ()
        router_sha = str(pipeline.router.snapshot["snapshot_sha256"])
        config_mapping = asdict(self.config)
        raw_usage = getattr(self.prediction_provider, "usage", None)
        provider_usage = asdict(raw_usage) if raw_usage is not None else {}
        raw_audits = tuple(getattr(self.prediction_provider, "call_audit", ()))
        provider_audit_hashes = tuple(canonical_sha256(asdict(audit)) for audit in raw_audits)
        body = {
            "schema_version": EXECUTION_SCHEMA,
            "config": config_mapping,
            "order_manifest_sha256": order.source_content_sha256,
            "records": [asdict(record) for record in records],
            "censored_selection_ids": list(censored),
            "router_snapshot_sha256": router_sha,
            "ecology_head_sha256": ecology.head_sha256,
            "provider_usage": provider_usage,
            "provider_call_audit_sha256s": list(provider_audit_hashes),
            "status": "DEVELOPMENT_COMPLETE" if self.config.development else "CONFIRMATORY_COMPLETE",
        }
        return ExecutionReport(
            EXECUTION_SCHEMA,
            config_mapping,
            order.source_content_sha256,
            tuple(records),
            tuple(censored),
            router_sha,
            ecology.head_sha256,
            provider_usage,
            provider_audit_hashes,
            body["status"],
            canonical_sha256(body),
        )
