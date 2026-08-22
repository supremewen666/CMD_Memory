"""Deterministic transport tests for public dataset acquisition."""
from __future__ import annotations

import hashlib
import importlib
import json
import urllib.error
from pathlib import Path

acquisition = importlib.import_module("experiments.download_datasets")


class _Response:
    def __init__(self, body: bytes, start: int, total: int) -> None:
        self._body = body
        self.status = 206
        self.headers = {"Content-Range": f"bytes {start}-{start + len(body) - 1}/{total}", "Content-Length": str(len(body))}

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            value, self._body = self._body, b""
            return value
        value, self._body = self._body[:size], self._body[size:]
        return value

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _range_server(payload: bytes, *, short_first: bool = False):
    calls: list[int] = []

    def open_url(request, timeout: int):  # type: ignore[no-untyped-def]
        start = int(request.headers["Range"].removeprefix("bytes=").removesuffix("-"))
        calls.append(start)
        end = start + 2 if short_first and len(calls) == 1 else len(payload)
        return _Response(payload[start:end], start, len(payload))

    return open_url, calls


def test_fetch_retries_short_response_until_exact_length(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    payload = b"0123456789"
    opener, calls = _range_server(payload, short_first=True)
    monkeypatch.setattr(acquisition.urllib.request, "urlopen", opener)
    target = tmp_path / "fixture.bin"

    acquisition.fetch("https://official.invalid/file", target, False)

    assert calls == [0, 2]
    assert target.read_bytes() == payload
    assert not target.with_suffix(".bin.partial").exists()


def test_fetch_migrates_invalid_target_and_resumes_prefix(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    payload = b"[1,2]"
    opener, calls = _range_server(payload)
    monkeypatch.setattr(acquisition.urllib.request, "urlopen", opener)
    target = tmp_path / "rows.json"
    target.write_bytes(payload[:2])  # Valid remote prefix, invalid JSON document.

    acquisition.fetch("https://official.invalid/rows", target, False, validator=acquisition.json_rows)

    assert calls == [2]
    assert json.loads(target.read_text()) == [1, 2]


def test_fetch_confirms_headerless_416_with_last_byte_probe(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    payload = b"complete"
    target = tmp_path / "fixture.bin"
    partial = target.with_suffix(".bin.partial")
    partial.write_bytes(payload)
    calls: list[str] = []

    def open_url(request, timeout: int):  # type: ignore[no-untyped-def]
        requested = request.headers["Range"]
        calls.append(requested)
        if requested == f"bytes={len(payload)}-":
            raise urllib.error.HTTPError(request.full_url, 416, "range", {}, None)
        assert requested == f"bytes={len(payload) - 1}-{len(payload) - 1}"
        return _Response(payload[-1:], len(payload) - 1, len(payload))

    monkeypatch.setattr(acquisition.urllib.request, "urlopen", open_url)
    acquisition.fetch("https://official.invalid/file", target, False)

    assert calls == [f"bytes={len(payload)}-", f"bytes={len(payload) - 1}-{len(payload) - 1}"]
    assert target.read_bytes() == payload
    assert not partial.exists()


def test_sha256_is_deterministic_for_manifest_verification(tmp_path: Path) -> None:
    target = tmp_path / "source.txt"
    target.write_bytes(b"official-source")
    assert acquisition.sha256(target) == hashlib.sha256(b"official-source").hexdigest()
