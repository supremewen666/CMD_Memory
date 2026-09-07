from __future__ import annotations

import sys
import json

import pytest

from experiments import spec_v03_stage5_9 as subject
from cmd_audit.spec_v03.industry_adapters import PinnedJsonSubprocessAdapter, UnsupportedAdapter


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


def test_cli_parses_family_disjoint_split_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "spec_v03_stage5_9.py",
            "--runtime-cases", "cases.json",
            "--event-order", "order.json",
            "--output", "report.json",
            "--run-id", "family-disjoint",
            "--split-manifest", "split.json",
            "--include-split", "T_online",
            "--include-split", "T_final",
            "--split-audit-output", "audit.json",
        ],
    )
    args = subject._parse_args()
    assert args.include_splits == ["T_online", "T_final"]
    assert args.split_manifest.name == "split.json"


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


def test_cli_parses_native_track_and_system_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "spec_v03_stage5_9.py",
            "--runtime-cases", "cases.json",
            "--event-order", "order.json",
            "--output", "report.json",
            "--run-id", "industry",
            "--track", "native",
            "--system-max-llm-calls", "2",
            "--system-max-input-tokens", "101",
            "--system-max-output-tokens", "53",
            "--system-max-wall-seconds", "4.5",
            "--system-max-gpu-seconds", "1",
        ],
    )
    args = subject._parse_args()
    assert args.track == "native"
    assert (
        args.system_max_llm_calls,
        args.system_max_input_tokens,
        args.system_max_output_tokens,
        args.system_max_wall_seconds,
        args.system_max_gpu_seconds,
    ) == (2, 101, 53, 4.5, 1)


@pytest.mark.parametrize(
    ("flag", "value"),
    (
        ("--system-max-llm-calls", "-1"),
        ("--system-max-wall-seconds", "-1"),
        ("--system-max-wall-seconds", "nan"),
        ("--system-max-wall-seconds", "inf"),
    ),
)
def test_cli_rejects_invalid_system_budget(
    monkeypatch: pytest.MonkeyPatch, flag: str, value: str,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "spec_v03_stage5_9.py",
            "--runtime-cases", "cases.json",
            "--event-order", "order.json",
            "--output", "report.json",
            "--run-id", "industry",
            flag, value,
        ],
    )
    with pytest.raises(SystemExit):
        subject._parse_args()


def test_industry_adapter_config_is_closed_and_unconfigured_is_fail_closed(tmp_path) -> None:
    unconfigured = subject._load_industry_adapters(None)
    assert set(unconfigured) == {"memskill", "erskill", "mem0"}
    assert all(isinstance(adapter, UnsupportedAdapter) for adapter in unconfigured.values())

    config = tmp_path / "industry.json"
    config.write_text(json.dumps({
        "memskill": {
            "command": ["python", "wrapper.py"],
            "repository": "/opt/memskill",
            "pinned_commit": "a" * 40,
            "supported_tracks": ["controlled_a1", "native"],
            "timeout_seconds": 300,
        },
    }), encoding="utf-8")
    adapters = subject._load_industry_adapters(config)
    assert isinstance(adapters["memskill"], PinnedJsonSubprocessAdapter)
    assert isinstance(adapters["erskill"], UnsupportedAdapter)
    assert isinstance(adapters["mem0"], UnsupportedAdapter)


def test_industry_adapter_config_rejects_unknown_system(tmp_path) -> None:
    config = tmp_path / "industry.json"
    config.write_text(json.dumps({"unknown": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="only memskill"):
        subject._load_industry_adapters(config)


def test_main_injects_configured_adapters_and_cli_budget(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "industry.json"
    config.write_text(json.dumps({
        "mem0": {
            "command": ["python", "wrapper.py"],
            "repository": "/opt/mem0",
            "pinned_commit": "b" * 40,
        },
    }), encoding="utf-8")
    captured: dict[str, object] = {}

    class _Report:
        report_sha256 = "test-report"
        results: dict[str, object] = {}

        @staticmethod
        def to_mapping() -> dict[str, object]:
            return {"report_sha256": "test-report"}

    class _Runner:
        def __init__(self, _config, capabilities) -> None:
            captured["capabilities"] = capabilities

        def run(self, _bundles, _order, *, system_budget):
            captured["budget"] = system_budget
            return _Report()

    monkeypatch.setattr(subject, "load_runtime_cases", lambda _path: ())
    monkeypatch.setattr(subject.RuntimeOrderManifest, "from_mapping", lambda _raw: object())
    monkeypatch.setattr(subject, "Stage59Runner", _Runner)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "spec_v03_stage5_9.py",
            "--runtime-cases", "cases.json",
            "--event-order", str(tmp_path / "order.json"),
            "--output", str(tmp_path / "report.json"),
            "--run-id", "industry",
            "--stage", "stage9",
            "--industry-adapters-config", str(config),
            "--system-max-llm-calls", "3",
            "--system-max-input-tokens", "10",
            "--system-max-output-tokens", "5",
            "--system-max-wall-seconds", "2.5",
            "--system-max-gpu-seconds", "1",
        ],
    )
    (tmp_path / "order.json").write_text("{}", encoding="utf-8")

    assert subject.main() == 0
    capabilities = captured["capabilities"]
    assert isinstance(capabilities.industry_adapters["mem0"], PinnedJsonSubprocessAdapter)
    assert isinstance(capabilities.industry_adapters["memskill"], UnsupportedAdapter)
    assert captured["budget"].to_mapping() == {
        "llm_calls": 3,
        "input_tokens": 10,
        "output_tokens": 5,
        "wall_clock_seconds": 2.5,
        "gpu_seconds": 1,
    }
