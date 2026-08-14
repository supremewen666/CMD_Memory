# Experiment Analysis Round 1 — GHOST Router V1 Reviewed Replay

## Evidence boundary

This round analyzes a local, zero-model-call replay over previously materialized
shadow-gold outcomes. It is a **shadow-gold development screen**, not a sealed,
prospective, live, or end-to-end gold-free result. Runtime selection remains
gold-free, but the selected-action feedback used for updating is marked
`previously_materialized_shadow_gold_answer_score` and `gold_derived=true`.

The review prerequisite was satisfied before execution:
`iterations/judge_v1.md` records `verdict: PASS`.

## Frozen protocol and execution

- Closed case stream:
  `artifacts/neuro_symbolic_evolution_v4/neuro_symbolic_evolution_v4/runs/v7-001/cases.merged.jsonl`
- Cases: 3,100; candidate budget: 4.
- Represented: 2,484 cases / 640 families.
- Unseen: 616 cases / 156 families.
- Seeds: 24, 25, 26, 27, 28.
- Family bootstrap samples: 10,000 per seed.
- Registered replay arms: 16, including every current GHOST ablation.
- Rows per seed: 49,600 = 3,100 cases × 16 arms.
- Model/API/network calls: 0.
- Claim scope serialized by every report:
  `selector_screen_not_end_to_end_gold_free`.

The closed stream's canonical case hash is
`51d18796abfb8f1eeaecbbe2d621854a6755ee018132f16364d1b9b9a31ce8aa`.
This equals the hash in the earlier reviewed reports, so the closed serialization
is the same 3,100-case stream. The reference outcome file SHA-256 is
`bb2a0acec091fa3acce39f549da009d5b82d3e06fc770ada905a1f7035d0dd29`,
also identical to the earlier reviewed binding.

## Current result summary

The primary `ghost` arm and the explicitly named `ghost_hierarchy_v1` arm are
byte-for-byte behaviorally equivalent in the aggregate metrics.

| Arm | Mean utility | Represented case mean | Unseen case mean | Harm rate | Represented family-macro Δ vs Full | Worst-seed one-sided 95% LB vs Full |
|---|---:|---:|---:|---:|---:|---:|
| GHOST hierarchy | 0.2350165790 | 0.2450875373 | 0.1944057665 | 0.1619354839 | +0.0459260825 | +0.0319071483 |
| Full V4 | 0.1845540324 | 0.1861170794 | 0.1782510959 | 0.2135483871 | — | — |
| Diagonal Thompson | 0.2099331831 | 0.2205754094 | 0.1670187512 | 0.1824516129 | +0.0335345019 | +0.0229068812 |
| Online linear SGD | 0.2079437598 | 0.2181471629 | 0.1667988681 | 0.1770967742 | +0.0340609481 | +0.0257719705 |

GHOST's overall mean is 27.3449% above Full V4. Its harm rate is 5.1613
percentage points lower than Full V4. These are descriptive development-screen
comparisons over the accessed stream.

## Required ablations and registered controls

| Arm | Mean utility | Represented family-macro Δ vs Full | Unseen case mean | Harm rate | Reading |
|---|---:|---:|---:|---:|---|
| `ghost_global_v1` | 0.2357088692 | +0.0445981047 | 0.1946168937 | 0.1578709677 | Flat global ablation is slightly higher on raw mean; hierarchy is not claimed superior on this development stream. |
| `ghost_hierarchy_v1` | 0.2350165790 | +0.0459260825 | 0.1944057665 | 0.1619354839 | Primary recursive hierarchy. |
| `ghost_no_semantic_level` | 0.2345246272 | +0.0452438828 | 0.1943783896 | 0.1621290323 | Removing semantic shrinkage causes a small raw decrease. |
| `ghost_no_signal_level` | 0.2356452636 | +0.0447203226 | 0.1944187793 | 0.1578064516 | No-signal variant is slightly higher on raw mean; no hierarchy-component superiority claim is supported. |
| `ghost_no_typed_motif` | 0.2227517775 | +0.0393408476 | 0.1806205043 | 0.1704516129 | Typed motif removal materially reduces utility. |
| `ghost_shuffled_feedback` | 0.1215968143 | -0.0174701005 | 0.0728207676 | 0.2481935484 | Destroying feedback chronology strongly degrades utility and safety. |
| `exp3_motif` | 0.1742806117 | +0.0169340572 | 0.1286827160 | 0.2084516129 | Registered motif bandit control. |
| `linucb` | 0.1227581355 | -0.0708595842 | 0.0694064873 | 0.2129032258 | Registered linear-UCB control. |
| `periodic_reset_100` | 0.1686406336 | +0.0077288884 | 0.0903056103 | 0.2122580645 | Registered forgetting/reset control. |
| `lagged_shuffled_feedback` | 0.1066135417 | -0.0317799462 | 0.0558805070 | 0.2718709677 | Registered generic shuffled-feedback control. |
| `online_global_replay` | 0.1310175461 | -0.0241801159 | 0.0861599863 | 0.2541935484 | Reproduces the frozen global reference as required. |
| `no_update_lexical` | 0.1406318190 | -0.0259352148 | 0.0907658666 | 0.2329032258 | No-update control. |
| `deposition_off` | 0.1406318190 | -0.0259352148 | 0.0907658666 | 0.2329032258 | Exactly matches no-update after arm-ID normalization. |

## Integrity and hash audit

Every report's embedded `report_sha256` matched an independent canonical payload
recomputation. Every arm contains exactly 3,100 unique case IDs.

| Seed | Embedded report SHA-256 | Actual report file SHA-256 | Actual arm rows file SHA-256 |
|---:|---|---|---|
| 24 | `bce37438c5a95e60ad1da737fc56a7f53b0e93c986fe4743241771adc4ba3b94` | `c7dc513a33804287001e4c72ce2fe64988c310697b790c63aed6c244e7bbd01b` | `caef556428bc65ff76b051214724a2a13d8f9056b2e98952b8c41f575c6cc01a` |
| 25 | `0714c602f9223d009e5b752bfb02c2413e19267762ac1be8fd3fc5e150db3435` | `671b184e2a816d4f2f6a70aa4ed1510e9d4ef4b9bc80009c6aaf8d6943e2efef` | `9ab8d3e234685cb6eba2f3e37101de6f545b642ddfa34a2cda29b8031b516fa8` |
| 26 | `b28f14e8d869fcb1694435c7bffd49d0142b880647ee1e2e9f432e35ddc4af31` | `4a15cbc515e78e5899c5e05af795ec6f6cd121f70f78fd7e22792348bd30ed3f` | `ef886eb4cf670c847c954d9ff9e99e694a3fb361523a36e3969a98140dd21153` |
| 27 | `744eb8d6c91e18bf0b2800bed08921dd14ab1789439455510b4128a269990999` | `0fb22d61355d7c9dc8905f7295840951aaf223011784af596798e08fef4901b3` | `9a892435d11d40d1f4d5b4823943048cddec22057904966b599ebd2dde66fe08` |
| 28 | `5b0e86ce3a6098b8c28d41fcc495421e97af2def729d715d3fef520ff5cbf7e6` | `af0b9445752c48d40d0e25624a692b44ece9b5f74022d7cba968718c93a3f51d` | `17c47bf03aa9f5bd3797ee22a821276459f651463bcb4eb918c7303a03bcf0ca` |

The multiseed embedded canonical `summary_sha256` is
`aa119b3c812dea18e19571b9869afb2ee5ad3a1ed8e88f5c29cf34980429c0f8`;
independent recomputation matched. The actual summary file SHA-256 is
`20d457fb1b75f236edbe52b5035836eae5d25f8d440bfe9edba2d0dde7a87185`.

## Findings and supplementary experiment plan

1. The report-native comparison establishes a positive GHOST-vs-Full family
   effect, but the build gate also names diagonal Thompson and online linear SGD.
   A raw-row paired family recomputation is needed against all three baselines.
2. The summary carries unseen case means but not the registered two-sided family
   bootstrap lower bound for the `-0.005` non-inferiority margin. Recompute it from
   raw rows with families as units.
3. The arm aggregate omits CVaR. Recompute per-seed and multiseed CVaR from raw
   rows and compare against the frozen Full V4 threshold.
4. Because global and no-signal ablations have slightly higher raw means, keep the
   conclusion bounded: the experiment screens the frozen hierarchy successfully,
   but does not establish that every hierarchy component is individually optimal.

| Supplementary analysis | Purpose | Modification | Expected output |
|---|---|---|---|
| Paired represented family bootstrap | Test all three registered baselines | Analysis only; no router change | Family-macro deltas and 10,000-draw lower bounds |
| Unseen non-inferiority bootstrap | Apply registered `delta_unseen=0.005` | Analysis only; no router change | Two-sided 95% lower bound |
| Raw-row CVaR audit | Complete safety gate | Analysis only; no router change | `CVaR_0.05` comparison |
