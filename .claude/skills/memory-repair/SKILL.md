---
name: memory-repair
description: >
  Audit and repair the project's auto-memory store (stale / conflicting /
  duplicated memory entries) using CMD item-layer repair operators with an
  accept-if-improves gate. Use when memory entries look outdated or
  contradictory, when the memory index has grown past ~40 entries, or
  around a context compaction.
---

# Memory Repair (CMD skill mode)

Store-level memory GC using CMD item-layer operators. Dry-run by default.

> Status (2026-07-28): the `repair-store` subcommand, memory-dir loader,
> bounded bucketing, deterministic freshness arbitration, retention gate,
> and snapshot/rollback path are implemented. Hooks remain advisory and must
> invoke dry-run only.

## How It Works

1. Dry-run audit (always first):

   cmd-audit repair-store "$MEMORY_DIR" --mode dry-run

2. Read the report. Per fingerprint bucket it lists: detected collisions
   (stale / conflict, from pairwise divergence + timestamps), the proposed
   item operator per entry (demote / de-conflict / merge / arbitrate-freshness),
   and the gate verdict (failure-memory probe-replay delta, or retention
     surrogate when no probes exist for that bucket). The deterministic
     freshness path currently uses the retention surrogate.
3. Present proposed repairs to the user. NEVER apply without confirmation.
4. On approval, re-run with --mode apply. Apply snapshots the memory dir
   first; a failed gate auto-rolls-back and reports why.

## Completion Output Contract

Every `/memory-repair` run (dry-run or apply) MUST end by presenting:

1. Report path (`$MEMORY_DIR/.cmd/repair-report.json`)
2. Per-bucket gate verdict: passed / failed / dry-run-only
3. Store checksum before and after (identical unless apply passed the gate)
4. Gate type for every applied bucket. A probe-replay delta is required when
   a probe callback is configured; deterministic freshness reports the
   retention-surrogate verdict instead.

Evidence comes from the gate, never from self-assessment of repair quality.

## Boundaries

- Item-layer only (stale / conflict / duplicate). No step-layer
  counterfactual: there is no failing query trace at repair time.
- Gold-free: operators read item text / metadata only; the default retention
  gate verifies that the newest item remains and demoted items leave the live
  store. A configured probe gate must use stored online probes, never gold.
- Self-consistency is NOT an acceptance gate (Exp20): never accept a
  repair because the model judges its own repair helpful.
- Entries without a passed gate are reported, not touched. If neither
  probes nor a usable surrogate exist for a bucket, that bucket stays
  dry-run-only.
- This skill does not carry the evolution claim (Exp23b: item-layer
  library transfer ≈ random, p=0.33 ns). The sell is "single-point item
  repair works (stale 0.732 / conflict 0.568) + gated safety".
