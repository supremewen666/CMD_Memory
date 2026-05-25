# CausalOS: Causal Memory Layer for AI Agents

GitHub: CausalOS/causalos-python (2026-04)

## Core Contribution

Stateful causal memory layer capturing action-outcome chains. "Prevents recurring failures by building a graph of action-outcome chains." Causal Guard proactively blocks risky actions based on past failure records.

## Key Features
- **Causal Records**: Link actions to outcomes with semantic search
- **Causal Guard**: Warn or block risky actions before execution
- **Causal Pools**: Shared experience database across agents
- **Downstream Effects**: Track delayed impacts
- **CLI Audit**: `causal-os view --db`, `causal-os recall "query"`

## CMD Relevance

**Engineering implementation of CMD's Failure Memory concept.** CausalOS's "action-outcome chains → prevent recurrence" mirrors CMD's "ECS records → Failure Memory retrieval."

**Key differences:**
- CausalOS: action-level (what the agent did), preventive blocking
- CMD: operation-level (which memory operation failed), diagnostic attribution + repair
- CausalOS stores raw action→outcome; CMD stores structured ECS (error + cause + corrected_memory + repair_guidance)

**Complementary:** CMD diagnoses which memory operation failed → CausalOS stores the causal chain for future blocking. Validates the "never make the same mistake twice" pattern from a causal reasoning perspective.
