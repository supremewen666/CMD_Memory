"""RepairExecutor — Issue 0020-A.

Stateless single-repair execution unit:
  ECS draft → build repair context → Post-Repair Context Replay → assessment.
"""

from __future__ import annotations

from dataclasses import dataclass

from .failure_memory import build_repair_context
from .post_repair import (
    RepairedContext,
    run_post_repair_context_replay,
)
from .repairs import (
    REPAIR_ACTION_SYSTEM_PROMPT,
    RepairAction,
    RepairActionOutputError,
    UnsupportedActionError,
    build_repair_action_prompt,
    parse_repair_action_response,
)


@dataclass(frozen=True)
class RepairExecutorResult:
    """Result of a single RepairExecutor run."""

    assessment: str
    post_repair_answer_score: float
    post_repair_evidence_score: float
    applied_action: RepairAction | None
    repair_context: str
    label: str
    action_selection_source: str = ""
    action_selection_error: str = ""
    action_skipped_reason: str | None = None


class RepairExecutor:
    """Stateless single-repair execution unit.

    Does NOT loop — one ECS draft → one repair → one Post-Repair result.
    """

    def __init__(
        self,
        *,
        llm_client=None,
        require_llm_action: bool = False,
    ) -> None:
        self._llm_client = llm_client
        self._require_llm_action = require_llm_action

    def run(
        self,
        *,
        ecs_draft,
        adapter,
        case,
        fm_context: str = "",
    ) -> RepairExecutorResult:
        """Execute a single repair from an ECS draft.

        Args:
            ecs_draft: The ECSDraft with label, corrected_memory, etc.
            adapter: CMD-Skill Adapter with supported_actions and apply_repair.
            case: ProbeCase for Post-Repair Context Replay scoring.
            fm_context: Failure Memory diagnostic context (wrong_memory + evidence).

        Returns:
            RepairExecutorResult with assessment and scores.
        """
        baseline_context = case.primary_baseline.injected_context
        label = ecs_draft.predicted_label
        evidence_block = ecs_draft.repaired_evidence_block

        # Build full repair context: baseline + label + evidence + fm_context
        repair_context = build_repair_context(
            baseline_context=baseline_context,
            label=label,
            evidence_block=evidence_block,
            fm_context=fm_context,
        )

        try:
            action, action_source, action_error = self._build_repair_action(
                ecs_draft=ecs_draft,
                adapter=adapter,
                case=case,
                fm_context=fm_context,
                evidence_block=evidence_block,
            )
        except RepairActionOutputError as exc:
            return RepairExecutorResult(
                assessment="action_selection_failed",
                post_repair_answer_score=0.0,
                post_repair_evidence_score=0.0,
                applied_action=None,
                repair_context=repair_context,
                label=label,
                action_selection_source="llm_error",
                action_selection_error=str(exc),
            )

        # Apply repair through adapter (sandboxed)
        try:
            adapter.apply_repair(action)
            applied_action = action
            action_skipped_reason = None
        except UnsupportedActionError as exc:
            # Adapter does not support this action type — proceed without apply
            applied_action = None
            action_skipped_reason = str(exc)

        # Run Post-Repair Context Replay with the repair context
        repaired_ctx = RepairedContext(
            case_id=case.case_id,
            corrected_memory=ecs_draft.corrected_memory,
            repair_guidance=ecs_draft.repair_guidance,
            repaired_evidence_block=evidence_block,
            original_query=case.query,
            fm_context=fm_context,
        )
        post_repair = run_post_repair_context_replay(case, repaired_ctx)

        return RepairExecutorResult(
            assessment=post_repair.repair_assessment,
            post_repair_answer_score=post_repair.post_repair_answer_score,
            post_repair_evidence_score=post_repair.post_repair_evidence_score,
            applied_action=applied_action,
            repair_context=repair_context,
            label=label,
            action_selection_source=action_source,
            action_selection_error=action_error,
            action_skipped_reason=action_skipped_reason,
        )

    def _build_repair_action(
        self,
        *,
        ecs_draft,
        adapter,
        case,
        fm_context: str,
        evidence_block: str,
    ) -> tuple[RepairAction, str, str]:
        if self._llm_client is not None:
            try:
                prompt = build_repair_action_prompt(
                    label=ecs_draft.predicted_label,
                    evidence_block=evidence_block,
                    fm_context=fm_context,
                    supported_actions=adapter.supported_actions,
                    target_store=case.default_store,
                    content=ecs_draft.corrected_memory,
                    repair_guidance=ecs_draft.repair_guidance,
                )
                response = self._llm_client.generate(
                    prompt, system=REPAIR_ACTION_SYSTEM_PROMPT
                )
                action = parse_repair_action_response(
                    response,
                    supported_actions=adapter.supported_actions,
                    expected_label=ecs_draft.predicted_label,
                )
                return action, "llm", ""
            except Exception as exc:
                if self._require_llm_action:
                    raise RepairActionOutputError(str(exc)) from exc
                action = self._heuristic_action(ecs_draft, adapter, case)
                return action, "heuristic_fallback", str(exc)

        return self._heuristic_action(ecs_draft, adapter, case), "heuristic", ""

    def _heuristic_action(self, ecs_draft, adapter, case) -> RepairAction:
        action_type = self._select_action_type(
            ecs_draft.predicted_label, adapter.supported_actions
        )
        return RepairAction(
            action_type=action_type,
            target_item_id=None,
            target_store=case.default_store,
            content=ecs_draft.corrected_memory,
            label=ecs_draft.predicted_label,
            reasoning=f"Repair for {ecs_draft.predicted_label}: {ecs_draft.repair_guidance}",
        )

    @staticmethod
    def _select_action_type(label: str, supported_actions: tuple[str, ...]) -> str:
        """Heuristic action type selection based on label and adapter support.

        Offline fallback when LLM is not available. Maps common labels to
        reasonable default actions.
        """
        label_to_action: dict[str, str] = {
            "write_error": "append",
            "compression_error": "replace",
            "premature_extraction_error": "append",
            "retrieval_error": "update_routing",
            "injection_error": "replace",
            "reasoning_error": "update_template",
            "ingestion_error": "append",
            "route_error": "update_routing",
            "granularity_error": "replace",
            "graph_error": "update_routing",
            "safety_error": "update_template",
        }
        preferred = label_to_action.get(label, "append")
        if preferred in supported_actions:
            return preferred
        # Fallback to first supported action
        return supported_actions[0] if supported_actions else "append"
