"""Runtime-field provenance policy for leak-safe structural signals."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Iterable, Literal, Mapping


ProvenanceRole = Literal[
    "runtime_payload",
    "injection_control",
    "post_outcome",
]
_VALID_ROLES = frozenset(
    {"runtime_payload", "injection_control", "post_outcome"}
)
FORBIDDEN_RUNTIME_FIELDS = frozenset(
    {
        "perturbation_label",
        "perturbation_type",
        "gold_answer",
        "gold_evidence",
        "recurrence_family_id",
        "family_id",
        "safety_filter_blocked",
        "passed_safety_filter",
    }
)
_FORBIDDEN_PREFIXES = ("gold_", "oracle_", "shadow_gold_", "perturbation_")


def is_forbidden_runtime_field(field_name: str) -> bool:
    """Return whether a field is unavailable to runtime construction/selection."""

    normalized = str(field_name).strip().casefold()
    return normalized in FORBIDDEN_RUNTIME_FIELDS or normalized.startswith(
        _FORBIDDEN_PREFIXES
    )


def assert_no_forbidden_runtime_fields(field_names: Iterable[str]) -> None:
    """Fail closed when a runtime API declares a forbidden field."""

    forbidden = sorted(
        {str(name) for name in field_names if is_forbidden_runtime_field(name)}
    )
    if forbidden:
        raise ValueError(
            "forbidden runtime fields: " + ", ".join(forbidden)
        )


@dataclass(frozen=True)
class RuntimeFieldRecord:
    field_name: str
    origin_component: str
    provenance_role: ProvenanceRole
    available_in_deployment: bool
    extractor_version: str

    def __post_init__(self) -> None:
        if not self.field_name or not self.origin_component:
            raise ValueError("field_name and origin_component are required")
        if self.provenance_role not in _VALID_ROLES:
            raise ValueError(
                f"invalid provenance role: {self.provenance_role}"
            )
        if not self.extractor_version:
            raise ValueError("extractor_version is required")

    @property
    def runtime_eligible(self) -> bool:
        return (
            self.provenance_role == "runtime_payload"
            and self.available_in_deployment
            and not is_forbidden_runtime_field(self.field_name)
        )


@dataclass(frozen=True)
class RuntimeFieldPolicy:
    """Immutable allowlist with content-addressed provenance."""

    records: tuple[RuntimeFieldRecord, ...]
    version: str = "sigil-runtime-fields-v1"

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("policy version is required")
        names = [record.field_name for record in self.records]
        if len(set(names)) != len(names):
            raise ValueError("runtime field policy contains duplicate fields")
        assert_no_forbidden_runtime_fields(names)
        ineligible = sorted(
            record.field_name
            for record in self.records
            if not record.runtime_eligible
        )
        if ineligible:
            raise ValueError(
                "runtime-ineligible fields: " + ", ".join(ineligible)
            )

    @property
    def allowlist(self) -> tuple[str, ...]:
        return tuple(sorted(record.field_name for record in self.records))

    @property
    def sha256(self) -> str:
        return _canonical_sha256(self.to_dict())

    def permits(self, field_name: str) -> bool:
        return str(field_name) in self.allowlist

    def validate_declared_fields(self, field_names: Iterable[str]) -> None:
        declared = tuple(str(value) for value in field_names)
        assert_no_forbidden_runtime_fields(declared)
        unknown = sorted(set(declared) - set(self.allowlist))
        if unknown:
            raise ValueError(
                "undeclared runtime fields: " + ", ".join(unknown)
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "records": [
                asdict(record)
                for record in sorted(
                    self.records,
                    key=lambda value: value.field_name,
                )
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
    def from_mapping(cls, value: Mapping[str, object]) -> "RuntimeFieldPolicy":
        return cls(
            records=tuple(
                RuntimeFieldRecord(**dict(row))
                for row in value.get("records", ())
            ),
            version=str(value.get("version") or "sigil-runtime-fields-v1"),
        )


def structural_runtime_field_policy(
    *,
    extractor_version: str,
) -> RuntimeFieldPolicy:
    """Return the fixed query/recall structural-extractor policy."""

    return RuntimeFieldPolicy(
        records=tuple(
            RuntimeFieldRecord(
                field_name=name,
                origin_component="runtime_query_or_recall",
                provenance_role="runtime_payload",
                available_in_deployment=True,
                extractor_version=extractor_version,
            )
            for name in ("query", "memory_id", "text", "store")
        )
    )


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
