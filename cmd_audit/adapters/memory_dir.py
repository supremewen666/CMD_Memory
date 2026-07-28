"""Load a one-fact-per-Markdown memory directory into ``MemoryItem`` objects."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

from ..core.models import MemoryItem


def load_memory_dir(path: str | Path) -> tuple[MemoryItem, ...]:
    """Load Markdown facts recursively, excluding CMD's private ``.cmd`` area."""
    root = Path(path)
    if not root.is_dir():
        raise ValueError(f"memory directory does not exist: {root}")
    items = [
        load_memory_file(file_path, root=root)
        for file_path in sorted(root.rglob("*.md"))
        if ".cmd" not in file_path.relative_to(root).parts
    ]
    return tuple(items)


def load_memory_file(path: str | Path, *, root: str | Path | None = None) -> MemoryItem:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    relative = file_path.relative_to(Path(root)) if root is not None else file_path
    memory_id = relative.with_suffix("").as_posix()
    timestamp = (
        frontmatter.get("timestamp")
        or frontmatter.get("updated")
        or frontmatter.get("date")
        or _mtime_iso(file_path)
    )
    description = frontmatter.get("description", "").strip()
    fact = body.strip() or description or frontmatter.get("name", "").strip()
    if not fact:
        raise ValueError(f"memory file has no fact content: {file_path}")
    source_ids = tuple(
        token.strip()
        for token in frontmatter.get("source_event_ids", "").split(",")
        if token.strip()
    )
    return MemoryItem(
        memory_id=memory_id,
        text=fact,
        source_event_ids=source_ids,
        store=_normalize_timestamp(timestamp, file_path),
    )


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    match = re.match(r"^---\s*\n(?P<meta>.*?)\n---\s*(?:\n|$)", text, re.DOTALL)
    if match is None:
        raise ValueError("unterminated YAML frontmatter")
    metadata: dict[str, str] = {}
    for line in match.group("meta").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip().casefold()] = value.strip().strip("\"'")
    return metadata, text[match.end() :]


def _normalize_timestamp(value: str, path: Path) -> str:
    raw = str(value).strip()
    if not raw:
        return _mtime_iso(path)
    candidate = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return _mtime_iso(path)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _mtime_iso(path: Path) -> str:
    return (
        datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
