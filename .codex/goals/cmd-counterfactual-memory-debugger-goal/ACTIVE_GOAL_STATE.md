---
status: active
owner_mode: goal
objective: "Improve this project through bounded, verified goal segments."
updated_at: 2026-08-21T15:19:24+08:00
adapter_id: cmd-counterfactual-memory-debugger-goal
---

# Active Goal State

## Objective

Improve this project through bounded, verified goal segments.

## Authority Sources

- No explicit goal document was provided during bootstrap.

## Operating Contract

- Treat this file as the durable goal state for future agent ticks.
- Treat the authority sources above as the first context to inspect before acting.
- Read current project evidence before choosing the next action.
- Run a bounded progress segment when useful; it does not have to be one tiny step.
- Keep private evidence, credentials, local paths, and raw logs out of public commits.
- End each tick with changed files, validation, residual risk, and the next action.

## Execution Profile

- `cadence=bounded_progress_segment minimum=multi_surface_or_implementation include=coherent_artifact,targeted_validation,state_writeback spend_rule=spend_only_after_artifact_validation_writeback small_streak_threshold=2`
- Repeated small-scale follow-through should expand the next delivery batch or report a blocker before spending quota.

## Non-Goals

- Do not perform irreversible production operations without explicit approval.
- Do not publish private project evidence.
- Do not optimize for activity if no useful artifact or decision can be produced.

## Onboarding Control

- Fast repository scan: `enabled`.
- Scan read file bodies: `False`.
- Git repo detected: `True`.
- Local change count from `git status --short`: `22`.
- Recent commits sampled: `5`.
- Project signal files: `pyproject.toml, package.json`.
- Validation signal files: `pyproject.toml, package.json`.
- Candidate agent todos: `requires user selection before delivery work`.
- Autonomous advancement: `requires an explicit user yes/no choice`.
- Codex App heartbeat: `requires explicit heartbeat=yes/no before a recurring Codex App automation is installed`.

## Proposed Onboarding Candidates

1. [P1] Inspect current uncommitted changes (M cmd_audit/counterfactual/actions.py, MM cmd_audit/repair/ghost_ecology.py, M cmd_audit/repair/skill_ecology.py, M experiments/ghost_ecology_zero_call.py, M experiments/v4_prequential_runner.py) and decide what belongs in the first LoopX segment before editing.
   - reason: The repo already has local changes, so the first safe step is ownership and scope classification.
   - metadata: `advancement_task:repo_status_review`
2. [P1] Summarize the last 5 commits and extract the safest next bounded project follow-up.
   - reason: Recent commits are a fast signal of current project direction without reading private bodies.
   - metadata: `advancement_task:commit_summary`
3. [P1] Identify the fastest validation command from pyproject.toml, package.json and record whether it is safe to run now.
   - reason: Validation entrypoints are visible from top-level project metadata.
   - metadata: `advancement_task:validation_plan`
4. [P2] Build a compact read-only project map from pyproject.toml, package.json and note authority sources, risks, and first useful handoff.
   - reason: Top-level project files can seed a useful map before any implementation work.
   - metadata: `advancement_task:read_only_map`


## User Todo / Owner Review Reading Queue

- [x] [P1] Choose which proposed onboarding agent todos to accept and whether Codex may start autonomous advancement and whether to enable the Codex App heartbeat; reply with accepted numbers plus autonomous=yes/no plus heartbeat=yes/no.
  <!-- loopx:todo todo_id=todo_ff948e17985e status=done task_class=user_gate action_kind=onboarding_decision decision_outcome=approve completion_continuation=active_goal evidence=Owner%20approved%20onboarding%20candidate%203%2C%20autonomous%20advancement%2C%20and%20Codex%20App%20heartbeat%20on%202026-08-21. completed_at=2026-08-21T12:03:48%2B08:00 updated_at=2026-08-21T12:03:48%2B08:00 -->

## Agent Todo

- [x] [P1] Present the onboarding scan and ask which candidate agent todos to accept, whether autonomous advancement may start, whether to enable the Codex App heartbeat before delivery work. If heartbeat=yes, create or update the Codex App heartbeat from an identity-scoped `loopx heartbeat-prompt --thin` task body before claiming recurring automation is active.
  <!-- loopx:todo todo_id=todo_cefd5ac318f0 status=done task_class=advancement_task action_kind=onboarding_todo_review completion_continuation=active_goal evidence=Owner%20accepted%20candidate%203%2C%20autonomous%3Dyes%2C%20heartbeat%3Dyes%3B%20accepted%20candidate%20added%20as%20todo_e65357a63032. completed_at=2026-08-21T12:04:18%2B08:00 updated_at=2026-08-21T12:04:18%2B08:00 -->
- [x] [P0] Diagnose the v4-confirm-002 primary, calibration, and typed-feedback gate failures and bind each cause to evidence and responsible code or protocol.
  <!-- loopx:todo todo_id=todo_05c93a30d179 status=done task_class=advancement_task action_kind=diagnose_experiment_failure claimed_by=codex-app-experiment-diagnosis-20260821 successor_todo_ids=todo_2f2236c733d7 completion_continuation=successor target_key=v4-confirm-002-gate-causal-chain note=Bounded%20causal%20analysis%20completed:%20typed%20feedback%20starvation%20causes%20three%20primary%20arms%20to%20remain%20identical%3B%20prospective%20GHOST%20feedback%20remains%20entirely%20pending. evidence=artifacts%2Fv4-confirm-002%2Freview%2Ffailure_analysis_and_remediation.md completed_at=2026-08-21T14:01:14%2B08:00 updated_at=2026-08-21T14:01:14%2B08:00 completion_turn_key=cmd-counterfactual-memory-debugger-goal:codex-app-experiment-diagnosis-20260821:todo_05c93a30d179:repair-20260821-01 -->
- [x] [P1] Define focused tests, coverage gates, and a small prequential smoke protocol that must pass before another full materialization run.
  <!-- loopx:todo todo_id=todo_71b4f55a8d7e status=done task_class=advancement_task action_kind=plan_bounded_validation claimed_by=codex-app-experiment-diagnosis-20260821 completion_continuation=active_goal target_key=v4-confirm-002-fix-validation note=superseded reason=Reordered%20to%20preserve%20planner%20order:%20remediation%20design%20must%20precede%20validation%20planning. completed_at=2026-08-21T11:53:42%2B08:00 updated_at=2026-08-21T11:53:42%2B08:00 -->
- [x] [P1] Design minimal corrections for typed evidence coverage, fresh-replay provenance binding, and prospective policy updates without admitting shadow outcome leakage.
  <!-- loopx:todo todo_id=todo_2f2236c733d7 status=done task_class=advancement_task action_kind=design_experiment_remediation claimed_by=codex-app-experiment-diagnosis-20260821 successor_todo_ids=todo_a4f9559f535e completion_continuation=successor target_key=v4-confirm-002-remediation-plan note=Leakage-safe%20remediation%20design%20completed:%20typed%20follow-up%20lineage%2C%20settlement%20ledger%2C%20manifest%20binding%2C%20stratified%20metrics%2C%20and%20pre-full-run%20mechanism%20gates. evidence=artifacts%2Fv4-confirm-002%2Freview%2Ffailure_analysis_and_remediation.md completed_at=2026-08-21T14:12:39%2B08:00 updated_at=2026-08-21T14:12:39%2B08:00 completion_turn_key=cmd-counterfactual-memory-debugger-goal:codex-app-experiment-diagnosis-20260821:todo_2f2236c733d7:postrepair-20260821-01 -->
- [x] [P1] After the remediation design, define focused tests, coverage gates, and a small prequential smoke protocol required before another full materialization run.
  <!-- loopx:todo todo_id=todo_a4f9559f535e status=done task_class=advancement_task action_kind=plan_bounded_validation task_repository=git:github.com%2Fsupremewen666%2FCMD_Memory required_write_scopes=repo claimed_by=codex-app-experiment-diagnosis-20260821 successor_todo_ids=todo_80be731c7774 completion_continuation=successor target_key=v4-confirm-002-fix-validation-v2 note=Focused%20seams%2C%20Tier%201%20settlement%20integration%20smoke%2C%20manifest-bound%20E2%20smoke%2C%20and%20full-run%20admission%20gates%20are%20defined. evidence=artifacts%2Fv4-confirm-002%2Freview%2Fvalidation_and_smoke_protocol.md completed_at=2026-08-21T14:22:21%2B08:00 updated_at=2026-08-21T14:22:21%2B08:00 completion_turn_key=cmd-counterfactual-memory-debugger-goal:codex-app-experiment-diagnosis-20260821:todo_a4f9559f535e:user-nextstep-20260821-01 -->
- [x] [P1] Identify the fastest validation command from pyproject.toml and package.json and record whether it is safe to run now.
  <!-- loopx:todo todo_id=todo_e65357a63032 status=done task_class=advancement_task action_kind=validation_plan task_repository=git:github.com%2Fsupremewen666%2FCMD_Memory required_write_scopes=repo claimed_by=codex-app-experiment-diagnosis-20260821 successor_todo_ids=todo_80be731c7774 completion_continuation=successor target_key=project-minimal-validation-command note=Fastest%20safe%20local%20command%20identified%3B%20all%20targets%20exist%3B%20execution%20is%20capability-blocked%20by%20missing%20terra_medium. evidence=artifacts%2Fv4-confirm-002%2Freview%2Fminimal_validation_command.md completed_at=2026-08-21T14:27:58%2B08:00 updated_at=2026-08-21T14:27:58%2B08:00 completion_turn_key=cmd-counterfactual-memory-debugger-goal:codex-app-experiment-diagnosis-20260821:todo_e65357a63032:2026-08-21T06:24:14.630Z -->
- [x] [P0] Implement the typed feedback settlement integration smoke, manifest-bound E2 guard, and focused validation commands from validation_and_smoke_protocol.md.
  <!-- loopx:todo todo_id=todo_80be731c7774 status=done task_class=advancement_task action_kind=implement_validation_smoke task_repository=git:github.com%2Fsupremewen666%2FCMD_Memory continuation_policy=same_agent_non_delivery required_write_scopes=repo required_capabilities=terra_medium target_capabilities=terra_medium claimed_by=codex-app-experiment-diagnosis-20260821 successor_todo_ids=todo_a330e3bcf777 completion_continuation=successor target_key=v4-feedback-settlement-smoke-implementation-v2 note=superseded reason=The%20prior%20todo%20was%20incorrectly%20marked%20capability-found%20while%20terra_medium%20was%20absent%3B%20use%20its%20linked%20clean%20replacement. completed_at=2026-08-21T14:34:31%2B08:00 updated_at=2026-08-21T14:34:31%2B08:00 -->
- [x] [P0] Implement v2 settlement smoke tests and manifest-bound validation only after the host actually exposes terra_medium.
  <!-- loopx:todo todo_id=todo_a330e3bcf777 status=done task_class=advancement_task action_kind=implement_validation_smoke task_repository=git:github.com%2Fsupremewen666%2FCMD_Memory continuation_policy=same_agent_non_delivery required_write_scopes=repo required_capabilities=terra_medium claimed_by=codex-app-experiment-diagnosis-20260821 successor_todo_ids=todo_906ad07c214d completion_continuation=successor target_key=v4-feedback-settlement-smoke-implementation-v3 note=Clean%20replacement%20for%20todo_80be731c7774%3B%20no%20capability-found%20assertion. evidence=Tier1%20settlement%20smoke:%2014%20passed%3B%20Tier0:%207%20passed%3B%20manifest%20mismatch%20gate:%201%20passed%3B%20files:%20experiments%2Fv4_feedback_settlement.py%2C%20experiments%2Fv4_prequential_runner.py%2C%20tests%2Fexperiments%2Ftest_v4_feedback_settlement_smoke.py%2C%20tests%2Fexperiments%2Ftest_e2_typed_identifiability.py completed_at=2026-08-21T15:18:38%2B08:00 updated_at=2026-08-21T15:18:38%2B08:00 completion_turn_key=cmd-counterfactual-memory-debugger-goal:codex-app-experiment-diagnosis-20260821:todo_a330e3bcf777:2026-08-21T06:37:14.636Z -->
- [ ] [P0] Add explicit runner resume mode with verified policy/repository snapshots, CLI follow-up ingestion, and an append-only manifest-bound settlement audit artifact.
  <!-- loopx:todo todo_id=todo_906ad07c214d status=open task_class=advancement_task action_kind=implement_runner_resume_and_settlement_audit task_repository=git:github.com%2Fsupremewen666%2FCMD_Memory continuation_policy=independent_handoff required_capabilities=terra_medium claimed_by=codex-app-experiment-diagnosis-20260821 unblocks_todo_id=todo_a330e3bcf777 updated_at=2026-08-21T15:18:38%2B08:00 -->

## Next Action

- Execute todo_906ad07c214d: add verified runner resume mode, CLI follow-up ingestion, and a manifest-bound settlement audit artifact under Terra medium.

## Recent User Feedback

- Initialized by `loopx bootstrap`.

## Progress Ledger

- Created the initial goal state and registry connection.
