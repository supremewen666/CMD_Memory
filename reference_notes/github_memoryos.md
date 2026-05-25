# GitHub: Per0x1de-1337/MemoryOS

- **来源**: GitHub repo, created 2026-05-08. 9 stars. Python.
- **核心贡献**: "AI agent memory. Temporal knowledge graph + hybrid vector retrieval + Ebbinghaus decay." Append-only graph preserves history (old facts superseded, not deleted). Query with score breakdown and reasoning trace. Claims 78ms queries (warm p50), 9ms/msg ingest on Azure D48ads-v5. Uses pgvector + Redis + Cohere rerank.
- **关键概念**: temporal knowledge graph, append-only memory, Ebbinghaus forgetting curve, hybrid vector retrieval, score breakdown and reasoning trace
- **CMD 相关性**: MEDIUM. Memory architecture reference. Temporal tracking (fact changes over time) is relevant to CMD's `item_stale` label. Append-only design aligns with CMD's provenance tracking needs. The performance numbers (78ms queries) show what's possible in production. The reasoning trace in retrieval results could serve as input to CMD's monitor.
- **开放空白**: No failure diagnosis or repair capability. No operation-level attribution. Pure memory storage, not memory debugging.
