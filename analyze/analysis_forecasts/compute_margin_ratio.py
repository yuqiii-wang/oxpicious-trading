"""margin_ratio_state bucket annual-snapshot aggregation (analysis_forecasts) —
sparse tensor engine.

The margin-buy intensity STATE buckets (see
database/sql/analysis/analysis_forecasts/06_margin_ratio.sql and the
2026-09 study temp_scripts/study_margin_ratio_forecast.py /
docs/margin_ratio_study.md): per stat date's trailing 5-year window
[lo, hi) of the (T, C) wide grid, a (code, date) joins ONE of the 6
ratio states:

  z = (ratio - μ)/σ  — z-scored 融资买入额/成交额 ratio (scattered "z"
      matrix; the fetch layer computes ratio = rz_buy / trading_amount
      on buy days and the rolling-1220-row shifted moments, so NaN here
      means "no bucket")
  nb = no-margin-buy flag (scattered "nb" bool matrix: rz_buy == 0
      with trading_amount > 0)

  ratio_state: no_buy nb | vlow z<=-2 | low -2<z<=-1 | mid -1<z<=+1 |
               high +1<z<=+2 | vhigh z>+2

There is NO cooldown (a state cell admits every qualifying day —
1-day signals, the identity registry's streak_signal_days constant;
the event families' streak-merge does not apply here), and the bucket
split is by PK member
regime_state only. The bucket is etf/stock only — index rz_buy is
NULL so every mask is False and no rows emit.

Per (side, hype) subset the horizon aggregates reuse
the engine's vectorized horizon aggregation — the crowding states
high/vhigh carry side='top' (the study's bearish reading),
vlow/low/no_buy side='bottom', mid side='flat' (no directional
claim). The config JSONB records the bucket's mean
ratio / mean z (motivation magnitude).

Yields (stat_date, rows) so __main__ can split each row into the
margin_ratio_state motivation dicts and the forecast_results result
dicts and write snapshot-major.
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
    LOOKBACK_PERIOD,
    MARGIN_RATIO_HIGH_BAR,
    MARGIN_RATIO_LOW_BAR,
    MARGIN_RATIO_STATE_SIDE,
    MARGIN_RATIO_STATES,
    MARGIN_RATIO_VHIGH_BAR,
    MARGIN_RATIO_VLOW_BAR,
    MARGIN_RATIO_Z_MIN_PERIODS,
    MARGIN_RATIO_Z_WINDOW,
)

# Ordinal → state name (the _band_ordinal ladder + the no_buy flag).
_STATE_OF_ORD = {0: "vlow", 1: "low", 2: "mid", 3: "high", 4: "vhigh",
                 5: "no_buy"}


class _MarginRatioEngine(WideDfEngine):
    """Margin-buy intensity STATE buckets: vlow/low/mid/high/vhigh z
    bands of ratio_z (the code's own shifted rolling moments) plus the
    no_buy state (margin traders absent). Every qualifying day is its
    own 1-day signal (state family — no run semantics); the band
    members carry no scalar qualifying bar, so trigger_excess is NULL."""

    BUCKET_COLS = ("ratio_state",)
    MERGE = False

    def _extra_window_cols(self) -> list[str]:
        return ["ratio", "ratio_z", "nb"]

    def family_constants(self) -> dict:
        return {
            "z_window": MARGIN_RATIO_Z_WINDOW,
            "z_min_periods": MARGIN_RATIO_Z_MIN_PERIODS,
            "vlow_bar": MARGIN_RATIO_VLOW_BAR,
            "low_bar": MARGIN_RATIO_LOW_BAR,
            "high_bar": MARGIN_RATIO_HIGH_BAR,
            "vhigh_bar": MARGIN_RATIO_VHIGH_BAR,
        }

    def emit_signals(self, win: pd.DataFrame) -> Iterator[pd.DataFrame]:
        nb = win["nb"].fillna(False)
        has_z = _finite_mask(win["ratio_z"])
        keep_rows = nb | has_z
        if not keep_rows.any():
            return
        ord_ = _band_ordinal(
            win["ratio_z"],
            (MARGIN_RATIO_VLOW_BAR, MARGIN_RATIO_LOW_BAR,
             MARGIN_RATIO_HIGH_BAR, MARGIN_RATIO_VHIGH_BAR),
        )
        ord_ = ord_.where(~nb, 5)          # no_buy overrides any band
        cells = win[keep_rows].copy()
        cells["ratio_state"] = ord_[keep_rows].map(_STATE_OF_ORD)
        cells["excess"] = np.nan           # band membership — no bar
        cells = cells[cells["ratio_state"].notna()]
        if cells.empty:
            return
        cells["side"] = cells["ratio_state"].map(MARGIN_RATIO_STATE_SIDE)
        yield self._streak_merge(
            cells, group_cols=["ratio_state", "side", "code"],
        )


def compute_margin_ratio_results(
    *, df, first_dates, regimes, codes, sec_type, specs,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_date, margin_ratio_state bucket rows) per month."""
    engine = _MarginRatioEngine(
        df=df,
        first_dates=first_dates,
        regimes=regimes,
        codes=codes,
        sec_type=sec_type,
        specs=specs,
    )
    return engine.run()
