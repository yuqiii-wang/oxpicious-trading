"""Candidate — data definition.

Migrated verbatim from :mod:`strategy.factors_and_algos._optm_engine.training.trainer` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class Candidate:
    """One top-K Set A param set (step 2), enriched with Kelly (step 3)."""

    trial_no: int
    #: Set A params (conf_threshold + algo model params).
    params: Dict[str, Any]
    #: Stage A loss bundle (OmegaLoss evaluate output).
    metrics: Dict[str, Any]
    #: Analytical Kelly outcome from this candidate's raw signal returns.
    kelly: Optional[KellyResult] = None
