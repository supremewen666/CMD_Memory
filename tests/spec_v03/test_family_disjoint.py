from __future__ import annotations

from dataclasses import replace
import json

import pytest

from cmd_audit.spec_v03.contracts import canonical_sha256
from cmd_audit.spec_v03.family_disjoint import select_runtime_splits
from cmd_audit.spec_v03.prequential_executor import RuntimeOrderManifest, RuntimeOrderRow
from tests.spec_v03.test_stage59_runner import _bundle


def _order(case_ids: list[str]) -> RuntimeOrderManifest:
    rows = tuple(
        RuntimeOrderRow(case_id, index, "stationary", index + 2, "benign")
        for index, case_id in enumerate(case_ids)
    )
    body = {"seed": 7, "schedule": "stationary", "rows": [row.__dict__ for row in rows]}
    return RuntimeOrderManifest(7, "stationary", rows, canonical_sha256(body))


def _case(case_id: str, family_id: str):
    bundle = _bundle()
    decision = replace(bundle.decision_view, case_id=case_id, family_id=family_id)
    return replace(bundle, case_id=case_id, family_id=family_id, decision_view=decision)


def test_select_runtime_splits_reindexes_order_and_preserves_delay(tmp_path) -> None:
    first = _case("case-a", "family-a")
    second = _case("case-b", "family-b")
    manifest = tmp_path / "split.json"
    manifest.write_text(json.dumps({
        "assignments": {"case-a": "D_router", "case-b": "T_final"},
    }), encoding="utf-8")

    bundles, order, audit = select_runtime_splits(
        (first, second), _order(["case-b", "case-a"]), manifest, ("D_router",),
    )

    assert [bundle.case_id for bundle in bundles] == ["case-a"]
    assert [row.case_id for row in order.rows] == ["case-a"]
    assert order.rows[0].event_index == 0
    assert order.rows[0].receipt_matures_at == 2
    assert audit["family_overlap_count"] == 0


def test_select_runtime_splits_rejects_family_leakage(tmp_path) -> None:
    first = _case("case-a", "family-a")
    second = _case("case-b", "family-a")
    manifest = tmp_path / "split.json"
    manifest.write_text(json.dumps({
        "assignments": {"case-a": "D_router", "case-b": "T_final"},
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="leaks a family"):
        select_runtime_splits(
            (first, second), _order(["case-a", "case-b"]), manifest, ("D_router",),
        )
