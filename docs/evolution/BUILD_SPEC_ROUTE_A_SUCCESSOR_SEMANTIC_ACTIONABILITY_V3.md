# BUILD SPEC — Route A Successor: Semantic Actionability v3

Protocol ID: `route-a-successor-semantic-actionability-v3`

Status: **DRAFT — F0 pilot/calibration development is allowed; no `D_search`/`D_query` read, adaptive operator search, or real-store mutation is authorized before its explicit gate.**

Predecessors: `route-a-state-fitness-open-synthesis-v1` (**frozen; E0 STOP**) and `route-a-ir-v2-slot` (**withdrawn before freeze**).

## 1. Decision and scope

This is a new successor protocol, not an amendment or rerun of either
predecessor.  Its premise is that automatic repair has three separable
identifiability requirements:

```text
Relation Detectability -> Intervention Actionability -> Evolutionary Headroom
```

1. **Relation Detectability (G0):** item texts can be measured as asserting a
   same slot with different values, without metadata or construction shortcuts.
2. **Intervention Actionability (G1):** deployment-visible, independently
   trustworthy evidence identifies one item as the safe target of a destructive
   action.  A relation edge alone is not such evidence.
3. **Evolutionary Headroom (E0):** a fixed, non-adaptive, preregistered v3
   envelope has measurable value beyond the strongest fixed policy built from
   G0/G1, not merely beyond a blind v1 predicate.  E0 is a headroom measurement,
   not synthesis; only its GO may authorize a later adaptive search.

The proposal deliberately places semantic measurement outside the optimizer.
Model calls produce a frozen, calibrated relation graph once; every later arm
reads that graph.  It does **not** permit a model to re-judge pairs while a
search is selecting policies.

### 1.1 Non-goals

- Do not rescue, edit, reinterpret, or rerun `route-a-state-fitness-open-synthesis-v1`.
- Do not rehabilitate `route-a-ir-v2-slot`, `SLOT_DIVERGES`, or its reported
  coverage; its `store` <-> `memory_id` construction-marker failure remains a
  withdrawal of record.
- Do not infer currency from `item_id`, memory order, recall rank, text prefixes,
  `store`, an arbitrary parseable field, or textual style (for example “now” or
  “ever since”).
- Do not claim answer recovery, general deployment safety, causal memory
  correction, or autonomous evolution merely because a pair relation is found.
- Do not run an online repair, modify a user memory store, or turn a G0-only
  relation into `SUPPRESS`, `DEMOTE`, or `REPLACE`.

## 2. Threat model and invariants

The adversary is the experimental construction itself: hidden identity can
leak through every runtime field, rendering apparently semantic scores
meaningless.  The protocol therefore assumes any field may be a shortcut until
an audit disproves a predictive linkage on the relevant split.

| Threat | Required control |
|---|---|
| `memory_id`, `store`, rank, order, source, or prefix encodes target identity | Text-only G0 prompt; generic field audit and field-permutation tests before G1. |
| A judge learns direction from wording or presentation order | G0 is symmetric and direction-free; cross-case style permutations test this explicitly. |
| Search overfits a stochastic judge | Verdicts are precomputed, content-addressed, versioned, and frozen before policy search. |
| Pair-positive means both items are acted on | Typed v3 effects distinguish pair annotation from a unique superseded target. |
| A candidate improves one target while damaging related memories | Independent neighborhood/query evaluation plus transactional rollback in deployment G5. |
| A hand-designed semantic rule already solves the task | E0 compares its fixed candidate envelope with the strongest preregistered hand-rule baseline. |

**Fail-closed invariant.** `direction_unknown`, contradictory ordering evidence,
non-unique target, malformed verdict, unavailable cache entry, or a failed audit
must produce only `ANNOTATE_CONFLICT` or `ABSTAIN`; no destructive effect is
well typed for those states.

## 3. Dataset, staged freeze, and split contract

Freeze happens in two stages.  “Freeze before read” never means that a pilot has
to be designed without seeing pilot data.

1. **Development reservation (F0).** Before development, record source hashes,
   case IDs, family/domain grouping keys, and split assignment.  Reserve and
   access-control `D_search` and `D_query`.  Their outcomes may not be inspected,
   summarized, queried, or used in a model call.  The pilot, `D_cal`, and
   `D_dev` may be read after F0 to design and calibrate the instrument.
2. **Full protocol freeze (F1).** After pilot/`D_cal`/`D_dev` work is complete,
   replace every TBD in the machine-readable protocol with a concrete value,
   freeze the prompt/parser/model, evidence policy, grammar envelope, baselines,
   metrics, thresholds, statistical method, and budgets, then validate and hash
   the manifest.  `D_search` may only be opened by the registered fixed-budget
   E0 command after F1.  `D_query` remains unread until a synthesized artifact
   is frozen for its single registered confirmation read.

- `D_pilot`: blinded pilot used to determine annotation sample size, uncertainty
  method, and feasible thresholds.  It cannot later count as held-out evidence.
- `D_cal`: human-labelled relation/actionability calibration set; stratified by
  family/domain and includes positives, unrelated pairs, difficult same-topic
  non-conflicts, and unknown-direction pairs.
- `D_dev`: instrument development only.  Prompt wording, parser behavior,
  abstention policy, audit implementation, and the fixed baseline catalog may
  be changed here only before their freeze.
- `D_search`: first opened by fixed-envelope E0 after F1 and then available to
  an authorized synthesis run.  It may not trigger a fresh model call, modify
  the graph, or change a frozen choice.
- `D_query`: family-blocked, single-read confirmation set.  It is not read for
  E0, prompt/schema/threshold design, baseline selection, or adaptive synthesis.
- `D_deploy_canary`: separately authored cross-domain, planted relation cases;
  it is neither calibration nor a policy score.

No pair may cross splits.  More importantly, no semantic family/template may
appear in both `D_dev` and `D_query`; report the grouping key and all exclusions.
The count, sampling procedure, inter-annotator protocol, adjudication rule,
and all numerical gates are **TBD** until a blinded pilot and power analysis are
completed.  They must be written into `protocol_freeze.json` before any outcome
on `D_search` or `D_query` is read.  No post-hoc threshold selection is valid.

### 3.1 F0 dataset manifest and access ledger

`dataset_manifest.json` is closed under
`route-a-successor-v3-dataset-manifest-v2`.  It contains exactly:

```text
schema_version, created_at, source_files, cases, pairs, templates,
split_hashes, access_log_path, access_log_genesis_sha256
```

- `source_files[] = {path, sha256}`; paths are repository-relative and unique.
- `cases[] = {case_id, split, family_id, domain_id, template_ids, pair_ids}`.
  `split` is exactly one of `pilot|cal|dev|search|query|deploy_canary`.
- `pairs[] = {pair_id, case_id, left_item_id, right_item_id, template_id}`;
  endpoints are distinct, IDs are globally unique, and the pair's template is
  listed by its case.
- `templates[] = {template_id, family_id, domain_id}`.  Case, pair, family, and
  template mappings are total: no ID may be inferred from list position.
- `split_hashes` has exactly the six split names, each containing
  `{case_ids_sha256, pair_ids_sha256, family_ids_sha256,
  template_ids_sha256}` over sorted unique IDs.

The protected split reader appends one canonical JSONL row for every attempted
read, list, aggregate, export, or model-input operation:

```text
{seq, previous_entry_sha256, at, actor_id, command_sha256, purpose,
 operation, requested_split, case_ids_sha256, allowed, result_sha256,
 entry_sha256}
```

`purpose` is `instrument_development|calibration|audit|e0|synthesis|confirmation`;
`operation` is `read|list|aggregate|export|model_input`.  Rows are numbered from
zero and hash-chain canonical JSON excluding `entry_sha256`.  Before validated
F1, every `search`/`query` request must be denied and still logged.  Any allowed
sealed-split row before F1 withdraws the protocol.  Direct filesystem access to
protected payloads is forbidden; the F0 validator recomputes case/pair/family/
template maps and all split hashes from the source manifests.

### 3.2 Closed `protocol_freeze.json` schema

The committed F1 JSON is the executable authority.  Its top level contains
exactly:

```text
schema_version, protocol_id, freeze_stage, frozen_at,
dataset_manifest_sha256, splits, instrument, ordering_policy, gates,
grammar, budgets, commands, query_policy, predecessor_status
```

`schema_version`, `protocol_id`, and `freeze_stage` are the literals
`route-a-successor-v3-freeze-schema-v2`,
`route-a-successor-semantic-actionability-v3`, and `F1`.  `splits` contains the
six exact split entries, each
`{case_ids_sha256,pair_ids_sha256,family_ids_sha256,template_ids_sha256}`.
`instrument` is closed as:

```text
{model_id, model_revision, temperature, top_p, seed, max_output_tokens,
 prompt_sha256, parser_version, normalization_version, cache_schema_version}
```

The four numeric generation fields have finite concrete values; no arbitrary
`model_parameters` object is allowed.  `budgets` contains exactly
`{human_labels,unique_pair_calls,retries,e0_candidates,synthesis_seeds,
proposals_per_seed,query_reads}` as non-negative integers, with all operational
budgets positive and `query_reads=1`.  `predecessor_status` is exactly
`{route_a_v1:E0_STOP_FROZEN,route_a_v2_slot:WITHDRAWN}`.
`query_policy` is exactly
`{ledger_path:"artifacts/route_a_successor_v3/query/query_read_ledger.sqlite3",
ledger_genesis_sha256,max_reservations:1,reservation_consumes_read:true}`;
the validator verifies this value against the canonical schema-descriptor hash
exported by `successor_query_ledger.py`; it does not create or hash mutable
SQLite bytes.  The confirmation command owns creation of the live database,
and later artifacts must reference the registered descriptor hash.

### 3.3 Closed ordering policy

`ordering_policy` contains exactly
`{policy_version,accepted_sources,source_semantics,conflict_policy}`;
`conflict_policy` is the literal `fail_closed`. `accepted_sources` is a
non-empty, duplicate-free subsequence of the three sources below.
`source_semantics` has exactly one entry per accepted source and no others:

| source | exact nested object |
|---|---|
| `observed_at` | `{semantic:"chronology_lower_target", comparable_domain_field:"observed_at_domain", value_type:"rfc3339_timezone_aware", requires_equal_domain:true}` |
| `event_sequence` | `{semantic:"chronology_lower_target", comparable_domain_field:"event_stream_id", value_type:"nonnegative_integer", requires_equal_domain:true}` |
| `source_priority` | `{semantic:"higher_wins", comparable_domain_field:"source_priority_domain", value_type:"integer", requires_equal_domain:true}` |

These strings and keys are literals enforced by `REGISTERED_SOURCE_SEMANTICS`
in `item_ordering.py`; alternate orientation, domain field, or value type
requires a new policy version and F1.

### 3.4 Closed grammar, gate, baseline, envelope, and command schemas

`grammar` contains exactly `{version,leaves,effects,bounds}`.  Version/leaves/
effects are the exact minimal v3 values in §6. `bounds` has exactly these values,
matching the currently imported `REGISTERED_BOUNDS`:

```text
{max_depth:3, max_nodes:32, max_actions_per_case:4,
 max_retrieved_additions:4, max_token_delta:512, max_logical_cost:16}
```

`RETRIEVE_FILL` remains unsupported; the inherited retrieval/token maxima are
recorded compatibility bounds, not permission to add content.

`gates` has exactly `g0|g1|g2|g3|e0`, with no free metric object:

- `g0 = {metric_version,relation_precision_min,relation_recall_min,
  permutation_fpr_max,canary_recall_min,abstention_rate_max,confidence_level,
  bootstrap_iterations,bootstrap_seed,min_pairs,min_positive_pairs,
  min_negative_pairs,min_families}`;
- `g1 = {metric_version,target_precision_min,target_recall_min,
  ordering_coverage_min,destructive_coverage_min,unknown_rate_max,
  conflict_rate_max,confidence_level,bootstrap_iterations,bootstrap_seed,
  min_pairs,min_directional_pairs,min_families}`;
- `g2 = {metric_version,min_firing_cases,min_firing_families,
  null_false_fire_max,field_alignment_max,nmi_alarm_max,
  permutation_target_precision_max,reusable_value_unique_ratio_max}`;
- `g3 = {baseline_catalog_path,baseline_catalog_sha256}`;
- `e0 = {candidate_envelope_path,candidate_envelope_sha256,score_metric,
  family_aggregation,strict_gain_min,confidence_level,bootstrap_iterations,
  bootstrap_seed,tie_epsilon,tie_policy,missing_policy,nonfinite_policy}`.

All rates/confidence levels lie in `[0,1]`; counts are positive integers; seeds
are integers; paths are repository-relative; hashes are lowercase SHA-256.
The G0/G1 command layer extracts these fields directly from the content-bound
F1 manifest. It applies the registered support minima and deterministic
family-block bootstrap bounds: lower bounds govern minimum metrics, and upper
bounds govern maximum error/abstention/unknown/conflict metrics. G2 applies
`null_false_fire_max`, direct field alignment, normalized mutual information,
the reusable-value unique-ratio cutoff, and requires explicit field-permutation
target predictions before its shortcut audit can GO. No registered threshold
may be present in F1 but ignored by the decision code.
`score_metric` and `family_aggregation` are the registered literals
`state_fitness` and `macro_mean_by_family`, respectively,
`tie_policy=STOP`, `missing_policy=STOP`, and `nonfinite_policy=STOP`.  Numeric
gate values are TBD only while reading F0 pilot/cal/dev; every field is concrete
before F1 validation.

The baseline catalog is canonical JSON with exactly
`{schema_version,protocol_id,grammar_version,baselines,catalog_sha256}`.
`baselines` is sorted by ID and contains exactly B0--B4 from §5, each
`{baseline_id,description,program,canonical_ast_sha256}`.  B0 uses the canonical
empty `SEQUENCE`; B1--B4 contain their exact AST.  `catalog_sha256` hashes the
canonical object without its own hash.

The E0 envelope is canonical JSON with exactly
`{schema_version,protocol_id,grammar_version,adaptive,generation_rule,
generation_rule_sha256,candidates,candidate_envelope_sha256}`. `adaptive=false`;
`generation_rule` is exactly `explicit_frozen_ast_list_v1`; its hashed rule
permits authoring valid bounded ASTs from pilot/dev evidence only and forbids
score-dependent generation, sampling, mutation, or expansion after F1. Each
candidate is `{candidate_id,program,canonical_ast_sha256}`, sorted by
`candidate_id`, unique by both ID and AST hash. Its count equals
`budgets.e0_candidates`; the envelope hash excludes only itself. E0's claim is
about this explicit envelope, never exhaustiveness over all bounded v3 ASTs.
Candidate AST hashes must be disjoint from B0--B4 hashes; a renamed baseline is
not headroom.

Each `commands.e0|synthesis|confirmation` locator contains exactly
`{script,script_sha256,entrypoint,required_flags,network_policy}`.  Scripts are,
respectively, `experiments/run_successor_v3_e0.py`,
`experiments/run_successor_v3_synthesis.py`, and
`experiments/run_successor_v3_confirmation.py`; `entrypoint=main`,
`network_policy=deny`. Required flags, in this exact order, are:

| locator | `required_flags` |
|---|---|
| e0 | `--protocol-freeze,--protocol-validation,--upstream-gates,--baseline-catalog,--envelope,--graph-manifest,--search-split,--access-ledger,--results,--output` |
| synthesis | `--f1-validation,--f1-validation-sha256,--f1-manifest-sha256,--gate-bundle,--e0-result,--plan,--candidates,--output` |
| confirmation | `--f1-validation,--f1-validation-sha256,--f1-manifest-sha256,--gate-bundle,--e0-result,--winner,--query,--query-input-sha256,--family-block-sha256,--ledger,--output` |

Arbitrary argv, shell fragments, unknown flags, positional paths, reordered
flags, or an unhashed executable are invalid. Runtime values may fill flag
arguments but cannot alter locator or add an option.

### 3.5 Validator contract

An F1 file is invalid if it contains `TBD`, placeholders, null required values,
free/unknown keys, non-positive operational budgets, or `query_reads != 1`.
`experiments/validate_successor_v3_protocol.py` must:

- validate the closed JSON schema, enum values, SHA256/RFC3339 forms, and all
  required nested keys;
- verify case, pair, family, domain, and template mappings; split disjointness;
  family/template blocking; the F0 access hash chain; and absence of an allowed
  `D_search`/`D_query` read before F1;
- recompute every referenced file hash and require the exact current minimal v3
  leaves/effects/bounds, nested source semantics, gates, baselines, E0 envelope,
  and command locators;
- reject any TBD/NaN/infinity, empty/unknown key, out-of-range gate, exceeded
  budget, duplicate AST, hash drift, predecessor status change, or query-ledger
  entry before synthesis winner freeze;
- emit `protocol_freeze_validation.json` containing `valid`, ordered reasons,
  validator version, manifest hash, and all recomputed hashes; exit nonzero on
  invalidity.  Gate commands must require `valid=true` and the exact manifest
  hash.  Merely finding the file is never authorization.

## 4. Typed semantic and actionability contracts

### 4.1 Relation layer (G0)

Input is exactly an unordered pair of normalized item texts.  It cannot receive
item IDs, ranking, stores, timestamps, provenance, source labels, or a side
described as old/new.

```python
class RelationType(str, Enum):
    UNRELATED = "unrelated"
    SAME_SLOT_DIFFERENT_VALUE = "same_slot_different_value"
    UNCERTAIN = "uncertain"

@dataclass(frozen=True)
class RelationVerdict:
    relation: RelationType
    slot: str | None                  # explanatory, never an action key
    abstained: bool
    prompt_sha256: str
    parser_version: str
    model_id: str
```

`relation(a, b)` is canonicalized on an unordered text-pair cache key and must
be observationally symmetric.  `slot` may be absent; it must not reintroduce
identity through a dataset-specific vocabulary.  Parse, transport, or schema
failure maps to `UNCERTAIN`, never to positive.

### 4.2 Ordering layer (G1)

Ordering is intentionally a separate adapter over deployment-visible evidence.
It must not invoke the semantic judge or treat pair order as chronology.

```python
class ResolutionRelation(str, Enum):
    LEFT_TARGET_RIGHT_SURVIVES = "left_target_right_survives"
    RIGHT_TARGET_LEFT_SURVIVES = "right_target_left_survives"
    UNKNOWN = "unknown"
    CONFLICTING = "conflicting"

@dataclass(frozen=True)
class OrderingEvidence:
    item_id: str
    observed_at: datetime | None
    observed_at_domain: str | None
    event_sequence: int | None
    event_stream_id: str | None
    source_priority: int | None
    source_priority_domain: str | None
    provenance: str
    deployment_visible: bool
    reliability: Literal["trusted", "untrusted", "unknown"]
    audit_version: str

@dataclass(frozen=True)
class OrderingVerdict:
    relation: ResolutionRelation
    agreeing_sources: tuple[str, ...]
    conflicting_sources: tuple[str, ...]
    reason_code: str
    policy_version: str

@dataclass(frozen=True)
class ActionabilityVerdict:
    relation_edge_id: str
    target_item_id: str | None
    survivor_item_id: str | None
    mode: Literal["destructive", "annotate_only", "abstain"]
    reason_code: str
```

Only a G0 positive plus accepted and audited ordering evidence can yield a
unique target.  The operational source semantics are:

- `observed_at`: chronology; the lower timestamp is the target and the higher
  timestamp survives, only when both values share an audited clock/domain;
- `event_sequence`: chronology within one audited event stream; the lower
  sequence is the target and the higher sequence survives;
- `source_priority`: **authoritative preference, not chronology**.  The current
  minimal policy is `higher_wins`: the lower trusted priority is the target and
  the higher priority survives.  F1 must spell out this orientation in
  `source_semantics`; changing it creates a new policy version.  No claim is
  made that either item occurred first.

Both items must carry comparable, trusted, deployment-visible sidecars from an
F1-allowlisted source.  Equal values are non-directional.  Every available
accepted signal must select the same target/survivor.  A disagreement between
chronology and authority, between two clocks/streams, or between any accepted
signals is `CONFLICTING` and fails closed.  V3 has no tie-break or precedence
fallback.  In the absence of unanimous evidence, the only valid result is
`annotate_only` or `abstain`.

For each F1-accepted source: both values absent means “unavailable” and it is
skipped; exactly one present is `incomplete` and the whole verdict is UNKNOWN;
both present with missing/unequal domains is `incomparable_domain` and UNKNOWN;
equal comparable values are non-directional and skipped; unequal comparable
values produce one direction.  Zero directional sources is UNKNOWN. Multiple
directional sources agreeing yield that direction and the exact ordered
`agreeing_sources`; disagreement yields CONFLICTING and the exact ordered
`conflicting_sources`.  Source order is F1 `accepted_sources`, never runtime
mapping order.

G1's word “destructive” describes an action *type*, not permission to mutate a
real store.  Through E0 and synthesis, destructive effects execute only against
an offline `RepairState` sandbox.  A production adapter must require a signed or
content-bound G5 authorization token naming protocol hash, program hash, graph
hash, case/item target, expiry, and rollback policy.  Without that token, real
mutation is rejected even after G1 GO.

### 4.3 Frozen relation graph

```python
@dataclass(frozen=True)
class RelationEdge:
    edge_id: str
    pair_id: str
    left_item_id: str
    right_item_id: str
    relation: RelationVerdict
    relation_cache_record_sha256: str
    relation_verdict_sha256: str
    left_ordering_evidence: OrderingEvidence
    right_ordering_evidence: OrderingEvidence
    source_comparisons: tuple[SourceComparison, ...]
    ordering: OrderingVerdict
    actionability: ActionabilityVerdict
    edge_sha256: str
```

`SourceComparison` is closed as
`{source,semantic,comparable_domain,left_value,right_value,comparable,
outcome,reason_code}`.  `source` follows F1 accepted-source order;
`semantic` is the exact registered string; `comparable_domain` is the common
non-empty domain or null; `outcome` is
`left_target_right_survives|right_target_left_survives|equal|incomplete|
incomparable_domain`.  `ordering.agreeing_sources` contains every comparable,
directional source when all agree. `conflicting_sources` contains all
directional sources participating in disagreement and is non-empty only for
`CONFLICTING`; refusals cannot claim agreeing sources.

Both endpoint sidecars are stored in full, including per-source domain and raw
value, provenance, audit version, deployment visibility, and reliability.
Sensitive values may be encrypted at rest, but a one-way value hash alone is
insufficient because replay must recompute comparisons.  The graph also stores:

- `relation_cache_record_sha256`: SHA-256 of the canonical cache audit row
  `{cache_key,canonical_left,canonical_right,prompt_sha256,parser_version,
  model_id,model_config_hash,normalization_version,instrument_version,verdict}`;
- `relation_verdict_sha256`: SHA-256 of the canonical closed `RelationVerdict`;
- `edge_id`: stable, non-circular SHA-256 identity of
  `{protocol_id,case_id,pair_id,left_item_id,right_item_id}`; the actionability
  verdict refers to this ID;
- `edge_sha256`: SHA-256 of every edge field above except itself.  It covers
  `edge_id` and actionability but is not copied back into actionability, avoiding
  a self-referential hash. An edge content hash derived only from endpoints and
  relation is invalid.

Replay loads the named cache row, recomputes its cache key and both hashes,
recomputes each source comparison from the stored endpoint sidecars under the
exact F1 policy, then recomputes ordering and actionability byte-for-byte.  Any
missing row, redaction that prevents replay, differing verdict, source list,
conflict list, target, survivor, or hash makes the graph unusable.  The current
`relation_graph.py`/`build_relation_graph.py` must implement these additional
fields before G1/G2 graph artifacts can pass; their earlier endpoint-only edge
shape is not sufficient evidence.

The graph manifest records `case_id`, `runtime_case_sha256`, sorted item-ID-set
hash, relation model/parser/prompt/normalization hashes, ordering policy hash,
evidence adapter, dataset manifest, cache manifest, and ordered edge hashes.
Each graph shard is bound to exactly one case.  The executor must reject a shard
when: its manifest hash is not the run's registered graph hash; its `case_id` or
runtime case hash differs; its item-set hash differs from the input state; an
edge is self/cross-case, duplicated, or refers to an absent item; an
actionability target/survivor is not the edge's two endpoints; evidence replay
fails; or the edge was built under a different protocol/policy/cache version.
No graph union, partial fallback, dangling-ID filtering, or live graph repair is
permitted during evaluation.

## 5. Instrument and audit gates

### G0 — Relation Detectability

Required F1-registered reports, each broken down by domain and family:

- human-labelled precision, recall, abstention, and confusion matrix;
- cross-case *text-style permutation* false-positive rate: exchange stale/current
  writing-style markers across otherwise matched cases while preserving the
  intended semantic relation and direction labels;
- cross-domain planted-canary recall on authored templates not seen in prompt
  development;
- symmetry, metadata-unreachability, normalization, parsing, and cache replay
  tests.

G0 permits only construction of relation edges and conflict annotations.  Its
numeric thresholds, confidence interval method, minimum per-family support, and
acceptable abstention range are **TBD during F0 development** because this repo
does not yet contain a calibrated annotation study or power estimate.  All are
mandatory concrete F1 fields before `D_search` is opened.

### G1 — Intervention Actionability

Required reports distinguish pair detection from target correctness:

- ordering-evidence coverage and source distribution;
- unique target precision/recall on an independently labelled actionability set;
- destructive coverage, unknown-direction share, and conflicting-evidence rate;
- field-to-hidden-identity audit, field-permutation target test, and audit of
  every evidence adapter used by the resolver.

G1 fails if direction can be reconstructed from a construction marker, if the
evidence is not deployment visible, or if any unknown/conflicting direction can
reach a destructive target.  Thresholds are **TBD during F0 development** and
must be populated and validated at F1.  G1 GO authorizes offline simulation
only; it never produces a production mutation credential.

### G2 — Predicate activity and type safety

On frozen `D_dev` runtime states, report each leaf's firing cases, firing pairs,
unique targets, null-case fires, action-compatible fires, and distribution by
family.  The minimum number and diversity requirement are **TBD +
freeze-before-read during F0** and mandatory at F1.  A leaf with zero valid
fires is not eligible for search.

The generic shortcut audit is not a fixed blacklist.  For every exposed runtime
field it measures cardinality, deterministic mapping accuracy to hidden target
identity, association alarm (including normalized mutual information when
applicable), and target performance after within-case and cross-case field
permutation.  Any material shortcut alarm blocks that field/evidence adapter;
the numerical alarm policy is frozen before outcomes.

### G3 — fixed-policy baseline gate

Enumerate and freeze this exact strongest legal non-evolved catalog before
opening `D_search`:

```text
B0 identity / no-op
B1 DIVERGENT_PAIR_MEMBER -> ANNOTATE_CONFLICT
B2 SUPERSEDED_ITEM -> DEMOTE
B3 SUPERSEDED_ITEM -> SUPPRESS
B4 SUPERSEDED_ITEM -> REPLACE (offline historical disposition)
```

`SUPERSEDED_ITEM` is only G1-confirmed target identity.  `DIVERGENT_PAIR_MEMBER`
may feed only annotation.  Baselines may not be weakened to make synthesis look
useful.

### E0 — fixed-budget headroom measurement

After G0--G3 GO, exhaustively evaluate a finite candidate list/envelope frozen
at F1 on family-blocked `D_search`.  This is not adaptive synthesis: there is no
proposal model, mutation, prompt revision, intermediate-result-dependent
candidate generation, or budget expansion.  The command emits only the
registered comparison to the best G3 fixed baseline.

E0 GO means the preregistered headroom rule is met under its frozen uncertainty
method and authorizes a separately budgeted adaptive synthesis stage.  E0 STOP
ends the route; `D_query` remains sealed.  Effect size, alpha/interval, power,
candidate count, and decision rule are **TBD during F0 development** and must be
concrete at F1.  A tie, support-only gain, failed upstream gate, outcome-aware
candidate change, or rerun is STOP.

Operationally, E0 must consume the validated F1 hash, G0--G3 bundle hash,
baseline catalog hash, candidate envelope hash, per-case graph-manifest hash,
`D_search` split hash, and current F0 access-ledger head.  It evaluates every
catalog baseline and every envelope candidate on every registered searchable
case exactly once; a missing/extra/duplicate candidate or case is STOP. Scores
are aggregated by the frozen `score_metric` and `family_aggregation` only.

Let `b*` be the maximum G3 score and `c*` the maximum candidate score.  Report
all baseline IDs attaining `b*`.  A candidate winner exists only when exactly
one candidate is more than `tie_epsilon` above every other candidate; otherwise
`tie_policy=STOP`.  E0 is GO only when the family-clustered confidence interval's
lower bound for `c* - b*` is strictly greater than `strict_gain_min`.  Equality,
non-finite scores, missing families, exhausted budget, hash drift, an opened
`D_query`, or any policy-visible intermediate result is STOP.  Candidate ID is
never a score tie-break.

`e0/headroom_results.json` is closed as
`{schema_version,protocol_id,protocol_freeze_sha256,upstream_gate_sha256,
baseline_catalog_sha256,candidate_envelope_sha256,graph_manifest_sha256,
search_split_sha256,access_ledger_head_before,baseline_rows,candidate_rows,
best_baseline_ids,best_candidate_id,strict_gain,confidence_interval,decision,
adaptive_synthesis_authorized,query_read_authorized,failures,e0_result_sha256}`.
Rows bind ID, AST hash, per-family scores, and aggregate score. `decision=GO`
implies `adaptive_synthesis_authorized=true` and
`query_read_authorized=false`; every other combination is invalid.  The current
`run_successor_v3_e0.py` implements the coarse strict-gain skeleton; it must add
these bindings, CI, completeness, and tie rules before its output is an
authoritative E0 artifact.

## 6. v3 grammar and implementation boundaries

Create a new implementation namespace; do not import v2's sensor as a source of
evidence and do not change v1 source, version strings, or artifacts.

```text
cmd_audit/counterfactual/slot_relation.py
cmd_audit/counterfactual/relation_cache.py
cmd_audit/counterfactual/item_ordering.py
cmd_audit/counterfactual/actionability.py
cmd_audit/counterfactual/relation_graph.py
cmd_audit/counterfactual/successor_program_ir.py
cmd_audit/counterfactual/successor_state_executor.py
experiments/run_relation_calibration.py
experiments/run_actionability_audit.py
experiments/audit_predicate_activity.py
experiments/audit_runtime_shortcuts.py
experiments/build_relation_graph.py
experiments/validate_successor_v3_protocol.py
experiments/run_successor_v3_e0.py
experiments/check_successor_v3_gates.py
experiments/run_successor_v3_synthesis.py
experiments/run_successor_v3_confirmation.py
```

The repository currently contains all paths above, including
`relation_graph.py`, `successor_protocol_freeze.py`, the F1 validator, and the
E0/synthesis/confirmation runners. Their structural schema-v2 conformance and
fail-closed contracts are covered by the repository test suite. This is still
**implementation evidence, not empirical protocol evidence**: an actual F1
manifest, real registered observations, and the resulting G0--E0 artifacts must
validate and pass before any scientific GO may be claimed.

The new grammar version is `route-a-ir-v3-semantic-actionability`.  The current
minimal v3 grammar is closed and contains exactly:

```text
nodes:       IF, SEQUENCE
connectives: AND, OR, NOT
leaves:      DIVERGENT_PAIR_MEMBER, SUPERSEDED_ITEM
effects:     KEEP, PRESERVE, ANNOTATE_CONFLICT, ABSTAIN, VERIFY,
             DEMOTE, SUPPRESS, REPLACE
unsupported: RETRIEVE_FILL and every v1 evidence leaf
```

### 6.1 Normative AST, canonicalization, and execution

The JSON AST is closed:

```text
IF       = {"node":"if", "predicate":Predicate, "action":{"kind":Effect}}
SEQUENCE = {"node":"sequence", "body":[Program, ...]}
Predicate leaf = {"kind":"divergent_pair_member"|"superseded_item"}
Predicate AND/OR = {"kind":"and"|"or", "operands":[Predicate, Predicate, ...]}
Predicate NOT = {"kind":"not", "operands":[Predicate]}
```

There are no optional keys, thresholds, labels, metadata, or extension fields.
AND/OR require at least two operands; NOT exactly one; leaves have no operands.
Unknown/missing keys or enum values fail parsing.  Canonicalization recursively
flattens same-kind AND/OR, sorts and deduplicates their operands by serialized
predicate, removes double NOT, flattens nested SEQUENCE in source order, and
removes KEEP/PRESERVE rules.  It never reorders remaining IF rules.  The empty
canonical SEQUENCE is B0 only and is excluded from candidate/synthesis proposal
ledgers.  Every AST hash is produced by the one implementation
`successor_program_ir.canonical_ast_hash`; manifests store both AST and hash and
validators recompute rather than reproduce the serializer independently.

Predicate matching is set-valued against the frozen graph and initial case item
set: AND is intersection, OR union, NOT complement over that initial set.
SEQUENCE evaluates every canonical IF in source order; matching is not changed
by earlier dispositions.  A non-matching IF does nothing.  ABSTAIN/VERIFY set
the result's abstention flag but do not short-circuit later rules.  A matched
state-changing rule emits a transition and charges one logical-cost unit per
matched item.  If multiple rules touch an item, each transition and cost is
recorded and the later rule's disposition is final.  Bounds are checked before
execution and the logical-cost bound after each rule; overflow invalidates the
whole offline execution, not a prefix result.

Effect semantics are exact:

| effect | offline result |
|---|---|
| KEEP/PRESERVE | byte-identical state; canonicalizer removes the rule |
| ABSTAIN/VERIFY | no disposition change; `abstained=true` |
| ANNOTATE_CONFLICT | matched items become `conflict` |
| DEMOTE | matched G1 target becomes `demoted` |
| SUPPRESS | matched G1 target becomes `suppressed` |
| REPLACE | matched G1 target becomes `historical` |

REPLACE neither copies the survivor, rewrites text, creates an item, nor calls a
generator.  A future content replacement effect requires a new grammar and
locality protocol.

Legal effect typing is narrower than the enum:

- `DIVERGENT_PAIR_MEMBER` may use only `KEEP`, `PRESERVE`,
  `ANNOTATE_CONFLICT`, `ABSTAIN`, or `VERIFY`;
- `SUPERSEDED_ITEM` may additionally use `DEMOTE`, `SUPPRESS`, or `REPLACE`;
- a destructive effect requires the predicate to be exactly
  `SUPERSEDED_ITEM`, not a connective that contains or negates it;
- `REPLACE` means the existing offline historical disposition; it does not
  synthesize replacement content.

`KEEP`/`PRESERVE` are identity effects.  `ABSTAIN`/`VERIFY` do not change item
disposition and record a refusal/verification requirement.  `ANNOTATE_CONFLICT`
changes only the offline conflict disposition.  The remaining three are the
only destructive action types and remain sandbox-only before G5.

V3 may reuse v1's `ActionKind`, `ResourceBounds`, state disposition primitive,
and other pure implementation types only when tests pin their behavior.  This
is **code reuse, never evidentiary reuse**: no v1 predicate verdict, grammar
envelope, headroom number, E0 result, version string, or artifact enters v3.
Adding any v1 leaf later requires a new grammar version, envelope hash, activity
audit, baseline catalog, and F1 freeze; it is not compatible with this minimal
v3.

The effect types must make this illegal at parse/type-check time:

```text
DIVERGENT_PAIR_MEMBER -> {DEMOTE, SUPPRESS, REPLACE}
DirectionUnknown        -> {DEMOTE, SUPPRESS, REPLACE}
```

The only state-changing legal G0-only effect is `ANNOTATE_CONFLICT`.
`SUPERSEDED_ITEM` is a target-valued, G1-confirmed leaf and is the sole
relation-derived operand of a destructive effect.  Do not generate a generic
predicate/action Cartesian product that can later bypass these restrictions.

## 7. Cache and artifact contract

Cache keys must include canonical unordered normalized text pair, prompt SHA256,
model identity, model parameters/revision where available, parser version, and
normalization version.  Cache records are append-only; changed keys create new
records.  A missing or incompatible record is uncertainty, not a live fallback
call during a frozen run.

The live cache is the implemented SQLite `RelationCache` at
`instrument/relation_cache.sqlite3`. Before graph construction,
`RelationCache.audit_rows()` is exported in `cache_key` order to canonical
`instrument/relation_cache_audit.jsonl`; its file SHA-256 and row count are
frozen in `relation_cache_manifest.json`. Graph edges bind audit-row hashes from
this export. The mutable SQLite file itself is never used as a content hash, and
an unexported row cannot enter a graph.
The manifest is closed as
`{schema_version,cache_schema_version,audit_export_path,
audit_export_sha256,row_count,first_cache_key,last_cache_key,prompt_sha256,
parser_version,model_id,model_config_hash,normalization_version,
instrument_version,manifest_sha256}`.

```text
artifacts/route_a_successor_v3/
  protocol_freeze.json
  protocol_freeze_validation.json
  instrument/human_calibration.jsonl
  instrument/prompt_manifest.json
  instrument/relation_cache.sqlite3
  instrument/relation_cache_audit.jsonl
  instrument/relation_cache_manifest.json
  instrument/permutation_results.json
  instrument/canary_results.json
  instrument/relation_gate.json
  actionability/evidence_adapter_manifest.json
  actionability/ordering_audit.json
  actionability/shortcut_audit.json
  actionability/actionability_gate.json
  graph/relation_graph.jsonl
  graph/relation_graph_manifest.json
  activity/predicate_activity.json
  activity/activity_gate.json
  baseline/baseline_catalog.json
  baseline/baseline_results.json
  e0/headroom_results.json
  e0/headroom_gate.json
  synthesis/run_manifest.json
  synthesis/proposal_ledger.jsonl
  synthesis/seed_winners.json
  synthesis/synthesis_results.json
  selection/winner_freeze.json
  query/query_read_ledger.sqlite3
  query/confirmation_results.json
  deployment/g5_policy.json
  deployment/use_ledger.sqlite3
  selection/artifact_freeze_manifest.json
```

The four `synthesis/*` and all downstream selection/query artifacts must be
absent unless E0 is GO. Deployment artifacts must be absent unless the sole
confirmation is GO and a separate G5 policy freeze passes.

Every result names all upstream hashes and declares whether it is a measurement,
baseline, search, confirmation, or deployment artifact.  Existing
`artifacts/route_a/` is read-only predecessor evidence and is never overwritten.

### 7.1 Adaptive synthesis and winner freeze

Synthesis starts only from an E0 artifact whose exact hash authorizes it.  Its
closed `run_manifest.json` is
`{schema_version,protocol_id,protocol_freeze_sha256,e0_result_sha256,
candidate_envelope_sha256,graph_manifest_sha256,search_split_sha256,
algorithm_id,algorithm_version,seeds,proposals_per_seed,selection_metric,
family_aggregation,model_calls,network_policy,run_manifest_sha256}`.
Seeds and proposal budget equal F1, `model_calls=0`, and
`network_policy=deny`.  The algorithm may mutate/crossover only valid minimal
v3 ASTs within F1 bounds.

Each append-only proposal row is
`{seq,previous_entry_sha256,seed,generation,proposal_index,parent_ast_hashes,
program,canonical_ast_sha256,valid,invalid_reason,per_family_scores,
aggregate_score,entry_sha256}`.  Invalid proposals consume budget.  Exactly one
winner per registered seed enters `seed_winners.json`; the preregistered final
selection metric selects a unique program across seeds.  A tie within F1
`tie_epsilon`, missing proposal, duplicate AST presented as a new proposal,
budget excess, non-finite score, or hash-chain break is synthesis STOP.

`synthesis_results.json` is closed as
`{schema_version,protocol_id,run_manifest_sha256,proposal_ledger_head_sha256,
seed_winners_sha256,selected_program,selected_program_sha256,
selected_search_score,selection_rule,decision,failures,
synthesis_results_sha256}`.  Only `decision=GO` can be frozen.  The immutable
`winner_freeze.json` contains exactly
`{schema_version,protocol_id,protocol_freeze_sha256,e0_result_sha256,
synthesis_results_sha256,program,program_sha256,graph_manifest_sha256,
search_split_sha256,query_split_sha256,query_ledger_genesis_sha256,frozen_at,
winner_freeze_sha256}`.  Every hash is recomputed before freeze; the program is
the selected canonical AST byte-for-byte.  Creating a second winner freeze is a
withdrawal, not a rerun.

### 7.2 Query read ledger and confirmation chain

`D_query` is opened through `run_successor_v3_confirmation.py` and
`QueryReadLedger` only. Before reading any payload, the command verifies F1, E0,
synthesis, and winner hashes, then executes `BEGIN IMMEDIATE` and inserts the
one claim. The committed claim consumes the sole read even if execution crashes.
There is no retry or replacement winner.

The SQLite database contains exactly one protocol table:

```sql
CREATE TABLE successor_v3_query_reads (
  protocol_manifest_sha256 TEXT PRIMARY KEY,
  input_sha256 TEXT NOT NULL,
  family_block_sha256 TEXT NOT NULL,
  winner_sha256 TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('CLAIMED','SUCCESS','FAILED')),
  claimed_at TEXT NOT NULL,
  finished_at TEXT,
  artifact_sha256 TEXT
)
```

`ledger_genesis_sha256` is the hash of the canonical schema descriptor above,
not mutable SQLite file bytes. The primary key enforces one claim per F1
protocol. `finish` may transition the exact matching CLAIMED row once to SUCCESS
or FAILED and bind `artifact_sha256`; no delete, reset, or second database is
valid. The immutable inserted row is serialized with the descriptor's exact
column names and hashed as `query_claim_row_sha256`; the terminal row is
serialized the same way and hashed as `query_terminal_row_sha256`.

`confirmation_results.json` contains exactly
`{schema_version,protocol_id,protocol_version,protocol_freeze_sha256,
winner_freeze_sha256,program_sha256,query_split_sha256,per_family_scores,
aggregate_score,confirmation_rule,decision,production_mutation_authorized,
query_read_authorized,failures,hash_chain,query_claim_row_sha256,
confirmation_payload_sha256,query_terminal_row_sha256,
confirmation_result_sha256}`. `confirmation_payload_sha256` hashes this closed
object while excluding itself, `query_terminal_row_sha256`, and
`confirmation_result_sha256`. `finish` writes that payload hash to the terminal
row's `artifact_sha256`; `query_terminal_row_sha256` then hashes the completed
row, including that payload hash. Finally, `confirmation_result_sha256` hashes
the closed final object while excluding only itself. These three distinct hash
domains prevent a content-hash cycle. A publication claim requires the chain

```text
F1 hash -> E0 GO hash -> synthesis GO hash -> winner-freeze hash
        -> query CLAIMED-row hash -> confirmation-payload hash
        -> terminal-row hash -> final confirmation-result hash
```

with one reservation total. Missing links, a pre-winner read, multiple
reservations, result/winner drift, or a failed confirmation rule is STOP and
may not be retried.

## 8. Test matrix

| Area | Required tests |
|---|---|
| Relation | symmetric canonical key; prompt has both texts and no metadata; prefix stripping; malformed/transport fail closed; parser schema rejection; deterministic cache replay/version miss. |
| Calibration | held-out human labels; style permutation; unseen-domain canary; per-family reporting; no pair/family split leakage. |
| F0/F1 | total case/pair/family/template mappings; access-log chain and denied attempts; closed nested source/gate/bounds/baseline/envelope/command schemas; every unknown/free key rejected. |
| Ordering | each accepted source is deployment-visible; exact domain-field/value-type/orientation semantics; incomplete/incomparable/agree/conflict source lists; no rank/ID/store/text-style direction. |
| Actionability | target uniqueness; direction reversal reverses target only with valid evidence; unknown direction cannot destructively execute. |
| Grammar/executor | exact minimal leaf/effect vocabulary; v1 artifact behavior unchanged; illegal predicate/effect pairs fail parse/type check; annotation changes only conflict disposition; target action touches exactly the named target; no real-store adapter without a G5 token. |
| AST/SEQUENCE | closed keys/arities; connective canonicalization; nested sequence flattening/source order; identity removal; repeated-touch last disposition and full cost; REPLACE is historical only. |
| Audit/activity | every field audited; synthetic known-leak alarm; field permutation; all leaves active on required families; null cases do not fire. |
| Binding/replay | both sidecars and source comparisons serialized; cache-row/verdict/edge hashes recompute; exact case/runtime/item set; reject redaction, dangling, cross-case, duplicate, endpoint or source-list mismatch. |
| Evaluation | E0 exact candidate/case completeness; family CI; strict gain; candidate tie/missing/nonfinite STOP; synthesis proposal ledger/budget/winner freeze hash chain. |
| Query | atomic reservation before read; crash consumes read; one reservation only; winner/result/ledger hash chain; pre-freeze and retry rejection. |
| Deployment (G5) | structured canonical-JSON token golden vector; malformed/extra-field/issuer/key-ID/MAC/nonce/TTL/expiry/request mismatch; atomic SQLite nonce race; transaction snapshot, independent locality rollback, crash recovery, and append-only audit rows. |

## 9. Execution order and authorization

1. Freeze F0 split reservation.  Then read only pilot/`D_cal`/`D_dev`; develop
   relation, cache, ordering, audits, schemas, and power estimates.
2. Populate every F1 field with exact thresholds, hashes, budgets, model,
   source policy, grammar, baseline catalog, E0 envelope, and commands.  Run the
   validator; only its exact validated manifest hash is authoritative.
3. Run G0 and G1 against their registered calibration/development evidence.
   G1 actions remain offline sandbox simulations.
4. Only on G0/G1 GO, freeze per-case relation graphs, run G2 activity/type and
   shortcut audits, and execute the G3 fixed baselines.
5. Only when G0--G3 are GO may the registered fixed-budget, non-adaptive E0
   command open `D_search` and emit its single headroom decision.
6. Only E0 GO authorizes bounded adaptive synthesis.  It receives the frozen
   graph, cannot make model calls, and may not change F1 choices.  Freeze its
   winner before the single `D_query` confirmation read.
7. A separately authorized G5 deployment phase adds locality, rollback, use
   ledger, and production authorization tokens; it is not evidence for G0--E0.

## 10. Deployment gate (G5), deliberately downstream

For a proposed online repair, record the target, relation edge, ordering
evidence, program hash, before/after state, target probe outcome, and independent
neighborhood/query outcome in an append-only use ledger.  Apply transactionally:

```text
apply -> evaluate target + independent neighborhood -> commit
                                           \-> rollback on any failed criterion
```

The acceptance vector and locality tolerance are **TBD + freeze-before-use**.
The support signal that suggested repair cannot be its only acceptance signal.
The G5 gate service must verify the production authorization token against the
exact F1, program, graph, case, and item hashes before beginning the transaction;
tokens are single-use and a failed/expired/mismatched token cannot fall back to
offline G1 authority.

### 10.1 G5 authorization token and use ledger

Before token issuance, freeze `deployment/g5_policy.json` with exactly
`{schema_version,protocol_freeze_sha256,confirmation_result_sha256,
trusted_issuer_id,trust_key_ids,max_token_ttl_seconds,rollback_policy_path,
rollback_policy_sha256,neighborhood_policy_path,neighborhood_policy_sha256,
use_ledger_path,use_ledger_genesis_sha256,g5_policy_sha256}`.  Key IDs are a
sorted, unique, non-empty list; TTL is a positive integer; paths are
repository-relative; all referenced hashes are recomputed.  This G5 freeze is
separate from, and cannot amend, F1 measurement choices.

The token wire value is one closed structured JSON object, matching
`AuthorizationToken.to_mapping()` in `successor_deployment_guard.py`:

```json
{
  "schema_version": "successor-v3-g5-token-v1",
  "algorithm": "HMAC-SHA256",
  "issuer_id": "<trusted issuer>",
  "key_id": "<trusted key>",
  "issued_at": 0,
  "expires_at": 0,
  "nonce": "<128-bit lowercase hex>",
  "request": {
    "protocol_manifest_sha256": "<sha256>",
    "program_sha256": "<sha256>",
    "graph_sha256": "<sha256>",
    "case_id": "<case>",
    "runtime_case_sha256": "<sha256>",
    "target_item_id": "<G1 target>",
    "rollback_policy_sha256": "<sha256>"
  },
  "mac": "<64 lowercase hex>"
}
```

`issued_at`/`expires_at` are Unix epoch-second integers; `nonce` is 32 lowercase
hex characters. The signing mapping is the object without `mac`, including the
closed nested request. Signing bytes are exactly Python-compatible
`json.dumps(mapping,ensure_ascii=False,sort_keys=True,separators=(",",":"))`
UTF-8 bytes. `mac` is the lowercase hex digest of HMAC-SHA256 over those bytes.
The secret is at least 32 random bytes in a trust store outside the repository,
indexed by the G5-allowlisted `(issuer_id,key_id)`; the token contains no key
material. Verification uses constant-time comparison and requires exact schema,
algorithm, issuer/key, request equality, `issued_at <= now < expires_at`, and
`expires_at-issued_at <= max_token_ttl_seconds`. Extra fields, noncanonical
types, bad hashes/nonces, unknown/revoked keys, or clock failure reject. HMAC
authenticates authorization; it does not hide the token.

Before the first store write, `DeploymentUseLedger.reserve_nonce()` performs an
atomic SQLite insert into
`g5_nonce_reservations(nonce TEXT PRIMARY KEY,reserved_at INTEGER)`. Reuse of a
reserved nonce is rejected across processes. The append-only audit table is
`g5_use_ledger(ledger_id INTEGER PRIMARY KEY AUTOINCREMENT,nonce,request_json,
committed,reason,before_json,after_json,target_json,neighborhood_json,
rollback_performed)`. JSON columns use the same canonical serializer; no UPDATE
or DELETE operation is exposed. Invalid authorization is audited; a verified
token's nonce is durably reserved before snapshot/apply. Commit requires both
the frozen target conditions and an independently identified neighborhood probe;
otherwise rollback and its snapshots/outcomes are recorded.

The current structured-JSON/HMAC/SQLite guard is a **G5 skeleton only**: it ships
no real production-store adapter. Tests already enforce 128-bit nonce shape,
minimum key length, maximum TTL, issuer/key identity, exact request binding,
cross-process single use, and transactional rollback. It becomes
G5-authoritative only after G5-policy/confirmation binding at issuance, exact
graph-edge target membership, and a durable real adapter proving crash recovery
after nonce reservation. Until then it cannot issue a real-mutation
authorization despite passing unit tests with a fake store.

## 11. Claims and withdrawal criteria

### May claim, only with the corresponding evidence

- After G0: calibrated text-only detection of same-slot/different-value
  relations on the frozen measured distribution, with stated abstention scope.
- After G1: offline identification of a destructive-action target under the
  audited deployment-visible evidence policy, not permission for real mutation.
- After E0 GO: fixed-envelope compositional headroom over the strongest
  registered fixed baseline; not yet successful evolution.
- After adaptive synthesis meets the F1-frozen rule on the single held-out
  `D_query` confirmation: residual value of the registered v3 evolution
  procedure over the strongest registered fixed baselines.
- After G5: rollback-guarded behavior under the evaluated locality protocol.

### May not claim

- That relation detection establishes chronological supersession or truth.
- That a style cue, timestamp-shaped field, or benchmark construction is
  deployment evidence.
- That v1/v2 was repaired, invalidated, or rerun by this work.
- That G1 or E0 GO authorizes mutation of a real memory store.
- That any result transfers outside named data, model, evidence, and deployment
  scope; or that synthesis is necessary if a fixed policy reaches the ceiling.

Withdraw this protocol (rather than patching an observed outcome) if any
predeclared falsifier occurs: metadata/identity leakage into G0; style
permutation collapse; canary failure below the frozen floor; non-deployment or
shortcut ordering evidence; an unknown/conflicting direction producing a
destructive action; treating `source_priority` as chronology; graph/case/version
drift or non-replayable edge evidence; a broken F0/proposal hash chain or
query/G5 ledger integrity failure;
a real mutation without a valid G5 token or with a reused nonce; any pre-F1
`D_search`/`D_query` outcome read; a pre-winner/multiple query reservation; an
unauthorized read or rerun; adaptive behavior inside E0; winner replacement;
or a baseline/headroom claim that omits the strongest legal fixed policy.
Preserve the code and results, write a withdrawal record, and do not reuse the
invalid measurement as successor evidence.

## 12. Resource budget and stop conditions

The resource plan is intentionally a budget envelope, not a performance claim:

- **Human annotation:** pilot, calibration, adjudication, and canary counts are
  TBD during F0 and must be populated from the blinded power/design review at
  F1; no arbitrary “100–200” substitute is a valid gate.
- **Model calls:** one cached call per unique unordered text pair/version in G0;
  zero model calls during G1 resolver evaluation, G2/G3/E0 policy runs, and
  confirmation.  Record pair count and cost in the cache manifest.
- **Compute:** freeze maximum pair count, retries, failure rate, number of
  baseline policies, grammar envelope, seeds, and confirmation reads before
  `D_search`; exhaustion stops the protocol rather than expanding the budget.
- **Time:** allocate a distinct review window after each gate.  A missed audit,
  unavailable trustworthy ordering source, or unpowered calibration is STOP/
  scope reduction, not permission to use a proxy.

## 13. Compatibility record

This document writes no code and changes no predecessor artifact.  In
particular, `route-a-state-fitness-open-synthesis-v1` remains frozen with its E0
STOP, and `route-a-ir-v2-slot` remains withdrawn for the demonstrated
`store`/`memory_id` construction-marker leakage.  V3 numbers, if ever produced,
must carry their own protocol ID and must never be substituted into predecessor
tables.
