# Limitations

CMD diagnoses memory-augmented agent failures via counterfactual replay. This section documents known boundaries, dependencies, and assumptions—both methodological (inherent to the approach) and implementation-level (scope of current evaluation).

---

## 1. Gold Evidence Dependency (Methodological)

**What it is.** Four of CMD's eleven pipeline labels require `gold_evidence`—the set of text phrases an ideal memory system should have stored to answer the query correctly:

| Label | Counterfactual Replay | Why Gold Evidence Is Needed |
|-------|----------------------|---------------------------|
| `write_error` | Oracle Write | Must inject evidence text that was never stored |
| `compression_error` | Oracle Compression | Must inject evidence text lost during compression |
| `premature_extraction_error` | Verbatim Event Oracle | Must inject raw events containing evidence lost at extraction |
| `injection_error` | Injection Oracle | Must inject evidence into specific retrieval slots |

These replays create memory items that *do not exist* in the system state. Without gold evidence, there is no way to know what content *should* have been stored.

The remaining seven labels (`retrieval_error`, `reasoning_error`, `ingestion_error`, `route_error`, `granularity_error`, `graph_error`, `safety_error`) operate on existing memory items—they rearrange, rerank, expand, or re-route them without needing to know what evidence should exist.

**Why this is necessary, not fixable.** Detecting missing content requires knowing what content should exist. This is an information-theoretic bound: no algorithm can determine whether `write()` omitted fact X if it has never seen fact X. The four affected labels diagnose *content absence* errors; the seven unaffected labels diagnose *structure and access* errors.

**Paper positioning.** CMD-Audit (offline, with gold evidence) proves the counterfactual replay methodology. CMD-Skill Adapter (online, no gold evidence) degrades to a reduced but functional label set. The comparison is not "CMD with vs. without gold evidence." The comparison is "CMD without gold evidence vs. status quo (zero automated operation-level attribution)."

**Mitigation path (self-supervision).** Success-trace memory items provide surrogate evidence candidates. Counterfactual replay filters candidates: only those producing causal recovery gain survive. This can approximate 2–3 of the 4 affected labels:
- `write_error`: candidate evidence from success-trace memory items absent in failure trace
- `compression_error`: candidate evidence present in raw events but absent in extracted memory
- `premature_extraction_error`: candidate evidence present in success-trace extracted memory but absent in failure trace

Candidate quality improves as the success-trace corpus grows. Full closure of the gold evidence gap is not claimed—only that degradation is bounded and measurable.

---

## 2. LLM-Based Semantic Scoring — Resolved (Implementation)

**What it was.** Pre-2026-05-23, CMD's `answer_score` used casefold exact-match scoring (`0.0` or `1.0`) and `evidence_recall_from_text` used casefold phrase matching. Both were lower bounds. The at-scale 596-case Macro F1 = 1.000 was driven by `replays.py:477` short-circuiting the agent loop entirely.

**Resolution (Decision 34 R1+R2, 2026-05-23/24).** The at-scale re-test moves to:
- `agent_generate` = qwen2.5-7b ollama producing replay answers from `(query, baseline + evidence_block)` (label string dropped from replay context).
- Evidence scorer = an LLM independent of agent model and adjudication LLM-A. Continuous `evidence_score ∈ [0,1]` from `count(PRESENT) / total` over per-fact subagent calls.
- `AnswerVerifier == EQUIVALENT` drives the `recovered` decision in Post-Repair Context Replay; partial threshold τ=0.5 default, calibrated post-hoc.
- `vector_memory` baseline rescored on-the-fly under same agent + scorer per case, eliminating asymmetric scoring between replays and baselines.
- Bootstrap CI (1000-iter case-level resample) on Macro F1 + top-2 + per-label F1 + per-baseline + κ.
- Two-evaluator robustness check on 130-case headline only (Decision 34 R10/Q19).

The phrase-match path is preserved as a fallback when `agent_generate is None and scorer is None`, gated by `PhraseMatchShortcutWarning`. Functions as regression-detecting lower bound and is the path used by V0 unit tests.

**Remaining limitation.** LLM scorer introduces variance (~5-10% intra-call at temperature=0). Mitigated by per-case `recovery_gain` distribution logging, 3-trial protocols at admission gates (Experiment 1 `none`-mode pre-check), bootstrap CIs on aggregate metrics, and two-evaluator robustness on 130-case headline.

---

## 3. Synthetic Perturbations (Evaluation)

**What it is.** All 596 probe cases are synthetically perturbed—each case has a known `perturbation_type` injected as ground truth. The perturbations are constructed to exercise specific pipeline operations under controlled conditions.

**Impact.** Results on synthetic data may not transfer to real production failure distributions. Real failures may:
- Combine multiple error types within a single pipeline operation
- Cascade across operations (write error → injection error → reasoning error)
- Include error types not in CMD's 11-label taxonomy (open-world failures)
- Contain noise from non-memory sources (model hallucination, prompt ambiguity)

**Mitigation.** This is a standard limitation of controlled evaluation. The synthetic probe suite establishes the methodology's *internal validity*—that counterfactual replay correctly attributes failures when the perturbation is known. *External validity* (real failure distributions) requires real agent deployment data, which is planned but not yet collected. The 596-case scale (across MemoryArena, LongMemEval, ToolBench) provides coverage across diverse query domains and memory patterns.

---

## 4. Single-Agent Scope (Methodological)

**What it is.** CMD attributes failures to pipeline operations *within* a single memory-augmented agent. It does not attribute failures across multiple agents in a multi-agent system.

**Impact.** In a multi-agent deployment, a failure attributed to `retrieval_error` in Agent A may originate from a memory write error in Agent B. CMD currently cannot trace cross-agent causality.

**Relationship to Shapley-value attribution (2605.13077).** Shapley-value counterfactual attribution operates at agent granularity with coalitional cost (exponential in agent count). CMD operates at operation granularity with linear cost (linear in pipeline length). These are complementary: Shapley identifies *which agent* failed; CMD diagnoses *which operation within that agent* failed. A composed Shapley + CMD attribution stack would provide multi-resolution diagnosis—agent-level responsibility → operation-level root cause—but is not implemented.

---

## 5. Operation-Level Granularity (Methodological)

**What it is.** CMD attributes failures to pipeline *operations* (write, compress, extract, retrieve, inject, reason, route, graph, safety). It does not attribute failures to specific *memory items* within an operation.

**Impact.** When a write operation stores 100 memory items and one is wrong, CMD labels the failure `write_error` but cannot identify *which* item is wrong. The ECS repair guidance describes the error at operation level; the targeted repair action (e.g., `fix_misattribution`) rewrites the evidence block but does not surgically edit the single faulty item.

**Why deferred.** Per-item attribution requires:
- Item-level provenance tracking (which items influenced which decisions)—Decision 28
- Item-level replay interventions (inject/remove/modify single items)
- Bad-item labels (`item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`)

These are planned for V2. The V0/V1 operation-level attribution is sufficient for diagnosis (which pipeline stage failed) and repair (what correction to apply at that stage). Surgical item-level repair is a precision improvement, not a correctness fix.

---

## 6. Phrase-Matching Evidence Recall — Resolved (Implementation)

**What it was.** `evidence_recall_from_text` checked whether all `required_phrases` of a gold evidence unit appear (casefold) in a memory item's text. Lexical substring matching, not semantic entailment. Misclassified paraphrases as missing.

**Resolution (Decision 34 R1, 2026-05-23/24).** Replaced in the at-scale evaluation path by `SubagentScorer` (`llm_scoring.py`, issue 0019 Phase B): one binary subagent call per `(gold_evidence_fact, text)`, output PRESENT | ABSENT, aggregated to continuous `[0,1]`. Phrase-match retained as fallback when `scorer is None`. Verbatim Event Oracle boundary (`evidence_recall_from_text(gold_evidence, memory_item.text)`) keeps phrase-match for the structural boundary check.

**Remaining limitation.** Same as §2: LLM scorer variance + per-fact subagent call cost (~6000 calls per 596-case re-test). Caching by `(fact_hash, text_hash)` mitigates repeated runs.

---

## 7. Evaluation Scope (Implementation)

**What it is.** The current evaluation covers:
- 596 synthetic probe cases (MemoryArena 198, LongMemEval 200, ToolBench 198)
- 4 baseline comparators (fixed-summary, vector-memory, evidence-recall heuristic, subagent judge)
- 2 adapter targets with recorded-trace mode (mem0, Letta)
- Deterministic scoring throughout

Not yet evaluated:
- Real agent deployment traces (production failure distribution)
- LLM-based semantic scoring (answer quality, evidence entailment)
- Cross-adapter generalization beyond mem0 and Letta
- Human baseline comparison (how well do human debuggers diagnose the same failures?)
- Real-time replay cost (latency, token consumption)

**What claims are supported.** Current evidence supports: (1) counterfactual replay attributing synthetic perturbations to correct pipeline operations, (2) deterministic scoring providing a verified lower bound, (3) adapter-label parity confirming cross-system generalization for two distinct memory architectures. External validity claims require real deployment data.

---

## 8. Closed-World Label Taxonomy (Methodological)

**What it is.** CMD attributes every failure to one of 11 predefined pipeline labels (or top-k when deltas are close). The taxonomy is closed-world: failures that do not fit any label are forced into the closest match.

**Impact.** Novel failure modes—from new memory architectures, new agent behaviors, or new interaction patterns—would be misattributed. The 11-label taxonomy covers all known pipeline operations in current memory-augmented agents, but future architectures may introduce operations not in the set (e.g., memory negotiation, cross-session memory merging, memory-forgetting policies).

**Mitigation.** The label taxonomy is extensible: adding a new label requires (1) defining the counterfactual replay intervention that fixes it, (2) adding it to the replay portfolio, (3) validating it on synthetic probe cases. V1 added 5 labels to V0's 6; the process is structured and tested. Open-world detection (flagging failures that do not fit any known label) is recognized as important but is not part of the current system.

---

## 9. Evaluator-Annotator Circularity (Methodological)

**What it is.** The 596 `perturbation_label`s were assigned by deepseek-v4-pro-max (LLM annotator). Macro F1 measured against this label set is by definition CMD-vs-LLM-annotator agreement, not ground-truth attribution accuracy. A reviewer pointing this out can dismiss any large-N number that depends on these labels.

**Why it cannot be fully eliminated for the 596 set.** Hand-labeling 596 cases at ~5 minutes each is ~50 hours, beyond timeline budget.

**Mitigation (Decision 34 R4+R11).** Two-tier evaluation with researcher-grade headline:
- **Headline (small, researcher-grade)**: 130 cases stratified ~16 per active label across 8 labels, hand-labeled by researcher with LLM-A (`llama-3.3-70b-instruct`) candidate suggestion + accept/reject. LLM-A is family-disjoint from deepseek annotator, qwen agent, and evaluator scorer (three-independent-LLMs rule). Researcher confidence ∈ {high, medium, low}; high+medium → headline, low → appendix.
  - **Automation-bias countermeasure**: 20 cases labeled blind first (no LLM-A); same 20 re-labeled with LLM-A. κ(researcher_blind, researcher_assisted) reported. If κ < 0.7, redo without LLM-A.
- **Scale sanity (large, LLM-annotated)**: 596 cases under same scorer/agent stack as headline, framed as "CMD reproduces deepseek-v4-pro-max labels at Macro F1 = Y." Functions as regression check.

**Why this is acceptable.** No memory-debugging benchmark currently exists with researcher-adjudicated labels at scale. Community baseline is "annotator-LLM produces labels, methods evaluated against them." CMD's claim is one step stronger: LLM-A-assisted human adjudication on a representative subset, with automation-bias measurement. V2 / community-resource scope: scale researcher-labeling to 500+ cases via crowdsourced annotation with multi-rater κ.

**V1.0 → V1.1 expansion**: post-issue-0035, full corpus rebuild + re-annotation. Researcher 130-case set re-sampled from new pool. Bootstrap CIs preserved.

**Implementation artifacts**:
- `data/probe_cases/researcher_labeled_subset.json` — 130 cases with researcher labels + confidence + LLM-A suggestion + disagreement flags.
- `scripts/annotate_perturbation_labels.py` — recovered or reconstructed deepseek prompt + run script (R4-prov).
- `artifacts/researcher_vs_deepseek_kappa.txt` — Cohen's κ + bootstrap CI between annotators.
- `artifacts/automation_bias_kappa.txt` — κ(researcher_blind, researcher_assisted) on 20-case spot-check.
- Methods section reports: headline on 130, scale on 596, two-evaluator robustness on 130, three κ values (researcher↔deepseek, blind↔assisted, evaluator-A↔evaluator-B).

---

## Summary

| Limitation | Type | Severity | Mitigation | Status |
|-----------|------|----------|------------|--------|
| Gold evidence dependency | Methodological | High (4/11 labels) | Two-tier deployment; surrogate self-supervision (issue 0036 retention% measurement) | V1 candidate generation; V2 deployment validation |
| LLM-based scoring (was: deterministic) | Implementation | Resolved | LLM `agent_generate` + independent scorer + AnswerVerifier per Decision 34 R1+R2 | Wiring sprint 05-25~28 |
| Synthetic + LLM-annotated perturbations | Evaluation | Medium | Researcher-adjudicated 130-case headline (R4) + LLM-A assist + 20-case blind spot-check (R11) | 05-30~01 (5 hr researcher) |
| Single-agent scope | Methodological | Medium | Shapley + CMD composition | V2 |
| Operation-level granularity | Methodological | Low | Per-item replay + bad-item labels | V2 |
| Phrase-matching evidence recall (was) | Implementation | Resolved | `SubagentScorer` replaces phrase-match in eval path | Wiring sprint |
| Evaluation scope | Implementation | Medium | Real traces, human baseline, cost measurement (cost in headline per R10) | V1.0 → V1.1 → V2 |
| Closed-world taxonomy | Methodological | Low | Open-world detection; taxonomy extension | V2 |
| Evaluator-annotator circularity (new §9) | Methodological | High before R4+R11, Medium after | LLM-A-assisted human adjudication + 20-case blind spot-check + automation-bias κ | R4+R11 in progress |

CMD's primary remaining limitation after Decision 34 is gold evidence dependency for content-absence diagnosis (information-theoretic bound) and evaluator-annotator circularity at scale (mitigated by hand-labeled headline subset; full scaling to N≥500 with multi-rater κ is community-resource work).
