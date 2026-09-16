"""Long → wide grid factorization (analyze.analysis_forecasts.wide.grid).

The monthly engines work on (T, C) numpy matrices — T = union trading-day
grid rows, C = codes — so a whole sec_type is aggregated with vectorized
passes instead of per-code Python loops. The GPU work happens BELOW this
boundary: the fetch layer loads and derives everything through
cudf.pandas DataFrames (vectorized grouped ops), and ``scatter_column``
unwraps the cudf.pandas proxy ONCE at the pandas→numpy edge
(``host_array``) — from there down the engine math never pays a
GPU↔CPU transfer or a cudf fallback.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from _common.df_utils import host_array

from analyze.analysis_forecasts.config import (
    PX_VOL_SPEED_ORD,
    PX_VOL_VOL_ORD,
)


def date_ordinals(s: pd.Series) -> np.ndarray:
    """datetime64 Series → REAL int64 host ndarray of day ordinals.

    Unwraps the cudf.pandas proxy ONCE at the pandas→numpy boundary
    (``.to_numpy()`` on a proxy Series returns a proxy-subclass ndarray
    whose every downstream op dispatches into cudf).
    """
    return host_array(s.to_numpy()).astype("datetime64[D]").astype(np.int64)


def build_grid(
    df: pd.DataFrame,
) -> tuple[np.ndarray, list[str], np.ndarray, np.ndarray]:
    """Factorize the long frame into the (T, C) grid coordinates.

    Args:
        df: long frame sorted by (code, date) with columns date + code.

    Returns:
        (grid_ord, codes, didx, cidx):
        grid_ord — (T,) sorted int64 day ordinals (union date grid);
        codes    — (C,) sorted code strings;
        didx     — (N,) per-row date index into the grid;
        cidx     — (N,) per-row code index.
    """
    dord = date_ordinals(df["date"])
    grid_ord = np.unique(dord)  # sorted union grid
    # np.unique(return_inverse) gives (sorted uniques, inverse) in one
    # deterministic call on a REAL host array (unwrapped above pattern),
    # so the outputs are real ndarrays — safe for numpy fancy indexing.
    code_arr = host_array(df["code"].to_numpy())
    codes_arr, cidx = np.unique(code_arr, return_inverse=True)
    cidx = cidx.astype(np.int64, copy=False)
    didx = np.searchsorted(grid_ord, dord).astype(np.int64, copy=False)
    return grid_ord, [str(c) for c in codes_arr], didx, cidx


def first_ords_from_dates(
    first_dates: dict[str, date],
    codes: list[str],
) -> np.ndarray:
    """Per-code first data date as ABSOLUTE epoch-day ordinals (days
    since 1970-01-01 — the same unit as grid_ord / MonthWindow.lo_ord).

    The dates are the codes' TRUE first data dates (min(date) from the
    source table — NOT derivable from the fetched frame, which is
    bounded to the earliest needed window start and would clip
    long-history codes to the fetch boundary).

    Codes without a date map to the int64 sentinel (never live). Used
    to gate the monthly buckets in DATE space (NOT grid-row space —
    the grid may begin after the nominal window start, so row-space
    lo would be clamped to 0 and wrongly pass codes first listed at
    the grid start): a code enters a stat_month only once its own
    history strictly PRECEDES the window start (first data month +
    60 months = first snapshot) — a code first listed 2020-01 first
    appears in the 2025-01 snapshot, NOT 2024-12 whose window merely
    STARTS at the first data date.
    """
    sentinel = np.iinfo(np.int64).max
    first = np.full(len(codes), sentinel, dtype=np.int64)
    code_pos = {c: i for i, c in enumerate(codes)}
    epoch = date(1970, 1, 1)
    for c, d in first_dates.items():
        i = code_pos.get(c)
        if i is not None:
            first[i] = (d - epoch).days
    return first


def scatter_column(
    df: pd.DataFrame,
    col: str,
    shape: tuple[int, int],
    didx: np.ndarray,
    cidx: np.ndarray,
    dtype: np.dtype = np.float64,
) -> np.ndarray:
    """Long column → (T, C) wide matrix (one fancy-index scatter).

    The ONE pandas→numpy boundary of the pipeline: the column is
    unwrapped from its cudf.pandas proxy once here (host_array) and the
    scatter itself is a single fancy-index assignment on a real host
    ndarray. float matrices are NaN-initialized (missing cell = no row
    that date); bool matrices are False-initialized. Source (code, date)
    pairs are unique (DB PKs), so no scatter collisions.
    """
    fill = np.nan if dtype == np.float64 else False
    mat = np.full(shape, fill, dtype=dtype)
    vals = host_array(df[col].to_numpy())
    mat[didx, cidx] = vals.astype(dtype, copy=False)
    return mat


def build_px_vol_state_matrices(
    states_df: pd.DataFrame,
    grid_ord: np.ndarray,
    codes: list[str],
    shape: tuple[int, int],
) -> dict[str, np.ndarray]:
    """Scatter the price_vs_amt registry rows into the (T, C) grid.

    Args:
        states_df: fetch_price_vs_amt_states' frame (code, date,
            px_speed, vol_state, px_t, px_z — sorted by (code, date)).
        grid_ord: the MAIN grid's sorted day ordinals (build_grid).
        codes: the MAIN grid's sorted code list.
        shape: (T, C).

    Returns:
        Wide state matrices keyed:
          "speed" — (T, C) int8 speed ordinal 0..4 (PX_VOL_SPEEDS
                    order), -1 = no valid state that day
          "vol"   — (T, C) int8 vol ordinal 0..2 (PX_VOL_VOL_STATES
                    order), -1 = none
          "t" / "z" — (T, C) float64 of the recorded px_t / px_z
                    (NaN where no state — the bucket means' weights)

    The engines consume these INSTEAD of thresholding raw features: the
    registry (analysis.mov_ave_price_vs_amt) is the px_vol family's
    date-level source of truth, so the buckets audit against it 1:1.
    Registry dates outside the main grid (shouldn't happen — both
    derive from the same basic_stats close rows) are dropped.
    """
    T_n, C_n = shape
    speed = np.full(shape, -1, dtype=np.int8)
    vol = np.full(shape, -1, dtype=np.int8)
    t = np.full(shape, np.nan, dtype=np.float64)
    z = np.full(shape, np.nan, dtype=np.float64)
    if states_df.empty:
        return {"speed": speed, "vol": vol, "t": t, "z": z}

    dord = date_ordinals(states_df["date"])
    didx = np.searchsorted(grid_ord, dord)
    scodes = host_array(states_df["code"].to_numpy())
    codes_arr = np.asarray(codes)
    cidx = np.searchsorted(codes_arr, scodes)

    # Keep only (code, date) pairs that exist on the main grid.
    ok = didx < T_n
    ok[ok] &= cidx[ok] < C_n
    ok[ok] &= grid_ord[didx[ok]] == dord[ok]
    ok[ok] &= codes_arr[cidx[ok]] == scodes[ok]

    speed_idx = host_array(
        states_df["px_speed"].map(PX_VOL_SPEED_ORD).to_numpy(dtype="float64")
    )
    vol_idx = host_array(
        states_df["vol_state"].map(PX_VOL_VOL_ORD).to_numpy(dtype="float64")
    )
    speed[didx[ok], cidx[ok]] = speed_idx[ok].astype(np.int8)
    vol[didx[ok], cidx[ok]] = vol_idx[ok].astype(np.int8)
    t[didx[ok], cidx[ok]] = host_array(
        states_df["px_t"].to_numpy(dtype="float64")
    )[ok]
    z[didx[ok], cidx[ok]] = host_array(
        states_df["px_z"].to_numpy(dtype="float64")
    )[ok]
    return {"speed": speed, "vol": vol, "t": t, "z": z}


def build_hype_matrix(
    episodes: pd.DataFrame,
    grid_ord: np.ndarray,
    codes: list[str],
    shape: tuple[int, int],
) -> np.ndarray:
    """(T, C) bool matrix of market-hyped (grid date, code) cells.

    An episode (code, start_date..end_date inclusive, any
    min_checkin_period) marks every grid date it spans. Episodes of codes
    outside the active universe (delisted) are ignored; interval marking
    is a small per-episode slice loop on real host ndarrays (irregular
    intervals — not vectorizable without a blow-up to calendar rows).
    """
    H = np.zeros(shape, dtype=bool)
    if episodes.empty:
        return H
    codes_arr = np.asarray(codes)
    ep_codes = host_array(episodes["code"].to_numpy())
    cidx = np.searchsorted(codes_arr, ep_codes)
    ok = cidx < len(codes_arr)
    ok[ok] = codes_arr[cidx[ok]] == ep_codes[ok]
    s = date_ordinals(episodes["start_date"])
    e = date_ordinals(episodes["end_date"])
    lo = np.searchsorted(grid_ord, s)
    hi = np.searchsorted(grid_ord, e, side="right")
    for l, h, c in zip(lo[ok].tolist(), hi[ok].tolist(), cidx[ok].tolist()):
        if h > l:
            H[l:h, c] = True
    return H
