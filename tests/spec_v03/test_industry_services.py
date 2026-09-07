from __future__ import annotations

import json
from pathlib import Path
import pytest

from cmd_audit.spec_v03.industry_services import (
    MeteringProxy, ProxyLimits, UsageReceiptStore, valid_scope,
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


def test_metering_proxy_can_enforce_non_thinking_controlled_inference(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = UsageReceiptStore(tmp_path, ProxyLimits(1, 1000, 20, 10))
    proxy = MeteringProxy(
        upstream="http://127.0.0.1:8001/v1", receipts=store,
        disable_thinking=True,
    )
    seen: dict[str, object] = {}

    def urlopen(req, **_kwargs):
        seen.update(json.loads(req.data))
        return _Response()

    monkeypatch.setattr("cmd_audit.spec_v03.industry_services.request.urlopen", urlopen)
    status, _body, _content_type = proxy.forward(
        SCOPE, "chat/completions",
        json.dumps({"max_tokens": 8, "messages": [], "chat_template_kwargs": {"foo": "bar"}}).encode(),
        {"Content-Type": "application/json"},
    )
    assert status == 200
    assert seen["chat_template_kwargs"] == {"foo": "bar", "enable_thinking": False}


def test_scope_contract_is_closed() -> None:
    assert valid_scope(SCOPE) == SCOPE
    with pytest.raises(ValueError):
        valid_scope("shared")
