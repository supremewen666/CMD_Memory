# Route A successor protocol: slot-divergence sensor — **WITHDRAWN**

Protocol ID: `route-a-successor-slot-sensor-v1`
Predecessor: `route-a-state-fitness-open-synthesis-v1` (frozen, STOP at E0)
Status: **WITHDRAWN before freeze. No run was authorized and none took place.**

Withdrawn by its own §7 falsification criterion, which was written before the
measurement that triggered it. The draft's §8 said: *"if it resolves against the
sensor, this protocol is withdrawn rather than amended."* It resolved against
the sensor.

---

## 1. What was built

`cmd_audit/counterfactual/slot_divergence.py` (`route-a-slot-divergence-v1`) and
`cmd_audit/counterfactual/successor_grammar.py` (`route-a-ir-v2-slot`). Both are
kept in the tree with their tests: the code is not the error, and the delegation
design in §4 below is reusable by any successor sensor. What is retracted is the
claim that this sensor measures staleness.

## 2. Why it was proposed

Route A stopped at E0 under §6.3 with a headroom of exactly 0.0000. Measurement
showed that was not "the legacy operators are optimal" — `stale_item` scored
0/23760 arm-case cells because `CONTRADICTS` cannot see the relation at all. It
gates on negation polarity before measuring overlap, and 75.8% of the 3600 item
pairs are same-polarity, the gold pair among them. Of the rest, every pair falls
at or below the 0.3 floor (max Jaccard 0.2326). `CONTRADICTS` models *same
content, opposite polarity*; staleness is *same slot, different value*. Different
relations, and no threshold converts one into the other.

**That diagnosis stands.** It is measured, it is reproducible, and it is
unaffected by this withdrawal. `CONTRADICTS` is still dead and `stale_item` is
still invisible to the frozen instrument.

## 3. Why it was withdrawn

The draft's §7 named three falsifiers. The third was:

> **The timestamp is an artifact of dataset construction.** `store` carrying an
> ISO timestamp with the stale item always earlier may be a property of how
> `stale_item` was built rather than of deployed memory stores. If so, the sensor
> is reading a construction marker one level less obvious than the `M_old:`
> prefix — the same failure this protocol claims to avoid. **This is the most
> serious open risk and it is not resolved by anything measured so far.**

It is now measured. The risk was real.

### 3.1 `store` is a construction marker

Across all 1200 `stale_item` cases, `store` takes exactly three values and they
stand in bijection with the three `memory_id`s:

| `memory_id` | `store` | count |
|---|---|---|
| `m_stale` | `2026-01-01T00:00:00Z` | 1200 (constant) |
| `m_current` | `2026-01-03T00:00:00Z` | 600 |
| `m_current` | `2026-02-01T00:00:00Z` | 600 |
| `m_haystack` | `haystack` | 1200 |

`m_stale`'s timestamp is a single constant. Reading `store` and reading
`memory_id` therefore carry identical information on this dataset — which makes
the sensor an instance of the shortcut its own tests forbid, entering through a
different field name.

### 3.2 The decoupling counterfactual

Replacing each item's `store` with a timestamp derived from its *text* rather
than its identity — still ISO, still parseable, still pairwise distinct, only no
longer aligned with construction identity:

| | as shipped | `store` decoupled from `memory_id` |
|---|---|---|
| fires | 1200/1200 (100.0%) | 1199/1200 (99.9%) |
| **exact gold pair** | **1200 (100.0%)** | **0 (0.0%)** |
| **haystack false positives** | **0 (0.0%)** | **1199 (99.9%)** |

All discriminative power came from the bijection. The haystack item was excluded
because its field happens to contain the unparseable literal `"haystack"`, not
because it lacks temporal evidence. Remove the alignment and the sensor
implicates the filler in 99.9% of cases and the true pair in none.

### 3.3 It does not transfer

Coverage on the other shipped datasets, same sensor, unchanged:

| dataset | items | parseable `store` | sensor fires |
|---|---|---|---|
| `stale_item_cases` | 3600 | 66.7% | 1200/1200 |
| `real_multihop_cases` | 600 | **0.0%** (all `episodic`) | **0/240** |
| `real_recurrent_cases` | 1500 | **0.0%** (all `episodic`) | **0/600** |
| `real_three_source_cases` | 375 | 17.6% | 33/150 |
| `real_longmemeval_cases` | 125 | 17.6% | 11/50 |
| `real_memoryarena_cases` | 125 | 17.6% | 11/50 |

Zero on both datasets whose `store` field carries provenance rather than a
timestamp — which is what the field is nominally for.

## 4. What survives

Four things, all independent of the withdrawn claim:

1. **The `CONTRADICTS` diagnosis** (§2). Measured, reproducible, unaffected.
2. **The delegation design.** `route-a-ir-v2-slot` adds a leaf to a frozen
   grammar without editing it, resting on the structural fact that v1's
   `_match_predicate` recurses only through connectives so a leaf-only handoff
   can never deliver a v2 kind to v1 code. Any future successor sensor needs this
   and it is tested (mutation sweep 15/15).
3. **The falsification discipline.** §7 was written before the measurement and it
   fired. That is the mechanism working, not failing.
4. **A correction to the frozen module, still true.** `_temporally_dominated`
   says "a `RuntimeMemoryItem` carries no timestamp." A timestamp-shaped value
   does sit in `store` — but per §3.1 it is a construction marker on the one
   dataset that has it and absent elsewhere, so the frozen comment is closer to
   right than the draft credited. It is not edited either way; it is inside the
   freeze.

## 5. What this closes off

The instrument-layer repair does **not** have a cheap version. A sensor that
sees same-slot supersession has to read the *values being asserted*, which is
the semantic comparison the original plan called for (NLI or embedding), not a
metadata field. The temporal shortcut looked like it made that unnecessary; it
did not.

Specifically retired:

- Any claim that `stale_item` has measurable headroom under a metadata-only
  sensor. The 1200/1200 number is void.
- Any re-run of E0 under `route-a-ir-v2-slot` with `SLOT_DIVERGES` as
  implemented. The grammar is sound; the leaf behind it is not.

Still open, and now known to be the only route: a same-slot detector reading item
*text* on both sides. That needs a model call per pair, which collides with the
zero-LLM-call property the sensor was built to preserve — and that collision is
the real finding here, not the withdrawn coverage number.

## 6. Record

E0's STOP under `route-a-state-fitness-open-synthesis-v1` stands unchanged.
Nothing under the predecessor was re-run, rewritten, or reinterpreted.
`state_executor.py` and `IR_GRAMMAR_VERSION = "route-a-ir-v1"` were never edited.
No preregistration manifest was frozen for this protocol, so there is no frozen
artifact to retract — only this document, kept in the tree as the record of a
sensor that measured its own scaffolding.
