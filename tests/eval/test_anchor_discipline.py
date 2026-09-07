from __future__ import annotations

import pytest

from cmd_audit.eval.anchor_discipline import (
    Anchor,
    AnchorSet,
    HeldOutAnchorReadError,
    SealedProtocol,
    SealedProtocolViolation,
)


def _anchor_set(set_id: str = "anchors_v1") -> AnchorSet:
    return AnchorSet(
        set_id=set_id,
        reference=tuple(
            Anchor(anchor_id=f"ref_{index}", payload=f"reference {index}", reference=0.5)
            for index in range(10)
        ),
        held_out=(
            Anchor(anchor_id="held_0", payload="held out zero", reference=1.0),
            Anchor(anchor_id="held_1", payload="held out one", reference=0.0),
        ),
    )


def _protocol(anchors: AnchorSet) -> SealedProtocol:
    return SealedProtocol(
        protocol_id="e1_sealed_confirmation",
        dataset_sha256="a" * 64,
        arms=("identity", "random_k", "full_v4"),
        primary_metric="family_macro_pearson",
        thresholds={"min_family_correlation": 0.2},
        seeds=(24,),
        anchor_fingerprint=anchors.fingerprint(),
    )


def test_reading_held_out_anchors_raises() -> None:
    anchors = _anchor_set()

    with pytest.raises(HeldOutAnchorReadError):
        anchors.held_out

    with pytest.raises(HeldOutAnchorReadError, match="held-out split"):
        anchors["held_0"]

    # The readable split stays readable, and the held-out size is exposed
    # without exposing content.
    assert anchors["ref_0"].payload == "reference 0"
    assert anchors.reference_size == 10
    assert anchors.held_out_size == 2


def test_audit_is_the_only_exit_and_fires_once() -> None:
    anchors = _anchor_set()
    assert anchors.audited is False

    report = anchors.audit(lambda payload: 1.0 if "zero" in payload else 0.0)

    assert anchors.audited is True
    assert report["held_out_count"] == 2
    # Perfect scorer: held_0 reference 1.0 observed 1.0, held_1 reference 0.0
    # observed 0.0.
    assert report["mean_absolute_deviation"] == 0.0
    assert report["max_absolute_deviation"] == 0.0

    with pytest.raises(HeldOutAnchorReadError, match="only run once"):
        anchors.audit(lambda payload: 0.0)


def test_audit_surfaces_a_metric_that_only_fits_its_reference_split() -> None:
    anchors = _anchor_set()

    # A degenerate detector that always returns the reference-split mean looks
    # fine on the anchors it was fitted to and fails the outer audit.
    report = anchors.audit(lambda payload: 0.5)

    assert report["mean_absolute_deviation"] == pytest.approx(0.5)


def test_file_score_audit_binds_ids_without_exposing_payloads() -> None:
    anchors = _anchor_set()
    report = anchors.audit_scores({"held_0": 1.0, "held_1": 0.0})
    assert report["mean_absolute_deviation"] == 0.0
    with pytest.raises(HeldOutAnchorReadError):
        anchors.held_out


def test_anchor_set_rejects_degenerate_construction() -> None:
    reference = (Anchor(anchor_id="ref_0", payload="p", reference=0.5),)
    held_out = (Anchor(anchor_id="held_0", payload="q", reference=0.5),)

    with pytest.raises(ValueError, match="both reference and held-out"):
        AnchorSet(set_id="s", reference=reference, held_out=())
    with pytest.raises(ValueError, match="unique across both splits"):
        AnchorSet(
            set_id="s",
            reference=reference,
            held_out=(Anchor(anchor_id="ref_0", payload="q", reference=0.5),),
        )
    with pytest.raises(ValueError, match="set_id"):
        AnchorSet(set_id="", reference=reference, held_out=held_out)


def test_anchor_rejects_out_of_range_reference() -> None:
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        Anchor(anchor_id="a", payload="p", reference=1.5)
    with pytest.raises(ValueError, match="non-empty string"):
        Anchor(anchor_id="a", payload="", reference=0.5)


def test_fingerprint_binds_held_out_split_without_exposing_it() -> None:
    left = _anchor_set()
    right = _anchor_set()
    assert left.fingerprint() == right.fingerprint()

    swapped = AnchorSet(
        set_id="anchors_v1",
        reference=left.reference,
        held_out=(
            Anchor(anchor_id="held_0", payload="held out zero", reference=1.0),
            Anchor(anchor_id="held_1", payload="different held out", reference=0.0),
        ),
    )
    assert swapped.fingerprint() != left.fingerprint()


def test_sealed_protocol_detects_every_deviation() -> None:
    anchors = _anchor_set()
    protocol = _protocol(anchors)

    # The matching run passes.
    protocol.verify_run(
        dataset_sha256="a" * 64,
        arms=("identity", "random_k", "full_v4"),
        primary_metric="family_macro_pearson",
        seeds=(24,),
        anchor_set=anchors,
    )

    with pytest.raises(SealedProtocolViolation, match="dataset"):
        protocol.verify_run(
            dataset_sha256="b" * 64,
            arms=("identity", "random_k", "full_v4"),
            primary_metric="family_macro_pearson",
            seeds=(24,),
            anchor_set=anchors,
        )
    with pytest.raises(SealedProtocolViolation, match="arms"):
        protocol.verify_run(
            dataset_sha256="a" * 64,
            arms=("identity", "full_v4"),
            primary_metric="family_macro_pearson",
            seeds=(24,),
            anchor_set=anchors,
        )
    with pytest.raises(SealedProtocolViolation, match="primary metric"):
        protocol.verify_run(
            dataset_sha256="a" * 64,
            arms=("identity", "random_k", "full_v4"),
            primary_metric="within_case_pairwise_concordance",
            seeds=(24,),
            anchor_set=anchors,
        )
    with pytest.raises(SealedProtocolViolation, match="seeds"):
        protocol.verify_run(
            dataset_sha256="a" * 64,
            arms=("identity", "random_k", "full_v4"),
            primary_metric="family_macro_pearson",
            seeds=(24, 25),
            anchor_set=anchors,
        )
    with pytest.raises(SealedProtocolViolation, match="anchor set"):
        protocol.verify_run(
            dataset_sha256="a" * 64,
            arms=("identity", "random_k", "full_v4"),
            primary_metric="family_macro_pearson",
            seeds=(24,),
            anchor_set=_anchor_set(set_id="anchors_v2"),
        )


def test_protocol_hash_is_stable_and_content_bound() -> None:
    anchors = _anchor_set()
    assert _protocol(anchors).protocol_sha256 == _protocol(anchors).protocol_sha256

    shifted = SealedProtocol(
        protocol_id="e1_sealed_confirmation",
        dataset_sha256="a" * 64,
        arms=("identity", "random_k", "full_v4"),
        primary_metric="family_macro_pearson",
        thresholds={"min_family_correlation": 0.3},
        seeds=(24,),
        anchor_fingerprint=anchors.fingerprint(),
    )
    assert shifted.protocol_sha256 != _protocol(anchors).protocol_sha256


def test_sealed_protocol_rejects_unusable_registration() -> None:
    anchors = _anchor_set()
    with pytest.raises(ValueError, match="two distinct arms"):
        SealedProtocol(
            protocol_id="p",
            dataset_sha256="a" * 64,
            arms=("identity",),
            primary_metric="m",
            thresholds={"t": 0.1},
            seeds=(1,),
            anchor_fingerprint=anchors.fingerprint(),
        )
    with pytest.raises(ValueError, match="distinct seeds"):
        SealedProtocol(
            protocol_id="p",
            dataset_sha256="a" * 64,
            arms=("identity", "full_v4"),
            primary_metric="m",
            thresholds={"t": 0.1},
            seeds=(1, 1),
            anchor_fingerprint=anchors.fingerprint(),
        )
    with pytest.raises(ValueError, match="sha256"):
        SealedProtocol(
            protocol_id="p",
            dataset_sha256="short",
            arms=("identity", "full_v4"),
            primary_metric="m",
            thresholds={"t": 0.1},
            seeds=(1,),
            anchor_fingerprint=anchors.fingerprint(),
        )

    with pytest.raises(ValueError, match="lowercase sha256"):
        SealedProtocol(
            protocol_id="p",
            dataset_sha256="G" * 64,
            arms=("identity", "full_v4"),
            primary_metric="m",
            thresholds={"t": 0.1},
            seeds=(1,),
            anchor_fingerprint=anchors.fingerprint(),
        )

    with pytest.raises(ValueError, match="numeric"):
        SealedProtocol(
            protocol_id="p",
            dataset_sha256="a" * 64,
            arms=("identity", "full_v4"),
            primary_metric="m",
            thresholds={"t": float("inf")},
            seeds=(1,),
            anchor_fingerprint=anchors.fingerprint(),
        )
