"""Repair-based offline audit cycle for SIGIL scope governance."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from cmd_audit.repair.scope_ledger import ScopeLedger, ScopeTransition


REQUIRED_PROVENANCE_FIELDS = frozenset(
    {
        "runtime_uses_gold",
        "uses_injection_control",
        "input_allowlist_sha256",
        "extractor_version",
        "evaluator_identity",
    }
)


@dataclass(frozen=True)
class ScopeAuditObservation:
    """One fired signal paired with post-outcome repair measurements."""

    case_id: str
    signal_type: str
    domain_fingerprint: str
    indication_action: str
    indication_gain: float | None
    oracle_gain: float | None
    frozen_gain: float | None
    family_id: str
    evidence_ids: tuple[str, ...] = ()
    success_threshold: float = 0.1
    oracle_epsilon: float = 0.05

    def __post_init__(self) -> None:
        if not self.case_id or not self.signal_type:
            raise ValueError("case_id and signal_type are required")
        if not self.domain_fingerprint or not self.indication_action:
            raise ValueError(
                "domain_fingerprint and indication_action are required"
            )
        if not self.family_id:
            raise ValueError("family_id is required for blocked inference")
        if self.success_threshold < 0.0 or self.oracle_epsilon < 0.0:
            raise ValueError(
                "success_threshold and oracle_epsilon must be non-negative"
            )

    @property
    def valid(self) -> bool:
        if not _all_finite(self.indication_gain, self.oracle_gain):
            return False
        indication = float(self.indication_gain)
        oracle = float(self.oracle_gain)
        return (
            indication >= self.success_threshold
            and indication >= oracle - self.oracle_epsilon
        )

    @property
    def incremental_gain(self) -> float:
        if not _all_finite(self.indication_gain, self.frozen_gain):
            return 0.0
        return float(self.indication_gain) - float(self.frozen_gain)

    @property
    def regret(self) -> float | None:
        if not _all_finite(self.indication_gain, self.oracle_gain):
            return None
        return max(0.0, float(self.oracle_gain) - float(self.indication_gain))


@dataclass(frozen=True)
class ScopeAuditEvent:
    signal_type: str
    domain_fingerprint: str
    case_ids: tuple[str, ...]
    family_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    previous_status: str
    new_status: str
    decision: str
    fires: int
    valid: int
    validity: float
    mean_incremental_gain: float
    mean_regret: float | None
    ci_lower: float
    ci_upper: float
    threshold: float
    n_min: int
    confidence: float
    bootstrap_samples: int
    seed: int
    audited_generation: int
    dataset_path: str
    dataset_sha256: str
    selected_case_ids_sha256: str
    provenance: Mapping[str, object]
    provenance_sha256: str

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["record_type"] = "scope_audit_event"
        return value


def audit_scope_signals(
    observations: Iterable[ScopeAuditObservation],
    *,
    ledger: ScopeLedger,
    generation: int,
    dataset_path: str | Path,
    provenance: Mapping[str, object],
) -> tuple[ScopeAuditEvent, ...]:
    """Audit fired signals and update the confidence-gated scope ledger."""

    validate_scope_audit_provenance(provenance)
    source = Path(dataset_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    dataset_sha256 = _file_sha256(source)
    grouped: dict[tuple[str, str], list[ScopeAuditObservation]] = {}
    for observation in observations:
        grouped.setdefault(
            (
                observation.signal_type,
                observation.domain_fingerprint,
            ),
            [],
        ).append(observation)

    output: list[ScopeAuditEvent] = []
    for key in sorted(grouped):
        rows = grouped[key]
        case_ids = tuple(row.case_id for row in rows)
        family_ids = tuple(row.family_id for row in rows)
        evidence_ids = tuple(
            sorted(
                {
                    evidence_id
                    for row in rows
                    for evidence_id in row.evidence_ids
                }
            )
        )
        selected_case_ids_sha256 = _canonical_sha256(case_ids)
        audit_provenance = {
            **dict(provenance),
            "provenance_contract_passed": True,
            "dataset_path": str(source),
            "dataset_sha256": dataset_sha256,
            "selected_case_ids_sha256": selected_case_ids_sha256,
            "generation": int(generation),
            "signal_type": key[0],
            "domain_fingerprint": key[1],
            "validity_definition": (
                "gain>=success_threshold_and_gain>=oracle-epsilon"
            ),
        }
        transition = ledger.audit(
            key[0],
            key[1],
            (row.valid for row in rows),
            incremental_gains=(row.incremental_gain for row in rows),
            family_ids=family_ids,
            generation=generation,
            provenance=audit_provenance,
        )
        output.append(
            _event_from_transition(
                transition,
                rows=rows,
                evidence_ids=evidence_ids,
                ledger=ledger,
                dataset_path=str(source),
                dataset_sha256=dataset_sha256,
                selected_case_ids_sha256=selected_case_ids_sha256,
                provenance=audit_provenance,
            )
        )
    return tuple(output)


def observations_from_repair_gains(
    rows: Iterable[Mapping[str, object]],
    *,
    domain_fingerprint: str,
    success_threshold: float = 0.1,
    oracle_epsilon: float = 0.05,
) -> tuple[ScopeAuditObservation, ...]:
    """Build repair-based audit observations from portable result rows."""

    output = []
    for row in rows:
        output.append(
            ScopeAuditObservation(
                case_id=str(row["case_id"]),
                signal_type=str(row["signal_type"]),
                domain_fingerprint=str(
                    row.get("domain_fingerprint") or domain_fingerprint
                ),
                indication_action=str(row["indication_action"]),
                indication_gain=_optional_float(row.get("indication_gain")),
                oracle_gain=_optional_float(row.get("oracle_gain")),
                frozen_gain=_optional_float(row.get("frozen_gain")),
                family_id=str(row.get("family_id") or row["case_id"]),
                evidence_ids=tuple(
                    str(value) for value in row.get("evidence_ids", ())
                ),
                success_threshold=success_threshold,
                oracle_epsilon=oracle_epsilon,
            )
        )
    return tuple(output)


def validate_scope_audit_provenance(
    provenance: Mapping[str, object],
) -> None:
    missing = sorted(REQUIRED_PROVENANCE_FIELDS - set(provenance))
    if missing:
        raise ValueError(
            "scope audit provenance missing: " + ", ".join(missing)
        )
    if provenance.get("runtime_uses_gold") is not False:
        raise ValueError("runtime_uses_gold must be false")
    if provenance.get("uses_injection_control") is not False:
        raise ValueError("uses_injection_control must be false")
    digest = str(provenance.get("input_allowlist_sha256") or "")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("input_allowlist_sha256 must be a SHA-256 hex digest")
    if not str(provenance.get("extractor_version") or ""):
        raise ValueError("extractor_version is required")
    if not str(provenance.get("evaluator_identity") or ""):
        raise ValueError("evaluator_identity is required")


def write_scope_audit_events(
    events: Sequence[ScopeAuditEvent],
    path: str | Path,
    *,
    append: bool = True,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with target.open(mode, encoding="utf-8") as handle:
        for event in events:
            handle.write(
                json.dumps(
                    event.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n"
            )
    return target


def _event_from_transition(
    transition: ScopeTransition,
    *,
    rows: Sequence[ScopeAuditObservation],
    evidence_ids: tuple[str, ...],
    ledger: ScopeLedger,
    dataset_path: str,
    dataset_sha256: str,
    selected_case_ids_sha256: str,
    provenance: Mapping[str, object],
) -> ScopeAuditEvent:
    regrets = [row.regret for row in rows if row.regret is not None]
    return ScopeAuditEvent(
        signal_type=transition.signal_type,
        domain_fingerprint=transition.domain_fingerprint,
        case_ids=tuple(row.case_id for row in rows),
        family_ids=tuple(row.family_id for row in rows),
        evidence_ids=evidence_ids,
        previous_status=transition.previous_status,
        new_status=transition.new_status,
        decision=transition.decision,
        fires=transition.fires,
        valid=transition.valid,
        validity=transition.validity,
        mean_incremental_gain=transition.mean_incremental_gain,
        mean_regret=sum(regrets) / len(regrets) if regrets else None,
        ci_lower=transition.ci_lower,
        ci_upper=transition.ci_upper,
        threshold=ledger.threshold,
        n_min=ledger.n_min,
        confidence=ledger.confidence,
        bootstrap_samples=ledger.bootstrap_samples,
        seed=ledger.seed,
        audited_generation=transition.audited_generation,
        dataset_path=dataset_path,
        dataset_sha256=dataset_sha256,
        selected_case_ids_sha256=selected_case_ids_sha256,
        provenance=dict(provenance),
        provenance_sha256=_canonical_sha256(provenance),
    )


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _all_finite(*values: float | None) -> bool:
    return all(value is not None and math.isfinite(float(value)) for value in values)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
