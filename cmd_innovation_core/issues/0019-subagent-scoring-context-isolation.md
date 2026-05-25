---
title: Subagent-based LLM scoring with context isolation replacing phrase-matching
labels:
  - ready-for-human
type: ready-for-human
blocked_by: ~
user_stories: []
tdd_cycle: 24
---

# Subagent-based LLM scoring with context isolation replacing phrase-matching

## Parent

`prd/cmd_minimal_probe_prd.md` V1 Scope
`issues/0016-real-data-probe-cases-and-memory-probe-integration.md`

## Motivation

CMD's current scoring (`cmd_audit/scoring.py`) uses deterministic phrase-matching:

- `answer_score`: casefolded exact string match → 0.0 or 1.0
- `evidence_recall_from_text`: all required_phrases must be substring → [0, 1]

This has two structural problems:

1. **Semantic blindness**: synonym paraphrase fails at 0.0; negation context may spuriously match. CMD is the only counterfactual attribution system still using deterministic scoring (literature survey: 40+ papers/projects, 5 attribution categories).

2. **Shortcut evaluation in oracle replays** (`replays.py:189`): `_score_recovered_evidence` returns `case.gold_answer` when `evidence_score == 1.0` instead of re-running the Agent with repaired context. This means CMD never actually verifies that a repaired memory context produces a correct answer through the Agent's own reasoning — it merely checks phrase presence and substitutes gold_answer.

```
Current (shortcut):
  oracle_retrieval → inject gold evidence → phrase_match = 1.0 → return gold_answer as output

Correct (full counterfactual):
  oracle_retrieval → repair retrieval operation → Agent re-runs with repaired context → LLM output → Subagent evaluates
```

## Design Decision: A/B Split (2026-05-21)

The current `_score_recovered_evidence` has two separable shortcuts:

(A) **Scoring shortcut**: deterministic phrase-matching (`evidence_recall_from_text` + `answer_score`)
(B) **Answer shortcut**: `answer = case.gold_answer` substitution when `evidence_score == 1.0`

These are independent. The A/B split:

- **Decision A (issue 0019 scope)**: Replace phrase-matching with SubagentScorer (EvidenceVerifier + AnswerVerifier subagents). Keeps existing contract. Keeps the shortcut for now. Subagent `{FACT, TEXT}` input is generic — works with any mem-augmented agent's output.
- **Decision B (deferred)**: Agent re-run + real LLM output. Only relevant when live agent loop exists (adapter mode). Subagent consumes real agent output with zero code changes.

## Design

### Two roles for LLM

| Role | Purpose | Model |
|------|---------|-------|
| **LLM-as-Judge baseline** | 3rd comparison baseline (alongside evidence_recall, random_label). Post-hoc trace explanation tests whether observational analysis already solves attribution. | qwen2.5-7b (provider-agnostic via `llm_client.py`) |
| **Subagent-based scoring** | Replace phrase-matching in `_score_recovered_evidence`. Subagent evaluates Agent output against gold_evidence facts (not gold_answer wording). | qwen2.5-7b (provider-agnostic via `llm_client.py` → `subagent_runner.py`) |

GPT-4o-mini reserved for calibration upper-bound on 100-case subset. Main agent and subagents share one LLM API through `llm_client.py`; provider is not hardcoded to ollama.

### Core insight: binary at subagent level, continuous at aggregation

The scoring mechanism does NOT use multi-level rubrics (Likert scales). Each subagent makes exactly one atomic binary decision:

- A fact is either present in the text or it is not — this is a binary property
- "Partial presence" is a human intuition that increases LLM variance when forced into a single call
- The continuous signal naturally emerges from aggregation: `evidence_score = count(PRESENT) / total_phrases`

```
                    ┌─────────────────────────────┐
                    │     AGGREGATION LAYER        │
                    │  evidence_score = ΣPRESENT/n │
                    │  answer_score = EQUIV? 1:0   │
                    │  recovery_gain = ans - base  │
                    └──────────┬──────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
  ┌───────▼───────┐  ┌────────▼────────┐  ┌───────▼───────┐
  │ subagent #1   │  │ subagent #2     │  │ subagent #3   │
  │ context:      │  │ context:        │  │ context:      │
  │ {fact₁, text} │  │ {fact₂, text}   │  │ {answer, gold}│
  │ → PRESENT     │  │ → ABSENT        │  │ → EQUIVALENT  │
  └───────────────┘  └─────────────────┘  └───────────────┘
```

### Subagent context construction: four constraints

1. **Atomicity**: one subagent = one judgment (one evidence phrase, or one answer pair)
2. **Self-contained**: the context window contains all information needed for the judgment, and nothing more
3. **Auditability**: context sources are traceable (which replay, which gold_evidence item)
4. **Leak-free**: no gold_label, no other case data, no full traces, no gold_answer wording (for evidence subagents)

### Judgment framework sourcing (design rationale only — not injected into subagent)

The templates below embed detailed decision rules derived from three published methods (TraceAudit intervention operations, AgentRx invariant constraints, Rewind factual dimension, G-EVAL behavioral anchors). The subagent sees only the rules and examples — source labels are stripped to minimize token cost. See `## Comparison with literature` below for the mapping of each rule to its source.

### EvidenceVerifier subagent context template

```
TASK: Verify whether the fact exists in the given text

FACT:
  <gold_evidence.text>

TEXT:
  <replay-produced evidence_block or agent's repaired answer>

DECISION RULES:

  PRESENT — The core propositional content of FACT is communicated by TEXT:
    - Paraphrase or reworded expression with same meaning
    - Abbreviation or expansion preserving the core claims
    - Fact embedded within a larger passage
    - Different ordering of the same claims

  ABSENT — The core proposition is NOT communicated:
    - Fact completely missing from TEXT
    - TEXT mentions a related but different concept (not the same proposition)
    - TEXT directly contradicts the fact
    - Only tangential mention; proposition not actually established
    - UNCERTAIN / BOUNDARY CASE → ABSENT (conservative tie-break)

  To resolve boundary cases, extract the core subject-verb-object triple
  from FACT and check whether TEXT preserves that same triple.

EXAMPLES:

  FACT: "The 6S algorithm is implemented in the SIAC_GEE tool."

  TEXT: "SIAC_GEE provides atmospheric correction including the 6S method."
    → Core proposition (6S ∈ SIAC_GEE) preserved → PRESENT

  TEXT: "SIAC_GEE is a toolbox for various atmospheric correction methods."
    → SIAC_GEE + atmospheric correction mentioned, but 6S absent → ABSENT

  TEXT: "The 6S method is described in Vermote et al. (1997)."
    → 6S mentioned but not "SIAC_GEE implements 6S" proposition → ABSENT

OUTPUT: PRESENT | ABSENT
```

The output is strictly binary. Continuous signal emerges from aggregation (`count(PRESENT) / total`).

### AnswerVerifier subagent context template

```
TASK: Verify whether two answers are semantically equivalent

ANSWER:
  <agent's output after re-running with repaired context>

GOLD ANSWER:
  <ground truth answer>

DECISION RULES:

  EQUIVALENT — Both answers communicate the same factual information:
    - Different word choice, same meaning
    - Non-contradictory extra details in either answer
    - Different presentation order of the same facts
    - Abbreviation or expansion of the same claims

  NOT_EQUIVALENT — The answers differ in factual content:
    - Core fact(s) missing from ANSWER
    - ANSWER contains information contradicting GOLD ANSWER
    - Extra details change the factual meaning of the original
    - UNCERTAIN / BOUNDARY CASE → NOT_EQUIVALENT (conservative tie-break)

OUTPUT: EQUIVALENT | NOT_EQUIVALENT
```

AnswerVerifier is used at the **Post-Repair Context Replay** validation step only — it compares the Agent's repaired-context answer against gold_answer. It is NOT used in the attribution loop (which only needs EvidenceVerifier). The answer verifier is removable after repair validation.

### Corrected CMD replay path

```
Step 1 — CMD repairs the memory operation:
  oracle_retrieval(case) → forces retrieval of gold evidence memory items
  Repaired context = baseline_retrieved + [gold_evidence_items]

Step 2 — Agent re-runs with repaired context:
  answer = agent.generate(query, repaired_context)
  ↑ Real LLM output, NOT gold_answer substitution

Step 3 — Subagent evaluates:
  For each gold_evidence item:
    subagent(FACT=ev.text, TEXT=answer) → PRESENT | ABSENT
  evidence_score = count(PRESENT) / total

Step 4 — Recovery gain:
  baseline_score = Σ subagent(FACT, baseline_answer)
  replay_score   = Σ subagent(FACT, replay_answer)
  recovery_gain  = replay_score - baseline_score

Step 5 — Attribution (unchanged):
  argmax recovery_gain → predicted_label
```

### Gold_evidence ≠ Gold_answer

| | gold_evidence | gold_answer |
|---|---|---|
| What it is | Set of factual propositions | Specific wording |
| Example | "6S algorithm implemented in SIAC_GEE" | "The 6S algorithm is implemented in the SIAC_GEE tool." |
| Needed for attribution | Yes (information-theoretic bound) | No |
| Subagent uses | EvidenceVerifier | AnswerVerifier (post-repair only) |

EvidenceVerifier needs gold_evidence (facts), not gold_answer (wording). This eliminates the gold_answer dependency from the attribution loop while preserving the information-theoretic lower bound.

### Design rationale: compositing, not inventing

The decision rules in the templates above are derived from published methods but injected without source labels to minimize token cost. The design contribution is structural, not algorithmic — each rule exists independently in the literature; CMD's contribution is packaging them into atomically-contextualized, single-call, binary-output subagent prompts.

Design rationale (not injected into subagent):

| Rule in template | Derived from | Original use in source |
|-----------------|-------------|----------------------|
| Paraphrase → PRESENT / Missing → ABSENT | TraceAudit intervention ops | Chunk-level RAG audit |
| Related-but-different concept → ABSENT | TraceAudit `distract` op | Chunk-level RAG audit |
| Extract subject-verb-object triple; check preservation | AgentRx invariant constraint | 10-category full-trace classification |
| Boundary case → conservative ABSENT | CMD design decision | N/A (tie-break policy) |
| Lexical variation / reordering → EQUIVALENT | G-EVAL behavioral anchors | Multi-criteria Likert rubric + CoT |

The structural shift applied to every source is the same: **from multi-dimensional, full-trace, multi-level → single-dimensional, atomically-contextualized, binary.**

## Implementation Plan

**Dependency-first file creation order:** `llm_client.py` → `llm_judge.py` → `subagent_runner.py` → `llm_scoring.py` + `hooks.py`

### Shared dependency

**Files:**
- `cmd_audit/llm_client.py`: Provider-agnostic LLM API client (`generate(prompt) -> str`). Shared by all LLM callers (LLMJudgeBaseline, SubagentScorer). Must handle: model unreachable (fallback to phrase-matching with warning), empty response, Unicode errors.
- `cmd_audit/subagent_runner.py`: Claude Code-style subagent runner. Minimal `run(system_prompt: str, user_message: str) -> str`. Isolated context window, dedicated system prompt, no tool access, returns result only.

### Phase A: LLM-as-Judge baseline

**Files:**
- `cmd_audit/llm_judge.py`: `LLMJudgeBaseline` class — observational post-hoc trace analysis (through `llm_client.py`)

**Model:** qwen2.5-7b (provider-agnostic via `llm_client.py`; reproducible)

**Input boundary:** Observable post-hoc trace artifacts only — NO gold_label, ptype, gold_evidence, or gold_answer.

**Key test:** If LLM-as-Judge accuracy ≈ CMD accuracy → counterfactual replay not needed (falsification)

### Phase B: Subagent scoring replacing phrase-matching

**Files:**
- `cmd_audit/llm_scoring.py`: `EvidenceVerifier` (active), `AnswerVerifier` (implemented, wired at Decision B), `SubagentScorer` — through `subagent_runner.py`
- `cmd_audit/hooks.py`: `validate_context_isolation`, `validate_output_format` — pure validation functions, enforced internally by SubagentScorer at subagent call boundary
- `cmd_audit/replays.py`: update `_score_recovered_evidence` to accept optional `scorer` parameter; contract `(gold_evidence, text) -> float` aligned to `evidence_recall_from_text`; `evidence_recall_from_text` preserved as default/fallback

**Integration point:** `_score_recovered_evidence` in `replays.py:330-343`

### Phase C: Variance study

**File:** `experiments/run_variance_study.py`
- 100 cases, 3 runs each, temperature=0
- Group A: open-ended LLM scoring → measure std dev
- Group B: atomic subagent + binary + hook → measure std dev

## Acceptance Criteria

### Phase A
- [ ] `llm_client.py` provides provider-agnostic `generate(prompt) -> str` interface
- [ ] `LLMJudgeBaseline` produces `DiagnosisPrediction` for all 596 cases
- [ ] LLM-as-Judge appears in comparison_metrics.csv
- [ ] Input boundary: LLMJudgeBaseline receives only observable trace artifacts (no gold_label, ptype, gold_evidence, gold_answer)
- [ ] Behavior test: falsification check — LLM-as-Judge accuracy vs CMD accuracy

### Phase B
- [ ] `subagent_runner.py` provides `run(system_prompt, user_message) -> str` with isolated context window
- [ ] `EvidenceVerifier` receives atomic context {FACT, TEXT, STANDARD}, outputs PRESENT | ABSENT (active)
- [ ] `AnswerVerifier` receives atomic context {ANSWER, GOLD_ANSWER, STANDARD}, outputs EQUIVALENT | NOT_EQUIVALENT (implemented, wired at Decision B)
- [ ] `SubagentScorer.score_evidence()` matches current `evidence_recall_from_text` contract (float in [0,1])
- [ ] `validate_context_isolation` raises `ContextLeakError` when context contains case_id, gold_label, ptype, or cross-case data
- [ ] `validate_output_format` raises `OutputFormatError` on non-binary output; retry once before fallback
- [ ] Hook enforcement is internal to SubagentScorer — external callers cannot bypass
- [ ] CMD attribution accuracy on 596 cases ≥ 0.95 (LLM variance tolerance)
- [ ] Deterministic phrase-matching path preserved as fallback (`scorer=None`)

### Phase C
- [ ] 100-case, 3-run variance study produces std dev comparison
- [ ] Group B (subagent) std dev ≤ Group A (open-ended) std dev
- [ ] Temperature=0 produces consistent outputs (≤ 5% deviation across runs)

## Edge Cases

- **LLM unavailable:** `llm_client.generate()` raises → SubagentScorer falls back to deterministic phrase-matching with warning. Shared fallback path for both LLMJudgeBaseline and SubagentScorer.
- **Output parse failure:** Hook catches non-binary output → retry once with stricter prompt → fall back to phrase-matching for that item.
- **Empty evidence_block:** Skip subagent call, return PRESENT=0 directly.
- **Evidence block >4K tokens:** Truncate with indicator, log truncation event.
- **Cross-case contamination:** Hook rejects context containing other case_ids, gold_labels, or perturbation_types.
- **Provider mismatch:** `llm_client.py` abstracts the provider; swapping ollama→openai→anthropic requires no changes to `llm_judge.py`, `subagent_runner.py`, or `llm_scoring.py`.

## Related

- `cmd_innovation_core/plans/limitations.md` Limitation #1 (gold evidence dependency)
- `cmd_audit/scoring.py` (current phrase-matching)
- `cmd_audit/replays.py:182-198` (`_score_recovered_evidence` — integration point)
- `reference_notes/github_traceaudit.md` — TraceAudit: `remove`/`paraphrase`/`distract` operations mapped to PRESENT/ABSENT conditions
- `reference_notes/github_agentrx.md` — AgentRx: invariant-based constraint (core proposition triple) as tie-break mechanism
- `reference_notes/github_rewind.md` — Rewind: single factual dimension validates atomic binary sufficiency; answer comparison dimension
- G-EVAL (LLM-as-evaluator) — behavioral anchors for semantic equivalence (lexical variation, non-contradictory additions, reordering)
