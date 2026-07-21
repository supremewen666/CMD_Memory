# CMD as an Evolvable Memory-Repair Skill System — Design + Code Audit

Status: proposal (2026-06). Supersedes the "guidance-text evolution" line, which is
empirically dead (Exp20 v1+v2: answer-time guidance adds ~0; deployable gate = 0).

## 1. Positioning (the narrow, un-occupied gap)

The *mechanism* — counterfactual/outcome-grounded attribution, failure→rule/skill
distillation, accept-if-improves, credit assignment — is **occupied** in 2025–26
(REFLECT, CausalFlow, CAR, MNL, RIMRULE, Reflexion/ExpeL). We do **not** claim the
mechanism.

What no one has done: apply it to the **agent memory subsystem**. All competitors
target reasoning / tool-call / action-step failures. CMD's novelty is:

> **First counterfactual repair + evolvable skill library for the agent-memory
> pipeline** — a memory-specific failure ontology (retrieval / injection /
> granularity / safety + item-level stale / conflict / poison / compression) and a
> memory-specific repair *action space*, shown to be localized, repairable, and
> abstractable into reusable skills.

Novelty = **domain + memory failure ontology + memory repair operators**, not the
loop mechanism. Carry the novelty in the skill *body* (memory repair actions), not
the skill *form* (an evolving notebook is shared with MNL/RIMRULE).

## 2. Target architecture

CMD = **an agent with memory-repair tools + an evolvable memory-repair skill library.**

```
recall → (Fill/Fix confidence gate)
  └─ Fix → retrieve SKILL by failure fingerprint
            → execute skill.body = a memory-repair OPERATOR (structural)
            → re-answer over the repaired context
            → recovery-gain GATE (accept-if-improves; surrogate online)
            → if recovered & novel: distill/refine skill back into the library
```

A **skill** is the ECC/Hermes form (verified against `ecc:rules-distill`):

| Skill element | CMD memory-repair skill |
|---|---|
| frontmatter `description` / `When to Use` | **trigger** = recall-content fingerprint pattern (gold-free) |
| `How It Works` (executable steps) | **body** = a repair OPERATOR: `SELECT(predicate over item metadata/text) × TRANSFORM(add-from-store \| expand-granularity \| re-emit \| promote \| demote \| de-conflict)`, single / composite / parameterized |
| acceptance criteria ("include ONLY if improves") | **gate** = recovery-gain ≥ threshold on ≥k held-out same-fingerprint cases |

### The one invariant that keeps this alive (and distinct from the dead path)

**Skill body is an EXECUTED operator (changes the context the agent reads), NOT a
hint injected into a frozen answerer's prompt.** Exp20 proved the latter is dead. The
skill text is consumed by an *agent that takes the repair actions*, then answers over
the corrected context. This is the action/guidance line: **action = fix the evidence;
guidance = ask the model to think differently.** We keep only the first.

Construction is gold-free (operators read recall/store metadata + text, never
`case.gold_*`). Fitness = recovery gain (gold answer offline; retention-rate /
post-repair surrogate online). **No label is ever a prediction target** — the 4 step
actions / 5 item labels are operator names in the action space, selected by recovery,
never scored as macro-F1.

## 3. THE GATE (do this before building the library)

Everything below is conditional on headroom. Run first:

**Exp21 — composite/parameterized residual scan.** On the multihop residual (115
cases where single-point structural repair fails; 0% are data floor), extend the
single-point exhaustive scan to **double-point + parameterized** operators
(add-from-store + demote-distractor via `item_signal_hints`). Measure recovery over
single-point.
- **Headroom → build the skill library (this doc).** Operator evolution is real.
- **No headroom (C6 wall holds; C6 saw coupled 1/30) → fall back:** skill library
  becomes the *reuse/efficiency* mechanism only (Exp18/19), evolution claim = "faster
  cheaper repair from experience," not "extends what's fixable." Still honest, less novel.

### Gate 1 result (Exp21/22): PASSED — but STATIC only

Exp21 headroom (34–37/115, p~1e-10) and Exp22 LOO transfer (`comp_fp_topN`≈oracle,
fp-key vs random 26/8 p=2.9e-3) confirm the operator library *extends fixability* and
*transfers*. Both are **static** measures.

### Gate 2 (next, UNRUN): online operator trajectory = the "逐代进化" claim

C7/Exp18's "warm-up reuse, recovery flat" was measured on the **OLD guidance/label
mechanism** (`failure_memory_skill/` is still boilerplate `Repair Guide` keyed by
diagnosis label — the unmodified `format_pattern`). The **operator** mechanism has
never been run as an online trajectory. So "per-generation evolution" is **untested,
not refuted**. Run Exp24: online operator stream with fingerprint key +
accept-if-improves + **multi-shape-per-fingerprint accumulation** on the residual
stream; measure whether recovery climbs toward the richer ceiling (0.74) as the
library thickens (Exp22 lever: thin 1-shape library = 0.58).
- **Recovery climbs → 逐代 capability evolution proven → ship step-only; do NOT extend to item.**
- **Recovery flat → write the item layer (Exp23) as a layered-dimensionality finding.**

### Item layer (Exp23) status: repairable, library FAILS to transfer

Single-point item repair works (stale 0.732 / conflict 0.568), but the fingerprint
library does NOT beat same-budget random at the item layer (`item_fp_topN` vs random
p=0.33 ns) — the opposite of step (Exp22). Item operators are outcome-redundant
(small space), so there is nothing for retrieval to discriminate. Item is a DOMAIN-
breadth contribution, **not** a substitute for the 逐代 evolution claim, and is gated
on Exp24 (only write it if Gate 2 fails).

## 4. Code audit — KEEP / MODIFY / RETIRE

### KEEP (already aligned with the operator-skill architecture)
| Module | Role |
|---|---|
| `counterfactual/actions.py` | operator executor (`apply_pipeline_action`, `get_legal_actions`, 4 `_repair_*`, **`item_signal_hints` param channel**). The core — extend, don't replace. |
| `counterfactual/rollout.py`, `context.py` | rollout-to-terminal + recovery gain. (also see FIX below) |
| `repair/efficacy.py` | `run_single_repair` — gold-free executor. The skill-execution primitive. |
| `repair/failure_memory.py` | fingerprint-keyed store + skill loop + markdown library (see MODIFY for `format_pattern`). |
| `hook/` | Fill/Fix confidence gate — the online router; also the natural confidence gate. |
| `scoring/` | G-Eval recovery gain — the fitness signal. |
| `core/models.py`, `core/labels.py` | dataclasses + label SETS (kept as action-space names, not targets). |
| `eval/provenance.py`, `eval/writers.py`, `eval/surrogate_gap.py` | provenance, CSV writers, gold-free surrogate. |
| `data_io/` | loaders (extend with item-level data — see §5). |

### MODIFY
| Module | Change |
|---|---|
| `repair/failure_memory.py` :: `format_pattern` | Emit an **operator-spec skill** (trigger fingerprint + the recovered `(gp, action, params)` + recovery track record), NOT the constant boilerplate "Repair Guide". This is the skill *body*. The `llm_client` path may draft the human-readable trigger/description; the executable body stays structured. |
| `counterfactual/actions.py` | EXTEND: lift the latent `SELECT(predicate) × TRANSFORM(primitive)` structure (already implicit in `_retrieval_missed_items`/`_coarse_recall_items` + `_order_items_by_signal`) into a **small composable operator DSL**; make composite (action sequences) and parameterized operators first-class. Keep signature-disjointness (no cross-action poaching). |
| `counterfactual/search.py` (`SinglePointAttributor`) | EXTEND to double-point / composite scan (Exp21), then to **skill-guided directed search** (try the fingerprint-matched skill's operator first, single-point remainder as fallback). |
| `harness.py` | Rewire the Fix path: fingerprint → retrieve skill → execute operator → recovery verify → distill. Feed `item_gate` output into operator params. **Remove diagnosis-metric / label-target computation from the live path.** |
| `item_gate/` | REPURPOSE: its output (`item_signal_hints`) becomes the **parameter source for item-level operators** (de-conflict stale, suppress poison, merge distorted), not a standalone Tier-2 classifier. Needs item-level DATA + experiments (currently untested). |
| `repair/post_repair.py` | KEEP the **Post-Repair recovery GATE** (accept-if-improves) and the gold-free corrected-content construction. RETIRE the `repair_guidance`/ECS-prose path into the answering context (see RETIRE). |
| `repair/actions.py` (`get_targeted_repair_action_v1`) | Demote `cause`/`repair_guidance` text to operator **documentation/label**; do NOT inject into the answer prompt. |

### RETIRE (退场)
| Target | Why |
|---|---|
| **Answer-time guidance injection** — `ECSDraft.repair_guidance` / `cause` fed into the answering context (`repair/post_repair.py`, `repair/ecs.py`) | Exp20 v1+v2: net ~0 ungated, +2 n.s. oracle-gated, deployable gate = 0. Dead as a recovery mechanism. |
| `repair/ecs.py` contrastive ECS **as a value mechanism** | C8: `full_ecs ≈ raw_corrected` (n.s.), `solution` is the *worst* content arm. Structure adds nothing over plain corrected content. Keep only as a content carrier if needed; drop the "structure helps" claim. |
| `eval/metrics.py` :: `compute_diagnosis_metrics` / `DiagnosisMetrics` (macro-F1); its calls in `harness.py`, `eval/release_gates.py` | Label-as-prediction-target — the paradigm the project pivoted away from. Keep as an internal diagnostic only; never headline; remove from live/release path. |
| 4 step labels / 5 item labels **as prediction targets** | Retire as targets; keep as operator names in the action space. |
| `replays/` (`portfolio.py`, `interventions.py`, formation-oracle / reasoning / route replays) | Already off the live path (CLAUDE.md/TASK.md). The live path is `counterfactual/` + skill library. Retire remnants or keep only a thin historical import shim. |
| MCTS (already deleted: `tree.py`/`value.py`/`distill.py`) | C6: TRUE_COUPLED 1/30. Stays retired. `search.py` is single-point/directed only. |
| Exp20 `ecs_template` / `ecs_skill` **as production arms** | Keep as the negative result that *motivates* skill-as-operator; retire from the live repair path. |

## 5. Data + venue bar (parallel to the gate)

- **Item-level data is missing.** Mainline cases have no item structure; only
  `real_item_poisoned_hitl_cases.json` (9 cases, unused). To make item operators part
  of the skill library, build item-labeled cases (stale / conflict / poison /
  compression) — otherwise the item layer cannot be claimed.
- **Top-tier bar:** (a) ≥2 answering models (show effects aren't 7B-specific);
  (b) some real (non-synthetic) memory-failure cases; (c) evolution shown as more than
  warm-up (the Exp21 headroom + a transfer/coverage curve).

## 6. One-line summary

Keep the operator executor + recovery-gain + fingerprint memory; modify the skill
representation to an **executed operator spec** and rewire the Fix path around it;
retire answer-time guidance, contrastive-ECS-as-value, and all label-as-target
machinery. Gate the whole build on Exp21 showing composite/parameterized operators
recover residual that single-point cannot.
