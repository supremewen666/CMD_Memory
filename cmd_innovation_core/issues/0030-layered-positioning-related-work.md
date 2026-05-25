---
id: 0030
title: Layered positioning section + Decision 30 addendum (related work)
status: needs-triage
labels: [paper, decision-34, related-work, writing]
blocks: []
blocked_by: []
created: 2026-05-24
---

# 0030 — Layered positioning + Decision 30 addendum

## Why

Decision 34 R6 dropped the CMD vs Rewind head-to-head benchmark mandated by Decision 30. They operate on different layers (Rewind = runtime, CMD = memory pipeline) and don't share an input modality; running both on the same cases produces meaningless metrics or restricts to an unrepresentative intersection.

The replacement is a related-work positioning section that uses a layered-stack framing and three concrete boundary examples. ~2 hours of writing, no code, no benchmark.

## Acceptance criteria

| AC | Required behavior | Verified by |
|----|-------------------|-------------|
| AC1 | Layered-stack section drafted: Runtime layer (Rewind / Culpa / TraceForge) ↔ Memory-pipeline layer (CMD) ↔ Item-content layer (MemRepair / MemLineage). Each layer described in 2-3 sentences with cited tools. | text draft in `cmd_innovation_core/plans/related_work_layered_positioning.md` |
| AC2 | 5-dim differentiation table reframed from "Rewind vs CMD" to "Runtime debugger vs Memory debugger." Same content as Decision 30's existing table but neutralized to layer-vs-layer comparison. | reframed table |
| AC3 | Three boundary examples drafted. Each is a single-paragraph case description, not a measured comparison: (a) memory-content failure CMD diagnoses (e.g., compression_error losing 'Messi GOAT'); (b) runtime failure Rewind handles (e.g., wrong tool choice with correct memory); (c) intersecting case showing complementary fixes (incorrect retrieval). | 3 paragraphs |
| AC4 | Section explicitly states no head-to-head benchmark is run, with rationale: "Rewind and CMD do not share input modality. Running both on identical cases either tortures the case format until both can ingest it (meaningless metrics) or restricts to the intersection (unrepresentative)." | rationale paragraph |
| AC5 | Decision 30 in `cmd_innovation_core/plans/cmd_open_decisions.md` receives the addendum block from REPAIR §1B, paste-ready. | edit verified by grep for "2026-05-23 addendum" |

## Drafting notes

The 5-dim differentiation table reframed:

| Dimension | Runtime debugger (Rewind / Culpa / TraceForge) | Memory debugger (CMD) |
|-----------|------------------------------------------------|------------------------|
| Granularity | Agent step / tool call | Memory pipeline operation (11 labels) |
| Diagnosis | LLM judges which step failed | Counterfactual replay produces Recovery Gain ranking |
| Repair | Retry step with different model/prompt | Rewrite specific memory item or routing config |
| Validation | LLM-as-judge score comparison on new trace | Post-Repair Context Replay rerun on original query |
| Learning | One-shot fix, no retention | ECS → Failure Memory → recurrence prevention |

Cite: Rewind (`rewind fix`), Culpa (deterministic replay + forking), TraceForge (counterfactual attribution with sensitivity scoring). Note them as adjacent infrastructure — paper's stance is they are complementary tools at a different layer, not competitors.

## Files affected

| File | Edit type |
|------|-----------|
| `cmd_innovation_core/plans/related_work_layered_positioning.md` | new; ~1 page draft |
| `cmd_innovation_core/plans/cmd_open_decisions.md` Decision 30 | append addendum block (REPAIR §1B paste-ready) |

## Out of scope

- Quantitative measurement of any kind.
- Installing or running Rewind / Culpa / TraceForge.
- Changes to other paper sections (limitations, methods, claims).

## Estimate

~2 hours of focused writing.

## Dependency

- No blockers — can be drafted any time.
- No blockees — supplementary related-work content.

## Detail map

`REPAIR.md` §1 R6, §1B (Decision 30 addendum text), §4 TASK Next Steps #11.
