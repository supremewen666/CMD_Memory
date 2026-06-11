"""Leave-One-Out reconstruction for Tier 3 item gate step ③.

Implements LOO reconstruction mechanism: m̂_i = Reconstruct(store without m_i, query)
with directed entailment divergence for wrong/compression_distorted classification.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from ..core.models import MemoryItem
from .divergence import DirectedDivergence, compute_directed_divergence

_logger = logging.getLogger(__name__)


@dataclass
class LOOReconstructionResult:
    """Result of Leave-One-Out reconstruction for one memory item."""
    original_item: MemoryItem
    reconstructed_item: MemoryItem | None
    divergence: DirectedDivergence | None
    reconstruction_successful: bool
    item_label: str | None  # "item_wrong" | "item_compression_distorted" | None

    @property
    def has_wrong_classification(self) -> bool:
        """True if classified as item_wrong (forward divergence dominant)."""
        return self.item_label == "item_wrong"

    @property
    def has_compression_classification(self) -> bool:
        """True if classified as item_compression_distorted (reverse divergence dominant)."""
        return self.item_label == "item_compression_distorted"


def leave_one_out_reconstruct(
    client: Any,
    target_item: MemoryItem,
    memory_store: tuple[MemoryItem, ...],
    query: str,
    *,
    reconstruction_prompt_template: str | None = None,
) -> MemoryItem | None:
    """Reconstruct target item using store without target item.

    Implements m̂_i = Reconstruct(store without m_i, query) from DISCUSSION.md.

    Args:
        client: LLM client for reconstruction generation
        target_item: Item to be reconstructed (excluded from store)
        memory_store: Full memory store including target_item
        query: Original query that would trigger target_item
        reconstruction_prompt_template: Custom template for reconstruction prompt

    Returns:
        Reconstructed MemoryItem or None if reconstruction failed
    """
    if not client:
        _logger.warning("No LLM client available for LOO reconstruction")
        return None

    # Create store without target item: store \ {m_i}
    filtered_store = tuple(
        item for item in memory_store
        if item.memory_id != target_item.memory_id
    )

    if len(filtered_store) == 0:
        _logger.debug("Empty store after filtering target item, cannot reconstruct")
        return None

    # Build reconstruction prompt
    prompt = _build_reconstruction_prompt(
        query, filtered_store, reconstruction_prompt_template
    )

    try:
        # Generate reconstruction
        response = client.generate(prompt)
        if not response or not response.strip():
            _logger.debug("Empty reconstruction response")
            return None

        # Create reconstructed memory item
        reconstructed_item = MemoryItem(
            memory_id=f"loo_reconstructed_{target_item.memory_id}",
            text=response.strip(),
        )

        _logger.debug("LOO reconstruction successful for item %s", target_item.memory_id)
        return reconstructed_item

    except Exception as exc:
        _logger.warning("LOO reconstruction failed for item %s: %s",
                       target_item.memory_id, exc)
    return None


def order_items_by_experience(
    query: str,
    memory_store: tuple[MemoryItem, ...],
    failure_memory_store: Any = None,
) -> tuple[MemoryItem, ...]:
    """Order LOO targets with Failure Memory item-priority hints.

    No item is pruned. A useful prior only moves likely problem items earlier
    so the caller can stop on the first detected item fault.
    """
    if failure_memory_store is None or not memory_store:
        return memory_store

    scorer = getattr(failure_memory_store, "score_item_priority", None)
    if scorer is None:
        return memory_store

    scored: list[tuple[float, int, MemoryItem]] = []
    for index, item in enumerate(memory_store):
        try:
            score = float(scorer(query, item))
        except Exception:
            score = 0.0
        scored.append((score, index, item))

    if not any(score > 0.0 for score, _, _ in scored):
        return memory_store

    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return tuple(item for _, _, item in scored)


def compute_loo_divergence(
    client: Any,
    target_item: MemoryItem,
    memory_store: tuple[MemoryItem, ...],
    query: str,
    *,
    divergence_threshold: float = 0.5,
    reconstruction_prompt_template: str | None = None,
    divergence_fn: Callable[..., DirectedDivergence] | None = None,
) -> LOOReconstructionResult:
    """Compute LOO reconstruction divergence for item classification.

    Performs step ③ of the cost ladder: 1 generation + directed contrast.

    Args:
        client: LLM client for reconstruction and divergence
        target_item: Item to reconstruct and compare
        memory_store: Full memory store including target_item
        query: Original query
        divergence_threshold: Threshold for significant divergence
        reconstruction_prompt_template: Custom reconstruction prompt

    Returns:
        LOOReconstructionResult with classification and divergence
    """
    # Step 1: Reconstruct item using LOO
    reconstructed_item = leave_one_out_reconstruct(
        client, target_item, memory_store, query,
        reconstruction_prompt_template=reconstruction_prompt_template
    )

    if reconstructed_item is None:
        return LOOReconstructionResult(
            original_item=target_item,
            reconstructed_item=None,
            divergence=None,
            reconstruction_successful=False,
            item_label=None,
        )

    # Step 2: Compute directed divergence
    active_divergence_fn = divergence_fn or compute_directed_divergence
    divergence = active_divergence_fn(client, target_item, reconstructed_item)

    # Step 3: Classify based on divergence direction and magnitude
    item_label = _classify_loo_divergence(divergence, divergence_threshold)

    return LOOReconstructionResult(
        original_item=target_item,
        reconstructed_item=reconstructed_item,
        divergence=divergence,
        reconstruction_successful=True,
        item_label=item_label,
    )


def _build_reconstruction_prompt(
    query: str,
    filtered_store: tuple[MemoryItem, ...],
    template: str | None = None,
) -> str:
    """Build prompt for LOO reconstruction generation.

    Args:
        query: Original query that would use the target item
        filtered_store: Memory store without target item
        template: Custom prompt template (uses default if None)

    Returns:
        Formatted reconstruction prompt
    """
    if template is None:
        template = _DEFAULT_RECONSTRUCTION_TEMPLATE

    # Format available memory items
    available_items = []
    for i, item in enumerate(filtered_store, 1):
        available_items.append(f"{i}. {item.text}")

    available_context = "\n".join(available_items) if available_items else "(no available items)"

    return template.format(
        query=query,
        available_memory=available_context,
    )


def _classify_loo_divergence(
    divergence: DirectedDivergence,
    threshold: float,
) -> str | None:
    """Classify LOO divergence into item labels.

    Based on DISCUSSION.md classification:
    - Forward divergence large → item_wrong (reconstructed more specific)
    - Reverse divergence large → item_compression_distorted (original more compressed)
    - Both small → None (consistent)

    Args:
        divergence: Computed directed divergence
        threshold: Minimum divergence for classification

    Returns:
        Item label string or None if no significant divergence
    """
    if divergence.max_divergence <= threshold:
        return None

    if divergence.is_forward_dominant:
        return "item_wrong"
    elif divergence.is_reverse_dominant:
        return "item_compression_distorted"
    else:
        # Edge case: equal divergence, conservative tie-break
        return None


_DEFAULT_RECONSTRUCTION_TEMPLATE = """\
TASK: Reconstruct the missing memory item that would best answer the given query.

QUERY: {query}

AVAILABLE MEMORY:
{available_memory}

Based on the available memory items and the query, reconstruct what the missing memory item should contain. Focus on the specific information needed to answer the query that isn't directly available in the existing items.

RECONSTRUCTION:"""
