# Culpa: Deterministic Replay & Counterfactual Debugging for AI Agents

GitHub: AnshKanyadi/culpa (2026-03), 91 tests, Python 3, MIT License

## Core Contribution

A flight recorder for AI agents that captures every LLM call, tool invocation, file change, and terminal command. Supports deterministic replay (zero API cost via stub responses) and counterfactual forking ("what if?" experiments at any decision point).

## Architecture
- Transparent HTTP proxy between AI tool and real API — zero latency
- Sessions stored as event streams in SQLite
- SDK + proxy modes: monkey-patching LLM SDK clients or proxying HTTP
- Fork engine replays deterministically up to fork point, injects alternative response, simulates downstream

## CMD Relevance

**Directly validates CMD's counterfactual replay paradigm from the engineering tooling side.** Culpa's "fork at any point to test what-if" is the engineering implementation of CMD's counterfactual replay. Culpa targets coding agents (LLM call level); CMD targets memory pipeline operations.

**Differentiation:** Culpa = generic agent debugging (no memory-specific attribution); CMD = specialized memory pipeline attribution with structured label taxonomy and automated ECS repair.

**Risk:** As counterfactual debugging tools mature (Culpa, Rewind, TraceForge), CMD must emphasize: (1) memory-pipeline-specific labels, (2) automated ECS repair, (3) Failure Memory store-and-retrieve.
