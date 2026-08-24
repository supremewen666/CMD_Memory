from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from cmd_audit.core.state_codec import content_sha256
from experiments.ecc_answer_causal_contrast import (
    MEMORY_HEADING,
    OPERATOR_SEMANTICS,
    render_causal_state,
)
from experiments.model_context_budget import ModelContextBudget


def _case() -> object:
    return SimpleNamespace(
        raw={
            "extracted_memory": [
                {"memory_id": "m1", "text": "alpha detail one two three four five six"},
                {"memory_id": "m2", "text": "beta detail one two three four five six"},
            ]
        }
    )


def _state() -> dict[str, object]:
    return {
        "pipeline": {
            "retrieval": True,
            "injection": True,
            "granularity": True,
            "safety": True,
        },
        "memories": {"m1": {"active": True}, "m2": {"active": True}},
        "memory_order": ["m2", "m1"],
        "lineage": [],
        "quarantine": [],
        "protected_ids": [],
    }


def _render(state: dict[str, object], subtype: str):
    return render_causal_state(
        case=_case(),
        state=state,
        state_root=content_sha256(state, ensure_ascii=False, allow_nan=False),
        process_fault_subtype=subtype,
        query="query",
        budget=ModelContextBudget(
            max_model_len=4096,
            max_output_tokens=64,
            reserve_tokens=64,
        ),
    )


def test_same_state_has_identical_prompt_and_explicit_order() -> None:
    state = _state()
    first = _render(state, "retrieval")
    second = _render(deepcopy(state), "retrieval")
    assert first.budgeted.context == second.budgeted.context
    assert first.context_sha256 == second.context_sha256
    assert first.source_memory_order == ("m2", "m1")
    assert first.budgeted.context.index("[m2]") < first.budgeted.context.index("[m1]")
    assert first.budgeted.context.startswith(f"{MEMORY_HEADING}:\n")


@pytest.mark.parametrize(
    ("subtype", "marker", "included"),
    (
        ("retrieval", "(empty)", ()),
        ("injection", "[context-injection-missing]", ("m2", "m1")),
        ("granularity", "[coarse-granularity]", ("m2", "m1")),
        ("safety", "[evidence-withheld-by-safety-gate]", ("m2", "m1")),
    ),
)
def test_each_process_fault_has_typed_before_after_semantics(
    subtype: str,
    marker: str,
    included: tuple[str, ...],
) -> None:
    after_state = _state()
    before_state = deepcopy(after_state)
    before_state["pipeline"][subtype] = False  # type: ignore[index]
    before = _render(before_state, subtype)
    after = _render(after_state, subtype)

    assert before.operator_semantics == OPERATOR_SEMANTICS[subtype]
    assert marker in before.budgeted.context
    assert before.budgeted.included_ids == included
    assert before.budgeted.context != after.budgeted.context
    assert before.budgeted.context.splitlines()[0] == after.budgeted.context.splitlines()[0]
    assert after.budgeted.included_ids == ("m2", "m1")
    assert "beta detail" in after.budgeted.context


def test_state_root_is_required_for_answer_rendering() -> None:
    with pytest.raises(ValueError, match="bound root"):
        render_causal_state(
            case=_case(),
            state=_state(),
            state_root="stale-root",
            process_fault_subtype="retrieval",
            query="query",
            budget=ModelContextBudget(
                max_model_len=4096,
                max_output_tokens=64,
                reserve_tokens=64,
            ),
        )
