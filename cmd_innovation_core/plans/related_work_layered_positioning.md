# Related Work Layered Positioning

Decision 34 R6 replaces the Decision 30 head-to-head benchmark against Rewind
with a layered related-work section. Rewind, Culpa, TraceForge, CMD, MemRepair,
and MemLineage all use replay, repair, or provenance ideas, but they operate on
different objects. The paper should position CMD by layer rather than forcing a
shared metric where the input modalities do not match.

## Layered Stack

**Runtime layer: Rewind / Culpa / TraceForge.** These tools operate over agent
execution traces: steps, tool calls, retries, forks, and trace-level sensitivity.
They are useful when the failure is caused by runtime control flow, model choice,
prompting, or a bad tool invocation. CMD treats them as complementary
infrastructure that can preserve the trace CMD later audits.

**Memory-pipeline layer: CMD.** CMD operates over the memory pipeline rather than
the whole runtime trace. Its unit of diagnosis is an operation label such as
write, compression, premature extraction, retrieval, injection, route,
granularity, graph, safety, or reasoning. Counterfactual replay produces a
Recovery Gain ranking, then ECS and Post-Repair Context Replay validate the
repair path.

**Item-content layer: MemRepair / MemLineage.** Item-level repair and provenance
systems operate on individual memory records and their derived state. MemRepair
validates the importance of failure-to-success repair memory; MemLineage
validates provenance as infrastructure for identifying which memory items were
affected. CMD can use these mechanisms downstream once it has attributed the
failed operation.

## Five-Dimension Differentiation

| Dimension | Runtime debugger (Rewind / Culpa / TraceForge) | Memory debugger (CMD) |
|-----------|------------------------------------------------|------------------------|
| Granularity | Agent step / tool call | Memory pipeline operation (11 labels) |
| Diagnosis | LLM judges which step failed | Counterfactual replay produces Recovery Gain ranking |
| Repair | Retry step with different model/prompt | Rewrite specific memory item or routing config |
| Validation | LLM-as-judge score comparison on new trace | Post-Repair Context Replay rerun on original query |
| Learning | One-shot fix, no retention | ECS -> Failure Memory -> recurrence prevention |

## Boundary Examples

**Memory-content failure where CMD is the right tool.** A memory-augmented agent
once stored that the user called Messi the GOAT, but a later consolidation step
compressed the item to "the user discussed football preferences." The final
answer fails because the decisive phrase was lost inside the memory pipeline.
CMD's oracle compression replay can restore the missing phrase, assign the
failure to `compression_error`, and draft an ECS repair for the corrupted memory
item. A runtime debugger can retry the final step, but the retry does not explain
which memory operation lost the phrase.

**Runtime failure where a runtime debugger is the right tool.** The memory store
contains the correct itinerary, retrieval returns it, and the context includes
the necessary evidence, but the agent calls the wrong calendar API endpoint. In
that case the failure is a tool-use or step-control problem. Rewind-style retry
and trace forking can isolate the bad runtime step more directly than CMD,
because the memory pipeline itself did not fail.

**Intersecting case where the tools are complementary.** An agent retrieves a
stale location from memory and then calls a booking tool with that stale value.
Runtime replay can show the bad tool call and repair the immediate action, while
CMD can replay retrieval and route interventions to determine whether the stale
value came from retrieval, routing, or memory content. The combined diagnosis is
stronger than either layer alone: fix the runtime action now, then repair the
memory pipeline so the same stale item is not reused.

## No Head-to-Head Benchmark

The paper does not run a head-to-head benchmark between Rewind and CMD. Rewind
and CMD do not share input modality: Rewind ingests runtime traces and repairs
agent steps, while CMD ingests memory-pipeline probe cases and repairs memory
operations. Running both on identical cases either tortures the case format until
both can ingest it, producing meaningless metrics, or restricts the evaluation to
an unrepresentative intersection. The defensible comparison is layered
positioning plus qualitative boundary examples.
