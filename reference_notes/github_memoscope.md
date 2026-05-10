# GitHub: eth-jashan/MemoScope — AI Agent Memory Debugger

- **GitHub**: eth-jashan/MemoScope
- **Updated**: 2026-01-09
- **Core Contribution**: Framework-agnostic memory debugger for AI agents. Captures memory events (insert, retrieve, update, delete), provides retrieval explanations (query/score/rank), timeline visualization, and memory diffs between turns. Monorepo: Hono API + React dashboard + SDK + Mem0 adapter.
- **Key Concepts**: Memory event capture, retrieval explanation, timeline visualization, memory diff, framework adapters.
- **CMD Relevance**: Validates engineering demand for dedicated memory debugging tools. Observational model (capture → visualize → explain) confirms CMD's unique position: no counterfactual replay, no automated failure attribution, no operation-level diagnosis. MemScope answers "what happened" but not "which operation failed and how to fix it."
- **Open Gap**: Purely observational. Adding CMD-Audit as a MemScope plugin would give it counterfactual attribution capability — a potential V1 integration path.
