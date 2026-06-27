"""Subagent loop orchestrator integrating confidence gate → item gate → attribution.

Implements the subagent loop from DISCUSSION.md:
Hook confidence gate → Tier 2 item gate → Tier 3 pipeline attribution
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..core.models import GoldEvidence, MemoryItem
from ..item_gate import (
    item_signal_hints_from_result,
    run_item_gate,
    order_items_by_experience,
    ItemGateResult,
    ItemGateStatus,
)
from ..counterfactual import attribute_single_point, SearchResult
from .confidence_gate import confidence_gate_hook, ConfidenceGateResult

_logger = logging.getLogger(__name__)


@dataclass
class SubagentLoopResult:
    """Complete result of V2 subagent loop execution."""
    # Hook results
    confidence_result: ConfidenceGateResult
    entered_loop: bool

    # Item gate results (only if entered loop)
    item_gate_result: ItemGateResult | None = None
    item_treatment_needed: bool = False

    # Step-level attribution results (item signals may reorder evidence)
    attribution_result: SearchResult | None = None
    attribution_complete: bool = False

    # Final attribution
    primary_label: str | None = None
    attribution_confidence: float = 0.0

    # Performance metrics
    total_processing_cost: int = 0
    loop_execution_time: float = 0.0

    @property
    def branch_taken(self) -> str:
        """Which branch was taken (fill/fix)."""
        return self.confidence_result.branch

    @property
    def requires_async_fill(self) -> bool:
        """True if Fill branch requires async memory extraction."""
        return self.branch_taken == "fill" and not self.entered_loop

    @property
    def has_item_issues(self) -> bool:
        """True if item gate found content issues."""
        return (
            self.item_gate_result is not None and
            self.item_gate_result.needs_item_treatment
        )

    @property
    def pipeline_attribution_available(self) -> bool:
        """True if pipeline-level attribution was performed."""
        return self.attribution_result is not None and self.attribution_complete


class SubagentLoopOrchestrator:
    """Orchestrates the V2 subagent loop cascade."""

    def __init__(
        self,
        *,
        confidence_threshold: float = 0.6,
        enable_light_corrections: bool = True,
        mcts_max_iterations: int = 50,
        mcts_max_depth: int = 3,
        failure_memory_store: Any = None,
    ):
        self.confidence_threshold = confidence_threshold
        self.enable_light_corrections = enable_light_corrections
        self.mcts_max_iterations = mcts_max_iterations
        self.mcts_max_depth = mcts_max_depth
        self.failure_memory_store = failure_memory_store

    def run_subagent_loop(
        self,
        query: str,
        recall_set: tuple[MemoryItem, ...],
        llm_client: Any,
        *,
        gold_evidence: tuple[GoldEvidence, ...] = (),
        gold_answer: str = "",
        answer_verifier: Any = None,
        failure_memory_store: Any = None,
    ) -> SubagentLoopResult:
        """Run complete V2 subagent loop.

        Args:
            query: User query
            recall_set: Retrieved memory items
            llm_client: LLM client for processing
            gold_evidence: Ground truth evidence (for attribution)
            gold_answer: Ground truth answer (for attribution)
            answer_verifier: Answer verifier for terminal evaluation

        Returns:
            SubagentLoopResult with complete loop execution results
        """
        import time
        start_time = time.time()
        total_cost = 0

        _logger.debug("Starting V2 subagent loop for query: %s", query[:50])
        active_failure_memory_store = (
            failure_memory_store
            if failure_memory_store is not None
            else self.failure_memory_store
        )

        # Step 1: Confidence Gate Hook
        confidence_result = confidence_gate_hook(
            query,
            recall_set,
            confidence_threshold=self.confidence_threshold,
            llm_client=llm_client,
            apply_light_corrections=self.enable_light_corrections,
            failure_memory_store=active_failure_memory_store,
        )

        # Check if we should enter the diagnostic loop
        if not confidence_result.trigger_subagent_loop:
            # High confidence - no diagnosis needed
            _logger.debug("High confidence (%.3f), skipping subagent loop",
                         confidence_result.confidence_score)

            execution_time = time.time() - start_time
            return SubagentLoopResult(
                confidence_result=confidence_result,
                entered_loop=False,
                total_processing_cost=total_cost,
                loop_execution_time=execution_time,
            )

        # Handle Fill branch (evidence missing)
        if confidence_result.should_fill:
            _logger.debug("Fill branch: evidence missing, async re-extraction needed")
            execution_time = time.time() - start_time
            return SubagentLoopResult(
                confidence_result=confidence_result,
                entered_loop=True,
                total_processing_cost=total_cost,
                loop_execution_time=execution_time,
            )

        # Fix branch: Enter diagnostic cascade
        _logger.debug("Fix branch: entering Tier 2-3 diagnostic cascade")

        # Use corrected items if available
        active_recall_set = (
            confidence_result.corrected_items
            if confidence_result.corrected_items
            else recall_set
        )

        # Step 2: Tier 2 Item Gate (for each item in recall set)
        item_gate_result = None
        item_treatment_needed = False

        for target_item in order_items_by_experience(
            query,
            active_recall_set,
            failure_memory_store=active_failure_memory_store,
        ):
            current_item_gate_result = run_item_gate(
                llm_client,
                target_item,
                active_recall_set,
                query,
            )

            total_cost += current_item_gate_result.processing_cost

            if item_gate_result is None:
                item_gate_result = current_item_gate_result

            if current_item_gate_result.needs_item_treatment:
                item_gate_result = current_item_gate_result
                item_treatment_needed = True
                _logger.debug(
                    "Item gate stopped on %s: %s",
                    target_item.memory_id,
                    current_item_gate_result.status,
                )
                break

            _logger.debug(
                "Item gate passed for %s: %s",
                target_item.memory_id,
                current_item_gate_result.status,
            )

        # Step 3: Tier 3 step-level attribution with item signals as hints.
        attribution_result = None
        attribution_complete = False
        item_signal_hints = item_signal_hints_from_result(item_gate_result)
        memory_texts = tuple(item.text for item in active_recall_set)

        _logger.debug("Running Tier 3 step-level attribution")

        try:
            attribution_result = attribute_single_point(
                llm_client,
                f"Query: {query}",  # Initial context
                active_recall_set,
                gold_evidence,
                gold_answer,
                max_iterations=self.mcts_max_iterations,
                max_depth=self.mcts_max_depth,
                answer_verifier=answer_verifier,
                intervention_config={"item_signal_hints": item_signal_hints},
                action_priors=_failure_memory_action_priors(
                    active_failure_memory_store,
                    query,
                    max_depth=self.mcts_max_depth,
                    memory_texts=memory_texts,
                ),
            )
            attribution_complete = True
            _logger.debug("Step-level attribution complete: %s",
                         attribution_result.primary_attribution_label)

        except Exception as exc:
            _logger.error("Step-level attribution failed: %s", exc)

        # Determine final attribution
        primary_label, attribution_confidence = self._determine_final_attribution(
            item_gate_result, attribution_result
        )

        execution_time = time.time() - start_time

        return SubagentLoopResult(
            confidence_result=confidence_result,
            entered_loop=True,
            item_gate_result=item_gate_result,
            item_treatment_needed=item_treatment_needed,
            attribution_result=attribution_result,
            attribution_complete=attribution_complete,
            primary_label=primary_label,
            attribution_confidence=attribution_confidence,
            total_processing_cost=total_cost,
            loop_execution_time=execution_time,
        )

    def _determine_final_attribution(
        self,
        item_gate_result: ItemGateResult | None,
        attribution_result: SearchResult | None,
    ) -> tuple[str | None, float]:
        """Determine final attribution from the step-level recovery result.

        Tier 2 item findings are evidence-ordering hints only. They do not
        produce item-label attribution or override recovered step actions.

        Returns:
            Tuple of (primary_label, confidence)
        """
        del item_gate_result

        # Pipeline attribution if available
        if attribution_result and attribution_result.primary_attribution_label:
            primary_label = attribution_result.primary_attribution_label.value
            confidence = attribution_result.attribution_confidence

            return primary_label, confidence

        # No issues found
        return None, 0.0


def _failure_memory_action_priors(
    failure_memory_store,
    query: str,
    *,
    max_depth: int,
    memory_texts: tuple[str, ...],
):
    if failure_memory_store is None:
        return None
    get_priors = getattr(failure_memory_store, "get_mcts_action_priors", None)
    if not callable(get_priors):
        return None
    return {
        hop: get_priors(
            query,
            hop_index=hop,
            memory_texts=memory_texts,
        )
        for hop in range(1, max_depth + 1)
    }


def run_v2_subagent_loop(
    query: str,
    recall_set: tuple[MemoryItem, ...],
    llm_client: Any,
    *,
    gold_evidence: tuple[GoldEvidence, ...] = (),
    gold_answer: str = "",
    confidence_threshold: float = 0.6,
    answer_verifier: Any = None,
    failure_memory_store: Any = None,
) -> SubagentLoopResult:
    """Convenience function to run V2 subagent loop with default config.

    Args:
        query: User query
        recall_set: Retrieved memory items
        llm_client: LLM client
        gold_evidence: Ground truth evidence
        gold_answer: Ground truth answer
        confidence_threshold: Hook confidence threshold
        answer_verifier: Answer verifier

    Returns:
        SubagentLoopResult with complete execution results
    """
    orchestrator = SubagentLoopOrchestrator(
        confidence_threshold=confidence_threshold,
        failure_memory_store=failure_memory_store,
    )

    return orchestrator.run_subagent_loop(
        query,
        recall_set,
        llm_client,
        gold_evidence=gold_evidence,
        gold_answer=gold_answer,
        answer_verifier=answer_verifier,
        failure_memory_store=failure_memory_store,
    )
