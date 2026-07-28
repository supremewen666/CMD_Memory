from pathlib import Path

from cmd_audit.adapters.memory_dir import load_memory_dir


def test_memory_dir_loads_frontmatter_and_excludes_private_reports(
    tmp_path: Path,
) -> None:
    first = tmp_path / "facts" / "current.md"
    first.parent.mkdir()
    first.write_text(
        "---\n"
        "name: Current city\n"
        "description: Latest city fact\n"
        "type: fact\n"
        "timestamp: 2026-07-01T12:00:00Z\n"
        "source_event_ids: e1, e2\n"
        "---\n"
        "The user currently lives in Shanghai.\n",
        encoding="utf-8",
    )
    private = tmp_path / ".cmd" / "report.md"
    private.parent.mkdir()
    private.write_text("not a memory fact", encoding="utf-8")

    items = load_memory_dir(tmp_path)

    assert len(items) == 1
    assert items[0].memory_id == "facts/current"
    assert items[0].store == "2026-07-01T12:00:00Z"
    assert items[0].source_event_ids == ("e1", "e2")
    assert "Shanghai" in items[0].text


def test_memory_dir_uses_file_mtime_when_timestamp_is_missing(
    tmp_path: Path,
) -> None:
    fact = tmp_path / "fact.md"
    fact.write_text("A durable fact.", encoding="utf-8")

    item = load_memory_dir(tmp_path)[0]

    assert item.store.endswith("Z")
