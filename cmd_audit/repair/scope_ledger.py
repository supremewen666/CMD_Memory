"""Audited, family-blocked scope ledger for structural indications."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from statistics import fmean
import hashlib
import json
import random
from pathlib import Path
from typing import Iterable, Literal, Mapping, Sequence

from .structural_router import ScopePolicy


ScopeStatus = Literal["shadow", "active", "retired"]
_VALID_STATUSES = frozenset({"shadow", "active", "retired"})


@dataclass
class ScopeLedgerEntry:
    signal_type: str
    domain_fingerprint: str
    fires: int = 0
    valid: int = 0
    incremental_gain_sum: float = 0.0
    status: ScopeStatus = "shadow"
    audited_generation: int = 0
    provenance: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.signal_type or not self.domain_fingerprint:
            raise ValueError("signal_type and domain_fingerprint are required")
        if self.fires < 0 or self.valid < 0 or self.valid > self.fires:
            raise ValueError("ledger counts must satisfy 0 <= valid <= fires")
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"invalid scope status: {self.status}")
        if self.audited_generation < 0:
            raise ValueError("audited_generation must be >= 0")

    @property
    def audited_validity(self) -> float | None:
        return self.valid / self.fires if self.fires else None

    @property
    def mean_incremental_gain(self) -> float | None:
        return (
            self.incremental_gain_sum / self.fires if self.fires else None
        )


@dataclass(frozen=True)
class ScopeAuditDatum:
    valid: bool
    incremental_gain: float
    family_id: str


@dataclass(frozen=True)
class ScopeTransition:
    signal_type: str
    domain_fingerprint: str
    previous_status: ScopeStatus
    new_status: ScopeStatus
    fires: int
    valid: int
    validity: float
    mean_incremental_gain: float
    ci_lower: float
    ci_upper: float
    decision: str
    audited_generation: int


class ScopeLedger:
    """Mutable ledger with precision, utility, and symmetric retirement gates."""

    def __init__(
        self,
        entries: Iterable[ScopeLedgerEntry] = (),
        *,
        threshold: float = 0.8,
        n_min: int = 30,
        confidence: float = 0.95,
        bootstrap_samples: int = 2000,
        seed: int = 0,
    ) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be in [0, 1]")
        if n_min < 1:
            raise ValueError("n_min must be >= 1")
        if not 0.0 < confidence < 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if bootstrap_samples < 100:
            raise ValueError("bootstrap_samples must be >= 100")
        self.threshold = float(threshold)
        self.n_min = int(n_min)
        self.confidence = float(confidence)
        self.bootstrap_samples = int(bootstrap_samples)
        self.seed = int(seed)
        self._entries: dict[tuple[str, str], ScopeLedgerEntry] = {}
        self._data: dict[tuple[str, str], list[ScopeAuditDatum]] = {}
        self._history: list[
            tuple[
                dict[tuple[str, str], ScopeLedgerEntry],
                dict[tuple[str, str], list[ScopeAuditDatum]],
            ]
        ] = []
        for entry in entries:
            key = (entry.signal_type, entry.domain_fingerprint)
            if key in self._entries:
                raise ValueError(f"duplicate scope ledger entry: {key}")
            self._entries[key] = deepcopy(entry)
            mean_gain = float(entry.mean_incremental_gain or 0.0)
            self._data[key] = [
                ScopeAuditDatum(
                    valid=index < entry.valid,
                    incremental_gain=mean_gain,
                    family_id=f"legacy:{index}",
                )
                for index in range(entry.fires)
            ]

    def entries(self) -> tuple[ScopeLedgerEntry, ...]:
        return tuple(
            deepcopy(self._entries[key]) for key in sorted(self._entries)
        )

    def get(
        self,
        signal_type: str,
        domain_fingerprint: str,
    ) -> ScopeLedgerEntry | None:
        entry = self._entries.get((signal_type, domain_fingerprint))
        return deepcopy(entry) if entry is not None else None

    def audit(
        self,
        signal_type: str,
        domain_fingerprint: str,
        outcomes: Iterable[bool],
        *,
        incremental_gains: Iterable[float] | None = None,
        family_ids: Iterable[str] | None = None,
        generation: int,
        provenance: Mapping[str, object],
    ) -> ScopeTransition:
        """Append repair-based audit data and apply the lifecycle gate."""

        if generation < 0:
            raise ValueError("generation must be >= 0")
        audited = [bool(value) for value in outcomes]
        if not audited:
            raise ValueError("audit requires at least one fired observation")
        gains = (
            [float(value) for value in incremental_gains]
            if incremental_gains is not None
            else [0.0] * len(audited)
        )
        families = (
            [str(value) for value in family_ids]
            if family_ids is not None
            else [f"audit:{generation}:{index}" for index in range(len(audited))]
        )
        if len(gains) != len(audited) or len(families) != len(audited):
            raise ValueError(
                "outcomes, incremental_gains, and family_ids must align"
            )
        if any(not _finite(value) for value in gains):
            raise ValueError("incremental gains must be finite")
        if any(not value for value in families):
            raise ValueError("family ids must not be empty")

        key = (str(signal_type), str(domain_fingerprint))
        self._history.append((deepcopy(self._entries), deepcopy(self._data)))
        entry = self._entries.setdefault(
            key,
            ScopeLedgerEntry(
                signal_type=key[0],
                domain_fingerprint=key[1],
            ),
        )
        stored = self._data.setdefault(key, [])
        stored.extend(
            ScopeAuditDatum(valid, gain, family)
            for valid, gain, family in zip(
                audited,
                gains,
                families,
                strict=True,
            )
        )
        entry.fires = len(stored)
        entry.valid = sum(row.valid for row in stored)
        entry.incremental_gain_sum = sum(
            row.incremental_gain for row in stored
        )
        entry.audited_generation = int(generation)
        entry.provenance = dict(provenance)

        ci_lower, ci_upper = _family_blocked_interval(
            stored,
            confidence=self.confidence,
            samples=self.bootstrap_samples,
            seed=self.seed + generation,
        )
        validity = entry.valid / entry.fires
        mean_incremental_gain = float(entry.mean_incremental_gain or 0.0)
        provenance_ok = (
            provenance.get("provenance_contract_passed") is True
        )
        previous = entry.status
        if (
            entry.status == "shadow"
            and entry.fires >= self.n_min
            and ci_lower >= self.threshold
            and mean_incremental_gain > 0.0
            and provenance_ok
        ):
            entry.status = "active"
            decision = "promote"
        elif (
            entry.status == "active"
            and entry.fires >= self.n_min
            and (ci_upper < self.threshold or not provenance_ok)
        ):
            entry.status = "retired"
            decision = (
                "retire"
                if ci_upper < self.threshold
                else "retire_provenance_failure"
            )
        elif entry.status == "retired":
            decision = "retired_hold"
        elif entry.fires < self.n_min:
            decision = "insufficient_support"
        elif entry.status == "shadow" and ci_lower < self.threshold:
            decision = "ci_below_promotion_threshold"
        elif entry.status == "shadow" and not provenance_ok:
            decision = "provenance_gate_failed"
        elif entry.status == "shadow":
            decision = "nonpositive_incremental_gain"
        else:
            decision = "ci_not_below_retirement_threshold"

        return ScopeTransition(
            signal_type=entry.signal_type,
            domain_fingerprint=entry.domain_fingerprint,
            previous_status=previous,
            new_status=entry.status,
            fires=entry.fires,
            valid=entry.valid,
            validity=validity,
            mean_incremental_gain=mean_incremental_gain,
            ci_lower=ci_lower,
            ci_upper=ci_upper,
            decision=decision,
            audited_generation=entry.audited_generation,
        )

    def rollback(self) -> tuple[ScopeLedgerEntry, ...]:
        if not self._history:
            raise RuntimeError("no scope audit to roll back")
        self._entries, self._data = self._history.pop()
        return self.entries()

    def to_scope_policy(self) -> ScopePolicy:
        domains: dict[str, list[str]] = {}
        for entry in self._entries.values():
            if (
                entry.status == "active"
                and entry.provenance.get("provenance_contract_passed") is True
            ):
                domains.setdefault(entry.signal_type, []).append(
                    entry.domain_fingerprint
                )
        payload = [
            (signal_type, sorted(values))
            for signal_type, values in sorted(domains.items())
        ]
        digest = hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:12]
        return ScopePolicy.active(
            domains,
            domains=domains,
            version=f"scope-{digest}",
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "version": "sigil-scope-ledger-v2",
            "config": {
                "threshold": self.threshold,
                "n_min": self.n_min,
                "confidence": self.confidence,
                "bootstrap_samples": self.bootstrap_samples,
                "seed": self.seed,
            },
            "entries": [
                {
                    **asdict(self._entries[key]),
                    "audit_data": [
                        asdict(value) for value in self._data.get(key, ())
                    ],
                }
                for key in sorted(self._entries)
            ],
        }

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                self.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return target

    @classmethod
    def read(cls, path: str | Path) -> "ScopeLedger":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        version = payload.get("version")
        if version not in {"sigil-scope-ledger-v1", "sigil-scope-ledger-v2"}:
            raise ValueError("unsupported scope ledger version")
        config = dict(payload.get("config") or {})
        ledger = cls(
            threshold=float(config.get("threshold", 0.8)),
            n_min=int(config.get("n_min", 30)),
            confidence=float(config.get("confidence", 0.95)),
            bootstrap_samples=int(config.get("bootstrap_samples", 2000)),
            seed=int(config.get("seed", 0)),
        )
        for raw in payload.get("entries", ()):
            value = dict(raw)
            old_outcomes = value.pop("outcomes", ())
            audit_data = value.pop("audit_data", ())
            entry = ScopeLedgerEntry(**value)
            key = (entry.signal_type, entry.domain_fingerprint)
            if entry.status == "active" and (
                version == "sigil-scope-ledger-v1"
                or entry.provenance.get("provenance_contract_passed") is not True
            ):
                raise ValueError(
                    "active scope ledger lacks the v2 provenance contract"
                )
            if audit_data:
                rows = [
                    ScopeAuditDatum(**dict(item)) for item in audit_data
                ]
            else:
                outcomes = [bool(item) for item in old_outcomes]
                if outcomes and (
                    len(outcomes) != entry.fires
                    or sum(outcomes) != entry.valid
                ):
                    raise ValueError(f"scope ledger outcome mismatch: {key}")
                mean_gain = float(entry.mean_incremental_gain or 0.0)
                rows = [
                    ScopeAuditDatum(
                        valid=valid,
                        incremental_gain=mean_gain,
                        family_id=f"legacy:{index}",
                    )
                    for index, valid in enumerate(
                        outcomes
                        or [True] * entry.valid
                        + [False] * (entry.fires - entry.valid)
                    )
                ]
            if (
                len(rows) != entry.fires
                or sum(row.valid for row in rows) != entry.valid
            ):
                raise ValueError(f"scope ledger audit-data mismatch: {key}")
            ledger._entries[key] = entry
            ledger._data[key] = rows
        return ledger


def _family_blocked_interval(
    rows: Sequence[ScopeAuditDatum],
    *,
    confidence: float,
    samples: int,
    seed: int,
) -> tuple[float, float]:
    by_family: dict[str, list[float]] = {}
    for row in rows:
        by_family.setdefault(row.family_id, []).append(
            1.0 if row.valid else 0.0
        )
    family_means = tuple(
        fmean(by_family[family_id]) for family_id in sorted(by_family)
    )
    rng = random.Random(seed)
    draws = sorted(
        fmean(
            family_means[rng.randrange(len(family_means))]
            for _ in family_means
        )
        for _ in range(samples)
    )
    alpha = 1.0 - confidence
    lower_index = max(
        0,
        min(len(draws) - 1, int(alpha * len(draws))),
    )
    upper_index = max(
        0,
        min(len(draws) - 1, int(confidence * len(draws)) - 1),
    )
    return draws[lower_index], draws[upper_index]


def _finite(value: float) -> bool:
    return value == value and value not in {float("inf"), float("-inf")}
