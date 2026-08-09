"""G5 must be a content-bound transaction, never an offline-G1 shortcut."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from cmd_audit.counterfactual.successor_deployment_guard import (
    AuthorizationToken,
    DeploymentRequest,
    DeploymentUseLedger,
    FrozenRollbackPolicy,
    G5DeploymentGuard,
    HMACAuthorizer,
    ProbeOutcome,
    StoreSnapshot,
)


def _hash(char: str) -> str:
    return char * 64


def _nonce(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()[:32]


@dataclass
class FakeStore:
    state: str = "before"
    snapshots: int = 0
    apply_calls: int = 0
    commits: int = 0
    rollbacks: int = 0

    def snapshot(self) -> StoreSnapshot:
        self.snapshots += 1
        return StoreSnapshot(snapshot_id=f"s{self.snapshots}", state_sha256=_hash("a" if self.state == "before" else "b"))

    def apply(self, *, target_item_id: str, program_sha256: str) -> None:
        assert target_item_id == "old"
        assert program_sha256 == _hash("e")
        self.apply_calls += 1
        self.state = "after"

    def commit(self) -> None:
        self.commits += 1

    def rollback(self, snapshot: StoreSnapshot) -> None:
        assert snapshot.snapshot_id == "s1"
        self.rollbacks += 1
        self.state = "before"


@dataclass(frozen=True)
class FakeEvaluator:
    target: ProbeOutcome
    neighborhood: ProbeOutcome
    neighborhood_independent: bool = True
    target_probe_id: str = "target-v1"
    neighborhood_probe_id: str = "neighborhood-v1"

    def evaluate_target(self, **_kwargs: object) -> ProbeOutcome:
        return self.target

    def evaluate_neighborhood(self, **_kwargs: object) -> ProbeOutcome:
        return self.neighborhood


def _policy() -> FrozenRollbackPolicy:
    return FrozenRollbackPolicy.build(
        policy_version="g5-policy-v1",
        target_conditions=("target_fixed",),
        neighborhood_conditions=("no_collateral_damage",),
    )


def _request(policy: FrozenRollbackPolicy, *, target: str = "old", program: str | None = None) -> DeploymentRequest:
    return DeploymentRequest(
        protocol_manifest_sha256=_hash("f"),
        program_sha256=program or _hash("e"),
        graph_sha256=_hash("d"),
        case_id="case-1",
        runtime_case_sha256=_hash("c"),
        target_item_id=target,
        rollback_policy_sha256=policy.policy_sha256,
    )


def _passed() -> tuple[ProbeOutcome, ProbeOutcome]:
    return (
        ProbeOutcome("target-v1", {"target_fixed": True}),
        ProbeOutcome("neighborhood-v1", {"no_collateral_damage": True}),
    )


def _guard(path: object) -> tuple[G5DeploymentGuard, HMACAuthorizer]:
    signer = HMACAuthorizer(b"x" * 32, issuer_id="issuer-v1", key_id="key-v1", max_token_ttl_seconds=60)
    return G5DeploymentGuard(signer, DeploymentUseLedger(path), now_epoch=lambda: 100), signer


def test_valid_exact_token_commits_and_ledger_binds_before_after_and_outcomes(tmp_path: object) -> None:
    policy = _policy()
    request = _request(policy)
    guard, signer = _guard(tmp_path / "g5.sqlite")
    token = signer.issue(request, issued_at=100, expires_at=101, nonce=_nonce("nonce-1"))
    target, neighborhood = _passed()
    store = FakeStore()

    decision = guard.execute(token=token, request=request, policy=policy, store=store, evaluator=FakeEvaluator(target, neighborhood))

    assert decision.committed is True
    assert store.apply_calls == store.commits == 1
    assert store.rollbacks == 0
    assert decision.ledger_entry.before_state_sha256 == _hash("a")
    assert decision.ledger_entry.after_state_sha256 == _hash("b")
    assert decision.ledger_entry.rollback_performed is False
    assert decision.ledger_entry.target_outcome == {"target_fixed": True}
    assert decision.ledger_entry.neighborhood_outcome == {"no_collateral_damage": True}
    assert len(guard.ledger) == 1


def test_expired_or_hash_mismatched_or_target_mismatched_tokens_refuse_before_snapshot(tmp_path: object) -> None:
    policy = _policy()
    request = _request(policy)
    guard, signer = _guard(tmp_path / "g5.sqlite")
    target, neighborhood = _passed()
    for token, attempted in (
        (signer.issue(request, issued_at=100, expires_at=100, nonce=_nonce("expired")), request),
        (signer.issue(request, issued_at=101, expires_at=102, nonce=_nonce("future")), request),
        (signer.issue(request, issued_at=100, expires_at=101, nonce=_nonce("program")), _request(policy, program=_hash("b"))),
        (signer.issue(request, issued_at=100, expires_at=101, nonce=_nonce("target")), _request(policy, target="other")),
    ):
        store = FakeStore()
        decision = guard.execute(token=token, request=attempted, policy=policy, store=store, evaluator=FakeEvaluator(target, neighborhood))
        assert decision.committed is False
        assert store.snapshots == store.apply_calls == store.commits == store.rollbacks == 0
        assert decision.ledger_entry.reason.startswith("authorization_")


def test_token_is_single_use_even_when_first_transaction_rolls_back(tmp_path: object) -> None:
    policy = _policy()
    request = _request(policy)
    guard, signer = _guard(tmp_path / "g5.sqlite")
    token = signer.issue(request, issued_at=100, expires_at=101, nonce=_nonce("only-once"))
    store = FakeStore()
    target, neighborhood = _passed()
    failed_neighborhood = ProbeOutcome("neighborhood-v1", {"no_collateral_damage": False})

    first = guard.execute(token=token, request=request, policy=policy, store=store, evaluator=FakeEvaluator(target, failed_neighborhood))
    second = guard.execute(token=token, request=request, policy=policy, store=store, evaluator=FakeEvaluator(target, neighborhood))

    assert first.committed is False
    assert store.apply_calls == 1 and store.rollbacks == 1
    assert second.committed is False
    assert second.ledger_entry.reason == "authorization_replay"
    assert store.apply_calls == 1


def test_any_frozen_acceptance_failure_rolls_back_and_is_ledgered(tmp_path: object) -> None:
    policy = _policy()
    request = _request(policy)
    guard, signer = _guard(tmp_path / "g5.sqlite")
    token = signer.issue(request, issued_at=100, expires_at=101, nonce=_nonce("rollback"))
    target, _ = _passed()
    store = FakeStore()

    decision = guard.execute(
        token=token,
        request=request,
        policy=policy,
        store=store,
        evaluator=FakeEvaluator(target, ProbeOutcome("neighborhood-v1", {"no_collateral_damage": False})),
    )

    assert decision.committed is False
    assert decision.ledger_entry.reason == "acceptance_failed"
    assert decision.ledger_entry.rollback_performed is True
    assert store.state == "before" and store.rollbacks == 1 and store.commits == 0


def test_non_independent_probe_refuses_before_mutation(tmp_path: object) -> None:
    policy = _policy()
    request = _request(policy)
    guard, signer = _guard(tmp_path / "g5.sqlite")
    token = signer.issue(request, issued_at=100, expires_at=101, nonce=_nonce("non-independent"))
    target, neighborhood = _passed()
    store = FakeStore()

    decision = guard.execute(
        token=token,
        request=request,
        policy=policy,
        store=store,
        evaluator=FakeEvaluator(target, neighborhood, neighborhood_independent=False),
    )

    assert decision.committed is False
    assert decision.ledger_entry.reason == "non_independent_neighborhood_probe"
    assert store.snapshots == store.apply_calls == 0


def test_ledger_is_append_only_snapshot(tmp_path: object) -> None:
    policy = _policy()
    request = _request(policy)
    guard, signer = _guard(tmp_path / "g5.sqlite")
    token = signer.issue(request, issued_at=100, expires_at=101, nonce=_nonce("ledger"))
    target, neighborhood = _passed()
    guard.execute(token=token, request=request, policy=policy, store=FakeStore(), evaluator=FakeEvaluator(target, neighborhood))

    assert isinstance(guard.ledger, tuple)
    assert guard.ledger[0].nonce == _nonce("ledger")


def test_two_guards_sharing_one_sqlite_ledger_cannot_replay(tmp_path: object) -> None:
    policy = _policy()
    request = _request(policy)
    ledger_path = tmp_path / "shared.sqlite"
    first_guard, signer = _guard(ledger_path)
    second_guard, _ = _guard(ledger_path)
    token = signer.issue(request, issued_at=100, expires_at=101, nonce=_nonce("shared-once"))
    target, neighborhood = _passed()
    first_store, second_store = FakeStore(), FakeStore()

    first = first_guard.execute(token=token, request=request, policy=policy, store=first_store, evaluator=FakeEvaluator(target, neighborhood))
    second = second_guard.execute(token=token, request=request, policy=policy, store=second_store, evaluator=FakeEvaluator(target, neighborhood))

    assert first.committed is True
    assert second.ledger_entry.reason == "authorization_replay"
    assert second_store.snapshots == second_store.apply_calls == 0
    assert len(second_guard.ledger) == 2


def test_bad_issuer_or_key_is_refused_before_snapshot(tmp_path: object) -> None:
    policy = _policy()
    request = _request(policy)
    guard, _ = _guard(tmp_path / "g5.sqlite")
    bad_issuer = HMACAuthorizer(b"x" * 32, issuer_id="other", key_id="key-v1", max_token_ttl_seconds=60)
    bad_key = HMACAuthorizer(b"x" * 32, issuer_id="issuer-v1", key_id="other", max_token_ttl_seconds=60)
    target, neighborhood = _passed()
    for token, reason in (
        (bad_issuer.issue(request, issued_at=100, expires_at=101, nonce=_nonce("bad-issuer")), "authorization_issuer_mismatch"),
        (bad_key.issue(request, issued_at=100, expires_at=101, nonce=_nonce("bad-key")), "authorization_key_mismatch"),
    ):
        store = FakeStore()
        decision = guard.execute(token=token, request=request, policy=policy, store=store, evaluator=FakeEvaluator(target, neighborhood))
        assert decision.ledger_entry.reason == reason
        assert store.snapshots == store.apply_calls == 0


def test_token_wire_mapping_round_trips_with_fixed_schema_and_algorithm(tmp_path: object) -> None:
    policy = _policy()
    request = _request(policy)
    _, signer = _guard(tmp_path / "g5.sqlite")
    token = signer.issue(request, issued_at=100, expires_at=101, nonce=_nonce("wire"))

    restored = AuthorizationToken.from_mapping(token.to_mapping())

    assert restored == token
    assert restored.algorithm == "HMAC-SHA256"
    assert restored.schema_version == "successor-v3-g5-token-v1"


def test_authorizer_rejects_short_keys_bad_nonces_and_excessive_ttl(tmp_path: object) -> None:
    policy = _policy()
    request = _request(policy)
    try:
        HMACAuthorizer(b"short", issuer_id="issuer-v1", key_id="key-v1", max_token_ttl_seconds=60)
    except ValueError as error:
        assert ">=32-byte" in str(error)
    else:
        raise AssertionError("short HMAC key was accepted")

    _, signer = _guard(tmp_path / "g5.sqlite")
    for nonce in ("", "not-hex", "a" * 31, "a" * 33, "A" * 32):
        try:
            signer.issue(request, issued_at=100, expires_at=101, nonce=nonce)
        except ValueError:
            pass
        else:
            raise AssertionError(f"malformed nonce was accepted: {nonce!r}")
    try:
        signer.issue(request, issued_at=100, expires_at=161, nonce=_nonce("too-long"))
    except ValueError as error:
        assert "TTL" in str(error)
    else:
        raise AssertionError("overlong token TTL was accepted")
