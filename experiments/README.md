# Experiment Runners

This package contains the runnable evidence for `EXPERIMENT.md`. The active paper path is claim-centric, not numbered-section-centric.

## Frontline runners

## No-Mem0 offline suite

`run_no_mem0_suite` is the closed allowlist for experiments that can run with
local inputs and no network, API, extractor, embedding endpoint, or Mem0
backend. It distinguishes fixture wiring from headline evidence and records
commands, input roots, logs, and a closed manifest. It never discovers or runs
legacy scripts implicitly.

```bash
python -m experiments.run_no_mem0_suite --profile plumbing-smoke \
  --output-root artifacts/experiments/no_mem0_smoke --limit 1 --fail-fast
```

Profiles: `plumbing-smoke` runs P3A → P3B → P3C (consuming the newly emitted
P3A retrieval root) → P3D's local fake lifecycle; `offline-memory` runs only
in-memory retrieval protocol; `zero-call-governance` runs the typed-v2
identifiability audit. `--plan-only` performs no write or execution.

## P4A no-Mem0 retrieval baselines

`experiments.baselines.retrieval_confirmation` is a vanilla-arm-only,
provider-neutral retrieval comparison over the P3 `MemoryRecord` ABI. It uses
the same visible ingest content, stable chronology, per-case namespace and
`top_k`; gold fields are opened only by an offline scorer after search. The
strategies are deterministic lexical, stdlib Okapi BM25, and the optional local
`all-MiniLM-L6-v2` adapter. MiniLM never downloads a model: a missing local
dependency/checkpoint produces an explicit `unavailable` manifest. The oracle
ceiling is scorer-only (`offline_upper_bound=true`) and writes no prediction
context, so it cannot feed P3C/repair/router code.

```bash
python -m experiments.run_no_mem0_suite --profile baseline-confirmation \
  --output-root artifacts/experiments/p4a_baseline_confirmation --limit 5 --fail-fast
```

The profile runs LongMemEval-S and all 692 MemFail prompts for lexical/BM25;
it runs MiniLM only on the requested small LongMemEval-S sample. Full
LongMemEval-M lexical/BM25 receipts are recorded separately under
`artifacts/experiments/p4a_baseline_confirmation/`: they use append-only,
gold-free ranking checkpoints plus a root-bound receipt so interrupted runs can
resume exactly once. P4A measures retrieval only, never answerer or judge quality.

## P4B typed evidence / frozen-BM25 selection

`build_p4b_typed_evidence` binds P4A BM25 rankings to a closed visible-feature
ledger; `run_p4b_cmd_bm25` consumes that same frozen candidate root for BM25,
static, CMD and GHOST. A cache is not typed outcome evidence: without selected
action telemetry and the decoupling audit, its machine gate remains blocked and
CMD/GHOST abstain. Current P4A MemFail has no root-bound candidate cache, so its
P4B receipt is explicitly unavailable rather than reconstructed.

## Retired P4C zero-call program (historical)

The P4C runner family, fixtures, tests and `run_remaining_experiment.sh` were
removed on 2026-08-24. Its source projection and structural ECC outcomes did
not measure answer quality and cannot stand in for LoCoMo/LongMemEval model
evaluation. The remainder of this section is retained only to explain old
artifact schemas; none of its commands are active entrypoints.

`experiments.p4c_ecc_runner` is the isolated P4C execution layer. It does not
extend the legacy V4 prequential runner: P4B remains a negative typed-evidence
boundary and the old V4 runner remains an explicit baseline. P4C consumes an
immutable, closed incident-overlay JSONL through `load_p4c_cases`; raw datasets
and sealed audit labels are not members of that runtime ABI.

The per-case chronology is:

```text
runtime observation -> EccSyndrome -> P4cGhostRouter selection
  -> apply_shadow -> evaluate_ecc -> commit/rollback
  -> EccRepairReceipt -> incident sink + observe_receipt
  -> hash-chained case completion
```

`P4cGhostRouter` binds prepared `GhostEcology` failures, pattern
responsibilities, skills, and registries to the runner. Alternative routers
must expose the same `select()` and receipt-only `observe_receipt()` seam.
Stores expose only `snapshot_root/apply_shadow/commit_shadow/rollback_shadow`;
evaluators expose only `evaluate_ecc`. There is no answerer, same-trace replay,
task reward, `TypedFollowup`, or gold/label parameter in the runtime
constructor.

Runtime artifacts are `incidents.jsonl`, `repair_receipts.jsonl`, the
manifest-bound hash-chained `case_completions.jsonl`, and `manifest.json`.
`run_mode="resume"` verifies the case-stream root, receipt hashes, incident
head, completion chain, and—when more cases remain—the restored router root
before skipping a completed prefix.

Only after runtime completion may `audit_p4c_run` open a sealed sidecar. The
sidecar must bind the run manifest and every receipt hash; it computes offline
accuracy, false-repair rate, incident recall, and incident-type accuracy while
refusing to write inside the runtime directory. These audit metrics never feed
back into GHOST.

### P4C-0 deterministic mechanism screen

`experiments.p4c_zero_call` supplies the first execution substrate for that
ABI. `StructuralMemoryStore` implements copy-on-write state for pipeline
health, memory activation, supersession lineage, quarantine, and protected
memory IDs. Its three closed operator kinds are `pipeline_patch`,
`supersede_lineage`, and `quarantine_poison`. `StructuralEccEvaluator` checks
root binding, syndrome resolution, structural invariants, protected-memory
safety, and mutation locality without exposing an answer or replay method.

`P4cZeroCallSuite` runs frozen `P4cZeroCallScenario` records through the normal
`P4cEccRunner` and writes `zero_call_report.json`. The report is bound to the
runtime manifest and receipt root and records commit, rollback, resolution,
invariant, safety, locality, and recurrence rates with
`model_call_count=0`. A real `P4cGhostRouter` can be supplied directly; router
learning still receives only `EccRepairReceipt`.

P4C-0 starts after an incident observation has been emitted, so its claim is
limited to the post-detection ECC mechanism loop. A no-fault detector control
belongs to the upstream MemAudit signal-generation experiment and is not
encoded as a fourth incident type. The deterministic suite is fresh-run only;
durable resume remains available at the lower-level P4C runner where the
caller also owns restoration of its real memory substrate.

The frozen three-mechanism formal screen is executable with the real GHOST
router and receipt feedback:

```bash
python -B -m experiments.run_p4c_zero_call_sweep \
  --overlay experiments/fixtures/p4c_zero_call_v1.jsonl \
  --output-dir artifacts/experiments/p4c_zero_call_v1
```

The top-level `sweep_manifest.json` binds the overlay, frozen registry, final
router snapshot, ecology ledger head, and runtime report. Runtime state and
receipts remain under `runtime/`; `ecology.jsonl` contains the real selection,
ECC receipt feedback, and posterior-snapshot transitions.

Mixed-GHOST prior calibration remains zero-call. It expands the three frozen
mechanisms into two same-family candidates and gives each candidate three
predeclared receipt opportunities, satisfying conservative global, pattern,
and local support gates without reading outcomes before selection:

```bash
python -B -m experiments.run_p4c_zero_call_sweep \
  --mode prior-calibration \
  --overlay experiments/fixtures/p4c_zero_call_v1.jsonl \
  --output-dir artifacts/experiments/p4c_zero_call_prior_calibration_v1
```

The calibration includes a protected-memory mutation control; its rollback
receipts supply negative evidence. This artifact qualifies router support and
is not a repair-effect headline comparison.

### P4C-1 real-source structural wiring

`experiments.run_p4c1_real_sources` projects deployment-visible structure from
LongMemEval, MemFail, and the coordinated poison sweep into a sealed
`source_projection.jsonl`. It then writes a separate
`incident_overlay.jsonl`: LongMemEval exercises state-drift supersession,
MemFail exercises retrieval process faults, and the poison construction
exercises CAS/influence quarantine. Runtime memory records contain source-bound
content hashes, never source answers or benchmark annotations.

```bash
python -B -m experiments.run_p4c1_real_sources \
  --longmemeval data/external/longmemeval/input/longmemeval_s_cleaned.json \
  --memfail-root data/external/memfail/datasets \
  --limit-per-source 5 --poison-recall-size 10 --poison-count 3 \
  --output-dir artifacts/experiments/p4c1_real_sources_v1
```

The P4C-1 manifest binds all source roots, the visible projection, incident
overlay, runtime receipts, ecology head, and final router snapshot. Its claim
scope is structural live-ABI wiring only, not task-answer accuracy.

## MemAudit/ECC memory benchmark execution

The headline runtime path is receipt-bound and has two isolated stages.  First,
an instrumented memory harness exports deployment-visible MemAudit telemetry,
GHOST bindings, structural shadow states, and a frozen three-layer ecology
ledger.  Runtime selection and updates consume only `EccRepairReceipt`; they do
not call an answer model or open benchmark references:

```bash
python -m experiments.run_ecc_memory_runtime \
  --cases /path/to/harness/memaudit_cases.jsonl \
  --bindings /path/to/harness/ghost_bindings.jsonl \
  --states /path/to/harness/shadow_states.jsonl \
  --ecology-ledger /path/to/harness/frozen_ecology.jsonl \
  --output artifacts/runtime/longmemeval_ecc
```

Only after the runtime report and committed states exist may the answer model
consume the committed memory view and seal official-shape
`{question_id,hypothesis}` predictions:

```bash
export LLM_BASE_URL=http://127.0.0.1:8000/v1
export LLM_MODEL=your-answer-model
export LLM_TOKENIZER_PATH=/path/to/the/served/model
export LLM_MAX_MODEL_LEN=32768
export LLM_MAX_TOKENS=512
python -m experiments.run_ecc_sealed_memory_benchmark \
  --benchmark longmemeval \
  --runtime-dir artifacts/runtime/longmemeval_ecc \
  --output artifacts/experiments/longmemeval_ecc_sealed
```

LoCoMo uses the same receipt-bound prediction stage:

```bash
python -m experiments.run_ecc_sealed_memory_benchmark \
  --benchmark locomo \
  --runtime-dir artifacts/runtime/locomo_ecc \
  --output artifacts/experiments/locomo_ecc_sealed
```

`experiments.run_sealed_memory_benchmark` is retained only as an explicit
legacy static-action baseline.  It enumerates `seed:*` answer-replay operators
and is not MemAudit, GHOST, Failure Memory, or the live ECC runtime.

Official scoring is deliberately a second command. Point it at a checkout of
the benchmark authors' repository; `--execute` is required before a judge is
called or official LoCoMo references are opened:

```bash
python -m experiments.run_official_memory_scoring \
  --benchmark longmemeval \
  --run-dir artifacts/experiments/longmemeval_ecc_sealed \
  --official-root /path/to/LongMemEval \
  --oracle data/external/longmemeval/oracle/longmemeval_oracle.json \
  --judge-model gpt-4o --execute

python -m experiments.run_official_memory_scoring \
  --benchmark locomo \
  --run-dir artifacts/experiments/locomo_ecc_sealed \
  --official-root /path/to/locomo \
  --dataset data/ghost_live_v2/raw_sources/locomo10.json --execute
```

P3A emits per-arm retrieval snapshots before its scorer-only oracle sidecar.
P3C consumes those snapshots and enforces `retrieve frozen -> predict-only ->
prediction seal -> score-only`.  It is offline by default:

```bash
python -m experiments.run_longmemeval_e2e --mode all \
  --retrieval-run artifacts/experiments/longmemeval_m0_r1_s5_live_ready_v1 \
  --data data/external/longmemeval/input/longmemeval_s_cleaned.json \
  --answerer-backend fake --judge-backend fake --limit 5 \
  --output artifacts/experiments/longmemeval_e2e_smoke
```

The fake result is only wiring smoke.  Live OpenAI-compatible execution is
explicitly opt-in via `--answerer-backend openai-compatible --llm-config ...`;
official judge scoring is exported as an interface, not claimed locally.

The plural `run_remaining_experiments.sh` remains the legacy Route A/V4
controller and is not part of this sealed memory-benchmark protocol.

## P3D Evo-Bench harness governance

This local module is governance-only and **does not produce an official
Evo-Bench score**. Formal evaluation must use the upstream CLI through
`experiments.run_evobench_official`. Its generated commands preserve the
canonical 160-validation/448-evaluation split, 20-iteration/1,000-step/48-hour
budget, General-domain three trials and post-freeze evaluation boundary.

```bash
python -m experiments.run_evobench_official \
  --stage evolve --official-root /path/to/Evo-Bench \
  --policy-config /path/to/policy.json \
  --judge-config /path/to/judge.json \
  --evolver-config /path/to/evolver.json
```

The command above is plan-only. Formal execution additionally requires
`EVOBENCH_EXECUTION_MODE=e2b` and `--execute`.

`experiments.run_evobench_harness` is a provider-neutral, offline governance
runner for the separate harness-evolution track. It parses the public 160-task
validation suite only; it never opens, generates, or executes a sealed
evaluation task. External evolvers and executors provide closed JSON receipts.
The runner records seed → prepare → validation → commit/rollback → freeze →
opaque sealed-evaluation export → external sealed-result ingest in an
append-only hash chain, with V4 outcome/checkpoint recovery.

```bash
python -m experiments.run_evobench_harness --help
```

Validation gain/cost/regression, failure/rollback, and resume parity are a
separate Track B report. A native evaluation score is reportable only after an
externally produced, root-bound sealed result is ingested. It must never be
pooled with LongMemEval, Mem0, or any memory-repair metric.

| Claim | Runner | Use |
|---|---|---|
| C3-C4 | `python -m experiments.run_experiment_14_repair_efficacy` | Four-arm repair efficacy: no-repair / random / LLM judge / CMD. |
| C2-C5 | `python -m experiments.probe_exhaustive` | Full single-point oracle; produces the prior bank for transfer. |
| C5 | `python -m experiments.run_experiment_15_prior_transfer` | Leave-one-out prior transfer from offline culprits to online top-K seeds. |
| C6 | `python -m experiments.run_experiment_16_coupled_exhaustive` | Existence decider for coupled `b^d` residuals: does a two-point repair ever beat the best single point? (Verdict C6: 1/30 — no.) |
| C8 | `python -m experiments.run_experiment_17_ecs_structure_ablation` | ECS structure ablation with fixed repair content. |
| C7 | `python -m experiments.run_experiment_18_failure_memory_trajectory` | Online FailureMemory trajectory: recovered priors reduce cost-to-recovery. |
| C7/P5 | `python -m experiments.run_experiment_19_skill_abstraction` | Two-tier FailureMemory skill abstraction: recovered cases -> validated patterns -> pattern seeds. |
| Operator evolution | `python -m experiments.run_experiment_21_operator_headroom` | Composite/parameterized operator headroom over single-point residuals. |
| Operator transfer | `python -m experiments.run_experiment_22_operator_transfer` | Leave-one-out operator-skill transfer with fingerprint retrieval and same-budget random control. |
| Skill evolution A | `python -m experiments.run_experiment_24a_offline_evolution` | Family-split offline closed loop with shared verified experience tape and two read-only probe sets. |
| Skill evolution B | `python -m experiments.run_experiment_24b_prequential_evolution` | Gate-controlled evaluate-before-update simulation with stream-order permutation null. |
| Arena A+B+C | `python -m experiments.run_arena_memtrace` | One-path MemTrace-B stream with gold-free, ecology, and chain observers. |
| Arena A+B replication | `python -m experiments.run_arena_memfail` | MemFail cross-environment signal/ecology replication. |
| Arena B adjacent niches | `python -m experiments.run_arena_stale` | STALE stale-vs-conflict niche observations. |
| Arena real LongMemEval | `python -m experiments.run_arena_longmemeval` | Full-history BM25 control plus bounded CMD retrieval repair, real answer model, matched best-of-N, and context-stuffing controls. |

Recommended sequence:

```bash
python -m experiments.run_experiment_14_repair_efficacy --cmd-attribution exhaustive --limit 0
python -m experiments.probe_exhaustive --limit 0 --aggregate --min-credit 0.05 --out artifacts/sandbox/exhaustive_detail_mincredit05.csv
python -m experiments.run_experiment_15_prior_transfer --prior-bank artifacts/sandbox/exhaustive_detail_mincredit05.csv --mode both
python -m experiments.run_experiment_16_coupled_exhaustive --limit 3
python -m experiments.run_experiment_16_coupled_exhaustive
python -m experiments.run_experiment_18_failure_memory_trajectory
python -m experiments.run_experiment_19_skill_abstraction
python -m experiments.run_experiment_17_ecs_structure_ablation
python -m experiments.run_experiment_21_operator_headroom --ecs-detail artifacts/ecs_structure_ablation_detail.csv --out artifacts/sandbox/operator_headroom_detail.csv
python -m experiments.run_experiment_22_operator_transfer --operator-bank artifacts/sandbox/operator_headroom_detail.csv --out artifacts/sandbox/operator_transfer_detail_run1.csv
python -m experiments.run_experiment_22_operator_transfer --operator-bank artifacts/sandbox/operator_headroom_detail.csv --out artifacts/sandbox/operator_transfer_detail_run2.csv --random-seed 23
python -m experiments.run_experiment_22_operator_transfer --operator-bank artifacts/sandbox/operator_headroom_detail.csv --out artifacts/sandbox/operator_transfer_detail_run3.csv --random-seed 24
python -m experiments.analyze_operator_transfer --csv artifacts/sandbox/operator_transfer_detail_run1.csv artifacts/sandbox/operator_transfer_detail_run2.csv artifacts/sandbox/operator_transfer_detail_run3.csv
```

## Observational arenas

Validate all three immutable streams without model calls:

```bash
python -m experiments.run_arena_memtrace --validate-only
python -m experiments.run_arena_memfail --validate-only
python -m experiments.run_arena_stale --validate-only
python -m experiments.run_arena_longmemeval --limit 50 --validate-only
```

Live execution uses the concrete vLLM/OpenAI-compatible dual-score backend:

```bash
export LLM_BASE_URL=http://localhost:8001/v1
export LLM_MODEL=llama-3.1-8b-instruct
export LLM_JUDGE_BASE_URL=http://localhost:8000/v1
export LLM_JUDGE_MODEL=qwen2.5-7b-instruct
python -m experiments.run_arena_memtrace --case-workers 32
```

The LongMemEval entrypoint runs this same live backend on the cleaned public
haystack. It indexes every session with BM25, uses top-5 as the unrepaired
control, and gives CMD only the bounded top-10 prefix as its repair candidate
pool. The answer and `answer_session_ids` fields do not participate in
retrieval, routing, context construction, or reference-free selection. They
are available only to the isolated shadow evaluator. Best-of-N and
context-stuffing controls are enabled by default for this entrypoint:

```bash
python -m experiments.run_arena_longmemeval \
  --limit 50 \
  --retrieval-top-k 5 \
  --candidate-pool-k 10 \
  --case-workers 8 \
  --output artifacts/arena/longmemeval_live_50.jsonl
```

Do not start with all 500 cases: each Fix case evaluates the legal CMD
operators and the matched control candidates. Use a 5-case endpoint smoke,
then 50 cases, inspect failure/abstention and budget-alignment coverage, and
only then launch the full stream. Under the default top-5/top-10 protocol, the
current 500-case corpus produces exactly two cache-distinct non-baseline CMD
contexts per case. With both controls enabled, the nominal budget is therefore
6 answer generations, 5 reference-free selection scores, and 5 frozen shadow
scores per case (about 80 / 800 / 8,000 endpoint calls for 5 / 50 / 500 cases,
before transport retries).

`experiments.arena_backends:create_vllm_backend` is the default factory. Its
runtime selection signal is scored by the answerer endpoint; the frozen judge
endpoint is reserved for shadow evaluation. Production validation rejects a
shared selection/evaluation client or the same configured model identity,
because that would optimize and evaluate the same judge. Each generated answer
is scored for grounding,
relevance, completeness, and internal consistency using only query + candidate
context + answer. Arena v2 requests score-token logprobs and uses the rubric
expectation on a continuous `[0, 1]` scale; endpoints that strip logprobs fall
back to the discrete JSON score. Every legal operator in the four physical
action families plus the two gated item-level families is evaluated. Finite
positive gains are retained in descending order until their additive sum
reaches `--saturation-threshold` (default `0.8`); zero, negative, and non-finite
gains are never retained. `--candidate-limit` exists only as an explicit
diagnostic cap and defaults to no cap.

The zero-call hook is evaluated while cases are loaded. `Fill` cases are
serialized as explicit routed abstentions and excluded from CMD selection-rate
denominators; they do not silently become failed no-repair cases. Measure the
split before a GPU run with:

```bash
python -m experiments.run_arena_memtrace --limit 50 --validate-only
```

Validation prints the exact source-file SHA-256 plus hashes of the ordered
selected case IDs and the full derived `ArenaCase` stream. The same values,
source byte size, resolved source path, and fingerprint schema version are
stored in `arena_manifest`. Analysis rejects artifacts without this provenance,
rejects an artifact whose serialized case IDs do not match its manifest, and,
when the source path is still mounted, re-hashes the current source bytes.

`--case-workers` (or `CMD_CASE_WORKERS`) enables cross-case concurrency for
stateless runs while preserving input order in artifacts. Values above one are
rejected with `--deposit-after` or `--perturb-after`, since those interventions
change the candidate set seen by later cases.

`--best-of-n-control` adds the compute-aligned structural control. For each Fix
case it sets `N` to CMD's actual non-baseline answer attempts after successful
cache reuse, as observed by backend counters, and gives the generic arm the
same `N` answer calls and `N` reference-free selection calls, and exposes the
**information superset** `origin_context + all candidate_items` without hook
routing, physical action operators, or item-gate labels. CMD sees an
action-specific repaired context instead. Thus the control is not
information-starved: it has at least the raw information available to any CMD
candidate and matched selection compute. A shared cached baseline answer and
baseline score are excluded from both budgets: `N` counts only actual
non-baseline CMD candidate answer attempts, so the control receives exactly
`N` candidate answer attempts and `N` selection-score attempts. The frozen
evaluation judge scores only after selection.
`arena_arm_comparison_event` records both budgets and
`experiments.analyze_arena_results` writes `cmd_vs_best_of_n.csv` plus
`cmd_vs_best_of_n_by_budget.csv`. The headline paired shadow-gain delta includes
only finite, budget-aligned pairs; the table also reports `n_paired`,
control failures, CMD/control abstentions, budget mismatches, and the `N`
distribution. `N=1` is marked as a non-selection stratum and must not be pooled
silently with `N>=2`. Additive saturation remains a separate ecology diagnostic
and is not used as the arm outcome.

The live CLI prints `arm_comparison_coverage_rate`,
`fix_cases_without_arm_comparison`, `budget_aligned_rate`,
`budget_aligned_pairs`, and `cmd_budget_source_distribution`. The 50-case
endpoint smoke is accepted only when the source distribution is
`backend_call_counters` and the alignment rate is reported alongside pair
coverage; fixture-only `logical_fallback` results cannot satisfy this
preflight.

The two arms intentionally pass different contexts to the reference-free
selection scorer (clean repaired context versus the control's larger
information superset), so grounding scores are not directly comparable across
arms. They are used only for within-arm choice. Cross-arm claims use the frozen
shadow evaluator, which neutralizes that selection-rubric context asymmetry.

Interpretation is frozen before live results: if CMD has a positive paired
shadow-gain delta over best-of-N, the supported claim is “directed structure
helps under matched selection compute.” A statistical tie or practically
negligible delta means “directed repair is approximately equal to undirected
search” and does **not** support a structural-superiority claim. A negative
delta means the structure hurts. No interpretation may omit pair coverage,
failure/abstention counts, budget strata, or the `N=1` share.

The shadow scorer then compares the same answers with `gold_answer`; shadow
values are materialized after runtime values and never enter candidate
selection. The additive sum is an independent-gain coverage diagnostic, not a
claim that the selected operators have already been composed. Joint effects
still require the chain path and held-out validation.

An alternative `--backend-factory module:factory` may be supplied. The factory
receives `cases=<tuple[ArenaCase, ...]>` and `args=<Namespace>` and must return
a `DualScoreArenaBackend` with:

- `runtime_uses_gold = False`;
- a named gold-free runtime signal;
- a separately named shadow-gold signal;
- `candidates(case)` and `evaluate(case, candidate, input_context,
  origin_context)` methods;
- `deposit_composite(event)` when `--deposit-after` is enabled.

The runner still refuses the existing `LiveEvolutionBackend` as-is because
that backend's net-gain calculation reads `gold_answer`. Re-labeling that score
as gold-free would invalidate experiment A.

Run the separate perturbation observation after the baseline arena:

```bash
python -m experiments.run_arena_memtrace \
  --output artifacts/arena/memtrace_keystone_removal.jsonl \
  --perturb-after 0.25 \
  --perturb-strategy keystone
```

The selected keystone skill is removed only after the trigger case. Recovery
uses the leading retained contributor as the perturbation stream and requires
two adjacent non-empty windows below the configured JSD threshold; windows
without a positive contributor are recorded as collapse, not recovery.

`--deposit-after 0.5` is deliberately one-shot. It materializes one supported
chain and calls `deposit_composite(event)` so the staged composite can enter
subsequent retrieval. Periodic deposition would be a different intervention
and is left for future work. A composite is classified by its terminal repair
family, making replacement versus complementarity with the terminal skill
directly observable.

After the arena files exist:

```bash
python -m experiments.analyze_arena_results \
  --inputs \
    artifacts/arena/memtrace_observations.jsonl \
    artifacts/arena/memfail_observations.jsonl \
    artifacts/arena/stale_observations.jsonl
```

This produces descriptive signal, saturation, per-skill contribution, niche,
succession, co-activation, chain spectrum, directionality, and cross-arena
reproducibility tables. It performs no hypothesis tests and does not turn
structural smoke scores into recovery claims.

## Legacy runners

## P3A LongMemEval M0/R1 execution

The production diagnostic runner streams a top-level S/M JSON array (it never
`json.loads` the full M file), stably sorts each instance's paired sessions by
`(date, original_index)`, and uses one isolated namespace per `(arm, question)`.
`answer`, `answer_session_ids`, and oracle content are evaluation-only: the
oracle is opened only for the post-retrieval sidecar scorer. `cmd` and `ghost`
are currently **shadow/observe-only**, not repair-efficacy arms. In-memory is
the default and makes no SDK, network, model, or LLM call.

```bash
python -m experiments.run_longmemeval_m0_r1 \
  --data data/external/longmemeval/input/longmemeval_s_cleaned.json \
  --oracle data/external/longmemeval/oracle/longmemeval_oracle.json \
  --backend in-memory --arms vanilla,static,cmd,ghost --limit 5 \
  --run-mode fresh --output artifacts/experiments/longmemeval_m0_r1_s5
```

Use `--run-mode resume` only with the identical data/oracle roots, arm list,
backend, top-k and frozen case stream. Results are append-only in
`outcomes.jsonl` and bind the existing P0 checkpoint journal. Real Mem0 is
explicit opt-in (`--backend mem0 --mem0-config CONFIG.json`); it requires a
locally installed/configured pinned SDK and never falls back to a model or
network default.

Older runners remain in the package for diagnostics and appendix evidence. They should not be cited as headline classification experiments unless `EXPERIMENT.md` explicitly promotes them back into the claim chain.

## B-scheme staged entrypoints

## P3B MemFail M0/R1 process-fault execution

`run_memfail_m0_r1` consumes the five official local MemFail CSVs directly,
validates the 492 physical-row corpus, and expands Persona rows into 692 scored
prompts. It uses only deployment-visible query/content/provenance during
add/search. Ground truth, choices, misleading status, family, and subtype are
opened only after all arms complete for offline scoring. `--limit` is **per
family physical-row** smoke limiting, never a headline sample.

```bash
python -m experiments.run_memfail_m0_r1 \
  --data-root data/external/memfail/datasets \
  --backend in-memory --arms vanilla,static,cmd,ghost --limit 1 --top-k 5 \
  --run-mode fresh --output artifacts/experiments/memfail_m0_r1_smoke
```

Arms use independent opaque case scopes and equal retrieval budgets. `ghost`
is shadow-only. The current runner reports retrieval-side probes, not generated
answers: Persona unsafe-answer metrics are explicitly unavailable, while
coexisting facts remain conflict controls rather than forced process faults.
Only post-retrieval scorer-confirmed misses append a hash-chained P1 incident.
Use `--backend mem0 --mem0-config CONFIG.json` only for an installed, locally
configured Mem0 SDK; no implicit network/model/API fallback exists. `fresh`
refuses non-empty output; `resume` requires an identical manifest and case root.

The complete B-plan experiment surface is:

| Experiment | Entrypoint | Required input |
|---|---|---|
| E1 | `experiments.e1_sealed_confirmation` (`seal`, `verify`, `audit`) | closed anchor JSONL, frozen dataset, externally produced held-out scores |
| E2 | `experiments.e2_typed_identifiability` | lineage-merged typed V4 cases and merge manifest |
| E3 | `experiments.poison_density_sweep` | frozen sweep parameters (zero calls) |
| E4 | `experiments.v4_prequential_runner` | typed V4 cases, frozen evaluator/protocol and merge manifest |
| E4b | `experiments.e4b_descriptor_policy` | the same typed cases, budget and merge manifest |
| E5 | `experiments.e5_competitor_matrix` | closed curator JSONL with source IDs for every system |

The shell exposes `b_e1_seal`, `b_e1_verify`, `b_e1_audit`, `b_e2`, `b_e3`,
`b_e4`, `b_e4b`, and `b_e5`. E1 seal/verify must happen before result access;
E1 audit is one-shot and happens only after external held-out scores exist.

The shell orchestrator exposes a fail-closed, user-triggered stage sequence for
the governance/lineage follow-up path:

```text
v4_single_gpu (or v4_gpu0 + v4_gpu1)
  -> b_materialization_merge
  -> b_preflight
  -> b_e1_seal -> b_e1_verify
  -> v4_lineage_plan
  -> v4_followup_capture
  -> v4_lineage_project
  -> v4_lineage_merge
  -> b_e2 / b_e3 / b_e4 / b_e4b
  -> b_e1_audit / b_e5
```

`b_materialization_merge` verifies and merges the live materialization shards
into the canonical run-local cases file and manifest, but deliberately does not
start the old pre-lineage replay. This keeps the source replay provenance intact
while follow-up capture and lineage enrichment are still pending.

Use `./run_remaining_experiments.sh --help` to inspect the roles. The shell
declares one capture contract for all stages:

```bash
export CMD_B_CAPTURE_BACKEND=your_capture_module:capture
export CMD_B_SOURCE_CASES=artifacts/neuro_symbolic_evolution_v4/runs/<run>/cases.merged.jsonl
export CMD_B_ROOT=artifacts/neuro_symbolic_evolution_v4/runs/<run>/b_plan
export CMD_B_SOURCE_MATERIALIZATION_MANIFEST=artifacts/neuro_symbolic_evolution_v4/runs/<run>/cases.merged.jsonl.manifest.json
export CMD_B_SOURCE_EXPORT_SCHEMA=claude-tap-normalized-v1
export CMD_B_SOURCE_EXPORT_SHA256=<sha256-of-source-export>
```

`b_preflight` reports module availability and paths only. It does not execute an
experiment. Each named role must be invoked explicitly by the user and fails
closed if its expected Python module entrypoint is unavailable. Capture input,
output, backend, lineage root, and protocol manifest must remain bound to the
same run; downstream analysis must not synthesize missing capture evidence.

`CMD_B_CAPTURE_BACKEND` has no default: the experiment runner itself is not a
capture backend. The callable must accept one frozen plan mapping and return
the v2 result documented by `python -m experiments.v4_followup_capture --help`.
The source schema/hash variables are available to that backend through the
environment; the backend must copy their verified values into its closed
result.

The lineage projection receives the frozen selections file explicitly. The
lineage merge then binds all three manifests:
source-materialization → capture → projected lineage. A merge without these
hash-chain inputs is rejected by the dataset module and must not feed E2/E4/E4b.
Capture backend v2 must also provide the required `source_export_schema` and
`source_export_sha256`. A `claude-tap` capture is `VERIFIED` only when it
declares exactly `claude-tap-normalized-v1` and its source-export hash is a
valid matching SHA-256; declaration without a valid hash is rejected. E2, E4,
and E4b consume `b_plan/lineage/cases.typed.jsonl` plus its merge manifest,
never the pre-lineage materialization directly.

E4b additionally writes `ecology_ledger.jsonl` and `ecology_summary.json` with
niche snapshot, transition, discovery-pressure, rejected-transition, and legal
transition-rate counts. These are post-outcome descriptive appendix evidence;
the manifest records `affects_headline_decision=false`.
