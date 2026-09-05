"""Internal band-break streak step for analyze.pe_and_dividends.

Band-BREAK excursion streaks audited against
analysis.pe_and_dividend_pct: one row per excursion streak per
(sec_type, code, date_year_month, metric, period, pct_type) in
analysis.pe_and_dividend_pct_streaks — the mov_ave_high_low_pct_streaks
pattern (high/low price streaks) applied to the pe_ma20 / dividend_yield
series.

=======================================================================
  FINANCIAL SEMANTICS
=======================================================================

For each (metric, period, pct_type) band — 255/500/750/1275 observations
x pct_type 1/5/10, metric ∈ {pe_ma20, dividend_yield} — each banded
TRADING day is tested with a VALUE-based breakout: a day is
OUT-OF-BAND when its metric value falls ABOVE the day's own month-band
``high_val`` or BELOW ``low_val``. A high streak = the metric is
STRETCHED vs its own trailing history (expensive PE / rich yield); a
low streak = COMPRESSED (cheap PE / lean yield).

NON-TRADING vendor rows are EXCLUDED before classification (the stats
identity tables carry ffilled OHLC rows for weekday CN holidays — e.g.
the 8 golden-week rows between Sep 30 and Oct 9 — which would otherwise
count as in-band "trading" days, artificially splitting streaks across
holidays and inflating day_count; see high_low_pct_streaks.py). The day
frame is filtered to the project CN trading calendar
(_common._holidays_and_weekdays.is_trading_day: adjusted workdays →
CN holidays → Mon-Fri rule) so a streak's span, gap tolerance and
day_count are all in REAL trading rows.

NULL-metric rows are excluded too (pe_ma20 before the PE source warms
up / on invalid-PE days; dividend_yield until the first trailing-12m
dividend window fills) — a streak's span contains only genuine
observations; a NULL stretch is invisible to the construction (neither
an in-band gap nor a break).

An excursion STREAK is the maximal consolidation of same-side
out-of-band days where re-entries into the band of up to
PD_PCT_GAP_TOLERANCE (5) consecutive TRADING days are TOLERATED
(bridged — the in-band gap stays INSIDE the streak's span and counts in
day_count). A longer in-band gap ends the streak; so does a side switch
(above -> below or vice versa — a new streak starts at the first day of
the opposite-side excursion). start_date / end_date bound the span: the
FIRST and LAST out-of-band trading rows. Trailing in-band days after
end_date are NOT part of the streak — they may later become a bridged
gap of a still-extending streak.

Streaks can span calendar months; each day is tested against its OWN
month's band, while date_year_month on the row records the streak's
START month (the band-month context in which the excursion began).

Per-streak measures (over the whole span, bridged days included):
start_value / end_value = the metric value on start_date / end_date;
max_value / min_value = max / min of the metric over the span;
day_count = trading rows in [start_date, end_date]; std_dev = population
std (ddof=0) of day-over-day metric-value changes in the metric's own
units (0.00 for single-day streaks). The SIDE (high/low) is NOT stored —
the API derives it by comparing end_value against the END month's band
(a streak never switches sides, so the end day's own-month band decides
exactly).

=======================================================================
  REBUILD SEMANTICS (episodes SHIFT — wholesale per sec_type)
=======================================================================

Unlike the bands (trailing windows, immutable per completed month),
episodes SHIFT when new data arrives: a code's LAST streak is open-ended
until a 6+-day in-band gap (or a side switch) closes it, and trailing
in-band days may later become a bridged gap — so a per-date PK coverage
check cannot maintain this table. Like the price streaks
(mov_ave_high_low_pct_streaks / market_hypes episodes precedent),
streaks are rebuilt WHOLESALE per sec_type on every parent run that
processes the sec_type: DELETE the scope's rows (sec_type, or the single
code in --code mode), recompute from the in-memory detail DataFrame
joined against the BANDS TABLE (computed by the pct_bands step earlier
in the same run — one SELECT, no recomputation), CSV-COPY insert,
registry upsert.

Vectorization: the whole episode construction per (metric, period,
pct_type) is column ops only — side classification, consecutive-in-band
run-length via groupby-cumcount, episode ids via cumsum of break-events,
trailing-in-band drop via per-streak max out-position, and one groupby
aggregation. No python row loops; the 24 (metric, period, pct_type)
combos iterate a vectorized body (the rolling-quantile precedent —
passes cannot be merged).

GPU note: the per-combo day/band merge runs on the cudf.pandas proxy;
the run-length / episode machinery drops to host numpy via host_array()
once per combo (deterministic dtypes, no proxied-array poisoning),
mirroring the price streaks step's host-at-the-boundary contract. The
aggregated streak frame is host-side for the CSV COPY render.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from _common._holidays_and_weekdays import is_trading_day
from _common.build_commons import rec_cols
from _common.db_commons import csv_copy_from_frame_async
from _common.df_utils import host_array
from analyze._common import upsert_analysis_identity
from analyze.pe_and_dividends.config import (
    PD_PCT_GAP_TOLERANCE,
    PD_PCT_METRICS,
    PD_PCT_PERIODS,
    PD_PCT_STREAKS_COLUMNS,
    PD_PCT_STREAKS_DESCRIPTION,
    PD_PCT_STREAKS_NAME,
    PD_PCT_STREAKS_TABLE,
    PD_PCT_TABLE,
    PD_PCT_TYPES,
)

# Source columns needed per detail row (the parent's in-memory detail
# frame carries them; the two metric columns are melted into the long
# (metric, value) day frame inside the compute).
_DAY_SRC_COLS = ("sec_type", "code", "date", "pe_ma20", "dividend_yield")

# Streak rows per CSV COPY chunk (bounds the in-memory chunk sliced off
# the long frame before rendering — the high_low_pct_streaks.py
# precedent).
_STREAK_CHUNK_ROWS = 200_000


# ---------------------------------------------------------------------------
#  Fetch (bands for the audited scope — computed earlier in the same run)
# ---------------------------------------------------------------------------

async def fetch_bands_async(
    conn, sec_type: str, code: str | None = None,
) -> pd.DataFrame:
    """Load the analysis.pe_and_dividend_pct band rows for ``sec_type``
    (optionally a single code) into a host DataFrame.

    The bands step runs BEFORE this step in the same pipeline run, so
    the table holds the current scope's bands (in --code mode the bands
    step deleted + recomputed the code's bands first).
    """
    where = "WHERE sec_type = $1"
    args: list = [sec_type]
    if code is not None:
        where += " AND code = $2"
        args.append(code)
    rows = await conn.fetch(
        f"SELECT sec_type, code, date_year_month, metric, period, "
        f"pct_type, high_val, low_val FROM {PD_PCT_TABLE} {where}",
        *args,
    )
    cols = rec_cols(rows)
    if not cols:
        return pd.DataFrame(
            columns=["sec_type", "code", "date_year_month", "metric",
                     "period", "pct_type", "high_val", "low_val"],
        )
    bnd = pd.DataFrame(cols)
    bnd["date_year_month"] = pd.to_datetime(bnd["date_year_month"])
    bnd["period"] = bnd["period"].astype("int64")
    bnd["pct_type"] = bnd["pct_type"].astype("int64")
    bnd["high_val"] = bnd["high_val"].astype("float64")
    bnd["low_val"] = bnd["low_val"].astype("float64")
    return bnd


# ---------------------------------------------------------------------------
#  Compute (host numpy run-length machinery at the write boundary)
# ---------------------------------------------------------------------------

def compute_pct_excursion_streaks(
    df: pd.DataFrame, bands: pd.DataFrame,
) -> pd.DataFrame:
    """Compute band-break excursion streaks for all (metric, period,
    pct_type) combos.

    Args:
      df: daily detail frame sorted by (sec_type, code, date) with a
          reset (positional) index, carrying _DAY_SRC_COLS. FULL
          per-code history of the sec_type. Non-trading vendor rows
          (ffilled weekday-holiday rows) and NULL-metric rows are
          dropped inside — streaks are computed over REAL observation
          days only.
      bands: the analysis.pe_and_dividend_pct rows for this sec_type
          (see fetch_bands_async).

    Returns:
      Long frame with PD_PCT_STREAKS_COLUMNS — one row per excursion
      streak; empty when no band/day joins or no breakouts.
    """
    out_cols = list(PD_PCT_STREAKS_COLUMNS)
    if df.empty or bands.empty:
        return pd.DataFrame(columns=out_cols)

    # ---- Long (metric, value) day frame ---------------------------------
    # One row per (code, date, metric) with a non-NULL value. The melt
    # is two column projections + concat — no per-row loops.
    df = df[list(_DAY_SRC_COLS)]
    day_frames: list[pd.DataFrame] = []
    base_cols = list(("sec_type", "code", "date"))
    for metric in PD_PCT_METRICS:
        if metric not in df.columns:
            continue
        mdf = df[base_cols + [metric]].dropna(subset=[metric]).rename(
            columns={metric: "value"}
        )
        mdf["metric"] = metric
        day_frames.append(mdf[base_cols + ["metric", "value"]])
    if not day_frames:
        return pd.DataFrame(columns=out_cols)
    day = pd.concat(day_frames, ignore_index=True)
    day = day.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)

    # ---- Trading-day filter --------------------------------------------
    # The stats identity tables carry ffilled OHLC rows for weekday CN
    # holidays — they would classify as in-band "trading" days and split
    # streaks across holidays (e.g. the 8-row golden week between Sep 30
    # and Oct 9). Keep only real trading days per the project CN calendar
    # (one python loop over the frame's UNIQUE dates — a few thousand —
    # then a vectorized isin).
    _norm = day["date"].dt.normalize()
    _uniq = _norm.unique()
    _ok = pd.DatetimeIndex(
        [u for u in _uniq if is_trading_day(pd.Timestamp(u).date())]
    )
    day = day[_norm.isin(_ok)].reset_index(drop=True)
    if day.empty:
        return pd.DataFrame(columns=out_cols)

    # Month key shared by the day rows and the bands (year*100+month —
    # the bands step's anchor convention).
    day["_ym"] = (
        day["date"].dt.year.astype("int64") * 100
        + day["date"].dt.month.astype("int64")
    )
    bnd = bands.copy()
    bnd["_ym"] = (
        bnd["date_year_month"].dt.year.astype("int64") * 100
        + bnd["date_year_month"].dt.month.astype("int64")
    )

    gap = PD_PCT_GAP_TOLERANCE
    frames: list[pd.DataFrame] = []
    for metric in PD_PCT_METRICS:
        m_day = day[day["metric"] == metric]
        m_bnd = bnd[bnd["metric"] == metric]
        for period in PD_PCT_PERIODS:
            for pct in PD_PCT_TYPES:
                b = m_bnd[
                    (m_bnd["period"] == period) & (m_bnd["pct_type"] == pct)
                ]
                # Inner join on (sec_type, code, _ym): each banded day
                # gets its own month's band; unbanded days/codes drop
                # out.
                m = m_day.merge(
                    b[["sec_type", "code", "_ym", "high_val", "low_val"]],
                    on=["sec_type", "code", "_ym"], how="inner",
                )
                if m.empty:
                    continue
                m = m.sort_values(
                    ["sec_type", "code", "date"]
                ).reset_index(drop=True)

                # ---- Host unwrap (one pass per combo) ---------------
                sec = host_array(m["sec_type"].to_numpy())
                code = host_array(m["code"].to_numpy())
                dates = host_array(m["date"].to_numpy())
                # Values + band edges are rounded to the table's 6-dp
                # storage precision BEFORE classification: the API
                # derives the streak's side by comparing the STORED
                # end_value against the STORED band row, so the
                # classification must run on exactly those quantized
                # values — otherwise a raw-value break whose stored
                # values round equal (dividend_yield at index scale,
                # ~1e-7) would yield no side at query time.
                values = np.round(
                    host_array(m["value"].to_numpy(dtype="float64")), 6
                )
                high_vals = np.round(
                    host_array(m["high_val"].to_numpy(dtype="float64")), 6
                )
                low_vals = np.round(
                    host_array(m["low_val"].to_numpy(dtype="float64")), 6
                )

                # ---- Side classification: +1 above band, -1 below,
                # 0 in-band.
                n = len(m)
                side = np.zeros(n, dtype=np.int8)
                side[values > high_vals] = 1
                side[values < low_vals] = -1
                out = side != 0

                # ---- Group-change mask ((sec_type, code) groups are
                # contiguous after the sort) and consecutive-in-band
                # runs.
                gc = np.ones(n, dtype=bool)
                gc[1:] = (sec[1:] != sec[:-1]) | (code[1:] != code[:-1])
                grp = np.cumsum(gc)  # per-(sec_type, code) group id
                reset = out | gc
                # cumcount within reset buckets = consecutive in-band
                # count for in-band rows (each bucket starts at an out
                # row or a group start); out rows get 0. np.array forces
                # a WRITABLE copy — pandas ≥3 to_numpy() returns a
                # read-only view under CPU pandas (cudf.pandas
                # materializes writable).
                consec = np.array(
                    pd.Series(reset).groupby(reset.cumsum()).cumcount(),
                    dtype=np.int64,
                )
                consec[out] = 0

                # ---- Side of the most recent out-of-band row (ffill),
                # masked to rows at/after the group's first out row so
                # the fill never leaks across codes.
                pos_f = np.arange(n, dtype=np.float64)
                out_pos = np.where(out, pos_f, np.nan)
                first_out = np.array(
                    pd.Series(out_pos).groupby(grp).transform("min"),
                    dtype=np.float64,
                )
                last_side = np.array(
                    pd.Series(
                        np.where(out, side, np.nan).astype(np.float64)
                    ).ffill(),
                    dtype=np.float64,
                )
                has_open = out | (pos_f > first_out)  # NaN cmp -> False
                last_side[~has_open] = 0.0  # 0 = no open episode yet

                # ---- Episode continuity ------------------------------
                # gap_before = in-band run length immediately before the
                # row (0 when the previous row was out); prev_side =
                # side of the previous row's open episode. Both masked
                # at group starts (a streak never continues across
                # codes).
                gap_before = np.r_[gap + 1, consec[:-1].astype(np.int64)]
                gap_before[gc] = gap + 1  # group start: cannot continue
                prev_side = np.r_[0.0, last_side[:-1]]
                prev_side[gc] = 0.0
                cont_out = (
                    out & (prev_side != 0) & (prev_side == side)
                    & (gap_before <= gap)
                )
                new_ep = out & ~cont_out
                # Episode id: cumsum of break-events, scoped per code
                # group. The group baseline samples gcum at the group's
                # FIRST row but EXCLUDES a new_ep firing on that same
                # row — otherwise a code whose first banded day is
                # itself a break-out would number its first episode 0
                # and its bridged in-band rows would fail the ep_id > 0
                # keep-test below.
                gcum = np.cumsum(new_ep)
                grp_start = (
                    pd.Series(
                        (gcum - new_ep.astype(np.int64)).astype(np.float64)
                    )
                    .where(pd.Series(gc)).ffill().fillna(0.0)
                    .to_numpy(dtype=np.int64)
                )
                ep_id = gcum - grp_start  # 1-based per-code episode ordinal

                # ---- Episode rows: out rows + bridged in-band rows ---
                in_keep = (
                    ~out & (last_side != 0) & (consec <= gap) & (ep_id > 0)
                )
                ep_rows = out | in_keep
                if not ep_rows.any():
                    continue

                pos = np.arange(n, dtype=np.int64)
                sub = pd.DataFrame({
                    "sec_type": sec, "code": code,
                    "date": dates,
                    "value": values,
                    "ep_id": ep_id, "pos": pos, "is_out": out,
                })[ep_rows]
                # ---- Drop trailing in-band rows: a streak ends at its
                # last OUT-OF-BAND row (trailing in-band days are a
                # candidate next bridge, not part of the streak).
                gkey = [sub["sec_type"], sub["code"], sub["ep_id"]]
                last_out_pos = (
                    sub["pos"].where(sub["is_out"]).groupby(gkey)
                    .transform("max")
                )
                sub = sub[sub["pos"] <= last_out_pos].copy()

                # ---- Per-streak measures (vectorized group ops) ------
                g = sub.groupby(gkey, sort=False)
                first_pos = g["pos"].transform("min")
                last_pos = g["pos"].transform("max")
                # value of the first row / of the last row via masked
                # group max (avoids agg 'first'/'last' dispatch).
                sub["_start_value"] = (
                    sub["value"].where(sub["pos"] == first_pos)
                    .groupby(gkey).transform("max")
                )
                sub["_end_value"] = (
                    sub["value"].where(sub["pos"] == last_pos)
                    .groupby(gkey).transform("max")
                )
                # Population std (ddof=0) of within-streak value diffs
                # in the metric's own units (single-day streaks -> 0.00).
                dd = g["value"].diff()
                dd_cnt = dd.groupby(gkey).transform("count")
                dd_var = (
                    (dd - dd.groupby(gkey).transform("mean")) ** 2
                ).groupby(gkey).transform("sum") / dd_cnt
                sub["_std"] = np.sqrt(dd_var.fillna(0.0))

                agg = sub.groupby(gkey, sort=False, as_index=False).agg(
                    start_date=("date", "min"),
                    end_date=("date", "max"),
                    start_value=("_start_value", "max"),
                    end_value=("_end_value", "max"),
                    max_value=("value", "max"),
                    min_value=("value", "min"),
                    day_count=("pos", "count"),
                    std_dev=("_std", "max"),
                )
                agg.insert(2, "metric", metric)
                agg.insert(3, "period", np.int64(period))
                agg.insert(4, "pct_type", np.int64(pct))
                frames.append(agg)

    if not frames:
        return pd.DataFrame(columns=out_cols)
    out = pd.concat(frames, ignore_index=True)
    # date_year_month = the streak's START month first day.
    out["date_year_month"] = (
        out["start_date"].to_numpy().astype("datetime64[M]")
        .astype("datetime64[ns]")
    )
    for c in ("start_value", "end_value", "max_value", "min_value",
              "std_dev"):
        # NUMERIC(12,6) target — round at the boundary so the CSV render
        # carries the stored precision (serves both pe_ma20-scale and
        # dividend_yield-scale metrics).
        out[c] = out[c].round(6)
    out["day_count"] = out["day_count"].astype("int64")
    return out[out_cols]


async def _copy_streaks_chunked(conn, streaks: pd.DataFrame) -> int:
    """CSV-COPY the streaks frame in row-count chunks.

    Sequential on ``conn`` (streak rows are sparse excursion episodes —
    a small fraction of the daily universe; the price streaks step's
    precedent). Conflict-free by construction: the scope was DELETEd
    upfront (wholesale rebuild).
    """
    n_total = len(streaks)
    columns = list(PD_PCT_STREAKS_COLUMNS)
    n_chunks = (n_total + _STREAK_CHUNK_ROWS - 1) // _STREAK_CHUNK_ROWS
    total = 0
    for i in range(n_chunks):
        lo = i * _STREAK_CHUNK_ROWS
        chunk = streaks.iloc[lo:lo + _STREAK_CHUNK_ROWS]
        n = await csv_copy_from_frame_async(
            conn, PD_PCT_STREAKS_TABLE, chunk, columns=columns,
        )
        total += n
        print(f"      streaks chunk {i + 1}/{n_chunks}: COPY {n:,} rows "
              f"(cumulative {total:,})", flush=True)
    return total


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from pe_and_dividends.__main__)
# ---------------------------------------------------------------------------

async def run_pd_pct_streaks(
    conn,
    detail_df: pd.DataFrame,
    *,
    sec_type: str,
    code_filter: str | None = None,
) -> None:
    """Run the band-break streak pipeline against the detail frame already
    computed by the parent pe_and_dividends run.

    WHOLESALE rebuild of the scope (episodes shift with new data — see
    the module docstring): DELETE the scope's streak rows (sec_type, or
    the single code in --code mode), fetch the bands computed by the
    pct_bands step earlier in the same run, compute the excursion
    streaks from the in-memory detail DataFrame, CSV-COPY insert,
    registry upsert.

    Args:
      conn: asyncpg connection (reused from parent).
      detail_df: the daily detail frame (build_detail_rows output) with
          at least _DAY_SRC_COLS — FULL per-code history of ONE
          sec_type.
      sec_type: the frame's sec_type (parent loop passes one at a time
          to bound memory).
      code_filter: single-code mode (--code): only this code's streak
          rows are deleted + rebuilt.
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  PE_AND_DIVIDEND_PCT_STREAKS (internal step of "
          "pe_and_dividends)", flush=True)
    print("=" * 78, flush=True)
    if code_filter is not None:
        print(f"    mode: SINGLE-CODE (full streak rebuild for "
              f"{code_filter})", flush=True)
    else:
        print(f"    mode: WHOLESALE for {sec_type} (episodes shift with "
              f"new data; mov_ave_high_low_pct_streaks precedent)",
              flush=True)

    if detail_df.empty:
        print("    -> no detail data; skipping streaks step.", flush=True)
        return

    # ---- Wholesale scope wipe (episodes shift; PK coverage diffing
    # does not apply to this table).
    if code_filter is not None:
        await conn.execute(
            f"DELETE FROM {PD_PCT_STREAKS_TABLE} "
            f"WHERE sec_type = $1 AND code = $2",
            sec_type, code_filter,
        )
    else:
        await conn.execute(
            f"DELETE FROM {PD_PCT_STREAKS_TABLE} WHERE sec_type = $1",
            sec_type,
        )

    # ---- Bands for the audited scope (bands step ran earlier) ---------
    bnd = await fetch_bands_async(conn, sec_type, code=code_filter)
    if bnd.empty:
        print(f"    -> {sec_type}: no bands in {PD_PCT_TABLE}; "
              f"skipping streaks.", flush=True)
        return

    # ---- Compute + insert ----------------------------------------------
    streaks = compute_pct_excursion_streaks(detail_df, bnd)
    n_codes = (
        streaks[["sec_type", "code"]].drop_duplicates().shape[0]
        if not streaks.empty else 0
    )
    print(f"    -> {sec_type}: {len(streaks):,} excursion streaks across "
          f"{n_codes:,} codes (gap tolerance {PD_PCT_GAP_TOLERANCE} "
          f"trading days)", flush=True)
    if not streaks.empty:
        n = await _copy_streaks_chunked(conn, streaks)
        print(f"    -> {sec_type}: inserted {n:,} streak rows", flush=True)

    # ---- Register in analysis_identity ----------------------------------
    await upsert_analysis_identity(
        conn,
        name=PD_PCT_STREAKS_NAME,
        detail_name="pe_and_dividend_pct_streaks",
        description=PD_PCT_STREAKS_DESCRIPTION,
    )

    print(f"\n  pe_and_dividend_pct_streaks wall time: "
          f"{time.time() - t0:.1f}s", flush=True)