from pathlib import Path

from cmd_audit.cli import main
from cmd_audit.repair.store_repair import (
    execute_store_repair,
    memory_dir_checksum,
)


def _write_fact(path: Path, timestamp: str, body: str) -> None:
    path.write_text(
        f"---\ntimestamp: {timestamp}\n---\n{body}\n",
        encoding="utf-8",
    )


def _memory_dir(tmp_path: Path) -> Path:
    root = tmp_path / "memory"
    root.mkdir()
    _write_fact(
        root / "old.md",
        "2026-01-01T00:00:00Z",
        "The user's city preference is Paris.",
    )
    _write_fact(
        root / "new.md",
        "2026-03-01T00:00:00Z",
        "The user's city preference is Shanghai.",
    )
    return root


def test_dry_run_writes_report_without_changing_facts(tmp_path: Path) -> None:
    root = _memory_dir(tmp_path)
    before = memory_dir_checksum(root)

    result = execute_store_repair(
        root,
        mode="dry-run",
        similarity_threshold=0.2,
    )

    assert not result.applied
    assert result.gate == "dry_run_only"
    assert memory_dir_checksum(root) == before
    assert Path(result.report_path).is_file()


def test_apply_snapshots_and_demotes_stale_fact(tmp_path: Path) -> None:
    root = _memory_dir(tmp_path)

    result = execute_store_repair(
        root,
        mode="apply",
        similarity_threshold=0.2,
    )

    assert result.applied
    assert result.gate == "accepted_retention_surrogate"
    assert result.demoted_ids == ("old",)
    assert not (root / "old.md").exists()
    assert (root / "new.md").exists()
    assert Path(result.snapshot_path, "old.md").is_file()


def test_failed_probe_rolls_back_to_identical_checksum(tmp_path: Path) -> None:
    root = _memory_dir(tmp_path)
    before = memory_dir_checksum(root)

    result = execute_store_repair(
        root,
        mode="apply",
        similarity_threshold=0.2,
        validation_probe=lambda _root, _plan: False,
    )

    assert not result.applied
    assert result.rolled_back
    assert memory_dir_checksum(root) == before
    assert (root / "old.md").is_file()
    assert (root / "new.md").is_file()


def test_cli_exposes_repair_store_dry_run(tmp_path: Path) -> None:
    root = _memory_dir(tmp_path)

    exit_code = main(
        [
            "repair-store",
            str(root),
            "--mode",
            "dry-run",
            "--similarity-threshold",
            "0.2",
        ]
    )

    assert exit_code == 0
    assert (root / ".cmd" / "repair-report.json").is_file()
