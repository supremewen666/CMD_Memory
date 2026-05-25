# github_debugmind — DebugMind: Experiential Memory for Bug Diagnosis

- **Source**: github.com/zavoryn/debug-mind (2026-05-18)
- **Core contribution**: AI-powered bug diagnosis agent with experiential memory. Hybrid storage (ChromaDB vector search + Markdown human-readable). Four layers: Memory, Skills, Diagnosis Engine, MCP Server. "The more bugs it sees, the faster it gets."
- **Key concepts**: experiential memory, hybrid vector+keyword retrieval, past diagnosis reuse, systematic RCA, MCP-compatible.
- **CMD relevance**: Engineering analogue of CMD's Failure Memory loop. DebugMind stores past bug diagnoses and retrieves them for similar new bugs; CMD stores ECS records and retrieves them for similar memory failures. DebugMind is code-bug-focused, less structured (free-text Markdown), and lacks automated attribution. CMD is memory-operation-focused, structured (ECS taxonomy), and includes automated counterfactual attribution + Post-Repair quality gate.
- **Differentiation**: DebugMind diagnoses code bugs reactively (search past cases); CMD diagnoses memory operation failures proactively (counterfactual replay → attribution → ECS).
