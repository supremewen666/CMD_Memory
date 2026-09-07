from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
import subprocess
import sys

from cmd_audit.spec_v03.runtime_bundle import serialize
from tests.spec_v03.test_stage59_runner import _bundle, _order


REPO = Path(__file__).resolve().parents[2]


def _record() -> dict[str, object]:
    return {
        "case_id": "case-1",
        "evidence": [{"memory": "new supersedes old"}],
        "selected_skill_ids": ["temporal-update"],
        "retrieval_trace": [{"primitive": "semantic_search", "rank": 1}],
        "source_event_ids": ["event-1"],
        "usage": {
            "llm_calls": 1, "input_tokens": 20, "output_tokens": 4,
            "wall_clock_seconds": 0.25, "gpu_seconds": 0,
        },
    }


def test_freeze_skill_evidence_and_bind_runtime(tmp_path: Path) -> None:
    records = tmp_path / "records.jsonl"
    records.write_text(json.dumps(_record()) + "\n", encoding="utf-8")
    memskill = tmp_path / "memskill.json"
    erskill = tmp_path / "erskill.json"
    for system_id, implementation, output in (
        ("memskill", "official_memskill_checkpoint_export", memskill),
        ("erskill", "paper_faithful_erskill_reimplementation", erskill),
    ):
        result = subprocess.run(
            [
                sys.executable, str(REPO / "experiments/spec_v03_freeze_skill_evidence.py"),
                "--system-id", system_id, "--implementation", implementation,
                "--artifact-revision", "fixture-v1", "--training-split", "D_skill",
                "--producer-repository", "https://example.test/skill-system.git",
                "--producer-commit", "a" * 40,
                "--records", str(records), "--output", str(output),
            ],
            cwd=REPO, capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stderr

    protocol = tmp_path / "protocol.json"
    protocol_value = json.loads((REPO / "protocol/controlled_memory_protocol.example.json").read_text())
    protocol_value["systems"]["obsolete-system"] = {"must_be_removed": True}
    protocol.write_text(json.dumps(protocol_value), encoding="utf-8")
    mem0 = tmp_path / "mem0.json"
    mem0.write_text(json.dumps({"llm": {"config": {"openai_base_url": "placeholder"}}}), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable, str(REPO / "experiments/spec_v03_configure_industry_runtime.py"),
            "--protocol", str(protocol), "--mem0-config", str(mem0),
            "--memskill-artifact", str(memskill), "--erskill-artifact", str(erskill),
            "--usage-root", str(tmp_path / "usage"),
        ],
        cwd=REPO, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    configured = json.loads(protocol.read_text())
    assert set(configured["systems"]) == {"memskill", "erskill", "mem0"}
    assert configured["systems"]["memskill"]["artifact_sha256"] == hashlib.sha256(memskill.read_bytes()).hexdigest()
    assert configured["systems"]["erskill"]["artifact_sha256"] == hashlib.sha256(erskill.read_bytes()).hexdigest()
    assert configured["systems"]["mem0"]["backend_usage"]["mode"] == "enforcing_proxy_receipt"
    assert configured["systems"]["mem0"]["config_path"] == str(mem0.resolve())


def test_freeze_skill_evidence_rejects_evaluator_fields(tmp_path: Path) -> None:
    record = _record()
    record["evidence"] = [{"ground_truth": "hidden"}]
    records = tmp_path / "records.json"
    records.write_text(json.dumps([record]), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable, str(REPO / "experiments/spec_v03_freeze_skill_evidence.py"),
            "--system-id", "erskill",
            "--implementation", "paper_faithful_erskill_reimplementation",
            "--artifact-revision", "fixture-v1", "--training-split", "D_skill",
            "--producer-repository", "https://example.test/erskill.git",
            "--producer-commit", "b" * 40,
            "--records", str(records), "--output", str(tmp_path / "artifact.json"),
        ],
        cwd=REPO, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "evaluator-only" in result.stderr


def test_export_skill_inputs_contains_only_runtime_decisions(tmp_path: Path) -> None:
    bundle = _bundle()
    runtime_cases = tmp_path / "runtime_cases.json"
    runtime_cases.write_text(json.dumps([serialize(
        case_id=bundle.case_id,
        source_dataset_id=bundle.source_dataset_id,
        source_episode_id=bundle.source_episode_id,
        family_id=bundle.family_id,
        lineage_id=bundle.lineage_id,
        source_event_ids=bundle.source_event_ids,
        decision_view=bundle.decision_view,
        memory_state=bundle.memory_state,
    )]), encoding="utf-8")
    event_order = tmp_path / "event_order.json"
    order = _order()
    event_order.write_text(json.dumps({
        "seed": order.seed,
        "schedule": order.schedule,
        "rows": [asdict(row) for row in order.rows],
        "content_sha256": order.source_content_sha256,
    }), encoding="utf-8")
    split = tmp_path / "split.json"
    split.write_text(json.dumps({"assignments": {bundle.case_id: "T_final"}}), encoding="utf-8")
    output = tmp_path / "inputs.json"
    result = subprocess.run(
        [
            sys.executable, str(REPO / "experiments/spec_v03_export_skill_competitor_inputs.py"),
            "--runtime-cases", str(runtime_cases), "--event-order", str(event_order),
            "--split-manifest", str(split), "--include-split", "T_final",
            "--output", str(output),
        ],
        cwd=REPO, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text())
    assert payload["split_audit"]["family_overlap_count"] == 0
    assert payload["records"][0]["case_id"] == bundle.case_id
    serialized = json.dumps(payload).casefold()
    assert "legal_operator_ids" not in serialized
    assert "ground_truth" not in serialized


def _git_repository(path: Path) -> str:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "fixture@example.test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Fixture"], check=True)
    marker = path / "marker.txt"
    marker.write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "marker.txt"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def test_configure_industry_adapters_pins_both_repositories(tmp_path: Path) -> None:
    cmd = tmp_path / "cmd"
    mem0 = tmp_path / "mem0"
    cmd_commit = _git_repository(cmd)
    mem0_commit = _git_repository(mem0)
    (cmd / "wrappers").mkdir()
    for name in ("memskill_adapter.py", "erskill_adapter.py", "mem0_adapter.py"):
        (cmd / "wrappers" / name).write_text("# fixture\n", encoding="utf-8")
    protocol = tmp_path / "protocol.json"
    protocol.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "adapters.json"
    result = subprocess.run(
        [
            sys.executable, str(REPO / "experiments/spec_v03_configure_industry_adapters.py"),
            "--output", str(output), "--protocol", str(protocol),
            "--cmd-repository", str(cmd), "--cmd-python", sys.executable,
            "--mem0-repository", str(mem0), "--mem0-python", sys.executable,
        ],
        cwd=REPO, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    configured = json.loads(output.read_text())
    assert configured["memskill"]["pinned_commit"] == cmd_commit
    assert configured["erskill"]["pinned_commit"] == cmd_commit
    assert configured["mem0"]["pinned_commit"] == mem0_commit
    assert configured["memskill"]["command"][1].endswith("wrappers/memskill_adapter.py")


def test_legacy_cleanup_is_dry_run_by_default_and_requires_confirmation(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    legacy = run_root / "industry_smoke_v7"
    legacy.mkdir(parents=True)
    (legacy / "report.json").write_text('{"system_id":"lycheemem"}\n', encoding="utf-8")
    script = REPO / "experiments/cleanup_spec_v03_legacy_industry_results.sh"
    dry = subprocess.run(
        [str(script), "--run-root", str(run_root)],
        cwd=REPO, capture_output=True, text=True, check=False,
    )
    assert dry.returncode == 0
    assert "[DRY RUN]" in dry.stdout
    assert legacy.exists()
    rejected = subprocess.run(
        [str(script), "--run-root", str(run_root), "--execute"],
        cwd=REPO, capture_output=True, text=True, check=False,
    )
    assert rejected.returncode == 2
    assert legacy.exists()
    executed = subprocess.run(
        [
            str(script), "--run-root", str(run_root), "--execute",
            "--confirm", "DELETE_LEGACY_INDUSTRY_RESULTS",
        ],
        cwd=REPO, capture_output=True, text=True, check=False,
    )
    assert executed.returncode == 0, executed.stderr
    assert not legacy.exists()
