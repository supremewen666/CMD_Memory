# CMD V1→V2 Gate Status

**Last checked:** 2026-05-21
**Gate ID:** V1→V2
**Status:** PASS — both mem0 and Letta adapters integrated. 684 tests pass. Issue 0019 Phase A complete (LLM-as-Judge baseline). Issue 0019 Phase B complete (Subagent-based LLM scoring). Issue 0018 Pre-CMD Hook design finalized — single `post_retrieve_hook`, zero-gold online gate, PrefixGuard (4 signals, logistic regression) + RPE (BM25), offline 6-step calibration on 596 cases. Design doc: `../issues/0018-pre-cmd-hook-design.md`. Phase B detail map: `../issues/0019-phase-b-subagent-scoring-implementation-details.md`.

## Gate Criteria

V1→V2 requires at least two distinct memory agents integrated through the Adapter Interface without macro F1 regression (per PRD AC14 and issue 0010).

### Criterion 1: Adapter Integration Count >= 2

- **Artifact:** `cmd_audit/adapters/` (mem0_adapter.py, letta_adapter.py)
- **Threshold:** `adapter_count >= 2 AND no macro F1 regression`
- **Result:** PASS
- **Evidence:** 2 adapter integration(s): mem0 (Issue 0014), Letta (Issue 0015).
- **Detail:** `Mem0Adapter` with `intercept_add`/`intercept_search` two-cut-point interception. `LettaAdapter` with `intercept_core_write`/`intercept_archival_store`/`intercept_recall` three-cut-point interception. Both use recorded-trace mode with sandbox checksum verification.

## Per-Agent Metrics

| Agent | Adapter | Macro F1 (V0 smoke) | Test Count | Adapter-Label Parity |
|-------|---------|---------------------|------------|---------------------|
| mem0 | `Mem0Adapter` | 1.000 | 30 tests | ✅ All 6 labels match standalone |
| Letta | `LettaAdapter` | 1.000 | 44 tests | ✅ All 6 labels match standalone |

> **2026-05-23 grilling caveat**: The per-adapter Macro F1 = 1.000 above was measured under V0 6-case smoke suite with the phrase-match shortcut active. See `V0V1_gate_status.md` § "2026-05-23 grilling caveat" for the at-scale 596-case re-test plan. Adapter-label parity is structurally still expected to hold under the LLM stack (the adapters' 10-replay portfolios are deterministic given identical evidence_block content and the scoring layer is shared with standalone harness), but the parity number must be re-measured on the adjudicated set + 596 sanity check before paper publication. Issue 0032 covers the parity-under-LLM-stack regression test.

## Cross-Agent Non-Regression

- mem0 results unchanged after Letta adapter addition (no cross-contamination). ✅
- Both agents independently produce identical labels to standalone harness. ✅
- Recovery gains match standalone for both agents independently. ✅
- mem0 and Letta Macro F1 are independently 1.000. ✅

## Agent Details

### mem0 (Issue 0014)

- **System:** mem0ai/mem0 (55k GitHub stars)
- **Memory model:** Flat store (`add()` → `search()`)
- **Interception:** Two cut points (write-side: `intercept_add`, retrieval-side: `intercept_search`)
- **Replays:** 10-replay portfolio (6 intercepted + 4 V1 passthrough)
- **Sandbox:** SHA-256 checksum over `sorted(add_inputs)`
- **`route_error` applicability:** N/A (flat store — Oracle Route always returns zero gain)
- **Detail map:** `../issues/0014-integrate-mem0-adapter-implementation-details.md`

### Letta (Issue 0015)

- **System:** letta-ai/letta (22.6k GitHub stars)
- **Memory model:** Tripartite (core working memory + archival long-term store + recall retrieval)
- **Interception:** Three cut points (write-side: `intercept_core_write` + `intercept_archival_store`, retrieval-side: `intercept_recall`)
- **Replays:** 10-replay portfolio (6 intercepted + 4 V1 passthrough)
- **Sandbox:** SHA-256 checksum over `sorted(core_blocks + archival_blocks)`
- **`route_error` applicability:** Structurally supported (core vs archival vs recall tier selection)
- **Detail map:** `../issues/0015-letta-adapter-implementation-details.md`

## Sandbox Guarantees

Both adapters enforce store immutability through SHA-256 checksum verification:

| Adapter | Checksum Domain | Violation Error |
|---------|----------------|-----------------|
| `Mem0Adapter` | `sorted(add_inputs)` | `SandboxViolationError` |
| `LettaAdapter` | `sorted(core_blocks + archival_blocks)` | `SandboxViolationError` |

After any replay portfolio execution, `adapter.verify_sandbox()` confirms the store checksum matches the pre-replay value. Any mutation is a hard error.

## Gate Check Implementation

`check_v1_to_v2_gate(*, mem0_integrated=False, letta_integrated=False)` in `cmd_audit/version_gates.py:114-165`:

```python
adapter_count = (1 if mem0_integrated else 0) + (1 if letta_integrated else 0)
passed = adapter_count >= 2
```

Gate behavior:
- `mem0_integrated=True, letta_integrated=True` → `adapter_count=2` → **PASS**
- `mem0_integrated=True, letta_integrated=False` → `adapter_count=1` → FAIL ("Integrate second adapter target")
- `mem0_integrated=False, letta_integrated=True` → `adapter_count=1` → FAIL
- `mem0_integrated=False, letta_integrated=False` → `adapter_count=0` → FAIL ("V0 operates as standalone harness")

## Missing Evidence (for scaling beyond smoke)

1. **Macro F1 at 1.000 on 6 smoke cases** is not a credible paper claim for adapter-label parity at scale. Need 50-100+ cases with realistic distributions.
2. **`route_error` on Letta** is structurally supported but not exercised by current V0 smoke cases (no smoke case triggers tier miss). 596-case expansion should include route-error cases for Letta.
3. **Cross-agent non-regression** is verified on 6 smoke cases only. Large-scale verification on 596 cases is needed.
4. **Real Letta integration** (live Letta instance, not recorded traces) is V2 scope. V1 recorded-trace mode validates the interception semantics but not the live integration path.

## HITL Review Log

| Date | Reviewer | Decision | Notes |
|------|----------|----------|-------|
| 2026-05-19 | — | pending | Gate criteria technically pass (2 agents integrated, adapter-label parity confirmed, no macro F1 regression). HITL review pending. |
| 2026-05-20 | — | pending | 535 tests pass (+82). RPE prefilter (275 lines), provenance (163 lines), PrefixGuard (93 lines) modules exist as untracked code. Issue numbering drift: on-disk 0016=real_data, 0017=RPE+provenance (two files), no 0018. Gate technically passes but scaling evidence (596 cases) still missing. |
| 2026-05-21 | — | pending | 684 tests pass. Issue 0019 Phase A complete (LLM-as-Judge baseline: `llm_client.py` + `llm_judge.py`, 32 tests). Issue 0019 Phase B complete (Subagent scoring: `llm_scoring.py` — EvidenceVerifier + AnswerVerifier + SubagentScorer, `replays.py` scorer param, 39 tests). `subagent_runner.py` + `hooks.py` merged into `llm_scoring.py`. Detail map: `../issues/0019-phase-b-subagent-scoring-implementation-details.md`. Issue 0018 Pre-CMD Hook design finalized. |
| 2026-05-23 | grilling-with-docs | pending re-test | Decision 34 R1+R4: parity number above is phrase-match shortcut, must be re-measured under LLM stack on 130 researcher-adjudicated + 596 sanity check before paper. |
| 2026-05-24 | grilling-with-docs | pending re-test V1.0 + V1.1 | Decision 34 R9+R10: parity numbers regenerate twice — V1.0 (596 corpus) and V1.1 (full corpus post-0035). |

## V2 Integration Plan

- **Real-time adapter integration:** Live mem0/Letta instances instead of recorded traces.
- **`route_error` at scale:** Exercise Letta's tier routing on real multi-tier cases.
- **Third agent:** Potential third adapter target for stronger V1→V2 gate evidence.
- **RPE prefilter across adapters:** Train scorer on adapter-agnostic evidence quality; validate across both agents.
- **Provenance tracking:** Execution Lineage DAG per MemoryItem; cascade repair via MemQ TD(λ).
