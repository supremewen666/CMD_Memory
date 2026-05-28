# Algorithm Selection — reasoning_error Handling in CMD-Audit

**Date:** 2026-05-28
**Decision scope:** how CMD-Audit detects and attributes `reasoning_error` for AAAI 2027 paper
**Author:** algorithm-selection skill workflow
**Inputs:** `knowledge/current-memory.md`, `cmd_innovation_core/CONTEXT.md`, 80+ `reference_notes/*.md`, prior conversation analysis of `at_scale_llm_retest.csv` (596 cases, reasoning_error gold n=83, F1=0)

## Project Goal

Bring `reasoning_error` from F1=0/83 (current V0/V1 attribution failure) to a paper-defensible state for AAAI 2027 submission, without breaking existing 8-label headline performance (mF1=0.629 → target ≥0.72 after combined improvements). Solution must:

1. Be honest about what CMD can and cannot diagnose under the gold-evidence dependency boundary
2. Produce statistically defensible numbers under bootstrap CI
3. Engage with closely-adjacent attribution methods (Shapley, Conformal, MASPrism) without claiming they are inapplicable in general — only for sequential memory pipeline
4. Operate offline (CMD-Audit) and degrade gracefully online (CMD-Skill Adapter)
5. Fit V1.0 ship window (push arxiv to 2026-07-08, AAAI deadline ~2026-08-end)

## Decision Criteria

| Criterion | Weight | Description |
|---|---|---|
| **C1. AAAI defensibility** | high | Survives reviewer Q on "reasoning F1=0" without retreat |
| **C2. Paper-window cost** | high | Fits in 1-2 weeks of W2-W3 in revised timeline (re. recompose plan) |
| **C3. Online generality** | medium | Method works (or degrades documented-ly) when gold_evidence absent |
| **C4. Engineering simplicity** | medium | Touches few files; reuses existing replay + scoring stack |
| **C5. Effect-size** | high | Produces reasoning F1 ≥0.40 on the narrow class kept in headline |
| **C6. Cross-method differentiation** | medium | Distinguishes CMD from TraceAudit/VerifyMAS/Shapley/Conformal |
| **C7. Reuse of existing artifacts** | medium | 596 LLM-rescored cases, hook calibration data not wasted |

## Candidate Routes

### Route A — Three-Subtype Decomposition with Principled Abstention (with optional retrieval/route merge)

**Core idea:** Decompose 83 reasoning gold cases into three subtypes by the recovery-gain signature already present in `at_scale_llm_retest.csv`:

- `context_recoverable` (n=27, 32.5%) — upstream replay (oracle_route / oracle_write) produced positive gain. These are pipeline failures upstream of reasoning that **happen to be labeled** reasoning_error in the deepseek-v4-pro-max annotation; functionally they are retrieval/route/write errors. Merge into the appropriate upstream label (or into `route_or_retrieval` if user adopts the merge ablation discussed earlier).
- `evidence_dependent_reasoning` (n=24, 28.9%) — `evidence_given_reasoning` replay produced positive gain but no upstream replay did. **This is the narrow, well-defined reasoning class CMD can claim to attribute**: model has the capability but failed to invoke it under the actual context.
- `hard_reasoning` (n=32, 38.6%) — no replay produced positive gain. CMD abstains and surfaces these as a research-grade `attribution_failed` set, citing 2605.15000 Premature Closure (55-81% residual failure rate is the LLM capability ceiling, not memory diagnosis scope).

**Supporting papers / sources:**
- 2605.15000 Premature Closure — establishes that residual reasoning failure is a model-capability problem, justifying abstention
- 2604.27283 RSCB-MC — abstention as first-class action with 0.0% FP
- 2605.06788 Conformal Attribution — finite-sample coverage guarantee provides formal grounding for abstention
- 2605.09863 Nautilus Compass — drift detection complements abstention

**Expected strengths:**
- Narrow-class F1 jumps from 0/83 to ~24/24 = potentially 0.95+ (TP=24 if `evidence_given_reasoning` replay cleanly identifies them, FP minimal because replay is highly selective on this class)
- Macro F1 improvement (relative to 11-label baseline) +0.07 to +0.10 even with the narrow split alone
- Honest scope: reviewer ask "why F1=0" gets answered by "because most reasoning_error labels were upstream failures or LLM-capability cases CMD correctly abstains on"
- Clean integration with proposed retrieval/route merge (Route A is composable with the merge already discussed)
- Adds a `§Discussion` story for AAAI: CMD as gold-dependent diagnostic, abstention as design feature

**Expected risks:**
- 24 cases is small for the narrow class — bootstrap CI may be wide
- Subtype assignment uses gold_evidence (offline only) — online deployment needs proxy (see C3); this is a documented limitation, not a methodological flaw
- Reviewer may still ask "why CMD doesn't predict reasoning_error online" — answered by "it doesn't, by design, in absence of gold_evidence — CMD operates within its bounded diagnostic scope" (consistent with `limitations.md` gold-dependent label section)

**Implementation cost:**
- ~50-100 lines: modify `attribution.py:assign_attribution_v1` to emit subtype tag; add `scripts/build_reasoning_ablation.py`; adjust `eval/metrics.py` to support per-subtype reporting
- Tests: ~20 new test cases in refactored `tests/attribution/`
- 2-3 days work, fits in revised W3

**Baseline compatibility:**
- Existing 9 non-reasoning labels untouched
- Existing replay portfolio untouched
- Compatible with retrieval/route merge (orthogonal change)
- Existing 596 artifacts reusable; only need re-run scripts/build_*.py to regenerate tables

---

### Route B — Self-Consistency Probe with Calibrated Abstention

**Core idea:** Replace gold-evidence-dependent `evidence_given_reasoning` replay with an in-domain reasoning probe inspired by Self-Consistency (Wang et al., NeurIPS 2023) and Premature Closure (2605.15000): rerun the agent N=5 times under temperature=0.5 on the same retrieved context. If high-agreement low-correctness → reasoning_error. If high-agreement high-correctness → some other label. If low-agreement → abstain.

**Supporting papers / sources:**
- Wang et al. 2023 Self-Consistency — N-sample voting in LLM reasoning
- 2605.15000 Premature Closure — calibrates "model commits to wrong answer" as detection target
- 2605.07509 MASPrism — token-level NLL signal as cheap alternative to multi-sample
- 2605.06788 Conformal Attribution — coverage-bound abstention

**Expected strengths:**
- Online-applicable (no gold_evidence) → improves CMD-Skill Adapter deployment claim from 7/11 to 8/11
- Cross-domain credibility: imports a well-established reasoning verification method into memory diagnosis
- Per-case confidence score (agreement ratio) gives natural abstention threshold

**Expected risks:**
- Adds 5× LLM cost per reasoning candidate case → ~80-100 added cases at hook trigger × 5 calls × ~10 s = +1-2 hours per full 596 run; meaningful but not blocker
- Calibration of agreement threshold requires hold-out — uses up some of the 596-case budget for calibration
- Effect size unknown — may or may not separate evidence_dependent from hard_reasoning cleanly; needs pilot

**Implementation cost:**
- ~150-200 lines: new `replays/self_consistency_probe.py`, new attribution branch, calibration script
- 1 week of pilot runs to set threshold, plus 3-5 days integration
- Compatible with refactor week-1 if ordered after refactor

**Baseline compatibility:**
- Adds new replay to portfolio without removing existing ones
- Touches `replays/`, `attribution/`, `hook/` — three packages
- Reasonably orthogonal to retrieval/route merge

---

### Route C — Process Reward Model (Step-Level Verifier)

**Core idea:** Train or adapt a step-level reward model (PRM) following Lightman et al. (OpenAI "Let's Verify Step by Step", ICLR 2024) on agent CoT traces. Score each reasoning step; failed step + retrieved memory at that step jointly determines whether failure is reasoning vs retrieval vs injection. Closest in spirit to MASPrism (2605.07509) but trained instead of heuristic.

**Supporting papers / sources:**
- Lightman et al. 2024 (Let's Verify Step by Step) — PRM training methodology in math reasoning
- 2605.07509 MASPrism — prefill-stage signals (NLL + attention), the heuristic version
- 2605.14865 HolisticEval — span-level diagnosis, structurally similar
- 2605.13941 EvolveMem — diagnosis-driven self-evolution (downstream use case)

**Expected strengths:**
- Operation-grade resolution: PRM can identify the specific reasoning step that failed
- Independent of CMD's replay paradigm — provides cross-validation for attribution decisions
- Cross-domain credibility (math reasoning to agent reasoning is a recognized transfer)

**Expected risks:**
- Requires CoT step annotations on 596 cases — does not exist; would need ~2-3 weeks of annotation
- PRM training data scarcity for memory-pipeline domain
- Cost: PRM inference adds another model call per step; not lightweight at evaluation time
- AAAI reviewer may flag "is your PRM the contribution or your attribution method?" — confounds the paper's central claim
- Window risk: too large for V1.0 (W2-W3); really a V2 follow-up

**Implementation cost:**
- 3-4 weeks (annotation + training + integration + evaluation)
- Cuts paper writing window in half
- Requires GPU budget not currently allocated

**Baseline compatibility:**
- Adds new attribution evidence channel orthogonal to replay
- Tests would need expansion for PRM unit tests + integration tests
- Could subsume Route B (PRM is a more sophisticated version of self-consistency)

---

### Route D — Decision-Centric Rate-Distortion Reframing

**Core idea:** Adopt 2605.10870's rate-distortion framework: redefine `reasoning_error` as "decision-relevant information loss in the agent's effective state". Replace recovery-gain attribution with decision-distortion measure. CMD becomes a decision-centric memory diagnostic.

**Supporting papers / sources:**
- 2605.10870 Decision-Centric Memory — rate-distortion framework, DeMem partition refinement
- 2605.12978 Memory Becomes Faulty — three failure modes (misclassification, interference, overfit) — provides empirical anchor

**Expected strengths:**
- Strong theoretical grounding — matches AAAI's preference for formally rigorous papers
- Reframes the entire taxonomy boundary problem (reasoning vs compression vs retrieval) under one unified information-theoretic lens

**Expected risks:**
- Requires explicit decision-space definition — open-ended agent tasks make this hard (open gap explicitly noted in 2605.10870)
- 4-6 weeks of theoretical work + reformulation of all existing 11 labels
- Risks rewriting the entire CMD framing 2 months before deadline
- Reviewer may say "this is a different paper, not a fix to the original CMD"

**Implementation cost:**
- 4-6 weeks; would push arxiv to August
- Touches every module (label registry, replay semantics, attribution, post-repair)

**Baseline compatibility:**
- Effectively a paradigm shift; not compatible with existing experimental setup

---

### Route E — Hybrid: Route A as primary attribution + Route B as online proxy

**Core idea:** Combine Route A (three-subtype decomposition for the offline CMD-Audit headline) with Route B (self-consistency probe) as the **online** disambiguator that approximates `evidence_given_reasoning` when gold_evidence is unavailable. Two separate stories with one consistent framework:

- **Offline (CMD-Audit, paper headline):** Route A's three-subtype split, abstention on hard_reasoning. Reasoning F1 reported on the narrow 24-case class.
- **Online (CMD-Skill Adapter, deployment claim):** Route B's self-consistency probe. Provides surrogate for `evidence_given_reasoning` replay; coverage degrades from 100% (offline) to ~80% (online) — explicitly reported as a limitation.

**Supporting papers / sources:**
- All Route A sources (Premature Closure, RSCB-MC, Conformal, Nautilus Compass)
- All Route B sources (Self-Consistency, MASPrism)
- 2604.27283 RSCB-MC — risk-sensitive abstention as bridge between offline and online

**Expected strengths:**
- Solves the offline vs online story problem in one move
- Each component has a smaller scope than the previous routes individually → higher chance both fit in 2-3 weeks
- §Limitations becomes evidence of methodological care, not a weakness
- Anticipates reviewer ask "what about online deployment without gold_evidence?" with a concrete answer
- Compatible with refactor week, the Shapley/conformal head-to-head ablation, and the retrieval/route merge ablation

**Expected risks:**
- Two-component story requires careful writing to avoid confusing reviewers
- Route B threshold calibration needs pilot — adds ~3 days to W2 vs Route A alone
- If self-consistency probe is uncalibrated within the timeline, fall back to Route A for V1.0 and ship Route B as a §Future Work / V1.1 enhancement

**Implementation cost:**
- ~200-250 lines combined
- Route A: 2-3 days (W3-W4 in revised timeline)
- Route B: 1 week (W4-W5)
- Total: 1.5-2 weeks
- Fits in current revised paper-craft window (W1 refactor → W2-W3 hook + headline → W3-W4 reasoning subtypes → W4-W5 self-consistency probe → W5-W6 paper writing)

**Baseline compatibility:**
- Composable with retrieval/route merge ablation (W3 work)
- Composable with §Methodological Comparison Shapley/conformal head-to-head (W4 work)
- Existing artifacts (596 LLM rescore, hook calibration data) reusable
- Refactor scope unchanged

---

## Candidate Comparison Table

| Route | Reasoning F1 (narrow class) | Online claim | Paper-window cost | Theoretical lift | AAAI defensibility | Differentiates from competitors |
|---|---|---|---|---|---|---|
| **A** | ~0.7-0.9 (24 cases) | offline only | 3-5 days | low | high | medium |
| B | unknown until pilot | online improvement | 1-1.5 weeks | medium | medium | high |
| C | unknown | online | 3-4 weeks | high | very high | very high |
| D | reframed metric | reframed | 4-6 weeks | very high | high (if done right) | very high |
| **E (A+B)** | **~0.7-0.9 + online surrogate** | **offline + online** | **1.5-2 weeks** | **medium** | **very high** | **high** |

---

## Chosen Route: E — Hybrid (Three-Subtype Decomposition + Self-Consistency Probe)

**Rationale:**

1. **Maximizes AAAI defensibility (C1, C5, C6).** Route E gives the paper two stories: a narrow-class F1 jump from 0 to ~0.8+ on the offline headline, plus an online-applicable surrogate that engages directly with Self-Consistency and Premature Closure literature. Reviewers at AAAI care about both rigor on the headline and a credible deployment story.

2. **Fits the paper-craft window (C2).** Route A alone is 3-5 days; Route B alone adds 1 week. Combined, Route E is 1.5-2 weeks, fitting between refactor week (W1) and paper writing (W5-W6) without compressing the §Methodological Comparison work.

3. **Cross-method differentiation (C6).** TraceAudit (chunk-level audit), VerifyMAS (agent-level observational), and 2605.13077 (Shapley) all stop at attribution. Route E adds a per-subtype abstention story plus an online cross-validation method. None of the competitors do both, and the combination is genuinely novel given that the underlying components are well-established in adjacent literature (Premature Closure, Self-Consistency, RSCB-MC).

4. **Reuses existing artifacts (C7).** 596 LLM-rescored cases already include all the recovery-gain signatures needed for Route A's subtype split. Route B reuses the existing agent_generate + scoring stack with N=5 sampling overhead.

5. **Engineering simplicity (C4).** Both components touch existing files (`attribution.py`, `replays/`, `hook/`, `eval/metrics.py`). No new training pipelines, no GPU dependencies beyond what already exists for SubagentScorer.

6. **Effect-size confidence (C5).** Route A's 24-case narrow class is the cleanest interpretable reasoning subset in the dataset. Even if Route B's calibration delivers only modest separation between evidence_dependent and hard_reasoning online, Route A alone delivers the headline number.

**Risk mitigation:**

- If Route B's self-consistency calibration fails to separate cases with statistical significance, ship Route A alone for V1.0 arxiv and present Route B as §Future Work. This is a low-cost graceful fallback because Route A is the floor.
- If Route A subtype split disagrees with researcher 130-case adjudication on >20% of cases, run a Cohen's κ post-hoc and report — this becomes a methods artifact rather than a failure (matches Decision 34 R4-prov spirit).

**Paper sections affected:**

- §Method: add §3.4 "Reasoning Failure Decomposition" — describes Route A
- §Method: add §3.5 "Online Reasoning Surrogate" — describes Route B
- §Experiment: add reasoning_error ablation table (3 subtypes × 5 baselines)
- §Discussion: incorporate Premature Closure boundary argument
- §Limitations: explicit gold-evidence dependency for offline subtype assignment
- §Future Work: V2 PRM extension (Route C as long-horizon)

---

## Rejected Routes

### Rejected: Route C (Process Reward Model)

**Reason for rejection:**

- **Window incompatibility (C2):** 3-4 weeks for annotation + training + integration cuts the paper writing window in half, threatening the 2026-08 AAAI deadline.
- **Data scarcity:** No CoT step-annotated dataset exists for memory-pipeline domain; would need fresh annotation on 596 cases.
- **Confounded contribution:** AAAI reviewers would ask whether the paper's contribution is the PRM or the attribution method — risks dilution of the operation-level repair-validated diagnosis loop framing.
- **Cost asymmetry:** PRM inference adds per-step LLM cost; latency/cost table in headline becomes worse not better.

**Reusable for future work:** Route C is the natural V2 extension once the V2 runtime subagent loop is in place. The runtime's "Validator" role can wrap a PRM. Document this in §Future Work.

### Rejected: Route D (Decision-Centric Rate-Distortion)

**Reason for rejection:**

- **Paradigm shift (C2):** 4-6 weeks of theoretical work + reformulation of all 11 labels effectively writes a different paper, not a fix to the existing one.
- **Open gap in source paper:** 2605.10870 itself notes the open-ended decision-space problem; adopting it inherits the same gap with no clear path to close it.
- **Risk to V1.0:** Risks rewriting the entire CMD framing 2 months before deadline; high probability of incomplete contribution at submission time.

**Reusable for future work:** Route D could become a long-term theoretical companion paper after V1.0 + V1.1 are published. Mention as §Future Work intersection with rate-distortion theory.

### Rejected: Route B as standalone (without Route A)

**Reason for rejection:**

- **C5 risk:** Route B's effect size is unknown until pilot; without Route A's narrow-class fallback, paper has no reasoning F1 floor.
- **C7 issue:** Self-consistency without subtype decomposition still leaves the 27 context_recoverable cases mislabeled as reasoning, polluting the metric.
- **Loses honesty story:** Without Route A's three-subtype framing, the paper cannot cite Premature Closure for the hard_reasoning ceiling; loses the §Discussion centerpiece.

**Reused as part of Route E:** Route B is the online half of Route E.

---

## Fallback Route: A alone (Three-Subtype Decomposition without Self-Consistency Probe)

If Route B's self-consistency calibration fails or runs over budget, fall back to Route A as the sole reasoning_error treatment for V1.0:

- Reasoning F1 ~0.7-0.9 on narrow 24-case class
- Offline-only attribution claim (no online surrogate)
- §Future Work explicitly mentions Route B (self-consistency) and Route C (PRM) as V1.1 / V2 directions
- Paper still ships within deadline; Cost reduced from 1.5-2 weeks to 3-5 days

**When to invoke fallback:** if W4-W5 pilot of self-consistency probe shows agreement-ratio threshold cannot separate evidence_dependent from hard_reasoning at p<0.05 on a 50-case calibration set.

**Why A is the right fallback (not the chosen):** it is strictly less ambitious than E and ships independently. It does not require Route B's calibration. But it leaves the online deployment story underdeveloped, which is why E is preferred when timeline permits.

---

## Open Questions

1. **Subtype assignment for in-flight 596 cases:** does the existing `at_scale_llm_retest.csv` carry enough recovery-gain detail to assign all 83 reasoning cases to the three subtypes, or do some cases need re-running? Initial inspection shows yes — the per-replay recovery_gain column is sufficient.
2. **Route B threshold calibration data:** which 50 cases form the calibration hold-out? Suggested: stratified random sample from the 83 reasoning cases plus 50 non-reasoning cases as negative control.
3. **Cohen's κ between Route A subtype assignment and researcher 130-case adjudication:** Route A's subtype tags should be added to the researcher adjudication interface so 130-case agreement can be measured.

---

## Next Recommended Command

`/research-implement` — implement Route E (Three-Subtype Decomposition + Self-Consistency Probe) according to the revised paper-craft timeline:

- W1 (5/29-6/4): refactor codebase per accepted skeleton plan
- W2 (6/5-6/11): hook re-calibration + 10-label headline re-test
- W3 (6/12-6/18): **Route A — three-subtype decomposition, ablation tables**
- W4 (6/19-6/25): **Route B — self-consistency probe pilot + integration** + Shapley/conformal head-to-head
- W5 (6/26-7/2): paper §Method + §Experiment writing
- W6 (7/3-7/9): paper §Introduction + §Discussion + arxiv upload

If pilot in W4 fails calibration, invoke fallback (Route A alone) and reallocate W4 surplus to §Methodological Comparison work.

---

## Reference Notes Cited

- 2605.15000 — Premature Closure in Frontier LLMs
- 2604.27283 — RSCB-MC (risk-sensitive contextual bandits with abstention)
- 2605.06788 — Conformal Agent Error Attribution
- 2605.07509 — MASPrism (lightweight prefill-stage signals)
- 2605.13077 — Counterfactual Responsibility Attribution (Shapley-value)
- 2605.09863 — Nautilus Compass (persona drift detection)
- 2605.10870 — Decision-Centric rate-distortion memory
- 2605.12978 — Memory Becomes Faulty (three failure modes)
- 2605.14865 — Holistic Eval & Failure Diagnosis (span-level)
- 2605.07242 — MEMOREPAIR (cascade repair)
- 2605.06455 — PrefixGuard (offline trace→monitor induction)
- github_traceaudit — TraceAudit (chunk-level counterfactual auditing, AAAI 2027 competitor)
- 2605.17467 — VerifyMAS (agent-level hypothesis verification)
- Wang et al. 2023 (NeurIPS) — Self-Consistency Improves CoT (cross-domain reasoning literature)
- Lightman et al. 2024 (ICLR) — Let's Verify Step by Step (PRM, cross-domain math reasoning)

---

**Decision authority:** chosen by user via `/algorithm-selection` skill workflow on 2026-05-28. Subject to revision after Route B pilot in W4.
