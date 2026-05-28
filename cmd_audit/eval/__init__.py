"""Evaluation subpackage: metrics, agreement, bootstrap, writers, provenance, surrogate gap, release gates."""

from .agreement import cohen_kappa
from .bootstrap import bootstrap_metric
from .metrics import (
    DiagnosisMetrics,
    DiagnosisPrediction,
    compute_diagnosis_metrics,
)
from .provenance import (
    ProvenanceTracker,
    compute_provenance_completeness,
    detect_tamper,
    get_graph_distractor_edges,
    record_provenance_edge,
)
from .release_gates import (
    GateCriterion,
    GateResult,
    GateReview,
    V0V1_CRITERION_IDS,
    check_v0_to_v1_gate,
    check_v1_to_v2_gate,
    write_gate_review,
    write_gate_status,
)
from .surrogate_gap import (
    GOLD_DEPENDENT_LABELS,
    SurrogateGapRow,
    SurrogateGapSummary,
    compute_surrogate_gap_summary,
    measure_surrogate_gap,
    measure_surrogate_gaps,
)
from .writers import (
    REPLAY_TABLE_ORDER,
    write_attribution_table,
    write_confusion_matrix_table,
    write_csv_table,
    write_post_repair_table,
    write_provenance_completeness_summary,
    write_retrieval_metrics_table,
    write_retrieval_trace_table,
    write_text_artifact,
)

__all__ = [
    "DiagnosisMetrics",
    "DiagnosisPrediction",
    "GOLD_DEPENDENT_LABELS",
    "GateCriterion",
    "GateResult",
    "GateReview",
    "ProvenanceTracker",
    "REPLAY_TABLE_ORDER",
    "SurrogateGapRow",
    "SurrogateGapSummary",
    "V0V1_CRITERION_IDS",
    "bootstrap_metric",
    "check_v0_to_v1_gate",
    "check_v1_to_v2_gate",
    "cohen_kappa",
    "compute_diagnosis_metrics",
    "compute_provenance_completeness",
    "compute_surrogate_gap_summary",
    "detect_tamper",
    "get_graph_distractor_edges",
    "measure_surrogate_gap",
    "measure_surrogate_gaps",
    "record_provenance_edge",
    "write_attribution_table",
    "write_confusion_matrix_table",
    "write_csv_table",
    "write_gate_review",
    "write_gate_status",
    "write_post_repair_table",
    "write_provenance_completeness_summary",
    "write_retrieval_metrics_table",
    "write_retrieval_trace_table",
    "write_text_artifact",
]
