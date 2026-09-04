from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from cmd_audit.spec_v03.industry_services import (
    LycheeInstanceManager, MeteringProxy, ProxyLimits, UsageReceiptStore, valid_scope,
)


SCOPE = "cmd-" + "a" * 24


class _Response:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps({
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        }).encode()


def test_metering_proxy_bootstraps_and_atomically_accounts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = UsageReceiptStore(tmp_path, ProxyLimits(2, 1000, 20, 10))
    proxy = MeteringProxy(upstream="http://127.0.0.1:8001/v1", receipts=store)
    assert proxy.ensure(SCOPE) == {"status": "ready", "scope": SCOPE}
    monkeypatch.setattr("cmd_audit.spec_v03.industry_services.request.urlopen", lambda *_args, **_kwargs: _Response())
    status, _body, _content_type = proxy.forward(
        SCOPE, "chat/completions", json.dumps({"max_tokens": 8, "messages": []}).encode(),
        {"Content-Type": "application/json"},
    )
    assert status == 200
    receipt = store.read(SCOPE)
    assert (receipt["llm_calls"], receipt["input_tokens"], receipt["output_tokens"]) == (1, 7, 3)
    assert receipt["gpu_seconds"] == 1


def test_metering_proxy_rejects_before_upstream_when_budget_is_exhausted(tmp_path: Path) -> None:
    store = UsageReceiptStore(tmp_path, ProxyLimits(0, 1000, 20, 10))
    proxy = MeteringProxy(upstream="http://127.0.0.1:8001/v1", receipts=store)
    status, body, _ = proxy.forward(
        SCOPE, "chat/completions", json.dumps({"max_tokens": 1}).encode(),
        {"Content-Type": "application/json"},
    )
    assert status == 429
    assert json.loads(body)["error"]["type"] == "budget_exhausted"


def test_metering_proxy_reserves_estimated_tokens_not_request_bytes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = UsageReceiptStore(tmp_path, ProxyLimits(1, 100, 20, 10))
    proxy = MeteringProxy(upstream="http://127.0.0.1:8001/v1", receipts=store)
    monkeypatch.setattr("cmd_audit.spec_v03.industry_services.request.urlopen", lambda *_args, **_kwargs: _Response())
    body = json.dumps({"max_tokens": 8, "messages": [{"content": "x" * 120}]}).encode()
    status, _body, _content_type = proxy.forward(
        SCOPE, "chat/completions", body, {"Content-Type": "application/json"},
    )
    assert status == 200


def test_scope_contract_is_closed() -> None:
    assert valid_scope(SCOPE) == SCOPE
    with pytest.raises(ValueError):
        valid_scope("shared")


def test_lychee_manager_binds_official_checkout_and_isolated_paths(tmp_path: Path) -> None:
    repository = tmp_path / "LycheeMem"
    repository.mkdir()
    subprocess.run(("git", "init", "-q", str(repository)), check=True)
    subprocess.run(("git", "-C", str(repository), "config", "user.email", "test@example.com"), check=True)
    subprocess.run(("git", "-C", str(repository), "config", "user.name", "Test"), check=True)
    (repository / "README").write_text("fixture", encoding="utf-8")
    subprocess.run(("git", "-C", str(repository), "add", "README"), check=True)
    subprocess.run(("git", "-C", str(repository), "commit", "-q", "-m", "fixture"), check=True)
    commit = subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), check=True, capture_output=True, text=True).stdout.strip()
    manager = LycheeInstanceManager(
        repository=repository, python=Path("/python"), root=tmp_path / "instances",
        receipt_root=tmp_path / "receipts", official_commit=commit,
        public_base_url="http://127.0.0.1:9000", llm_proxy_base_url="http://127.0.0.1:9100",
        embedding_base_url="http://127.0.0.1:8003", embedding_model="all-MiniLM-L6-v2",
    )
    env = manager._environment(SCOPE, tmp_path / "instances" / SCOPE)
    assert env["LLM_API_BASE"] == f"http://127.0.0.1:9100/{SCOPE}/v1"
    assert SCOPE in env["COMPACT_MEMORY_DB_PATH"]
    assert env["EMBEDDING_BACKEND"] == "http"
    assert env["EMBEDDING_MODEL"] == "all-MiniLM-L6-v2"
    assert env["EMBEDDING_API_BASE"] == "http://127.0.0.1:8003/v1"
    assert env["EMBEDDING_DIM"] == "384"
    with pytest.raises(ValueError, match="claim"):
        manager.ensure(SCOPE, claimed_base_url="http://wrong", claimed_commit=commit)
