"""Public names for the MemAudit -> ECC -> GHOST receipt runtime.

The implementation was originally introduced under the P4C experiment name.
These aliases expose the runtime ABI without making a benchmark phase name part
of the production-facing interface.
"""

from experiments.p4c_ecc_runner import (
    P4cEccCase as EccRuntimeCase,
    P4cEccRunner as EccRuntimeRunner,
    P4cGhostBinding as EccGhostBinding,
    P4cGhostRouter as EccGhostRouter,
    P4cRepairCandidate as EccRepairCandidate,
    audit_p4c_run as audit_ecc_runtime,
    load_p4c_cases as load_ecc_runtime_cases,
)
from experiments.p4c_zero_call import (
    StructuralEccEvaluator,
    StructuralMemoryStore,
)

__all__ = [
    "EccGhostBinding",
    "EccGhostRouter",
    "EccRepairCandidate",
    "EccRuntimeCase",
    "EccRuntimeRunner",
    "StructuralEccEvaluator",
    "StructuralMemoryStore",
    "audit_ecc_runtime",
    "load_ecc_runtime_cases",
]
