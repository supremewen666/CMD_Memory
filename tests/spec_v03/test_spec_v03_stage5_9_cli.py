from __future__ import annotations

import sys

import pytest

from experiments import spec_v03_stage5_9 as subject


def test_cli_parses_router_snapshot_migration_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "spec_v03_stage5_9.py",
            "--runtime-cases", "cases.json",
            "--event-order", "order.json",
            "--output", "report.json",
            "--run-id", "migration",
            "--stage", "stage5",
            "--initial-router-snapshot", "source_router.json",
            "--router-snapshot-output", "target_router.json",
            "--adaptation-prefix-ratio", "0.2",
        ],
    )
    args = subject._parse_args()
    assert args.initial_router_snapshot.name == "source_router.json"
    assert args.router_snapshot_output.name == "target_router.json"
    assert args.adaptation_prefix_ratio == 0.2


def test_cli_rejects_router_migration_without_stage5(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "spec_v03_stage5_9.py",
            "--runtime-cases", "cases.json",
            "--event-order", "order.json",
            "--output", "report.json",
            "--run-id", "migration",
            "--stage", "stage6",
            "--adaptation-prefix-ratio", "0.2",
        ],
    )
    with pytest.raises(ValueError, match="require --stage stage5"):
        subject.main()
