"""dividend_state bucket monthly aggregation (analysis_forecasts) —
sparse tensor engine.

The valuation STATE buckets over the dividend-yield series of
analysis.dividends (see
database/sql/analysis/analysis_forecasts/12_dividend_state.sql): per
stat month's trailing 5-year window [lo, hi) of the (T, C) wide grid, a
(code, date) joins ONE of the 5 z states — the trailing-12m D/P
(fractional) standardized by the code's OWN trailing moments (the fetch
layer computes z = (dividend_yield - μ)/σ on the rolling-1220-row
shifted moments, min 250 non-NULL observations, so NaN here means "no
bucket" — non-payer days):

  vlow z <= -2 | low (-2,-1] | mid (-1,+1] | high (+1,+2] | vhigh z > +2

The family's defining semantics: the yield is HIGHER-the-better — a
high-yield day is a cheap, well-supported valuation → the extreme high
states are bullish (side 'bottom'), the low-yield states bearish (side
'top'); mid is 'flat' (reverse_prob NULL — no directional claim). The
mapping REVERSES the pe sibling's (compute_pe).

Signals are STREAK-MERGED via the UNIFIED bucket pipeline
(wide.iter_bucket_subsets, merge=True — the 2026-09 px_vol convention):
consecutive grid rows holding the same state collapse into ONE forecast
signal at the run's MID row, the bucket's MEAN run length recorded on
forecast_identities.streak_signal_days, the bucket split by PK member
is_market_hyped only.

Per (side, hype) subset the horizon aggregates reuse
wide.aggregate_horizons_sparse against the code's ADAPTIVE reversal bar
(thresholds: k_n·σ of the window's n-day forward changes).
The config JSONB records the bucket's mean yield level (mean_metric —
fractional D/P) and mean z (motivation magnitude, like margin_ratio's
mean_ratio / mean_z).

Yields (stat_month, rows) so __main__ can split each row into the
dividend_state motivation dicts and the forecast_results result dicts
and write month-major.
"""



from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import numpy as np
import pandas as pd

from analyze.analysis_forecasts._dfengine import (
    WideDfEngine,
    _band_ordinal,
    _finite_mask,
)
from analyze.analysis_forecasts.config import (
    VAL_HIGH_BAR,
    VAL_LOW_BAR,
    VAL_STATES,
    VAL_VHIGH_BAR,
    VAL_VLOW_BAR,
    VAL_Z_MIN_PERIODS,
    VAL_Z_WINDOW,
)

_STATE_OF_ORD = {i: s for i, s in enumerate(VAL_STATES)}


class _ValStateEngine(WideDfEngine):
    """Valuation STATE buckets over one standardized series (the z the
    fetch layer computed vs the code's OWN shifted rolling moments): 5
    bands vlow..vhigh, ONE family per series with the OPPOSITE side
    mapping — PE lower-the-better (high z = expensive = bearish 'top'),
    dividend yield higher-the-better (high z = cheap = bullish
    'bottom'); mid = 'flat' (NULL reverse_prob). Every qualifying day
    is its own 1-day signal; band membership has no scalar bar."""

    BUCKET_COLS = ("val_state",)
    MERGE = False
    src_col: str = ""          # "pe_z" / "div_z"
    state_side: dict = {}

    def _extra_window_cols(self) -> list[str]:
        return [self.src_col]

    def family_constants(self) -> dict:
        return {
            "z_window": VAL_Z_WINDOW,
            "z_min_periods": VAL_Z_MIN_PERIODS,
            "vlow_bar": VAL_VLOW_BAR,
            "low_bar": VAL_LOW_BAR,
            "high_bar": VAL_HIGH_BAR,
            "vhigh_bar": VAL_VHIGH_BAR,
        }

    def emit_signals(self, win: pd.DataFrame) -> Iterator[pd.DataFrame]:
        z = win[self.src_col]
        has = _finite_mask(z)
        if not has.any():
            return
        ord_ = _band_ordinal(
            z, (VAL_VLOW_BAR, VAL_LOW_BAR, VAL_HIGH_BAR, VAL_VHIGH_BAR),
        )
        cells = win[has].copy()
        cells["val_state"] = ord_[has].map(_STATE_OF_ORD)
        cells["side"] = cells["val_state"].map(self.state_side)
        cells["excess"] = np.nan          # band membership — no bar
        yield self._streak_merge(
            cells, group_cols=["val_state", "side", "code"],
        )


class _PeStateEngine(_ValStateEngine):
    src_col = "pe_z"
    state_side = {
        "vlow": "bottom", "low": "bottom", "mid": "flat",
        "high": "top", "vhigh": "top",
    }


class _DividendStateEngine(_ValStateEngine):
    src_col = "div_z"
    state_side = {
        "vlow": "top", "low": "top", "mid": "flat",
        "high": "bottom", "vhigh": "bottom",
    }


def compute_pe_results(
    *, df, first_dates, episodes, codes, sec_type, specs,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, pe_state bucket rows) per month."""
    engine = _PeStateEngine(
        df=df, first_dates=first_dates, episodes=episodes, codes=codes,
        sec_type=sec_type, specs=specs,
    )
    return engine.run()


def compute_dividend_results(
    *, df, first_dates, episodes, codes, sec_type, specs,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, dividend_state bucket rows) per month."""
    engine = _DividendStateEngine(
        df=df, first_dates=first_dates, episodes=episodes, codes=codes,
        sec_type=sec_type, specs=specs,
    )
    return engine.run()
