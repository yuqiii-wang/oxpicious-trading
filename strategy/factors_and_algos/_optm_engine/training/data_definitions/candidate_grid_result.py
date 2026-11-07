"""CandidateGridResult — data definition.

Migrated verbatim from :mod:`strategy.factors_and_algos._optm_engine.training.trainer` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class CandidateGridResult:
    """One candidate's best grid point from step 4."""

    candidate: Candidate
    #: Best Set B execution params for this candidate.
    set_b_params: Dict[str, Any]
    #: Stage B loss bundle (CalmarLoss evaluate output) at that point.
    metrics: Dict[str, Any]
