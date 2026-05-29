from pathlib import Path
import unittest

from cmd_audit import load_probe_cases, run_case
from cmd_audit.adapters import (
    load_letta_traces,
    load_mem0_traces,
    run_case_with_letta,
    run_case_with_mem0,
)
from cmd_audit.scoring import evidence_recall_from_text


V0_SMOKE = Path("data/probe_cases/v0_issue3_cases.json")
MEM0_TRACES = Path("data/probe_cases/mem0_v0_smoke_traces.json")
LETTA_TRACES = Path("data/probe_cases/letta_v0_smoke_traces.json")


def _stub_agent_generate(query: str, context: str) -> str:
    if "COUNTERFACTUAL EVIDENCE BLOCK:" not in context:
        return "baseline failed answer"
    return context


def _stub_scorer(gold_evidence, text: str) -> float:
    return evidence_recall_from_text(gold_evidence, text)


class AdapterParityUnderLLMStack(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)
        cls.mem0_traces = load_mem0_traces(MEM0_TRACES)
        cls.letta_traces = load_letta_traces(LETTA_TRACES)

    def test_mem0_adapter_label_parity_under_llm_stack(self) -> None:
        for case in self.cases:
            with self.subTest(case_id=case.case_id):
                standalone = run_case(
                    case,
                    agent_generate=_stub_agent_generate,
                    scorer=_stub_scorer,
                    on_the_fly_baseline_rescore=True,
                )
                adapter = run_case_with_mem0(
                    case,
                    self.mem0_traces[case.case_id],
                    agent_generate=_stub_agent_generate,
                    scorer=_stub_scorer,
                    on_the_fly_baseline_rescore=True,
                )

                self.assertEqual(
                    standalone.attribution.predicted_label,
                    adapter.attribution.predicted_label,
                )

    def test_letta_adapter_label_parity_under_llm_stack(self) -> None:
        for case in self.cases:
            with self.subTest(case_id=case.case_id):
                standalone = run_case(
                    case,
                    agent_generate=_stub_agent_generate,
                    scorer=_stub_scorer,
                    on_the_fly_baseline_rescore=True,
                )
                adapter = run_case_with_letta(
                    case,
                    self.letta_traces[case.case_id],
                    agent_generate=_stub_agent_generate,
                    scorer=_stub_scorer,
                    on_the_fly_baseline_rescore=True,
                )

                self.assertEqual(
                    standalone.attribution.predicted_label,
                    adapter.attribution.predicted_label,
                )

    def test_cross_adapter_non_regression_under_llm_stack(self) -> None:
        mem0_before = [
            run_case_with_mem0(
                case,
                self.mem0_traces[case.case_id],
                agent_generate=_stub_agent_generate,
                scorer=_stub_scorer,
                on_the_fly_baseline_rescore=True,
            ).attribution.predicted_label
            for case in self.cases
        ]

        _ = [
            run_case_with_letta(
                case,
                self.letta_traces[case.case_id],
                agent_generate=_stub_agent_generate,
                scorer=_stub_scorer,
                on_the_fly_baseline_rescore=True,
            )
            for case in self.cases
        ]

        mem0_after = [
            run_case_with_mem0(
                case,
                self.mem0_traces[case.case_id],
                agent_generate=_stub_agent_generate,
                scorer=_stub_scorer,
                on_the_fly_baseline_rescore=True,
            ).attribution.predicted_label
            for case in self.cases
        ]

        self.assertEqual(mem0_before, mem0_after)


if __name__ == "__main__":
    unittest.main()
