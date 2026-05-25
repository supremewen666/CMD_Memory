# TraceForge: Record, Replay, Fuzz, Counterfactual Attribution

GitHub: AbhimanyuBhagwati/TraceForge (2026-02)

## Core Contribution

Test harness for agent tool-calling. Immutable content-addressed traces (SHA-256). Built-in counterfactual attribution: change one thing at a time, measure outcome sensitivity.

## Key Features
- **Replay**: re-evaluate traces offline with different expectations
- **Fuzz**: mutate tool responses (nulls, type swaps, empty strings)
- **MinRepro**: delta debugging — find the 1 step among N that matters
- **Mine**: discover behavioral rules from passing traces
- **Attribute**: counterfactual experiments — sensitivity scoring per factor

## Attribution Output
```
CAUSAL FACTOR          SENSITIVITY
tool_output_value         40%
tool_output_format         0%
system_prompt_clause       0%
```

## CMD Relevance

Counterfactual sensitivity analysis as a first-class testing feature. TraceForge's "sensitivity" = CMD's Recovery Gain (Δk) — both measure outcome change when one factor is modified. TraceForge targets tool-calling (coarse factors); CMD targets memory operations (fine-grained, 11 labels). Validates counterfactual sensitivity as an intuitive debugging primitive.
