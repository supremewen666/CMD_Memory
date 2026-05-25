# github_traceaudit — TraceAudit: Counterfactual Auditing of Agentic RAG

- **Source**: github.com/kaanrkaraman/traceaudit-paper (2026-05-16)
- **Core contribution**: Pre-registered research project on counterfactual auditing of agentic RAG systems (CRAG, IRCoT, Self-RAG, FLARE). Three intervention modes (A: fixed-policy ablation, B: counterfactual rollout, C: step-truncation), three operators (remove, paraphrase, distract). URR (Usefulness via Removal Ratio) as headline metric.
- **Key concepts**: counterfactual auditing, URR, chunk-level intervention, pre-registered hypotheses, trace replay determinism, calibrated judge, Phase 0-4 plan through August 2026.
- **CMD relevance**: DIRECTLY competitive in counterfactual approach. TraceAudit = chunk-level counterfactual auditing of external RAG systems; CMD = operation-level counterfactual diagnosis of own memory pipeline. TraceAudit removes chunks and measures answer change (audit); CMD replays with oracle memory operations and measures Recovery Gain (diagnose+repair). Different intervention granularity, different end goal (audit vs repair). Both use counterfactual as core methodology — validates counterfactual as the right foundation.
- **Differentiation**: (1) Granularity: chunk vs operation. (2) Purpose: audit/report vs diagnose/repair. (3) Target: RAG retrieval chunks vs agent memory pipeline operations. (4) CMD adds Post-Repair quality gate and Failure Memory loop — TraceAudit is audit-only.
- **Budget**: $2000 USD API cap, GPT-4o reserved for calibrated judge, Azure-hosted Llama for heavy replay.
- **Target venues**: AAAI 2027 primary, ECIR 2027 fallback.
