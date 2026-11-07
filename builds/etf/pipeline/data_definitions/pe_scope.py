"""PeScope — data definition.

Migrated verbatim from :mod:`builds.etf.pipeline.pe_scope` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import numpy as np
import pandas as pd


@dataclass
class PeScope:
    """Shared mask machinery + PE-augmented frame."""
    merged: pd.DataFrame = None              # type: ignore[assignment]
    comp_latest: pd.DataFrame = field(default_factory=pd.DataFrame)
    in_range: np.ndarray = None              # type: ignore[assignment]
    exists: np.ndarray = None                # type: ignore[assignment]
    split_hit: np.ndarray = None              # type: ignore[assignment]
    split_mask: np.ndarray = None            # type: ignore[assignment]
    pe_null_df: Optional[pd.DataFrame] = None
    pe_null_hit: np.ndarray = None            # type: ignore[assignment]
    forced_mask: np.ndarray = None            # type: ignore[assignment]
    cand: np.ndarray = None                   # type: ignore[assignment]
