"""Atomic, content-addressed single-read ledger for successor-v3 confirmation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable


QUERY_LEDGER_SCHEMA_DESCRIPTOR = {
    "schema_version": "route-a-successor-v3-query-ledger-v1",
    "table": "successor_v3_query_reads",
    "columns": [
        ["protocol_manifest_sha256", "TEXT", "PRIMARY KEY NOT NULL"],
        ["input_sha256", "TEXT", "NOT NULL"],
        ["family_block_sha256", "TEXT", "NOT NULL"],
        ["winner_sha256", "TEXT", "NOT NULL"],
        ["state", "TEXT", "NOT NULL CHECK(state IN ('CLAIMED','SUCCESS','FAILED'))"],
        ["claimed_at", "TEXT", "NOT NULL"],
        ["finished_at", "TEXT", "NULL"],
        ["artifact_sha256", "TEXT", "NULL"],
    ],
    "transition": "INSERT_CLAIMED_ONCE_THEN_EXACTLY_ONE_SUCCESS_OR_FAILED",
}


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


QUERY_LEDGER_GENESIS_SHA256 = _canonical_sha256(QUERY_LEDGER_SCHEMA_DESCRIPTOR)
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256")
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class QueryLedgerRow:
    protocol_manifest_sha256: str
    input_sha256: str
    family_block_sha256: str
    winner_sha256: str
    state: str
    claimed_at: str
    finished_at: str | None
    artifact_sha256: str | None

    def __post_init__(self) -> None:
        for field in (
            "protocol_manifest_sha256",
            "input_sha256",
            "family_block_sha256",
            "winner_sha256",
        ):
            _require_sha256(getattr(self, field), field)
        if self.state not in {"CLAIMED", "SUCCESS", "FAILED"}:
            raise ValueError("invalid query-ledger state")
        if not self.claimed_at:
            raise ValueError("claimed_at is required")
        if self.state == "CLAIMED":
            if self.finished_at is not None or self.artifact_sha256 is not None:
                raise ValueError("CLAIMED row cannot contain terminal fields")
        else:
            if not self.finished_at:
                raise ValueError("terminal row requires finished_at")
            _require_sha256(self.artifact_sha256, "artifact_sha256")

    def as_mapping(self) -> dict[str, object]:
        return {
            "protocol_manifest_sha256": self.protocol_manifest_sha256,
            "input_sha256": self.input_sha256,
            "family_block_sha256": self.family_block_sha256,
            "winner_sha256": self.winner_sha256,
            "state": self.state,
            "claimed_at": self.claimed_at,
            "finished_at": self.finished_at,
            "artifact_sha256": self.artifact_sha256,
        }

    @property
    def row_sha256(self) -> str:
        return _canonical_sha256(self.as_mapping())


@dataclass(frozen=True)
class QueryClaim:
    protocol_manifest_sha256: str
    input_sha256: str
    family_block_sha256: str
    winner_sha256: str
    claimed: bool
    claim_row_sha256: str | None = None


class QueryReadLedger:
    """Reserve the sealed read before opening it and permit one terminal write."""

    def __init__(self, path: Path, *, now_rfc3339: Callable[[], str] = _now) -> None:
        self.path = path
        self._now_rfc3339 = now_rfc3339

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS successor_v3_query_reads ("
            "protocol_manifest_sha256 TEXT PRIMARY KEY NOT NULL, "
            "input_sha256 TEXT NOT NULL, family_block_sha256 TEXT NOT NULL, "
            "winner_sha256 TEXT NOT NULL, "
            "state TEXT NOT NULL CHECK(state IN ('CLAIMED','SUCCESS','FAILED')), "
            "claimed_at TEXT NOT NULL, finished_at TEXT, artifact_sha256 TEXT)"
        )
        return connection

    @staticmethod
    def _validate_identity(
        *,
        protocol_manifest_sha256: str,
        input_sha256: str,
        family_block_sha256: str,
        winner_sha256: str,
    ) -> None:
        for field, value in (
            ("protocol_manifest_sha256", protocol_manifest_sha256),
            ("input_sha256", input_sha256),
            ("family_block_sha256", family_block_sha256),
            ("winner_sha256", winner_sha256),
        ):
            _require_sha256(value, field)

    def claim(
        self,
        *,
        protocol_manifest_sha256: str,
        input_sha256: str,
        family_block_sha256: str,
        winner_sha256: str,
    ) -> QueryClaim:
        self._validate_identity(
            protocol_manifest_sha256=protocol_manifest_sha256,
            input_sha256=input_sha256,
            family_block_sha256=family_block_sha256,
            winner_sha256=winner_sha256,
        )
        claimed_at = self._now_rfc3339()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO successor_v3_query_reads "
                    "(protocol_manifest_sha256, input_sha256, family_block_sha256, winner_sha256, state, claimed_at) "
                    "VALUES (?, ?, ?, ?, 'CLAIMED', ?)",
                    (
                        protocol_manifest_sha256,
                        input_sha256,
                        family_block_sha256,
                        winner_sha256,
                        claimed_at,
                    ),
                )
            except sqlite3.IntegrityError:
                connection.execute("ROLLBACK")
                return QueryClaim(
                    protocol_manifest_sha256,
                    input_sha256,
                    family_block_sha256,
                    winner_sha256,
                    False,
                )
            connection.execute("COMMIT")
        row = QueryLedgerRow(
            protocol_manifest_sha256,
            input_sha256,
            family_block_sha256,
            winner_sha256,
            "CLAIMED",
            claimed_at,
            None,
            None,
        )
        return QueryClaim(
            protocol_manifest_sha256,
            input_sha256,
            family_block_sha256,
            winner_sha256,
            True,
            row.row_sha256,
        )

    def finish(self, claim: QueryClaim, *, success: bool, artifact_sha256: str) -> bool:
        if not claim.claimed:
            return False
        _require_sha256(artifact_sha256, "artifact_sha256")
        state = "SUCCESS" if success else "FAILED"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE successor_v3_query_reads SET state=?, finished_at=?, artifact_sha256=? "
                "WHERE protocol_manifest_sha256=? AND input_sha256=? AND family_block_sha256=? "
                "AND winner_sha256=? AND state='CLAIMED'",
                (
                    state,
                    self._now_rfc3339(),
                    artifact_sha256,
                    claim.protocol_manifest_sha256,
                    claim.input_sha256,
                    claim.family_block_sha256,
                    claim.winner_sha256,
                ),
            )
            connection.execute("COMMIT")
        return cursor.rowcount == 1

    def row(self, protocol_manifest_sha256: str) -> QueryLedgerRow | None:
        _require_sha256(protocol_manifest_sha256, "protocol_manifest_sha256")
        with self._connect() as connection:
            record = connection.execute(
                "SELECT protocol_manifest_sha256,input_sha256,family_block_sha256,"
                "winner_sha256,state,claimed_at,finished_at,artifact_sha256 "
                "FROM successor_v3_query_reads WHERE protocol_manifest_sha256=?",
                (protocol_manifest_sha256,),
            ).fetchone()
        return None if record is None else QueryLedgerRow(*record)
