"""Concrete dual-score backends for observational arena execution."""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import logging
import re
from threading import Lock
from typing import Any, Mapping, Sequence

from cmd_audit.core.llm_client import LLMResponse
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
from cmd_audit.scoring.llm import (
    RUBRIC_MAX_SCORE,
    _expected_score_from_logprobs,
    _find_score_digit_logprobs,
    score_answer_with_verifier,
)
from experiments.arena_runner_common import (
    ArenaCase,
    BestOfNControlExecution,
    ContextStuffingExecution,
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

#: Pipeline and item actions that correspond to real CC memory system failures.
#: ``item_wrong``, ``item_compression_distorted``, and ``item_poisoned`` are
#: CMD-internal experimental operators with no real-system failure mode; they
#: are excluded from the arena to keep the skill set aligned with MemTrace-B
#: and real-world CC memory frontmatter (timestamps, provenance, conflicts).
_ARENA_ACTION_WHITELIST: frozenset[str] = frozenset(
    {
        "retrieval_error",
        "injection_error",
        "granularity_error",
        "safety_error",
        "item_stale",
        "item_conflict",
    }
)

#: Frozen token policy for the named context-stuffing baseline. Whitespace
#: tokens rather than model tokens, so the cap is recomputable from the artifact
#: without depending on which answerer's tokenizer was in use. The cap sits
#: below the 8192-token serving limit with room for the prompt scaffold and the
#: generated answer.
CONTEXT_STUFFING_TOKEN_POLICY = "whitespace_token_cap"
CONTEXT_STUFFING_TOKEN_BUDGET = 4000

REFERENCE_FREE_RUBRIC_VERSION = "arena-reference-free-v2"
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
        continuous = self._score_continuous(prompt)
        if continuous is not None:
            return continuous
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

    def _score_continuous(self, prompt: str) -> float | None:
        """Return logprob G-Eval expectation, or ``None`` for fallback."""
        if not hasattr(self.judge_client, "generate_with_logprobs"):
            return None
        try:
            response = self.judge_client.generate_with_logprobs(
                prompt,
                system=_REFERENCE_FREE_SYSTEM_PROMPT,
                top_logprobs=10,
            )
        except Exception as exc:
            _logger.warning(
                "reference-free judge logprob call failed; using fallback: %s",
                exc,
            )
            return None
        if not isinstance(response, LLMResponse) or not response.token_logprobs:
            return None
        digits = _find_score_digit_logprobs(response.token_logprobs)
        if not digits:
            return None
        try:
            return _expected_score_from_logprobs(digits) / RUBRIC_MAX_SCORE
        except ValueError:
            return None


class VLLMDualScoreArenaBackend:
    """Real answerer plus isolated selection/evaluation judges.

    Runtime ranking uses the answerer endpoint as a reference-free selection
    judge. The frozen evaluation judge is used only for shadow scoring.
    ``gold_answer`` is read in :meth:`_shadow_score` and nowhere in candidate
    retrieval or runtime scoring.
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
        selection_judge_client: Any | None = None,
        shadow_verifier: Any | None = None,
        validate_endpoints: bool = True,
        max_reference_free_retries: int = 1,
        enable_shadow_scoring: bool = True,
    ) -> None:
        if answer_client is None or judge_client is None:
            if validate_endpoints:
                assert_live_llm_env_configured(
                    roles=("answer", "judge")
                    if enable_shadow_scoring
                    else ("answer",)
                )
            built_answer, built_judge = build_clients()
            answer_client = answer_client or built_answer
            judge_client = judge_client or built_judge
        # The answerer endpoint doubles as the reference-free *selection*
        # judge.  The frozen judge endpoint is reserved for shadow evaluation.
        # This keeps runtime argmax and reported outcome measurement from
        # optimizing the same judge signal.
        selection_judge_client = selection_judge_client or answer_client
        if validate_endpoints and enable_shadow_scoring:
            _assert_distinct_judge_identities(
                selection_judge_client,
                judge_client,
            )
            assert_g_eval_available(
                judge_client,
                role="observational-arena-shadow-judge",
            )
        self.answer_client = answer_client
        self.selection_judge_client = selection_judge_client
        self.judge_client = judge_client
        self.enable_shadow_scoring = bool(enable_shadow_scoring)
        self.shadow_verifier = None
        if self.enable_shadow_scoring:
            self.shadow_verifier = shadow_verifier or build_answer_verifier(
                judge_client,
                answer_mode="answer-rubric",
            )
        self.reference_free_scorer = ReferenceFreeAnswerScorer(
            selection_judge_client,
            max_retries=max_reference_free_retries,
        )
        self._runtime_views: dict[str, RuntimeCaseView] = {}
        self._answer_cache: dict[tuple[str, str], str] = {}
        self._runtime_score_cache: dict[tuple[str, str], float | None] = {}
        self._shadow_score_cache: dict[tuple[str, str], float] = {}
        self._deposited: list[ChainDepositionEvent] = []
        self._composite_lifecycle: dict[str, tuple[str, float]] = {}
        self._cache_lock_guard = Lock()
        self._cache_locks: dict[tuple[str, object], Lock] = {}
        self._counter_guard = Lock()
        self._cmd_call_counts: dict[str, list[int]] = {}

    @property
    def deposited_events(self) -> tuple[ChainDepositionEvent, ...]:
        return tuple(self._deposited)

    #: Declared on the backend so the manifest records the policy actually in
    #: force rather than a constant the runner hopes matches.
    context_stuffing_token_policy = CONTEXT_STUFFING_TOKEN_POLICY
    context_stuffing_token_budget = CONTEXT_STUFFING_TOKEN_BUDGET

    @property
    def selection_judge_identity(self) -> str:
        return _client_identity(self.selection_judge_client)

    @property
    def evaluation_judge_identity(self) -> str:
        return _client_identity(self.judge_client)

    def cmd_call_counts(self, case: ArenaCase) -> tuple[int, int]:
        """Actual candidate answer/selection API attempts for one case."""
        with self._counter_guard:
            counts = self._cmd_call_counts.get(case.case_id, [0, 0])
            return counts[0], counts[1]

    def answer_context(
        self,
        case: ArenaCase,
        context: str,
        *,
        purpose: str = "benchmark_control",
    ) -> str:
        """Generate one answer for a frozen control context without scoring."""
        return self._answer(case, context, purpose=purpose)

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
            and action.value in _ARENA_ACTION_WHITELIST
        ]
        for event in self._deposited:
            lifecycle, _eta = self._composite_lifecycle.get(
                event.composite_skill_id,
                ("probation", 0.5),
            )
            if lifecycle == "retired":
                continue
            candidates.append(
                SkillCandidate(
                    skill_id=event.composite_skill_id,
                    operator=event.composite_spec,  # staged, not flattened
                )
            )
        # V2 evaluates every legal operator. Stable ids make invocation order
        # deterministic without using labels, gold answers, or length proxies.
        return tuple(
            sorted(
                candidates,
                key=lambda candidate: (
                    self._candidate_lifecycle_rank(candidate.skill_id),
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
            baseline_answer = self._answer(
                case,
                origin_context,
                purpose="baseline",
            )
            repaired_answer = self._answer(
                case,
                repaired_context,
                purpose="cmd_candidate",
            )
            baseline_runtime = self._runtime_score(
                case,
                context=origin_context,
                answer=baseline_answer,
                purpose="baseline",
            )
            repaired_runtime = self._runtime_score(
                case,
                context=repaired_context,
                answer=repaired_answer,
                purpose="cmd_candidate",
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
            if self.enable_shadow_scoring:
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
            baseline_hypothesis=baseline_answer,
            repaired_hypothesis=repaired_answer,
        )

    def deposit_composite(self, event: ChainDepositionEvent) -> None:
        if any(
            existing.composite_skill_id == event.composite_skill_id
            for existing in self._deposited
        ):
            return
        self._deposited.append(event)
        self._composite_lifecycle[event.composite_skill_id] = (
            event.lifecycle_status,
            0.5,
        )

    def update_composite_lifecycle(
        self,
        composite_skill_id: str,
        *,
        status: str,
        eta: float,
    ) -> None:
        if status not in {"probation", "active", "retired"}:
            raise ValueError("invalid composite lifecycle status")
        self._composite_lifecycle[str(composite_skill_id)] = (
            str(status),
            float(eta),
        )

    def _candidate_lifecycle_rank(self, skill_id: str) -> tuple[int, float]:
        status, eta = self._composite_lifecycle.get(
            skill_id,
            ("active", 1.0),
        )
        return (1 if status == "probation" else 0, -eta)

    def confirm_composite(
        self,
        case: ArenaCase,
        candidate: SkillCandidate,
    ) -> tuple[DualScoreExecution, int]:
        """Fresh, separately-budgeted D2 replay for one composite."""
        view = self._runtime_view(case)
        calls = 0
        try:
            repaired_context = self._apply_candidate(
                candidate,
                input_context=case.base_context,
                view=view,
            )
            baseline_answer = self._answer(
                case,
                case.base_context,
                purpose="baseline",
            )
            baseline_runtime = self._runtime_score(
                case,
                context=case.base_context,
                answer=baseline_answer,
                purpose="baseline",
            )
            prompt = "\n\n".join(
                (
                    "CONTEXT:",
                    repaired_context or "(empty)",
                    "QUERY:",
                    view.query,
                    "ANSWER:",
                )
            )
            calls += 1
            repaired_answer = self.answer_client.generate(
                prompt,
                system=AGENT_SYSTEM_PROMPT,
            )
            calls += 1
            repaired_runtime = self.reference_free_scorer.score(
                query=view.query,
                context=repaired_context,
                answer=repaired_answer,
            )
            gold_free_gain = (
                float(repaired_runtime) - float(baseline_runtime)
                if repaired_runtime is not None and baseline_runtime is not None
                else None
            )
            baseline_shadow = self._shadow_score(case, baseline_answer)
            calls += 1
            repaired_shadow = score_answer_with_verifier(
                self.shadow_verifier,
                repaired_answer,
                str(case.raw["gold_answer"]),
            )
            shadow_gain = repaired_shadow - baseline_shadow
            return (
                DualScoreExecution(
                    skill_id=candidate.skill_id,
                    repaired_context=repaired_context,
                    gold_free_gain=gold_free_gain,
                    shadow_gold_gain=shadow_gain,
                    execution_cost=3.0,
                ),
                calls,
            )
        except Exception as exc:
            _logger.warning(
                "deposition confirmation failed case=%s skill=%s: %s",
                case.case_id,
                candidate.skill_id,
                exc,
            )
            return (
                DualScoreExecution(
                    skill_id=candidate.skill_id,
                    repaired_context=case.base_context,
                    gold_free_gain=None,
                    shadow_gold_gain=None,
                    execution_cost=float(calls),
                    status=f"confirmation_error:{type(exc).__name__}",
                ),
                calls,
            )

    def evaluate_best_of_n(
        self,
        case: ArenaCase,
        *,
        candidate_count: int,
        origin_context: str,
    ) -> BestOfNControlExecution:
        """Same-budget generic answer search without CMD routing/operators.

        The control sees an unstructured information superset (origin context
        plus every candidate item), generates ``N`` independent answer
        candidates, and uses the same reference-free selection scorer as the
        CMD arm. Only the selected answer reaches the frozen shadow evaluator.
        """
        if candidate_count <= 0:
            raise ValueError("candidate_count must be > 0")
        view = self._runtime_view(case)
        pool_context = _unstructured_pool_context(
            origin_context,
            view.candidate_items,
        )
        baseline_answer = self._answer(case, origin_context, purpose="baseline")
        baseline_runtime = self._runtime_score(
            case,
            context=origin_context,
            answer=baseline_answer,
            purpose="baseline",
        )
        scored: list[tuple[float, int, str]] = []
        answer_calls = 0
        selection_calls = 0
        for index in range(candidate_count):
            prompt = "\n\n".join(
                (
                    "UNSTRUCTURED BEST-OF-N CONTROL.",
                    "Do not diagnose a pipeline action or use a repair taxonomy.",
                    f"CANDIDATE INDEX: {index + 1}/{candidate_count}",
                    "CONTEXT AND FLAT MEMORY POOL:",
                    pool_context,
                    "QUERY:",
                    view.query,
                    "Produce one independent candidate answer.",
                    "ANSWER:",
                )
            )
            try:
                answer_calls += 1
                answer = self.answer_client.generate(
                    prompt,
                    system=AGENT_SYSTEM_PROMPT,
                )
                selection_calls += 1
                score = self.reference_free_scorer.score(
                    query=view.query,
                    context=pool_context,
                    answer=answer,
                )
            except Exception as exc:
                _logger.warning(
                    "best-of-N candidate failed case=%s index=%s: %s",
                    case.case_id,
                    index,
                    exc,
                )
                continue
            if score is not None:
                scored.append((float(score), index, answer))

        if not scored or baseline_runtime is None:
            return BestOfNControlExecution(
                candidate_count=candidate_count,
                finite_candidate_count=len(scored),
                selected_index=None,
                selection_gain=None,
                shadow_gold_gain=None,
                answer_calls=answer_calls,
                selection_judge_calls=selection_calls,
                status="selection_score_unavailable",
            )
        best_score, best_index, best_answer = min(
            scored,
            key=lambda row: (-row[0], row[1]),
        )
        selection_gain = best_score - baseline_runtime
        if len(scored) != candidate_count:
            return BestOfNControlExecution(
                candidate_count=candidate_count,
                finite_candidate_count=len(scored),
                selected_index=None,
                selection_gain=selection_gain,
                shadow_gold_gain=None,
                answer_calls=answer_calls,
                selection_judge_calls=selection_calls,
                status="partial_selection_score_unavailable",
            )
        if selection_gain <= 0.0:
            return BestOfNControlExecution(
                candidate_count=candidate_count,
                finite_candidate_count=len(scored),
                selected_index=None,
                selection_gain=selection_gain,
                shadow_gold_gain=None,
                answer_calls=answer_calls,
                selection_judge_calls=selection_calls,
                status="abstained_nonpositive_gain",
                abstained=True,
            )
        try:
            baseline_shadow = self._shadow_score(case, baseline_answer)
            selected_shadow = self._shadow_score(case, best_answer)
        except Exception as exc:
            _logger.warning(
                "best-of-N shadow evaluation failed case=%s: %s",
                case.case_id,
                exc,
            )
            return BestOfNControlExecution(
                candidate_count=candidate_count,
                finite_candidate_count=len(scored),
                selected_index=best_index,
                selection_gain=selection_gain,
                shadow_gold_gain=None,
                answer_calls=answer_calls,
                selection_judge_calls=selection_calls,
                status=f"shadow_evaluation_failed:{type(exc).__name__}",
            )
        return BestOfNControlExecution(
            candidate_count=candidate_count,
            finite_candidate_count=len(scored),
            selected_index=best_index,
            selection_gain=selection_gain,
            shadow_gold_gain=selected_shadow - baseline_shadow,
            answer_calls=answer_calls,
            selection_judge_calls=selection_calls,
        )

    def evaluate_context_stuffing(
        self,
        case: ArenaCase,
        *,
        origin_context: str,
        token_budget: int = CONTEXT_STUFFING_TOKEN_BUDGET,
    ) -> ContextStuffingExecution:
        """No-search baseline: stuff the whole pool in, answer once.

        Best-of-N already sees the same flat pool, so what this isolates is
        whether the pool *alone* recovers the answer -- no routing, no
        operators, no selection over candidates. It therefore spends exactly one
        answer call and zero selection-judge calls, and reports its own gain
        against the same unrepaired baseline the other arms use.

        Items are admitted in order under a frozen whitespace-token cap. When
        the pool does not fit, the arm says so rather than quietly becoming a
        different baseline on the cases where stuffing overflows.
        """
        view = self._runtime_view(case)
        included, truncated = _fit_items_to_token_budget(
            view.candidate_items,
            origin_context,
            token_budget,
        )
        stuffed_context = _unstructured_pool_context(origin_context, included)
        prompt = "\n\n".join(
            (
                "CONTEXT STUFFING BASELINE.",
                "Do not diagnose a pipeline action or use a repair taxonomy.",
                "Every retrieved item is present; none has been ranked,"
                " filtered, or repaired.",
                "CONTEXT AND FLAT MEMORY POOL:",
                stuffed_context,
                "QUERY:",
                view.query,
                "ANSWER:",
            )
        )
        policy = ContextStuffingExecution(
            shadow_gold_gain=None,
            answer_calls=0,
            selection_judge_calls=0,
            token_policy=CONTEXT_STUFFING_TOKEN_POLICY,
            token_budget=token_budget,
            items_offered=len(view.candidate_items),
            items_included=len(included),
            truncated=truncated,
        )
        try:
            answer = self.answer_client.generate(
                prompt,
                system=AGENT_SYSTEM_PROMPT,
            )
        except Exception as exc:
            _logger.warning(
                "context-stuffing answer failed case=%s: %s",
                case.case_id,
                exc,
            )
            return replace(
                policy,
                answer_calls=1,
                status=f"answer_failed:{type(exc).__name__}",
            )
        baseline_answer = self._answer(case, origin_context, purpose="baseline")
        try:
            baseline_shadow = self._shadow_score(case, baseline_answer)
            stuffed_shadow = self._shadow_score(case, answer)
        except Exception as exc:
            _logger.warning(
                "context-stuffing shadow evaluation failed case=%s: %s",
                case.case_id,
                exc,
            )
            return replace(
                policy,
                answer_calls=1,
                status=f"shadow_evaluation_failed:{type(exc).__name__}",
            )
        return replace(
            policy,
            answer_calls=1,
            shadow_gold_gain=stuffed_shadow - baseline_shadow,
        )

    def _runtime_view(self, case: ArenaCase) -> RuntimeCaseView:
        cached = self._runtime_views.get(case.case_id)
        if cached is not None:
            return cached
        with self._cache_lock("runtime_view", case.case_id):
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

    def _answer(
        self,
        case: ArenaCase,
        context: str,
        *,
        purpose: str = "unspecified",
    ) -> str:
        key = (case.case_id, _hash_text(context))
        cached = self._answer_cache.get(key)
        if cached is not None:
            return cached
        with self._cache_lock("answer", key):
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
            if purpose == "cmd_candidate":
                self._increment_cmd_call(case.case_id, answer_calls=1)
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
        purpose: str = "unspecified",
    ) -> float | None:
        key = (case.case_id, _hash_text(context + "\0" + answer))
        if key in self._runtime_score_cache:
            return self._runtime_score_cache[key]
        with self._cache_lock("runtime_score", key):
            if key not in self._runtime_score_cache:
                if purpose == "cmd_candidate":
                    self._increment_cmd_call(
                        case.case_id,
                        selection_calls=1,
                    )
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
        with self._cache_lock("shadow_score", key):
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

    def _cache_lock(self, namespace: str, key: object) -> Lock:
        lock_key = (namespace, key)
        with self._cache_lock_guard:
            lock = self._cache_locks.get(lock_key)
            if lock is None:
                lock = Lock()
                self._cache_locks[lock_key] = lock
            return lock

    def _increment_cmd_call(
        self,
        case_id: str,
        *,
        answer_calls: int = 0,
        selection_calls: int = 0,
    ) -> None:
        with self._counter_guard:
            counts = self._cmd_call_counts.setdefault(case_id, [0, 0])
            counts[0] += int(answer_calls)
            counts[1] += int(selection_calls)


def create_vllm_backend(*, cases, args) -> VLLMDualScoreArenaBackend:
    """Default ``arena_cli`` factory for configured OpenAI/vLLM endpoints."""
    del cases, args
    return VLLMDualScoreArenaBackend()


def _assert_distinct_judge_identities(selection_client: Any, evaluation_client: Any) -> None:
    """Reject a circular arena where selection and evaluation share a judge."""
    if selection_client is evaluation_client:
        raise ValueError("arena selection judge and evaluation judge must differ")
    selection_config = getattr(selection_client, "config", None)
    evaluation_config = getattr(evaluation_client, "config", None)
    if selection_config is None or evaluation_config is None:
        return
    selection_identity = (
        str(getattr(selection_config, "base_url", "")).rstrip("/"),
        str(getattr(selection_config, "model", "")),
    )
    evaluation_identity = (
        str(getattr(evaluation_config, "base_url", "")).rstrip("/"),
        str(getattr(evaluation_config, "model", "")),
    )
    same_model = (
        bool(selection_identity[1])
        and selection_identity[1] == evaluation_identity[1]
    )
    if selection_identity == evaluation_identity or same_model:
        raise ValueError(
            "arena selection judge and evaluation judge resolve to the same "
            f"model identity: selection={selection_identity!r}, "
            f"evaluation={evaluation_identity!r}"
        )


def _client_identity(client: Any) -> str:
    config = getattr(client, "config", None)
    if config is None:
        return type(client).__name__
    return (
        f"{str(getattr(config, 'base_url', '')).rstrip('/')}|"
        f"{getattr(config, 'model', '')}"
    )


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


def _fit_items_to_token_budget(
    items: Sequence[MemoryItem],
    origin_context: str,
    token_budget: int,
) -> tuple[list[MemoryItem], bool]:
    """Admit items in retrieval order until the token cap is reached.

    Whitespace tokens, not model tokens: the cap has to be reproducible from
    the artifact alone, without a tokenizer that varies by answerer.
    """
    spent = len(origin_context.split())
    included: list[MemoryItem] = []
    for item in items:
        cost = len(item.text.split())
        if spent + cost > token_budget:
            return included, True
        spent += cost
        included.append(item)
    return included, False


def _unstructured_pool_context(
    origin_context: str,
    items: Sequence[MemoryItem],
) -> str:
    flat_pool = "\n".join(
        f"- [{item.memory_id}] {item.text}" for item in items
    ) or "(empty)"
    return "\n\n".join(
        (
            origin_context,
            "FLAT MEMORY POOL (no routing, action, or item-gate labels):",
            flat_pool,
        )
    )


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
