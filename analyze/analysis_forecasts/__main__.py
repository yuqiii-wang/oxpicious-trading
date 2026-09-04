"""Entry point for analyze.analysis_forecasts.

Run via ``python -m analyze.analysis_forecasts``.

Monthly per-security forecast analysis in the ``analysis_forecasts``
schema (see database/sql/analysis/analysis_forecasts/):

  - mov_rsi: per (sec_type, code, stat_month, rsi_window, side, pct,
    is_market_hyped) RSI extreme-percentile bucket definitions (RSI
    values join from analysis.mov_ave_rsi).

  - mov_std: per (sec_type, code, stat_month, ma_window, k, side,
    is_market_hyped) Bollinger-breach bucket definitions (motivation:
    breach magnitude mean_excess_close / mean_excess_max /
    max_excess_max; band inputs join from
    analysis.mov_ave_spreads_detail / stats.*_tech_stats).

  - mov_gap: per (sec_type, code, stat_month, gap_window, side, pct,
    is_market_hyped) N-day price-return extreme-percentile bucket
    definitions (gap_{W}days W ∈ {2, 3} joins from analysis.mov_ave_rsi).

  - base_rates: per (sec_type, code, stat_month, period) the
    UNCONDITIONAL same-window base rates (mean n-day forward change +
    P(change < -1%) / P(change > +1%) over ALL of the code's window
    trading days) — the reference the bucket results are read against
    (lift).

  - forecast_results: the result data (mean forward changes at
    next/5d/20d/60d horizons; close-based max/min forward changes and
    the best-to-worst n-day outcome ratio max_low_change_ratio at the
    5d/20d/60d horizons; per-horizon >1% reversal probabilities),
    keyed by forecast_id; every mov_rsi / mov_std / mov_gap row links
    1:1 to its result rows via forecast_id.

Pipeline per sec_type (index / etf / stock):
  1. Fetch active-universe codes (recent-data pre-filter).
  2. Incremental: stat_months missing from each target table are
     computed, and the most recent REFRESH_MONTHS completed months are
     REFRESHED each run (deleted + recomputed — a month written right
     after month-end carries permanently truncated 20d/60d occurrence
     counts because its forward windows were not complete yet).
     ``--force`` deletes the sec_type's mov_* rows AND their linked
     forecast_results rows (plus base_rates), then recomputes every
     target month.
  3. Fetch the joined long input frame (price / high / low / ma / rsi /
     gap / std columns; date >= earliest needed window start), the
     compact market-hype EPISODES list, and compute per-code forward
     changes (1/5/20/60 trading days).
  4. Scatter to (date × code) wide matrices + the market-hype flag
     matrix and run the vectorized monthly aggregation engines
     (compute_rsi / compute_std / compute_gap / compute_base), writing
     month-major batches: forecast_id allocated from the identity
     sequence, then COPY into forecast_results + the mov_* table in
     ONE transaction per month (no pre-clear DELETEs — months are only
     written when missing or after force/refresh deletion; atomicity
     keeps the 1:1 link crash-safe).
  5. Upsert analysis.analysis_identity.

Row emission contract: buckets with day_count = 0 emit NO row (a code
without valid RSI / MA+std in a window simply has no buckets that
month); base-rate rows are emitted only where base_count > 0.
"""
from __future__ import annotations


# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()
import argparse
import asyncio
import os
import sys
import time
from datetime import date, timedelta

# Ensure project root is on sys.path so ``_common`` is importable when run
# directly via ``python -m analyze.analysis_forecasts`` or as a script.
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from _common.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    print_build_header,
    print_wall_time,
    add_force_arg,
)
from _common.db_commons import (  # noqa: E402
    copy_insert_async,
)

setup_utf8_stdout()

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate  # noqa: E402
activate()

from analyze._common import upsert_analysis_identity  # noqa: E402
from analyze.analysis_forecasts.config import (  # noqa: E402
    TABLE_FORECAST,
    TABLE_MOV_RSI,
    TABLE_MOV_STD,
    TABLE_MOV_GAP,
    TABLE_BASE_RATE,
    ANALYSIS_NAME_RSI,
    ANALYSIS_NAME_STD,
    ANALYSIS_NAME_GAP,
    ANALYSIS_NAME_BASE_RATE,
    DESCRIPTION_RSI,
    DESCRIPTION_STD,
    DESCRIPTION_GAP,
    DESCRIPTION_BASE_RATE,
    SEC_TYPES,
    MOV_RSI_COLUMNS,
    MOV_STD_COLUMNS,
    MOV_GAP_COLUMNS,
    RSI_WINDOWS,
    MA_WINDOWS,
    GAP_WINDOWS,
    N_MONTHS,
    REFRESH_MONTHS,
    WINDOW_YEARS,
)
from analyze.analysis_forecasts.fetch import (  # noqa: E402
    fetch_active_codes,
    fetch_analysis_inputs,
    fetch_first_dates,
    fetch_hyped_episodes,
    add_forward_changes,
)
from analyze.analysis_forecasts.wide import (  # noqa: E402
    build_month_specs,
    build_grid,
    first_ords_from_dates,
    build_hype_matrix,
    scatter_column,
    build_change_matrices,
    month_row_windows,
    split_forecast_rows,
    MonthSpec,
    _shift_years,
)
from analyze.analysis_forecasts.compute_rsi import compute_rsi_results  # noqa: E402
from analyze.analysis_forecasts.compute_std import compute_std_results  # noqa: E402
from analyze.analysis_forecasts.compute_gap import compute_gap_results  # noqa: E402
from analyze.analysis_forecasts.compute_base import compute_base_rate_rows  # noqa: E402


# Max period rows per COPY chunk (a full stock-universe month of rsi
# buckets is <= 64 × ~5,400 ≈ 345K buckets → 1.38M period rows; chunk
# 100K period rows = 25K buckets per chunk).
_WRITE_CHUNK = 100_000

# Each forecast bucket expands to 4 period rows (next / 5d / 20d / 60d).
_PERIODS = 4

# Identity sequence backing forecast_results.forecast_id (GENERATED BY
# DEFAULT AS IDENTITY → <table>_<column>_seq). Allocated one per
# bucket (NOT per period row), shared across all 4 periods.
_FORECAST_ID_SEQ = "analysis_forecasts.forecast_results_forecast_id_seq"


# ---------------------------------------------------------------------------
#  Incremental month detection + refresh-window deletion
# ---------------------------------------------------------------------------

async def _compute_months(
    conn,
    table: str,
    sec_type: str,
    specs: list[MonthSpec],
) -> tuple[list[MonthSpec], list[date]]:
    """(compute, refreshed) — the spec months to (re)compute for
    ``table`` / sec_type.

    compute = stat_months MISSING from the table plus the PRESENT
    months inside the refresh window (the most recent REFRESH_MONTHS
    completed months). A month written right after month-end carries
    permanently truncated 20d/60d occurrence counts — its forward
    windows were not complete yet at write time — so present
    refresh-window months are deleted and recomputed on every run
    (the caller performs the deletion). Truly missing months need no
    delete.

    Returns the compute list (spec order) and the refreshed months'
    dates (empty when nothing present needs a refresh).
    """
    rows = await conn.fetch(
        f"SELECT DISTINCT stat_month FROM {table} WHERE sec_type = $1",
        sec_type,
    )
    present: set[date] = {r["stat_month"] for r in rows}
    refresh_set = {s.stat_month for s in specs[-REFRESH_MONTHS:]}
    refreshed = [
        s.stat_month for s in specs
        if s.stat_month in present and s.stat_month in refresh_set
    ]
    compute = [
        s for s in specs
        if s.stat_month not in present or s.stat_month in refresh_set
    ]
    return compute, refreshed


async def _delete_months(
    conn,
    table: str,
    sec_type: str,
    months: list[date],
    *,
    linked_results: bool,
) -> None:
    """Delete the given stat_months' rows of ``table`` (sec_type-scoped)
    — plus, for the mov_* tables, the forecast_results rows they link
    to (base_rates has no link)."""
    if not months:
        return
    if linked_results:
        await conn.execute(
            f"DELETE FROM {TABLE_FORECAST} f USING {table} m "
            f"WHERE m.forecast_id = f.forecast_id AND m.sec_type = $1 "
            f"AND m.stat_month = ANY($2::date[])",
            sec_type, months,
        )
    await conn.execute(
        f"DELETE FROM {table} WHERE sec_type = $1 "
        f"AND stat_month = ANY($2::date[])",
        sec_type, months,
    )


# ---------------------------------------------------------------------------
#  Force deletion (mov rows + their linked forecast_results rows)
# ---------------------------------------------------------------------------

async def _delete_sec_type(conn, sec_type: str) -> None:
    """Delete a sec_type's mov_rsi / mov_std / mov_gap rows, the
    forecast_results rows they link to, and its base_rates rows."""
    for table, linked in ((TABLE_MOV_RSI, True), (TABLE_MOV_STD, True),
                          (TABLE_MOV_GAP, True), (TABLE_BASE_RATE, False)):
        if linked:
            await conn.execute(
                f"DELETE FROM {TABLE_FORECAST} f USING {table} m "
                f"WHERE m.forecast_id = f.forecast_id AND m.sec_type = $1",
                sec_type,
            )
        await conn.execute(
            f"DELETE FROM {table} WHERE sec_type = $1", sec_type
        )


# ---------------------------------------------------------------------------
#  Month-batch writes (COPY both tables with allocated forecast_ids)
# ---------------------------------------------------------------------------

async def _write_month(
    conn,
    mov_table: str,
    mov_columns: list[str],
    rows: list[dict],
) -> int:
    """Write one month-batch of bucket rows to the mov table + its linked
    forecast_results rows.

    ``rows`` is (4·R,) period rows emitted by ``build_result_rows`` —
    bucket-major (4 consecutive rows per bucket). Allocates R forecast_ids
    (one per bucket) and shares each across the 4 period rows, then
    splits into R mov_rows + 4·R result_rows and COPYs both tables in
    ONE transaction per chunk. Pure COPY, NO pre-clear. Crash safety
    comes from transactional atomicity.

    Returns the number of BUCKETS written (R, not 4·R).
    """
    if not rows:
        return 0
    n_total_buckets = 0
    # Chunk must be a multiple of 4 (complete bucket groups) — trim.
    chunk_size = _WRITE_CHUNK - (_WRITE_CHUNK % _PERIODS)
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i : i + chunk_size]
        n_buckets = len(chunk) // _PERIODS
        async with conn.transaction():
            ids = [
                r[0] for r in await conn.fetch(
                    f"SELECT nextval('{_FORECAST_ID_SEQ}') "
                    f"FROM generate_series(1, $1::int)",
                    n_buckets,
                )
            ]
            # Assign each forecast_id to its 4 consecutive period rows.
            for bi, fid in enumerate(ids):
                for j in range(_PERIODS):
                    chunk[bi * _PERIODS + j]["forecast_id"] = fid
            mov_rows, result_rows = split_forecast_rows(chunk, mov_columns)
            await copy_insert_async(conn, TABLE_FORECAST, result_rows)
            await copy_insert_async(conn, mov_table, mov_rows)
        n_total_buckets += n_buckets
    return n_total_buckets


# ---------------------------------------------------------------------------
#  Per-sec_type pipeline
# ---------------------------------------------------------------------------

async def _process_sec_type(
    conn,
    sec_type: str,
    specs: list[MonthSpec],
    *,
    force: bool,
) -> tuple[int, int, int, int]:
    """Process one sec_type end-to-end.
    Returns (mov_rsi, mov_std, mov_gap) bucket rows + base_rates rows."""
    print(f"\n  [{sec_type}] Fetching active codes...", flush=True)
    codes = sorted(await fetch_active_codes(conn, sec_type))
    print(f"  [{sec_type}]   {len(codes):,} active codes", flush=True)
    if not codes:
        print(f"  [{sec_type}]   no active codes; skipping.", flush=True)
        return 0, 0, 0, 0

    # ---- Emittable-month bound -------------------------------------------
    # The full-window gate (below) means a snapshot month can emit rows
    # only once the universe's EARLIEST first-data date precedes the
    # month's window start (≈ first data + WINDOW_YEARS). Without this
    # bound the earlier months are "missing" FOREVER (they can never
    # emit a row), so every incremental run re-targets them and drags
    # the input fetch back years before any code needs it.
    first_dates = await fetch_first_dates(conn, sec_type, codes)
    if first_dates:
        f_min = min(first_dates.values())
        n_all = len(specs)
        specs = [
            s for s in specs
            if _shift_years(s.stat_month, -WINDOW_YEARS)
            + timedelta(days=1) > f_min
        ]
        print(f"  [{sec_type}]   universe first data {f_min.isoformat()} "
              f"→ {len(specs)} of {n_all} months can emit "
              f"(first data + {WINDOW_YEARS}y full-window gate)",
              flush=True)

    # ---- Determine target months (incremental / force) -------------------
    if force:
        print(f"  [{sec_type}] FORCE mode: deleting existing {sec_type} "
              f"mov rows + linked forecast_results + base_rates...",
              flush=True)
        await _delete_sec_type(conn, sec_type)
        compute_rsi = compute_std = compute_gap = compute_base = list(specs)
    else:
        compute_rsi, refresh_rsi = await _compute_months(
            conn, TABLE_MOV_RSI, sec_type, specs)
        compute_std, refresh_std = await _compute_months(
            conn, TABLE_MOV_STD, sec_type, specs)
        compute_gap, refresh_gap = await _compute_months(
            conn, TABLE_MOV_GAP, sec_type, specs)
        compute_base, refresh_base = await _compute_months(
            conn, TABLE_BASE_RATE, sec_type, specs)
        # Refresh-window months present in the DB: delete + recompute
        # (their long-horizon forward windows were not complete at
        # first write).
        await _delete_months(conn, TABLE_MOV_RSI, sec_type, refresh_rsi,
                             linked_results=True)
        await _delete_months(conn, TABLE_MOV_STD, sec_type, refresh_std,
                             linked_results=True)
        await _delete_months(conn, TABLE_MOV_GAP, sec_type, refresh_gap,
                             linked_results=True)
        await _delete_months(conn, TABLE_BASE_RATE, sec_type, refresh_base,
                             linked_results=False)
        print(f"  [{sec_type}]   months to compute: "
              f"rsi={len(compute_rsi)} std={len(compute_std)} "
              f"gap={len(compute_gap)} base={len(compute_base)} "
              f"of {len(specs)} (+ refresh of the last "
              f"{REFRESH_MONTHS})", flush=True)
    if not compute_rsi and not compute_std and not compute_gap \
            and not compute_base:
        print(f"  [{sec_type}]   up to date; skipping.", flush=True)
        return 0, 0, 0, 0

    # ---- Fetch inputs (bounded to the earliest needed window start) ------
    todo = {
        s.stat_month: s
        for s in compute_rsi + compute_std + compute_gap + compute_base
    }
    since = min(s.lower for s in todo.values())
    print(f"  [{sec_type}] Fetching joined inputs (price / high / low / "
          f"ma / rsi / gap / std) for {len(codes):,} codes since "
          f"{since.isoformat()}...", flush=True)
    df = await fetch_analysis_inputs(conn, sec_type, codes, since)
    print(f"  [{sec_type}]   {len(df):,} (code, date) rows", flush=True)
    if df.empty:
        print(f"  [{sec_type}]   no source data; skipping.", flush=True)
        return 0, 0, 0, 0
    episodes = await fetch_hyped_episodes(conn, sec_type, since)
    print(f"  [{sec_type}]   {len(episodes):,} market-hype episodes",
          flush=True)

    # ---- Wide grid + shared change matrices -------------------------------
    df = add_forward_changes(df)
    grid_ord, grid_codes, didx, cidx = build_grid(df)
    shape = (len(grid_ord), len(grid_codes))
    chg = build_change_matrices(df, shape, didx, cidx)
    hype = build_hype_matrix(episodes, grid_ord, grid_codes, shape)
    # Per-code first data date as ABSOLUTE epoch ordinals (min(date)
    # from the DB — fetched above for the emittable-month bound; the
    # fetched frame is bounded to the earliest window start and would
    # clip long-history codes). DATE-space gate: codes enter a month
    # only once their own history spans the FULL trailing 5-year window
    # (first listed 2020-01 → first snapshot 2025-01).
    first_ord = first_ords_from_dates(first_dates, grid_codes)
    print(f"  [{sec_type}]   {int(hype.sum()):,} hyped (date, code) "
          f"grid cells", flush=True)

    windows_by_month = {
        w.stat_month: w for w in month_row_windows(grid_ord, specs)
    }
    windows_rsi = [
        windows_by_month[m] for m in
        (s.stat_month for s in compute_rsi) if m in windows_by_month
    ]
    windows_std = [
        windows_by_month[m] for m in
        (s.stat_month for s in compute_std) if m in windows_by_month
    ]
    windows_gap = [
        windows_by_month[m] for m in
        (s.stat_month for s in compute_gap) if m in windows_by_month
    ]
    windows_base = [
        windows_by_month[m] for m in
        (s.stat_month for s in compute_base) if m in windows_by_month
    ]

    n_rsi = n_std = n_gap = 0
    n_base = 0

    # ---- Stage 1: RSI extreme buckets -------------------------------------
    if windows_rsi:
        print(f"  [{sec_type}] Computing RSI extreme buckets "
              f"(windows={list(RSI_WINDOWS)}) for {len(windows_rsi)} "
              f"months...", flush=True)
        rsi_mats = {
            f"rsi_{w}": scatter_column(df, f"rsi_{w}days", shape, didx, cidx)
            for w in RSI_WINDOWS
        }
        for stat_month, rows in compute_rsi_results(
            rsi_mats, chg, windows_rsi, grid_codes, sec_type, hype,
            first_ord,
        ):
            n = await _write_month(conn, TABLE_MOV_RSI, MOV_RSI_COLUMNS, rows)
            n_rsi += n
            print(f"    [{stat_month}] mov_rsi + forecast_results: "
                  f"wrote {n:,} rows", flush=True)
        del rsi_mats

    # ---- Stage 2: Bollinger-breach buckets --------------------------------
    if windows_std:
        print(f"  [{sec_type}] Computing Bollinger-breach buckets "
              f"(ma_windows={list(MA_WINDOWS)}) for {len(windows_std)} "
              f"months...", flush=True)
        std_mats: dict = {
            "price": scatter_column(df, "price", shape, didx, cidx),
            "high": scatter_column(df, "high", shape, didx, cidx),
            "low": scatter_column(df, "low", shape, didx, cidx),
        }
        for w in MA_WINDOWS:
            std_mats[f"ma_{w}"] = scatter_column(df, f"ma_{w}days", shape,
                                                 didx, cidx)
            std_mats[f"std_{w}"] = scatter_column(df, f"std_{w}days", shape,
                                                  didx, cidx)
        for stat_month, rows in compute_std_results(
            std_mats, chg, windows_std, grid_codes, sec_type, hype,
            first_ord,
        ):
            n = await _write_month(conn, TABLE_MOV_STD, MOV_STD_COLUMNS, rows)
            n_std += n
            print(f"    [{stat_month}] mov_std + forecast_results: "
                  f"wrote {n:,} rows", flush=True)
        del std_mats

    # ---- Stage 3: Gap extreme buckets --------------------------------------
    if windows_gap:
        print(f"  [{sec_type}] Computing gap extreme buckets "
              f"(windows={list(GAP_WINDOWS)}) for {len(windows_gap)} "
              f"months...", flush=True)
        gap_mats = {
            f"gap_{w}": scatter_column(df, f"gap_{w}days", shape, didx, cidx)
            for w in GAP_WINDOWS
        }
        for stat_month, rows in compute_gap_results(
            gap_mats, chg, windows_gap, grid_codes, sec_type, hype,
            first_ord,
        ):
            n = await _write_month(conn, TABLE_MOV_GAP, MOV_GAP_COLUMNS, rows)
            n_gap += n
            print(f"    [{stat_month}] mov_gap + forecast_results: "
                  f"wrote {n:,} rows", flush=True)
        del gap_mats

    # ---- Stage 4: unconditional base rates ---------------------------------
    if windows_base:
        print(f"  [{sec_type}] Computing base rates for "
              f"{len(windows_base)} months...", flush=True)
        for stat_month, rows in compute_base_rate_rows(
            chg, windows_base, grid_codes, sec_type, first_ord,
        ):
            await copy_insert_async(conn, TABLE_BASE_RATE, rows)
            n_base += len(rows)
        print(f"    base_rates: wrote {n_base:,} rows", flush=True)

    return n_rsi, n_std, n_gap, n_base


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Analysis Forecasts (ETF + Index + Stock). Monthly "
                    "per-security forecast analysis over a trailing "
                    "5-year window: analysis_forecasts.mov_rsi (RSI "
                    "extreme-percentile buckets), mov_std (Bollinger-"
                    "breach buckets) and mov_gap (gap-extreme buckets) "
                    "hold the motivation cols; forecast_results (linked "
                    "1:1 via forecast_id) holds the result data — mean "
                    "forward changes at next/5d/20d/60d horizons; "
                    "close-based max/min forward changes and the "
                    "best-to-worst n-day outcome ratio "
                    "(max_low_change_ratio) at the 5d/20d/60d horizons; "
                    "per-horizon >1% reversal probabilities; base_rates "
                    "holds the unconditional same-window reference "
                    "(mean change + P(<-1%) / P(>+1%) over all window "
                    "days)."
    )
    ap.add_argument(
        "--sec-type", choices=SEC_TYPES, default=None,
        help="Process only this sec_type (for testing). Default: all.",
    )
    ap.add_argument(
        "--months", type=int, default=N_MONTHS,
        help=f"Number of completed month-end snapshots to target "
             f"(default {N_MONTHS} = 5 years, monthly).",
    )
    add_force_arg(ap)
    args = ap.parse_args()
    force = args.force

    sec_types = (args.sec_type,) if args.sec_type else SEC_TYPES
    specs = build_month_specs(n_months=args.months)

    t0 = time.time()
    print_build_header(
        "ANALYZE FORECASTS (monthly RSI-extreme + Bollinger-breach + "
        "gap-extreme forecasts)",
        tables=f"{TABLE_FORECAST}, {TABLE_MOV_RSI}, {TABLE_MOV_STD}, "
               f"{TABLE_MOV_GAP}, {TABLE_BASE_RATE}",
        sec_types=", ".join(sec_types),
        months=f"{args.months} (window {specs[0].lower} .. "
               f"{specs[-1].stat_month})",
        mode="FORCE (delete + recompute all target months)" if force
        else f"incremental (missing stat_months + refresh of the last "
             f"{REFRESH_MONTHS})",
    )

    conn = await get_db_connection_async()
    try:
        total_rsi = total_std = total_gap = total_base = 0
        for st in sec_types:
            r, s, g, b = await _process_sec_type(conn, st, specs, force=force)
            total_rsi += r
            total_std += s
            total_gap += g
            total_base += b

            if r or force:
                await upsert_analysis_identity(
                    conn, name=ANALYSIS_NAME_RSI,
                    detail_name=ANALYSIS_NAME_RSI, description=DESCRIPTION_RSI,
                )
            if s or force:
                await upsert_analysis_identity(
                    conn, name=ANALYSIS_NAME_STD,
                    detail_name=ANALYSIS_NAME_STD, description=DESCRIPTION_STD,
                )
            if g or force:
                await upsert_analysis_identity(
                    conn, name=ANALYSIS_NAME_GAP,
                    detail_name=ANALYSIS_NAME_GAP, description=DESCRIPTION_GAP,
                )
            if b or force:
                await upsert_analysis_identity(
                    conn, name=ANALYSIS_NAME_BASE_RATE,
                    detail_name=ANALYSIS_NAME_BASE_RATE,
                    description=DESCRIPTION_BASE_RATE,
                )

        if total_rsi == 0 and total_std == 0 and total_gap == 0 \
                and total_base == 0 and not force:
            print("\n  DB is up to date; nothing to do.", flush=True)
            print_wall_time(t0)
            return

        print(f"\n  TOTAL: {total_rsi:,} mov_rsi + {total_std:,} mov_std "
              f"+ {total_gap:,} mov_gap rows written (with linked "
              f"forecast_results rows) + {total_base:,} base_rates rows",
              flush=True)
        print_wall_time(t0)
    finally:
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass


if __name__ == "__main__":
    from _common.post_check import post_check
    try:
        asyncio.run(main())
    finally:
        post_check()
