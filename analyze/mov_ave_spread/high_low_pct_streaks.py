"""Internal band-break streak step for analyze.mov_ave_spread.

Band-BREAK excursion streaks audited against
analysis.mov_ave_high_low_pct: one row per excursion streak per
(sec_type, code, date_year_month, period, pct_type) in
analysis.mov_ave_high_low_pct_streaks.

=======================================================================
  FINANCIAL SEMANTICS
=======================================================================

For each (period, pct_type) band — 255/500/750/1275 trading rows x
pct_type 1/5/10 — each banded TRADING day is tested with a CLOSE-based
breakout: a day is OUT-OF-BAND when its adjusted close (the parent
``price`` column) falls ABOVE the day's own month-band ``high_val`` or
BELOW ``low_val``. Intraday spikes do not trigger a streak; the
streak's own high/low columns carry that information.

NON-TRADING vendor rows are EXCLUDED before classification: the stats
identity tables carry ffilled OHLC rows for weekday CN holidays (NULL
trading_amount) — e.g. the 8 golden-week rows between Sep 30 and Oct 9
— which would otherwise count as in-band "trading" days, artificially
splitting streaks across holidays and inflating day_count. The day
frame is filtered to the project CN trading calendar
(_common._holidays_and_weekdays.is_trading_day: adjusted workdays →
CN holidays → Mon-Fri rule) so a streak's span, gap tolerance and
day_count are all in REAL trading rows.

An excursion STREAK is the maximal consolidation of same-side
out-of-band days where re-entries into the band of up to
HIGH_LOW_PCT_GAP_TOLERANCE (5) consecutive TRADING days are TOLERATED
(bridged — the in-band gap stays INSIDE the streak's span and counts
in day_count). A longer in-band gap ends the streak; so does a side
switch (above -> below or vice versa — a new streak starts at the
first day of the opposite-side excursion). start_date / end_date bound
the span: the FIRST and LAST out-of-band trading rows. Trailing
in-band days after end_date are NOT part of the streak — they may
later become a bridged gap of a still-extending streak.

Streaks can span calendar months; each day is tested against its OWN
month's band, while date_year_month on the row records the streak's
START month (the band-month context in which the excursion began).

Per-streak measures (over the whole span, bridged days included):
open = adjusted open on start_date; close = adjusted close on
end_date; high / low = max / min of the adjusted high / low prices;
day_count = trading rows in [start_date, end_date]; std_dev =
population std (ddof=0) of day-over-day adjusted-close changes in
PRICE units (0.00 for single-day streaks); daily_ave_trading_amt =
mean trading_amount.

=======================================================================
  REBUILD SEMANTICS (episodes SHIFT — wholesale per sec_type)
=======================================================================

Unlike the bands (trailing windows, immutable per completed month),
episodes SHIFT when new data arrives: a code's LAST streak is
open-ended until a 6+-day in-band gap (or a side switch) closes it,
and trailing in-band days may later become a bridged gap — so a
per-date PK coverage check cannot maintain this table. Like
mov_ave_market_hypes episodes (margin_changes precedent), streaks are
rebuilt WHOLESALE per sec_type on every run that processes the
sec_type: DELETE the sec_type's rows, recompute from the parent source
DataFrame joined against the BANDS TABLE (computed by the
high-low-percentile step earlier in the same run — one SELECT, no
recomputation), CSV-COPY insert, registry upsert.

Vectorization: the whole episode construction per (period, pct_type)
is column ops only — side classification, consecutive-in-band
run-length via groupby-cumcount, episode ids via cumsum of
break-events, trailing-in-band drop via per-streak max out-position,
and one groupby aggregation. No python row loops; the 12 (period,
pct_type) combos iterate a vectorized body (the rolling-quantile
precedent — passes cannot be merged).

GPU note: the per-combo day/band merge runs on the cudf.pandas proxy;
the run-length / episode machinery drops to host numpy via
host_array() once per combo (deterministic dtypes, no proxied-array
poisoning), mirroring the bands step's host-at-the-boundary contract.
The aggregated streak frame is host-side for the CSV COPY render.
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
from analyze.mov_ave_spread.config import (
    HIGH_LOW_PCT_GAP_TOLERANCE,
    HIGH_LOW_PCT_PERIODS,
    HIGH_LOW_PCT_STREAKS_COLUMNS,
    HIGH_LOW_PCT_STREAKS_DESCRIPTION,
    HIGH_LOW_PCT_STREAKS_NAME,
    HIGH_LOW_PCT_STREAKS_TABLE,
    HIGH_LOW_PCT_TABLE,
    HIGH_LOW_PCT_TYPES,
)

# Source columns needed per trading row (the parent source DataFrame
# carries them; 'price' is the adjusted close used for the breakout
# test and the streak's close measure).
_STREAK_SRC_COLS = (
    "sec_type", "code", "date", "open", "high", "low", "price",
    "trading_amount",
)

# Streak rows per CSV COPY chunk (bounds the in-memory chunk sliced off
# the long frame before rendering — same spirit as _BAND_CHUNK_ROWS in
# high_low_pct.py).
_STREAK_CHUNK_ROWS = 200_000


# ---------------------------------------------------------------------------
#  Fetch (bands for the audited scope — computed earlier in the same run)
# ---------------------------------------------------------------------------

async def fetch_bands_async(
    conn, sec_type: str, code: str | None = None,
) -> pd.DataFrame:
    """Load the analysis.mov_ave_high_low_pct band rows for ``sec_type``
    (optionally a single code) into a host DataFrame.

    The bands step runs BEFORE this step in the same pipeline run, so
    the table holds the current scope's bands (in --code mode the
    caller deleted + recomputed the code's bands first).
    """
    where = "WHERE sec_type = $1"
    args: list = [sec_type]
    if code is not None:
        where += " AND code = $2"
        args.append(code)
    rows = await conn.fetch(
        f"SELECT sec_type, code, date_year_month, period, pct_type, "
        f"high_val, low_val FROM {HIGH_LOW_PCT_TABLE} {where}",
        *args,
    )
    cols = rec_cols(rows)
    if not cols:
        return pd.DataFrame(
            columns=["sec_type", "code", "date_year_month", "period",
                     "pct_type", "high_val", "low_val"],
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

def compute_band_excursion_streaks(
    df: pd.DataFrame, bands: pd.DataFrame,
) -> pd.DataFrame:
    """Compute band-break excursion streaks for all (period, pct_type)
    combos.

    Args:
      df: daily frame sorted by (sec_type, code, date) with a reset
          (positional) index, carrying _STREAK_SRC_COLS. FULL per-code
          history of the sec_type(s). Non-trading vendor rows (ffilled
          weekday-holiday rows carried by the stats identity tables)
          are dropped inside — streaks are computed over REAL trading
          days only.
      bands: the analysis.mov_ave_high_low_pct rows for this sec_type
          (see fetch_bands_async).

    Returns:
      Long frame with HIGH_LOW_PCT_STREAKS_COLUMNS — one row per
      excursion streak; empty when no band/day joins or no breakouts.
    """
    out_cols = list(HIGH_LOW_PCT_STREAKS_COLUMNS)
    if df.empty or bands.empty:
        return pd.DataFrame(columns=out_cols)

    day = df[list(_STREAK_SRC_COLS)].copy()
    # ---- Trading-day filter -------------------------------------------
    # The stats identity tables carry ffilled OHLC rows for weekday CN
    # holidays (NULL trading_amount) — they would classify as in-band
    # "trading" days and split streaks across holidays (e.g. the 8-row
    # golden week between Sep 30 and Oct 9). Keep only real trading days
    # per the project CN calendar (one python loop over the frame's
    # UNIQUE dates — a few thousand — then a vectorized isin).
    _norm = day["date"].dt.normalize()
    _uniq = _norm.unique()
    _ok = pd.DatetimeIndex(
        [u for u in _uniq if is_trading_day(pd.Timestamp(u).date())]
    )
    day = day[_norm.isin(_ok)]
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

    gap = HIGH_LOW_PCT_GAP_TOLERANCE
    frames: list[pd.DataFrame] = []
    for period in HIGH_LOW_PCT_PERIODS:
        for pct in HIGH_LOW_PCT_TYPES:
            b = bnd[(bnd["period"] == period) & (bnd["pct_type"] == pct)]
            # Inner join on (sec_type, code, _ym): each banded day gets
            # its own month's band; unbanded days/codes drop out.
            m = day.merge(
                b[["sec_type", "code", "_ym", "high_val", "low_val"]],
                on=["sec_type", "code", "_ym"], how="inner",
            )
            if m.empty:
                continue
            m = m.sort_values(
                ["sec_type", "code", "date"]
            ).reset_index(drop=True)

            # ---- Host unwrap (one pass per combo) --------------------
            sec = host_array(m["sec_type"].to_numpy())
            code = host_array(m["code"].to_numpy())
            dates = host_array(m["date"].to_numpy())
            opens = host_array(m["open"].to_numpy(dtype="float64"))
            highs = host_array(m["high"].to_numpy(dtype="float64"))
            lows = host_array(m["low"].to_numpy(dtype="float64"))
            closes = host_array(m["price"].to_numpy(dtype="float64"))
            amts = host_array(m["trading_amount"].to_numpy(dtype="float64"))
            high_vals = host_array(m["high_val"].to_numpy(dtype="float64"))
            low_vals = host_array(m["low_val"].to_numpy(dtype="float64"))

            # ---- Side classification: +1 above band, -1 below, 0 in-band
            n = len(m)
            side = np.zeros(n, dtype=np.int8)
            side[closes > high_vals] = 1
            side[closes < low_vals] = -1
            out = side != 0

            # ---- Group-change mask ((sec_type, code) groups are
            # contiguous after the sort) and consecutive-in-band runs.
            gc = np.ones(n, dtype=bool)
            gc[1:] = (sec[1:] != sec[:-1]) | (code[1:] != code[:-1])
            grp = np.cumsum(gc)  # per-(sec_type, code) group id
            reset = out | gc
            # cumcount within reset buckets = consecutive in-band count
            # for in-band rows (each bucket starts at an out row or a
            # group start); out rows get 0. np.array forces a WRITABLE
            # copy — pandas ≥3 to_numpy() returns a read-only view
            # under CPU pandas (cudf.pandas materializes writable).
            consec = np.array(
                pd.Series(reset).groupby(reset.cumsum()).cumcount(),
                dtype=np.int64,
            )
            consec[out] = 0

            # ---- Side of the most recent out-of-band row (ffill),
            # masked to rows at/after the group's first out row so the
            # fill never leaks across codes.
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

            # ---- Episode continuity ---------------------------------
            # gap_before = in-band run length immediately before the row
            # (0 when the previous row was out); prev_side = side of the
            # previous row's open episode. Both masked at group starts
            # (a streak never continues across codes).
            gap_before = np.r_[gap + 1, consec[:-1].astype(np.int64)]
            gap_before[gc] = gap + 1  # group start: cannot continue
            prev_side = np.r_[0.0, last_side[:-1]]
            prev_side[gc] = 0.0
            cont_out = (
                out & (prev_side != 0) & (prev_side == side)
                & (gap_before <= gap)
            )
            new_ep = out & ~cont_out
            # Episode id: cumsum of break-events, scoped per code group.
            gcum = np.cumsum(new_ep)
            grp_start = (
                pd.Series(gcum.astype(np.float64))
                .where(pd.Series(gc)).ffill().fillna(0.0)
                .to_numpy(dtype=np.int64)
            )
            ep_id = gcum - grp_start  # 0 = before the group's first streak

            # ---- Episode rows: out rows + bridged in-band rows -------
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
                "open": opens, "close": closes, "high": highs,
                "low": lows, "amount": amts,
                "ep_id": ep_id, "pos": pos, "is_out": out,
            })[ep_rows]
            # ---- Drop trailing in-band rows: a streak ends at its last
            # OUT-OF-BAND row (trailing in-band days are a candidate next
            # bridge, not part of the streak).
            gkey = [sub["sec_type"], sub["code"], sub["ep_id"]]
            last_out_pos = (
                sub["pos"].where(sub["is_out"]).groupby(gkey)
                .transform("max")
            )
            sub = sub[sub["pos"] <= last_out_pos].copy()

            # ---- Per-streak measures (vectorized group ops) ----------
            g = sub.groupby(gkey, sort=False)
            first_pos = g["pos"].transform("min")
            last_pos = g["pos"].transform("max")
            # open of the first row / close of the last row via masked
            # group max (avoids agg 'first'/'last' dispatch).
            sub["_open_first"] = (
                sub["open"].where(sub["pos"] == first_pos)
                .groupby(gkey).transform("max")
            )
            sub["_close_last"] = (
                sub["close"].where(sub["pos"] == last_pos)
                .groupby(gkey).transform("max")
            )
            # Population std (ddof=0) of within-streak close diffs in
            # price units (single-day streaks -> 0.00).
            dd = g["close"].diff()
            dd_cnt = dd.groupby(gkey).transform("count")
            dd_var = (
                (dd - dd.groupby(gkey).transform("mean")) ** 2
            ).groupby(gkey).transform("sum") / dd_cnt
            sub["_std"] = np.sqrt(dd_var.fillna(0.0))  # constant/streak

            agg = sub.groupby(gkey, sort=False, as_index=False).agg(
                start_date=("date", "min"),
                end_date=("date", "max"),
                open=("_open_first", "max"),
                close=("_close_last", "max"),
                high=("high", "max"),
                low=("low", "min"),
                day_count=("pos", "count"),
                std_dev=("_std", "max"),
                daily_ave_trading_amt=("amount", "mean"),
            )
            agg.insert(2, "period", np.int64(period))
            agg.insert(3, "pct_type", np.int64(pct))
            frames.append(agg)

    if not frames:
        return pd.DataFrame(columns=out_cols)
    out = pd.concat(frames, ignore_index=True)
    # date_year_month = the streak's START month first day.
    out["date_year_month"] = (
        out["start_date"].to_numpy().astype("datetime64[M]")
        .astype("datetime64[ns]")
    )
    for c in ("open", "close", "high", "low", "std_dev",
              "daily_ave_trading_amt"):
        out[c] = out[c].round(2)
    # Sentinel for spans with NO trading-amount data at all (some
    # indices publish NULL amounts on scattered dates): 0.00 = "no
    # amount data in the span" (the DDL's daily_ave_trading_amt is NOT
    # NULL; mixed spans use the mean over the non-NULL rows).
    out["daily_ave_trading_amt"] = out["daily_ave_trading_amt"].fillna(0.0)
    out["day_count"] = out["day_count"].astype("int64")
    return out[out_cols]


async def _copy_streaks_chunked(conn, streaks: pd.DataFrame) -> int:
    """CSV-COPY the streaks frame in row-count chunks.

    Sequential on ``conn`` (streak rows are sparse excursion episodes —
    a small fraction of the daily universe; no pool-acquire
    bookkeeping needed). The ``pool`` / ``max_concurrent`` parameters
    are accepted for API compatibility with the sibling steps but
    unused. Conflict-free by construction: the sec_type scope was
    DELETEd upfront (wholesale rebuild).
    """
    n_total = len(streaks)
    columns = list(HIGH_LOW_PCT_STREAKS_COLUMNS)
    n_chunks = (n_total + _STREAK_CHUNK_ROWS - 1) // _STREAK_CHUNK_ROWS
    total = 0
    for i in range(n_chunks):
        lo = i * _STREAK_CHUNK_ROWS
        chunk = streaks.iloc[lo:lo + _STREAK_CHUNK_ROWS]
        n = await csv_copy_from_frame_async(
            conn, HIGH_LOW_PCT_STREAKS_TABLE, chunk, columns=columns,
        )
        total += n
        print(f"      streaks chunk {i + 1}/{n_chunks}: COPY {n:,} rows "
              f"(cumulative {total:,})", flush=True)
    return total


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__)
# ---------------------------------------------------------------------------

async def run_high_low_pct_streaks(
    conn,
    df: pd.DataFrame,
    *,
    force: bool = False,
    pool=None,
    max_concurrent: int = 20,
    sec_type: str | None = None,
    code_filter: str | None = None,
) -> None:
    """Run the band-break streak pipeline against the source data already
    loaded by the parent mov_ave_spread.

    WHOLESALE rebuild per sec_type (episodes shift with new data — see
    the module docstring): DELETE the sec_type's streak rows, fetch the
    bands computed by the high-low-percentile step earlier in the same
    run, compute the excursion streaks from the parent source
    DataFrame, CSV-COPY insert, registry upsert.

    Args:
      conn: asyncpg connection (reused from parent).
      df: source DataFrame with at least _STREAK_SRC_COLS — FULL
          per-code history of the sec_type(s).
      force: accepted for API symmetry (the wholesale DELETE below
              already covers the force semantics).
      pool: accepted for API compatibility (unused — sequential COPY).
      max_concurrent: accepted for API compatibility (unused).
      sec_type: when provided, process only this sec_type (parent loop
                passes one sec_type at a time to bound memory). When
                None, infers sec_types from the DataFrame.
      code_filter: single-code mode (--code): the caller deleted this
                   code's streak rows; only this code is processed.
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  MOV_AVE_HIGH_LOW_PCT_STREAKS (internal step of "
          "mov_ave_spread)", flush=True)
    print("=" * 78, flush=True)
    if code_filter is not None:
        print(f"    mode: SINGLE-CODE (full streak rebuild for "
              f"{code_filter})", flush=True)
    else:
        print("    mode: WHOLESALE per sec_type (episodes shift with "
              "new data; market_hypes precedent)", flush=True)

    if df.empty:
        print("    -> no source data; skipping streaks step.", flush=True)
        return

    if sec_type is not None:
        sec_types = (sec_type,)
    else:
        sec_types = tuple(sorted(df["sec_type"].unique()))

    n_total = 0
    for st in sec_types:
        st_df = df if sec_type is not None else df[df["sec_type"] == st]
        if st_df.empty:
            continue

        # ---- Wholesale scope wipe (episodes shift; PK coverage diffing
        # does not apply to this table).
        await conn.execute(
            f"DELETE FROM {HIGH_LOW_PCT_STREAKS_TABLE} WHERE sec_type = $1",
            st,
        )

        # ---- Bands for the audited scope (bands step ran earlier) ----
        bnd = await fetch_bands_async(conn, st, code=code_filter)
        if bnd.empty:
            print(f"    -> {st}: no bands in {HIGH_LOW_PCT_TABLE}; "
                  f"skipping streaks.", flush=True)
            continue

        # ---- Compute + insert ------------------------------------------
        streaks = compute_band_excursion_streaks(st_df, bnd)
        n_codes = (
            streaks[["sec_type", "code"]].drop_duplicates().shape[0]
            if not streaks.empty else 0
        )
        print(f"    -> {st}: {len(streaks):,} excursion streaks across "
              f"{n_codes:,} codes "
              f"(gap tolerance {HIGH_LOW_PCT_GAP_TOLERANCE} trading "
              f"days)", flush=True)
        if streaks.empty:
            continue
        n = await _copy_streaks_chunked(conn, streaks)
        n_total += n
        print(f"    -> {st}: inserted {n:,} streak rows", flush=True)

    # ---- Register in analysis_identity ------------------------------
    await upsert_analysis_identity(
        conn,
        name=HIGH_LOW_PCT_STREAKS_NAME,
        detail_name="mov_ave_high_low_pct_streaks",
        description=HIGH_LOW_PCT_STREAKS_DESCRIPTION,
    )

    print(f"\n  mov_ave_high_low_pct_streaks wall time: "
          f"{time.time() - t0:.1f}s", flush=True)
