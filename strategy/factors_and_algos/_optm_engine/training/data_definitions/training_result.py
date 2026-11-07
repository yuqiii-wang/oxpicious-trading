"""TrainingResult — data definition.

Migrated verbatim from :mod:`strategy.factors_and_algos._optm_engine.training.trainer` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class TrainingResult:
    """Final nested-training outcome (step 5)."""

    #: Combined best params (Set A ∪ Set B) — what gets upserted.
    best_params: Dict[str, Any]
    best_a_params: Dict[str, Any]
    best_b_params: Dict[str, Any]
    best_a_metrics: Dict[str, Any]
    best_b_metrics: Dict[str, Any]
    #: Kelly outcome of the winning candidate (sizing static, reported).
    kelly: Optional[KellyResult]
    winner_trial_no: int
    n_candidates: int
    grid_size: int
    #: Full-series sanity check of the winner (report only).
    full_series_metrics: Optional[Dict[str, Any]] = None
