# Direction 01: Counterfactual Memory Debugger

Full plan: `plans/direction_01_research_plan.md`

## Core Question

When a memory-augmented agent fails, can we determine whether the memory item itself is bad, whether the memory pipeline failed, and what repair should be stored for future similar tasks?

## Grounding

- arXiv:2602.02474 - MemSkill evolves memory skills from hard cases.
- arXiv:2604.01007 - Omni-SimpleMem uses failure-driven search to improve memory systems.
- arXiv:2501.18160 - RepoAudit uses validation to reduce hallucinated agent outputs.
- arXiv:2603.07670 - Agent Memory Survey (2022-2026): formalizes write-manage-read loop, confirms no existing counterfactual attribution framework.
- arXiv:2603.10600 - Trajectory-Informed Memory: closest observational approach to operation-level attribution (Decision Attribution Analyzer).
- arXiv:2603.14597 - D-MEM: RPE-gated Fast/Slow memory routing suggests cheap pre-filter for CMD replay trigger.
- arXiv:2604.27283 - RSCB-MC: risk-sensitive retrieval with abstention action; retrieval_error should distinguish unsafe injection from wrong retrieval.
- GitHub: acailic/agent_debugger (Peaky Peek) - closest open-source tool; validates demand but is interactive (HITL), not automated counterfactual.

## Minimal Probe

For each failed query, first run a lightweight Memory Monitor. If it flags hallucination, omission, conflict, or memory misuse, start Failure Diagnoser and replay the V0 counterfactual portfolio:

1. Oracle Write,
2. Oracle Compression,
3. Verbatim Event Oracle,
4. Oracle Retrieval,
5. Injection-Oracle,
6. Evidence-Given Reasoning.

The predicted failure cause is the replay with the largest recovery gain. CMD then generates an Error-Cause-Solution record:

```text
wrong_memory + cause + corrected_memory + repair_action + repair_guidance
```

It updates User Memory and writes compact Failure Memory so future similar tasks retrieve only `corrected_memory + repair_guidance`.

Post-Repair Context Replay verifies the repair by rebuilding repaired context and rerunning the original failed query, outputting three-value `repair_assessment` (`recovered`/`partial`/`failed`).

Deferred replay interventions (V1/V2): granularity, route, graph, safety oracles.

## Failure Split

V0 pipeline labels (active):
- `write_error`, `compression_error`, `premature_extraction_error`, `retrieval_error`, `injection_error`, `reasoning_error`

Bad memory item labels (excluded from V0 attribution, deferred to V1):
- `item_wrong`, `item_stale`, `item_conflict`, `item_poisoned`, `item_compression_distorted`

Deferred pipeline labels (V1/V2):
- `granularity_error`, `route_error`, `graph_error`, `safety_error`, `ingestion_error`

## Promotion Signal

Promote if failure attribution accuracy is clearly above heuristic baselines, CMD-guided fixes outperform undifferentiated hard-case updates, and ECS Failure Memory reduces recurrence of similar hallucination or conflict cases.
