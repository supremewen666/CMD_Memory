import pytest

from cmd_audit.counterfactual.program_ir import ActionKind
from cmd_audit.counterfactual.successor_program_ir import (
    IR_GRAMMAR_VERSION,
    Action,
    If,
    Predicate,
    PredicateKind,
    ProgramParseError,
    ProgramBoundsError,
    canonicalize,
    parse_program,
    program_to_mapping,
)


def test_v3_has_a_distinct_semantic_actionability_version() -> None:
    assert IR_GRAMMAR_VERSION == "route-a-ir-v3-semantic-actionability"


def test_divergent_member_allows_annotation_round_trip() -> None:
    program = If(Predicate(PredicateKind.DIVERGENT_PAIR_MEMBER), Action(ActionKind.ANNOTATE_CONFLICT))
    assert parse_program(program_to_mapping(program)) == program


def test_verify_is_registered_and_non_destructive() -> None:
    program = parse_program({
        "node": "if",
        "predicate": {"kind": "divergent_pair_member"},
        "action": {"kind": "verify"},
    })
    assert isinstance(program, If)


@pytest.mark.parametrize("action", ["demote", "suppress", "replace"])
def test_divergent_member_cannot_drive_destructive_action(action: str) -> None:
    with pytest.raises(ProgramParseError):
        parse_program({"node": "if", "predicate": {"kind": "divergent_pair_member"}, "action": {"kind": action}})


def test_superseded_member_can_drive_destructive_action() -> None:
    program = parse_program({"node": "if", "predicate": {"kind": "superseded_item"}, "action": {"kind": "demote"}})
    assert isinstance(program, If)


def test_superseded_member_supports_exact_graph_bound_target_round_trip() -> None:
    program = parse_program({
        "node": "if",
        "predicate": {
            "kind": "superseded_item",
            "relation_edge_id": "e" * 64,
            "target_item_id": "old",
        },
        "action": {"kind": "demote"},
    })

    assert program_to_mapping(program)["predicate"] == {
        "kind": "superseded_item",
        "relation_edge_id": "e" * 64,
        "target_item_id": "old",
    }


def test_exact_target_binding_is_all_or_nothing_and_leaf_only() -> None:
    with pytest.raises(ProgramParseError):
        parse_program({
            "node": "if",
            "predicate": {
                "kind": "superseded_item",
                "target_item_id": "old",
            },
            "action": {"kind": "demote"},
        })
    with pytest.raises(ProgramParseError):
        parse_program({
            "node": "if",
            "predicate": {
                "kind": "and",
                "relation_edge_id": "e" * 64,
                "target_item_id": "old",
                "operands": [
                    {"kind": "superseded_item"},
                    {"kind": "divergent_pair_member"},
                ],
            },
            "action": {"kind": "verify"},
        })


def test_connective_cannot_hide_divergent_member_under_destructive_action() -> None:
    with pytest.raises(ProgramParseError):
        parse_program({"node": "if", "predicate": {"kind": "or", "operands": [{"kind": "divergent_pair_member"}, {"kind": "superseded_item"}]}, "action": {"kind": "suppress"}})


def test_connective_cannot_refine_superseded_destructive_target() -> None:
    with pytest.raises(ProgramParseError):
        parse_program({
            "node": "if",
            "predicate": {"kind": "and", "operands": [
                {"kind": "superseded_item"}, {"kind": "divergent_pair_member"}
            ]},
            "action": {"kind": "suppress"},
        })


def test_v1_leaf_is_rejected_even_for_safe_action() -> None:
    with pytest.raises(ProgramParseError):
        parse_program({"node": "if", "predicate": {"kind": "query_relevant"}, "action": {"kind": "verify"}})


def test_unimplemented_v1_action_is_rejected_statically() -> None:
    with pytest.raises(ProgramParseError):
        parse_program({
            "node": "if",
            "predicate": {"kind": "superseded_item"},
            "action": {"kind": "retrieve_fill"},
        })


def test_free_form_item_literal_is_rejected() -> None:
    with pytest.raises(ProgramParseError):
        parse_program({"node": "if", "predicate": {"kind": "superseded_item", "item_id": "old"}, "action": {"kind": "demote"}})


def test_nested_sequence_flattens_and_overdeep_predicate_is_rejected() -> None:
    nested = parse_program({"node": "sequence", "body": [
        {"node": "sequence", "body": [{
            "node": "if",
            "predicate": {"kind": "divergent_pair_member"},
            "action": {"kind": "annotate_conflict"},
        }]}
    ]})
    assert program_to_mapping(nested)["body"][0]["node"] == "sequence"
    assert program_to_mapping(canonicalize(nested))["node"] == "if"

    predicate: dict[str, object] = {"kind": "superseded_item"}
    for _ in range(4):
        predicate = {"kind": "not", "operands": [predicate]}
    with pytest.raises(ProgramBoundsError):
        parse_program({
            "node": "if",
            "predicate": predicate,
            "action": {"kind": "verify"},
        })
