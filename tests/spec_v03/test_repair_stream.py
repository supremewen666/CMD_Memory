from __future__ import annotations

import json
from pathlib import Path

import pytest

from cmd_audit.spec_v03.contracts import deserialize_decision_view
from cmd_audit.spec_v03.event_order import compile_event_order
from cmd_audit.spec_v03.repair_stream import (
    ALL_TEMPLATES,
    build_intervention,
    build_shadow_matrix,
    clean_memory_state,
    compile_repair_case,
    execute_copy_on_write,
    iter_public_episodes,
    operator_catalog,
    supported_templates,
    normalize_memfail,
)
import cmd_audit.spec_v03.repair_stream as repair_stream
from cmd_audit.spec_v03.splits import build_split_manifest
from experiments.spec_v03_compile_pilot import main


ROOT = Path("data/external/group_a")


@pytest.mark.parametrize("source", ("locomo", "halumem", "memfail", "memtracebench"))
def test_real_source_normalizers_keep_events_queries_and_source_hashes(source: str) -> None:
    episode = next(iter_public_episodes(source, ROOT))

    assert episode.immutable_events
    assert episode.sealed_queries
    assert len(episode.source_sha256) == 64
    assert all(event.payload_sha256 for event in episode.immutable_events)
    assert all(query.answer is not None for query in episode.sealed_queries)
    # Query answer/evidence are a separate sealed namespace, even when a
    # source dialogue independently contains the same factual words.
    public = episode.public_mapping()
    assert "sealed_queries" not in public
    assert "answer" not in public


def test_halu_source_supports_all_required_incident_families_and_is_deterministic() -> None:
    episode = next(iter_public_episodes("halumem", ROOT))
    capability = supported_templates(episode)
    assert all(capability[template] == "supported" for template in ALL_TEMPLATES)

    first = build_intervention(episode, "explicit_supersede", seed=17)
    second = build_intervention(episode, "explicit_supersede", seed=17)
    assert first == second
    assert first.before_root == clean_memory_state(episode).root
    assert first.after_root != first.before_root


def test_memfail_all_five_csv_families_preserve_real_queries() -> None:
    expected = {
        "coexisting_facts_dataset.csv": (100, 100),
        "conditional_facts_dataset_easy.csv": (100, 100),
        "conditional_facts_dataset_hard.csv": (100, 100),
        "long_hop_chains.csv": (92, 92),
        "persona_dataset.csv": (100, 300),
    }
    for name, (episode_count, query_count) in expected.items():
        episodes = list(normalize_memfail(ROOT / "MemFail" / name))
        assert len(episodes) == episode_count
        assert sum(len(episode.sealed_queries) for episode in episodes) == query_count


def test_compiled_case_has_paired_lineage_and_runtime_deserialization_rejects_sidecar_leak() -> None:
    episode = next(iter_public_episodes("halumem", ROOT))
    case = compile_repair_case(episode, build_intervention(episode, "untrusted_injection", seed=3))

    assert case.clean_events == episode.immutable_events
    assert case.intervention.after_root == case.decision_view.observation["current_state"]["state_root"]
    runtime = json.dumps(case.decision_view.to_mapping()).casefold()
    for token in ("synthetic_intervention", "template_id", "target_event_id", "expected_effect", "intervention_visible", "synthetic_event_count", "untrusted_injection"):
        assert token not in runtime
    leaked = case.decision_view.to_mapping()
    leaked["observation"] = {"gold_legal_operator_ids": list(case.evaluator_only.legal_operator_ids)}
    with pytest.raises(ValueError, match="evaluator field"):
        deserialize_decision_view(leaked)
    leaked_value = case.decision_view.to_mapping()
    leaked_value["observation"] = {"event_log": [{"authority": "untrusted", "content": "untrusted_injection"}]}
    with pytest.raises(ValueError, match="sealed value"):
        deserialize_decision_view(leaked_value)


def test_complete_shadow_matrix_records_masked_wrong_and_correct_operator_outcomes() -> None:
    episode = next(iter_public_episodes("halumem", ROOT))
    case = compile_repair_case(episode, build_intervention(episode, "drop", seed=2))
    matrix = build_shadow_matrix(case)
    rows = {row.operator_id: row for row in matrix.entries}

    assert len(rows) == len(operator_catalog())
    assert rows["process_restore"].committed
    assert not rows["process_replay_order"].legal and not rows["process_replay_order"].executed
    assert rows["poison_quarantine_audit"].mask_reason == "incident type incompatible"
    assert matrix.candidate_set_oracle == "process_restore"


def test_oracle_universes_are_explicit_and_not_aliases() -> None:
    episode = next(iter_public_episodes("halumem", ROOT))
    matrix = build_shadow_matrix(compile_repair_case(episode, build_intervention(episode, "drop", seed=2)))

    candidate = set(matrix.candidate_member_ids)
    library = set(matrix.library_member_ids)
    mechanism = set(matrix.mechanism_member_ids)
    assert candidate < library < mechanism
    assert matrix.candidate_set_oracle in candidate
    assert matrix.library_oracle in library
    assert matrix.mechanism_oracle in mechanism
    assert matrix.mechanism_oracle.startswith("oracle_transform:")
    assert matrix.evaluator_oracle_transform.action_id == matrix.mechanism_oracle
    assert matrix.evaluator_oracle_transform.root_corrected


def test_truncate_restore_is_a_local_projection_repair() -> None:
    episode = next(iter_public_episodes("halumem", ROOT))
    case = compile_repair_case(episode, build_intervention(episode, "truncate", seed=27))
    outcome = next(row for row in build_shadow_matrix(case).entries if row.operator_id == "process_restore")

    assert outcome.committed
    assert outcome.locality_cost == 1
    assert outcome.after_root == case.clean_state.root


def test_repair_preserves_logs_and_applies_mechanism_specific_state_changes() -> None:
    episode = next(iter_public_episodes("halumem", ROOT))
    process = compile_repair_case(episode, build_intervention(episode, "drop", seed=9))
    state = compile_repair_case(episode, build_intervention(episode, "explicit_supersede", seed=9))
    poison = compile_repair_case(episode, build_intervention(episode, "untrusted_injection", seed=9))

    process_result = next(row for row in build_shadow_matrix(process).entries if row.operator_id == "process_restore")
    state_result = next(row for row in build_shadow_matrix(state).entries if row.operator_id == "state_supersede_lineage")
    poison_result = next(row for row in build_shadow_matrix(poison).entries if row.operator_id == "poison_quarantine_audit")
    assert process_result.committed and process.clean_state.immutable_source_log == process.corrupt_state.immutable_source_log
    assert state_result.committed and state.corrupt_state.audit_log
    assert poison_result.committed and poison.corrupt_state.audit_log
    assert state_result.after_root != state.clean_state.root
    assert poison_result.after_root != poison.clean_state.root


def test_state_and_poison_expected_transforms_retain_audit_and_lineage() -> None:
    episode = next(iter_public_episodes("halumem", ROOT))
    for template, operator in (("explicit_supersede", "state_supersede_lineage"), ("untrusted_injection", "poison_quarantine_audit")):
        case = compile_repair_case(episode, build_intervention(episode, template, seed=10))
        spec = next(row for row in operator_catalog() if row.operator_id == operator)
        repaired = execute_copy_on_write(case, spec)
        outcome = next(row for row in build_shadow_matrix(case).entries if row.operator_id == operator)
        assert outcome.committed
        assert case.corrupt_state.audit_log  # repair does not delete audit evidence
        if template == "explicit_supersede":
            assert case.intervention.target_event_id in case.corrupt_state.projection_order
            assert repaired.supersession_edges
            assert case.intervention.target_event_id not in repaired.projection_order
        else:
            assert case.corrupt_state.audit_log[0].actor_scope == "untrusted"
            assert case.corrupt_state.audit_log[0].event_id in repaired.quarantine_set
            assert case.corrupt_state.audit_log[0].event_id not in repaired.projection_order


@pytest.mark.parametrize(
    ("template", "operator"),
    (
        ("drop", "process_restore"),
        ("explicit_supersede", "state_supersede_lineage"),
        ("untrusted_injection", "poison_quarantine_audit"),
    ),
)
def test_runtime_operator_cannot_access_sealed_intervention(template: str, operator: str) -> None:
    class ForbiddenIntervention:
        def __getattribute__(self, _name: str) -> object:
            raise AssertionError("runtime operator accessed sealed intervention")

    episode = next(iter_public_episodes("halumem", ROOT))
    case = compile_repair_case(episode, build_intervention(episode, template, seed=41))
    spec = next(item for item in operator_catalog() if item.operator_id == operator)
    before = case.corrupt_state
    object.__setattr__(case, "intervention", ForbiddenIntervention())

    after = execute_copy_on_write(case, spec)
    assert after != before


def test_audit_events_expose_domain_lineage_or_authority_not_constructor_labels() -> None:
    episode = next(iter_public_episodes("halumem", ROOT))
    state = compile_repair_case(episode, build_intervention(episode, "explicit_supersede", seed=43))
    poison = compile_repair_case(episode, build_intervention(episode, "untrusted_injection", seed=43))

    forbidden = {"synthetic_intervention", "kind", "template_id", "target_event_id", "expected_effect"}
    assert forbidden.isdisjoint(state.corrupt_state.audit_log[0].payload)
    assert forbidden.isdisjoint(poison.corrupt_state.audit_log[0].payload)
    assert "supersedes_source_ref" in state.corrupt_state.audit_log[0].payload
    assert poison.corrupt_state.audit_log[0].actor_scope == "untrusted"


@pytest.mark.parametrize(
    ("template", "operator"),
    (
        ("drop", "process_restore"),
        ("duplicate", "process_restore"),
        ("truncate", "process_restore"),
        ("reorder", "process_replay_order"),
        ("wrong_index", "process_rebuild_index"),
        ("wrong_scope", "process_scope_repair"),
        ("stale_cache", "process_cache_invalidate"),
    ),
)
def test_process_operators_are_local_typed_transforms(template: str, operator: str, monkeypatch: pytest.MonkeyPatch) -> None:
    episode = next(iter_public_episodes("halumem", ROOT))
    case = compile_repair_case(episode, build_intervention(episode, template, seed=37))
    spec = next(item for item in operator_catalog() if item.operator_id == operator)
    before = case.corrupt_state

    def evaluator_must_not_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("runtime transform must not read evaluator expected state")

    monkeypatch.setattr(repair_stream, "expected_repaired_state", evaluator_must_not_run)
    after = execute_copy_on_write(case, spec)
    assert after.immutable_source_log == before.immutable_source_log
    assert after.audit_log == before.audit_log
    assert after.supersession_edges == before.supersession_edges
    assert after.quarantine_set == before.quarantine_set
    if template == "wrong_index":
        assert before.projection_order == after.projection_order
        assert before.projection_index != after.projection_index
        assert after.projection_index == tuple((event_id, index) for index, event_id in enumerate(after.projection_order))
    elif template == "wrong_scope":
        assert before.projection_order == after.projection_order
        assert before.projection_index == after.projection_index
        assert before.cache_event_ids == after.cache_event_ids
        assert sum(left != right for left, right in zip(before.scope_projection, after.scope_projection)) == 1
    elif template == "stale_cache":
        assert before.projection_order == after.projection_order
        assert before.projection_index == after.projection_index
        assert before.scope_projection == after.scope_projection
        assert before.cache_event_ids != after.cache_event_ids
    else:
        assert before.scope_projection == after.scope_projection
        assert before.cache_event_ids == after.cache_event_ids
        assert before.projection_order != after.projection_order


def test_event_orders_are_seeded_outcome_independent_and_cover_regimes() -> None:
    episode = next(iter_public_episodes("halumem", ROOT))
    cases = [compile_repair_case(episode, build_intervention(episode, template, seed=5)) for template in ("drop", "explicit_supersede", "untrusted_injection", "clean")]
    stationary = compile_event_order(cases, seed=19, schedule="stationary")
    abrupt = compile_event_order(cases, seed=19, schedule="abrupt_process_state_poison")
    recurring = compile_event_order(cases, seed=19, schedule="recurring_a_b_a")

    assert abrupt == compile_event_order(cases, seed=19, schedule="abrupt_process_state_poison")
    for manifest in (stationary, abrupt, recurring):
        assert [row.event_index for row in manifest.rows] == list(range(len(cases)))
        assert sorted(row.case_id for row in manifest.rows) == sorted(case.case_id for case in cases)
    assert all(row.receipt_matures_at > row.event_index for row in recurring.rows)
    assert {row.cas_interleaving for row in abrupt.rows} == {"benign", "conflicting"}


def test_template_and_lineage_blocks_cannot_leak_across_splits() -> None:
    episode = next(iter_public_episodes("halumem", ROOT))
    cases = [compile_repair_case(episode, build_intervention(episode, template, seed=23)) for template in ("clean", "drop", "untrusted_injection")]
    blocks = {case.case_id: (f"template:{case.intervention.template_id}", f"trigger:{case.intervention.expected_effect.get('trigger')}") for case in cases}
    split = build_split_manifest([case.decision_view for case in cases], seed=23, extra_block_keys=blocks)

    assert set(split.split_case_ids) == {"D_skill", "D_router", "D_cal", "D_lifecycle", "T_online", "T_anchor", "T_final"}
    assert len({split.assignments[case.case_id] for case in cases}) == 1


def test_twenty_real_episodes_can_occupy_multiple_split_components() -> None:
    episodes = []
    for episode in iter_public_episodes("halumem", ROOT):
        episodes.append(episode)
        if len(episodes) == 20:
            break
    partitions = ("D_skill", "D_router", "D_cal", "D_lifecycle", "T_online", "T_anchor", "T_final")
    templates = ("drop", "duplicate", "reorder", "truncate", "wrong_index", "wrong_scope", "stale_cache")
    cases = []
    forced = {}
    blocks = {}
    for index, episode in enumerate(episodes):
        assigned, template = partitions[index % 7], templates[index % 7]
        for value in ("clean", template):
            case = compile_repair_case(episode, build_intervention(episode, value, seed=29))
            cases.append(case)
            forced[case.case_id] = assigned
            if value != "clean":
                blocks[case.case_id] = (f"template:{value}", f"trigger:{value}")
    split = build_split_manifest([case.decision_view for case in cases], seed=29, extra_block_keys=blocks, forced_assignments=forced)
    assert len(set(split.assignments.values())) > 1
    assert len(set(split.assignments.values())) == 7
    for template in templates:
        assigned = {split.assignments[case.case_id] for case in cases if case.intervention.template_id == template}
        assert len(assigned) == 1


def test_pilot_cli_writes_sealed_sidecar_and_development_only_status(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "pilot"
    assert main(["--source", "halumem", "--limit", "1", "--seed", "31", "--schedule", "stationary", "--output-dir", str(output)]) == 0
    manifest = json.loads((output / "pilot_manifest.json").read_text(encoding="utf-8"))
    runtime = (output / "runtime_cases.json").read_text(encoding="utf-8")
    sidecar = (output / "sealed_evaluator_sidecar.json").read_text(encoding="utf-8")

    assert manifest["status"] == "DEVELOPMENT_PILOT_NOT_F_DATA_FROZEN"
    assert manifest["case_count"] >= 2
    assert "golden_answers" not in runtime
    assert "answer" in sidecar
    assert "status=DEVELOPMENT_PILOT_NOT_F_DATA_FROZEN" in capsys.readouterr().out
