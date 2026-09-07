"""Small, compatibility-first primitives for durable state artifacts.

The helpers deliberately expose encoding knobs: older ledgers used both ASCII
and UTF-8 JSON encodings, and their content hashes are part of the audit
contract.  Callers must therefore opt into the encoding they already used.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Mapping, Iterable


def canonical_json(
    value: object, *, ensure_ascii: bool = True, allow_nan: bool = True,
    indent: int | None = None, trailing_newline: bool = False,
) -> str:
    """Return deterministic JSON without imposing a new persistence schema."""
    text = json.dumps(
        value, ensure_ascii=ensure_ascii, sort_keys=True,
        separators=(",", ":") if indent is None else None,
        allow_nan=allow_nan, indent=indent,
    )
    return text + ("\n" if trailing_newline else "")


def content_sha256(value: object, *, ensure_ascii: bool = True, allow_nan: bool = True) -> str:
    return hashlib.sha256(
        canonical_json(value, ensure_ascii=ensure_ascii, allow_nan=allow_nan).encode("utf-8")
    ).hexdigest()


def require_closed_mapping(value: Mapping[str, object], fields: Iterable[str], name: str = "mapping") -> None:
    if set(value) != set(fields):
        raise ValueError(f"{name} must be a closed mapping")


def require_sha256(actual: object, expected: str, name: str = "content") -> None:
    if not isinstance(actual, str) or actual != expected:
        raise ValueError(f"{name} hash mismatch")


def append_jsonl_fsync(path: str | Path, value: object, *, ensure_ascii: bool = True, allow_nan: bool = True) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(value, ensure_ascii=ensure_ascii, allow_nan=allow_nan) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def atomic_json_write(
    path: str | Path, value: object, *, ensure_ascii: bool = True,
    allow_nan: bool = True, indent: int | None = None, trailing_newline: bool = False,
) -> None:
    """Atomically replace a JSON cache/pointer; journal remains the authority."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        canonical_json(value, ensure_ascii=ensure_ascii, allow_nan=allow_nan,
                       indent=indent, trailing_newline=trailing_newline),
        encoding="utf-8",
    )
    os.replace(temporary, target)
