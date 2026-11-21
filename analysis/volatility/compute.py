"""Compute layer for analysis.volatility — ONE whole-frame grouped
pass building every vol column (class-first: the VolatilityFrame owns
the daily-bar transforms + every estimator). Layout per cudf-dtypes:
whole-frame grouped rolling / shift / diff via the shared _common.df_utils
helpers and elementwise np-ufunc chains on frame columns (GPU under
cudf.pandas); the grouped EWMA recursions (RiskMetrics vol, Wilder ATR)
use the builds.market_regimes GroupBy.transform precedent (cudf has no
grouped ewm — the accepted CPU fallback, trivial at this row count);
grouped_rolling_rank_pct is the accepted CPU-fallback convention for the
trailing percentile. NO host-numpy core: the rv leg is grouped diffs +
shifts within (code, date) and one dict-form groupby agg, merged back on
(code, date)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import (
    grouped_diff,
    grouped_rolling_agg,
    grouped_rolling_rank_pct,
    grouped_shift,
)
from analysis.volatility import config


def _ln(s: pd.Series) -> pd.Series:
    """ln of a positive price series (non-positive / NULL -> NaN, no
    sqrt/log warnings)."""
    return np.log(np.where(s > 0.0, s, np.nan))


def _ann_vol(var: pd.Series) -> pd.Series:
    """Daily variance series -> percent annualized vol (×√252×100).
    Negative variance (degenerate Garman-Klass bars) -> NaN, not 0."""
    nonneg = var >= 0.0
    vol = np.sqrt(var.clip(lower=0.0) * config.ANN_DAYS) * 100.0
    return vol.where(nonneg)


def _ann_std(std: pd.Series) -> pd.Series:
    """Daily std series -> percent annualized vol (std × √252 × 100) —
    the STD is already a daily vol, so the annualization is a straight
    ×√252 scale (NOT sqrt(std×252), which would square-root a vol)."""
    return std * np.sqrt(config.ANN_DAYS) * 100.0


class VolatilityFrame:
    """Builds the analysis.volatility output frame from the daily OHLC
    frame (+ the optional 5-minute close frame for the rv leg)."""

    KEYS = ["sec_type", "code"]

    def __init__(
        self,
        daily: pd.DataFrame,
        intraday: pd.DataFrame,
        sec_code: int,
    ) -> None:
        self.daily = daily
        self.intraday = intraday
        self.sec_code = sec_code

    # -- public ----------------------------------------------------------

    def build(self) -> pd.DataFrame:
        out = self._prepare_daily()
        self._attach_log_terms(out)
        self._attach_hvs(out)
        self._attach_ewma(out)
        self._attach_range_estimators(out)
        self._attach_decomposition(out)
        self._attach_atr(out)
        self._attach_boll(out)
        self._attach_structure(out)
        self._attach_regime(out)
        self._merge_rv(out)
        return out

    # -- pieces ----------------------------------------------------------

    def _prepare_daily(self) -> pd.DataFrame:
        """Scope the daily bars, attach the constant sec_type code, and
        sort by (sec_type, code, date) — every later grouped op runs
        with sort=False and relies on this order."""
        df = self.daily.copy()
        df["sec_type"] = np.int16(self.sec_code)
        return df.sort_values(
            self.KEYS + ["date"]).reset_index(drop=True)

    def _attach_log_terms(self, out: pd.DataFrame) -> None:
        """The per-bar log ratios every estimator builds on, plus the
        shifted predecessors (prev log close for the return + overnight
        legs, prev raw close for the ATR true range)."""
        for col in ("open", "high", "low", "close"):
            out[f"ln_{col}"] = _ln(out[col])
        ln_close = out["ln_close"]
        grouped_shift(out, self.KEYS, "ln_close",
                      out_names="ln_close_shift", periods=1, sort=False)
        grouped_shift(out, self.KEYS, "close",
                      out_names="close_prev", periods=1, sort=False)
        # Daily log return r_t = ln(C_t / C_{t-1})
        out["ret_1d"] = ln_close - out["ln_close_shift"]
        # Overnight gap ln(O_t / C_{t-1}) and intraday ln(C_t / O_t)
        out["overnight"] = out["ln_open"] - out["ln_close_shift"]
        out["ln_hl"] = out["ln_high"] - out["ln_low"]
        out["ln_co"] = out["ln_close"] - out["ln_open"]
        out["ln_ho"] = out["ln_high"] - out["ln_open"]
        out["ln_lo"] = out["ln_low"] - out["ln_open"]
        out["ln_hc"] = out["ln_high"] - out["ln_close"]
        out["ln_lc"] = out["ln_low"] - out["ln_close"]

    def _attach_hvs(self, out: pd.DataFrame) -> None:
        """Close-to-close historical vol across the windows: rolling
        std of the daily log return (ddof=1), annualized %."""
        for w in config.HV_WINDOWS:
            std = grouped_rolling_agg(
                out, self.KEYS, "ret_1d", w,
                min_periods=w, agg="std", ddof=1, sort=False)
            out[f"hv_{w}"] = _ann_std(std)

    def _attach_ewma(self, out: pd.DataFrame) -> None:
        """RiskMetrics EWMA vol (λ=0.94, contemporaneous realized level):
        σ²_t = λ·σ²_{t-1} + (1−λ)·r²_t via the grouped-ewm CPU fallback
        (builds.market_regimes precedent)."""
        alpha = 1.0 - config.EWMA_LAMBDA
        ret_sq = (out["ret_1d"] * out["ret_1d"]).rename("ret_sq")
        ewm_var = (
            out[self.KEYS]
            .assign(ret_sq=ret_sq)
            .groupby(self.KEYS, sort=False)["ret_sq"]
            .transform(lambda s: s.ewm(alpha=alpha, adjust=False).mean())
        )
        out["ewma_vol"] = np.sqrt(ewm_var * config.ANN_DAYS) * 100.0

    def _attach_range_estimators(self, out: pd.DataFrame) -> None:
        """Parkinson / Garman-Klass / Rogers-Satchell / Yang-Zhang: the
        rolling mean of each estimator's daily variance terms over
        RANGE_WINDOW rows, annualized. YZ combines the overnight, open
        and RS legs with k = 0.34/(1.34 + (n+1)/(n−1)), n = window."""
        w = config.RANGE_WINDOW
        # Daily variance terms
        ln2 = np.log(2.0)
        out["parkinson_term"] = out["ln_hl"] * out["ln_hl"] / (4.0 * ln2)
        out["gk_term"] = (
            0.5 * out["ln_hl"] * out["ln_hl"]
            - (2.0 * ln2 - 1.0) * out["ln_co"] * out["ln_co"])
        out["rs_term"] = (
            out["ln_hc"] * out["ln_ho"] + out["ln_lc"] * out["ln_lo"])
        out["overnight_sq"] = out["overnight"] * out["overnight"]
        # σ²_Open leg of YZ: ln(O/H)·ln(O/L) = (−ln_ho)·(−ln_lo)
        out["sigma_open_term"] = out["ln_ho"] * out["ln_lo"]
        park_var = grouped_rolling_agg(
            out, self.KEYS, "parkinson_term", w, agg="mean", sort=False)
        gk_var = grouped_rolling_agg(
            out, self.KEYS, "gk_term", w, agg="mean", sort=False)
        rs_var = grouped_rolling_agg(
            out, self.KEYS, "rs_term", w, agg="mean", sort=False)
        oz_var = grouped_rolling_agg(
            out, self.KEYS, "overnight_sq", w, agg="mean", sort=False)
        so_var = grouped_rolling_agg(
            out, self.KEYS, "sigma_open_term", w, agg="mean", sort=False)
        n = float(w)
        k = config.YZ_K_NUMERATOR / (
            1.34 + (n + 1.0) / (n - 1.0))
        yz_var = oz_var + k * so_var + (1.0 - k) * rs_var
        out["parkinson_vol_20"] = _ann_vol(park_var)
        out["garman_klass_vol_20"] = _ann_vol(gk_var)
        out["rogers_satchell_vol_20"] = _ann_vol(rs_var)
        out["yang_zhang_vol_20"] = _ann_vol(yz_var)

    def _attach_decomposition(self, out: pd.DataFrame) -> None:
        """Overnight-gap vs open-to-close vol split (20-row stds,
        annualized %)."""
        w = config.RANGE_WINDOW
        on_std = grouped_rolling_agg(
            out, self.KEYS, "overnight", w,
            min_periods=w, agg="std", ddof=1, sort=False)
        id_std = grouped_rolling_agg(
            out, self.KEYS, "ln_co", w,
            min_periods=w, agg="std", ddof=1, sort=False)
        out["overnight_vol_20"] = _ann_std(on_std)
        out["intraday_vol_20"] = _ann_std(id_std)

    def _attach_atr(self, out: pd.DataFrame) -> None:
        """Wilder ATR(14): RMA (ewm alpha=1/14, adjust=False) of the true
        range TR = max(H−L, |H−C_prev|, |L−C_prev|); NATR normalizes by
        the close (percent of level)."""
        prev = out["close_prev"]
        tr = np.maximum(
            out["high"] - out["low"], np.abs(out["high"] - prev))
        tr = np.maximum(tr, np.abs(out["low"] - prev)).rename("tr")
        atr = (
            out[self.KEYS]
            .assign(tr=tr)
            .groupby(self.KEYS, sort=False)["tr"]
            .transform(lambda s: s.ewm(
                alpha=1.0 / config.ATR_PERIOD,
                adjust=False, min_periods=config.ATR_PERIOD).mean())
        )
        out["atr_14"] = atr
        out["natr_14"] = atr / out["close"] * 100.0

    def _attach_boll(self, out: pd.DataFrame) -> None:
        """Bollinger band width: 4·σ_price / ma × 100 (price-level σ,
        ddof=0, matching the boll20 signal family's own σ)."""
        w = config.BOLL_WINDOW
        ma = grouped_rolling_agg(
            out, self.KEYS, "close", w, agg="mean", sort=False)
        sd = grouped_rolling_agg(
            out, self.KEYS, "close", w,
            min_periods=w, agg="std", ddof=0, sort=False)
        out["boll_bw_20"] = (
            4.0 * sd / ma.where(ma != 0.0) * 100.0)

    def _attach_structure(self, out: pd.DataFrame) -> None:
        """Vol-of-vol (rolling std of the hv_20 series) and the
        short/long vol ratio (hv_20 / hv_252)."""
        out["vol_of_vol_20"] = grouped_rolling_agg(
            out, self.KEYS, "hv_20", config.VOL_OF_VOL_WINDOW,
            agg="std", ddof=1, sort=False)
        hv_long = out[f"hv_{config.VOL_RATIO_LONG}"]
        out["vol_ratio_20_252"] = (
            out[f"hv_{config.VOL_RATIO_SHORT}"] / hv_long.where(hv_long != 0.0))

    def _attach_regime(self, out: pd.DataFrame) -> None:
        """Regime gauges: trailing-252-row percentile ranks (house
        0..1 convention, >= 60 obs; the accepted CPU fallback) and
        z-scores against the trailing-252 moments SHIFTED 1 row (today
        scored vs the past year, never vs a window including itself;
        >= 60 obs)."""
        for col, out_col in (
            ("hv_20", "hv_20_pct_rank_252"),
            ("ewma_vol", "ewma_vol_pct_rank_252"),
            ("yang_zhang_vol_20", "yang_zhang_vol_20_pct_rank_252"),
            ("natr_14", "natr_14_pct_rank_252"),
        ):
            out[out_col] = grouped_rolling_rank_pct(
                out, self.KEYS, col,
                config.PCT_WINDOW, config.MIN_PCT_OBS)
        for col, out_col in (
            ("hv_20", "hv_20_z_252"),
            ("ewma_vol", "ewma_vol_z_252"),
        ):
            mean = grouped_rolling_agg(
                out, self.KEYS, col, config.Z_WINDOW,
                min_periods=config.MIN_Z_OBS, agg="mean", sort=False)
            std = grouped_rolling_agg(
                out, self.KEYS, col, config.Z_WINDOW,
                min_periods=config.MIN_Z_OBS, agg="std", ddof=1,
                sort=False)
            grouped_shift(out, self.KEYS, [col, col],
                          out_names=["_mean_shift", "_std_shift"],
                          periods=1, sort=False)
            out[out_col] = (
                (out[col] - out["_mean_shift"])
                / out["_std_shift"].where(out["_std_shift"] != 0.0))

    def _merge_rv(self, out: pd.DataFrame) -> None:
        """5-minute realized-vol leg: within-(code, date) bar-to-bar log
        returns of the intraday closes (the overnight gap is excluded by
        construction — diffs never cross a day boundary), aggregated per
        day into RV / bipower variation / bar count and merged back on
        (code, date). Dates without bars stay NULL (retention ~90d)."""
        intr = self.intraday
        if intr is None or intr.empty:
            return
        intr = intr[intr["close"] > 0.0].copy()
        if intr.empty:
            return
        intr["ln_c"] = np.log(intr["close"])
        grouped_diff(intr, ["code", "date"], "ln_c",
                     out_names="r5", periods=1, sort=False)
        intr["r2"] = intr["r5"] * intr["r5"]
        intr["ar"] = np.abs(intr["r5"])
        grouped_shift(intr, ["code", "date"], "ar",
                      out_names="ar_prev", periods=1, sort=False)
        intr["bpv_term"] = intr["ar"] * intr["ar_prev"]
        agg = intr.groupby(["code", "date"], sort=False).agg({
            "r2": "sum", "ar": "count", "bpv_term": "sum",
            "close": "count",
        }).reset_index().rename(columns={
            "r2": "rv_sum", "ar": "n_ret", "bpv_term": "bpv_sum",
            "close": "bar_count",
        })
        merged = out.merge(agg, on=["code", "date"], how="left")
        for col in ("rv_sum", "n_ret", "bpv_sum", "bar_count"):
            out[col] = merged[col]
        rv_ok = out["n_ret"].fillna(0.0) >= float(config.MIN_RV_RETURNS)
        out["rv_5min"] = np.where(
            rv_ok,
            np.sqrt(out["rv_sum"].fillna(0.0) * config.ANN_DAYS) * 100.0,
            np.nan)
        bpv_ok = (
            rv_ok
            & (out["n_ret"].fillna(0.0) >= float(config.MIN_BPV_RETURNS))
            & (out["bpv_sum"].fillna(0.0) > 0.0))
        jump = out["rv_sum"] / out["bpv_sum"].where(out["bpv_sum"] != 0.0)
        out["jump_ratio_5min"] = np.where(
            bpv_ok, np.maximum(jump.fillna(0.0) - 1.0, 0.0), np.nan)
        # The bar count is the one INTEGER column — nullable Int64 at the
        # emit boundary only (cudf-dtypes rule), keeping python ints /
        # None through sanitize's int branch.
        out["rv_bar_count"] = out["bar_count"].astype("Int64")
