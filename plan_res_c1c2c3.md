# Implementation Plan — C1/C2/C3 Identifiability Submission

task.md Phase 2 (2.1–2.5).  Four required sections: **Dataset**, **Estimator**,
**Protocol**, **Testing**.  §Definition / §Falsification / §Substrate are folded
into those four as task.md's per-row landing points require.

**Scope decision (Plan A, chosen 2026-08-18).** The contribution list is
**C1/C2/C3 only**.  The GHOST ecology — router, niche archive, lifecycle ledger,
governance — is a *system description* in setup/appendix and is **not expanded**
for this submission (task.md 6.4).  The only in-text niche evidence is one free
comparison already available from existing arms: `descriptor` vs `random_niche`
vs `unkeyed_pool` inside E4.  Rationale: the C1/C2/C3 narrative closes on its
own, promoting ecology would dilute the identifiability line, and the evidence
gap for niche *dynamics* (a driver, not just a ledger) cannot be filled inside
the submission time box.

This plan is written against APIs that exist on disk today.  Every named symbol
was verified before being cited; nothing here proposes new mechanism.

---

## §Definition — operational identifiability (2.1)

**Claim shape.** "Gold-free memory-repair evolution is identifiable" is not a
property of a method in isolation; it is a *separation* statement about arms
under a fixed prequential order.

**Definition.** Let `A_mech` be a mechanism arm and `A_ctrl ∈ {random_k,
identity}` be the two control arms already in `V4_ARMS`
(`experiments/v4_prequential_runner.py:61`).  Under arm-paired prequential
evaluation over a fixed case order, a mechanism is **identifiable** iff its
paired per-case advantage over *both* controls is positive and significant:

```
Δ(A_mech, A_ctrl) = mean_over_cases [ score(A_mech, case) - score(A_ctrl, case) ]
identifiable(A_mech) ⇔ ∀ A_ctrl ∈ {random_k, identity}:
                         Δ(A_mech, A_ctrl) > 0  ∧  CI_lower(Δ) > 0
```

Pairing is per case, not per arm mean: both arms see the same case at the same
stream position, so the difference removes case difficulty. `identity` bounds
"do nothing" and `random_k` bounds "act, but without the mechanism's keying" —
a mechanism that beats `identity` but not `random_k` has shown that acting
helps, not that its mechanism does. Both must be cleared.

**Why this definition and not accuracy.** A single-channel gold-free signal was
already measured as *unidentifiable* (frozen negative result: the stale arm
reproduced a 220.9 bias against 219.7, well below the 251 reference).  That
negative is a contribution, and it only reads as one because the arms were
paired against controls rather than scored absolutely.

**Significance judgement.** Paired bootstrap over cases via
`cmd_audit/eval/paired_stats.py` and `bootstrap.py`; the seed set is sealed in
§Protocol.  No new statistics are introduced.

**Reused concepts only.** `V4_ARMS`, `_UPDATING_ARMS`, prequential case order,
`V4CandidateOutcome`.  No new arm is defined by this plan.

---

## §Dataset

### Primary substrate (2.5)

`V4CandidateOutcome` (`v4_prequential_runner.py:95`) is the frozen multi-arm
shape everything must align to:

```
intent_id, recovery_gain, locality_cost, changed_item_count, valid, rolled_back
```

`recovery_gain` is the **audit reference**; the other four are the **telemetry
channels**.  Any substrate must supply both sides per candidate, per arm.

| Substrate | State on disk | Role |
|---|---|---|
| `data/evolution_v4/` | present (manifest + runtime/shadow cases) | E1 sealed confirmation, E4 ablation |
| `data/ghost_live_v2/` | present (partitioned, provenance recorded) | E4 Mix GHOST decomposition |
| `data/probe_cases/` | present | E2 construct-side, E3 density sweep |
| `data/complexity_trap/raw/` | **empty** — acquisition halted by operator instruction | third-party substrate for E2, *not currently available* |

**Consequence, stated rather than hidden.** task.md 2.5 names complexity-trap
three-arm traces (raw / observation_masking / llm_summary + outcome) as the
primary third-party substrate.  That directory is empty, so E2's third-party
requirement is served by the **session-log adapter substrate**
(`cmd_audit/adapters/session_log.py`, task.md 3.4) which implements exactly that
three-arm shape and is verified end-to-end through `run_case` on constructed
traces (`tests/adapters/test_session_log_adapter.py`).  The distinction is
load-bearing and must appear in the paper: the adapter and its arm semantics are
third-party-*shaped* and real, but the traces currently flowing through it are
constructed.  E2 therefore reports a **construct-substrate gap**, and the
third-party gap remains outstanding until the traces are fetched.  No number may
be labelled "third-party" on constructed input.

### Integrity constraints

- Construction of repaired context stays a pure function of
  `(recall_set, pipeline_action)`; `case.gold_*` never enters it.
- Telemetry channels are read via `TelemetryChannels.from_outcome`, which pulls
  four attributes by name and **deliberately excludes `recovery_gain`**
  (`telemetry_cmis.py:87-100`).  That exclusion is the gold-free boundary of the
  estimator and is asserted in tests, not assumed.
- Every reported case_id must be bidirectionally traceable to an on-disk
  dataset row (Phase 4.2), per the historical provenance-break lesson where
  headline artifacts and disk datasets had zero case_id overlap.

### ITEM_STALE exclusion on constructed substrates (O4)

`PipelineAction.ITEM_STALE` orders items by a timestamp parsed out of
`MemoryItem.store` (`counterfactual/actions.py:_item_timestamp`).  On a
constructed substrate that field is a **construction marker** — bijective with
`memory_id` — not an observed write time, so the action measures the fixture's
authoring order rather than memory staleness.  Existing `DECOUPLING_ARMS`
permute *telemetry*, not item metadata, so they cannot detect this shortcut.

Therefore: constructed-substrate runs pass
`intervention_config["store_timestamps_are_observed"] = False`, which withholds
`ITEM_STALE` and leaves every other item action intact
(`tests/counterfactual/test_counterfactual_attribution.py`).  The default stays
permissive so recorded traces carrying real write times are unaffected.

---

## §Estimator — C2 zero-call proxy and surrogate gap (2.3)

### Proxy

`telemetry_cmis_proxy(effect, channels)` (`eval/telemetry_cmis.py:102`) maps the
four typed channels to `[-1, 1]` at **zero LLM calls**:

```
if not valid or rolled_back:        -1.0                        # guard floor
elif effect ∈ {verify, abstain}:    success = (changed_item_count == 0)
else:                               success = (changed_item_count > 0)
proxy = clip(success - locality_cost - 0.05·changed_item_count, -1, 1)
```

### Reference

`replay_cmis(harm_before, harm_after)` = MemAudit Eq. 7.  Each `harm_after`
costs one agent replay upstream; the function itself only subtracts.

### Gap definition

```
gap = D( rank(proxy over candidates) , rank(replay_cmis over candidates) )
```
measured by `measure_telemetry_cmis_gap` (`telemetry_cmis.py:237`) using
Spearman correlation and pairwise concordance (`_spearman`,
`_pairwise_concordance`), and reported through
`eval/surrogate_gap.py::compute_surrogate_gap_summary`.

**Cost declaration.** Proxy side: 0 LLM calls.  Reference side: 1 agent replay
per `harm_after`.  The gap is exactly the price of that saving, and the paper
must report it as such rather than as a free win.

### Conditioned routing claim (O5) — required, not optional

`success = float(changed_item_count > 0)` means that **once the guard passes, a
repair that fixes an item and a repair that damages one score identically**.
The proxy measures *execution cleanliness*, not repair benefit; `valid` and
`rolled_back` are the only quality discriminators, and they are coarse.

So the routing claim is **conditional and must be stated that way**:

> The router optimizes a proxy for repair benefit **only on domains where the
> measured surrogate gap ≤ τ**; on all other domains it degrades to optimizing
> execution reliability.

Consequently E2 reports the gap **per domain**, never pooled, and τ is sealed in
§Protocol before any run.  A pooled gap would let a low-gap domain mask a
high-gap one and would make the routing claim unfalsifiable.

---

## §Protocol — sealed pre-registration and anchor discipline (2.2)

### Sealing

`SealedProtocol` (`eval/anchor_discipline.py:206`) freezes
`protocol_id, dataset_sha256, arms, primary_metric, thresholds, seeds,
anchor_fingerprint` and exposes `protocol_sha256` computed at construction, so a
report carries the hash of the protocol it claims to have followed and a reader
can recompute it from the appendix text.

`verify_run(...)` is called **before results are written** and raises
`SealedProtocolViolation` on any mismatch of dataset, arms, metric, seeds, or
anchor set.

**Violation-voids-the-run clause.** A run whose `verify_run` raises produces no
reportable number.  This is machine-enforced by the exception, not by author
discipline; the failure is recorded and the run is re-sealed and re-executed.

### Anchor discipline

`AnchorSet` (`anchor_discipline.py:86`) holds a small `reference` set plus a
`held_out` set behind name mangling (`self.__held_out`).  Reading a held-out
anchor raises `HeldOutAnchorReadError`.  `audit(scorer)` is the one-shot channel;
the confirmation set is burned exactly once.

### Frozen environment spec (O2) — closes the lookahead objection

`_freeze_ghost_registry` builds its `semantic_cluster` vocabulary by scanning
stream segments that, at wall-clock time, lie in the future of the case being
routed.  A reviewer will read that as lookahead.

**Declaration:** the `semantic_cluster` vocabulary is a **frozen environment
specification**, independent of case outcomes — it is a property of the memory
domain (which clusters exist), not of whether any case succeeded.  It is fixed
before the run, hashed into `dataset_sha256`, and never updated during
evaluation.  Under that declaration the scan is specification-loading, not
outcome-peeking.

This is closed **by declaration plus hashing**, which is auditable, rather than
by rebuilding the registry from a dev prefix — the latter is mechanism work and
is out of scope under Plan A.  The declaration must appear in the appendix
protocol text so a reviewer can check it against the hash.

### Ecology under seal

`GhostEcology(evaluation_only=True)` refuses every state-changing call:
`deposit_failure`, `record_niche_snapshot`, `record_niche_transition`, and
`lifecycle_transition` all raise `PermissionError` under seal, and the
constructor rejects `evaluation_only and discovery_authorized` together.

**Non-wiring decision (supersedes O3's "zero-cost wiring").** Wiring
`derive_discovery_pressure` / `record_niche_snapshot` into the sealed zero-call
runner is **not** zero-cost: both raise `PermissionError` when
`evaluation_only=True`, and the zero-call runner holds no `GhostEcology` at all.
Threading a discovery-authorized ecology into a sealed run would be ecology
expansion and would weaken the seal — both forbidden under Plan A.  The niche
ledger therefore stays observation-only in the appendix, and niche *dynamics*
are explicitly **not claimed**.

What was fixed instead is a real defect the review surfaced: niche lifecycle
transitions had no legality table, so `lifecycle_transition(subject_kind=
"niche", ...)` accepted any distinct state pair and a ledger could record
`extinct -> latent` while auditing clean.  `_NICHE_TRANSITIONS` now constrains it
(extinction terminal, no skipping emergence), with tests.

---

## §Testing

### Falsification / de-shortcutting design (2.4)

The circularity risk is that the constructor's own output *is* the telemetry, so
correlation is guaranteed by construction.  `DECOUPLING_ARMS =
("telemetry_permutation", "telemetry_placebo")`
(`experiments/ghost_ecology_zero_call.py:44`) breaks the candidate↔telemetry
pairing while leaving the audit reference bound to its own candidate.

**Kill condition (positive control).** After decoupling, identifiability must
**collapse**:

```
within_case_pairwise_concordance < 0.55
family_macro_pearson             < 0.2
```
and the true arm must be strictly separable from each control arm on both
metrics (`tests/experiments/test_ghost_ecology_decoupling.py`).  A decoupled arm
that stays identifiable proves the true arm's signal was circular — that outcome
kills the C1 positive claim and must be reported if observed.

`telemetry_placebo` is additionally required to be *fully degenerate*: constant
telemetry means no comparable pair exists, so `comparable_pair_count == 0`.  A
non-zero count there indicates the placebo is leaking.

**Known coverage limit.** These arms permute telemetry only.  A shortcut living
in *item metadata* (the `store` field) is invisible to them — which is precisely
why §Dataset withholds `ITEM_STALE` by declaration instead of relying on the
decoupling controls to catch it.

### Density boundary (E3 input, 3.5)

`experiments/poison_density_sweep.py` scores three decision rules on identical
recall sets at 0 LLM calls.  Measured result (recall_size=10):

| density | minority_vote | loo_reconstruction | anchored_contrast |
|---|---|---|---|
| 0.00 | fp=0 | fp=0 | fp=0 |
| 0.10–0.40 | 1.00 | 1.00 | 1.00 |
| 0.50 | 0.00 | 0.67 | 1.00 |
| 0.60–0.90 | 0.00 **!** | 0.00 **!** | 1.00 |

`!` = inverted (flagged more clean items than poisoned — it would repair the
healthy memory).  `first_inverted_density`: minority_vote 0.6,
loo_reconstruction 0.6, anchored_contrast `None`.

The finding is a boundary **on CMD, not only on the baseline**: CMD's own LOO
reconstruction inverts at the same density as the MCG-style minority rule,
because its reference is endogenous to the poisoned store.  Only the exogenous
anchored reference is density-invariant.  Scope caveat travels with the numbers:
divergence here is a deterministic lexical oracle, not the G-Eval judge, so this
measures the *decision rule's* density dependence with judge noise at zero.

### Acceptance

- `python -m pytest tests/ -q` fully green.  Current: **1312 passed, 1 skipped,
  58246 subtests**.
- Every component named in this plan has a test file; no claim cites an API that
  does not exist on disk.
- Phase 4.2 sampling: case_id ↔ on-disk dataset row, bidirectional.

### Experiment mapping and degrade order

| Exp | Depends on | Cuttable |
|---|---|---|
| E1 sealed confirmation | 2.2 + 3.2 (`SealedProtocol`, `AnchorSet`) | **no** |
| E2 surrogate gap | 3.3 + 3.4; per-domain, τ sealed | **no** |
| E3 density boundary | 3.5 | yes (3rd) |
| E4 ablation + free descriptor/random_niche/unkeyed_pool contrast | existing arms | yes (2nd) |
| E5 competitor comparison | — | yes (1st) |

Degrade order on budget pressure: **E5 → E4 → E3**.  E1 and E2 are not cuttable.
