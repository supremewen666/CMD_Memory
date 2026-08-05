from __future__ import annotations

import pytest

from cmd_audit.counterfactual.actions import PipelineAction
from cmd_audit.counterfactual.operators import OperatorSpec
from cmd_audit.repair.niche_archive import (
    BehaviorDescriptor,
    NicheArchive,
    NicheValidationEvidence,
)


def _descriptor(cluster: str = "cluster-a") -> BehaviorDescriptor:
    return BehaviorDescriptor(
        cluster,
        ("recall_set_collision:high",),
        "tier2_item",
    )


def _evidence(
    case_id: str,
    family_id: str,
    case_index: int,
    gain: float,
    cost: float = 1.0,
) -> NicheValidationEvidence:
    return NicheValidationEvidence(
        case_id,
        family_id,
        case_index,
        gain,
        cost,
    )


def test_descriptor_rejects_label_metadata() -> None:
    with pytest.raises(ValueError, match="label/evaluation"):
        BehaviorDescriptor(
            "item_stale",
            ("recall_set_collision",),
            "tier2_item",
        )


def test_producing_case_cannot_validate_and_first_stable_becomes_elite() -> None:
    archive = NicheArchive(bootstrap_samples=200)
    record = archive.propose(
        _descriptor(),
        OperatorSpec.single(0, PipelineAction.ITEM_CONFLICT),
        producing_case_id="p0",
        producing_family_id="f0",
        created_after_case_index=0,
    )

    with pytest.raises(ValueError, match="producing case"):
        archive.record_validation(
            record.revision_id,
            _evidence("p0", "f0", 1, 0.8),
        )

    for index, (case_id, family_id) in enumerate(
        (("c1", "f1"), ("c2", "f2"), ("c3", "f1")),
        start=1,
    ):
        archive.record_validation(
            record.revision_id,
            _evidence(case_id, family_id, index, 0.8),
        )

    transition = archive.consider_elite(record.revision_id)
    assert transition.decision == "install_first_elite"
    elite = archive.elite(_descriptor(), case_index=4)
    assert elite is not None
    assert elite.status == "stable"
    assert elite.anchor_case_ids == ("p0", "c1", "c2", "c3")


def test_cross_niche_candidate_cannot_replace_an_elite() -> None:
    archive = NicheArchive(bootstrap_samples=200)
    left = archive.propose(
        _descriptor("left"),
        OperatorSpec.single(0, PipelineAction.ITEM_CONFLICT),
        producing_case_id="p-left",
        producing_family_id="f0",
        created_after_case_index=0,
    )
    right = archive.propose(
        _descriptor("right"),
        OperatorSpec.single(0, PipelineAction.ITEM_STALE),
        producing_case_id="p-right",
        producing_family_id="f0",
        created_after_case_index=0,
    )
    for record in (left, right):
        for index in range(1, 4):
            archive.record_validation(
                record.revision_id,
                _evidence(
                    f"{record.revision_id}:{index}",
                    f"f{index % 2}",
                    index,
                    0.8,
                ),
            )
        assert (
            archive.consider_elite(record.revision_id).decision
            == "install_first_elite"
        )

    assert archive.elite(_descriptor("left")).revision_id == left.revision_id
    assert archive.elite(_descriptor("right")).revision_id == right.revision_id


def test_replacement_requires_anchor_preservation() -> None:
    archive = NicheArchive(bootstrap_samples=200)
    incumbent = archive.propose(
        _descriptor(),
        OperatorSpec.single(0, PipelineAction.ITEM_CONFLICT),
        producing_case_id="inc-p",
        producing_family_id="f0",
        created_after_case_index=0,
    )
    for index, case_id in enumerate(("i1", "i2", "i3"), start=1):
        archive.record_validation(
            incumbent.revision_id,
            _evidence(case_id, f"f{index % 2}", index, 0.5),
        )
    archive.consider_elite(incumbent.revision_id)

    challenger = archive.propose(
        _descriptor(),
        OperatorSpec.single(0, PipelineAction.ITEM_WRONG),
        producing_case_id="chal-p",
        producing_family_id="f3",
        created_after_case_index=4,
        parent_revision_id=incumbent.revision_id,
    )
    for index, case_id in enumerate(("x1", "x2", "x3"), start=5):
        archive.record_validation(
            challenger.revision_id,
            _evidence(case_id, f"g{index % 2}", index, 0.9),
        )

    transition = archive.consider_elite(challenger.revision_id)
    assert transition.decision == "no_paired_cases"
    assert archive.elite(_descriptor()).revision_id == incumbent.revision_id
