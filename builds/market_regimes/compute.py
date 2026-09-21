"""Pure compute for builds.market_regimes — daily 4-state regime labels.

The Phase-A study's detector (temp_scripts/study_market_regimes_
weights.py add_regime_features / classify_regime), productionized with
the repo's grouped helpers. All inputs are TRAILING + SHIFTED 1 row —
no look-ahead (the px_vol log_level convention; the retired
market_hypes build used a look-ahead CENTERED ±10y percentile base,
inconsistent between backtest and live ends).

Per (sec_type, code, date) — input frame sorted by those keys:

  1. ret_1d = price / price.shift(1) - 1          (grouped shift)
  2. vol    = sqrt(EWMA(ret_1d^2, span=VOL_SPAN,
                        min_periods=VOL_MIN_PERIODS))
     cuDF lacks grouped-EWM, so under cudf.pandas this ONE transform
     falls back to CPU pandas (the same accepted fallback as the
     retired build's centered rolling quantile — one pass, cheap).
  3. vol_z  = (vol - mu_vol) / sd_vol where mu/sd are the vol's own
     trailing VOL_Z_WINDOW-row moments (ddof=1), SHIFTED 1 row.
  4. amt_z  = (log(trading_amount) - mu) / sd of log(amount)'s own
     trailing AMT_Z_WINDOW-row moments (ddof=1), SHIFTED 1 row.
  5. regime (exhaustive, np.select on host numpy arrays — the
     classify_price_vs_amt precedent):
       hot    = vol_z > VOL_BAR  AND amt_z > AMT_BAR
       panic  = vol_z > VOL_BAR  AND amt_z <= AMT_BAR
       quiet  = vol_z <= VOL_BAR AND amt_z > AMT_BAR
       calm   = rest
     No-state days (NULL vol_z/amt_z — the code's first warm-up rows)
     are labeled calm — an explicit label instead of a consumer-side
     hidden default (old semantics: young codes were never hyped).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg, grouped_shift
from builds.market_regimes.config import (
    AMT_BAR,
    AMT_Z_MIN_PERIODS,
    AMT_Z_WINDOW,
    MARKET_REGIME_SPANS_COLUMNS,
    REGIMES,
    VOL_BAR,
    VOL_MIN_PERIODS,
    VOL_SPAN,
    VOL_Z_MIN_PERIODS,
    VOL_Z_WINDOW,
)

import logging
logger = logging.getLogger(__name__)

_KEYS = ["sec_type", "code"]


def _shifted_moments(df: pd.DataFrame, col: str, window: int,
                     min_periods: int) -> tuple[pd.Series, pd.Series]:
    """(mu, sd) of col's own trailing window, SHIFTED 1 row per group."""
    mu = grouped_rolling_agg(
        df, _KEYS, col, window=window,
        min_periods=min_periods, agg="mean", ddof=1,
    )
    sd = grouped_rolling_agg(
        df, _KEYS, col, window=window,
        min_periods=min_periods, agg="std", ddof=1,
    )
    tmp = f"_{col}_mu"
    df[tmp] = mu
    grouped_shift(df, _KEYS, tmp, out_names=[f"_{col}_mu_s"], periods=1)
    df[f"_{col}_sd"] = sd
    grouped_shift(df, _KEYS, f"_{col}_sd", out_names=[f"_{col}_sd_s"],
                  periods=1)
    return df[f"_{col}_mu_s"], df[f"_{col}_sd_s"]


def compute_market_regimes(df: pd.DataFrame) -> pd.DataFrame:
    """Attach the regime evidence + label columns to the source frame.

    Input: a (sec_type, code, date)-sorted frame with columns price +
    trading_amount (fetch_regime_source). Adds vol / vol_z / amt_z
    (evidence, NaN on warm-up rows) and regime (the 4-state label,
    'calm' on warm-up rows) in place; drops the transient moment
    columns. Every source row keeps a label — the output frame's row
    count ALWAYS equals the input's.
    """
    if df.empty:
        df["vol"] = pd.Series(dtype="float64")
        df["vol_z"] = pd.Series(dtype="float64")
        df["amt_z"] = pd.Series(dtype="float64")
        df["regime"] = pd.Series(dtype="object")
        return df

    # ---- ret_1d + EWMA vol ------------------------------------------
    grouped_shift(df, _KEYS, "price", out_names=["_prev_price"], periods=1)
    df["ret_1d"] = df["price"] / df["_prev_price"] - 1.0
    del df["_prev_price"]

    g = df.groupby(_KEYS, sort=False)
    # cuDF lacks grouped-EWM -> transparent CPU pandas fallback here
    # (accepted — see module docstring; one transform over the frame).
    vol2 = g["ret_1d"].transform(
        lambda s: (s * s).ewm(
            span=VOL_SPAN, min_periods=VOL_MIN_PERIODS).mean()
    )
    df["vol"] = np.sqrt(vol2)

    # ---- z-features vs own trailing moments (shift 1) ----------------
    mu_v, sd_v = _shifted_moments(df, "vol", VOL_Z_WINDOW,
                                  VOL_Z_MIN_PERIODS)
    df["vol_z"] = (df["vol"] - mu_v) / sd_v
    df["log_amt"] = np.log(df["trading_amount"])
    mu_a, sd_a = _shifted_moments(df, "log_amt", AMT_Z_WINDOW,
                                  AMT_Z_MIN_PERIODS)
    df["amt_z"] = (df["log_amt"] - mu_a) / sd_a

    df.drop(columns=[
        "_vol_mu", "_vol_sd", "_vol_mu_s", "_vol_sd_s",
        "_log_amt_mu", "_log_amt_sd", "_log_amt_mu_s", "_log_amt_sd_s",
        "log_amt", "ret_1d",
    ], inplace=True)

    # ---- classify (host numpy — the classify_price_vs_amt precedent) -
    vz = df["vol_z"].to_numpy(dtype="float64", copy=True)
    az = df["amt_z"].to_numpy(dtype="float64", copy=True)
    high_vol = vz > VOL_BAR
    high_amt = az > AMT_BAR
    ordinals = np.select(
        [high_vol & high_amt, high_vol & ~high_amt, ~high_vol & high_amt],
        [REGIMES.index("hot"), REGIMES.index("panic"),
         REGIMES.index("quiet")],
        default=REGIMES.index("calm"),
    )
    # NaN comparisons are all False -> warm-up rows land on 'calm'.
    df["regime"] = np.asarray(REGIMES, dtype=object)[ordinals]
    return df


def compute_regime_spans(states: pd.DataFrame) -> pd.DataFrame:
    """Collapse daily regime states into CONTIGUOUS same-regime spans.

    Input: compute_market_regimes' output (the sec_type / code / date /
    regime columns), (sec_type, code, date)-sorted — the order the
    runner guarantees (it sorts right before compute, and compute
    preserves row order). Output: the MARKET_REGIME_SPANS_COLUMNS frame
    with one row per MAXIMAL run of consecutive trading rows holding
    the same regime within a (sec_type, code) — start_date / end_date
    (inclusive) + span_days (trading-row count). The vectorized
    equivalent of the gaps-and-islands VIEW this build now materializes
    as stats.market_regime_spans.

    Vectorized collapse: a grouped regime shift marks run starts, one
    cumsum numbers the runs (every group start is a run start, so run
    numbers are globally unique), one groupby-agg collapses each run.
    The run-start test MUST null-guard the shifted column — under
    cudf.pandas the shifted object column is a cuDF str series whose
    group-start fill is <NA>, and ``regime != <NA>`` evaluates to <NA>
    (Kleene logic), not True: the unguarded form poisoned the cumsum
    and silently dropped each group's first day from its span.
    ``prev.isna() |`` forces an exact True at every group start under
    both engines; fillna(False) keeps the cumsum dtype non-nullable.
    """
    if states.empty:
        return pd.DataFrame({
            "sec_type": pd.Series(dtype="object"),
            "code": pd.Series(dtype="object"),
            "regime": pd.Series(dtype="object"),
            "start_date": pd.Series(dtype="datetime64[ns]"),
            "end_date": pd.Series(dtype="datetime64[ns]"),
            "span_days": pd.Series(dtype="int64"),
        })

    base = states.loc[:, ["sec_type", "code", "date", "regime"]]
    prev = base.groupby(_KEYS, sort=False)["regime"].shift(1)
    run_start = prev.isna() | (base["regime"] != prev)
    base["_run"] = (
        run_start.fillna(False).astype("bool").cumsum().astype("int64")
    )
    spans = (
        base.groupby(["sec_type", "code", "regime", "_run"], sort=False)
        .agg(
            start_date=("date", "min"),
            end_date=("date", "max"),
            span_days=("date", "size"),
        )
        .reset_index()
        .drop(columns="_run")
        .sort_values(["sec_type", "code", "start_date"], kind="stable")
        .reset_index(drop=True)
    )
    return spans.reindex(columns=list(MARKET_REGIME_SPANS_COLUMNS))
