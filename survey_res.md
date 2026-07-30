# Implementation-Traceable Survey — Memory Repair Ecologies

## Scope Boundary

This artifact is a compact implementation bridge derived from `survey_report.md` and the downloaded paper sources. It is not a new exhaustive search and does not prove novelty.

In scope:

- LLM-agent memory extraction/management, test-time evolution and failure attribution;
- population evolution and quality-diversity mechanisms transferable to typed repair operators;
- empirical gold-free/supervised agreement and abstention;
- adjacent unsupervised skill specialization/composition.

Out of scope:

- claiming all prior memory systems are fixed single rules;
- claiming ordinary mutation/crossover is novel;
- calling a post-hoc margin curve conformal risk control;
- treating enumerated two-step search as emergent ecological chaining;
- importing reference repositories directly into CMD.

## Core Method Comparison

| Method | Evolution/optimization unit | Signal/supervision | Interaction topology | Constraint on CMD |
|---|---|---|---|---|
| MemTrace (`2605.28732`) | operation-graph attribution and attribution-guided prompt optimization | benchmark faulty-operation/error annotations plus model-based attribution | one attribution/optimization pipeline | CMD must prove structural no-gold runtime provenance and compare on downstream repair, not misdescribe MemTrace as fixed rules |
| UMEM (`2602.10652`) | one learned Mem-Optimizer jointly extracting/managing memories | semantic-neighborhood marginal utility, GRPO | single learned policy | Darwinian population must be compared fairly against a strong single-policy optimizer and disclose compute |
| Evo-Memory/ReMem (`2511.20857`) | streaming memory state and Think–Act–Refine loop | task feedback in sequential streams | one refining agent pipeline | use prequential evaluate-then-update semantics; do not let producing cases validate themselves |
| A-MEM (`2502.12110`) | dynamic linked memory organization | LLM-driven note/link updates | single agentic memory organization | “memory evolution” is prior terminology; CMD differentiates by typed repair population/competition |
| MAP-Elites (`1504.04909`) | archive of elites over behavior descriptors | fitness within descriptor cells | population with niche-local competition | plain global truncation does not preserve ecology; implement/report a niche-elite selection mode |
| Promptbreeder (`2309.16797`) | populations of task and mutation prompts | task-set fitness | mutation and selection | record invalid/no-op rate, genotype hashes, seeds and genealogy; evolution alone is not novelty |
| Conformal Risk Control (`2208.02814`) | calibrated threshold for monotone loss | labeled calibration data under assumptions | post-hoc risk calibration | CMD’s current output is empirical agreement-under-abstention unless independent calibration and assumptions are added |
| DIAYN (`1802.06070`) | latent policy skills | mutual-information/maximum-entropy objective, no downstream reward | diverse skill repertoire | adjacent evidence only; it does not validate memory-repair competition or chain deposition |

## Key Formulas and Metrics

### Repair selection

```text
Δ_gf(c,s)   = gold-free recovery/surrogate change for skill s on case c
Δ_gold(c,s) = shadow gold-supervised score change; analysis only
s_gf        = argmax_s Δ_gf(c,s)
s_gold      = argmax_s Δ_gold(c,s)
agreement   = mean_c 1[s_gf = s_gold]
margin      = best(Δ_gf) - second_best(Δ_gf)
regret      = max_s Δ_gold(c,s) - Δ_gold(c,s_gf)
```

Ties and missing/nonfinite scores must be explicit. Agreement after thresholding is reported with coverage:

```text
coverage(τ) = P(margin >= τ)
selective_agreement(τ) = P(s_gf=s_gold | margin>=τ)
```

No guarantee follows without an independent calibration protocol.

### Skill ecology

```text
win_rate(s,f) = wins(s,f) / attempts(s,f)
specialization(s) = 1 - H(normalize_f win_rate(s,f)) / log(number of supported niches)
overlap(a,b) = cosine(win_rate_vector_a, win_rate_vector_b)
winner_diversity = H(normalized total winner counts)
stability_t = JSD(winner_distribution_t, winner_distribution_(t-1))
```

Family-blocked uncertainty and a label-permutation null are required for paper claims; raw heatmap diagonality is descriptive only.

### Darwinian evolution

```text
genotype = canonical_json(OperatorSpec)
fitness = aggregate finite recovery gains under a frozen case budget
global selection = top capacity by fitness, cost, lexical ID
niche elite = best genotype per descriptor cell, then global fill
```

Selection must exclude invalid/nonfinite offspring and preserve immutable parents/genealogy.

### Chaining

```text
chain_gain(A→B) = Δ(context produced by executing B on A(base_context))
                  - max(Δ(A), Δ(B))
```

Directionality requires evaluating both `A→B` and `B→A` from the same base snapshot. Concatenating DSL steps without evaluating the intermediate output is not evidence of chaining.

## Concrete Constraints on This Repository

1. `cmd_audit/` remains stdlib-only.
2. Continue using canonical OperatorSpec hashing and append-only records in `operator_library.py`.
3. Keep `EvolutionCoordinator` as the Lamarckian/versioning layer; put Darwinian population logic in a separate module/API.
4. Runtime repair selection receives no `case.gold_*`; shadow gold enters only the identifiability analyzer.
5. Use local seeded RNG and lexical tie-breakers.
6. Maintain Exp24 effective-after boundaries and per-arm stores.
7. No-update must execute symmetrically but commit nothing.
8. Do not weaken existing promotion, anchor, safety or within-family gates.
9. Full recovery/specialization claims require multi-seed family-blocked experiments; fixture/smoke values are implementation checks only.
10. UMEM code was not verified as public at collection time; no unofficial implementation is treated as the baseline.

## Source Locations

- `papers/memory_evolution/2605.28732/` — MemTrace
- `papers/memory_evolution/2602.10652/` — UMEM
- `papers/memory_evolution/2511.20857/` — Evo-Memory/ReMem
- `papers/memory_evolution/2502.12110/` — A-MEM
- `papers/quality_diversity/1504.04909/` — MAP-Elites
- `papers/quality_diversity/2309.16797/` — Promptbreeder
- `papers/selective_risk/2208.02814/` — Conformal Risk Control
- `papers/skill_specialization/1802.06070/` — DIAYN
- `repos/MemTrace/` — author repository, shallow-cloned at collection time

