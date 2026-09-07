# CMD-RepairStream Data Validation

- Validation date: 2026-08-27
- Protocol: `docs/MIX_GHOST_ECOLOGY_REPAIR_EXPERIMENT_SPEC.md` v0.3
- Scope: public semantic substrate and readiness for the CMD-RepairStream compiler
- Evidence exclusion: no `artifacts/`, cached result summaries, or prior experiment scores

## Verdict

**READY_FOR_DEVELOPMENT PILOTS, not yet F-DATA frozen.**

The downloaded public data is real and sufficiently structured to ground CMD-RepairStream. The development compiler now materializes reproducible interventions, paired lineages, sealed repair labels, typed operator outcomes, event-order schedules, and shadow outcome matrices.

This is not a replacement QA world: public episodes retain source semantics, while synthetic data is restricted to versioned process/state/poison interventions. The remaining data gap is benchmark-scale compilation, human review, and an explicitly authorized F-DATA freeze.

## Data Reality

| Dataset | Reality and integrity | RepairStream role |
|---|---|---|
| MemTraceBench | 103 JSON graphs across four system splits; checksum manifest present; sampled graph exposes `nodes`, `edges`, `operations`, and `sessions` | process-fault substrate and execution-graph provenance |
| MemFail | Five readable CSV files: 492 physical rows; facts, conditions, persona questions, multi-hop chains, and evaluator labels | semantic facts, conditional behavior, distractors, and process/state probes |
| HaluMem | Medium and Long JSONL parse; 40 episodes, 3,804 sessions, 6,934 questions; all sampled session timestamps present | long event streams, extraction/update propagation, and delayed effects |
| LoCoMo | `locomo10.json` parses; 10 multi-session conversations with session timestamps, summaries, observations, and QA | conversation-grounded semantic episodes and state evolution |
| Evo-Memory | Six payloads exist and match manifest SHA-256 | auxiliary evolution protocol, not primary repair truth |
| Evo-Bench | Validation/evaluation payloads exist and match manifest SHA-256 | harness/protocol reference, not CMD repair labels |
| STALE | Official final instances unavailable | blocked; no substitute permitted |
| MemSecBench | No official non-PDF payload located | blocked; no substitute permitted |
| MemEvoBench | Official payload retrieval unavailable | blocked; no substitute permitted |
| LongMemEval | Official payload retrieval unavailable in the current acquisition | blocked as a standalone source; MemTrace-derived material is not relabeled as official LongMemEval |

## Manifest Issues

1. `data/external/group_a/download_manifest.json` records MemTraceBench, MemFail, and HaluMem but omits the downloaded LoCoMo payload.
2. Group B uses `DATASET_INVENTORY.json` plus per-dataset manifests; the `spec_v03` source auditor now reads and verifies that structure directly.
3. Evo-Memory and Evo-Bench remain auxiliary/protocol inputs rather than executable repair truth, even when their checksums pass.
4. Blocked datasets must remain explicit and absent from executable denominators.

## Semantic Adequacy

The public sources can supply immutable clean episodes, entities, facts, dialogue turns, timestamps, questions, evidence relations, memory operations, and graph structure. Synthetic content is allowed only in the intervention layer:

- process interventions mutate event/pipeline operations while retaining source semantics;
- state interventions append or project explicit superseding evidence with lineage;
- poison interventions add typed untrusted events, authority metadata, and triggers while preserving benign source events;
- questions and source answers remain evaluator material from the public dataset and are not regenerated to fit an intervention.

## Implemented Compiler Products

The development compiler now provides:

1. canonical `PublicEpisode` records with source hashes and sealed public queries;
2. versioned and seeded `InterventionSpec` constructors for process/state/poison/clean;
3. `MemoryState` with immutable source log, audit log, active projection, index, scope, cache, supersession, and quarantine;
4. paired `RepairCase` lineage with runtime-only `DecisionView` and sealed `EvaluatorOnly` fields;
5. typed runtime operators whose execution reads only `MemoryState` and `OperatorSpec`;
6. copy-on-write execution, rollback outcomes, and complete `ShadowOutcomeMatrix` records;
7. distinct candidate-set, frozen-library, and evaluator-mechanism oracle universes;
8. stationary, abrupt, recurring, CAS-interleaving, and delayed-receipt order manifests;
9. the seven-way source-episode and constructor-blocked split including `D_lifecycle`.

## Runtime And Experiment Wiring

The compiler products are now connected to an executable, gold-free serving path:

```text
DecisionView + MemoryState
  -> structural MemAudit / mutually exclusive ECC syndrome
  -> typed legal candidate mask
  -> frozen operator-skill library
  -> route-only Mix GHOST selection
  -> selected-skill-only executor dispatch
  -> copy-on-write shadow execution
  -> root / invariant / safety / locality gates
  -> compare-and-swap commit or rollback
  -> pending outcome
  -> later EccRepairReceipt settlement
  -> FailureMemory, router posterior, skill evidence, lineage, and quarantine
```

The runtime does not import `InterventionSpec`, `RepairCase`, evaluator labels, or `ShadowOutcomeMatrix`. A selected action cannot train itself at selection time: only an externally matured observation can create a receipt, and settlement occurs before a later event is routed. Clean and structurally ambiguous states abstain.

The repository also contains:

- an F-DATA compiler with source/template quotas, family and constructor blocking, 3-5 seeded event orders, physical runtime/lockbox separation, checksums, compiler-closure hashes, and explicit freeze authorization;
- executable Stage 5-9 runners covering all frozen matrix arms: shared-backbone router isolation, typed-skill ecology transitions, repair governance, cross-model residual/content transfer, and controlled competitor tracks;
- a closed runtime-bundle codec and batch loader that reconstructs the complete serving `MemoryState`, verifies its root and `DecisionView` bindings, and rejects evaluator-side fields;
- a unified `stage59_runner` plus `experiments/spec_v03_stage5_9.py` entry point that writes one content-addressed report and preserves missing model, feedback, oracle, discovery, or baseline capabilities as `UNSUPPORTED` denominator rows;
- separate reporting strata for Qwen3, Llama 3.1, and GPT-4o so incompatible model scales or closed/open implementations cannot be averaged into one headline number.

MemSkill, ERSkill, and Mem0 now have a closed pinned-subprocess adapter contract. Exact upstream checkouts and frozen competitor artifacts are not configured, and no model/API or competitor-comparison result is claimed here. In the controlled track, a configured adapter's legal proposal is replayed through the same CMD COW/ECC/CAS governance. MemSkill and ERSkill use frozen, split-audited evidence exports; native scoring is outside the current repair-action contract.

## Independent Pilot Validation

- Test suite: 185 passed across `tests/spec_v03` and `tests/repair/test_ghost_ecology.py`.
- Stage 5-9 file-chain smoke: one public HaluMem episode -> 3 runtime cases -> all six stage namespaces; status `DEVELOPMENT_WIRING_NO_MODEL_RESULTS`, report SHA-256 `4edde65c796ba6e38991e3b545e6a63573bd934689183fb56f6f47fd82adff86`.
- HaluMem: 20 public episodes -> 58 repair cases; split counts `9/9/9/9/9/9/4`; incidents `20 clean`, `20 process_fault`, `9 state_drift`, `9 poison`.
- Oracle universes: all 38 corrupted HaluMem cases have cardinalities `candidate=1`, `library=2`, `mechanism=3`; clean cases are `1/1/2` by design.
- MemFail smoke: 5 public episodes -> 7 cases; persona nested questions are flattened with source answer/evidence retained only in the sealed namespace.
- Runtime leak scan: no `template_id`, `target_event_id`, `synthetic_intervention`, `expected_effect`, or intervention-template value appears in generated runtime cases.

## Leakage Rules

- Public answers, evidence IDs, intervention metadata, root truth, and operator oracle never enter runtime views.
- Every corruption descendant stays in the source episode/family block of its clean parent.
- Constructor template and attack-trigger families are blocked across splits.
- Event order is generated from a frozen seed and never sorted using downstream outcomes.
- Operator legality is derived inside the evaluator namespace from intervention semantics, not copied into the router candidate builder.

## Required Next Step

Scale and freeze:

```text
audited public sources and development runtime
  -> benchmark-scale compiler run and sampled human review
  -> family/constructor leakage audit
  -> resolve exact model snapshots and baseline commits
  -> explicitly authorize F-DATA and lockbox publication
  -> execute frozen Mix GHOST/ecology/repair experiments
  -> execute controlled-stack baselines and report native-task results only as external context
```

Before F-DATA freeze, add benchmark-scale distribution targets and confirmatory source-family holdouts. The compiler must continue to fail closed when a source adapter cannot preserve semantic evidence or when an intervention lacks deterministic root, invariant, safety, locality, and rollback oracles. `DEVELOPMENT_UNPINNED` experiment manifests and development bundles must never be presented as confirmatory results.
