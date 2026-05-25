# Intent Gap: A Taxonomy of Real-User Failure Modes in Frontier AI Agents

- **来源**: Yakhmi, Kanupriya. Zenodo preprint, 2026-05-12.
- **核心贡献**: Mines WildChat-1M and LMSYS-Chat-1M using three-stage frustration-signal filter (regex → embedding → LLM-as-judge) to surface "intent gap" failures: cases where models answer the literal prompt but miss what the user actually wanted. Replays prompts across 4 frontier models (Claude, GPT, Gemini, one open model). Triangulates taxonomy against existing failure-mode taxonomies and documented production incidents.
- **关键概念**: intent gap, frustration-signal filter, conversational repair, sycophancy, literal compliance vs intent satisfaction, researcher-imagined benchmarks vs real-user failure modes
- **CMD 相关性**: HIGH. Validates that failure mode taxonomies need real-user data. Intent Gap's user-facing failure categories (sycophancy, dialogue breakdown) are a complementary layer to CMD's memory operation failure categories. The replay-across-models methodology is structurally similar to CMD's replay-across-interventions.
- **开放空白**: Intent Gap focuses on user intent failures, not memory operation failures. CMD's memory pipeline taxonomy (6 labels) and Intent Gap's user-facing taxonomy are orthogonal failure dimensions. A compound taxonomy (intent gap × memory operation) could capture failures neither captures alone.
