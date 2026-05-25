from pathlib import Path
import unittest

from cmd_audit import load_probe_cases
from cmd_audit.labels import V1_PIPELINE_LABELS
from cmd_audit.replays import run_oracle_retrieval


FIXTURE = Path("data/probe_cases/v0_retrieval_error_case.json")


class Decision34ReplayContextNoLabelTest(unittest.TestCase):
    def test_agent_context_does_not_contain_attribution_label(self) -> None:
        case = load_probe_cases(FIXTURE)[0]
        seen_contexts: list[str] = []

        def agent_generate(query: str, context: str) -> str:
            seen_contexts.append(context)
            return "Lisbon"

        run_oracle_retrieval(case, agent_generate=agent_generate)

        self.assertEqual(len(seen_contexts), 1)
        context = seen_contexts[0]
        self.assertIn("BASELINE CONTEXT:", context)
        self.assertIn("COUNTERFACTUAL EVIDENCE BLOCK:", context)
        self.assertNotIn("CMD ATTRIBUTION LABEL", context)
        for label in V1_PIPELINE_LABELS:
            self.assertNotIn(label, context)


if __name__ == "__main__":
    unittest.main()
