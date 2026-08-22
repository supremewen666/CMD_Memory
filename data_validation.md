# Data Validation

Validation time: 2026-08-21. This review is restricted to acquisition and
protocol inputs; it makes no claim about the experiment model.

## Dataset Identity

| Dataset | Official source pinned by the acquisition path | Intended protocol boundary |
|---|---|---|
| LongMemEval cleaned | `xiaowu0162/LongMemEval` and HF dataset `xiaowu0162/longmemeval-cleaned` | 500 shared `question_id`s across S, M, and oracle; S/M are runner input and oracle is offline scorer only. |
| MemFail | `ishirgarg/MemFail` | Real API stress/replay only; no delayed-live-outcome claim. |
| Evo-Bench | `RUCAIBox/Evo-Bench` | Public validation and seed only; evaluation assets must remain sealed. |

The manifest currently pins Evo-Bench commit
`e1dc9386a193cab1ee8630824c085e5e26d0c730`. It is **not closed**: its
`datasets` section contains only `evobench`, and it records a LongMemEval
download failure. No partial file was treated as data.

## Reality Check

### LongMemEval

- `longmemeval_s_cleaned.json` exists, is a real JSON list, and has 500 rows,
  500 unique nonmissing string `question_id`s.
- Its row schema is exactly `answer`, `answer_session_ids`, `haystack_dates`,
  `haystack_session_ids`, `haystack_sessions`, `question`, `question_date`,
  `question_id`, and `question_type`; scalar/list types are consistent in the
  inspected rows.
- S has 23,867 session entries. The three aligned session arrays have equal
  length in all 500 rows. Question-type counts are: knowledge-update 78,
  multi-session 133, single-session-assistant 56, single-session-preference
  30, single-session-user 70, temporal-reasoning 133.
- `longmemeval_m_cleaned.json` is presently an incomplete `.partial` file;
  `longmemeval_oracle.json` is absent. They are not valid inputs and were not
  parsed. Consequently S/M/oracle 500-qid equality, M schema, oracle schema,
  and the oracle/full-history content-hash mapping cannot yet be verified.
- The required `oracle` sidecar must stay offline scoring-only. Its absence
  prevents a valid R1 evidence score and makes the current unscorable fraction
  **not computable** (not zero).

### MemFail

All six official acquisition targets exist: five CSV files and the long-hop
metadata JSON. The CSVs are nonempty and have these physical-row counts:

| File family | Rows | Target fields / observations |
|---|---:|---|
| coexisting facts | 100 | `preference_category`, facts, question, `ground_truth_answer`; questions unique. |
| conditional easy | 100 | condition and `condition_met` (`yes`/`no`), question, `ground_truth_answer`; questions unique. |
| conditional hard | 100 | Same schema as easy; `condition_met` (`yes`/`no`); questions unique. |
| persona retrieval | 100 | Each row has facts plus a JSON `questions` list; 300 nested scored questions total, with `is_misleading`, distractor, and ground-truth answer. |
| long hop | 92 | Unique `id`; hop count 1/2/3; answer plus choices A--E and `correct_choice`. |

The physical CSV total is 492, while flattening the persona nested questions
gives 692 scored prompts. Runners must record which unit they use and must not
report 692 as CSV rows. The long-hop metadata declares a fixed seed of 42,
but its generated-at/model fields show this component is generated benchmark
material; it is real downloaded upstream material, not a local mock.

### Evo-Bench

The public validation JSON exists and contains a `validation` list of exactly
160 nested tasks. Every inspected task has `id`, `domain`, `prompt`, `scorer`,
`metadata`, and `apex_public`. The acquired seed harness is the public
`seed_codeact_bash_policy_harness` v0.1.0 (`max_steps=300`,
`rollout_wall_clock_seconds=3600`). The local acquisition contains no
evaluation/sealed suite or assets; this is correct and must remain so.

## Split Integrity and Leakage Risk

- LongMemEval cannot yet establish the required S/M/oracle exact-qid split
  boundary. Do not run a scored M0/R1 or E2E result from S alone as if it were
  the three-file protocol.
- Session chronology is not safely represented by raw list order: only
  289/500 S instances have lexically nondecreasing `haystack_dates`. The
  dates are sortable `YYYY/MM/DD (Day) HH:MM` strings and line up with the
  session arrays, so the loader must stably sort paired
  `(haystack_date, session_id, session)` records before sequential ingestion;
  it must retain original index as a tie breaker and audit the resulting order.
- The LongMemEval `answer`, `answer_session_ids`, and future oracle evidence
  are evaluation-only fields. They must not enter the memory writer, retriever,
  router, candidate selector, prompt, or namespace key. Content-hash matching
  may be built only after ingestion for offline scoring.
- MemFail ground-truth and choice/correct-choice columns are labels. They are
  offline scorer inputs, never Mem0 `add` content or repair-routing input.
- Evo-Bench validation may tune/freeze a harness. The missing sealed
  evaluation asset is intentional; no validation task or public seed should be
  called a sealed evaluation result.

## Preprocessing and Shape Check

- JSON is UTF-8 list-of-object data for LongMemEval S; its 500-row cardinality
  and array alignment are sound. M and oracle remain unvalidated.
- MemFail CSV headers and label dtypes are coherent with their task families;
  persona requires JSON decoding and flattening, whereas long-hop requires
  categorical A--E validation.
- Acquisition records SHA-256, bytes, and row/item count, but its open manifest
  has not yet recorded LongMemEval or MemFail revisions/licenses/hashes/sizes.
  Therefore it cannot yet serve as the required single provenance root.

## Mock-data Disclosure

No local synthetic substitute was used for these checks. MemFail long-hop is
upstream generated material (metadata reports model `gpt-5` and seed 42), not
an undisclosed local mock. The incomplete LongMemEval M file is explicitly
excluded rather than substituted.

## Verdict

**BLOCKED**

The closed acquisition manifest and mandatory LongMemEval M/oracle integrity
checks are incomplete. It is unsafe to connect the scored LongMemEval runner
or claim R1 recall/current-evidence results yet.

## Next Step

After no process holds `longmemeval_m_cleaned.json.partial`, resume only with:

```sh
python3 experiments/download_datasets.py --dataset longmemeval
python3 experiments/download_datasets.py --dataset all
python3 experiments/download_datasets.py --verify-only
```

Then rerun this validation to require: three 500-item qid sets with exact
equality, M/oracle schema checks, session content-hash mapping coverage and
unscorable rate, plus a closed manifest containing all three datasets with
revision, license, SHA-256, bytes, and count. **Do not connect the runner
until that succeeds.**
