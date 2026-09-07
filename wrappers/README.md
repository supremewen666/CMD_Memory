# Controlled Competitor Wrappers

Stage 9 compares CMD with two skill-level methods and one deployed memory
system under the same legal repair API:

```text
CMD-RepairStream decision view
  -> MemSkill / ERSkill / Mem0 evidence
  -> frozen shared repair head
  -> legal OperatorSpec or abstention
  -> common COW + ECC + CAS governance
```

## Competitors

- `memskill_adapter.py` consumes evidence exported by an official pinned
  MemSkill checkpoint. Its skills concern memory construction and evolution;
  the shared head maps that evidence to CMD's legal repair API.
- `erskill_adapter.py` consumes frozen retrieval evidence from an official
  ERSkill artifact when available, or from an explicitly labelled
  `paper_faithful_erskill_reimplementation`.
- `mem0_adapter.py` executes the pinned Mem0 OSS SDK and scopes state by the
  case namespace.

MemSkill and ERSkill are not described as native memory-repair systems. Their
controlled labels are `MemSkill + shared repair head` and
`ERSkill + shared repair head`. Native-track scoring is intentionally disabled
for all three competitors because the current response contract represents a
repair action, not each project's native answer format.

## Frozen Skill Evidence

The two skill competitors use `cmd-frozen-skill-evidence-v1`. The artifact is
generated after competitor training and before `T_final` evaluation:

```bash
python experiments/spec_v03_export_skill_competitor_inputs.py \
  --runtime-cases "$DATA/runtime_cases.json" \
  --event-order "$DATA/event_order_manifest.json" \
  --split-manifest "$DATA/split_manifest.json" \
  --include-split T_final \
  --output "$RUN_ROOT/industry/inputs/$STREAM.json"
```

This export contains only serving-visible decision views. It deliberately
omits incident labels, legal or oracle operators, sealed outcomes, and future
events. The pinned competitor implementation consumes these records and emits
the JSONL evidence rows frozen below.

```json
{
  "schema_version": "cmd-frozen-skill-evidence-v1",
  "system_id": "memskill",
  "implementation": "official_memskill_checkpoint_export",
  "artifact_revision": "checkpoint-7",
  "producer_repository": "https://github.com/ViktorAxelsen/MemSkill",
  "producer_commit": "40-character exact git commit",
  "frozen": true,
  "training_splits": ["D_skill", "D_router"],
  "records": {
    "case-id": {
      "evidence": [{"memory": "visible, retrieved evidence"}],
      "selected_skill_ids": ["capture_temporal_context"],
      "retrieval_trace": [{"primitive": "semantic_search", "rank": 1}],
      "source_event_ids": ["event-id"],
      "usage": {
        "llm_calls": 1,
        "input_tokens": 1000,
        "output_tokens": 100,
        "wall_clock_seconds": 2.0,
        "gpu_seconds": 2
      }
    }
  }
}
```

The wrapper rejects artifacts that are mutable, trained on evaluation splits,
do not match their configured digest, cite events outside the serving view, or
contain evaluator-only keys. Recorded evidence-construction usage is charged
before the shared-head call.

## Reproducibility Boundary

1. Pin each competitor checkout to an exact commit.
2. Train or adapt only on `D_skill`, `D_router`, `D_cal`, and `D_lifecycle`.
3. Freeze the checkpoint and export one evidence record per evaluation case.
4. Record `official_memskill_checkpoint_export` for official MemSkill.
5. Record `official_erskill_artifact` only for an author-released artifact;
   otherwise use `paper_faithful_erskill_reimplementation` in every table.
6. Put exact artifact paths and digests in
   `controlled_memory_protocol.json`, then run Stage 9.

Convert each competitor's per-case JSONL export into the closed artifact. Each
input row is the record shown above plus a top-level `case_id`:

```bash
python experiments/spec_v03_freeze_skill_evidence.py \
  --system-id memskill \
  --implementation official_memskill_checkpoint_export \
  --artifact-revision "$MEMSKILL_CHECKPOINT" \
  --producer-repository https://github.com/ViktorAxelsen/MemSkill \
  --producer-commit "$MEMSKILL_COMMIT" \
  --training-split D_skill \
  --training-split D_router \
  --records "$RUN_ROOT/industry/exports/memskill.jsonl" \
  --output "$RUN_ROOT/industry/frozen/memskill.json"

python experiments/spec_v03_freeze_skill_evidence.py \
  --system-id erskill \
  --implementation paper_faithful_erskill_reimplementation \
  --artifact-revision "$ERSKILL_REVISION" \
  --producer-repository "$ERSKILL_REPOSITORY" \
  --producer-commit "$ERSKILL_COMMIT" \
  --training-split D_skill \
  --training-split D_router \
  --records "$RUN_ROOT/industry/exports/erskill.jsonl" \
  --output "$RUN_ROOT/industry/frozen/erskill.json"
```

Bind both frozen artifacts and the Mem0 metering endpoint in one operation:

```bash
python experiments/spec_v03_configure_industry_runtime.py \
  --protocol "$RUN_ROOT/industry/controlled_memory_protocol.json" \
  --mem0-config "$RUN_ROOT/industry/configs/mem0-controlled.json" \
  --memskill-artifact "$RUN_ROOT/industry/frozen/memskill.json" \
  --erskill-artifact "$RUN_ROOT/industry/frozen/erskill.json" \
  --erskill-implementation paper_faithful_erskill_reimplementation \
  --usage-root "$RUN_ROOT/industry_runtime/usage" \
  --metering-url http://127.0.0.1:9100
```

## Run

```bash
python experiments/spec_v03_stage5_9.py \
  --runtime-cases /ABS/PATH/runtime_cases.json \
  --event-order /ABS/PATH/event_order_manifest.json \
  --split-manifest /ABS/PATH/split_manifest.json \
  --include-split T_final \
  --output /ABS/PATH/stage9_report.json \
  --run-id controlled-competitors \
  --stage stage9 \
  --track controlled_a1 \
  --industry-adapters-config /ABS/PATH/industry_adapters.json \
  --system-max-llm-calls 64 \
  --system-max-input-tokens 500000 \
  --system-max-output-tokens 16384 \
  --system-max-wall-seconds 1200 \
  --system-max-gpu-seconds 600
```

`run_spec_v03_industry_services.sh` now supervises only the shared embedding
endpoint and enforcing model proxy used by Mem0. Frozen skill artifacts carry
their measured inference usage directly.
