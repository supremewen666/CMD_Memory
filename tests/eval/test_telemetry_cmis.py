from __future__ import annotations

from dataclasses import dataclass

import pytest

from cmd_audit.eval.telemetry_cmis import (
    GAP_SCHEMA_VERSION,
    ProxyRow,
    TelemetryChannels,
    build_proxy_rows,
    measure_telemetry_cmis_gap,
    replay_cmis,
    telemetry_cmis_proxy,
)


@dataclass(frozen=True)
class _Intent:
    intent_id: str
    effect: str


@dataclass
class _Outcome:
    valid: bool = True
    rolled_back: bool = False
    changed_item_count: int = 0
    locality_cost: float = 0.0
    _recovery_gain: float = 0.0

    @property
    def recovery_gain(self) -> float:
        return self._recovery_gain


class _ShadowTrap:
    """Outcome whose reference channel explodes when the proxy touches it."""

    valid = True
    rolled_back = False
    changed_item_count = 1
    locality_cost = 0.1

    @property
    def recovery_gain(self) -> float:
        raise AssertionError("proxy read the reference channel")


def test_proxy_never_reads_the_reference_channel() -> None:
    channels = TelemetryChannels.from_outcome(_ShadowTrap())
    assert telemetry_cmis_proxy("replace", channels) == pytest.approx(0.85)


def test_proxy_separates_executed_from_noop_by_effect_type() -> None:
    executed = TelemetryChannels(
        valid=True, rolled_back=False, changed_item_count=1, locality_cost=0.0
    )
    untouched = TelemetryChannels(
        valid=True, rolled_back=False, changed_item_count=0, locality_cost=0.0
    )

    # A mutating effect must move an item; a no-op effect must not.
    assert telemetry_cmis_proxy("replace", executed) == pytest.approx(0.95)
    assert telemetry_cmis_proxy("replace", untouched) == 0.0
    assert telemetry_cmis_proxy("verify", untouched) == 1.0
    assert telemetry_cmis_proxy("verify", executed) == pytest.approx(-0.05)


def test_guard_failure_and_rollback_floor_the_proxy() -> None:
    invalid = TelemetryChannels(
        valid=False, rolled_back=False, changed_item_count=1, locality_cost=0.0
    )
    rolled_back = TelemetryChannels(
        valid=True, rolled_back=True, changed_item_count=1, locality_cost=0.0
    )
    assert telemetry_cmis_proxy("replace", invalid) == -1.0
    assert telemetry_cmis_proxy("demote", rolled_back) == -1.0


def test_proxy_penalizes_collateral_damage() -> None:
    focused = TelemetryChannels(
        valid=True, rolled_back=False, changed_item_count=1, locality_cost=0.0
    )
    sprawling = TelemetryChannels(
        valid=True, rolled_back=False, changed_item_count=8, locality_cost=0.4
    )
    assert telemetry_cmis_proxy("replace", focused) > telemetry_cmis_proxy(
        "replace", sprawling
    )


def test_replay_cmis_matches_memaudit_equation_7() -> None:
    # CMIS(m_i) = h(q*, y*) - h(q*, f(q*, R(q*, M \ {m_i})))
    assert replay_cmis(harm_before=0.9, harm_after=0.1) == pytest.approx(0.8)
    assert replay_cmis(harm_before=0.5, harm_after=0.5) == 0.0
    with pytest.raises(ValueError, match="finite"):
        replay_cmis(harm_before=float("inf"), harm_after=0.0)


def test_channels_reject_malformed_telemetry() -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        TelemetryChannels(
            valid=True, rolled_back=False, changed_item_count=-1, locality_cost=0.0
        )
    with pytest.raises(ValueError, match="locality_cost must be non-negative"):
        TelemetryChannels(
            valid=True, rolled_back=False, changed_item_count=0, locality_cost=-0.5
        )
    with pytest.raises(ValueError, match="booleans"):
        TelemetryChannels(
            valid=1, rolled_back=False, changed_item_count=0, locality_cost=0.0
        )


def test_build_proxy_rows_pairs_each_intent_with_its_outcome() -> None:
    intents = (_Intent("i1", "replace"), _Intent("i2", "verify"))
    outcomes = {
        "i1": _Outcome(changed_item_count=1, _recovery_gain=0.4),
        "i2": _Outcome(changed_item_count=0, _recovery_gain=0.1),
    }

    rows = build_proxy_rows(
        intents, outcomes, reference=lambda outcome: outcome.recovery_gain
    )

    assert [row.intent_id for row in rows] == ["i1", "i2"]
    assert [row.reference_score for row in rows] == [0.4, 0.1]
    assert all(row.model_calls == 0 for row in rows)

    with pytest.raises(ValueError, match="no outcome for intent"):
        build_proxy_rows(
            (_Intent("missing", "replace"),), {}, reference=lambda o: 0.0
        )


def test_gap_reports_perfect_agreement_when_orderings_match() -> None:
    rows = (
        ProxyRow("i1", "replace", proxy_score=0.9, reference_score=0.5),
        ProxyRow("i2", "replace", proxy_score=0.5, reference_score=0.3),
        ProxyRow("i3", "replace", proxy_score=0.1, reference_score=0.1),
    )

    report = measure_telemetry_cmis_gap({"case_1": rows})

    assert report["schema_version"] == GAP_SCHEMA_VERSION
    assert report["model_calls"] == 0
    assert report["proxy_reads_reference_channel"] is False
    assert report["within_group_pairwise_concordance"] == 1.0
    assert report["global_spearman"] == pytest.approx(1.0)
    assert report["replay_calls_avoided"] == 3
    # Scale gap is nonzero even when the ranking is perfect — that separation is
    # the point of reporting both.
    assert report["mean_absolute_gap"] > 0.0


def test_gap_detects_inverted_ordering() -> None:
    rows = (
        ProxyRow("i1", "replace", proxy_score=0.9, reference_score=0.1),
        ProxyRow("i2", "replace", proxy_score=0.1, reference_score=0.9),
    )

    report = measure_telemetry_cmis_gap({"case_1": rows})

    assert report["within_group_pairwise_concordance"] == 0.0
    assert report["global_spearman"] == pytest.approx(-1.0)


def test_gap_aggregates_across_groups_and_ignores_indifferent_pairs() -> None:
    report = measure_telemetry_cmis_gap(
        {
            "case_1": (
                ProxyRow("a", "replace", proxy_score=1.0, reference_score=0.6),
                ProxyRow("b", "replace", proxy_score=0.2, reference_score=0.2),
            ),
            # Proxy is indifferent here, so neither pair is comparable and the
            # group cannot inflate or deflate concordance.
            "case_2": (
                ProxyRow("c", "verify", proxy_score=0.5, reference_score=0.9),
                ProxyRow("d", "verify", proxy_score=0.5, reference_score=0.1),
            ),
        }
    )

    assert report["group_count"] == 2
    assert report["candidate_count"] == 4
    assert report["comparable_pair_count"] == 1
    assert report["within_group_pairwise_concordance"] == 1.0


def test_spearman_handles_ties_without_collapsing() -> None:
    # Telemetry takes few distinct values, so ties are the common case; a
    # tie-blind rank correlation would report 0.0 here.
    rows = (
        ProxyRow("a", "replace", proxy_score=0.95, reference_score=0.5),
        ProxyRow("b", "replace", proxy_score=0.95, reference_score=0.4),
        ProxyRow("c", "replace", proxy_score=-1.0, reference_score=0.0),
    )

    report = measure_telemetry_cmis_gap({"case_1": rows})

    assert report["global_spearman"] > 0.7
    assert report["mean_within_group_spearman"] > 0.7


def test_gap_requires_data() -> None:
    with pytest.raises(ValueError, match="at least one group"):
        measure_telemetry_cmis_gap({})
    with pytest.raises(ValueError, match="at least one candidate row"):
        measure_telemetry_cmis_gap({"empty": ()})
