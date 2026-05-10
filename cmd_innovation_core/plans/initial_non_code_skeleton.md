# Initial Non-Code Skeleton

Date: 2026-05-08

This skeleton turns the CMD core reading path into planning artifacts without adding implementation code. It follows the requested order: domain grilling, PRD synthesis, issue slicing, prototype framing, then TDD planning.

## Source Reading Order

1. `README.md`
2. `knowledge/current-memory.md`
3. `plans/cmd_research_plan_and_roadmap.zh.md`
4. `plans/direction_01_research_plan.md`
5. `knowledge/topic-cmd-memory-failure.md`
6. `hypotheses/hyp-005.md`
7. `hypotheses/hyp-006.md`

## Durable Artifacts

- Domain language: `../CONTEXT.md`
- PRD: `../prd/cmd_minimal_probe_prd.md`
- Local issue tracker: `../issues/`
- Throwaway prototype brief: `../prototypes/cmd_probe_logic_prototype.md`
- TDD tracer bullets: `../tdd/cmd_tracer_bullets.md`

## Synthesis

CMD's smallest credible artifact is not a full memory system. It is a probe that creates labeled memory failures, runs counterfactual replays, produces an attribution table, and checks whether operation-level labels lead to better repairs than heuristic or judge-only baselines.

The V0 probe is intentionally scoped to six core labels:

- `write_error`
- `compression_error`
- `premature_extraction_error`
- `retrieval_error`
- `injection_error`
- `reasoning_error`

`granularity_error`, `route_error`, `graph_error`, and `safety_error` are deferred to V1/V2.

The research line should gate claims on evidence:

- First table: attribution accuracy, macro F1, top-2 accuracy, and cost per diagnosis.
- First post-repair result: repaired-context replay recovers or fails to recover the original failed query.
- First repair result: targeted memory fixes compared with undifferentiated hard-case updates using post-repair results.
- First recurrence result: ECS Failure Memory compared with no Failure Memory and full failed-trace retrieval.
- First framing defense: CMD compared with subagent judge as intervention-grounded attribution versus post-hoc explanation.

## Working Assumptions

- No remote issue tracker is configured in this repo, so issues are represented as local markdown files.
- No ADR is created yet. The current choices are research planning choices, not hard-to-reverse implementation commitments.
- The prototype branch is logic/state-model oriented, not UI oriented.
- The first public CMD-Audit harness interfaces now exist for issue 0001 and issue 0002. Continue with behavior tests over those public interfaces rather than private helpers.
- The first implementation target is a standalone research harness with an adapter interface reserved for later memory-agent integration.

## Current Addendum

Issue 0001 and issue 0002 have green smoke coverage. The active non-code skeleton now lives in `../issues/0003-counterfactual-attribution-table-implementation-details.md` and focuses on converting the one-replay Oracle Retrieval path into a V0 Replay Portfolio, starting with Verbatim Event Oracle and `premature_extraction_error`.

## Resolved Decisions

1. V0 uses the six-label core set and does not cover the full taxonomy.
2. `premature_extraction_error` is a first-class pipeline label diagnosed by Verbatim Event Oracle.
3. Subagent judge serves both as a baseline and as a cheap high-recall monitor, but final attribution and ECS remain replay-delta grounded.
4. V0 is a standalone research harness for reproducible perturbation labels, replay deltas, baselines, and metrics.
5. Post-Repair Context Replay is a required V0 gate before future Failure Memory evaluation.

## Boundary Rules and Acceptance Conditions

1. `CONTEXT.md` defines `CMD-Audit` and `CMD-Skill Adapter` separately.
2. Subagent Judge Monitor is leak-safe: it can trigger replay but cannot emit final labels, ECS, memory writes, gold answers, or full failed traces.
3. V0 attribution excludes bad memory item labels and only evaluates the six pipeline labels.

## Remaining Open Decisions

1. What threshold defines "close enough" replay deltas for top-2 or multi-label attribution.
2. Whether V1 should add `granularity_error` and `route_error` before graph and safety labels.
3. What concrete adapter interface shape is sufficient without overfitting to a future memory agent.
