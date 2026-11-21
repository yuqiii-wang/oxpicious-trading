"""One picked-up analysis-signal event, enriched for the comb statistics.

Data carrier only (house convention: one dataclass per file under
``data_definitions/``); extraction lives in
:mod:`strategy.factors_and_algos._comb.events`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class SignalEvent:
    """A single nonzero family-signal entry on one (code, row).

    ``quality`` is the SIGN-ALIGNED realized forward return (the repo's
    dir_ave blend over 0/3/5/10/20 rows): positive = the event's side was
    right. ``None`` until the 20-row forward window has completed.
    ``completion_date`` is the code's own trading date 20 rows after the
    event (``None`` alongside ``quality``). ``conf_bp`` is the analysis
    tier's recorded basis-point evidence (NaN when absent).
    """

    family: str
    code: str
    row_idx: int
    event_date: date
    side: str                      # "buy" | "sell"
    conf_bp: float                 # NaN when the row carries no evidence
    quality: Optional[float]
    completion_date: Optional[date]
