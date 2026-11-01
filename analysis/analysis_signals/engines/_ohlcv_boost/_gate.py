"""The OHLCV-boost variant gate (analysis.analysis_signals.engines.
_ohlcv_boost._gate, 2026-10-27 — the class OOP restructure of the
flat engines/variants.py): the shared is_ohlcv_boosted axis the
family engines import. DORMANT since the 2026-11-01 variant-axes
UNLINK (user-directed "keep the pe and ohlcv boosted logic in py,
however, offload to unlink from the computation ... delete is pe and
ohlcv boosted col as from pk"): the is_ohlcv_boosted column left the
DB PKs and no engine imports this package — the logic is kept for a
future re-link (the trust-weighted measurement beside it,
measurement.py, stays ACTIVE through the OHLCV_BOOST class flag).

The gate AND-gates a family's plain candidate masks by the day's
TURNOVER-CLIMAX confirmation — the amount / 255d-MA ratio bucketed
into the int8 climax code (fetch/inputs._shared.finalize_input_frame:
0 normal / 1 >= AMT_CLIMAX_HEAVY heavy / 2 >= AMT_CLIMAX_EXTREME
climax; AXIS_MISSING -1 where the stores have no row). The
volume-confirmation literature (buying climax at blow-off tops,
capitulation volume at lows, volume-confirmed breakouts) as a VARIANT
population: the boost returned to GENERATION as this axis 2026-10-26
(user-directed "create shared pe and ohlcv logic ... use them where
possible or relevant") keeping its twin-era KEY — the
trust-weighted measurement beside it (measurement.py) never gates
(the plain rows stay the population of record), and a variant that
cannot clear the measured bar emits nothing.
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines._variant_base import VariantGate

__all__ = ["OhlcvBoostGate"]


class OhlcvBoostGate(VariantGate):
    """The OHLCV-boost variant axis (is_ohlcv_boosted): the plain
    candidates' masks AND-gated by the day's turnover-climax grade at
    least ``min_code`` (default HEAVY — the ratio >= 1.5x the 255d
    turnover average; 2 = the 2.5x climax grade)."""

    bar_key: ClassVar[str] = "ohlcv_gate"
    flag_kwarg: ClassVar[str] = "ohlcv_boosted"
    suffix: ClassVar[str] = "_amt"

    def __init__(self, min_code: int | None = None) -> None:
        super().__init__()
        self.min_code = cfg.OHLCV_BOOST_MIN_AMT_CODE if min_code is None \
            else min_code

    def available(self, frame: pd.DataFrame) -> bool:
        """Whether the fetch carries the turnover-climax axis (the
        boost-capable fetches select it through
        amt_ratio_fragments; the options frames do not)."""
        return not frame.empty and "amt_climax" in frame.columns

    def attach(self, frame: pd.DataFrame) -> None:
        """Nothing to compute — the bucketed grade ships with the
        fetch (finalize_input_frame)."""

    def _condition(self, frame: pd.DataFrame) -> pd.Series:
        """The grade-at-least condition (AXIS_MISSING -1 and any
        missing store row can never pass)."""
        a = frame["amt_climax"].fillna(-1.0)
        return (a >= float(self.min_code)).fillna(False)

    def bar_def(self) -> dict:
        """The stored bar fragment (the UI bar word renders it)."""
        return {"axis": "amt_climax", "rule": "at_least",
                "min_code": self.min_code,
                "heavy_ratio": cfg.AMT_CLIMAX_HEAVY}
