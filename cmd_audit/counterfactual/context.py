"""Context helpers shared by counterfactual attribution probes."""
from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)


def generate_conditioned_context(
    client: Any,
    context: str,
    generation_point: int,
) -> str:
    """Re-run one generation point under the already-applied intervention."""
    if client is None or not hasattr(client, "generate"):
        return context

    prompt = f"""Continue the trajectory for generation point {generation_point}.

Use the current counterfactual context exactly as the state so far. Generate only
the next reasoning/output prefix for this generation point.

CURRENT CONTEXT:
{context}

NEXT PREFIX:"""
    try:
        generated = client.generate(prompt)
    except Exception as exc:
        _logger.warning("Conditioned generation failed: %s", exc)
        return context
    if not isinstance(generated, str) or not generated.strip():
        return context
    return f"{context.rstrip()}\n\nGenerated prefix {generation_point}:\n{generated.strip()}"
