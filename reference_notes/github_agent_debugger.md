# Peaky Peek (agent_debugger) Note

- GitHub: acailic/agent_debugger (MIT, 5 stars, v0.1.18)
- Core contribution: local-first agent debugger with replay, failure memory, decision tree visualization, and drift detection. Four-layer architecture: SDK instrumentation, Intelligence (pattern detection, failure memory, replay engine), API server (FastAPI+SSE), React frontend with 8 panels.
- Key concepts: checkpoint replay ranked by restore value, failure clustering, session comparison, decision tree visualization, privacy-first (SQLite WAL, no cloud), framework adapters (PydanticAI, LangChain, OpenAI SDK, CrewAI, AutoGen, LlamaIndex, Anthropic).
- CMD relevance: strikingly close to CMD's surface goals (replay, failure memory, attribution) but fundamentally different approach: Peaky Peek does interactive visual debugging with human-in-the-loop, while CMD does automated counterfactual attribution with Recovery Gain scoring. Peaky Peek validates engineering demand for CMD-style tooling.
- Open gap: Peaky Peek's replay is checkpoint restoration, not counterfactual memory operation replay; it cannot answer "which memory operation caused this failure." CMD's counterfactual replay portfolio fills this gap.
