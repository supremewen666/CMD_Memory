from cmd_audit.core.models import MemoryItem
from cmd_audit.item_gate.bucketing import bucket_memory_items
from cmd_audit.item_gate.freshness import arbitrate_freshness
from cmd_audit.repair.failure_memory import memory_fingerprint_for_items


def _item(memory_id: str, text: str, timestamp: str) -> MemoryItem:
    return MemoryItem(memory_id=memory_id, text=text, store=timestamp)


def test_buckets_never_exceed_pairwise_cost_cap() -> None:
    items = tuple(
        _item(
            f"m{index}",
            f"The user's current city preference is Shanghai version {index}.",
            f"2026-07-{index + 1:02d}T00:00:00Z",
        )
        for index in range(12)
    )

    buckets = bucket_memory_items(
        items,
        max_bucket_size=5,
        similarity_threshold=0.1,
    )

    assert sum(len(bucket.items) for bucket in buckets) == 12
    assert max(len(bucket.items) for bucket in buckets) <= 5


def test_freshness_arbitration_demotes_old_timestamp() -> None:
    items = (
        _item("old", "The user lives in Paris.", "2026-01-01T00:00:00Z"),
        _item("new", "The user lives in Shanghai.", "2026-03-01T00:00:00Z"),
    )

    decision = arbitrate_freshness(items)

    assert decision.applicable
    assert decision.kept_ids == ("new",)
    assert decision.demoted_ids == ("old",)
    assert decision.hints() == {"new": 1.0, "old": -1.0}


def test_hybrid_fingerprint_preserves_content_and_adds_item_shape() -> None:
    items = (
        _item("old", "The user lives in a city.", "2026-01-01T00:00:00Z"),
        _item("new", "The user lives in a city.", "2026-03-01T00:00:00Z"),
    )

    content = memory_fingerprint_for_items(items, fingerprint_mode="content")
    hybrid = memory_fingerprint_for_items(items, fingerprint_mode="hybrid")

    assert content in hybrid
    assert "ts:months_plus" in hybrid
    assert "count:2" in hybrid


def test_invalid_fingerprint_mode_is_rejected() -> None:
    try:
        memory_fingerprint_for_items((), fingerprint_mode="query")
    except ValueError as exc:
        assert "fingerprint_mode" in str(exc)
    else:
        raise AssertionError("invalid mode was accepted")
