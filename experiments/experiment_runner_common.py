"""Shared helpers for the EXPERIMENT.md runner suite."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA = ROOT / "data" / "probe_cases"
OUT = ROOT / "artifacts" / "sandbox"

AGENT_SYSTEM_PROMPT = """\
You are a memory-augmented QA agent. Answer the user query using only the
provided context. If the context contains enough information, give the concise
answer. Do not mention hidden labels or the evaluation protocol."""


@dataclass(frozen=True)
class CaseWithRaw:
    case: Any
    raw: dict[str, Any]


class AgentGenerateWithLogprobs:
    """Callable agent wrapper that also exposes ``generate_with_logprobs``."""

    def __init__(self, client: Any, *, system_prompt: str) -> None:
        self.client = client
        self.system_prompt = system_prompt

    def __call__(self, query: str, context: str) -> str:
        prompt = "\n\n".join(
            (
                "CONTEXT:",
                context or "(empty)",
                "QUERY:",
                query,
                "ANSWER:",
            )
        )
        return self.client.generate(prompt, system=self.system_prompt)

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        return self.client.generate(prompt, system=system)

    def generate_with_logprobs(
        self,
        prompt: str,
        *,
        system: str | None = None,
        top_logprobs: int = 10,
    ):
        return self.client.generate_with_logprobs(
            prompt,
            system=system,
            top_logprobs=top_logprobs,
        )


@dataclass(frozen=True)
class RubricPair:
    pair_id: str
    fact: str
    text: str
    expected_score: int
    rubric_level: str


RUBRIC_PAIRS = (
    RubricPair(
        "6s-exact",
        "The 6S algorithm is implemented in the SIAC_GEE tool.",
        "The 6S algorithm is implemented in the SIAC_GEE tool.",
        4,
        "exact match",
    ),
    RubricPair(
        "6s-strong",
        "The 6S algorithm is implemented in the SIAC_GEE tool.",
        "SIAC_GEE relies on the 6S algorithm for atmospheric correction.",
        3,
        "strong paraphrase",
    ),
    RubricPair(
        "6s-partial",
        "The 6S algorithm is implemented in the SIAC_GEE tool.",
        "SIAC_GEE is an atmospheric-correction toolbox built for Sentinel-2 imagery.",
        2,
        "partial",
    ),
    RubricPair(
        "6s-vague",
        "The 6S algorithm is implemented in the SIAC_GEE tool.",
        "There are several atmospheric correction algorithms in remote sensing.",
        1,
        "vague allusion",
    ),
    RubricPair(
        "6s-absent",
        "The 6S algorithm is implemented in the SIAC_GEE tool.",
        "I had a sandwich and a coffee for lunch yesterday.",
        0,
        "absent",
    ),
)


def build_evidence_scorer(
    client: Any,
    *,
    scorer_mode: str = "g-eval-hybrid",
    max_workers: int = 4,
    max_retries: int = 1,
):
    from cmd_audit.scoring.llm import RubricScorer, SubagentScorer

    if scorer_mode == "binary":
        return SubagentScorer(
            client,
            max_workers=max_workers,
            max_retries=max_retries,
        )
    rubric = RubricScorer(client, max_workers=max_workers, max_retries=max_retries)
    if scorer_mode == "rubric":
        return rubric
    if scorer_mode in {"g-eval", "g-eval-hybrid", "rubric-continuous"}:
        return rubric.score_continuous
    if scorer_mode == "g-eval-strict":
        return _strict_g_eval_scorer(client)
    raise ValueError(f"unknown scorer mode: {scorer_mode}")


def build_answer_verifier(
    client: Any,
    *,
    answer_mode: str = "answer-rubric",
    max_workers: int = 1,
    max_retries: int = 1,
):
    from cmd_audit.scoring.llm import AnswerRubricScorer, AnswerVerifier, RubricScorer

    del max_workers
    if answer_mode == "binary":
        return AnswerVerifier(client, max_retries=max_retries)
    if answer_mode in {"answer-rubric", "rubric-continuous", "g-eval", "g-eval-hybrid"}:
        return AnswerRubricScorer(client, max_retries=max_retries)
    if answer_mode == "rubric":
        rubric = RubricScorer(client, max_workers=1, max_retries=max_retries)
        return _rubric_answer_callable(rubric)
    if answer_mode == "g-eval-strict":
        return _rubric_answer_callable(_strict_g_eval_scorer(client))
    raise ValueError(f"unknown answer mode: {answer_mode}")


def assert_g_eval_available(client: Any, *, role: str) -> None:
    from cmd_audit.scoring.llm import _continuous_verify

    expected = _continuous_verify(client, "Paris is in France.", "Paris is in France.")
    if expected is None:
        raise RuntimeError(
            f"{role} endpoint did not return parseable G-Eval logprobs."
        )


def write_surrogate_gap_rows(path: str | Path, rows: tuple[Any, ...]) -> None:
    from cmd_audit.eval.writers import write_csv_table

    write_csv_table(
        path,
        [
            "case_id",
            "label",
            "gold_recovery_gain",
            "surrogate_recovery_gain",
            "gap",
            "surrogate_found",
        ],
        [
            {
                "case_id": row.case_id,
                "label": row.label,
                "gold_recovery_gain": f"{row.gold_recovery_gain:.6f}",
                "surrogate_recovery_gain": f"{row.surrogate_recovery_gain:.6f}",
                "gap": f"{row.gap:.6f}",
                "surrogate_found": str(row.surrogate_found).lower(),
            }
            for row in rows
        ],
    )


def _strict_g_eval_scorer(client: Any):
    from cmd_audit.scoring.llm import RUBRIC_MAX_SCORE, _continuous_verify

    def score(gold_evidence, text: str) -> float:
        if not gold_evidence or not text:
            return 0.0
        scores = []
        for evidence in gold_evidence:
            expected = _continuous_verify(client, evidence.text, text)
            if expected is None:
                raise RuntimeError("G-Eval logprob scoring failed")
            scores.append(expected / RUBRIC_MAX_SCORE)
        return sum(scores) / len(scores)

    return score


def _rubric_answer_callable(scorer):
    from cmd_audit.core.models import GoldEvidence

    def score_answer(answer: str, gold_answer: str) -> float:
        evidence = (GoldEvidence("gold_answer", gold_answer),)
        return float(scorer(evidence, answer))

    return score_answer


def load_raw_rows(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("cases", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError(f"{path} must contain a JSON array or a cases array")
    return [row for row in rows if isinstance(row, dict)]


def load_cases_with_raw(path: str | Path) -> list[CaseWithRaw]:
    from cmd_audit.core.models import ProbeCase

    return [CaseWithRaw(ProbeCase.from_mapping(row), row) for row in load_raw_rows(path)]


def action_name(action: Any) -> str | None:
    if action is None:
        return None
    return getattr(action, "value", str(action))


def stable_coin(case_id: str, labels: tuple[str, str]) -> str:
    value = sum(case_id.encode("utf-8"))
    return labels[value % 2]


def source_from_case_id(case_id: str) -> str:
    return case_id.split("-", 1)[0] if "-" in case_id else "unknown"


def target_item_for_case(case: Any):
    memory_by_id = {item.memory_id: item for item in case.extracted_memory}
    for evidence in case.gold_evidence:
        if evidence.source_memory_id in memory_by_id:
            return memory_by_id[evidence.source_memory_id]
    return case.extracted_memory[0] if case.extracted_memory else None


def max_tree_q(search_result: Any) -> float:
    """Return the best positive single-point credit.

    Kept for legacy experiment code that used to inspect the MCTS tree's
    maximum Q value. The live attribution result is tree-free and exposes the
    same decision signal through ``action_credits``.
    """
    best = 0.0
    for credits in getattr(search_result, "action_credits", {}).values():
        for action, credit in credits.items():
            if action_name(action) == "identity":
                continue
            best = max(best, float(credit))
    return best


def run_mcts_for_case(
    case: Any,
    client: Any,
    answer_verifier: Any,
    *,
    max_iterations: int,
    max_depth: int | None = None,
    restrict_to_hop: int | None = None,
    action_priors: dict[str, float] | None = None,
):
    from cmd_audit.harness import _initial_mcts_context, _retrieved_memory_items
    from cmd_audit.counterfactual.search import attribute_single_point

    recall_set = _retrieved_memory_items(case)
    return attribute_single_point(
        client,
        _initial_mcts_context(case, recall_set),
        recall_set,
        case.gold_evidence,
        case.gold_answer,
        max_iterations=max_iterations,
        max_depth=max_depth or max(1, min(3, len(recall_set) or 1)),
        answer_verifier=answer_verifier,
        baseline_answer_score=case.primary_baseline.answer_score,
        intervention_config={"candidate_items": case.extracted_memory},
        restrict_to_hop=restrict_to_hop,
        action_priors=action_priors,
    )


def print_table_written(path: Path) -> None:
    print(f"Wrote {path}")
