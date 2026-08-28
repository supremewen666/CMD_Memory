from __future__ import annotations

from pathlib import Path

import pytest

from cmd_audit.spec_v03.repair_stream import (
    build_intervention,
    build_shadow_matrix,
    compile_repair_case,
    iter_public_episodes,
    operator_catalog,
)
from cmd_audit.spec_v03.runtime_pipeline import RuntimePipeline, build_legal_candidates
from cmd_audit.spec_v03.syndrome_runtime import decode_ecc_syndrome


ROOT = Path("data/external/group_a")


def _case(template: str):
    episode = next(iter_public_episodes("halumem", ROOT))
    return compile_repair_case(episode, build_intervention(episode, template, seed=71))


@pytest.mark.parametrize(
    ("template", "family", "targeted", "general"),
    (
        ("wrong_index", "process_restore", "process_rebuild_index", "process_projection_rebuild"),
        ("explicit_supersede", "state_supersede", "state_supersede_lineage", "state_supersede_rebuild"),
        ("untrusted_injection", "poison_quarantine", "poison_quarantine_audit", "poison_quarantine_rebuild"),
    ),
)
def test_incident_profiles_offer_two_safe_distinct_executable_strategies(
    template: str,
    family: str,
    targeted: str,
    general: str,
) -> None:
    case = _case(template)
    syndrome = decode_ecc_syndrome(case.decision_view, case.corrupt_state)
    candidates = build_legal_candidates(case.corrupt_state, syndrome)
    by_id = {skill.skill_revision_id: skill for skill in RuntimePipeline().frozen_skill_library}
    candidate_programs = {
        str(by_id[revision_id].program["operator_id"]): by_id[revision_id].program
        for revision_id in candidates.skill_revision_ids
    }

    assert targeted in candidate_programs
    assert general in candidate_programs
    assert candidate_programs[targeted] != candidate_programs[general]

    specs = {spec.operator_id: spec for spec in operator_catalog()}
    assert specs[targeted].operator_family == specs[general].operator_family == family
    expected_strategies = {
        "process_restore": ("targeted", "rebuild"),
        "state_supersede": ("targeted", "cascade"),
        "poison_quarantine": ("quarantine_only", "quarantine_and_rebuild"),
    }
    assert (specs[targeted].strategy_id, specs[general].strategy_id) == expected_strategies[family]
    assert specs[targeted].read_set and specs[targeted].write_set and specs[targeted].repair_action
    assert specs[targeted].expected_cost < specs[general].expected_cost

    outcomes = {row.operator_id: row for row in build_shadow_matrix(case).entries}
    assert outcomes[targeted].committed and outcomes[general].committed
    assert outcomes[targeted].utility > outcomes[general].utility


@pytest.mark.parametrize("template", ("drop", "reorder", "wrong_scope", "stale_cache"))
def test_process_general_profile_is_legal_alongside_each_specialized_process_repair(template: str) -> None:
    case = _case(template)
    syndrome = decode_ecc_syndrome(case.decision_view, case.corrupt_state)
    candidates = build_legal_candidates(case.corrupt_state, syndrome)
    assert "process_projection_rebuild" in candidates.legal_operator_ids
    outcomes = {row.operator_id: row for row in build_shadow_matrix(case).entries}
    assert outcomes["process_projection_rebuild"].committed


def test_clean_case_still_abstains_before_strategy_candidates_are_materialized() -> None:
    case = _case("clean")
    syndrome = decode_ecc_syndrome(case.decision_view, case.corrupt_state)
    candidates = build_legal_candidates(case.corrupt_state, syndrome)

    assert syndrome.abstains
    assert candidates.skill_revision_ids == ()
    assert candidates.legal_operator_ids == ()
