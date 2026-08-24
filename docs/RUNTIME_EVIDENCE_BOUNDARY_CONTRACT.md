# Runtime Evidence Boundary Contract

This document is the authoritative contract for CMD runtime behavior and
evaluation evidence. It applies to every router, memory mutation path,
counterfactual evaluator, and benchmark adapter.

## Runtime boundary

Runtime inputs are deployment-visible state plus a typed repair receipt. Runtime
code must not read, derive from, or route on dataset gold answers, labels,
reference answers, split identifiers, or post-outcome annotations. Project
memory is context, never ground truth.

The router consumes only a repair receipt: the typed, provenance-bound record
of an executed or evaluated repair transition. It must not consume a benchmark
label, a gold-derived reward, or an answer-replay result as a routing feature.
The concrete runtime ABI is `EccSyndrome` -> shadow transition ->
`EccRepairReceipt`, produced and settled by `MemAuditEccAdapter`. Runtime
provenance is recursively gold-free; nesting a sealed field inside a generic
metadata object does not bypass the firewall.

## Counterfactual boundary

`counterfactual` means an ECC shadow transition: execute or evaluate a
candidate repair against the frozen pre-repair state in a separate shadow lane,
then record the receipt and lineage. It does **not** mean replaying an answer
on the same trace with altered context, and same-trace answer replay cannot be
used as counterfactual causal evidence.

## Evaluation boundary

Dataset labels, gold answers, and reference answers are sealed offline-audit
inputs only. They may score a completed, root-bound shadow artifact after the
runtime boundary, but may not flow back into runtime retrieval, mutation,
selection, context construction, receipt creation, or online router updates.

Any result that cannot prove this separation is protocol-invalid and cannot
support a runtime, causal, or headline claim.

## P4C execution binding

The compatibility implementation of this contract is
`experiments/p4c_ecc_runner.py`; its public, non-phase-specific API is exported
from `experiments/ecc_memory_runtime.py`, and the runnable harness entrypoint is
`experiments/run_ecc_memory_runtime.py`. Its runtime input is a closed incident overlay,
not the raw dataset or sealed sidecar. Its durable completion authority binds
the frozen case-stream root, each `EccRepairReceipt`, the incident-ledger head,
and the receipt-only router snapshot. `audit_p4c_run` is a separate post-runtime
consumer; its output is never a router update input.
