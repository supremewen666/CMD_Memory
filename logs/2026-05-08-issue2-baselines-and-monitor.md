# 2026-05-08 Issue 0002 Baselines and Monitor

Implemented issue 0002 for the standalone CMD-Audit harness.

Added code:

- `cmd_audit/baselines.py`: fixed-summary/vector baseline validation, evidence-recall comparator, subagent-judge comparator, deterministic random-label baseline, and leak-safe monitor payload validation.
- `cmd_audit/metrics.py`: diagnosis prediction rows plus attribution accuracy, macro F1, top-2 accuracy, and cost-per-diagnosis metrics.
- `cmd_audit/harness.py`: `AuditResult.baseline_suite`, `diagnosis_predictions`, and comparison metrics CSV writer.
- `cmd_audit/cli.py`: writes both attribution and comparison-metrics artifacts.
- `tests/test_cmd_audit_issue2_baselines.py`: issue 0002 behavior tests.

Boundary decision preserved:

- subagent judge baseline is a comparator, not CMD attribution;
- subagent judge monitor can trigger replay but cannot emit final labels, ECS, memory writes, gold answers, or full failed traces;
- CMD attribution remains replay-delta grounded.

Verification:

```bash
python3 -m unittest discover -s tests -v
python3 -m cmd_audit run
```
