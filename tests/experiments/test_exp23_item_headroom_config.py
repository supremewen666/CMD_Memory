from __future__ import annotations

from cmd_audit.core.labels import ITEM_LABELS
from experiments.run_experiment_23_item_headroom import _allowed_item_actions


def test_allowed_item_actions_auto_narrows_stale_conflict_suite() -> None:
    assert _allowed_item_actions({"item_stale", "item_conflict"}, "auto") == {
        "item_stale",
        "item_conflict",
    }


def test_allowed_item_actions_auto_uses_full_item_space_for_mixed_suite() -> None:
    assert _allowed_item_actions({"item_stale", "item_wrong"}, "auto") == set(ITEM_LABELS)


def test_allowed_item_actions_can_force_stale_conflict() -> None:
    assert _allowed_item_actions({"item_wrong"}, "stale-conflict") == {
        "item_stale",
        "item_conflict",
    }
