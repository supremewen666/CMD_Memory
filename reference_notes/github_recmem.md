# github_recmem — RecMem: Recurrence-based Memory Consolidation (ACL 2026 Findings)

- **Source**: github.com/CaiusDai/RecMem (2026-05-15)
- **Core contribution**: Rethinks when and how memory consolidation happens. Defers LLM consolidation until recurrence indicates content is worth promoting (semantic similarity clustering in subconscious buffer). Dual extraction: event summaries + atomic facts. Solves eager-consolidation waste and single-facet extraction information loss.
- **Key concepts**: recurrence-based consolidation, subconscious buffer, deferred LLM extraction, multi-facet extraction, embedding-based indexing without LLM cost.
- **CMD relevance**: Prevention-oriented approach to the problems CMD diagnoses. RecMem's "eager consolidation wastes tokens" validates `compression_error` (unnecessary compression). RecMem's "single-facet extraction loses details" validates `premature_extraction_error` (information lost at extraction). RecMem prevents these errors; CMD diagnoses them after they occur. RecMem's recurrence trigger is a preventive heuristic — CMD could use it as a pre-filter to reduce attribution search space.
- **Status**: Accepted to ACL 2026 Findings. Open source.
