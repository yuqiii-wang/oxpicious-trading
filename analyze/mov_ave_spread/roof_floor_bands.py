"""Internal roof/floor BANDS step for analyze.mov_ave_spread.

Precomputes the UI shade toggle's roof/floor band edges + per-band BREACH
analytics into analysis.mov_ave_spreads_roof_and_floor_bands (LONG: one
row per (sec_type, code, date, period ∈ OHLC_WINDOWS)).

Band edges (same anchors as the sibling roof_and_floor_lines step —
recomputed here via the SAME vectorised sparse-table helper, from the
SAME parent source DataFrame, no second DB round-trip):
  - upper_band_low/high = min/max of high_Wd (1st-half max CLOSE) and
    high_2nd_Wd (2nd anchor's INTRADAY HIGH). The anchor pair is stored
    in DATE order, so the band spans min↔max of the two values — exactly
    the derivation the UI's shade rectangles use.
  - lower_band_low/high = the mirror on low_Wd / low_2nd_Wd.
  NULL when either anchor of the pair is absent.

BREACH semantics (directional CROSSING EVENTS, NOT "days beyond"):
  day t is an UPPER breach for period p iff
      close[t] > upper_band_high of the row (t-1, p)
  — the close breaks ABOVE the PRIOR day's rolling roof top (a fresh
  upward breakout). Falling back below the top is NOT a breach (only the
  upward direction counts), and a day that merely STAYS above an already
  broken level is not a NEW breach either (each day is measured against
  the prior day's band, which has absorbed the breakout). Mirror on the
  low side: close[t] < lower_band_low of (t-1, p).

  Row (T, p) aggregates the breach days inside its OWN trailing window
  [T-p+1, T]: upper/lower_band_breach_count + breach_dates[] (ascending).
  count == cardinality(dates) by construction, and a date d is a breach
  EVENT for period p iff d = ANY(breach_dates) of the row (d, p) — the
  predicate the API serves as the per-date breach flag.

Incremental mode (``force=False``): target dates = dates present in the
source identity tables but missing in the bands table (per sec_type).
Breach events at day t depend on the band of t-1 (both recomputed here
from the FULL per-code history), so — as in the lines step — the full
history is computed and filtered to target dates before the upsert. The
table is new (2026-10), so there are no legacy rows to repair; if the
breach semantics ever change, bump via a --force rebuild.

Force mode: purge the sec_type's rows (or TRUNCATE when no sec_type),
then recompute everything. Single-code mode (--code): the caller
pre-deleted the code's rows; recompute all its dates.

The breach_dates columns are rendered as PostgreSQL array-literal
strings ('{"2024-01-02","2024-01-05"}', '{}' when empty) at the compute
boundary: the CSV COPY writer's minimal quoting round-trips them into
the DATE[] columns (a field containing commas/quotes is CSV-escaped by
pandas, and PostgreSQL's CSV-mode parser + date[] input reader restore
the array). breach_count columns render as integer STRINGS for the same
reason as the lines step's days_since_last_extreme (cudf.pandas renders
Int64 columns as "3.0" via to_numpy, which INTEGER COPY rejects).
"""
from __future__ import annotations

import time
from typing import Optional, Set

import numpy as np
import pandas as pd

from _common.build_commons import (
    truncate_table_async,
    find_missing_analysis_dates,
)
from _common.db_commons import chunked_purge_async
from _common.df_utils import (
    column_subset,
    host_array,
    host_unique,
    safe_columns,
)
from analyze.mov_ave_spread.helpers import null_if_overflow_counted
from analyze._common import (
    build_and_insert_chunked_df,
    upsert_analysis_identity,
)
from analyze.mov_ave_spread.config import (
    BANDS_ANALYSIS_NAME,
    BANDS_COLUMNS,
    BANDS_DESCRIPTION,
    BANDS_EDGE_COLUMNS,
    BANDS_TABLE,
    OHLC_WINDOWS,
    SEC_TYPES,
    SEC_TYPE_IDENTITY_TABLE,
    bands_wide_columns,
)
from analyze.mov_ave_spread.ohlc_vector import (
    compute_group_anchors_all_windows,
)

import logging
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Compute helpers (per (sec_type, code) group, plain host numpy)
# ---------------------------------------------------------------------------

def _pair_band_edges(
    first: np.ndarray, second: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Band edges (low, high) from an anchor pair: NaN when EITHER anchor
    is absent (a one-sided band cannot be drawn), else min/max of the two
    values (the pair is stored in DATE order, not value order)."""
    both: np.ndarray = ~np.isnan(first) & ~np.isnan(second)
    lo: np.ndarray = np.where(both, np.fmin(first, second), np.nan)
    hi: np.ndarray = np.where(both, np.fmax(first, second), np.nan)
    return lo, hi


def _breach_events(
    close: np.ndarray, band_edge: np.ndarray, side: int,
) -> np.ndarray:
    """Boolean breach-EVENT flags for one (group, window, side).

    side=+1 (upper): close[t] > band_edge[t-1]; side=-1 (lower):
    close[t] < band_edge[t-1]. Day 0 has no prior band; NaN edges and
    NaN closes never breach (NaN comparisons are False in numpy).
    """
    prev: np.ndarray = np.full(len(close), np.nan)
    prev[1:] = band_edge[:-1]
    if side > 0:
        return close > prev
    return close < prev


def _window_breach_dates(
    ev: np.ndarray,
    dates64: np.ndarray,
    w: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Per-row in-window breach COUNT + PostgreSQL array-literal strings.

    For each row t the window is [max(0, t-w+1), t]; the count is the
    number of event days inside it and the string the '{"d1","d2"}'
    literal of their dates ('{}' when none) — count == cardinality by
    construction. Two searchsorted passes locate each window's event
    slice inside the (globally sorted) event index array; the literal is
    built only for rows with a non-empty slice (breach days are sparse).
    """
    n: int = len(ev)
    counts: np.ndarray = np.zeros(n, dtype=np.int64)
    date_strs: np.ndarray = np.full(n, "{}", dtype=object)
    ev_idx: np.ndarray = np.flatnonzero(ev)
    if ev_idx.size == 0 or n == 0:
        return counts, date_strs
    t_arr: np.ndarray = np.arange(n, dtype=np.int64)
    win_start: np.ndarray = np.maximum(0, t_arr - w + 1)
    left: np.ndarray = np.searchsorted(ev_idx, win_start, side="left")
    right: np.ndarray = np.searchsorted(ev_idx, t_arr + 1, side="right")
    counts = right - left
    ev_dates_str: np.ndarray = (
        dates64[ev_idx].astype("datetime64[D]").astype(str)
    )
    for r in np.flatnonzero(counts > 0):
        parts = ev_dates_str[left[r]:right[r]]
        date_strs[r] = '{"' + '","'.join(parts) + '"}'
    return counts, date_strs


def compute_bands_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add the per-window band-edge + breach wide columns to the source
    frame (in place), one (sec_type, code) group at a time.

    Reuses ``compute_group_anchors_all_windows`` (the same vectorised
    sparse-table anchors as the lines step) for the anchor values, then
    derives edges + breach events per window. The writes are BATCHED per
    group (one concat + one .loc write per column — cudf.pandas
    discipline B-A3, mirroring compute_ohlc_columns Phase 2).
    """
    if df.empty:
        for col in BANDS_COLUMNS:
            df[col] = pd.Series(dtype="object")
        return df

    grp_keys: list[str] = ["sec_type", "code"]
    cols = set(safe_columns(df))
    for col in ("price", "high", "low"):
        if col in cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    parts: list[pd.DataFrame] = []
    for (_st, _code), g in df.groupby(grp_keys, sort=False):
        n: int = len(g)
        anchors: pd.DataFrame = compute_group_anchors_all_windows(
            g[["date", "price", "high", "low"]],
            OHLC_WINDOWS,
        )
        close: np.ndarray = host_array(
            g["price"].to_numpy()
        ).astype("float64")
        dates64: np.ndarray = host_array(g["date"].to_numpy())
        out: dict[str, np.ndarray] = {}
        for w in OHLC_WINDOWS:
            h1: np.ndarray = host_array(
                anchors[f"high_{w}d"].to_numpy()
            ).astype("float64")
            h2: np.ndarray = host_array(
                anchors[f"high_2nd_{w}d"].to_numpy()
            ).astype("float64")
            l1: np.ndarray = host_array(
                anchors[f"low_{w}d"].to_numpy()
            ).astype("float64")
            l2: np.ndarray = host_array(
                anchors[f"low_2nd_{w}d"].to_numpy()
            ).astype("float64")
            ub_lo, ub_hi = _pair_band_edges(h1, h2)
            lb_lo, lb_hi = _pair_band_edges(l1, l2)
            ev_up: np.ndarray = _breach_events(close, ub_hi, +1)
            ev_dn: np.ndarray = _breach_events(close, lb_lo, -1)
            up_counts, up_dates = _window_breach_dates(ev_up, dates64, w)
            dn_counts, dn_dates = _window_breach_dates(ev_dn, dates64, w)
            for stem, arr in (
                (f"upper_band_low_{w}d", ub_lo),
                (f"upper_band_high_{w}d", ub_hi),
                (f"lower_band_low_{w}d", lb_lo),
                (f"lower_band_high_{w}d", lb_hi),
                (f"upper_band_breach_count_{w}d", up_counts),
                (f"upper_band_breach_dates_{w}d", up_dates),
                (f"lower_band_breach_count_{w}d", dn_counts),
                (f"lower_band_breach_dates_{w}d", dn_dates),
            ):
                out[stem] = arr
        parts.append(pd.DataFrame(out, index=g.index))

    if parts:
        real_concat = getattr(pd.concat, "_fsproxy_slow", pd.concat)
        all_bands = real_concat(parts)
        band_cols = safe_columns(all_bands)
        idx = all_bands.index
        for bc in band_cols:
            df.loc[idx, bc] = host_array(all_bands[bc].to_numpy())

    return df


def build_bands_long_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Melt the wide per-window bands frame to the long DB schema.

    Mirrors build_ohlc_long_frame: one slice + rename per window, a
    single concat, whole-column overflow guard on the edge columns, and
    the INTEGER render of the breach counts (float64 → int strings —
    see the module docstring for the CSV COPY rationale). The
    *_breach_dates columns are already array-literal STRINGS from the
    compute step.
    """
    if df.empty:
        return pd.DataFrame(
            columns=["sec_type", "code", "date", "period"] + list(BANDS_COLUMNS)
        )

    base: list[str] = ["sec_type", "code", "date"]
    parts: list[pd.DataFrame] = []
    for w in OHLC_WINDOWS:
        wide = list(bands_wide_columns(w))
        parts.append(
            df[base + wide]
            .rename(columns=dict(zip(wide, BANDS_COLUMNS)))
            .assign(period=w)
        )
    long_df = pd.concat(parts, ignore_index=True)
    long_df = long_df[base + ["period"] + list(BANDS_COLUMNS)]

    nulled: dict[str, int] = {}
    for c in BANDS_EDGE_COLUMNS:
        clean, n = null_if_overflow_counted(long_df[c], max_abs=1e12, scale=6)
        long_df[c] = clean
        if n > 0:
            nulled[c] = n
    if nulled:
        total: int = sum(nulled.values())
        per: str = ", ".join(f"{c}={n}" for c, n in nulled.items())
        logger.info(f"    -> overflow-guard nulled {total:,} value(s) across "
              f"{len(nulled)} column(s): {per}")

    # INTEGER render of the breach counts (CSV COPY discipline — the
    # compute arrays are float64-capable int64).
    for c in ("upper_band_breach_count", "lower_band_breach_count"):
        vals: np.ndarray = host_array(long_df[c].to_numpy())
        ok: np.ndarray = ~np.isnan(vals)
        strs: np.ndarray = np.full(len(vals), None, dtype=object)
        strs[ok] = vals[ok].astype("int64").astype(str)
        long_df[c] = strs

    return long_df


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__)
# ---------------------------------------------------------------------------

async def run_roof_floor_bands(
    conn,
    df: pd.DataFrame,
    *,
    force: bool = False,
    pool=None,
    max_concurrent: int = 20,
    sec_type: str | None = None,
    code_filter: str | None = None,
) -> None:
    """Run the bands pipeline against the source data already loaded by
    the parent mov_ave_spread.

    Mirrors run_ohlc's mode handling (incremental missing dates / force
    purge / single-code full recompute), computes over the FULL per-code
    history, filters to the target dates, and chunk-CSV-COPYs the melted
    long frame into analysis.mov_ave_spreads_roof_and_floor_bands.
    """
    t0 = time.time()
    logger.info("\n" + "=" * 78)
    logger.info("  ROOF_AND_FLOOR_BANDS (internal step of mov_ave_spread)")
    logger.info("=" * 78)

    needed_cols = ["sec_type", "code", "date", "price", "high", "low"]
    available = column_subset(df, needed_cols)
    bands_df = df[available].copy()

    if bands_df.empty:
        logger.info("    -> no source data; skipping bands step.")
        return

    if sec_type is not None:
        sec_types = (sec_type,)
    else:
        sec_types = tuple(host_unique(bands_df["sec_type"]))

    # ---- Step 0: determine target dates (per-sec_type) ------------------
    if code_filter is not None:
        logger.info("    mode: SINGLE-CODE (full recompute for this code)")
        target_dates_union: Optional[Set] = None
    elif force:
        logger.info("    mode: FORCE (full recompute)")
        if sec_type is not None:
            logger.info(f"\n[b0/3] Force mode: deleting {sec_type} rows from "
                  "mov_ave_spreads_roof_and_floor_bands...")
            n_del: int = await chunked_purge_async(
                conn, BANDS_TABLE,
                where_sql="sec_type = $1", params=(sec_type,),
            )
            logger.info(f"    -> deleted {n_del:,} rows; will recompute all "
                  f"{sec_type} rows")
        else:
            logger.info("\n[b0/3] Force mode: truncating "
                  "mov_ave_spreads_roof_and_floor_bands...")
            await truncate_table_async(conn, BANDS_TABLE)
            logger.info("    -> truncated; will recompute all rows")
        target_dates_union = None
    else:
        logger.info("    mode: incremental (missing dates only)")
        target_dates_union = set()
        for st in sec_types:
            td_st = await find_missing_analysis_dates(
                conn, BANDS_TABLE,
                [SEC_TYPE_IDENTITY_TABLE[st]], sec_type=st,
            )
            target_dates_union |= td_st
            logger.info(f"    -> {st}: {len(td_st)} missing dates")
        logger.info(f"    -> union across sec_types: "
              f"{len(target_dates_union)} dates to (re)compute")
        if not target_dates_union:
            logger.info("    -> DB is up to date; nothing to do.")
            return

    # ---- Step 1: compute band columns over full history, then filter ----
    logger.info("\n[b1/3] Computing band edges + breach analytics per "
          "(sec_type, code, date, window) over full history...")
    bands_df = compute_bands_columns(bands_df)

    if target_dates_union is not None and len(target_dates_union) > 0:
        n_before = len(bands_df)
        td64 = pd.to_datetime(sorted(target_dates_union)).values
        bands_df = bands_df[bands_df["date"].isin(td64)].reset_index(drop=True)
        logger.info(f"    -> incremental filter: {len(bands_df):,} of "
              f"{n_before:,} rows are in target_dates_union")

    if bands_df.empty:
        logger.info("    -> no rows to upsert; skipping bands upsert.")
        return

    # ---- Step 2: build + insert (chunked by date) ------------------------
    logger.info(f"\n[b2/3] Building + inserting {len(bands_df):,} wide rows "
          f"(x{len(OHLC_WINDOWS)} periods = "
          f"{len(bands_df) * len(OHLC_WINDOWS):,} long rows) in "
          f"date-bounded chunks via CSV COPY...")
    n = await build_and_insert_chunked_df(
        conn, pool, bands_df,
        build_bands_long_frame,
        table_name=BANDS_TABLE,
        force=force,
        sec_types=() if code_filter is not None else sec_types,
        max_concurrent=max_concurrent,
        label="mov_ave_spreads_roof_and_floor_bands",
    )
    del bands_df
    logger.info(f"    -> inserted {n:,} rows")

    # ---- Step 3: register in analysis_identity ---------------------------
    logger.info(f"\n[b3/3] Upserting analysis.analysis_identity registry...")
    await upsert_analysis_identity(
        conn,
        name=BANDS_ANALYSIS_NAME,
        detail_name="mov_ave_spreads_roof_and_floor_bands",
        description=BANDS_DESCRIPTION,
    )

    logger.info(f"\n  mov_ave_spreads_roof_and_floor_bands wall time: "
          f"{time.time() - t0:.1f}s")
