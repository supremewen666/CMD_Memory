from __future__ import annotations

import json

from cmd_audit.spec_v03.order_only import CaseOrderMetadata, compile_phase_labelled_recurring_order
from cmd_audit.spec_v03.prequential_executor import RuntimeOrderManifest
from experiments.spec_v03_family_bootstrap import bootstrap_family_means


def _row(case: str, incident: str) -> CaseOrderMetadata:
    return CaseOrderMetadata(case, "family:" + case, "episode:" + case, "fixture", incident)


def test_phase_labelled_recurring_order_has_two_switches() -> None:
    order = compile_phase_labelled_recurring_order(
        (
            _row("p1", "process_fault"),
            _row("p2", "process_fault"),
            _row("p3", "process_fault"),
            _row("s1", "state_drift"),
            _row("x1", "poison"),
            _row("c1", "clean"),
        ),
        seed=7,
    )
    phases = [row.regime for row in order.rows]
    assert phases[0] == "recurring_a_stationary"
    assert "recurring_b_abrupt" in phases
    assert phases[-1] == "recurring_a_return_stationary"
    assert sum(left != right for left, right in zip(phases, phases[1:])) == 2
    disk_mapping = json.loads(json.dumps(order.to_mapping()))
    RuntimeOrderManifest.from_mapping(disk_mapping).verify()


def test_recurring_order_rejects_missing_a_return() -> None:
    try:
        compile_phase_labelled_recurring_order(
            (_row("p1", "process_fault"), _row("x1", "poison")), seed=7
        )
    except ValueError as error:
        assert "two process cases" in str(error)
    else:
        raise AssertionError("expected phase compiler to reject a missing A return")


def test_family_bootstrap_keeps_family_rows_together() -> None:
    result = bootstrap_family_means(
        {"a": (1.0, 1.0), "b": (-1.0,), "c": (0.5, 0.5, 0.5)},
        iterations=500,
        seed=3,
    )
    assert result["family_count"] == 3
    assert result["paired_event_count"] == 6
    assert result["family_macro_mean"] == (1.0 - 1.0 + 0.5) / 3
