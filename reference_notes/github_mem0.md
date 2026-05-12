# mem0: Universal Memory Layer for AI Agents

GitHub: mem0ai/mem0
Stars: 55,320
Language: Python
YC: S24

## Core Contribution

mem0 is the most popular open-source agent memory layer. v3 algorithm (April 2026) uses single-pass ADD-only extraction with multi-signal retrieval (semantic + BM25 + entity matching). SOTA on LoCoMo (91.6) and LongMemEval (93.4).

## Key Concepts

- **ADD-only extraction**: one LLM call, no UPDATE/DELETE. Memories accumulate; nothing is overwritten.
- **Entity linking**: entities extracted, embedded, and linked across memories for retrieval boosting.
- **Multi-signal retrieval**: semantic, BM25 keyword, and entity matching scored in parallel and fused.
- **Minimal API**: `add()`, `search()`, `update()`, `delete()`.

## CMD Relevance

- mem0's explicit write/retrieve boundary maps cleanly to CMD's counterfactual replay portfolio.
- `add()` interception → Oracle Write replay; `search()` interception → Oracle Retrieval replay.
- mem0 is the recommended first CMD-Skill Adapter target (V1).
- ADD-only extraction model is the simplest intervention point for diagnosing `premature_extraction_error`.

## Open Gap

mem0 provides no memory failure diagnosis or attribution. When retrieval fails, there is no mechanism to distinguish write-time extraction loss from retrieval ranking failure. CMD-Audit fills this gap.
