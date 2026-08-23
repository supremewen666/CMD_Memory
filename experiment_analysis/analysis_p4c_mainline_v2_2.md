# P4C mainline analysis round 2 — v2 zero-call verification

## Executed verification

All commands were local and zero-call. They used the repository LongMemEval
and MemFail inputs and wrote disposable verification artifacts under `/tmp`.
Formal release artifacts must be rerun into the documented fresh artifact
directories before paper use.

## P4C-1/P4C-3

- P4C-1: 684 cases = LongMemEval 500 + MemFail 92 + poison structural
  variants 92; 684/684 committed.
- P4C-1 plan root:
  `54a7fd59fa52f38a158c67288f62fb3bafee7428efbd408ab36c46fbaf444480`.
- P4C-3: 1,368 decisions = 684 fault + 684 post-repair clean; observed
  accuracy 1.0 and false-repair rate 0.0 on the clear injected telemetry.
- Scope remains structural injected-fault coverage, not natural fault
  prevalence, answer accuracy, or noisy-detector generalization.

## P4C-4/5 v2

- 600 structural scenario variants × 8 arms = 4,800 outcomes.
- Phase counts: calibration 120, adaptation 240, sealed holdout 240.
- Case-stream root:
  `8ee15477c093fe93a856b2704bdf211417197991293d433414c6b27f4096432f`.
- Outcome root:
  `5e9fb840ed6dab0b6ab963dad81e55049c113acd606349e5d7e7437a8576cf5e`.

Holdout safe committed correction:

| Arm | Safe corrections | Rate | Unsafe selection rate |
|---|---:|---:|---:|
| static typed | 40/240 | 0.1667 | 0.0000 |
| zero-prior frozen | 16/240 | 0.0667 | 0.5000 |
| zero-prior receipt evolution | 40/240 | 0.1667 | 0.0000 |
| typed-prior frozen | 36/240 | 0.1500 | 0.0708 |
| typed-prior receipt evolution | 40/240 | 0.1667 | 0.0667 |
| random legal | 21/240 | 0.0875 | 0.5042 |
| no repair | 0/240 | 0.0000 | n/a |
| without ECC gate | 0/240 safe | 0.0000 | 1.0000 |

## Interpretation

Receipt evolution now improves over its matched frozen controls in this
deterministic robustness stream, but it only matches `static_typed` on the
primary holdout rate. Because the 600 cases are variants of three base
templates, this is mechanism evidence, not independent-source statistical
generalization. The next falsification target is a noisy/ambiguous detector
holdout and more independent structural templates; model-call answer
confirmation remains supplementary.
