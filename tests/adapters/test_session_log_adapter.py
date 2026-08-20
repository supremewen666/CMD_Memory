from __future__ import annotations

import json

import pytest

from cmd_audit.core.models import GoldEvidence
from cmd_audit.adapters.session_log import (
    SessionEvent,
    SessionLogAdapter,
    SessionLogError,
    SessionTrace,
    iter_arms,
    load_session_traces,
    session_events_to_probe_case,
)
from cmd_audit.repair.actions import RepairAction


def _action(action_type: str, content: str, target_item_id: str | None = None):
    return RepairAction(
        action_type=action_type,
        target_item_id=target_item_id,
        target_store="episodic",
        content=content,
        label="retrieval_error",
    )


def _events(*, compacted: bool) -> tuple[SessionEvent, ...]:
    base = (
        SessionEvent(event_id="e0", kind="user_message", text="fix the auth bug", turn=0),
        SessionEvent(event_id="e1", kind="tool_call", text="read auth.py", turn=1),
        SessionEvent(
            event_id="e2", kind="tool_result", text="AssertionError at auth.py:42", turn=2
        ),
    )
    if not compacted:
        return base
    return base + (
        SessionEvent(
            event_id="e3",
            kind="summary",
            text="inspected auth.py, saw a failure",
            turn=3,
            replaces_event_ids=("e1", "e2"),
        ),
    )


def _trace(*, arm: str = "llm_summary", compacted: bool = True) -> SessionTrace:
    return SessionTrace(
        session_id="sess_1",
        arm=arm,
        events=_events(compacted=compacted),
        resolved=True,
    )


def _gold() -> tuple[GoldEvidence, ...]:
    return (GoldEvidence(evidence_id="g0", text="auth.py:42"),)


def _probe_case(trace: SessionTrace):
    return session_events_to_probe_case(
        trace,
        query="fix the auth bug",
        gold_answer="patch auth.py line 42",
        gold_evidence=_gold(),
    )


def test_projection_drops_events_a_summary_replaced() -> None:
    trace = _trace()

    projected = [row.event_id for row in trace.project()]

    # e1/e2 were superseded by the summary, so only the user turn and the
    # summary reach the model's context.
    assert projected == ["e0", "e3"]
    assert trace.compaction_event_count == 1
    assert trace.replaced_event_ids == frozenset({"e1", "e2"})


def test_raw_arm_projects_every_content_event() -> None:
    trace = _trace(arm="raw", compacted=False)

    assert [row.event_id for row in trace.project()] == ["e0", "e1", "e2"]
    assert trace.compaction_event_count == 0
    assert trace.replaced_event_ids == frozenset()


def test_masked_events_stay_logged_but_carry_no_content_forward() -> None:
    trace = SessionTrace(
        session_id="sess_masked",
        arm="observation_masking",
        resolved=False,
        events=(
            SessionEvent(event_id="e0", kind="user_message", text="q", turn=0),
            SessionEvent(
                event_id="e1", kind="tool_result", text="long output", turn=1, masked=True
            ),
        ),
    )

    assert [row.event_id for row in trace.project()] == ["e0"]
    # The masked event is still in the log — the audit needs to see that it
    # existed, which is the whole reason for an append-only substrate.
    assert len(trace.events) == 2


def test_conversion_maps_summaries_into_their_own_store() -> None:
    case = _probe_case(_trace())

    stores = {item.memory_id: item.store for item in case.extracted_memory}
    assert stores == {"sess_1-e0": "episodic", "sess_1-e3": "summary"}
    assert case.case_id == "sess_1-llm_summary"
    assert case.current_granularity == "summary"
    # Every event stays available as the raw ingestion trace, including the ones
    # compaction removed from context.
    assert [row.event_id for row in case.raw_events] == ["e0", "e1", "e2", "e3"]


def test_uncompacted_conversion_reports_session_granularity() -> None:
    case = _probe_case(_trace(arm="raw", compacted=False))

    assert case.current_granularity == "session"
    assert case.case_id == "sess_1-raw"
    assert len(case.extracted_memory) == 3


def test_conversion_refuses_to_invent_a_reference_signal() -> None:
    trace = _trace()

    with pytest.raises(SessionLogError, match="supply gold_answer explicitly"):
        session_events_to_probe_case(
            trace, query="q", gold_answer="", gold_evidence=_gold()
        )
    with pytest.raises(SessionLogError, match="supply gold_evidence explicitly"):
        session_events_to_probe_case(
            trace, query="q", gold_answer="a", gold_evidence=()
        )
    with pytest.raises(SessionLogError, match="requires a query"):
        session_events_to_probe_case(
            trace, query="", gold_answer="a", gold_evidence=_gold()
        )


def test_conversion_refuses_a_session_that_projects_nothing() -> None:
    trace = SessionTrace(
        session_id="sess_empty",
        arm="raw",
        resolved=False,
        events=(SessionEvent(event_id="e0", kind="session_start", text="", turn=0),),
    )

    with pytest.raises(SessionLogError, match="projects no context"):
        session_events_to_probe_case(
            trace, query="q", gold_answer="a", gold_evidence=_gold()
        )


def test_cut_point_a_restores_the_verbatim_turns_compaction_removed() -> None:
    trace = _trace()
    case = _probe_case(trace)
    adapter = SessionLogAdapter(trace, extracted_memory=case.extracted_memory)

    restored = adapter.intercept_projection("c", adapter.original_projected_events)

    # The summary is out and the two turns it replaced are back, in turn order.
    assert [row.event_id for row in restored] == ["e0", "e1", "e2"]


def test_cut_point_a_is_a_passthrough_when_nothing_was_compacted() -> None:
    trace = _trace(arm="raw", compacted=False)
    adapter = SessionLogAdapter(trace)

    original = adapter.original_projected_events
    assert adapter.intercept_projection("c", original) == original


def test_cut_point_b_returns_the_sandboxed_recall_set() -> None:
    trace = _trace()
    case = _probe_case(trace)
    adapter = SessionLogAdapter(trace, extracted_memory=case.extracted_memory)

    recalled = adapter.intercept_recall("c", "fix the auth bug", adapter.original_results)

    assert [item.memory_id for item in recalled] == ["sess_1-e0", "sess_1-e3"]
    assert adapter.original_query == "fix the auth bug"


def test_repairs_touch_only_the_sandbox_and_leave_the_log_intact() -> None:
    trace = _trace()
    case = _probe_case(trace)
    adapter = SessionLogAdapter(trace, extracted_memory=case.extracted_memory)
    before = adapter.get_store_snapshot()

    assert (
        adapter.apply_repair(_action("append", "auth.py:42 is the failing line"))
        == "session_log append: new -> episodic"
    )
    after = adapter.get_store_snapshot()

    assert after.item_count == before.item_count + 1
    assert after.checksum != before.checksum
    # The recorded session itself is untouched.
    adapter.verify_sandbox()


def test_replace_requires_a_target_in_the_recall_set() -> None:
    trace = _trace()
    case = _probe_case(trace)
    adapter = SessionLogAdapter(trace, extracted_memory=case.extracted_memory)

    assert adapter.apply_repair(
        _action("replace", "AssertionError at auth.py:42", "sess_1-e3")
    ) == "session_log replace: sess_1-e3 -> episodic"
    assert adapter.original_results[1].text == "AssertionError at auth.py:42"

    # A bad target must be a hard error, not an UnsupportedActionError — the
    # executor swallows the latter as "adapter can't do this action type" and
    # would turn a wrong target into a silent no-op.
    with pytest.raises(ValueError, match="not in sandboxed recall set"):
        adapter.apply_repair(_action("replace", "x", "sess_1-nope"))
    with pytest.raises(ValueError, match="replace requires target_item_id"):
        adapter.apply_repair(_action("replace", "x", None))


def test_trace_validation_rejects_malformed_logs() -> None:
    with pytest.raises(SessionLogError, match="unique"):
        SessionTrace(
            session_id="s",
            arm="raw",
            resolved=False,
            events=(
                SessionEvent(event_id="e0", kind="user_message", text="a", turn=0),
                SessionEvent(event_id="e0", kind="user_message", text="b", turn=1),
            ),
        )
    with pytest.raises(SessionLogError, match="non-decreasing turn order"):
        SessionTrace(
            session_id="s",
            arm="raw",
            resolved=False,
            events=(
                SessionEvent(event_id="e0", kind="user_message", text="a", turn=5),
                SessionEvent(event_id="e1", kind="user_message", text="b", turn=1),
            ),
        )
    with pytest.raises(SessionLogError, match="replaces unknown events"):
        SessionTrace(
            session_id="s",
            arm="raw",
            resolved=False,
            events=(
                SessionEvent(
                    event_id="e0",
                    kind="summary",
                    text="a",
                    turn=0,
                    replaces_event_ids=("ghost",),
                ),
            ),
        )
    with pytest.raises(SessionLogError, match="at least one event"):
        SessionTrace(session_id="s", arm="raw", resolved=False, events=())


def test_event_validation_rejects_unknown_kinds_and_illegal_replacement() -> None:
    with pytest.raises(SessionLogError, match="unknown session event kind"):
        SessionEvent(event_id="e", kind="telepathy", text="", turn=0)
    with pytest.raises(SessionLogError, match="only summary/compaction"):
        SessionEvent(
            event_id="e",
            kind="tool_result",
            text="",
            turn=0,
            replaces_event_ids=("e0",),
        )
    with pytest.raises(SessionLogError, match="non-negative integer"):
        SessionEvent(event_id="e", kind="user_message", text="", turn=-1)


def test_loader_reads_jsonl_and_groups_by_arm(tmp_path) -> None:
    path = tmp_path / "sessions.jsonl"
    rows = [
        {
            "session_id": "s1",
            "arm": "raw",
            "resolved": True,
            "events": [{"event_id": "e0", "kind": "user_message", "text": "q", "turn": 0}],
        },
        {
            "session_id": "s1",
            "arm": "llm_summary",
            "resolved": False,
            "events": [
                {"event_id": "e0", "kind": "user_message", "text": "q", "turn": 0},
                {
                    "event_id": "e1",
                    "kind": "summary",
                    "text": "s",
                    "turn": 1,
                    "replaces_event_ids": ["e0"],
                },
            ],
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    traces = load_session_traces(path)

    assert len(traces) == 2
    assert [arm for arm, _ in iter_arms(traces)] == ["llm_summary", "raw"]
    # Same session under two arms — the frozen multi-arm shape the substrate
    # needs to be comparable.
    assert {trace.session_id for trace in traces} == {"s1"}


def test_loader_rejects_unusable_files(tmp_path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(SessionLogError, match="empty"):
        load_session_traces(empty)

    broken = tmp_path / "broken.jsonl"
    broken.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(SessionLogError, match="invalid session-log JSONL"):
        load_session_traces(broken)

    scalar = tmp_path / "scalar.jsonl"
    scalar.write_text("42\n", encoding="utf-8")
    with pytest.raises(SessionLogError, match="not an object"):
        load_session_traces(scalar)


_FACT = "the failure is an AssertionError at auth.py line 42"


def _arm_trace(arm: str) -> SessionTrace:
    """One session recorded under three context-management strategies.

    The three arms are the comparison the substrate exists for: ``raw`` keeps the
    tool result, ``observation_masking`` drops its content, and ``llm_summary``
    replaces it with a lossy stand-in.
    """
    events = [
        SessionEvent(event_id="e0", kind="user_message", text="fix the auth bug", turn=0),
        SessionEvent(event_id="e1", kind="tool_call", text="read auth.py", turn=1),
        SessionEvent(
            event_id="e2",
            kind="tool_result",
            text=_FACT,
            turn=2,
            masked=(arm == "observation_masking"),
        ),
    ]
    if arm == "llm_summary":
        events.append(
            SessionEvent(
                event_id="e3",
                kind="summary",
                text="looked at auth.py, saw a failure",
                turn=3,
                replaces_event_ids=("e1", "e2"),
            )
        )
    return SessionTrace(
        session_id="sesse2e",
        arm=arm,
        events=tuple(events),
        resolved=(arm == "raw"),
    )


def _arm_case(trace: SessionTrace):
    return session_events_to_probe_case(
        trace,
        query="where does the auth bug fail?",
        gold_answer="AssertionError at auth.py line 42",
        gold_evidence=(
            GoldEvidence(
                evidence_id="g0", text=_FACT, source_memory_id="sesse2e-e2"
            ),
        ),
    )


@pytest.mark.parametrize(
    "arm, expected_label",
    [
        ("raw", None),
        ("observation_masking", "retrieval_error"),
        ("llm_summary", "retrieval_error"),
    ],
)
def test_three_arm_traces_run_end_to_end_through_run_case(arm, expected_label) -> None:
    from cmd_audit import run_case

    trace = _arm_trace(arm)
    case = _arm_case(trace)

    audited = run_case(case, hook=True)
    adapter = SessionLogAdapter(trace, extracted_memory=case.extracted_memory)
    repaired = run_case(case, repair=adapter)

    assert audited.runtime_branch == "fix"
    label = audited.attribution.predicted_label if audited.attribution else None
    # raw still carries the deciding tool result, so there is nothing to
    # attribute; the two lossy arms lose it and a repair is attempted.
    assert label == expected_label
    assert bool(repaired.orchestrator_result) is (expected_label is not None)
    adapter.verify_sandbox()


def test_identifiers_are_opaque_enough_for_the_leak_safe_monitor() -> None:
    # memory_ids reach the monitor as evidence pointers, and
    # validate_evidence_pointers rejects ':' as a content-bearing separator.
    case = _arm_case(_arm_trace("raw"))

    assert ":" not in case.case_id
    assert all(":" not in item.memory_id for item in case.extracted_memory)


def test_conversion_supplies_both_required_memory_baselines() -> None:
    from cmd_audit.baselines.comparators import REQUIRED_MEMORY_BASELINES

    case = _arm_case(_arm_trace("raw"))

    names = {output.baseline_name for output in case.baseline_outputs}
    assert set(REQUIRED_MEMORY_BASELINES) <= names
    # fixed_summary is a contract stand-in, not an observed second summarizer:
    # it must not claim an answer the session log never recorded.
    fixed = next(
        row for row in case.baseline_outputs if row.baseline_name == "fixed_summary"
    )
    assert fixed.answer == ""
    assert fixed.answer_score == 0.0


def test_content_hash_is_stable_and_detects_any_edit() -> None:
    left = _trace()
    right = _trace()
    assert left.content_sha256() == right.content_sha256()

    edited = SessionTrace(
        session_id="sess_1",
        arm="llm_summary",
        resolved=True,
        events=_events(compacted=False)
        + (
            SessionEvent(
                event_id="e3",
                kind="summary",
                text="a different summary",
                turn=3,
                replaces_event_ids=("e1", "e2"),
            ),
        ),
    )
    assert edited.content_sha256() != left.content_sha256()
