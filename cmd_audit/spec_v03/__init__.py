"""Executable, fail-closed contracts for the Mix GHOST v0.3 protocol.

This namespace intentionally does not import ``artifacts`` or the retired
``experiments`` implementations.  It is a small integration surface for the
normative v0.3 specification.
"""

from .contracts import (
    DecisionView,
    EvaluatorOnly,
    RepairCase,
    SkillEvidenceState,
    SkillSpec,
    deserialize_decision_view,
)
from .ecology_runtime import EcologyRuntime
from .experiment_matrix import build_experiment_matrix
from .freeze import FreezeConfig, compile_freeze_bundle
from .runtime_pipeline import RuntimePipeline
from .stage59_runner import Stage59Capabilities, Stage59Config, Stage59Runner
from .system_runtime import PrequentialCMDRuntime, VersionedMemoryStore

__all__ = [
    "DecisionView",
    "EvaluatorOnly",
    "RepairCase",
    "SkillEvidenceState",
    "SkillSpec",
    "deserialize_decision_view",
    "EcologyRuntime",
    "FreezeConfig",
    "PrequentialCMDRuntime",
    "RuntimePipeline",
    "Stage59Capabilities",
    "Stage59Config",
    "Stage59Runner",
    "VersionedMemoryStore",
    "build_experiment_matrix",
    "compile_freeze_bundle",
]
