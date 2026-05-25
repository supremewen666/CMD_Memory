# Rewind: Time-Travel Debugger for AI Agents

GitHub: agentoptics/rewind (2026-04)

## Core Contribution

"Chrome DevTools for AI agents." Fork at any failure, replay with the fix, prove it works. Key command: `rewind fix` — LLM diagnoses the failure, suggests a fix (model swap, system prompt, temperature, retry), and optionally forks + replays with the patch to verify.

## Key Features
- **Fork & Replay**: Branch timeline at any step; steps before fork from cache (0 tokens, 0ms)
- **Prove the Fix**: Score original vs forked with LLM-as-judge (correctness, coherence, safety, relevance)
- **Import & Debug**: Import from Langfuse, Datadog, OTel backends
- **`rewind fix`**: One command from "broken" to "proven fix"

## CMD Relevance

**Closest engineering analogue to CMD's full diagnosis→repair→validate loop:**
- `rewind fix` diagnosis ≈ CMD's attribution
- `rewind fix` suggested fix ≈ CMD's ECS
- `rewind replay` with patch ≈ CMD's Post-Repair Context Replay
- `rewind eval score` ≈ CMD's repair_assessment

**Key difference:** Rewind operates at agent-step level (replace model, change prompt, retry). CMD's unique claim is memory-pipeline-specific attribution at operation granularity. Rewind validates the engineering demand for automated diagnosis→repair→verify loops.
