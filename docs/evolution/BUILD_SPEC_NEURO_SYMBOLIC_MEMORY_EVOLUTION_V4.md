# BUILD SPEC V4 — Neuro-Symbolic Memory Evolution

- **Status:** Implemented normative specification (2026-08-09)
- **Protocol:** `cmd-neuro-symbolic-memory-evolution-v4`
- **Replaces:** successor-v3 fixed candidate synthesis and open grammar search
- **Keeps:** frozen semantic graph, typed IR, state executor, protocol validation,
  deployment guard, append-only evidence, rollback, and existing audited ecology
- **Claim boundary:** learned repair selection and governed capability deposition;
  not base-model weight editing and not unbounded autonomous self-modification

## 1. Product decision

CMD SHALL implement memory evolution as:

```text
deployment-visible trace + frozen semantic graph
    -> parameterized repair policy
    -> ranked typed RepairIntent or abstention
    -> graph-bound compiler and type checker
    -> counterfactual execution and post-repair validation
    -> append-only outcome deposition
    -> niche-local policy update and repair-chain governance
```

The typed program is the execution ABI.  It SHALL NOT be the search space of an
exhaustive grammar enumerator.  A policy or proposer emits complete intents;
the compiler accepts or rejects them without expanding combinations.

## 2. Required boundaries

### 2.1 Soft, parameterized layer

The following MAY learn from chronologically prior validated outcomes:

- candidate intent score;
- action-family utility;
- niche-local feature weights;
- hierarchical niche backoff weights;
- memory/operator Q-values;
- proposal prompts or adapters outside the stdlib runtime package.

The reference implementation SHALL provide a deterministic online linear
utility policy so evolution is executable without a model endpoint.  Learned
weights SHALL be serializable, content hashed, versioned, and replayable.

### 2.2 Hard, non-learned layer

The following SHALL remain code and protocol invariants:

- relation graph identity, evidence provenance, and runtime/gold separation;
- destructive target typing and effect semantics;
- canonical typed-program compilation and resource bounds;
- transaction, locality validation, authorization, ledger, and rollback;
- event ordering and effective-after boundaries;
- deposition, promotion, probation, retirement, and active-cap rules;
- abstention on unknown, conflicting, untrusted, non-visible, or non-finite data.

No learned score authorizes a store mutation.

## 3. Public seams and schemas

### 3.1 `RepairIntent`

An intent is a complete proposal, not a partial grammar fragment:

```text
intent_id:             content-derived SHA-256 identifier
strategy_id:           reusable proposer-defined semantic motif identifier
relation_edge_id:      frozen graph edge identifier
target_item_id:        exact item, or null for non-destructive effects
effect:                annotate_conflict | abstain | verify |
                       demote | suppress | replace
replacement_item_id:   required only for replace
proposer_id:           versioned producer identity
proposer_model_hash:   content/config hash, never an authority token
evidence_ids:          sorted non-empty tuple
```

`strategy_id` is the sedimentable species identity: it names a reusable repair
abstraction such as `prefer_trusted_later_fact_then_verify`, while edge/item IDs
bind one concrete execution.  It SHALL be non-empty, versioned, and free of
case IDs, target IDs, gold labels, and evaluation-family markers.  Species are
deduplicated by `(strategy_id, effect, compiler_version, proposer_model_hash)`;
concrete `intent_id` remains case- and graph-bound.

Identifier leakage SHALL be checked against exact frozen graph identifiers and
explicit reserved metadata markers, not against ordinary vocabulary.  Words
such as `case`, `family`, `target`, and `test` MAY appear as semantic content;
strings such as `case_id`, `target_item_id`, `family_id`, gold/evaluation
markers, or a concrete case/edge/item identifier SHALL be rejected.

The compiler SHALL map destructive intents only to
`SUPERSEDED_ITEM -> {DEMOTE,SUPPRESS,REPLACE}`.  A non-destructive divergent
intent may map only to annotate, verify, or abstain.  Invalid intents SHALL be
rejected before policy scoring.

### 3.2 `PolicyContext`

```text
case_id
event_index                         # strictly increasing selection index
graph_sha256                        # exact frozen graph
runtime_surface
domain
semantic_cluster
signal_signature
features: mapping[str, finite float]
```

Feature names SHALL reject denylisted gold, label, injector, case-marker, and
post-outcome tokens.  Feature values must be finite and deployment-visible.

### 3.3 `SelectionDecision`

```text
selection_id                        # hash of context, candidates, policy hash
case_id / event_index / graph_sha256
niche_path                          # global -> surface -> semantic -> local
selected_intent_id | null
ranked_intent_ids
scores
policy_snapshot_sha256
reason                              # selected | no_candidates | abstain |
                                    # below_margin | invalid_context
```

Selection SHALL complete before any outcome for that case can be recorded.

### 3.4 `OutcomeObservation`

```text
selection_id
case_id
observed_after_event_index           # greater than selection event index
family_id                            # evaluation-only; never a policy feature
intent_id
recovery_gain
locality_cost
changed_item_count
valid
rolled_back
```

Reference utility:

```text
utility = recovery_gain
          - locality_penalty * locality_cost
          - change_penalty * changed_item_count
```

Invalid, rolled-back, or non-finite observations SHALL never produce a positive
deposit.  The producing case SHALL never validate or promote its own deposit.

## 4. Parameterized policy

The reference `OnlineRepairPolicy` SHALL score a validated complete intent as:

```text
s(x, a, n) = prior(intent_hash)
             + action_bias(n, effect)
             + sum_f weight(n, effect, f) * x[f]
```

`n` is selected by hierarchical backoff.  Pairwise post-outcome updates SHALL
move the best valid intent above inferior intents using a deterministic margin
update.  Updates SHALL occur only after `OutcomeObservation` is accepted.
Selection with no stable local evidence SHALL back off rather than pretending
the most specific niche is calibrated.

Every update SHALL create an immutable `PolicySnapshot` containing:

- parent snapshot hash;
- effective-after event index;
- feature schema version;
- weights and priors;
- learning configuration;
- source observation hashes;
- canonical snapshot hash.

## 5. Ecological niche layering

The exact path is:

```text
global
global/surface:<runtime_surface>
global/surface:<runtime_surface>/semantic:<semantic_cluster>
global/surface:<runtime_surface>/semantic:<semantic_cluster>/signals:<signature>
```

Rules:

1. Gold/evaluation family IDs SHALL NOT form runtime niches.
2. A layer is `cold`, `probation`, `stable`, or `retired`.
3. A stable deeper layer is preferred; otherwise selection backs off to the
   nearest stable ancestor.
4. Promotion requires support from later cases in at least two evaluation
   families, positive conservative utility, and no anchor/locality regression.
5. Duplicate intent/program hashes update evidence but do not create species.
6. Active species are capped per niche; weak or repeatedly failing species are
   retired, never silently deleted.

The existing `NicheArchive` and `OperatorGovernance` remain the normative
admission mechanisms where their contracts apply.

## 6. Durable sedimentation

`EvolutionRepository` SHALL use SQLite and transactions.  It SHALL persist:

- selections;
- outcome observations;
- immutable policy snapshots;
- intent species and niche memberships;
- lifecycle transitions;
- repair-chain attempts and governance decisions.

Rows SHALL contain canonical payload JSON plus payload SHA-256.  Selection IDs,
observation IDs, and snapshot hashes SHALL be unique.  Replaying the same event
is idempotent; conflicting payload under an existing ID fails closed.  No row is
updated to rewrite history: lifecycle changes are additional rows.

## 7. Repair-chain governance

A repair chain is ordered: `A -> B` differs from `B -> A`.  Chain benefit is:

```text
chain_benefit = utility(A -> B) - max(utility(A), utility(B))
```

The chain governor SHALL maintain candidate, probation, stable, blocked, and
retired states.  A chain may be deposited only when:

- both component intents/programs are already admitted;
- B consumed A's materialized intermediate state;
- support comes from later cases and at least two families;
- conservative chain benefit is positive;
- its direction beats or is distinguishable from the reverse direction;
- changed-item and locality budgets pass;
- no typed effect conflict exists.

Negative benefit, rollback, type conflict, reverse-direction dominance, or
anchor regression records an anti-pattern/retirement event.  A stable chain is
still compiled and transactionally executed as typed IR; deposition does not
grant deployment authorization.

The existing `ChainObserver` remains the statistical evidence source.  V4 adds
a durable lifecycle governor around it rather than replacing its tests.

## 8. Runtime and CLI protocol

The CLI SHALL consume chronological JSONL with two separate record types:

```text
select   -> context + complete intents
outcome  -> a prior selection_id + observations
```

An outcome embedded in a `select` record is invalid.  Outcomes referring to an
unknown/future selection are invalid.  The command writes a closed JSON report
with counts, decisions, niche lifecycle, policy hashes, deposits, chain states,
repository hash, and an overall report hash.

The command SHALL make zero model calls.  Model/LLM proposers are adapters that
produce the complete input intents before this seam.

### 8.1 Collect-all input preparation

The live-input preparation adapter MAY run in an explicit collect-all mode.
Exhausting the closed-schema retries for one case SHALL quarantine that case
and SHALL NOT stop proposal attempts for later cases.  The attempt SHALL emit:

- a content-hashed quarantine row for every exhausted case;
- partial prepared-case and intent streams containing only compiler-accepted
  cases;
- the complete raw-response audit stream and aggregate refusal report; and
- a `repair_required` attempt manifest with exact successful/quarantined counts
  and stream hashes.

Collect-all changes batching, not authorization.  A non-empty quarantine SHALL
NOT create the normative `prepared_cases.jsonl` or preparation manifest, SHALL
NOT be accepted by the prepared-case validator, and SHALL NOT authorize either
GPU lane.  After the proposer/compiler issue is repaired, a new attempt MAY
reuse content-addressed accepted responses; quarantined cases, which were never
deposited in the accepted-response cache, SHALL be proposed again.  Only a
zero-quarantine attempt may publish `build_status=gpu_input_ready`.

The V6 proposer cache migration SHALL preserve the V5 investment without
weakening current validation.  On a V6 miss, the adapter MAY load the matching
V5 response and compile it under the complete V6 graph-bound contract.  A
passing response SHALL be deposited under the V6 cache key with zero model
calls.  A rejected legacy response SHALL emit a content-bound
`cache_rejected` audit row and trigger a normal V6 proposer attempt; it SHALL
NOT be promoted under the V6 key.  Thus a validation-policy upgrade reuses all
still-legal responses and re-queries only the incompatible cases.

Reference command:

```bash
python -m experiments.run_memory_evolution_v4 \
  --events path/to/evolution_events.jsonl \
  --repository artifacts/evolution/evolution.sqlite \
  --output artifacts/evolution/report.json
```

Reference implementation:

```text
cmd_audit/repair/parametric_policy.py
cmd_audit/repair/evolution_repository.py
cmd_audit/repair/repair_chain_governance.py
cmd_audit/repair/neuro_symbolic_evolution.py
experiments/run_memory_evolution_v4.py
```

## 9. Migration and deletion manifest

Delete these successor-v3 search artifacts because they treat typed IR as a
candidate combination/search space:

```text
experiments/run_successor_v3_synthesis.py
experiments/run_successor_v3_confirmation.py
experiments/run_open_operator_synthesis.py
tests/experiments/test_successor_v3_synthesis_confirmation.py
```

Keep `run_successor_v3_e0.py` only as a historical bounded-search baseline; it
does not authorize v4 and SHALL be labelled legacy in its module docstring.
Keep the graph, relation instrument, actionability, typed IR/executor,
protocol-freeze validator, gate audits, query ledger, and deployment guard.

No tracked predecessor evidence artifact is rewritten.  New v4 source/version
names SHALL NOT be used to reinterpret v1-v3 experimental results.

## 10. Acceptance tests

The build is complete only when public-seam tests prove:

1. a valid policy selection compiles to canonical typed IR without enumeration;
2. unknown/conflicting direction cannot produce a destructive program;
3. selection occurs before outcome and policy weights change only afterward;
4. snapshots round-trip with stable hashes and reject tampering;
5. deeper stable niches win and cold/probation niches back off;
6. successful later cross-family evidence deposits a reusable species;
7. duplicate deposit is idempotent and conflicting replay fails closed;
8. chain order, marginal benefit, family support, locality, conflict, probation,
   promotion, retirement, and anti-pattern behavior are observable;
9. SQLite reopening preserves the full ledger and active materialized view;
10. the CLI replays a select/outcome stream deterministically with zero model
    calls and emits a content-bound report;
11. removed synthesis modules are absent and no v4 import references them;
12. existing graph, executor, niche, governance, chain, and deployment tests
    continue to pass.

## 11. Scientific success criterion

V4 supports an evolution claim only if, at equal candidate-execution budget,
the learned hierarchical policy improves held-out-family recovery or uses fewer
executions for equal recovery while satisfying calibration, locality, rollback,
and null-scope constraints.  Library growth, weight change, deposits, or chain
count alone are not evidence of capability growth.

## 12. Experimental validation implementation

The registered experiment SHALL separate parallel post-outcome materialization
from chronological policy evolution:

1. GPU0 owns cases where `SHA256(case_id) mod 2 == 0`; GPU1 owns bucket 1.
2. Each lane executes complete intents against the exact frozen graph and typed
   state executor before the answerer/judge produces shadow outcomes.
3. A merge refuses duplicate/missing cases, non-unique event indexes, malformed
   schemas, or shard hash drift, then restores the frozen event order.
4. Exactly one CPU replay evaluates `identity`, `legacy_symbolic`, `random_k`,
   `global_policy`, `hierarchical_no_chain`, and `full_v4` on arm-paired frozen
   candidates.
5. Every arm selects from state `L_t` before any current-case outcome is exposed;
   only that arm's selected-action outcome may update its `L_(t+1)`.
6. The primary registered comparison is `full_v4 - global_policy` on represented
   family blocks.  Its estimate and one-sided 95% lower bound must both exceed
   zero.  The unseen-family comparison against identity must have non-negative
   point estimate and lower bound above the frozen safety margin.

Repair chains are materialized in both directions and recorded as governed
shadow evidence. Until a separately authorized deployment/execution experiment
allows stable chains to participate in online selection, chain count or shadow
benefit SHALL NOT be reported as an end-to-end capability gain.

Reference implementation:

```text
experiments/v4_live_materialization.py
experiments/v4_materialization.py
experiments/v4_prequential_runner.py
experiments/detached_run.py
experiments/jsonl_monitor.py
run_remaining_experiments.sh
RUN_EXPERIMENTS_SINGLE_A100.md
```
