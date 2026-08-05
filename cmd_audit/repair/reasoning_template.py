"""Inspectable reasoning templates bound to executable repair revisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Mapping


@dataclass(frozen=True)
class StructuredReasoningTemplate:
    """ECS-style plan metadata, never free-form answer-time guidance."""

    preconditions: tuple[str, ...] = ()
    evidence_slots: tuple[str, ...] = ()
    ordered_actions: tuple[str, ...] = ()
    expected_effect: str = ""
    verification_steps: tuple[str, ...] = ()
    abstention_conditions: tuple[str, ...] = ()
    version: str = "sigil-reasoning-template-v1"

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("template version is required")
        if any(not value.strip() for value in self.ordered_actions):
            raise ValueError("ordered actions must not be empty")

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.to_dict(),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, object],
    ) -> "StructuredReasoningTemplate":
        return cls(
            preconditions=tuple(
                str(item) for item in value.get("preconditions", ())
            ),
            evidence_slots=tuple(
                str(item) for item in value.get("evidence_slots", ())
            ),
            ordered_actions=tuple(
                str(item) for item in value.get("ordered_actions", ())
            ),
            expected_effect=str(value.get("expected_effect") or ""),
            verification_steps=tuple(
                str(item) for item in value.get("verification_steps", ())
            ),
            abstention_conditions=tuple(
                str(item)
                for item in value.get("abstention_conditions", ())
            ),
            version=str(
                value.get("version") or "sigil-reasoning-template-v1"
            ),
        )
