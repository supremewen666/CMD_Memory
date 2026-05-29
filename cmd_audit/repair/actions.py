"""Targeted memory repair actions — Issue 0006."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from ..core.labels import PIPELINE_LABEL_ORDER, validate_label_base, validate_label
from .post_repair import PostRepairResult
from ..eval.writers import write_csv_table, write_text_artifact


@dataclass(frozen=True)
class TargetedRepairAction:
    label: str
    action_name: str
    description: str
    intervention_summary: str
    cause: str = ""
    repair_guidance: str = ""

    def __post_init__(self) -> None:
        validate_label(self.label)


REPAIR_ACTION_BY_LABEL: dict[str, TargetedRepairAction] = {
    "write_error": TargetedRepairAction(
        label="write_error",
        action_name="Oracle Write Repair",
        description="Inject gold evidence directly into memory as a newly written item.",
        intervention_summary="Replay with Oracle Write recovers evidence that was never written.",
        cause="no recoverable evidence found in extracted memory; the failure "
        "may originate at or before the write step",
        repair_guidance="ensure events are written to memory and evidence is preserved "
        "through the pipeline",
    ),
    "compression_error": TargetedRepairAction(
        label="compression_error",
        action_name="Oracle Compression Repair",
        description="Replace lossy-compressed memory with an evidence-preserving representation.",
        intervention_summary="Replay with Oracle Compression recovers evidence lost during compression.",
        cause="lossy compression removed key evidence that was present in the "
        "original memory item",
        repair_guidance="reduce compression aggressiveness or preserve key evidence "
        "phrases during compression",
    ),
    "premature_extraction_error": TargetedRepairAction(
        label="premature_extraction_error",
        action_name="Verbatim Event Repair",
        description="Extract raw event evidence into a new memory item before it is abstracted away.",
        intervention_summary="Replay with Verbatim Event Oracle recovers evidence lost during extraction.",
        cause="key evidence was present in raw events but was not preserved "
        "in any extracted memory item",
        repair_guidance="improve extraction to preserve evidence from raw events into memory items",
    ),
    "retrieval_error": TargetedRepairAction(
        label="retrieval_error",
        action_name="Oracle Retrieval Repair",
        description="Ensure the correct memory item is retrieved and surfaced in context.",
        intervention_summary="Replay with Oracle Retrieval recovers evidence that was present but not retrieved.",
        cause="retrieved context did not include the correct memory item "
        "even though the item was present in extracted memory",
        repair_guidance="update retrieval routing to include the corrected memory item",
    ),
    "injection_error": TargetedRepairAction(
        label="injection_error",
        action_name="Injection Oracle Repair",
        description="Reformat retrieved evidence as a clean, structured evidence block.",
        intervention_summary="Replay with Injection-Oracle recovers evidence lost in malformed injection.",
        cause="retrieved evidence was not correctly injected into the final "
        "context for the agent to use",
        repair_guidance="fix injection formatting so retrieved evidence is presented "
        "as a clean evidence block",
    ),
    "reasoning_error": TargetedRepairAction(
        label="reasoning_error",
        action_name="Evidence-Given Reasoning Repair",
        description="Present evidence in a structured block with explicit reasoning guidance.",
        intervention_summary="Replay with Evidence-Given Reasoning recovers correct answer from available evidence.",
        cause="the injected context contained the required evidence, but the "
        "final answer did not match the gold answer",
        repair_guidance="review reasoning step over provided evidence; the evidence was "
        "sufficient but the conclusion was wrong",
    ),
}


REPAIR_ACTION_BY_LABEL.update(
    {
        "ingestion_error": TargetedRepairAction(
            label="ingestion_error",
            action_name="Oracle Write Repair",
            description="Inject gold evidence directly into memory; ingestion pipeline missed it.",
            intervention_summary="Oracle Write recovers evidence the agent never received.",
            cause="gold evidence never reached the agent through the ingestion pipeline; "
            "the write step could not store what it never received",
            repair_guidance="verify ingestion pipeline is receiving and forwarding all "
            "relevant evidence to the write step",
        ),
        "route_error": TargetedRepairAction(
            label="route_error",
            action_name="Oracle Route Repair",
            description="Route evidence through the correct store/tier for retrieval.",
            intervention_summary="Oracle Route recovers evidence stored in the wrong store/tier.",
            cause="evidence was stored in a store that the baseline retrieval did not query",
            repair_guidance="update routing logic to store evidence in the correct store "
            "or expand retrieval to query all relevant stores",
        ),
        "granularity_error": TargetedRepairAction(
            label="granularity_error",
            action_name="Oracle Granularity Repair",
            description="Re-express memory at a finer or coarser granularity to preserve evidence.",
            intervention_summary="Oracle Granularity recovers evidence lost at the original granularity level.",
            cause="memory was expressed at a granularity level that lost key evidence; "
            "a different granularity preserves the evidence",
            repair_guidance="adjust memory expression granularity to preserve evidence; "
            "consider the level that best balances detail and conciseness",
        ),
        "graph_error": TargetedRepairAction(
            label="graph_error",
            action_name="Graph-Off Repair",
            description="Disable graph expansion to avoid distractor items masking correct evidence.",
            intervention_summary="Graph-Off recovers evidence when graph expansion introduced distractors.",
            cause="graph expansion introduced distractor items that masked correct evidence "
            "present in directly-matched memory items",
            repair_guidance="constrain or re-rank graph expansion results to prevent "
            "distractors from overriding directly-matched evidence",
        ),
        "safety_error": TargetedRepairAction(
            label="safety_error",
            action_name="Safety-Off Repair",
            description="Bypass safety filter to allow valid blocked evidence through.",
            intervention_summary="Safety-Off recovers evidence blocked by an over-aggressive safety filter.",
            cause="safety filter blocked valid evidence that was necessary for a correct answer",
            repair_guidance="review safety filter rules to reduce false positives; "
            "consider evidence-level allow-listing for known-safe content",
        ),
    }
)


def get_targeted_repair_action(label: str) -> TargetedRepairAction:
    """Return the targeted repair action for a V0 attribution label."""
    validate_label_base(label)
    return REPAIR_ACTION_BY_LABEL[label]


def get_targeted_repair_action_v1(label: str) -> TargetedRepairAction:
    """Return the targeted repair action for a V1 attribution label."""
    validate_label(label)
    return REPAIR_ACTION_BY_LABEL[label]


@dataclass(frozen=True)
class RepairComparisonRow:
    """One row comparing CMD-guided targeted repair vs hard-case update."""

    case_id: str
    perturbation_label: str
    predicted_label: str
    repair_action: str
    pre_repair_answer_score: float
    pre_repair_evidence_score: float
    targeted_assessment: str
    targeted_answer_score: float
    targeted_evidence_score: float
    targeted_token_cost: float
    hard_case_assessment: str
    hard_case_answer_score: float
    hard_case_evidence_score: float
    hard_case_token_cost: float
    targeted_better: bool


def make_repair_comparison(full_result) -> RepairComparisonRow:
    """Build a comparison row from a post-repair AuditResult."""
    audit = full_result
    targeted = full_result.post_repair
    hard_case = full_result.hard_case_baseline
    repair_action = get_targeted_repair_action_v1(audit.attribution.predicted_label)

    targeted_better = _is_targeted_better(targeted, hard_case)

    return RepairComparisonRow(
        case_id=audit.case_id,
        perturbation_label=audit.perturbation_label,
        predicted_label=audit.attribution.predicted_label,
        repair_action=repair_action.action_name,
        pre_repair_answer_score=audit.baseline_answer_score,
        pre_repair_evidence_score=audit.baseline_evidence_score,
        targeted_assessment=targeted.repair_assessment,
        targeted_answer_score=targeted.post_repair_answer_score,
        targeted_evidence_score=targeted.post_repair_evidence_score,
        targeted_token_cost=targeted.token_cost,
        hard_case_assessment=hard_case.repair_assessment,
        hard_case_answer_score=hard_case.post_repair_answer_score,
        hard_case_evidence_score=hard_case.post_repair_evidence_score,
        hard_case_token_cost=hard_case.token_cost,
        targeted_better=targeted_better,
    )


@dataclass(frozen=True)
class RepairSuccessLabelSummary:
    """Per-label aggregation of repair outcomes."""

    label: str
    total_cases: int
    targeted_recovered: int
    targeted_partial: int
    targeted_failed: int
    hard_case_recovered: int
    hard_case_partial: int
    hard_case_failed: int
    targeted_better_count: int
    hard_case_better_count: int
    same_outcome_count: int
    avg_targeted_token_cost: float
    avg_hard_case_token_cost: float

    @property
    def targeted_recovery_rate(self) -> float:
        return self.targeted_recovered / self.total_cases if self.total_cases else 0.0

    @property
    def hard_case_recovery_rate(self) -> float:
        return self.hard_case_recovered / self.total_cases if self.total_cases else 0.0

    @property
    def targeted_any_recovery_rate(self) -> float:
        return (
            (self.targeted_recovered + self.targeted_partial) / self.total_cases
            if self.total_cases
            else 0.0
        )

    @property
    def hard_case_any_recovery_rate(self) -> float:
        return (
            (self.hard_case_recovered + self.hard_case_partial) / self.total_cases
            if self.total_cases
            else 0.0
        )


def compute_repair_success_summary(
    rows: list[RepairComparisonRow],
) -> dict[str, RepairSuccessLabelSummary]:
    """Aggregate repair outcomes per attribution label."""
    by_label: dict[str, list[RepairComparisonRow]] = {
        label: [] for label in PIPELINE_LABEL_ORDER
    }
    for row in rows:
        by_label[row.perturbation_label].append(row)

    summaries: dict[str, RepairSuccessLabelSummary] = {}
    for label in PIPELINE_LABEL_ORDER:
        label_rows = by_label[label]
        total = len(label_rows)
        if total == 0:
            continue
        targeted_recovered = sum(
            1 for r in label_rows if r.targeted_assessment == "recovered"
        )
        targeted_partial = sum(
            1 for r in label_rows if r.targeted_assessment == "partial"
        )
        targeted_failed = sum(
            1 for r in label_rows if r.targeted_assessment == "failed"
        )
        hard_case_recovered = sum(
            1 for r in label_rows if r.hard_case_assessment == "recovered"
        )
        hard_case_partial = sum(
            1 for r in label_rows if r.hard_case_assessment == "partial"
        )
        hard_case_failed = sum(
            1 for r in label_rows if r.hard_case_assessment == "failed"
        )
        targeted_better = sum(1 for r in label_rows if r.targeted_better)
        hard_case_better = sum(
            1
            for r in label_rows
            if not r.targeted_better and r.targeted_assessment != r.hard_case_assessment
        )
        same = total - targeted_better - hard_case_better

        summaries[label] = RepairSuccessLabelSummary(
            label=label,
            total_cases=total,
            targeted_recovered=targeted_recovered,
            targeted_partial=targeted_partial,
            targeted_failed=targeted_failed,
            hard_case_recovered=hard_case_recovered,
            hard_case_partial=hard_case_partial,
            hard_case_failed=hard_case_failed,
            targeted_better_count=targeted_better,
            hard_case_better_count=hard_case_better,
            same_outcome_count=same,
            avg_targeted_token_cost=sum(r.targeted_token_cost for r in label_rows)
            / total,
            avg_hard_case_token_cost=sum(r.hard_case_token_cost for r in label_rows)
            / total,
        )
    return summaries


@dataclass(frozen=True)
class RepairClaimLedger:
    """Evidence-backed claims about CMD-guided targeted repair efficacy."""

    total_cases: int
    targeted_recovery_rate: float
    hard_case_recovery_rate: float
    targeted_full_plus_partial_rate: float
    hard_case_full_plus_partial_rate: float
    targeted_better_pct: float
    avg_targeted_token_saving_pct: float
    claim_supported: bool


def build_repair_claim_ledger(
    summaries: dict[str, RepairSuccessLabelSummary],
) -> RepairClaimLedger:
    """Build a claim ledger from per-label repair success summaries."""
    all_rows: list[RepairSuccessLabelSummary] = list(summaries.values())
    if not all_rows:
        return RepairClaimLedger(
            total_cases=0,
            targeted_recovery_rate=0.0,
            hard_case_recovery_rate=0.0,
            targeted_full_plus_partial_rate=0.0,
            hard_case_full_plus_partial_rate=0.0,
            targeted_better_pct=0.0,
            avg_targeted_token_saving_pct=0.0,
            claim_supported=False,
        )

    total_cases = sum(s.total_cases for s in all_rows)
    targeted_recovered = sum(s.targeted_recovered for s in all_rows)
    hard_case_recovered = sum(s.hard_case_recovered for s in all_rows)
    targeted_any = sum(s.targeted_recovered + s.targeted_partial for s in all_rows)
    hard_case_any = sum(s.hard_case_recovered + s.hard_case_partial for s in all_rows)
    targeted_better = sum(s.targeted_better_count for s in all_rows)
    total_targeted_tokens = sum(
        s.avg_targeted_token_cost * s.total_cases for s in all_rows
    )
    total_hard_case_tokens = sum(
        s.avg_hard_case_token_cost * s.total_cases for s in all_rows
    )

    t_rate = targeted_recovered / total_cases if total_cases else 0.0
    h_rate = hard_case_recovered / total_cases if total_cases else 0.0
    t_any_rate = targeted_any / total_cases if total_cases else 0.0
    h_any_rate = hard_case_any / total_cases if total_cases else 0.0
    tb_pct = targeted_better / total_cases if total_cases else 0.0

    token_saving = 0.0
    if total_hard_case_tokens > 0:
        token_saving = (
            total_hard_case_tokens - total_targeted_tokens
        ) / total_hard_case_tokens

    # Claim supported if targeted is at least as good as hard-case on recovery
    # and better on token efficiency, or strictly better on recovery.
    claim_supported = (
        t_any_rate >= h_any_rate and token_saving > 0.0
    ) or t_rate > h_rate

    return RepairClaimLedger(
        total_cases=total_cases,
        targeted_recovery_rate=t_rate,
        hard_case_recovery_rate=h_rate,
        targeted_full_plus_partial_rate=t_any_rate,
        hard_case_full_plus_partial_rate=h_any_rate,
        targeted_better_pct=tb_pct,
        avg_targeted_token_saving_pct=max(0.0, token_saving),
        claim_supported=claim_supported,
    )


def write_repair_success_table(
    rows: list[RepairComparisonRow],
    output_path: str | Path,
    *,
    sandbox_root: str | Path | None = None,
) -> None:
    """Write the per-case repair comparison table and per-label summary."""
    output = Path(output_path)
    _write_comparison_csv(rows, output, sandbox_root=sandbox_root)
    _write_label_summary_csv(rows, output.parent / "repair_label_summary.csv")
    _write_claim_ledger(rows, output.parent / "repair_claim_ledger.txt")


def _write_comparison_csv(
    rows: list[RepairComparisonRow],
    path: Path,
    *,
    sandbox_root: str | Path | None = None,
) -> None:
    fieldnames = [
        "case_id",
        "perturbation_label",
        "predicted_label",
        "repair_action",
        "pre_repair_answer_score",
        "pre_repair_evidence_score",
        "targeted_assessment",
        "targeted_answer_score",
        "targeted_evidence_score",
        "targeted_token_cost",
        "hard_case_assessment",
        "hard_case_answer_score",
        "hard_case_evidence_score",
        "hard_case_token_cost",
        "targeted_better",
    ]
    row_dicts = [
        {
            "case_id": row.case_id,
            "perturbation_label": row.perturbation_label,
            "predicted_label": row.predicted_label,
            "repair_action": row.repair_action,
            "pre_repair_answer_score": f"{row.pre_repair_answer_score:.3f}",
            "pre_repair_evidence_score": f"{row.pre_repair_evidence_score:.3f}",
            "targeted_assessment": row.targeted_assessment,
            "targeted_answer_score": f"{row.targeted_answer_score:.3f}",
            "targeted_evidence_score": f"{row.targeted_evidence_score:.3f}",
            "targeted_token_cost": f"{row.targeted_token_cost:.1f}",
            "hard_case_assessment": row.hard_case_assessment,
            "hard_case_answer_score": f"{row.hard_case_answer_score:.3f}",
            "hard_case_evidence_score": f"{row.hard_case_evidence_score:.3f}",
            "hard_case_token_cost": f"{row.hard_case_token_cost:.1f}",
            "targeted_better": str(row.targeted_better).lower(),
        }
        for row in rows
    ]
    write_csv_table(path, fieldnames, row_dicts, sandbox_root=sandbox_root)


def _write_label_summary_csv(rows: list[RepairComparisonRow], path: Path) -> None:
    summaries = compute_repair_success_summary(rows)
    fieldnames = [
        "label",
        "total_cases",
        "targeted_recovered",
        "targeted_partial",
        "targeted_failed",
        "targeted_recovery_rate",
        "hard_case_recovered",
        "hard_case_partial",
        "hard_case_failed",
        "hard_case_recovery_rate",
        "targeted_better_count",
        "hard_case_better_count",
        "same_outcome_count",
        "avg_targeted_token_cost",
        "avg_hard_case_token_cost",
    ]
    row_dicts: list[dict[str, str]] = []
    for label in PIPELINE_LABEL_ORDER:
        summary = summaries.get(label)
        if summary is None:
            continue
        row_dicts.append(
            {
                "label": label,
                "total_cases": str(summary.total_cases),
                "targeted_recovered": str(summary.targeted_recovered),
                "targeted_partial": str(summary.targeted_partial),
                "targeted_failed": str(summary.targeted_failed),
                "targeted_recovery_rate": f"{summary.targeted_recovery_rate:.3f}",
                "hard_case_recovered": str(summary.hard_case_recovered),
                "hard_case_partial": str(summary.hard_case_partial),
                "hard_case_failed": str(summary.hard_case_failed),
                "hard_case_recovery_rate": f"{summary.hard_case_recovery_rate:.3f}",
                "targeted_better_count": str(summary.targeted_better_count),
                "hard_case_better_count": str(summary.hard_case_better_count),
                "same_outcome_count": str(summary.same_outcome_count),
                "avg_targeted_token_cost": f"{summary.avg_targeted_token_cost:.1f}",
                "avg_hard_case_token_cost": f"{summary.avg_hard_case_token_cost:.1f}",
            }
        )
    write_csv_table(path, fieldnames, row_dicts)


def _write_claim_ledger(rows: list[RepairComparisonRow], path: Path) -> None:
    summaries = compute_repair_success_summary(rows)
    ledger = build_repair_claim_ledger(summaries)

    lines = [
        "CMD V1 Repair Claim Ledger — Issue 0006",
        "=" * 60,
        "",
        f"Total cases: {ledger.total_cases}",
        "",
        "Full Recovery (recovered):",
        f"  CMD-guided targeted repair: {ledger.targeted_recovery_rate:.3f}",
        f"  Undifferentiated hard-case:  {ledger.hard_case_recovery_rate:.3f}",
        "",
        "Full + Partial Recovery (recovered + partial):",
        f"  CMD-guided targeted repair: {ledger.targeted_full_plus_partial_rate:.3f}",
        f"  Undifferentiated hard-case:  {ledger.hard_case_full_plus_partial_rate:.3f}",
        "",
        f"Targeted repair strictly better: {ledger.targeted_better_pct:.3f}",
        f"Avg token saving (targeted vs hard-case): {ledger.avg_targeted_token_saving_pct:.1%}",
        "",
        f"Claim supported: {ledger.claim_supported}",
        "",
        "Claim: CMD-guided targeted repairs are at least as effective as",
        "undifferentiated hard-case updates while using less context.",
        "",
        "-" * 60,
        "Per-label detail:",
    ]

    for label in PIPELINE_LABEL_ORDER:
        s = summaries.get(label)
        if s is None:
            continue
        lines.append(
            f"  {label}: {s.total_cases} case(s), "
            f"targeted={s.targeted_recovery_rate:.3f}, "
            f"hard_case={s.hard_case_recovery_rate:.3f}, "
            f"targeted_better={s.targeted_better_count}/{s.total_cases}"
        )

    write_text_artifact(path, lines)


def _is_targeted_better(
    targeted: PostRepairResult, hard_case: PostRepairResult
) -> bool:
    """Determine whether targeted repair out-performs hard-case update.

    Priority: recovered > partial > failed.
    Tie-break: lower token cost.
    """
    order = {"recovered": 0, "partial": 1, "failed": 2}
    t_rank = order[targeted.repair_assessment]
    h_rank = order[hard_case.repair_assessment]
    if t_rank < h_rank:
        return True
    if t_rank > h_rank:
        return False
    return targeted.token_cost < hard_case.token_cost


# ── Issue 0020-B: RepairAction + adapter.apply_repair ───────────────────

REPAIR_ACTION_TYPES = ("append", "replace", "relocate", "update_routing", "update_template")


class RepairActionTypeError(ValueError):
    """Raised when an invalid repair action type is used."""


class RepairActionOutputError(ValueError):
    """Raised when a RepairAction subagent response is not strict valid JSON."""


class UnsupportedActionError(ValueError):
    """Raised when an adapter does not support a selected repair action."""


@dataclass(frozen=True)
class RepairAction:
    """Concrete repair operation emitted by RepairExecutor, executed by adapter.

    The LLM sees label + evidence_block + fm_context + adapter.supported_actions
    and autonomously selects action_type and fills parameters.
    """

    action_type: str
    target_item_id: str | None
    target_store: str
    content: str
    label: str
    reasoning: str = ""

    def __post_init__(self) -> None:
        validate_repair_action_type(self.action_type)
        validate_label(self.label)
        if not self.target_store:
            raise RepairActionTypeError("RepairAction target_store must be non-empty")
        if not self.content:
            raise RepairActionTypeError("RepairAction content must be non-empty")


REPAIR_ACTION_TOOL_DEFINITION: dict = {
    "name": "select_repair_action",
    "description": (
        "Select a repair action to fix the identified memory pipeline error. "
        "Choose from the adapter's supported_actions based on the label, "
        "evidence block, and failure memory context."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action_type": {
                "type": "string",
                "enum": list(REPAIR_ACTION_TYPES),
                "description": "Type of repair action to execute",
            },
            "target_item_id": {
                "type": ["string", "null"],
                "description": "ID of the memory item to modify (null for append)",
            },
            "target_store": {
                "type": "string",
                "description": "Store/tier name (e.g., episodic, archival, core)",
            },
            "content": {
                "type": "string",
                "description": "Content to write, replace, or append",
            },
            "label": {
                "type": "string",
                "description": "Attribution label that triggered this repair",
            },
            "reasoning": {
                "type": "string",
                "description": "Rationale for choosing this action",
            },
        },
        "required": ["action_type", "target_item_id", "target_store", "content", "label"],
    },
}

REPAIR_ACTION_SYSTEM_PROMPT = """\
You are the CMD RepairAction subagent.

You receive an already-computed CMD attribution label and repair context.
Your only task is to emit the concrete RepairAction JSON object the adapter
should execute. Do not re-diagnose the case. Do not emit prose, Markdown,
code fences, or multiple JSON objects.

OUTPUT: one strict JSON object only."""


def build_repair_action_prompt(
    *,
    label: str,
    evidence_block: str,
    fm_context: str,
    supported_actions: tuple[str, ...],
    target_store: str,
    content: str,
    repair_guidance: str,
) -> str:
    """Build the JSON-only RepairAction subagent prompt."""
    validate_label(label)
    return "\n\n".join(
        (
            "CMD ATTRIBUTION LABEL:\n" + label,
            "ADAPTER SUPPORTED ACTIONS:\n" + json.dumps(list(supported_actions)),
            "DEFAULT TARGET STORE:\n" + target_store,
            "CONTENT TO APPLY:\n" + content,
            "REPAIR GUIDANCE:\n" + repair_guidance,
            "COUNTERFACTUAL EVIDENCE BLOCK:\n" + evidence_block,
            "FAILURE MEMORY CONTEXT:\n" + fm_context,
            "REQUIRED JSON SCHEMA:\n"
            + json.dumps(
                {
                    "action_type": "one of ADAPTER SUPPORTED ACTIONS",
                    "target_item_id": "string or null",
                    "target_store": "string",
                    "content": "string; use CONTENT TO APPLY unless target API requires a narrower payload",
                    "label": label,
                    "reasoning": "short string",
                },
                indent=2,
            ),
        )
    )


def validate_repair_action_type(action_type: str) -> str:
    """Validate action_type is one of the supported values."""
    if action_type not in REPAIR_ACTION_TYPES:
        raise RepairActionTypeError(
            f"Invalid repair action type {action_type!r}; "
            f"must be one of {REPAIR_ACTION_TYPES}"
        )
    return action_type


def parse_repair_action_response(
    response: str,
    *,
    supported_actions: tuple[str, ...],
    expected_label: str | None = None,
) -> RepairAction:
    """Parse strict JSON-only subagent output into a validated RepairAction.

    The parser intentionally rejects Markdown fences and any surrounding prose
    so real subagent runs cannot silently pass malformed tool output.
    """
    raw = response.strip()
    if not raw:
        raise RepairActionOutputError("RepairAction response is empty")
    if raw.startswith("```") or raw.endswith("```"):
        raise RepairActionOutputError("RepairAction response must be JSON only")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RepairActionOutputError(
            f"RepairAction response is not valid JSON: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise RepairActionOutputError("RepairAction response must be a JSON object")

    required = ("action_type", "target_item_id", "target_store", "content", "label")
    missing = [field for field in required if field not in payload]
    if missing:
        raise RepairActionOutputError(
            "RepairAction response missing required fields: " + ", ".join(missing)
        )

    action_type = str(payload["action_type"])
    if action_type not in supported_actions:
        raise RepairActionOutputError(
            f"RepairAction action_type {action_type!r} not supported; "
            f"adapter supports {supported_actions}"
        )

    label = str(payload["label"])
    if expected_label is not None and label != expected_label:
        raise RepairActionOutputError(
            f"RepairAction label {label!r} does not match expected {expected_label!r}"
        )

    target_item_id = payload["target_item_id"]
    if target_item_id is not None and not isinstance(target_item_id, str):
        raise RepairActionOutputError(
            "RepairAction target_item_id must be a string or null"
        )

    try:
        return RepairAction(
            action_type=action_type,
            target_item_id=target_item_id,
            target_store=str(payload["target_store"]),
            content=str(payload["content"]),
            label=label,
            reasoning=str(payload.get("reasoning", "")),
        )
    except (RepairActionTypeError, ValueError) as exc:
        raise RepairActionOutputError(str(exc)) from exc


@dataclass(frozen=True)
class RepairActionResult:
    """Result of applying a RepairAction through an adapter."""

    success: bool
    action: RepairAction
    store_checksum_before: str
    store_checksum_after: str
    error_message: str = ""
