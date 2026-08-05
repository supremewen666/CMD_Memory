from __future__ import annotations

import pytest

from cmd_audit.eval.runtime_fields import (
    RuntimeFieldPolicy,
    RuntimeFieldRecord,
    assert_no_forbidden_runtime_fields,
    structural_runtime_field_policy,
)


def test_structural_policy_is_content_addressed_and_runtime_only() -> None:
    first = structural_runtime_field_policy(extractor_version="v1")
    second = structural_runtime_field_policy(extractor_version="v1")

    assert first.sha256 == second.sha256
    assert first.allowlist == ("memory_id", "query", "store", "text")
    first.validate_declared_fields(("query", "text"))


@pytest.mark.parametrize(
    "field_name",
    (
        "perturbation_label",
        "gold_answer",
        "oracle_skill_id",
        "shadow_gold_gain",
        "safety_filter_blocked",
        "passed_safety_filter",
        "family_id",
    ),
)
def test_forbidden_runtime_fields_fail_closed(field_name: str) -> None:
    with pytest.raises(ValueError, match="forbidden runtime fields"):
        assert_no_forbidden_runtime_fields((field_name,))


def test_injection_control_cannot_enter_runtime_policy() -> None:
    with pytest.raises(ValueError, match="runtime-ineligible"):
        RuntimeFieldPolicy(
            (
                RuntimeFieldRecord(
                    field_name="safe_feature",
                    origin_component="fault_injector",
                    provenance_role="injection_control",
                    available_in_deployment=True,
                    extractor_version="v1",
                ),
            )
        )
