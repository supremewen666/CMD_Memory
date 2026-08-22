"""Append-only SQLite sediment for neuro-symbolic memory evolution.

This is deliberately a *data* seam: callers pass closed JSON-like mappings,
not policy classes.  That keeps the durable ledger replayable across policy
implementations and prevents a persistence layer from becoming an authority
for store mutations.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from cmd_audit.core.state_codec import canonical_json as _state_canonical_json
from cmd_audit.core.state_codec import content_sha256 as _state_content_sha256


JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


def canonical_json(value: object) -> str:
    """Serialize a closed, finite JSON value in its content-addressed form."""
    return _state_canonical_json(_closed_json(value), ensure_ascii=True, allow_nan=False)


def content_sha256(value: object) -> str:
    # Retain validation-before-hashing compatibility for repository callers.
    return _state_content_sha256(_closed_json(value), ensure_ascii=True, allow_nan=False)


class EvolutionRepository:
    """Transactional immutable event repository with deterministic replay.

    ``append_*`` returns the event identity. Reappending byte-equivalent
    logical content is a no-op; attempting to reuse an identity with different
    content raises ``ValueError`` before any row is changed.
    """

    _ID_FIELDS = {
        "selection": "selection_id",
        "outcome": "observation_id",
        "policy_snapshot": "snapshot_sha256",
        "species": "species_id",
        "niche_membership": "membership_id",
        "lifecycle": "event_id",
        "chain_attempt": "attempt_id",
        "chain_decision": "decision_id",
    }

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._connection = sqlite3.connect(
            self.path, timeout=10, isolation_level=None, check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA busy_timeout=10000")
        if self.path != ":memory:":
            self._connection.execute("PRAGMA journal_mode=WAL")
        self._initialize()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "EvolutionRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def append_selection(self, payload: Mapping[str, object]) -> str:
        normalized = self._prepare("selection", payload)
        event_index = _positive_int(normalized, "event_index")
        case_id = _required_string(normalized, "case_id")
        # A selection index is chronologically unique; different cases may not
        # silently claim the same position after restart.
        row = self._connection.execute(
            "SELECT event_id, payload_sha256 FROM events "
            "WHERE event_type = 'selection' AND event_index = ?", (event_index,),
        ).fetchone()
        if row is not None and row["event_id"] != normalized["selection_id"]:
            raise ValueError("selection event_index already belongs to another selection")
        self._append("selection", normalized)
        _ = case_id
        return str(normalized["selection_id"])

    def append_outcome(self, payload: Mapping[str, object]) -> str:
        normalized = self._prepare("outcome", payload)
        selection_id = _required_string(normalized, "selection_id")
        selection = self._event_payload("selection", selection_id)
        if selection is None:
            raise ValueError("outcome references an unknown selection")
        if normalized.get("case_id") != selection.get("case_id"):
            raise ValueError("outcome case_id must match its selection")
        observed = _positive_int(normalized, "observed_after_event_index")
        if observed <= int(selection["event_index"]):
            raise ValueError("outcome must be observed after its selection")
        self._append("outcome", normalized)
        return str(normalized["observation_id"])

    def append_policy_snapshot(self, payload: Mapping[str, object]) -> str:
        normalized = self._prepare("policy_snapshot", payload)
        effective = normalized.get("effective_after_event_index")
        if (
            not isinstance(effective, int)
            or isinstance(effective, bool)
            or effective < -1
        ):
            raise ValueError("effective_after_event_index must be >= -1")
        if (
            "parent_snapshot_hash" in normalized
            and "parent_snapshot_sha256" in normalized
        ):
            raise ValueError("policy snapshot has ambiguous parent fields")
        parent = normalized.get(
            "parent_snapshot_sha256", normalized.get("parent_snapshot_hash")
        )
        if parent is not None and self._event_payload("policy_snapshot", str(parent)) is None:
            raise ValueError("policy snapshot parent is unknown")
        if effective == -1 and parent is not None:
            raise ValueError("initial policy snapshot cannot have a parent")
        self._append("policy_snapshot", normalized)
        return str(normalized["snapshot_sha256"])

    def append_species(self, payload: Mapping[str, object]) -> str:
        normalized = self._prepare("species", payload)
        self._append("species", normalized)
        return str(normalized["species_id"])

    def append_niche_membership(self, payload: Mapping[str, object]) -> str:
        normalized = self._prepare("niche_membership", payload)
        _required_string(normalized, "species_id")
        _required_string(normalized, "niche_path")
        self._append("niche_membership", normalized)
        return str(normalized["membership_id"])

    def append_lifecycle_event(self, payload: Mapping[str, object]) -> str:
        normalized = self._prepare("lifecycle", payload)
        _required_string(normalized, "subject_id")
        _required_string(normalized, "to_state")
        self._append("lifecycle", normalized)
        return str(normalized["event_id"])

    def append_chain_attempt(self, payload: Mapping[str, object]) -> str:
        normalized = self._prepare("chain_attempt", payload)
        first = _required_string(normalized, "first_strategy_id")
        second = _required_string(normalized, "second_strategy_id")
        if first == second:
            raise ValueError("repair chain components must differ")
        self._append("chain_attempt", normalized)
        return str(normalized["attempt_id"])

    def append_chain_decision(self, payload: Mapping[str, object]) -> str:
        normalized = self._prepare("chain_decision", payload)
        _required_string(normalized, "chain_id")
        _required_string(normalized, "to_state")
        self._append("chain_decision", normalized)
        return str(normalized["decision_id"])

    # Deliberately explicit aliases: callers may use record terminology while
    # all paths retain the same immutable append semantics.
    record_selection = append_selection
    record_outcome = append_outcome
    record_policy_snapshot = append_policy_snapshot
    record_species = append_species
    record_niche_membership = append_niche_membership
    record_lifecycle_event = append_lifecycle_event
    record_chain_attempt = append_chain_attempt
    record_chain_decision = append_chain_decision

    def rows(self, event_type: str | None = None) -> tuple[dict[str, JsonValue], ...]:
        """Return logical rows in stable event-type/identity order."""
        if event_type is None:
            statement = "SELECT event_type, event_id, payload_json, payload_sha256 FROM events ORDER BY event_type, event_id"
            params: tuple[object, ...] = ()
        else:
            statement = "SELECT event_type, event_id, payload_json, payload_sha256 FROM events WHERE event_type = ? ORDER BY event_id"
            params = (event_type,)
        return tuple(_row_to_public(row) for row in self._connection.execute(statement, params))

    def active_species(self) -> tuple[dict[str, JsonValue], ...]:
        """Materialize species whose latest lifecycle event is not retired."""
        species = {str(row["species_id"]): row for row in self._payloads("species")}
        lifecycle: dict[str, dict[str, JsonValue]] = {}
        for row in self._payloads("lifecycle"):
            subject = str(row.get("subject_id", ""))
            previous = lifecycle.get(subject)
            if previous is None or _event_order(row) > _event_order(previous):
                lifecycle[subject] = row
        active = [
            row for species_id, row in species.items()
            if lifecycle.get(species_id, {}).get("to_state") != "retired"
        ]
        return tuple(sorted(active, key=canonical_json))

    def active_niche_memberships(self) -> tuple[dict[str, JsonValue], ...]:
        """Return newest membership per (species, niche), excluding inactive."""
        latest: dict[tuple[str, str], dict[str, JsonValue]] = {}
        for row in self._payloads("niche_membership"):
            key = (str(row["species_id"]), str(row["niche_path"]))
            old = latest.get(key)
            if old is None or _event_order(row) > _event_order(old):
                latest[key] = row
        active_ids = {str(row["species_id"]) for row in self.active_species()}
        return tuple(sorted(
            (row for row in latest.values() if str(row["species_id"]) in active_ids),
            key=canonical_json,
        ))

    def active_chains(self) -> tuple[dict[str, JsonValue], ...]:
        """Materialize each chain's newest non-closed governance state."""
        latest: dict[str, dict[str, JsonValue]] = {}
        for row in self._payloads("chain_decision"):
            chain_id = str(row.get("chain_id", ""))
            previous = latest.get(chain_id)
            if previous is None or _event_order(row) > _event_order(previous):
                latest[chain_id] = row
        return tuple(
            sorted(
                (
                    row
                    for row in latest.values()
                    if row.get("to_state") not in {"blocked", "retired"}
                ),
                key=canonical_json,
            )
        )

    def repository_hash(self) -> str:
        """Hash logical records, never mutable SQLite pages or insertion order."""
        # Byte-identical to ``content_sha256(self.rows())`` without retaining
        # the full append-only history as nested Python objects.
        digest = hashlib.sha256()
        digest.update(b"[")
        statement = (
            "SELECT event_type, event_id, payload_json, payload_sha256 "
            "FROM events ORDER BY event_type, event_id"
        )
        first = True
        for raw in self._connection.execute(statement):
            if not first:
                digest.update(b",")
            first = False
            digest.update(canonical_json(_row_to_public(raw)).encode("utf-8"))
        digest.update(b"]")
        return digest.hexdigest()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        else:
            self._connection.execute("COMMIT")

    def _initialize(self) -> None:
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS events (
                event_type TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_index INTEGER,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                PRIMARY KEY (event_type, event_id),
                UNIQUE (event_type, payload_sha256)
            )"""
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS events_selection_index "
            "ON events(event_type, event_index)"
        )

    def _prepare(self, event_type: str, payload: Mapping[str, object]) -> dict[str, JsonValue]:
        if not isinstance(payload, Mapping):
            raise TypeError("repository payload must be a mapping")
        normalized = _closed_json(payload)
        assert isinstance(normalized, dict)
        id_field = self._ID_FIELDS[event_type]
        content = {key: value for key, value in normalized.items() if key != id_field}
        expected = content_sha256(content)
        supplied = normalized.get(id_field)
        if supplied is not None and supplied != expected:
            raise ValueError(f"{id_field} does not bind its canonical payload")
        normalized[id_field] = expected
        return normalized

    def _append(self, event_type: str, payload: dict[str, JsonValue]) -> None:
        event_id = str(payload[self._ID_FIELDS[event_type]])
        payload_json = canonical_json(payload)
        payload_sha = content_sha256(payload)
        event_index = payload.get("event_index") if event_type == "selection" else None
        with self.transaction():
            existing = self._connection.execute(
                "SELECT payload_sha256 FROM events WHERE event_type = ? AND event_id = ?",
                (event_type, event_id),
            ).fetchone()
            if existing is not None:
                if existing["payload_sha256"] != payload_sha:
                    raise ValueError("immutable event id replay has conflicting payload")
                return
            try:
                self._connection.execute(
                    "INSERT INTO events(event_type, event_id, event_index, payload_json, payload_sha256) VALUES (?, ?, ?, ?, ?)",
                    (event_type, event_id, event_index, payload_json, payload_sha),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("duplicate event payload or identity conflict") from error

    def _event_payload(self, event_type: str, event_id: str) -> dict[str, JsonValue] | None:
        row = self._connection.execute(
            "SELECT payload_json FROM events WHERE event_type = ? AND event_id = ?",
            (event_type, event_id),
        ).fetchone()
        return None if row is None else json.loads(str(row["payload_json"]))

    def _payloads(self, event_type: str) -> tuple[dict[str, JsonValue], ...]:
        return tuple(json.loads(str(row["payload_json"])) for row in self._connection.execute(
            "SELECT payload_json FROM events WHERE event_type = ? ORDER BY event_id", (event_type,),
        ))


def _closed_json(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("repository payload contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        converted: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("repository mapping keys must be strings")
            converted[key] = _closed_json(item)
        return converted
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [_closed_json(item) for item in value]
    raise TypeError(f"repository payload is not closed JSON: {type(value).__name__}")


def _required_string(payload: Mapping[str, JsonValue], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _positive_int(payload: Mapping[str, JsonValue], field: str) -> int:
    value = _nonnegative_int(payload, field)
    if value <= 0:
        raise ValueError(f"{field} must be positive")
    return value


def _nonnegative_int(payload: Mapping[str, JsonValue], field: str) -> int:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _event_order(payload: Mapping[str, JsonValue]) -> tuple[int, str]:
    value = payload.get("event_index", payload.get("observed_after_event_index", -1))
    return (int(value) if isinstance(value, int) else -1, canonical_json(payload))


def _row_to_public(row: sqlite3.Row) -> dict[str, JsonValue]:
    return {
        "event_type": str(row["event_type"]),
        "event_id": str(row["event_id"]),
        "payload": json.loads(str(row["payload_json"])),
        "payload_sha256": str(row["payload_sha256"]),
    }
