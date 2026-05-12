# Memory-Probe: Diagnosing Retrieval vs. Utilization Bottlenecks in LLM Agent Memory

arXiv: 2603.02473
Authors: Boqin Yuan, Yue Su, Kun Yao
Date: 2026-03-02
Code: github.com/boqiny/memory-probe

## Core Contribution

A diagnostic framework that separates retrieval quality from write quality in agent memory. 3x3 study crossing three write strategies (raw chunks, Mem0-style fact extraction, MemGPT-style summarization) with three retrieval methods (cosine, BM25, hybrid reranking) on LoCoMo.

## Key Findings

- Retrieval method is the dominant factor: 20-point accuracy span across retrieval methods (57.1% to 77.2%) vs only 3-8 points across write strategies.
- Raw chunked storage (zero LLM calls) matches or outperforms expensive lossy extraction, suggesting current memory pipelines discard useful context.
- Failures most often manifest at retrieval stage rather than utilization.

## CMD Relevance

- **Directly adjacent work**: independent diagnostic framework for agent memory failure, closest in spirit to CMD.
- **Method is observational** (3x3 grid comparison), not counterfactual. CMD's replay intervention provides stronger causal evidence.
- **Validates `premature_extraction_error`**: finding that "raw chunks outperform lossy extraction" is empirical evidence that extraction-stage information loss is a real failure mode.
- **Names mem0 and MemGPT as target systems**, same as CMD's V1 adapter targets.
- Should be cited as the closest existing diagnostic approach; CMD's counterfactual replay is the natural next step beyond grid-comparison diagnosis.

## Open Gap

Grid-comparison diagnosis identifies whether retrieval or write matters more at the aggregate level, but cannot attribute individual failures to specific memory operations. CMD's case-level counterfactual replay fills this gap.
