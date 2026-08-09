"""Persistent, content-addressed cache contract for relation judgments."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from cmd_audit.counterfactual.relation_cache import RelationCache, RelationCacheKey
from cmd_audit.counterfactual.slot_relation import (
    RelationReason,
    RelationType,
    judge_relation,
)


class CountingJudge:
    def __init__(self):
        self.calls = 0

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        self.calls += 1
        return '{"relation":"same_slot_different_value", "slot":"location"}'


def test_cache_key_is_unordered_normalized_text_and_versions_not_item_identity(
    tmp_path,
):
    first = RelationCacheKey.build(
        " M_old: I live in Seattle. ",
        "M_new: I live in Austin.",
        prompt_sha256="p" * 64,
        parser_version="parser-v1",
        model_id="model-v1",
    )
    reverse = RelationCacheKey.build(
        "I live in Austin.",
        "I live in Seattle.",
        prompt_sha256="p" * 64,
        parser_version="parser-v1",
        model_id="model-v1",
    )
    changed_model = RelationCacheKey.build(
        "I live in Seattle.",
        "I live in Austin.",
        prompt_sha256="p" * 64,
        parser_version="parser-v1",
        model_id="model-v2",
    )
    changed_config = RelationCacheKey.build(
        "I live in Seattle.",
        "I live in Austin.",
        prompt_sha256="p" * 64,
        parser_version="parser-v1",
        model_id="model-v1",
        model_config_hash="temperature-0.5",
    )
    assert first.cache_key == reverse.cache_key
    assert first.cache_key != changed_model.cache_key
    assert first.cache_key != changed_config.cache_key
    assert "item" not in first.__dict__ and "id" not in first.__dict__


def test_same_text_pair_calls_judge_once_and_survives_reopen(tmp_path):
    path = tmp_path / "relations.sqlite3"
    cache = RelationCache(path)
    judge = CountingJudge()
    for _ in range(3):
        verdict = judge_relation(
            "M_old: I live in Seattle.",
            "M_new: I live in Austin.",
            judge=judge,
            cache=cache,
            model_id="test",
        )
        assert verdict.relation is RelationType.SAME_SLOT_DIFFERENT_VALUE
    assert judge.calls == 1
    cache.close()

    reopened = RelationCache(path)
    judge_relation(
        "I live in Austin.",
        "I live in Seattle.",
        judge=judge,
        cache=reopened,
        model_id="test",
    )
    assert judge.calls == 1
    audit = reopened.audit_rows()
    assert len(audit) == 1
    assert audit[0]["model_id"] == "test"
    assert "item_id" not in audit[0]
    reopened.close()


def test_concurrent_identical_pairs_share_one_measurement(tmp_path):
    cache = RelationCache(tmp_path / "relations.sqlite3")
    judge = CountingJudge()
    with ThreadPoolExecutor(max_workers=4) as pool:
        verdicts = list(
            pool.map(
                lambda _: judge_relation(
                    "I live in Seattle.",
                    "I live in Austin.",
                    judge=judge,
                    cache=cache,
                    model_id="test",
                ),
                range(8),
            )
        )
    assert all(
        verdict.relation is RelationType.SAME_SLOT_DIFFERENT_VALUE
        for verdict in verdicts
    )
    assert judge.calls == 1
    cache.close()


def test_cached_verdict_replays_raw_attempt_audit_after_reopen(tmp_path):
    class RetryJudge:
        def __init__(self):
            self.calls = 0

        def generate(self, prompt: str, *, system: str | None = None) -> str:
            self.calls += 1
            if self.calls == 1:
                return '{"relation":"unrelated","reason":"extra"}'
            return '```json\n{"relation":"unrelated"}\n```'

    path = tmp_path / "relations.sqlite3"
    judge = RetryJudge()
    with RelationCache(path) as cache:
        measured = judge_relation("Seattle", "Austin", judge=judge, cache=cache)
    with RelationCache(path) as cache:
        replayed = judge_relation("Austin", "Seattle", judge=judge, cache=cache)

    assert judge.calls == 2
    assert replayed == measured
    assert replayed.reason_code is RelationReason.ACCEPTED_FENCED_JSON
    assert [attempt.reason_code for attempt in replayed.attempts] == [
        RelationReason.INVALID_SCHEMA,
        RelationReason.ACCEPTED_FENCED_JSON,
    ]


def test_exhausted_uncertain_measurement_does_not_poison_persistent_cache(tmp_path):
    class MalformedJudge:
        def __init__(self):
            self.calls = 0

        def generate(self, prompt: str, *, system: str | None = None) -> str:
            self.calls += 1
            return "not-json"

    path = tmp_path / "relations.sqlite3"
    malformed = MalformedJudge()
    with RelationCache(path) as cache:
        failed = judge_relation("Seattle", "Austin", judge=malformed, cache=cache)
        assert cache.audit_rows() == []

    recovered = CountingJudge()
    with RelationCache(path) as cache:
        verdict = judge_relation("Seattle", "Austin", judge=recovered, cache=cache)

    assert failed.relation is RelationType.UNCERTAIN
    assert malformed.calls == 3
    assert recovered.calls == 1
    assert verdict.relation is RelationType.SAME_SLOT_DIFFERENT_VALUE
