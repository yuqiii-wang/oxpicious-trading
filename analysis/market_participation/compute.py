"""Compute layer for analysis.market_participation — ONE whole-frame
pass building all derived columns (class-first: the ParticipationFrame
owns the merge + every rolling gauge). Layout per cudf-dtypes: plain
un-grouped rolling over a single date-ordered frame, rolling-sum closed
forms for the OLS slope and the trailing-252 percentile (a gather/index
algorithm with no cuDF-native form — the ONE sanctioned host core in
this pipeline, unwrapped once via host_array at entry), everything else
elementwise on frame columns."""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import host_array


class ParticipationFrame:
    """Builds the analysis.market_participation output frame from the
    two source frames (exchange totals + breadth)."""

    MA_WINDOWS = (5, 20, 60)
    AD_MA_WINDOWS = (5, 20)
    PCT_WINDOW = 252
    MIN_PCT_OBS = 60
    SLOPE_WINDOW = 20

    def __init__(
        self, totals: pd.DataFrame, breadth: pd.DataFrame,
    ) -> None:
        self.totals = totals
        self.breadth = breadth

    # -- public ----------------------------------------------------------

    def build(self) -> pd.DataFrame:
        out = self.totals.merge(
            self.breadth, on="date", how="left")
        out["total_trading_amount"] = (
            out["ss_trading_amount"] + out["sz_trading_amount"])
        out["ss_share"] = (
            out["ss_trading_amount"] / out["total_trading_amount"])
        out["ad_ratio"] = (
            out["adv_count"]
            / np.where(out["dec_count"].fillna(0.0) == 0.0,
                       np.nan, out["dec_count"]))
        self._attach_turnover_mas(out)
        self._attach_share_gauges(out)
        self._attach_ad_mas(out)
        self._attach_percentiles(out)
        # int counts back to int64 at the emit boundary only (Nullable
        # rule: NaN-able floats ride the frame; ints materialize at the
        # write boundary — sanitize keeps None for missing days).
        for col in ("adv_count", "dec_count", "flat_count"):
            valid = out[col].notna()
            out[col] = pd.Series(
                np.where(valid, out[col], 0.0).astype("float64"),
                index=out.index, name=col)
            out.loc[~valid, col] = np.nan
        return out.sort_values("date").reset_index(drop=True)

    # -- pieces ----------------------------------------------------------

    def _attach_turnover_mas(self, out: pd.DataFrame) -> None:
        amt = out["total_trading_amount"]
        for w in self.MA_WINDOWS:
            ma = amt.rolling(w, min_periods=w).mean()
            out[f"total_amt_ma{w}"] = ma
            out[f"total_amt_vs_ma{w}"] = (
                (amt - ma) / ma.where(ma != 0.0))

    def _attach_share_gauges(self, out: pd.DataFrame) -> None:
        share = out["ss_share"]
        for w in self.MA_WINDOWS:
            out[f"ss_share_ma{w}"] = share.rolling(
                w, min_periods=w).mean()
        ma20 = out["ss_share_ma20"]
        out["ss_share_vs_ma20_pp"] = (share - ma20) * 100.0
        # pp/day scale: share is 0..1, slope must be ×100
        out["ss_share_slope20_pp"] = self._ols_slope_pp(
            share, self.SLOPE_WINDOW) * 100.0

    def _attach_ad_mas(self, out: pd.DataFrame) -> None:
        ratio = out["ad_ratio"]
        for w in self.AD_MA_WINDOWS:
            out[f"ad_ratio_ma{w}"] = ratio.rolling(
                w, min_periods=w).mean()

    def _attach_percentiles(self, out: pd.DataFrame) -> None:
        out["total_amt_pct_rank_252"] = self._trailing_pct_rank(
            out["total_trading_amount"])
        out["ad_ratio_pct_rank_252"] = self._trailing_pct_rank(
            out["ad_ratio"])

    # -- host core ---------------------------------------------------------

    @staticmethod
    def _ols_slope_pp(series: pd.Series, window: int) -> pd.Series:
        """Trailing-window OLS slope of y on row position, via rolling
        sums. With x = 0..w-1 RELATIVE to the window start:
        slope = (Σxy − x̄·Σy) / Sxx, x̄ = (w−1)/2,
        Sxx = w(w²−1)/12 (both fixed constants). Σxy with relative x is
        recovered from ABSOLUTE-position rolling sums:
        Σxy_rel = Σ(i·y) − start·Σy, start = t−w+1. Output in the
        series' own units per row (the caller applies the pp scale)."""
        y = series.fillna(0.0).reset_index(drop=True)
        n = len(y)
        if n == 0:
            return pd.Series(np.full(0, np.nan), name=series.name)
        x_bar = (window - 1.0) / 2.0
        s_xx = window * (window * window - 1.0) / 12.0
        if s_xx == 0.0:
            return pd.Series(np.full(n, np.nan), name=series.name)
        pos = pd.Series(np.arange(n, dtype="float64"))
        sy = y.rolling(window, min_periods=window).sum()
        sxy_abs = (y * pos).rolling(
            window, min_periods=window).sum()
        # window start position for each output row t
        idx = pd.Series(np.arange(n, dtype="float64"))
        start = idx - (window - 1.0)
        sxy_rel = sxy_abs - start * sy
        slope = (sxy_rel - x_bar * sy) / s_xx
        # Windows containing NaN-filled y values (0.0 stand-ins) would
        # produce a defined but WRONG slope — null those out by
        # requiring the raw series' window to be fully observed.
        raw_valid = series.notna().reset_index(drop=True).rolling(
            window, min_periods=window).sum()
        slope = slope.where(raw_valid == window)
        return pd.Series(slope.to_numpy(), name=series.name)

    @staticmethod
    def _trailing_pct_rank(series: pd.Series) -> pd.Series:
        """Share of non-NULL values in the trailing PCT_WINDOW rows
        that are <= today's (>= MIN_PCT_OBS non-NULL obs required).
        Rolling rank is a gather/index algorithm with no cuDF-native
        form — the sanctioned host core (sliding_window_view over the
        small series, unwrapped once)."""
        vals = host_array(series.to_numpy(dtype=np.float64,
                                          na_value=np.nan))
        n = len(vals)
        out = np.full(n, np.nan)
        for i in range(n):
            lo = max(0, i - ParticipationFrame.PCT_WINDOW + 1)
            window = vals[lo:i + 1]
            window = window[~np.isnan(window)]
            if (window.size >= ParticipationFrame.MIN_PCT_OBS
                    and not np.isnan(vals[i])):
                out[i] = float(
                    np.mean(window <= vals[i]))
        return pd.Series(out, name=series.name)
