from types import SimpleNamespace

from experiments.v4_live_materialization import _changed_item_ids


def _item(item_id: str, disposition: str = "active"):
    return SimpleNamespace(
        item_id=item_id,
        text="same text",
        source_event_ids=(),
        store="observed",
        provenance_hash=f"hash-{item_id}",
        rank=0,
        disposition=disposition,
        as_mapping=lambda: {
            "item_id": item_id,
            "text": "same text",
            "source_event_ids": [],
            "store": "observed",
            "provenance_hash": f"hash-{item_id}",
            "rank": 0,
            "disposition": disposition,
        },
    )


def test_changed_ids_are_content_hash_diff_not_trace_match_ids():
    initial = SimpleNamespace(items=(_item("m1"),), trace=())
    noop = SimpleNamespace(items=(_item("m1"),), trace=(SimpleNamespace(matched_item_ids=("m1",)),))
    changed = SimpleNamespace(items=(_item("m1", "suppressed"),), trace=())
    assert _changed_item_ids(initial, noop) == set()
    assert _changed_item_ids(initial, changed) == {"m1"}
