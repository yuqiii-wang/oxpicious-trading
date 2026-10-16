"""Rolling correlation between the 30d model-free vol index and spot.

compute_options_vol_spot_corr — the VIX-vs-spot "fear gauge" relation per
underlying (CBOE documents a historically strong INVERSE relationship
between the S&P 500 and the VIX; the standard measurement correlates daily
spot RETURNS against daily vol-index CHANGES — both stationary — never the
trending levels, whose correlation would be spurious):

  x_t = ln(spot_t / spot_{t-1})       daily spot log-return
  y_t = ln(vol_t / vol_{t-1})         daily vol-index log-change
  corr_Wd_t = Pearson corr of x, y over the trailing W TRADING sessions,
              counting only sessions where BOTH changes exist (a missing
              vol-index or spot session NaNs its own change AND the one
              after it — groupby diff, never a cross-gap return); NULL
              until the window holds W joint-valid pairs.

Windows 5/20/60/255 sessions (~1w/1m/3m/1y); rows are emitted at two kinds
of sample per underlying (append-only — no stored row is ever rewritten):
  • the VOL_SPOT_CORR_STRIDE-session grid (the seed — a pure
    1-based row-ordinal rule, immutable once written) plus the latest
    session (the seed tail) for underlyings with no stored corr history;
  • every session STRICTLY AFTER the underlying's latest stored corr
    date (``stored_max``) — the daily tail growth: each new trading day
    adds one row, a multi-day pipeline gap backfills each day, and every
    window reaches back up to 255 sessions from its own date.

cudf layout (B-A4): ONE whole-frame grouped pass — groupby().diff() for
the log-changes, then grouped rolling SUMS for the Pearson algebra
  corr = (n·Sxy − Sx·Sy) / sqrt(max(n·Sxx − Sx², 0) · max(n·Syy − Sy², 0))
(cuDF implements rolling sum but not rolling().corr() — see
_common.df_utils.rolling_corr; the running-sum algebra here is the same
identity applied to the single pair per group, so no pairwise tensor is
needed). Guards mirror the shared kernel: zero variance → NaN, and
|corr| > 1 + 1e-9 (float rounding garbage on a near-degenerate window) →
NaN.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg
from analysis.options.config import (
    VOL_SPOT_CORR_RESULT_COLUMNS,
    VOL_SPOT_CORR_STRIDE,
    VOL_SPOT_CORR_WINDOWS,
)

# Mathematical impossibility margin for the corr ratio (|corr| <= 1).
_EPS = 1e-9

_GROUP_KEY = ["underlying_code"]
_PAIR_KEY = ["underlying_code", "date"]


def _rolling_pearson(
    frame: pd.DataFrame,
    window: int,
) -> pd.Series:
    """Pearson corr of the joint-masked columns xm/ym over the trailing
    ``window`` sessions per underlying, aligned to ``frame``'s index.

    ``frame`` carries the precomputed operand columns: v (joint-valid 0/1
    float), xm/ym (changes masked to NaN where the pair is invalid) and
    their squares / product. Emitted only where the window holds
    ``window`` joint-valid pairs (n >= window — pandas min_periods=window
    semantics).
    """
    def _rsum(col: str) -> pd.Series:
        return grouped_rolling_agg(
            frame, _GROUP_KEY, col, window, agg="sum", min_periods=1,
        )

    n = _rsum("v")
    sx = _rsum("xm")
    sy = _rsum("ym")
    sxx = _rsum("xm2")
    syy = _rsum("ym2")
    sxy = _rsum("xym")

    num = n * sxy - sx * sy
    d1 = np.maximum(n * sxx - sx * sx, 0.0)
    d2 = np.maximum(n * syy - sy * sy, 0.0)
    den = np.sqrt(d1 * d2)
    with np.errstate(invalid="ignore", divide="ignore"):
        raw = num / den
    ok = (n >= window) & (den > 0.0) & (raw.abs() <= 1.0 + _EPS)
    return raw.where(ok)


def compute_options_vol_spot_corr(
    df: pd.DataFrame,
    stored_max: pd.DataFrame | None = None,
    backfill: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Compute the stride-seeded, append-only rolling vol-vs-spot
    correlations.

    Args:
        df: DataFrame from fetch.fetch_vol_spot_corr_rows with columns:
            date, underlying_code, vol_index_30d, spot_close.
        stored_max: optional DataFrame [underlying_code, max_date] from
            fetch.fetch_stored_max_corr_dates — the per-underlying
            latest stored corr date. Sessions STRICTLY AFTER it are
            emitted on top of the stride/seed samples (the daily tail
            growth); underlyings absent from the frame keep the pure
            seed rule (stride grid + latest). None in --force (full
            stride rebuild).
        backfill: optional DataFrame with the PK columns from
            fetch.fetch_null_corr5_pairs — stored rows whose corr_5d is
            still NULL (pre-column rows). They are emitted so the upsert
            fills the new column (every other value recomputes
            identically).

    Returns:
        DataFrame with VOL_SPOT_CORR_RESULT_COLUMNS (dates datetime64) —
        the sampled rows, corr columns NaN until each window fills with
        joint-valid pairs.
    """
    from analysis.options.config import VOL_SPOT_CORR_RESULT_COLUMNS as _cols

    if df.empty:
        return pd.DataFrame(columns=_cols)

    frame = df[["date", "underlying_code"]].copy()
    # Log levels — spot/vol <= 0 (loader "no value" sentinels) or NaN
    # propagate to NaN, killing that session's change pair.
    spot_ok = df["spot_close"].notna() & df["spot_close"].gt(0.0)
    vol_ok = df["vol_index_30d"].notna() & df["vol_index_30d"].gt(0.0)
    frame["lx"] = np.log(df["spot_close"].where(spot_ok))
    frame["ly"] = np.log(df["vol_index_30d"].where(vol_ok))

    frame = frame.sort_values(
        _GROUP_KEY + ["date"], ignore_index=True,
    )
    grp = frame.groupby(_GROUP_KEY, sort=False)
    frame["x"] = grp["lx"].diff()
    frame["y"] = grp["ly"].diff()

    # Joint-valid pair masking: xm/ym NaN wherever EITHER change is
    # missing, so every rolling sum below counts pairwise-valid
    # contributions only (pandas rolling-corr pairwise NaN semantics).
    valid = frame["x"].notna() & frame["y"].notna()
    frame["xm"] = frame["x"].where(valid)
    frame["ym"] = frame["y"].where(valid)
    frame["v"] = valid.astype("float64")
    frame["xm2"] = frame["xm"] * frame["xm"]
    frame["ym2"] = frame["ym"] * frame["ym"]
    frame["xym"] = frame["xm"] * frame["ym"]

    for w in VOL_SPOT_CORR_WINDOWS:
        frame[f"corr_{w}d"] = _rolling_pearson(frame, w)

    # Samples (append-only): the stride grid + the seed tail — a pure
    # 1-based row ordinal, matched by the candidates SQL — and, for
    # underlyings with stored corr history, every session strictly AFTER
    # the stored frontier date (the daily tail growth), plus any stored
    # row whose corr_5d is still NULL (the pre-column backfill). The
    # merges are the cudf-native hash joins, NOT Series.map(dict) on the
    # string code column — the numba-state lottery the dtypes skill
    # bans. The pipeline's target_pairs filter picks exactly the missing
    # candidates, so emitting an already-stored date here is harmless.
    if stored_max is not None and not stored_max.empty:
        frame = frame.merge(stored_max, on="underlying_code", how="left")
        after_stored = frame["date"] > frame["max_date"]
    else:
        after_stored = None
    if backfill is not None and not backfill.empty:
        backfill = backfill.assign(_null5=np.int8(1))
        frame = frame.merge(backfill, on=_PAIR_KEY, how="left")
        is_backfill = frame["_null5"].notna()
    else:
        is_backfill = None
    frame["rn"] = frame.groupby(_GROUP_KEY, sort=False).cumcount() + 1
    emit = (
        (frame["rn"] % VOL_SPOT_CORR_STRIDE == 0)
        | (frame["rn"] == frame.groupby(
            _GROUP_KEY, sort=False,
        )["rn"].transform("max"))
    )
    if after_stored is not None:
        emit = emit | after_stored
    if is_backfill is not None:
        emit = emit | is_backfill
    out = frame.loc[emit, _cols].reset_index(drop=True)
    return out
