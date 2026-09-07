from __future__ import annotations

import json
from pathlib import Path

import pytest

from cmd_audit.spec_v03.contracts import canonical_sha256
from cmd_audit.spec_v03.repair_stream import (
    PublicEpisode,
    PublicEvent,
    PublicQuery,
    build_intervention,
    compile_repair_case,
)
from cmd_audit.spec_v03.runtime_bundle import RuntimeBundle, deserialize, load, load_many, load_runtime_cases


def _event(event_id: str, source_ref: str, ordinal: int, payload: dict[str, object]) -> PublicEvent:
    payload_sha256 = canonical_sha256(payload)
    return PublicEvent(
        event_id=event_id,
        source_ref=source_ref,
        ordinal=ordinal,
        timestamp=None,
        actor_scope="user",
        payload=payload,
        payload_sha256=payload_sha256,
        source_payload_sha256=payload_sha256,
    )


def _episode() -> PublicEpisode:
    events = (
        _event("event-alpha", "source:alpha", 0, {"memory": "alpha"}),
        _event("event-beta", "source:beta", 1, {"memory": "beta", "original_memories": ["alpha"]}),
    )
    return PublicEpisode(
        episode_id="fixture:episode",
        source_dataset_id="fixture",
        family_id="fixture-family",
        source_path="fixture.json",
        source_sha256="a" * 64,
        immutable_events=events,
        sealed_queries=(PublicQuery("fixture:q", "What is current?", "beta", (), "fixture:q"),),
        capabilities=("process", "state", "poison"),
        source_metadata={"fixture": "runtime-bundle"},
    )


def _case(template: str):
    episode = _episode()
    return compile_repair_case(episode, build_intervention(episode, template, seed=59))


@pytest.mark.parametrize("template", ("drop", "explicit_supersede", "untrusted_injection"))
def test_runtime_bundle_round_trip_is_root_closed(template: str, tmp_path: Path) -> None:
    case = _case(template)
    encoded = case.public_mapping()
    text = json.dumps(encoded, sort_keys=True).casefold()
    for forbidden in ("template_id", "target_event_id", "expected_effect", "process_fault", "state_drift", '"poison"', "intervention:"):
        assert forbidden not in text

    decoded = deserialize(encoded)
    path = tmp_path / f"{template}.json"
    path.write_text(json.dumps(encoded), encoding="utf-8")
    from_disk = load(path)

    assert decoded == from_disk
    assert isinstance(decoded, RuntimeBundle)
    assert decoded.memory_state == case.corrupt_state
    assert decoded.memory_state.root == case.decision_view.observation["current_state"]["state_root"]
    assert decoded.source_event_ids == tuple(event.event_id for event in case.clean_events)
    assert [row["event_id"] for row in decoded.decision_view.observation["event_log"]] == [
        event.event_id for event in decoded.memory_state.immutable_source_log + decoded.memory_state.audit_log
    ]


@pytest.mark.parametrize(
    ("template", "mutation", "message"),
    (
        ("drop", lambda value: value["memory_state"].__setitem__("state_root", "0" * 64), "root mismatch"),
        ("explicit_supersede", lambda value: value["decision_view"].__setitem__("lineage_id", "lineage-tampered"), "lineage_id"),
        ("untrusted_injection", lambda value: value.__setitem__("template_id", "leak"), "closed"),
    ),
)
def test_runtime_bundle_rejects_tampering(template: str, mutation, message: str) -> None:
    encoded = json.loads(json.dumps(_case(template).public_mapping()))
    mutation(encoded)
    with pytest.raises(ValueError, match=message):
        deserialize(encoded)


def test_load_many_reads_freeze_style_list_and_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    encoded = [_case(template).public_mapping() for template in ("drop", "explicit_supersede", "untrusted_injection")]
    path = tmp_path / "runtime_cases.json"
    path.write_text(json.dumps(encoded), encoding="utf-8")

    bundles = load_many(path)
    assert load_runtime_cases(path) == bundles
    assert [bundle.case_id for bundle in bundles] == [row["case_id"] for row in encoded]

    path.write_text(json.dumps([encoded[0], encoded[0]]), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate case_id"):
        load_many(path)
