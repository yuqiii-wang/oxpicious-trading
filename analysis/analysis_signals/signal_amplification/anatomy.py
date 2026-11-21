"""The ohlcv_ext column compute (analysis.analysis_signals.
signal_amplification.anatomy).

OHLCV EXTENDED TECH STATS — the per-day intraday + prev-day anatomy
the close-only signal tier does not see. The classification SEMANTICS
are migrated from the mov_ave_spread anatomy producers (the repo's
existing OHLCV study, analysis/mov_ave_spread/steps/ohlc_gaps.py —
gap conventions verbatim) but the module is SELF-CONTAINED: it reads
no mov_ave_spread table and shares no code with it; the producer
stays untouched (user-directed migration semantics: consolidate the
study, not the pipelines).

Column set (all signed FRACTIONS; NULL when an input is NaN or the
denominator is zero/denormalized — the ohlc_gaps _frac_over_den
guard semantics):

  same-day      gap_oc   (close-open)/open      the intraday leg
                gap_hc   (close-high)/high      rejection depth
                gap_lc   (close-low)/low        support depth
                uw       (high-GREATEST(open,close))/prev_close
                lw       (LEAST(open,close)-low)/prev_close
                rng      (high-low)/prev_close
                close_pos (close-low)/(high-low)
  prev-day      prev_close_gap (open-prev_close)/prev_close  overnight
                ret_1d         (close-prev_close)/prev_close
                close_vs_prev_high (close-prev_high)/prev_high
                close_vs_prev_low  (close-prev_low)/prev_low
  participation amt_ratio_20  trading_amount / trailing 20-row MA
                amt_ratio_252 trading_amount / trailing 252-row MA
  amplifier     uw_top10 / lw_top10  the wick >= its trailing-252d q90
                bars (>= 120 obs; the ohlc_gaps PATTERN-block
                convention, unshifted — the day's own level is same-day
                observable)
                ret_sigma_252  the trailing 252-row std of ret_1d
                (>= 60 obs) — the gap/breakout DEPTH normalizer

prev_* references the previous row of the same code ordered by date —
the estimated-OHLC rows are already excluded at the source fetch (the
ohlc_gaps convention: the exclusion is the prev-row break, overnight
legs never cross a synthetic row).

Layout: whole-frame grouped ops only (grouped_shift / grouped_rolling_
agg / grouped_rolling_quantile + elementwise where-chains) — the
cudf GPU-first preference; no host-numpy boundary (the fraction guards
are cuDF-native Series ops).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import (
    grouped_rolling_agg,
    grouped_rolling_quantile,
    grouped_shift,
    safe_columns,
)

from analysis.analysis_signals import config as cfg

__all__ = ["OhlcvAnatomy", "OHLV_EXT_COLUMNS"]

# the analysis.ohlcv_ext column order (the writer's projection)
OHLV_EXT_COLUMNS = [
    "sec_type", "code", "date",
    "gap_oc", "gap_hc", "gap_lc", "uw", "lw", "rng", "close_pos",
    "prev_close_gap", "ret_1d", "close_vs_prev_high", "close_vs_prev_low",
    "amt_ratio_20", "amt_ratio_252",
]

# the amplifier-aid columns (computed beside the stored ones, NOT
# written to ohlcv_ext): the wick top-decile flags + their q90 bars +
# the return-sigma depth normalizer
_AMP_AID_COLUMNS = ["uw_top10", "lw_top10", "uw_bar", "lw_bar",
                    "ret_sigma_252"]

_GROUP_KEYS = ["code"]


def _frac(num: pd.Series, den: pd.Series) -> pd.Series:
    """num / den with SQL-like NULL semantics: NULL where num or den is
    NaN, |den| < 1e-12 (denormalized — would blow the ratio), or the
    result is non-finite. The ohlc_gaps._frac_over_den semantics
    (migrated; cited in the module docstring)."""
    out = num / den
    bad = (
        num.isna() | den.isna()
        | (den.abs() < 1e-12)
        | ~np.isfinite(out)
    )
    return out.where(~bad, other=None)


class OhlcvAnatomy:
    """Compute the ohlcv_ext columns (+ the amplifier-aid columns) over
    a fetched source frame.

    The frame must carry one row per (code, date) sorted-or-sortable —
    [code, date, open, high, low, close, trading_amount] plus the run's
    constant ``sec_type`` int16 code column. Estimated-OHLC rows never
    reach here (excluded at the source SQL). ``compute`` sorts by
    (code, date), adds the columns, and returns the frame (new columns
    appended; input not mutated beyond the sort copy).
    """

    def __init__(self, sec_type_code: int) -> None:
        self.sec_type_code = int(sec_type_code)

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add the ohlcv_ext + amplifier-aid columns to the source
        frame (see the module docstring for the column semantics)."""
        if df.empty:
            for c in OHLV_EXT_COLUMNS + _AMP_AID_COLUMNS:
                if c not in safe_columns(df):
                    df[c] = pd.Series(dtype="float64")
            return df

        df = df.sort_values(_GROUP_KEYS + ["date"], kind="stable",
                            ignore_index=True)
        op, hi, lo, cl = df["open"], df["high"], df["low"], df["close"]

        # ---- prev-day references (the valid-row chain: estimated rows
        # were excluded at the source SQL, so shift(1) IS the previous
        # valid row) --------------------------------------------------
        prev = grouped_shift(df, _GROUP_KEYS, ["close", "high", "low"],
                             ["prev_close", "prev_high", "prev_low"])

        # ---- same-day anatomy ----------------------------------------
        df["gap_oc"] = _frac(cl - op, op)
        df["gap_hc"] = _frac(cl - hi, hi)
        df["gap_lc"] = _frac(cl - lo, lo)
        df["uw"] = _frac(hi - np.maximum(op, cl), prev["prev_close"])
        df["lw"] = _frac(np.minimum(op, cl) - lo, prev["prev_close"])
        df["rng"] = _frac(hi - lo, prev["prev_close"])
        df["close_pos"] = _frac(cl - lo, hi - lo)

        # ---- prev-day gaps -------------------------------------------
        df["prev_close_gap"] = _frac(op - prev["prev_close"],
                                     prev["prev_close"])
        df["ret_1d"] = _frac(cl - prev["prev_close"], prev["prev_close"])
        df["close_vs_prev_high"] = _frac(cl - prev["prev_high"],
                                         prev["prev_high"])
        df["close_vs_prev_low"] = _frac(cl - prev["prev_low"],
                                        prev["prev_low"])

        # ---- participation gauges (trailing MAs INCLUDE the current
        # row — the level gauge, same-day observable) ------------------
        amt_ma20 = grouped_rolling_agg(df, _GROUP_KEYS, "trading_amount",
                                       20, min_periods=20, agg="mean")
        df["amt_ratio_20"] = _frac(df["trading_amount"], amt_ma20)
        amt_ma252 = grouped_rolling_agg(df, _GROUP_KEYS, "trading_amount",
                                        252, min_periods=60, agg="mean")
        df["amt_ratio_252"] = _frac(df["trading_amount"], amt_ma252)

        # ---- amplifier-aid columns -----------------------------------
        # wick top-decile bars (the ohlc_gaps PATTERN-block convention:
        # trailing 252-row q90, >= 120 obs, the day INCLUDED — the
        # flags feed post-close records, not intraday triggers)
        for src, flag, bar in (("uw", "uw_top10", "uw_bar"),
                               ("lw", "lw_top10", "lw_bar")):
            q = grouped_rolling_quantile(df, _GROUP_KEYS, src,
                                         cfg.AMP_WICK_WINDOW,
                                         cfg.AMP_WICK_MIN_PERIODS,
                                         cfg.AMP_WICK_Q)
            df[bar] = q
            has = df[src].notna() & q.notna()
            df[flag] = ((df[src] >= q) & has).astype("float64").where(has)

        # the depth normalizer: trailing return sigma (252 rows, >= 60
        # obs — the px_vol sigma convention), unshifted (same-day)
        df["ret_sigma_252"] = grouped_rolling_agg(
            df, _GROUP_KEYS, "ret_1d", cfg.AMP_RET_SIGMA_WINDOW,
            min_periods=cfg.AMP_RET_SIGMA_MIN_PERIODS, agg="std", ddof=0,
        )
        return df
