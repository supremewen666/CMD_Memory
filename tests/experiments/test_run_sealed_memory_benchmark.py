from __future__ import annotations

from types import SimpleNamespace

import pytest

import experiments.run_sealed_memory_benchmark as runner


class _Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.payload


def _backend(model: str = "served"):
    config = SimpleNamespace(
        base_url="http://127.0.0.1:8000/v1",
        api_key="",
        model=model,
    )
    return SimpleNamespace(answer_client=SimpleNamespace(config=config))


def test_endpoint_preflight_accepts_matching_served_model(monkeypatch) -> None:
    monkeypatch.setattr(
        runner,
        "urlopen",
        lambda *_args, **_kwargs: _Response(b'{"data":[{"id":"served"}]}'),
    )
    result = runner.preflight_openai_endpoint(_backend())
    assert result["model"] == "served"


def test_endpoint_preflight_rejects_model_path_not_served_name(monkeypatch) -> None:
    monkeypatch.setattr(
        runner,
        "urlopen",
        lambda *_args, **_kwargs: _Response(b'{"data":[{"id":"served"}]}'),
    )
    with pytest.raises(RuntimeError, match="served-model-name"):
        runner.preflight_openai_endpoint(_backend("/models/Qwen"))
