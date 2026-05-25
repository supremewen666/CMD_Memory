"""Experiment scripts for CMD paper evidence.

experiment_01: Context Construction 4-Mode Comparison
experiment_02: CMD Attribution (planned)

Data pipeline: download_datasets -> clean_datasets -> context_construction
"""

from .clean_datasets import clean_all
from .context_construction import (
    ContextCase,
    build_context_cases,
    load_context_cases,
    save_context_cases,
)
from .download_datasets import download_all

__all__ = [
    "ContextCase",
    "build_context_cases",
    "clean_all",
    "download_all",
    "load_context_cases",
    "save_context_cases",
]
