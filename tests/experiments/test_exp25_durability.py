"""Exp25 repair durability: read-time vs write-back.

The three properties these tests protect are the ones that make the experiment
interpretable: relapse is measured strictly within a family, the write-back arm
is reported as a NET effect (so a store edit that fixes one family while
degrading others cannot look like a win), and a timed-out identity baseline
leaves the case unmeasured in every arm rather than counted as a failure.
"""

from __future__ import annotations

from types import SimpleNamespace
import csv

from cmd_audit.counterfactual.actions import PipelineAction
from experiments import analyze_significance
from experiments import run_experiment_25_repair_durability as exp25


def _item(memory_id: str, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        memory_id=memory_id,
        text=text,
        store="episodic",
        source_event_ids=(f"e-{memory_id}",),
        passed_safety_filter=True,
        is_graph_expanded=False,
    )


def _row(family: str, variant: int, **over) -> dict[str, str]:
    row = {
        "family": family,
        "family_index": "1",
        "case_id": f"{family}-v{variant}",
        "gold_label": "retrieval_error",
        "variant_index": str(variant),
        "status": "ok",
        "excluded": "false",
        "timeout_count": "0",
        "base_gain": "0.0000",
        "no_repair_recovered": "false",
        "read_time_recovered": "true",
        "read_time_net": "0.5000",
        "read_time_rollouts": "4",
        "write_back_recovered": "true",
        "write_back_net": "0.5000",
        "write_back_rollouts": "1",
        "write_back_active": "true",
        "store_rolled_back": "true",
    }
    row.update(over)
    return row


# ── candidate legality ──────────────────────────────────────────────────


def test_candidate_choices_use_structural_legality() -> None:
    items = (_item("m0", "original"),)

    choices = exp25._candidate_choices(items, 1)

    assert (0, PipelineAction.RETRIEVAL_ERROR) in choices
    assert (0, PipelineAction.SAFETY_ERROR) in choices
    assert all(action != PipelineAction.IDENTITY for _, action in choices)
    assert all(not action.is_item_level for _, action in choices)


def test_candidate_choices_exclude_gated_safety_without_metadata() -> None:
    item = _item("m0", "original")
    item.passed_safety_filter = False

    choices = exp25._candidate_choices((item,), 1)

    assert (0, PipelineAction.SAFETY_ERROR) not in choices


# ── relapse, measured within a family ───────────────────────────────────


def test_relapse_counts_only_post_repair_variants_in_the_same_family() -> None:
    rows = [
        _row("famA", 0, read_time_recovered="true"),
        _row("famA", 1, read_time_recovered="false"),  # relapse
        _row("famA", 2, read_time_recovered="true"),
        # A different family's failure must NOT count as famA relapsing.
        _row("famB", 0, read_time_recovered="false"),
        _row("famB", 1, read_time_recovered="false"),
    ]

    relapses, considered = exp25.relapse_rate(rows, "read_time")

    assert (relapses, considered) == (1, 2)


def test_relapse_ignores_variants_before_the_first_repair() -> None:
    rows = [
        _row("famA", 0, read_time_recovered="false"),
        _row("famA", 1, read_time_recovered="false"),
        _row("famA", 2, read_time_recovered="true"),
        _row("famA", 3, read_time_recovered="false"),
    ]

    relapses, considered = exp25.relapse_rate(rows, "read_time")

    assert (relapses, considered) == (1, 1)


def test_relapse_excludes_timed_out_cases() -> None:
    rows = [
        _row("famA", 0, read_time_recovered="true"),
        _row(
            "famA",
            1,
            excluded="true",
            status="base_gain_timeout",
            read_time_recovered="",
        ),
        _row("famA", 2, read_time_recovered="true"),
    ]

    relapses, considered = exp25.relapse_rate(rows, "read_time")

    assert (relapses, considered) == (0, 1)


def test_relapse_is_computed_per_arm() -> None:
    rows = [
        _row("famA", 0, read_time_recovered="true", write_back_recovered="true"),
        _row("famA", 1, read_time_recovered="true", write_back_recovered="false"),
    ]

    assert exp25.relapse_rate(rows, "read_time") == (0, 1)
    assert exp25.relapse_rate(rows, "write_back") == (1, 1)


# ── net regression ──────────────────────────────────────────────────────


def test_net_regression_reports_both_directions() -> None:
    """A write-back that helps two cases and hurts three is a NET LOSS; naming
    only the wins would misreport it."""
    rows = [
        _row("f1", 0, read_time_recovered="false", write_back_recovered="true"),
        _row("f2", 0, read_time_recovered="false", write_back_recovered="true"),
        _row("f3", 0, read_time_recovered="true", write_back_recovered="false"),
        _row("f4", 0, read_time_recovered="true", write_back_recovered="false"),
        _row("f5", 0, read_time_recovered="true", write_back_recovered="false"),
    ]

    helped, hurt = exp25.net_regression(rows)

    assert (helped, hurt) == (2, 3)
    assert helped - hurt < 0


def test_net_regression_skips_excluded_rows() -> None:
    rows = [
        _row(
            "f1",
            0,
            excluded="true",
            read_time_recovered="",
            write_back_recovered="",
        ),
        _row("f2", 0, read_time_recovered="false", write_back_recovered="true"),
    ]

    assert exp25.net_regression(rows) == (1, 0)


def test_net_regression_prefers_cross_family_sentinel_counts() -> None:
    rows = [
        _row(
            "f1",
            0,
            cross_family_helped="1",
            cross_family_hurt="2",
        ),
        _row(
            "f1",
            1,
            cross_family_helped="0",
            cross_family_hurt="0",
        ),
    ]

    assert exp25.net_regression(rows) == (1, 2)


# ── summary shape ───────────────────────────────────────────────────────


def test_summary_groups_by_variant_index_and_excludes_timeouts() -> None:
    rows = [
        _row("f1", 0),
        _row("f2", 0, read_time_recovered="false"),
        _row(
            "f3",
            0,
            excluded="true",
            status="base_gain_timeout",
            read_time_recovered="",
            write_back_recovered="",
            no_repair_recovered="",
            read_time_rollouts="0",
            write_back_rollouts="0",
        ),
        _row("f1", 1),
    ]

    summary = exp25._summary_rows(rows)
    by_variant = {row["variant_index"]: row for row in summary}

    assert by_variant["0"]["cases"] == "2"
    assert by_variant["0"]["excluded_cases"] == "1"
    assert by_variant["0"]["read_time_rate"] == "0.5000"
    assert by_variant["1"]["cases"] == "1"


def test_summary_keys_match_declared_fieldnames() -> None:
    """csv.DictWriter raises on stray keys."""
    summary = exp25._summary_rows([_row("f1", 0)])

    assert set(summary[0]) == set(exp25._summary_fieldnames())


def test_excluded_detail_row_keys_are_declared() -> None:
    row = exp25._excluded_row(
        "famA",
        1,
        SimpleNamespace(case_id="c1", perturbation_label="safety_error"),
        2,
    )

    assert set(row) <= set(exp25._detail_fieldnames())
    assert row["status"] == "base_gain_timeout"
    assert row["excluded"] == "true"
    # Every arm blank: the case is unmeasured, not failed, in all three.
    assert row["read_time_recovered"] == ""
    assert row["write_back_recovered"] == ""
    assert row["no_repair_recovered"] == ""


def test_amortized_cost_columns_average_only_measured_rows() -> None:
    rows = [
        _row("f1", 0, read_time_rollouts="4", write_back_rollouts="4"),
        _row("f1", 1, read_time_rollouts="4", write_back_rollouts="1"),
    ]

    summary = {r["variant_index"]: r for r in exp25._summary_rows(rows)}

    assert summary["0"]["avg_write_back_rollouts"] == "4.0000"
    assert summary["1"]["avg_write_back_rollouts"] == "1.0000"


def test_significance_loader_reads_exp25_wide_arms(tmp_path) -> None:
    path = tmp_path / "repair_durability_detail.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_id",
                "excluded",
                "no_repair_recovered",
                "read_time_recovered",
                "write_back_recovered",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "case_id": "c1",
                "excluded": "false",
                "no_repair_recovered": "false",
                "read_time_recovered": "true",
                "write_back_recovered": "true",
            }
        )

    paired = analyze_significance._load_exp25_paired(path)

    assert paired == {
        "c1": {
            "no_repair": False,
            "read_time": True,
            "write_back": True,
        }
    }


# ── gold-free discipline ────────────────────────────────────────────────


def test_candidate_choices_are_derived_without_gold_or_labels() -> None:
    """Repair candidates come from the action space and depth alone -- never
    from the case's gold answer or its perturbation label."""
    recall = (_item("m0", "a"), _item("m1", "b"))

    choices = exp25._candidate_choices(recall, 2)

    assert choices
    assert all(isinstance(gp, int) for gp, _ in choices)
    assert all(isinstance(a, PipelineAction) for _, a in choices)
    assert {gp for gp, _ in choices} == {0, 1}
    assert PipelineAction.IDENTITY not in {a for _, a in choices}


def test_family_key_prefers_recurrent_family_id() -> None:
    case = SimpleNamespace(
        recurrent_family_id="longmemeval-fam007",
        extracted_memory=(_item("m0", "x"),),
    )

    assert exp25._family_key(case) == "longmemeval-fam007"


def test_variant_index_falls_back_when_absent() -> None:
    case = SimpleNamespace(recurrent_variant_index=None)

    assert exp25._variant_index(case, 3) == 3
