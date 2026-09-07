"""Versioned, persistent cache for text-only relation measurements.

The cache identity deliberately has no memory-item identity or retrieval
metadata: a relation measurement belongs to an unordered pair of texts under a
particular instrument version.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .slot_relation import RelationVerdict


_CONSTRUCTION_PREFIX = re.compile(r"^\s*M_(?:old|new)\s*:\s*", re.IGNORECASE)
_SPACE = re.compile(r"\s+")
NORMALIZATION_VERSION = "relation-text-normalization-v1"


def canonical_text(text: str) -> str:
    """Normalize a text for content addressing, removing known build markers."""
    if not isinstance(text, str):
        raise TypeError("relation text must be a string")
    return _SPACE.sub(" ", _CONSTRUCTION_PREFIX.sub("", text).strip())


@dataclass(frozen=True)
class RelationCacheKey:
    cache_key: str
    canonical_left: str
    canonical_right: str
    prompt_sha256: str
    parser_version: str
    model_id: str
    model_config_hash: str
    normalization_version: str
    instrument_version: str

    @classmethod
    def build(
        cls,
        left_text: str,
        right_text: str,
        *,
        prompt_sha256: str,
        parser_version: str,
        model_id: str,
        model_config_hash: str = "unspecified",
        normalization_version: str = NORMALIZATION_VERSION,
        instrument_version: str = "unspecified",
    ) -> "RelationCacheKey":
        left, right = sorted((canonical_text(left_text), canonical_text(right_text)))
        payload = {
            "canonical_left": left,
            "canonical_right": right,
            "prompt_sha256": prompt_sha256,
            "parser_version": parser_version,
            "model_id": model_id,
            "model_config_hash": model_config_hash,
            "normalization_version": normalization_version,
            "instrument_version": instrument_version,
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return cls(
            cache_key=hashlib.sha256(encoded.encode("utf-8")).hexdigest(), **payload
        )


class RelationCache:
    """A SQLite cache with auditable key material and atomic inserts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS relation_verdicts (
                cache_key TEXT PRIMARY KEY,
                canonical_left TEXT NOT NULL,
                canonical_right TEXT NOT NULL,
                prompt_sha256 TEXT NOT NULL,
                parser_version TEXT NOT NULL,
                model_id TEXT NOT NULL,
                model_config_hash TEXT NOT NULL,
                normalization_version TEXT NOT NULL,
                instrument_version TEXT NOT NULL,
                verdict_json TEXT NOT NULL
            )
            """
        )
        existing_columns = {
            row[1]
            for row in self._connection.execute("PRAGMA table_info(relation_verdicts)")
        }
        for name in (
            "model_config_hash",
            "normalization_version",
            "instrument_version",
        ):
            if name not in existing_columns:
                self._connection.execute(
                    f"ALTER TABLE relation_verdicts ADD COLUMN {name} TEXT NOT NULL DEFAULT 'legacy'"
                )
        self._connection.commit()

    def get(self, key: RelationCacheKey) -> "RelationVerdict | None":
        with self._lock:
            row = self._connection.execute(
                "SELECT verdict_json FROM relation_verdicts WHERE cache_key = ?",
                (key.cache_key,),
            ).fetchone()
        if row is None:
            return None
        from .slot_relation import (
            RelationAttempt,
            RelationReason,
            RelationType,
            RelationVerdict,
        )

        payload = json.loads(row[0])
        attempts = tuple(
            RelationAttempt(
                attempt_index=attempt["attempt_index"],
                reason_code=RelationReason(attempt["reason_code"]),
                raw_response=attempt["raw_response"],
                raw_response_sha256=attempt["raw_response_sha256"],
                structured_output_used=attempt["structured_output_used"],
            )
            for attempt in payload.get("attempts", ())
        )
        return RelationVerdict(
            relation=RelationType(payload["relation"]),
            **{
                key: payload[key]
                for key in (
                    "slot",
                    "abstained",
                    "prompt_sha256",
                    "parser_version",
                    "model_id",
                )
            },
            reason_code=RelationReason(
                payload.get(
                    "reason_code",
                    "accepted" if not payload["abstained"] else "invalid_schema",
                )
            ),
            raw_response_sha256=payload.get("raw_response_sha256"),
            attempts=attempts,
        )

    def put(self, key: RelationCacheKey, verdict: "RelationVerdict") -> None:
        payload = asdict(verdict)
        payload["relation"] = verdict.relation.value
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO relation_verdicts
                (cache_key, canonical_left, canonical_right, prompt_sha256,
                 parser_version, model_id, model_config_hash,
                 normalization_version, instrument_version, verdict_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key.cache_key,
                    key.canonical_left,
                    key.canonical_right,
                    key.prompt_sha256,
                    key.parser_version,
                    key.model_id,
                    key.model_config_hash,
                    key.normalization_version,
                    key.instrument_version,
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                ),
            )

    def resolve(
        self,
        key: RelationCacheKey,
        measure: Callable[[], "RelationVerdict"],
        *,
        cache_if: Callable[["RelationVerdict"], bool] | None = None,
    ) -> "RelationVerdict":
        """Return a cached measurement or execute it exactly once per process."""
        with self._lock:
            cached = self.get(key)
            if cached is not None:
                return cached
            verdict = measure()
            if cache_if is None or cache_if(verdict):
                self.put(key, verdict)
            return verdict

    def audit_rows(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
            SELECT cache_key, canonical_left, canonical_right, prompt_sha256,
                   parser_version, model_id, model_config_hash,
                   normalization_version, instrument_version, verdict_json
                FROM relation_verdicts ORDER BY cache_key
                """
            ).fetchall()
        return [
            {
                "cache_key": row[0],
                "canonical_left": row[1],
                "canonical_right": row[2],
                "prompt_sha256": row[3],
                "parser_version": row[4],
                "model_id": row[5],
                "model_config_hash": row[6],
                "normalization_version": row[7],
                "instrument_version": row[8],
                "verdict": json.loads(row[9]),
            }
            for row in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "RelationCache":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
