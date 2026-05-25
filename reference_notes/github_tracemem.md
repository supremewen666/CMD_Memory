# github_tracemem — trace-mem: Verifiable Memory with Counterfactual Admission Gate

- **Source**: github.com/bettyguo/agent-memory (2026-05-14)
- **Core contribution**: Memory framework for LLM agents where every stored item is verifiable and counterfactually justified. HMAC-signed citations back to originating trajectory span. Counterfactual gate: "admit only if a held-out probe set's accuracy improves by ≥ ε". Non-redundancy gate rejects near-identical items. Tamper detection filters out memory whose source content was mutated.
- **Key concepts**: verifiable memory, HMAC-signed provenance, counterfactual admission gate, non-redundancy gate, tamper detection, trajectory-span citation.
- **CMD relevance**: Counterfactual gate at ingestion (preventive) complements CMD's counterfactual replay at diagnosis (retrospective). trace-mem prevents bad memories from entering; CMD diagnoses which operation caused failure after it happened. Together they cover the full prevention→diagnosis spectrum. The counterfactual gate concept ("admit if accuracy improves") is similar in spirit to CMD's Recovery Gain but applied at write-time rather than diagnosis-time.
- **Threat model**: v0.1 defends against cross-trajectory replay and post-hoc tampering, not in-process key extraction.
- **Adapters**: LangChain, LlamaIndex, MCP server.
