"""Hook redesign tests — Issue 0021."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import unittest

from cmd_audit import load_probe_cases, load_probe_cases_v1, run_case_v1_with_hook
from cmd_audit.hook import (
    PreCmdDecision,
    ReplayScore,
    compute_global_features,
    compute_replay_type_one_hot,
    extract_features,
    post_retrieve_hook,
    rank_scores,
    score_replays,
)
from cmd_audit.hook import constants as hook_constants
from cmd_audit.core.models import RetrievedItem


RETRIEVAL_FIXTURE = Path("data/probe_cases/v0_retrieval_error_case.json")
GRAPH_FIXTURE = Path("data/probe_cases/v1_graph_error_case.json")


def _items(*texts: str) -> tuple[RetrievedItem, ...]:
    return tuple(
        RetrievedItem(memory_id=f"mem-{idx:03d}", text=text)
        for idx, text in enumerate(texts)
    )


@contextmanager
def _patched_constants(**values):
    previous = {name: getattr(hook_constants, name) for name in values}
    for name, value in values.items():
        setattr(hook_constants, name, value)
    try:
        yield
    finally:
        for name, value in previous.items():
            setattr(hook_constants, name, value)


def _weights_for(**one_hot_weights: float) -> tuple[float, ...]:
    replay_weights = []
    for replay_name in hook_constants.V1_REPLAY_NAME_ORDER:
        replay_weights.append(float(one_hot_weights.get(replay_name, 0.0)))
    return (0.0,) * 6 + tuple(replay_weights)


class TestEmptyCtxHardShortCircuit(unittest.TestCase):
    def test_zero_items_triggers_empty_ctx(self) -> None:
        decision = post_retrieve_hook("query", ())
        self.assertTrue(decision.trigger_cmd)
        self.assertEqual(decision.stage, "empty_ctx")

    def test_empty_ctx_selected_replays_full_10(self) -> None:
        decision = post_retrieve_hook("query", ())
        self.assertEqual(decision.selected_replays, hook_constants.V1_REPLAY_NAME_ORDER)

    def test_online_mode_uses_sentinel_scores(self) -> None:
        decision = post_retrieve_hook("query", (), mode="online")
        self.assertTrue(all(score.is_sentinel for score in decision.per_replay_scores))
        self.assertTrue(all(score.p_score == -1.0 for score in decision.per_replay_scores))
        self.assertTrue(all(score.selected for score in decision.per_replay_scores))

    def test_offline_mode_runs_rpe_judge(self) -> None:
        decision = post_retrieve_hook("query", (), mode="offline")
        self.assertFalse(any(score.is_sentinel for score in decision.per_replay_scores))
        self.assertTrue(all(0.0 <= score.p_score <= 1.0 for score in decision.per_replay_scores))
        self.assertTrue(all(score.selected for score in decision.per_replay_scores))

    def test_default_mode_is_online(self) -> None:
        decision = post_retrieve_hook("query", ())
        self.assertTrue(decision.per_replay_scores[0].is_sentinel)


class TestRPEJudgeFeatureExtraction(unittest.TestCase):
    def test_global_feature_count_is_6(self) -> None:
        features = compute_global_features("Kai workshop city", _items("Kai chose Madrid"))
        self.assertEqual(len(features), 6)

    def test_one_hot_count_is_10(self) -> None:
        one_hot = compute_replay_type_one_hot("oracle_retrieval")
        self.assertEqual(len(one_hot), 10)
        self.assertEqual(sum(one_hot), 1.0)

    def test_full_feature_count_is_16(self) -> None:
        features = extract_features(
            "Kai workshop city",
            _items("Kai chose Madrid"),
            "oracle_retrieval",
        )
        self.assertEqual(len(features), 16)

    def test_item_count_capped_and_normalized(self) -> None:
        items = tuple(RetrievedItem(memory_id=str(i), text="text") for i in range(25))
        features = compute_global_features("query", items)
        self.assertEqual(features[3], 1.0)

    def test_bm25_std_zero_when_single_item(self) -> None:
        features = compute_global_features("Kai Madrid", _items("Kai chose Madrid"))
        self.assertEqual(features[2], 0.0)

    def test_near_duplicate_uses_jaccard(self) -> None:
        text = "Kai chose Madrid for the partner workshop"
        features = compute_global_features("Kai workshop", _items(text, text))
        self.assertGreater(features[4], 0.9)

    def test_low_count_binary(self) -> None:
        single = compute_global_features("query", _items("one"))
        multiple = compute_global_features("query", _items("one", "two"))
        self.assertEqual(single[5], 1.0)
        self.assertEqual(multiple[5], 0.0)

    def test_unknown_replay_name_rejected(self) -> None:
        with self.assertRaises(ValueError):
            compute_replay_type_one_hot("not_a_replay")


class TestRPEJudgeInference(unittest.TestCase):
    def test_sigmoid_output_in_unit_interval(self) -> None:
        scores = score_replays("Kai Madrid", _items("Kai chose Madrid"))
        self.assertTrue(all(0.0 <= score.p_score <= 1.0 for score in scores))

    def test_per_replay_scores_length_10(self) -> None:
        scores = score_replays("Kai Madrid", _items("Kai chose Madrid"))
        self.assertEqual(len(scores), 10)

    def test_sort_by_p_desc_with_deterministic_tiebreak(self) -> None:
        with _patched_constants(TOP_K=3, RPE_JUDGE_WEIGHTS=(0.0,) * 16):
            ranked = rank_scores(score_replays("query", _items("text")))
        self.assertEqual(
            tuple(score.replay_name for score in ranked[:3]),
            hook_constants.V1_REPLAY_NAME_ORDER[:3],
        )

    def test_top_k_selection(self) -> None:
        weights = _weights_for(graph_off=8.0, safety_off=7.0, oracle_route=6.0)
        with _patched_constants(
            TOP_K=2,
            RPE_JUDGE_WEIGHTS=weights,
            RPE_JUDGE_INTERCEPT=-4.0,
        ):
            scores = score_replays("query", _items("text"))
        selected = {score.replay_name for score in scores if score.selected}
        self.assertEqual(selected, {"graph_off", "safety_off"})

    def test_top_k_gt_portfolio_size_returns_all(self) -> None:
        with _patched_constants(TOP_K=99, RPE_JUDGE_WEIGHTS=(0.0,) * 16):
            scores = score_replays("query", _items("text"))
        self.assertTrue(all(score.selected for score in scores))


class TestStageDecisionLogic(unittest.TestCase):
    def test_stage_rpe_top_k_when_max_p_above_threshold(self) -> None:
        weights = _weights_for(oracle_retrieval=8.0)
        with _patched_constants(
            TOP_K=1,
            FALLBACK_THRESHOLD=0.5,
            RPE_JUDGE_WEIGHTS=weights,
            RPE_JUDGE_INTERCEPT=-4.0,
        ):
            decision = post_retrieve_hook("query", _items("text"))
        self.assertTrue(decision.trigger_cmd)
        self.assertEqual(decision.stage, "rpe_top_k")
        self.assertEqual(decision.selected_replays, ("oracle_retrieval",))

    def test_stage_rpe_below_threshold_when_max_p_below(self) -> None:
        with _patched_constants(
            FALLBACK_THRESHOLD=0.5,
            RPE_JUDGE_WEIGHTS=(0.0,) * 16,
            RPE_JUDGE_INTERCEPT=-10.0,
        ):
            decision = post_retrieve_hook("query", _items("text"))
        self.assertFalse(decision.trigger_cmd)
        self.assertEqual(decision.stage, "rpe_below_threshold")
        self.assertEqual(decision.selected_replays, ())
        self.assertFalse(any(score.selected for score in decision.per_replay_scores))

    def test_trigger_cmd_consistent_with_stage(self) -> None:
        empty = post_retrieve_hook("query", ())
        self.assertTrue(empty.trigger_cmd)
        self.assertEqual(empty.stage, "empty_ctx")
        with _patched_constants(
            FALLBACK_THRESHOLD=0.5,
            RPE_JUDGE_WEIGHTS=(0.0,) * 16,
            RPE_JUDGE_INTERCEPT=-10.0,
        ):
            skipped = post_retrieve_hook("query", _items("text"))
        self.assertFalse(skipped.trigger_cmd)
        self.assertEqual(skipped.stage, "rpe_below_threshold")


class TestPreCmdDecisionShape(unittest.TestCase):
    def test_per_replay_scores_always_10_elements(self) -> None:
        decision = post_retrieve_hook("query", _items("text"))
        self.assertEqual(len(decision.per_replay_scores), 10)

    def test_sentinel_p_score_is_negative_one(self) -> None:
        score = ReplayScore(
            replay_name="oracle_write",
            p_score=-1.0,
            selected=True,
            is_sentinel=True,
        )
        self.assertEqual(score.p_score, -1.0)

    def test_sentinel_only_in_online_empty_ctx(self) -> None:
        decision = post_retrieve_hook("query", _items("text"))
        self.assertFalse(any(score.is_sentinel for score in decision.per_replay_scores))

    def test_no_fallback_triggered_field(self) -> None:
        decision = post_retrieve_hook("query", _items("text"))
        self.assertFalse(hasattr(decision, "fallback_triggered"))

    def test_invalid_decision_stage_rejected(self) -> None:
        scores = score_replays("query", _items("text"))
        with self.assertRaises(ValueError):
            PreCmdDecision(
                trigger_cmd=False,
                stage="clean",
                per_replay_scores=scores,
                selected_replays=(),
            )


class TestRunCaseV1WithHook(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.retrieval_case = load_probe_cases(RETRIEVAL_FIXTURE)[0]
        cls.graph_case = load_probe_cases_v1(GRAPH_FIXTURE)[0]

    def test_hook_stage_propagates_to_audit_result(self) -> None:
        result = run_case_v1_with_hook(self.retrieval_case)
        self.assertIn(result.hook_stage, {"empty_ctx", "rpe_top_k", "rpe_below_threshold"})

    def test_selected_replays_propagates(self) -> None:
        result = run_case_v1_with_hook(self.retrieval_case)
        self.assertEqual(len(result.per_replay_scores), 10)
        self.assertIsInstance(result.selected_replays, tuple)

    def test_clean_case_returns_no_attribution(self) -> None:
        with _patched_constants(
            FALLBACK_THRESHOLD=0.5,
            RPE_JUDGE_WEIGHTS=(0.0,) * 16,
            RPE_JUDGE_INTERCEPT=-10.0,
        ):
            result = run_case_v1_with_hook(self.retrieval_case)
        self.assertIsNone(result.attribution)
        self.assertEqual(result.replays, ())
        self.assertEqual(result.hook_stage, "rpe_below_threshold")
        self.assertFalse(hasattr(result, "fallback_triggered"))

    def test_triggered_case_runs_subset_replays(self) -> None:
        with _patched_constants(TOP_K=3, FALLBACK_THRESHOLD=0.35):
            result = run_case_v1_with_hook(self.retrieval_case)
        self.assertEqual(result.hook_stage, "rpe_top_k")
        self.assertLessEqual(len(result.replays), 3)

    def test_provenance_tracker_intact_under_hook(self) -> None:
        weights = _weights_for(graph_off=8.0)
        with _patched_constants(
            TOP_K=1,
            FALLBACK_THRESHOLD=0.5,
            RPE_JUDGE_WEIGHTS=weights,
            RPE_JUDGE_INTERCEPT=-4.0,
        ):
            result = run_case_v1_with_hook(self.graph_case)
        self.assertEqual(result.selected_replays, ("graph_off",))
        for replay in result.replays:
            self.assertIsInstance(replay.provenance_edges, tuple)


class TestOnlineOfflineModeSplit(unittest.TestCase):
    def test_online_empty_ctx_sentinel_per_replay(self) -> None:
        decision = post_retrieve_hook("query", (), mode="online")
        self.assertTrue(all(score.is_sentinel for score in decision.per_replay_scores))

    def test_offline_empty_ctx_real_rpe_per_replay(self) -> None:
        decision = post_retrieve_hook("query", (), mode="offline")
        self.assertFalse(any(score.is_sentinel for score in decision.per_replay_scores))

    def test_invalid_mode_rejected(self) -> None:
        with self.assertRaises(ValueError):
            post_retrieve_hook("query", (), mode="batch")


if __name__ == "__main__":
    unittest.main()
