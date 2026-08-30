# Controlled Industry-System Wrappers

These wrappers connect Stage 9 to fixed official checkouts without importing
their dependency trees into the CMD process.

```text
Stage59Runner
  -> PinnedJsonSubprocessAdapter (closed JSON stdin/stdout)
  -> official-system venv + thin wrapper
  -> official Python SDK or dedicated localhost service
  -> shared pinned Qwen3 repair head
  -> legal operator ID or abstention
```

## Controlled call paths

- `lightmem_adapter.py`: `LightMemory.from_config` -> `add_memory` -> optional
  offline update -> `retrieve(limit=top_k)`.
- `lycheemem_adapter.py`: `POST /memory/append-turn` -> synchronous
  `POST /memory/consolidate` -> raw `POST /memory/search`. The service must be a
  dedicated, isolated instance because the raw search API is not case-scoped.
  The wrapper requires a namespace-bound endpoint and an isolated-instance
  receipt before it will write or search.
- `mem0_adapter.py`: `Memory.from_config` -> `add(user_id=namespace)` ->
  `search(filters={"user_id": namespace}, top_k=top_k)`.

MCP, Lychee `/memory/reason`, Lychee smart-search synthesis, and Mem0 Cloud are
not used in the controlled track. They belong to the future native response
contract.

Use these labels in controlled-track tables and figures:

- `LightMem + shared repair head`
- `LycheeMem + shared repair head`
- `Mem0 OSS + shared repair head`
- `CMD`

The first three labels deliberately do not imply that an official system
natively emits a CMD repair operator.

## Required setup

1. Clone each official repository, checkout an exact 40-character commit, and
   install it in its own `.venv`.
2. Copy `protocol/controlled_memory_protocol.example.json` to
   `protocol/controlled_memory_protocol.json` and replace every placeholder.
   Set LycheeMemory `expected_commit` to the same commit used by its adapter
   entry and isolated service receipt.
3. Copy `protocol/industry_adapters.example.json` to an experiment-local config,
   replace repository/venv paths and exact commits, and keep wrapper paths
   absolute. The adapter sets the official repository as the subprocess cwd.
4. Ensure every LightMem storage path/collection that needs isolation contains
   `{namespace}`. Mem0 is additionally scoped by the official `user_id` filter.
5. Start one isolated LycheeMemory service for every evaluated case namespace.
   A service manager or reverse proxy must resolve the `{namespace}` endpoint
   template to that process and create the receipt below before the wrapper runs.
   Do not point controlled runs at a shared personal service.

```json
{
  "schema_version": "cmd-lycheemem-isolated-instance-v1",
  "scope": "cmd-<case namespace>",
  "base_url": "http://127.0.0.1:9000/instances/cmd-<case namespace>",
  "official_commit": "<exact 40-character LycheeMemory commit>",
  "empty_at_start": true
}
```

The shared head endpoint, model snapshot, top-k, temperature, prompt contract,
and system settings are covered by the protocol SHA emitted in
`adapter_revision`.

## Resource accounting

The wrapper measures shared-head usage directly from the OpenAI-compatible
response. Official SDKs often consume provider usage internally, so claim-
eligible runs must route their model traffic through a budget-enforcing proxy
that writes an atomic cumulative receipt before and after each backend call:

```json
{
  "schema_version": "cmd-metered-model-usage-receipt-v1",
  "scope": "cmd-<case namespace>",
  "llm_calls": 7,
  "input_tokens": 12000,
  "output_tokens": 900,
  "gpu_seconds": 12
}
```

Use `backend_usage.mode=enforcing_proxy_receipt` for reported experiments. A
local wiring smoke may use:

```json
{"mode": "development_unmetered", "receipt_path": null}
```

The executable wrappers return `UNSUPPORTED` in this unmetered mode, while the
SDK/API functions remain available for local contract tests. This prevents an
unmetered smoke result from entering the Stage 9 comparison as `OK`. The proxy,
not just the post-hoc receipt reader, must stop backend model calls before a
budget is exceeded. If an endpoint nevertheless reports an overrun, the wrapper
returns `FAILED/budget_exhausted`; counters that crossed a limit are saturated
at that limit because the parent `AdapterResponse` contract rejects over-budget
values.

## Run Stage 9

```bash
python -m experiments.spec_v03_stage5_9 \
  --runtime-cases /ABS/PATH/runtime_cases.jsonl \
  --event-order /ABS/PATH/event_order.json \
  --output /ABS/PATH/stage9_report.json \
  --run-id controlled-industry-pilot \
  --stage stage9 \
  --track controlled_a1 \
  --industry-adapters-config /ABS/PATH/industry_adapters.json \
  --system-max-llm-calls 20 \
  --system-max-input-tokens 100000 \
  --system-max-output-tokens 4096 \
  --system-max-wall-seconds 300 \
  --system-max-gpu-seconds 300
```

The current budget is per `AdapterRequest` (per Stage 9 case), not a cumulative
whole-run cap. Aggregate usage remains available in the Stage 9 resource ledger.

## Output discipline

Wrappers write exactly one `AdapterResponse` JSON object to stdout. SDK logging
is redirected to stderr. Native track requests fail closed because the current
`AdapterResponse` cannot faithfully represent native answer/memory outputs.
