"""Concrete dual-score backends for observational arena execution."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import re
from typing import Any, Mapping, Sequence

from cmd_audit.core.models import MemoryItem, RawEvent
from cmd_audit.counterfactual.actions import (
    SINGLE_GENERATION_POINT,
    PipelineAction,
    get_legal_actions,
)
from cmd_audit.counterfactual.operators import (
    OperatorSpec,
    apply_operator_static,
)
from cmd_audit.repair.chain_dynamics import ChainDepositionEvent
from cmd_audit.repair.operator_library import CompositeOperatorSpec
from cmd_audit.repair.skill_ecology import SkillCandidate
from cmd_audit.scoring.llm import score_answer_with_verifier
from experiments.arena_runner_common import (
    ArenaCase,
    DualScoreExecution,
)
from experiments.experiment_runner_common import (
    AGENT_SYSTEM_PROMPT,
    assert_g_eval_available,
    assert_live_llm_env_configured,
    build_answer_verifier,
    build_clients,
)


_logger = logging.getLogger(__name__)

REFERENCE_FREE_RUBRIC_VERSION = "arena-reference-free-v1"
_REFERENCE_FREE_SYSTEM_PROMPT = """\
TASK: Rate the answer quality using only the QUERY and EVIDENCE CONTEXT.

Score on a 0-4 scale:
0 = unsupported, contradictory, irrelevant, or no answer.
1 = weakly related but unsupported by the supplied context.
2 = partially supported; important query requirements are missing.
3 = well supported and answers the main query with minor omissions.
4 = fully supported, directly answers the query, and introduces no
    unsupported factual claims.

Do not assume an external reference answer. Judge grounding, relevance,
completeness, and internal consistency only from the supplied context.

OUTPUT: one JSON object and no prose:
{"reasoning": "<short sentence>", "score": <integer 0..4>}"""
_SCORE_JSON_RE = re.compile(r"\{[^{}]*\"score\"[^{}]*\}", re.DOTALL)


@dataclass(frozen=True)
class RuntimeCaseView:
    """Gold-free subset of an :class:`ArenaCase`."""

    query: str
    recall_set: tuple[MemoryItem, ...]
    candidate_items: tuple[MemoryItem, ...]
    raw_events: tuple[RawEvent, ...]


class ReferenceFreeAnswerScorer:
    """No-reference judge over ``(query, context, answer)``."""

    def __init__(self, judge_client: Any, *, max_retries: int = 1) -> None:
        self.judge_client = judge_client
        self.max_retries = int(max_retries)

    def score(
        self,
        *,
        query: str,
        context: str,
        answer: str,
    ) -> float | None:
        prompt = _reference_free_prompt(
            query=query,
            context=context,
            answer=answer,
        )
        for attempt in range(self.max_retries + 1):
            active_prompt = prompt
            if attempt:
                active_prompt += (
                    '\n\nReturn only {"reasoning":"...", "score":0}. '
                    "Replace 0 with one integer from 0 to 4."
                )
            try:
                response = self.judge_client.generate(
                    active_prompt,
                    system=_REFERENCE_FREE_SYSTEM_PROMPT,
                )
            except Exception as exc:
                _logger.warning("reference-free judge failed: %s", exc)
                continue
            parsed = parse_reference_free_score(response)
            if parsed is not None:
                return parsed / 4.0
        return None


class VLLMDualScoreArenaBackend:
    """Real answerer+judge backend with isolated runtime/shadow scoring.

    Runtime ranking uses only a reference-free judge.  ``gold_answer`` is read
    in :meth:`_shadow_score` and nowhere in candidate retrieval or runtime
    scoring.
    """

    gold_free_signal_name = (
        f"reference_free_grounded_answer_gain:{REFERENCE_FREE_RUBRIC_VERSION}"
    )
    shadow_gold_signal_name = "shadow_gold_answer_rubric_gain"
    runtime_uses_gold = False

    def __init__(
        self,
        *,
        answer_client: Any | None = None,
        judge_client: Any | None = None,
        shadow_verifier: Any | None = None,
        validate_endpoints: bool = True,
        max_reference_free_retries: int = 1,
    ) -> None:
        if answer_client is None or judge_client is None:
            if validate_endpoints:
                assert_live_llm_env_configured()
            built_answer, built_judge = build_clients()
            answer_client = answer_client or built_answer
            judge_client = judge_client or built_judge
        if validate_endpoints:
            assert_g_eval_available(
                judge_client,
                role="observational-arena-shadow-judge",
            )
        self.answer_client = answer_client
        self.judge_client = judge_client
        self.shadow_verifier = shadow_verifier or build_answer_verifier(
            judge_client,
            answer_mode="answer-rubric",
        )
        self.reference_free_scorer = ReferenceFreeAnswerScorer(
            judge_client,
            max_retries=max_reference_free_retries,
        )
        self._runtime_views: dict[str, RuntimeCaseView] = {}
        self._answer_cache: dict[tuple[str, str], str] = {}
        self._runtime_score_cache: dict[tuple[str, str], float | None] = {}
        self._shadow_score_cache: dict[tuple[str, str], float] = {}
        self._deposited: list[ChainDepositionEvent] = []

    @property
    def deposited_events(self) -> tuple[ChainDepositionEvent, ...]:
        return tuple(self._deposited)

    def candidates(self, case: ArenaCase) -> Sequence[SkillCandidate]:
        view = self._runtime_view(case)
        config = {
            "candidate_items": view.candidate_items,
            "raw_events": view.raw_events,
        }
        actions = get_legal_actions(
            view.recall_set,
            SINGLE_GENERATION_POINT,
            include_gated_actions=True,
            include_item_actions=True,
            intervention_config=config,
        )
        candidates = [
            SkillCandidate(
                skill_id=f"seed:{action.value}",
                operator=OperatorSpec.single(
                    SINGLE_GENERATION_POINT,
                    action,
                ),
            )
            for action in actions
            if action != PipelineAction.IDENTITY
        ]
        for event in self._deposited:
            candidates.append(
                SkillCandidate(
                    skill_id=event.composite_skill_id,
                    operator=event.composite_spec,  # staged, not flattened
                )
            )
        # Retrieval is gold-free: structurally applicable operators come first,
        # then stable ids break ties. No label or reference answer is inspected.
        return tuple(
            sorted(
                candidates,
                key=lambda candidate: (
                    -self._structural_activation(case, candidate),
                    candidate.skill_id,
                ),
            )
        )

    def evaluate(
        self,
        case: ArenaCase,
        candidate: SkillCandidate,
        *,
        input_context: str,
        origin_context: str,
    ) -> DualScoreExecution:
        view = self._runtime_view(case)
        try:
            repaired_context = self._apply_candidate(
                candidate,
                input_context=input_context,
                view=view,
            )
            baseline_answer = self._answer(case, origin_context)
            repaired_answer = self._answer(case, repaired_context)
            baseline_runtime = self._runtime_score(
                case,
                context=origin_context,
                answer=baseline_answer,
            )
            repaired_runtime = self._runtime_score(
                case,
                context=repaired_context,
                answer=repaired_answer,
            )
            gold_free_gain = (
                repaired_runtime - baseline_runtime
                if repaired_runtime is not None
                and baseline_runtime is not None
                else None
            )
        except Exception as exc:
            _logger.warning(
                "arena runtime candidate failed case=%s skill=%s: %s",
                case.case_id,
                candidate.skill_id,
                exc,
            )
            return DualScoreExecution(
                skill_id=candidate.skill_id,
                repaired_context=input_context,
                gold_free_gain=None,
                shadow_gold_gain=None,
                execution_cost=0.0,
                status=f"runtime_backend_error:{type(exc).__name__}",
            )

        status = (
            "ok"
            if gold_free_gain is not None
            else "reference_free_score_unavailable"
        )
        shadow_gain: float | None = None
        try:
            # Shadow scoring happens only after runtime scores are materialized.
            baseline_shadow = self._shadow_score(case, baseline_answer)
            repaired_shadow = self._shadow_score(case, repaired_answer)
            shadow_gain = repaired_shadow - baseline_shadow
        except Exception as exc:
            _logger.warning(
                "arena shadow score failed case=%s skill=%s: %s",
                case.case_id,
                candidate.skill_id,
                exc,
            )
            status = f"{status},shadow_error:{type(exc).__name__}"
        return DualScoreExecution(
            skill_id=candidate.skill_id,
            repaired_context=repaired_context,
            gold_free_gain=gold_free_gain,
            shadow_gold_gain=shadow_gain,
            execution_cost=3.0,
            status=status,
        )

    def deposit_composite(self, event: ChainDepositionEvent) -> None:
        if any(
            existing.composite_skill_id == event.composite_skill_id
            for existing in self._deposited
        ):
            return
        self._deposited.append(event)

    def _runtime_view(self, case: ArenaCase) -> RuntimeCaseView:
        cached = self._runtime_views.get(case.case_id)
        if cached is not None:
            return cached
        raw = case.raw
        candidate_items = tuple(
            MemoryItem.from_mapping(dict(item))
            for item in raw.get("extracted_memory", ())
        )
        by_id = {item.memory_id: item for item in candidate_items}
        baselines = raw.get("baseline_outputs") or ()
        baseline = baselines[0] if baselines else {}
        recall_set = tuple(
            by_id[memory_id]
            for memory_id in baseline.get("retrieved_memory_ids", ())
            if memory_id in by_id
        )
        view = RuntimeCaseView(
            query=str(raw.get("query", "")),
            recall_set=recall_set,
            candidate_items=candidate_items,
            raw_events=tuple(
                RawEvent.from_mapping(dict(item))
                for item in raw.get("raw_events", ())
            ),
        )
        self._runtime_views[case.case_id] = view
        return view

    def _structural_activation(
        self,
        case: ArenaCase,
        candidate: SkillCandidate,
    ) -> float:
        view = self._runtime_view(case)
        try:
            repaired = self._apply_candidate(
                candidate,
                input_context=case.base_context,
                view=view,
            )
        except Exception as exc:
            _logger.debug(
                "operator activation failed case=%s skill=%s: %s",
                case.case_id,
                candidate.skill_id,
                exc,
            )
            return -1.0
        if repaired == case.base_context:
            return 0.0
        length = max(1, len(case.base_context), len(repaired))
        prefix = 0
        for left, right in zip(case.base_context, repaired):
            if left != right:
                break
            prefix += 1
        return 1.0 + (length - prefix) / length

    def _apply_candidate(
        self,
        candidate: SkillCandidate,
        *,
        input_context: str,
        view: RuntimeCaseView,
    ) -> str:
        config = {
            "candidate_items": view.candidate_items,
            "raw_events": view.raw_events,
        }
        operator = candidate.operator
        if isinstance(operator, CompositeOperatorSpec):
            current = input_context
            for stage in operator.stages:
                current = apply_operator_static(
                    current,
                    view.recall_set,
                    stage,
                    intervention_config=config,
                )
            return current
        return apply_operator_static(
            input_context,
            view.recall_set,
            operator,
            intervention_config=config,
        )

    def _answer(self, case: ArenaCase, context: str) -> str:
        key = (case.case_id, _hash_text(context))
        cached = self._answer_cache.get(key)
        if cached is not None:
            return cached
        query = self._runtime_view(case).query
        prompt = "\n\n".join(
            (
                "CONTEXT:",
                context or "(empty)",
                "QUERY:",
                query,
                "ANSWER:",
            )
        )
        answer = self.answer_client.generate(
            prompt,
            system=AGENT_SYSTEM_PROMPT,
        )
        self._answer_cache[key] = answer
        return answer

    def _runtime_score(
        self,
        case: ArenaCase,
        *,
        context: str,
        answer: str,
    ) -> float | None:
        key = (case.case_id, _hash_text(context + "\0" + answer))
        if key not in self._runtime_score_cache:
            self._runtime_score_cache[key] = self.reference_free_scorer.score(
                query=self._runtime_view(case).query,
                context=context,
                answer=answer,
            )
        return self._runtime_score_cache[key]

    def _shadow_score(self, case: ArenaCase, answer: str) -> float:
        key = (case.case_id, _hash_text(answer))
        cached = self._shadow_score_cache.get(key)
        if cached is not None:
            return cached
        # This is the only method in the runtime backend that reads gold_answer.
        gold_answer = str(case.raw["gold_answer"])
        score = score_answer_with_verifier(
            self.shadow_verifier,
            answer,
            gold_answer,
        )
        self._shadow_score_cache[key] = score
        return score


def create_vllm_backend(*, cases, args) -> VLLMDualScoreArenaBackend:
    """Default ``arena_cli`` factory for configured OpenAI/vLLM endpoints."""
    del cases, args
    return VLLMDualScoreArenaBackend()


def parse_reference_free_score(response: str) -> int | None:
    text = response.strip()
    payload: Mapping[str, Any] | None = None
    try:
        decoded = json.loads(text)
        payload = decoded if isinstance(decoded, dict) else None
    except json.JSONDecodeError:
        match = _SCORE_JSON_RE.search(text)
        if match:
            try:
                decoded = json.loads(match.group(0))
                payload = decoded if isinstance(decoded, dict) else None
            except json.JSONDecodeError:
                payload = None
    if payload is None or "score" not in payload:
        return None
    try:
        score = int(payload["score"])
    except (TypeError, ValueError):
        return None
    return score if 0 <= score <= 4 else None


def _reference_free_prompt(
    *,
    query: str,
    context: str,
    answer: str,
) -> str:
    return "\n\n".join(
        (
            "QUERY:",
            query,
            "EVIDENCE CONTEXT:",
            context,
            "ANSWER:",
            answer,
        )
    )


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
