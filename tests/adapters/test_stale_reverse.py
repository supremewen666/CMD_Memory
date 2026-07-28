from cmd_audit.adapters.stale_reverse import (
    repair_post_retrieval,
    three_dimension_accuracy,
)
from cmd_audit.core.models import MemoryItem


def test_reverse_adapter_keeps_newest_item_before_answering() -> None:
    items = (
        MemoryItem("old", "M_old", store="2026-01-01T00:00:00Z"),
        MemoryItem("new", "M_new", store="2026-03-01T00:00:00Z"),
        MemoryItem("haystack", "noise", store="haystack"),
    )

    result = repair_post_retrieval(items)

    assert result.changed
    assert [item.memory_id for item in result.repaired_items] == [
        "new",
        "haystack",
    ]


def test_three_dimension_accuracy_is_per_dimension() -> None:
    assert three_dimension_accuracy(
        ("Shanghai", "yes", "blue"),
        ("shanghai", "no", "blue"),
    ) == (1.0, 0.0, 1.0)
