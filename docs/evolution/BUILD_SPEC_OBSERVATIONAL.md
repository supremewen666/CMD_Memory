# BUILD SPEC — Observational Evolution Line (memtrace family-keyed)

Status: normative build order for the observational refactor.
Authority: this file for the items below; `docs/evolution/IMPLEMENTATION_CONTRACT.md` for
everything it already freezes. Where they disagree, the CONTRACT wins except where this
spec explicitly says "amends CONTRACT §N".

Iron rules (non-negotiable):

- `cmd_audit/` stays stdlib-only. No PyPI imports, not even guarded `try: import`.
- Construction of repaired context stays gold-free: a pure function of
  `(recall_set, pipeline_action)`. It must never read `case.gold_*`. Scoring may.
- Append-only stores stay append-only. No in-place mutation of committed revisions.
- Fail closed. A missing precondition raises, never silently degrades.

## Verified data facts (do not re-derive; assert them in tests)

From `data/probe_cases/memtrace_kp_cases.json`, 2047 cases, two `case_id` shapes,
zero unmatched:

```
numeric:  memtraceb-<uuid8>-kp<NNNN>-a<N>c<I>-<condition>     1551 cases, 120 families
slug:     memtraceb-<uuid8>-<slug>-a<N>c<I>-<condition>         496 cases,  62 families
total families 182
```

- numeric family size: min 4, median 8, max 32
- slug family size: exactly 8 for all 62 families
- slug conditions: `current-missing` for all 496 (the ONLY carrier of `missing`)
- within-family memory scale drift is ZERO on both keyings: comparing the members at
  min(c) against the members at max(c), median `len(extracted_memory)` is equal for
  120/120 numeric families and 62/62 slug families (0 growing, 0 shrinking).
  This is the fact that makes within-family marginal utility confound-free. Assert it.
- only 20 `user_uuid` values appear under both keyings

## Task 1 — family-keyed stream builder

New file: `cmd_audit/repair/memtrace_families.py` (stdlib only).

Parse both id shapes with two anchored regexes. Every case must match exactly one;
if any case matches neither, raise (do not skip).

```python
@dataclass(frozen=True)
class MemtraceFamily:
    family_id: str        # SHA256 hex of ("memtrace", keying, user_uuid, key_value)
    keying: str           # "kp" | "slug"
    user_uuid: str
    key_value: str        # str(kp_position) for kp, slug text for slug
    members: tuple[MemtraceMember, ...]   # sorted by (c_index, a_index, case_id)

@dataclass(frozen=True)
class MemtraceMember:
    case_id: str
    c_index: int
    a_index: int
    condition: str        # e.g. "current-present", "current-missing"
    case: ProbeCase
```

Public functions:

- `build_families(cases) -> tuple[MemtraceFamily, ...]` — deterministic order by
  `family_id`.
- `family_bucket(user_uuid) -> int` — `int(SHA256(user_uuid).hexdigest(), 16) % 5`.
  Bucket on `user_uuid`, NOT on `family_id`: 20 uuids span both keyings and bucketing
  on family_id would put the same user's memories in different buckets, leaking across
  the unseen/represented split. Document this reason in the docstring.
- `split_families(families) -> FamilySplit` — bucket 0 = `unseen` (safety probe
  families, never used for updates), buckets 1-4 = `represented`. Within a represented
  family, members with `c_index` in {0,1,2} are `update` members and {3,4,5,6,7} are
  `heldout` probe members. Members whose `c_index` exceeds the observed range simply
  produce a shorter list; never pad, never raise.
- `family_stream(families, *, seed) -> tuple[MemtraceMember, ...]` — shuffle family
  ORDER with a seeded `random.Random(seed)`, keep member order WITHIN a family at the
  sorted `(c_index, a_index, case_id)` order. Never interleave families.

Determinism requirement: same `seed` -> byte-identical `case_id` sequence. Two
different seeds must produce different family order but identical within-family order.

## Task 2 — within-family marginal utility gate

Amends CONTRACT §8: the evolution gate becomes a CONJUNCTION of the existing
endpoint/DiD/AULC triple AND a new within-family gate. Do not weaken or reinterpret
the existing triple; add alongside it.

In `cmd_audit/eval/evolution_gates.py`:

```python
@dataclass(frozen=True)
class WithinFamilyResult:
    keying: str            # "kp" | "slug" | "combined"
    n_families: int
    mean_marginal_gain: float
    lower_bound_95: float
    passed: bool
```

Definition. For each family with at least one member at `c_index == 0` and at least
one member at `c_index > 0`:

```
baseline_k = mean(net_gain of members with c_index == 0)
later_k    = mean(net_gain of members with c_index  > 0)
marginal   = later_k - baseline_k
```

The paired unit is the FAMILY, so the resample unit is the family. Reuse the existing
one-sided paired bootstrap with `bootstrap_samples = 10_000` and the existing
`_one_sided_lower` helper — do not write a second bootstrap. Pass iff
`lower_bound_95 > 0`.

Report three stratified results — `kp` (120 families), `slug` (62), and `combined`
(182). The gate verdict uses `combined`; the two strata are reported for direction
agreement and are NOT gates themselves. If the two strata disagree in sign, set a
`strata_disagree` flag on the review so it is impossible to report silently.

Families with no `c_index == 0` member, or no `c_index > 0` member, are excluded and
counted in an `excluded_families` field. Excluded count must be reported, never
silently dropped.

Wire into `evaluate_evolution_gates` so the primary verdict is
`existing_triple_passed and within_family.passed`. The safety gate is unchanged
(`S >= 0` and `LB95(S) >= -0.05`).

## Task 3 — three missing promotion tests

CONTRACT §14 lists these; promotion now actually executes via
`experiments/evolution_runner_common.py`, so untested promotion is a live risk.
Add to `tests/repair/test_operator_library_evolution.py` (or a sibling file):

1. **three-successes-plus-two-families** — a revision is NOT promoted with 2 distinct
   post-creation successful runtime validations, and IS promoted at 3, and is NOT
   promoted when the 3 successes span only 1 recurrence family. The producing case
   must be excluded from the count; add a case asserting that.
2. **anchor set has exactly four legal members** — `RevisionAnchorSet` construction
   raises on 3 or 5 members, and anchor preservation blocks a promotion that
   regresses any one of the four.
3. **two consecutive regressions retire a stable revision** — one regression does not
   retire; two consecutive do; a success between them resets the counter.

Read the real implementation first and test the behaviour that exists. If the
implementation cannot satisfy one of these, do NOT weaken the test — leave it failing
and report it in your summary as an implementation gap.

## Task 4 — scorer identity

Two defects to close:

(a) `--live` silently falls back to local ollama `qwen2.5:7b` when no `LLM_*` env vars
are set. Make the live path fail closed: if `LLM_BASE_URL` / `LLM_MODEL` (and the judge
equivalents when a judge role is used) are unset, raise with a message naming the
missing variables. Do not invent defaults on the live path. The existing defaults in
`cmd_audit/core/llm_client.py` may stay for offline/unit use.

(b) `--scorer-version` is a free-form string, which violates CONTRACT §1 "scorer
version frozen". Derive it from real identity instead: compose from the resolved judge
model id, judge base url host, and rubric version, e.g.
`sha256("|".join([judge_model, judge_host, rubric_version]))[:12]` recorded alongside
the human-readable parts. If the caller passes `--scorer-version` and it disagrees
with the derived value, raise.

## Out of scope for this build

Do NOT touch: the live mem0 backend (later, `experiments/` layer, optional dependency),
`Pattern` prototypes, `_memory_fingerprint`, `_query_signature_similarity`, top-5
matching, prompts, the OperatorSpec grammar or executor, the 3+2 retrieval quota, the
0.1 threshold, or the discovery space. All are frozen by CONTRACT §1.
Do NOT delete any dataset file. Do NOT modify `real_*.json` probe data.

## Definition of done

- `python -m pytest tests/ -v` passes with no pre-existing test newly failing.
- New tests cover: both id shapes parse with zero unmatched; 182 families / 120 kp /
  62 slug; slug families all size 8; zero within-family scale drift on both keyings;
  bucketing is on user_uuid; stream determinism under a fixed seed and difference
  across seeds; within-family gate arithmetic on a hand-built fixture with a known
  answer; the three promotion tests; live-path fail-closed on missing env; derived
  scorer version mismatch raises.
- No PyPI import anywhere under `cmd_audit/`.
- Report at the end: which of the three promotion tests pass against the real
  implementation, and any that expose a genuine implementation gap.
