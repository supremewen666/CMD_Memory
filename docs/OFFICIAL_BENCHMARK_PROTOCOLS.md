# Official benchmark protocols used by CMD

This document separates benchmark-author scoring from local diagnostics. A
result is called **official** only when the benchmark authors' suite and scorer
are invoked without changing their split, metric or budget.

## LoCoMo

- Source: <https://github.com/snap-research/locomo>
- Data: ten conversations, 1,986 QA items.
- JSON category mapping used by the official evaluator:
  `1=multi-hop` (282), `2=temporal` (321), `3=open-domain` (96),
  `4=single-hop` (841), `5=adversarial` (446).
- Official lexical QA metric: Porter-stemmed token F1 for categories 2/3/4;
  comma-separated partial F1 for category 1; category 5 succeeds only when the
  answer states that the information is unavailable/not mentioned.
- Current ICLR memory papers commonly add an LLM judge and BLEU-1/F1, report
  category breakdowns, token/call/latency cost, and include full-context plus
  retrieval/memory-system baselines. Those additions must be labeled with the
  exact judge model and are not the original LoCoMo metric.

CMD's headline arm answers from the root-bound state committed by the
MemAudit/ECC/GHOST runtime. BM25 is retained as the matched retrieval control.
The legacy `seed:*` static-action arena is not reported as CMD. The upstream
`task_eval.evaluation.eval_question_answering` is imported only after the
prediction seal has been verified.

## LongMemEval (ICLR 2025)

- Source: <https://github.com/xiaowu0162/LongMemEval>
- Data: 500 questions covering information extraction, multi-session
  reasoning, temporal reasoning, knowledge updates and abstention.
- Prediction ABI: one JSON object per line with exactly `question_id` and
  `hypothesis`.
- Official QA score: run upstream `src/evaluation/evaluate_qa.py` with the
  chosen judge model and `longmemeval_oracle.json`; aggregate with
  `print_qa_metrics.py`.
- `longmemeval_s_cleaned.json` and the oracle fit a 128k reader control;
  LongMemEval-M is too long for that full-context control and should be tested
  as a memory/retrieval system rather than silently truncated.

CMD exposes BM25 top-5 and a root-bound ECC committed-state arm. MemAudit
telemetry and GHOST updates are completed before the answer model runs; the
runtime never reads `answer` or `answer_session_ids`. The scorer is a separate
post-seal process.

## Evo-Bench

- Source: <https://github.com/RUCAIBox/Evo-Bench>
- Formal suite: 160 validation tasks plus a disjoint 448-task evaluation suite.
- Native metrics: BrowseComp-EN Pass@1, HLE Pass@1, GDPval rubric mean,
  APEX-Agents Pass@1, and Claw-Eval Pass^3.
- Overall weighting: Search:Office:General = 2:2:1.
- Fixed protocol: the same minimal CodeAct seed harness, policy model and
  scorer; at most 20 validation-consuming iterations, 1,000 steps and 48
  hours. General tasks use three trials.
- The evolver can request validation only. Evaluation is run after the selected
  harness is frozen and the evolver sandbox is destroyed. Formal execution
  uses E2B; local mode is only for smoke/development.

`run_evobench_harness.py` is therefore only a local governance ledger.
`run_evobench_official.py` builds or executes the upstream canonical commands;
only the latter path can produce an Evo-Bench score.

## Reproduction entrypoints

For a local vLLM deployment, the repository includes a detached launcher. A
model path loads weights only in `start-server`; `LLM_MODEL` is the served API
name and must not be used as a substitute for starting the server.

```bash
bash run_memory_benchmarks_nohup.sh start-server \
  /path/to/Qwen2.5-7B-Instruct 65536
bash run_memory_benchmarks_nohup.sh server-status
bash run_memory_benchmarks_nohup.sh server-smoke
bash run_memory_benchmarks_nohup.sh run \
  artifacts/runtime/longmemeval_ecc \
  artifacts/runtime/locomo_ecc
tail -f artifacts/logs/memory_benchmarks.log
```

The detached runner saves the served tokenizer path and context window. The
prediction process drops complete lowest-priority memory items before the
prompt exceeds that same window; every truncation is recorded in
`runtime_ledger.jsonl`. HTTP 4xx responses retain the provider error body and
are never reported as an unreachable endpoint.

```bash
# Receipt-bound prediction (requires a completed instrumented ECC runtime).
python -m experiments.run_ecc_sealed_memory_benchmark \
  --benchmark longmemeval \
  --runtime-dir artifacts/runtime/longmemeval_ecc \
  --output artifacts/experiments/longmemeval_ecc_sealed

# Explicit legacy baseline only; not the MemAudit/GHOST system.
python -m experiments.run_sealed_memory_benchmark \
  --benchmark longmemeval \
  --output artifacts/experiments/longmemeval_legacy_static

# Inspect an official Evo-Bench formal command without spending resources.
python -m experiments.run_evobench_official --stage evolve \
  --official-root /path/to/Evo-Bench \
  --policy-config /path/to/policy.json \
  --judge-config /path/to/judge.json \
  --evolver-config /path/to/evolver.json
```
