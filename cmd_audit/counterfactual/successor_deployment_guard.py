"""Successor-v3 G5 deployment guard, with no production-store adapter.

The only persistence here is a SQLite authorization/use ledger.  Its unique
nonce reservation is committed *before* a test adapter can be touched, so two
guards in different processes cannot replay one signed authorization.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol

_SHA256 = re.compile(r"[0-9a-f]{64}")
_NONCE = re.compile(r"[0-9a-f]{32}")
_TOKEN_SCHEMA_VERSION = "successor-v3-g5-token-v1"
_TOKEN_ALGORITHM = "HMAC-SHA256"

__all__ = [
    "AuthorizationToken", "DeploymentDecision", "DeploymentEvaluator", "DeploymentRequest",
    "DeploymentUseLedger", "FrozenRollbackPolicy", "G5DeploymentGuard", "HMACAuthorizer",
    "LedgerEntry", "ProbeOutcome", "StoreSnapshot", "TransactionalStoreAdapter",
]


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _require_hash(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256")
    return value


def _mapping(value: object, *, keys: set[str], what: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"malformed {what} wire mapping")
    return value


@dataclass(frozen=True)
class DeploymentRequest:
    protocol_manifest_sha256: str
    program_sha256: str
    graph_sha256: str
    case_id: str
    runtime_case_sha256: str
    target_item_id: str
    rollback_policy_sha256: str

    def __post_init__(self) -> None:
        for field in ("protocol_manifest_sha256", "program_sha256", "graph_sha256", "runtime_case_sha256", "rollback_policy_sha256"):
            _require_hash(getattr(self, field), field)
        if not self.case_id or not self.target_item_id:
            raise ValueError("case_id and target_item_id are required")

    def as_mapping(self) -> dict[str, object]:
        return {field: getattr(self, field) for field in (
            "protocol_manifest_sha256", "program_sha256", "graph_sha256", "case_id",
            "runtime_case_sha256", "target_item_id", "rollback_policy_sha256",
        )}

    @classmethod
    def from_mapping(cls, value: object) -> "DeploymentRequest":
        data = _mapping(value, keys={
            "protocol_manifest_sha256", "program_sha256", "graph_sha256", "case_id",
            "runtime_case_sha256", "target_item_id", "rollback_policy_sha256",
        }, what="deployment request")
        return cls(**data)  # type: ignore[arg-type]


@dataclass(frozen=True)
class AuthorizationToken:
    request: DeploymentRequest
    issuer_id: str
    key_id: str
    issued_at: int
    expires_at: int
    nonce: str
    mac: str
    algorithm: str = _TOKEN_ALGORITHM
    schema_version: str = _TOKEN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != _TOKEN_SCHEMA_VERSION or self.algorithm != _TOKEN_ALGORITHM:
            raise ValueError("unregistered G5 token schema or algorithm")
        if (
            not self.issuer_id
            or not self.key_id
            or not _NONCE.fullmatch(self.nonce)
            or type(self.issued_at) is not int
            or type(self.expires_at) is not int
            or self.issued_at < 0
            or self.expires_at < self.issued_at
        ):
            raise ValueError("malformed G5 authorization token")
        if not re.fullmatch(r"[0-9a-f]{64}", self.mac):
            raise ValueError("malformed G5 token MAC")

    def signing_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version, "algorithm": self.algorithm,
            "issuer_id": self.issuer_id, "key_id": self.key_id,
            "issued_at": self.issued_at, "expires_at": self.expires_at,
            "nonce": self.nonce, "request": self.request.as_mapping(),
        }

    def to_mapping(self) -> dict[str, object]:
        return {**self.signing_mapping(), "mac": self.mac}

    @classmethod
    def from_mapping(cls, value: object) -> "AuthorizationToken":
        data = _mapping(value, keys={
            "schema_version", "algorithm", "issuer_id", "key_id", "issued_at", "expires_at", "nonce", "request", "mac",
        }, what="authorization token")
        return cls(
            request=DeploymentRequest.from_mapping(data["request"]),
            issuer_id=data["issuer_id"], key_id=data["key_id"], issued_at=data["issued_at"],
            expires_at=data["expires_at"], nonce=data["nonce"], mac=data["mac"],
            algorithm=data["algorithm"], schema_version=data["schema_version"],
        )  # type: ignore[arg-type]


class HMACAuthorizer:
    """Issues tokens for one named issuer/key and verifies that exact identity."""

    def __init__(self, secret: bytes, *, issuer_id: str, key_id: str, max_token_ttl_seconds: int) -> None:
        if len(secret) < 32 or not issuer_id or not key_id:
            raise ValueError("a >=32-byte HMAC secret, issuer_id, and key_id are required")
        if type(max_token_ttl_seconds) is not int or max_token_ttl_seconds <= 0:
            raise ValueError("max_token_ttl_seconds must be a positive integer")
        self._secret, self.issuer_id, self.key_id = secret, issuer_id, key_id
        self.max_token_ttl_seconds = max_token_ttl_seconds

    def _mac(self, payload: Mapping[str, object]) -> str:
        return hmac.new(self._secret, _canonical_bytes(payload), hashlib.sha256).hexdigest()

    def issue(self, request: DeploymentRequest, *, issued_at: int, expires_at: int, nonce: str) -> AuthorizationToken:
        unsigned = AuthorizationToken(request, self.issuer_id, self.key_id, issued_at, expires_at, nonce, "0" * 64)
        if expires_at - issued_at > self.max_token_ttl_seconds:
            raise ValueError("token TTL exceeds the frozen maximum")
        return AuthorizationToken(request, self.issuer_id, self.key_id, issued_at, expires_at, nonce, self._mac(unsigned.signing_mapping()))

    def verify_signature(self, token: AuthorizationToken) -> bool:
        return (
            token.issuer_id == self.issuer_id
            and token.key_id == self.key_id
            and token.algorithm == _TOKEN_ALGORITHM
            and token.schema_version == _TOKEN_SCHEMA_VERSION
            and token.expires_at - token.issued_at <= self.max_token_ttl_seconds
            and hmac.compare_digest(token.mac, self._mac(token.signing_mapping()))
        )


@dataclass(frozen=True)
class FrozenRollbackPolicy:
    policy_version: str
    target_conditions: tuple[str, ...]
    neighborhood_conditions: tuple[str, ...]
    policy_sha256: str

    @classmethod
    def build(cls, *, policy_version: str, target_conditions: tuple[str, ...], neighborhood_conditions: tuple[str, ...]) -> "FrozenRollbackPolicy":
        return cls(policy_version, target_conditions, neighborhood_conditions, _sha256(cls._payload(policy_version, target_conditions, neighborhood_conditions)))

    @staticmethod
    def _payload(policy_version: str, target_conditions: tuple[str, ...], neighborhood_conditions: tuple[str, ...]) -> dict[str, object]:
        return {"policy_version": policy_version, "target_conditions": list(target_conditions), "neighborhood_conditions": list(neighborhood_conditions)}

    def __post_init__(self) -> None:
        if not self.policy_version:
            raise ValueError("rollback policy version is required")
        for name, values in (("target", self.target_conditions), ("neighborhood", self.neighborhood_conditions)):
            if not values or any(not value for value in values) or len(set(values)) != len(values):
                raise ValueError(f"{name} acceptance conditions must be non-empty and unique")
        _require_hash(self.policy_sha256, "policy_sha256")
        if not hmac.compare_digest(self.policy_sha256, _sha256(self._payload(self.policy_version, self.target_conditions, self.neighborhood_conditions))):
            raise ValueError("rollback policy hash does not match conditions")


@dataclass(frozen=True)
class StoreSnapshot:
    snapshot_id: str
    state_sha256: str

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            raise ValueError("snapshot_id is required")
        _require_hash(self.state_sha256, "state_sha256")

    def as_mapping(self) -> dict[str, str]:
        return {"snapshot_id": self.snapshot_id, "state_sha256": self.state_sha256}


class TransactionalStoreAdapter(Protocol):
    """Structural test protocol only; no concrete/real adapter is shipped."""
    def snapshot(self) -> StoreSnapshot: ...
    def apply(self, *, target_item_id: str, program_sha256: str) -> None: ...
    def commit(self) -> None: ...
    def rollback(self, snapshot: StoreSnapshot) -> None: ...


@dataclass(frozen=True)
class ProbeOutcome:
    probe_id: str
    conditions: Mapping[str, bool]

    def __post_init__(self) -> None:
        if not self.probe_id or not self.conditions or any(type(value) is not bool for value in self.conditions.values()):
            raise ValueError("probe id and boolean outcome conditions are required")

    def as_mapping(self) -> dict[str, bool]:
        return dict(sorted(self.conditions.items()))


class DeploymentEvaluator(Protocol):
    neighborhood_independent: bool
    target_probe_id: str
    neighborhood_probe_id: str
    def evaluate_target(self, **kwargs: object) -> ProbeOutcome: ...
    def evaluate_neighborhood(self, **kwargs: object) -> ProbeOutcome: ...


@dataclass(frozen=True)
class LedgerEntry:
    ledger_id: int
    nonce: str
    request: dict[str, object]
    committed: bool
    reason: str
    before_snapshot: dict[str, str] | None
    after_snapshot: dict[str, str] | None
    target_outcome: dict[str, bool] | None
    neighborhood_outcome: dict[str, bool] | None
    rollback_performed: bool

    @property
    def before_state_sha256(self) -> str | None:
        return None if self.before_snapshot is None else self.before_snapshot["state_sha256"]

    @property
    def after_state_sha256(self) -> str | None:
        return None if self.after_snapshot is None else self.after_snapshot["state_sha256"]


class DeploymentUseLedger:
    """Persistent append-only use ledger and cross-process nonce reservation."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._connection = sqlite3.connect(self._path, timeout=10, isolation_level=None)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=10000")
        self._connection.execute("CREATE TABLE IF NOT EXISTS g5_nonce_reservations (nonce TEXT PRIMARY KEY NOT NULL, reserved_at INTEGER NOT NULL)")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS g5_use_ledger (
            ledger_id INTEGER PRIMARY KEY AUTOINCREMENT, nonce TEXT NOT NULL, request_json TEXT NOT NULL,
            committed INTEGER NOT NULL, reason TEXT NOT NULL, before_json TEXT, after_json TEXT,
            target_json TEXT, neighborhood_json TEXT, rollback_performed INTEGER NOT NULL
        )""")

    def reserve_nonce(self, nonce: str, *, reserved_at: int) -> bool:
        try:
            self._connection.execute("INSERT INTO g5_nonce_reservations (nonce, reserved_at) VALUES (?, ?)", (nonce, reserved_at))
        except sqlite3.IntegrityError:
            return False
        return True

    def append(self, entry: LedgerEntry) -> LedgerEntry:
        cursor = self._connection.execute(
            """INSERT INTO g5_use_ledger
            (nonce, request_json, committed, reason, before_json, after_json, target_json, neighborhood_json, rollback_performed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (entry.nonce, json.dumps(entry.request, sort_keys=True), int(entry.committed), entry.reason,
             None if entry.before_snapshot is None else json.dumps(entry.before_snapshot, sort_keys=True),
             None if entry.after_snapshot is None else json.dumps(entry.after_snapshot, sort_keys=True),
             None if entry.target_outcome is None else json.dumps(entry.target_outcome, sort_keys=True),
             None if entry.neighborhood_outcome is None else json.dumps(entry.neighborhood_outcome, sort_keys=True), int(entry.rollback_performed)),
        )
        return LedgerEntry(int(cursor.lastrowid), **{field: getattr(entry, field) for field in entry.__dataclass_fields__ if field != "ledger_id"})

    @property
    def rows(self) -> tuple[LedgerEntry, ...]:
        records = self._connection.execute("SELECT ledger_id, nonce, request_json, committed, reason, before_json, after_json, target_json, neighborhood_json, rollback_performed FROM g5_use_ledger ORDER BY ledger_id").fetchall()
        return tuple(LedgerEntry(
            ledger_id=row[0], nonce=row[1], request=json.loads(row[2]), committed=bool(row[3]), reason=row[4],
            before_snapshot=None if row[5] is None else json.loads(row[5]), after_snapshot=None if row[6] is None else json.loads(row[6]),
            target_outcome=None if row[7] is None else json.loads(row[7]), neighborhood_outcome=None if row[8] is None else json.loads(row[8]), rollback_performed=bool(row[9]),
        ) for row in records)


@dataclass(frozen=True)
class DeploymentDecision:
    committed: bool
    ledger_entry: LedgerEntry


class G5DeploymentGuard:
    """Snapshot -> apply -> independent probes -> commit, else rollback."""

    def __init__(self, authorizer: HMACAuthorizer, ledger: DeploymentUseLedger, *, now_epoch: Callable[[], int]) -> None:
        self._authorizer, self._ledger, self._now_epoch = authorizer, ledger, now_epoch

    @property
    def ledger(self) -> tuple[LedgerEntry, ...]:
        return self._ledger.rows

    def _append(self, *, token: AuthorizationToken, request: DeploymentRequest, committed: bool, reason: str,
                before: StoreSnapshot | None = None, after: StoreSnapshot | None = None,
                target: ProbeOutcome | None = None, neighborhood: ProbeOutcome | None = None, rollback: bool = False) -> DeploymentDecision:
        row = self._ledger.append(LedgerEntry(
            ledger_id=0, nonce=token.nonce, request=request.as_mapping(), committed=committed, reason=reason,
            before_snapshot=None if before is None else before.as_mapping(), after_snapshot=None if after is None else after.as_mapping(),
            target_outcome=None if target is None else target.as_mapping(), neighborhood_outcome=None if neighborhood is None else neighborhood.as_mapping(), rollback_performed=rollback,
        ))
        return DeploymentDecision(committed, row)

    def _authorization_reason(self, token: AuthorizationToken, request: DeploymentRequest) -> str | None:
        if token.issuer_id != self._authorizer.issuer_id:
            return "authorization_issuer_mismatch"
        if token.key_id != self._authorizer.key_id:
            return "authorization_key_mismatch"
        if not self._authorizer.verify_signature(token):
            return "authorization_bad_mac"
        if token.issued_at > self._now_epoch():
            return "authorization_not_yet_valid"
        if token.expires_at <= self._now_epoch():
            return "authorization_expired"
        if token.request != request:
            return "authorization_request_mismatch"
        return None

    @staticmethod
    def _accepts(outcome: ProbeOutcome, required: tuple[str, ...]) -> bool:
        return all(outcome.conditions.get(condition) is True for condition in required)

    def execute(self, *, token: AuthorizationToken, request: DeploymentRequest, policy: FrozenRollbackPolicy,
                store: TransactionalStoreAdapter, evaluator: DeploymentEvaluator) -> DeploymentDecision:
        reason = self._authorization_reason(token, request)
        if reason is not None:
            return self._append(token=token, request=request, committed=False, reason=reason)
        if request.rollback_policy_sha256 != policy.policy_sha256:
            return self._append(token=token, request=request, committed=False, reason="authorization_rollback_policy_mismatch")
        if (getattr(evaluator, "neighborhood_independent", False) is not True or not getattr(evaluator, "target_probe_id", "")
                or not getattr(evaluator, "neighborhood_probe_id", "") or evaluator.target_probe_id == evaluator.neighborhood_probe_id):
            return self._append(token=token, request=request, committed=False, reason="non_independent_neighborhood_probe")
        # INSERT's primary key makes this durable and atomic across guard/process boundaries.
        if not self._ledger.reserve_nonce(token.nonce, reserved_at=self._now_epoch()):
            return self._append(token=token, request=request, committed=False, reason="authorization_replay")

        before: StoreSnapshot | None = None
        after: StoreSnapshot | None = None
        target: ProbeOutcome | None = None
        neighborhood: ProbeOutcome | None = None
        try:
            before = store.snapshot()
            store.apply(target_item_id=request.target_item_id, program_sha256=request.program_sha256)
            after = store.snapshot()
            target = evaluator.evaluate_target(request=request, before=before, after=after)
            neighborhood = evaluator.evaluate_neighborhood(request=request, before=before, after=after)
            if target.probe_id != evaluator.target_probe_id or neighborhood.probe_id != evaluator.neighborhood_probe_id:
                raise ValueError("evaluator probe identity changed during transaction")
            if self._accepts(target, policy.target_conditions) and self._accepts(neighborhood, policy.neighborhood_conditions):
                store.commit()
                return self._append(token=token, request=request, committed=True, reason="committed", before=before, after=after, target=target, neighborhood=neighborhood)
            store.rollback(before)
            return self._append(token=token, request=request, committed=False, reason="acceptance_failed", before=before, after=after, target=target, neighborhood=neighborhood, rollback=True)
        except Exception:
            rollback = False
            if before is None:
                reason = "transaction_error_before_snapshot"
            else:
                try:
                    store.rollback(before)
                    rollback, reason = True, "transaction_error"
                except Exception:
                    reason = "transaction_error_rollback_failed"
            return self._append(token=token, request=request, committed=False, reason=reason, before=before, after=after, target=target, neighborhood=neighborhood, rollback=rollback)
