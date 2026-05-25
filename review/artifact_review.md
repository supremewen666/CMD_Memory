release_verdict: HOLD

# Artifact Review — Decision 34 Non-Code Repair

## Scope

Reviewed artifacts:

| File | Mode | Headline claim handling |
|------|------|-------------------------|
| `TASK.md` | release page review, style review | Timeline now binds headline to 130 researcher-adjudicated cases; 596 marked sanity only. |
| `cmd_innovation_core/plans/cmd_open_decisions.md` | paper review | Decision 34 records R1-R7 and supersedes Decision 30/33 details. |
| `cmd_innovation_core/gates/V0V1_gate_status.md` | paper review | 596-case Macro F1 caveat and re-test plan added. |
| `cmd_innovation_core/gates/V1V2_gate_status.md` | paper review | Adapter parity caveat added. |
| `cmd_innovation_core/plans/experiment_02_cmd_attribution.md` | paper review | Two-tier 130/596 evaluation set defined. |
| `cmd_innovation_core/plans/experiment_01_context_construction.md` | paper review | R7 80-case/5-mode protocol defined. |
| `cmd_innovation_core/plans/limitations.md` | paper review | Evaluator-annotator circularity added; scoring limitations reframed. |
| `CLAUDE.md`, `CONTEXT.md`, `knowledge/_index.md`, `knowledge/current-memory.md` | release page review | Navigation and current-state guidance updated. |

## Findings

### P0 — Paper release still lacks headline evidence artifacts

Affected files: `TASK.md`, `cmd_innovation_core/plans/experiment_02_cmd_attribution.md`, `cmd_innovation_core/gates/V0V1_gate_status.md`

evidence_path: N/A

affected_claim_id: Experiment 2 headline Macro F1

Why it matters: The documents now correctly say the headline will come from 130 researcher-adjudicated cases, but that subset is still an empty stub and the LLM re-test artifact does not exist. The paper cannot be released until those artifacts exist.

Concrete fix: Complete Decision 34 steps R1-R4: wiring sprint, `artifacts/at_scale_llm_retest.csv`, populated `data/probe_cases/researcher_labeled_subset.json`, and final Experiment 2 metrics.

### P1 — deepseek label provenance is acknowledged but not recovered

Affected files: `data/cleaned_cases/cleaning_report.txt`, `cmd_innovation_core/plans/experiment_02_cmd_attribution.md`

evidence_path: `scripts/annotate_perturbation_labels.py`

affected_claim_id: 596-case scale sanity

Why it matters: The scale sanity result is reproducibility-limited until the prompt/script that generated the 596 labels is recovered or explicitly reconstructed.

Concrete fix: Add `scripts/annotate_perturbation_labels.py` during the code/provenance sprint with prompt template, label definitions, and reproducibility target.

### P1 — Review gate must be regenerated after any headline file changes

Affected files: all checked files

evidence_path: N/A

affected_claim_id: N/A

Why it matters: This review is valid only for the current non-code repair pass. Any later metric, protocol, or claim text change can invalidate the gate.

Concrete fix: Re-run artifact review after the LLM re-test and researcher adjudication land.
