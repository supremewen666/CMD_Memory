import unittest

from scripts import build_hook_efficacy_supplementary as hook_eff


class FakeBaseline:
    retrieved_memory_ids = ()


class FakeCase:
    query = "query"
    extracted_memory = ()
    primary_baseline = FakeBaseline()


class HookEfficacySupplementaryTest(unittest.TestCase):
    def test_recall_hit_and_cost_reduction_from_selected_replays(self) -> None:
        retest = {
            "c1": [
                {"case_id": "c1", "replay_name": "oracle_write", "recovery_gain": "0.1"},
                {"case_id": "c1", "replay_name": "oracle_retrieval", "recovery_gain": "0.9"},
            ]
        }
        case_index = {"c1": ("unit", FakeCase())}

        rows = hook_eff.build_hook_efficacy_rows(
            retest,
            case_index,
            decision_fn=lambda query, retrieved: ("oracle_retrieval", "oracle_write"),
        )

        self.assertTrue(rows[0].recall_hit)
        self.assertEqual(rows[0].cost_reduction, 0.8)

    def test_summary_averages_rows(self) -> None:
        rows = [
            hook_eff.HookEfficacyRow(
                case_id="c1",
                source="unit",
                top_replay="oracle_retrieval",
                selected_replays=("oracle_retrieval",),
                recall_hit=True,
                cost_reduction=0.9,
            ),
            hook_eff.HookEfficacyRow(
                case_id="c2",
                source="unit",
                top_replay="oracle_write",
                selected_replays=("oracle_retrieval",),
                recall_hit=False,
                cost_reduction=0.9,
            ),
        ]

        summary = hook_eff.summarize(rows)

        self.assertEqual(summary["n"], 2.0)
        self.assertEqual(summary["recall"], 0.5)
        self.assertEqual(summary["cost_reduction"], 0.9)


if __name__ == "__main__":
    unittest.main()
