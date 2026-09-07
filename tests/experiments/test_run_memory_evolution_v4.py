import hashlib
import json

from cmd_audit.repair.parametric_policy import OnlineRepairPolicy, OutcomeObservation
from experiments.run_memory_evolution_v4 import main
from tests.repair.test_neuro_symbolic_evolution import _context, _graph, _intent


def _write_stream(path) -> None:
    graph = _graph("cli-case")
    context = _context(graph, 1)
    intent = _intent(graph)
    policy = OnlineRepairPolicy()
    decision = policy.select(context, (intent,))
    outcome = OutcomeObservation(
        decision.selection_id,
        context.case_id,
        2,
        "heldout-family",
        intent.intent_id,
        0.8,
        0.01,
        1,
        True,
        False,
    )
    rows = (
        {
            "record_type": "select",
            "context": context.to_mapping(),
            "graph": graph.as_mapping(),
            "intents": [intent.to_mapping()],
        },
        {
            "record_type": "outcome",
            "selection_id": decision.selection_id,
            "observations": [outcome.to_mapping()],
        },
    )
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_cli_replays_split_events_with_zero_model_calls_and_durable_report(
    tmp_path,
) -> None:
    events = tmp_path / "events.jsonl"
    database = tmp_path / "evolution.sqlite"
    output = tmp_path / "report.json"
    _write_stream(events)

    assert main(
        [
            "--events",
            str(events),
            "--repository",
            str(database),
            "--output",
            str(output),
        ]
    ) == 0

    report = json.loads(output.read_text(encoding="utf-8"))
    claimed = report.pop("report_sha256")
    expected = hashlib.sha256(
        json.dumps(
            report,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert claimed == expected
    assert report["protocol"] == "cmd-neuro-symbolic-memory-evolution-v4"
    assert report["model_calls"] == 0
    assert report["selection_count"] == 1
    assert report["outcome_count"] == 1
    assert report["selections"][0]["compiled_programs"][0]["program"][
        "predicate"
    ]["target_item_id"] == "old"
    assert database.exists()


def test_cli_rejects_outcome_smuggled_into_selection_record(tmp_path) -> None:
    events = tmp_path / "bad.jsonl"
    graph = _graph("bad-cli")
    row = {
        "record_type": "select",
        "context": _context(graph, 1).to_mapping(),
        "graph": graph.as_mapping(),
        "intents": [_intent(graph).to_mapping()],
        "outcome": {"recovery_gain": 1.0},
    }
    events.write_text(json.dumps(row) + "\n", encoding="utf-8")

    import pytest

    with pytest.raises(ValueError, match="closed select record"):
        main(
            [
                "--events",
                str(events),
                "--repository",
                str(tmp_path / "bad.sqlite"),
                "--output",
                str(tmp_path / "bad-report.json"),
            ]
        )
