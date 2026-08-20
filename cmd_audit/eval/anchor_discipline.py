"""Anchor discipline for sealed confirmation — task.md 3.2.

Double Ratchet (2607.12790) makes a small anchored reference set the only
supervised signal any evolution loop reads, and audits the result against a
held-out anchor split the loop never reads.  Their ablation is the reason this
module exists: with the anchor guards removed the evolved metric collapsed into
a vacuous detector *while task scores went up*, so a run cannot be trusted on
its own reported numbers.  The guard therefore has to be structural.

Two objects:

* :class:`AnchorSet` — the reference split a protocol is allowed to read, plus
  a held-out split it is not.  Reading the held-out anchors raises; the only
  legal consumer is :meth:`AnchorSet.audit`, which is called once after the
  confirmation set has been burned.
* :class:`SealedProtocol` — the pre-registered record (data, arms, metrics,
  thresholds, seeds) hashed before any new experiment runs, so "the protocol
  existed first" is checkable rather than asserted.

Nothing here reads ``recovery_gain`` or any ``gold_*`` field: anchors carry
their own reference labels, supplied by whoever registered the protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Mapping, Sequence


ANCHOR_SCHEMA_VERSION = "cmd-anchor-discipline-v1"
PROTOCOL_SCHEMA_VERSION = "cmd-sealed-protocol-v1"

# Double Ratchet anchors its metric loop on ten items.  Matching that size is
# not cargo-culting: it is the comparability condition for the E5 table.
DEFAULT_REFERENCE_SIZE = 10


class HeldOutAnchorReadError(PermissionError):
    """Raised when anything but the post-hoc audit touches a held-out anchor."""


class SealedProtocolViolation(PermissionError):
    """Raised when a run contradicts the pre-registered protocol.

    task.md 2.2 carries a "violation voids the run" clause.  Raising rather
    than returning a flag is what makes voiding non-optional.
    """


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{name} must be a lowercase sha256 hex digest")
    return value


@dataclass(frozen=True)
class Anchor:
    """One reference item: an id, the input, and its reference judgement."""

    anchor_id: str
    payload: str
    reference: float

    def __post_init__(self) -> None:
        if not isinstance(self.anchor_id, str) or not self.anchor_id:
            raise ValueError("anchor requires anchor_id")
        if not isinstance(self.payload, str) or not self.payload:
            raise ValueError("anchor payload must be a non-empty string")
        if isinstance(self.reference, bool) or not isinstance(
            self.reference, (int, float)
        ):
            raise ValueError("anchor reference must be numeric")
        if not 0.0 <= float(self.reference) <= 1.0:
            raise ValueError("anchor reference must lie in [0, 1]")
        object.__setattr__(self, "reference", float(self.reference))

    def to_mapping(self) -> dict[str, object]:
        return {
            "anchor_id": self.anchor_id,
            "payload": self.payload,
            "reference": self.reference,
        }


class AnchorSet:
    """A reference split that may be read and a held-out split that may not.

    The held-out anchors are stored behind ``__``-prefixed state and every
    ordinary accessor refuses them.  :meth:`audit` is the single exit, and it
    records that it fired so a report can state whether the audit ever ran.
    """

    def __init__(
        self,
        *,
        reference: Sequence[Anchor],
        held_out: Sequence[Anchor],
        set_id: str,
    ) -> None:
        if not set_id:
            raise ValueError("anchor set requires set_id")
        reference = tuple(reference)
        held_out = tuple(held_out)
        if not reference or not held_out:
            raise ValueError("anchor set requires both reference and held-out anchors")
        if any(not isinstance(row, Anchor) for row in (*reference, *held_out)):
            raise TypeError("anchor set entries must be Anchor instances")
        ids = [row.anchor_id for row in (*reference, *held_out)]
        if len(set(ids)) != len(ids):
            raise ValueError("anchor ids must be unique across both splits")
        self.set_id = set_id
        self._reference = reference
        self.__held_out = held_out
        self._audited = False

    @property
    def reference(self) -> tuple[Anchor, ...]:
        """The anchors a protocol is allowed to fit against."""
        return self._reference

    @property
    def reference_size(self) -> int:
        return len(self._reference)

    @property
    def held_out_size(self) -> int:
        """Count only — the anchors themselves stay unreadable."""
        return len(self.__held_out)

    @property
    def held_out(self) -> tuple[Anchor, ...]:
        raise HeldOutAnchorReadError(
            "held-out anchors are never read by a protocol; use audit()"
        )

    @property
    def audited(self) -> bool:
        return self._audited

    def __getitem__(self, key: str) -> Anchor:
        for row in self._reference:
            if row.anchor_id == key:
                return row
        if any(row.anchor_id == key for row in self.__held_out):
            raise HeldOutAnchorReadError(
                f"anchor {key} belongs to the held-out split and may not be read"
            )
        raise KeyError(key)

    def fingerprint(self) -> str:
        """Bind both splits without exposing held-out content.

        The held-out anchors enter as a hash, so a protocol registered before
        the run provably names the same held-out split it is later audited on,
        while the payloads stay unreadable.
        """
        return _digest(
            {
                "schema_version": ANCHOR_SCHEMA_VERSION,
                "set_id": self.set_id,
                "reference": [row.to_mapping() for row in self._reference],
                "held_out_sha256": _digest(
                    [row.to_mapping() for row in self.__held_out]
                ),
            }
        )

    def audit(self, scorer) -> dict[str, object]:
        """Score the held-out anchors once, after the run is already decided.

        ``scorer`` maps an anchor payload to a number in [0, 1].  The mean
        absolute deviation from the references is the Double-Ratchet-style
        outer check: a metric that only looks good on the anchors it was fitted
        to shows up here.
        """
        if self._audited:
            raise HeldOutAnchorReadError("held-out anchor audit may only run once")
        self._audited = True
        deviations: list[float] = []
        rows: list[dict[str, object]] = []
        for anchor in self.__held_out:
            observed = float(scorer(anchor.payload))
            if not 0.0 <= observed <= 1.0:
                raise ValueError("anchor scorer must return a value in [0, 1]")
            deviation = abs(observed - anchor.reference)
            deviations.append(deviation)
            rows.append(
                {
                    "anchor_id": anchor.anchor_id,
                    "observed": observed,
                    "reference": anchor.reference,
                    "absolute_deviation": deviation,
                }
            )
        mean_absolute_deviation = sum(deviations) / len(deviations)
        return {
            "schema_version": ANCHOR_SCHEMA_VERSION,
            "set_id": self.set_id,
            "held_out_count": len(rows),
            "mean_absolute_deviation": mean_absolute_deviation,
            "max_absolute_deviation": max(deviations),
            "rows": rows,
        }

    def audit_scores(self, scores: Mapping[str, float]) -> dict[str, object]:
        """Audit a closed set of externally materialized scores by anchor id.

        This is the file-based experiment runner's legal exit.  The caller can
        bind scores to opaque anchor IDs without reading held-out payloads; the
        private split remains accessible only inside the audit object.
        """
        expected = {row.anchor_id for row in self.__held_out}
        if set(scores) != expected:
            raise ValueError("held-out score coverage mismatch")
        checked: dict[str, float] = {}
        for anchor_id, value in scores.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("held-out observed score must be numeric")
            number = float(value)
            if not 0.0 <= number <= 1.0:
                raise ValueError("held-out observed score must lie in [0, 1]")
            checked[anchor_id] = number
        by_payload = {
            row.payload: checked[row.anchor_id] for row in self.__held_out
        }
        return self.audit(lambda payload: by_payload[payload])


@dataclass(frozen=True)
class SealedProtocol:
    """A pre-registered experiment protocol — task.md 2.2.

    ``protocol_sha256`` is computed at construction, so a report can carry the
    hash of the protocol it claims to have followed and a reader can recompute
    it from the protocol text in the appendix.
    """

    protocol_id: str
    dataset_sha256: str
    arms: tuple[str, ...]
    primary_metric: str
    thresholds: Mapping[str, float]
    seeds: tuple[int, ...]
    anchor_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.protocol_id, str) or not self.protocol_id:
            raise ValueError("sealed protocol requires protocol_id")
        _sha256(self.dataset_sha256, "dataset_sha256")
        arms = tuple(self.arms)
        if len(arms) < 2 or len(set(arms)) != len(arms):
            raise ValueError("sealed protocol requires at least two distinct arms")
        if any(not isinstance(arm, str) or not arm for arm in arms):
            raise ValueError("sealed protocol arms must be non-empty strings")
        if not isinstance(self.primary_metric, str) or not self.primary_metric:
            raise ValueError("sealed protocol requires a primary metric")
        if not self.thresholds:
            raise ValueError("sealed protocol requires at least one threshold")
        for key, value in self.thresholds.items():
            if not isinstance(key, str) or not key:
                raise ValueError("threshold names must be non-empty strings")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"threshold {key} must be numeric")
        seeds = tuple(self.seeds)
        if not seeds or len(set(seeds)) != len(seeds) or any(
            isinstance(seed, bool) or not isinstance(seed, int) for seed in seeds
        ):
            raise ValueError("sealed protocol requires distinct seeds")
        _sha256(self.anchor_fingerprint, "anchor_fingerprint")
        object.__setattr__(self, "arms", arms)
        object.__setattr__(
            self, "thresholds", {k: float(v) for k, v in sorted(self.thresholds.items())}
        )
        object.__setattr__(self, "seeds", seeds)

    @property
    def protocol_sha256(self) -> str:
        return _digest(self.to_mapping())

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": PROTOCOL_SCHEMA_VERSION,
            "protocol_id": self.protocol_id,
            "dataset_sha256": self.dataset_sha256,
            "arms": list(self.arms),
            "primary_metric": self.primary_metric,
            "thresholds": dict(self.thresholds),
            "seeds": list(self.seeds),
            "anchor_fingerprint": self.anchor_fingerprint,
        }

    def verify_run(
        self,
        *,
        dataset_sha256: str,
        arms: Sequence[str],
        primary_metric: str,
        seeds: Sequence[int],
        anchor_set: AnchorSet,
    ) -> None:
        """Raise unless the run matches what was sealed.

        Called before results are written, so a mismatched run cannot produce a
        publishable artifact.
        """
        if dataset_sha256 != self.dataset_sha256:
            raise SealedProtocolViolation(
                "run dataset does not match the sealed protocol"
            )
        if tuple(arms) != self.arms:
            raise SealedProtocolViolation("run arms do not match the sealed protocol")
        if primary_metric != self.primary_metric:
            raise SealedProtocolViolation(
                "run primary metric does not match the sealed protocol"
            )
        if tuple(seeds) != self.seeds:
            raise SealedProtocolViolation("run seeds do not match the sealed protocol")
        if anchor_set.fingerprint() != self.anchor_fingerprint:
            raise SealedProtocolViolation(
                "run anchor set does not match the sealed protocol"
            )


__all__ = [
    "ANCHOR_SCHEMA_VERSION",
    "DEFAULT_REFERENCE_SIZE",
    "PROTOCOL_SCHEMA_VERSION",
    "Anchor",
    "AnchorSet",
    "HeldOutAnchorReadError",
    "SealedProtocol",
    "SealedProtocolViolation",
]
