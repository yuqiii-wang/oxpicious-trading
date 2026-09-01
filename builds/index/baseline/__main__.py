"""builds.index.baseline — Build CSIndex daily history to DATABASE
(missing-data-only, no intermediate CSV).

Reads the history CSV archive produced by download_csindex.py:
  • {code}_history.csv        (daily OHLCV + PE + amount)
  • {code}_1m.csv             (recent 1-month export, bilingual headers)

Also reads SZSE index daily CSVs (399001 / 399006), SSE index
trend snapshots (~200 SSE indices), and CNINDEX history (399303 / 399310 /
399311). All sources are concatenated, deduplicated (priority: CNINDEX >
SSE trend > SZSE > 1m > history), then PE is backfilled from CSIndex rows
that lost the dedup. Missing trading days are filled with estimated close
prices using the best proxy index (> 60% composition shared weight).

Computes moving averages (ma5, ma20, ma60, ma120, ma255) from daily close.

NOTE: 5-minute intraday bars (stats.index_intraday_5min) are NO LONGER built
here. Intraday is now streamed in real time by stream_sse_price.py. The
former tick-file resample / 5min build / has_intraday_5mins sync helpers
have been removed; the flag is now synced by stream_sse_price after each
index bar lands.

Missing-data detection flow (DAILY, latest-missing-dates):
  1. Query stats.index_tech_stats for one MAX(date) per code (single GROUP
     BY — rows are inserted per table in one transaction, so max >= d
     implies the row at d exists).
  2. Peek every source file's last date (byte-level; snapshot filename
     dates for SZSE/SSE) → the source grid latest date. A code is read at
     all only when it has no DB rows, carries stale rebuild keys, or its
     latest DB date is behind the grid latest.
  3. Compute MAs over the loaded per-code history.
  4. Keep rows NEW vs the DB: date > the code's max date, or stale keys.
  5. Bulk upsert only the new rows into the 4 daily index_* tables.

With --force: truncate the 4 daily index_* tables first, so all source data
is treated as missing. (stats.index_intraday_5min is owned by
stream_sse_price.py and is NOT truncated here.)

With --date YYYY-MM-DD: single-date rebuild — every code's sources are
loaded (tail-read), only rows AT the forced date survive the new-vs-DB
filter, and rows already in the DB are refreshed through the normal upsert
path (no truncation, no deletes). Mutually exclusive with --force; the
refresh-estimated-days self-heal is skipped in this mode.

Inserts to database tables:
  • stats.index_identity      (date, code, name)
  • stats.index_basic_stats   (date, code, OHLCV, trading_shares,
                               trading_amount, change, change_pct,
                               is_close_estimated)
  • stats.index_valuation     (date, code, pe, cons_number)
  • stats.index_tech_stats    (date, code, MAs)

This package is split across functional submodules:
  • paths.py            — directory locations and validation regex
  • shared_weights.py   — fetch_index_shared_weights (proxy lookup for close estimation)
  • close_estimation.py — fill_missing_closes (gap-fill missing trading days)
  • loaders.py          — SZSE / SSE / CNINDEX CSV loaders → CSIndex schema
  • build_daily.py      — build_daily_df (concat + dedup + PE merge + MAs)
  • db_insert.py        — insert_daily_to_db (4-table upsert)

Usage:
  python -m builds.index.baseline
  python -m builds.index.baseline --force   (rebuild all daily tables)
  python -m builds.index.baseline --date 2026-08-14   (force one date)
"""

# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

import argparse
import asyncio
import sys
import time

from _common.build_commons import (
    setup_utf8_stdout, add_common_build_args, get_db_or_exit,
    print_build_header, print_wall_time,
    TODAY_STR,
    get_latest_dates_async, truncate_table_async,
    enforce_date_force_exclusion, parse_date_arg,
)

setup_utf8_stdout()

from builds._commons.code_filter import add_code_arg, normalize_code
from builds.index.baseline.paths import CSINDEX_DIR
from builds.index.baseline.shared_weights import fetch_index_shared_weights
from builds.index.baseline.build_daily import build_daily_df
from builds.index.baseline.db_insert import insert_daily_to_db


# ============================================================================
# Main pipeline
# ============================================================================
async def main():
    ap = argparse.ArgumentParser()
    add_common_build_args(ap)
    add_code_arg(ap)
    ap.add_argument(
        "--refresh-estimated-days",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Treat (date, code) rows newer than N days that are estimated "
            "(is_close_estimated = TRUE) or lack real OHLC (NULL/NaN open) "
            "as MISSING so they are rebuilt from the local CSVs (upsert is "
            "idempotent; 0 = off). Self-heals rows gap-filled by a build "
            "that ran before the EOD CSV publish landed. Used by the "
            "nightly builds.index run and the 'Build Yday Ref' chain."
        ),
    )
    args = ap.parse_args()

    # --date mode: mutual exclusion + parse (SystemExit 2 on bad input).
    enforce_date_force_exclusion(args)
    forced = parse_date_arg(args.date)
    if forced is not None:
        print(f"[DATE MODE] Forced single-date build: {forced}", flush=True)

    # Index codes are bare 6-digit codes (e.g. 000300) — strip the
    # exchange suffix normalize_code may have appended.
    code_filter = normalize_code(args.code)
    if code_filter:
        code_filter = code_filter.split(".")[0]

    t0 = time.time()
    print_build_header(
        "BUILD CSINDEX DAILY  ·  missing-data-only → DATABASE",
        **{
            "CSIndex dir": CSINDEX_DIR,
            "Code filter": code_filter or "(none — all indices)",
            "Forced date": str(forced) if forced else "(none)",
            "Today":       TODAY_STR,
        }
    )
    if code_filter:
        print(f"    [CODE FILTER] Restricting build to single index: {code_filter}", flush=True)

    # ------------------------------------------------------------------
    # 1. Connect to DB and query latest date per code
    # ------------------------------------------------------------------
    print("\n[1/3] Connecting to database and querying latest dates …", flush=True)
    conn = await get_db_or_exit()

    try:
        if args.force:
            if code_filter:
                # Single-code force mode: DELETE only this index's rows
                # (FK children first, identity last) instead of truncating.
                print(f"    [DB] Force mode for code {code_filter}: deleting existing rows for this code", flush=True)
                for tbl in ("stats.index_tech_stats",
                            "stats.index_valuation", "stats.index_basic_stats",
                            "stats.index_identity"):
                    await conn.execute(f"DELETE FROM {tbl} WHERE code = $1", code_filter)
            else:
                print("    [DB] Force mode: truncating existing daily tables", flush=True)
                # NOTE: stats.index_intraday_5min is owned by stream_sse_price.py
                # (real-time SSE streaming) and is intentionally NOT truncated here.
                for tbl in ("stats.index_tech_stats",
                            "stats.index_valuation", "stats.index_basic_stats",
                            "stats.index_identity"):
                    await truncate_table_async(conn, tbl)
            # Force mode: no DB rows → every code is fresh, everything loads.
            latest_dates: dict = {}
            stale_keys: set = set()
        elif code_filter:
            # Single-code mode: only check this index's latest date so dates
            # loaded for OTHER indices don't mask this code's gaps.
            row = await conn.fetchrow(
                "SELECT max(date) AS max_date FROM stats.index_tech_stats WHERE code = $1",
                code_filter,
            )
            latest_dates = {code_filter: row["max_date"].isoformat()} \
                if row and row["max_date"] else {}
            stale_keys: set = set()
            print(f"    [DB] code {code_filter} latest date in stats.index_tech_stats: "
                  f"{latest_dates.get(code_filter) or '(none)'}", flush=True)
        else:
            # Latest-missing-dates check: one MAX(date) per code from
            # stats.index_tech_stats (the LAST table in the insert sequence)
            # instead of loading every (date, code) pair. Rows are inserted
            # per table in a single transaction, so a code's max date >= d
            # implies the row at d exists — only dates AFTER the max can be
            # missing. If tech_stats lacks a row entirely, all four tables
            # are re-upserted (upsert is idempotent).
            latest_dates = {
                str(c): str(d)[:10]
                for c, d in (await get_latest_dates_async(
                    conn, "stats.index_tech_stats", ["code"])).items()
            }
            n_dates = len(latest_dates)
            print(f"    [DB] {n_dates:,} index codes in stats.index_tech_stats; "
                  f"latest {max(latest_dates.values()) if latest_dates else '(none)'}", flush=True)

            # Self-heal: recent estimated/NULL-OHLC rows are stale keys —
            # they are rebuilt from the local CSVs. Covers rows gap-filled
            # by a build that ran before the EOD CSV publish landed; rows
            # whose CSVs still lack the data are re-estimated identically.
            # --date mode skips the self-heal: single-date scope only (the
            # forced date is always rebuilt regardless of its row state).
            stale_keys: set = set()
            if args.refresh_estimated_days > 0 and forced is None:
                stale_rows = await conn.fetch(
                    """
                    SELECT date, code
                    FROM stats.index_basic_stats
                    WHERE date >= CURRENT_DATE - ($1::int)
                      AND (is_close_estimated = TRUE
                           OR open IS NULL
                           OR open::text = 'NaN')
                    """,
                    args.refresh_estimated_days,
                )
                stale_keys = {f"{str(r['date'])[:10]}|{str(r['code'])}"
                              for r in stale_rows}
                if stale_keys:
                    print(
                        f"    [DB] refresh-estimated({args.refresh_estimated_days}d): "
                        f"{len(stale_keys):,} estimated/NULL-open keys marked for rebuild",
                        flush=True,
                    )
                else:
                    print(
                        f"    [DB] refresh-estimated({args.refresh_estimated_days}d): "
                        f"no estimated/NULL-open rows in window",
                        flush=True,
                    )

        # ------------------------------------------------------------------
        # 2. Build daily frame (latest-missing-dates only)
        # ------------------------------------------------------------------
        print("\n[2/3] Building daily history frame (latest missing dates only) …", flush=True)

        # Fetch shared weights for close-price estimation of missing dates
        shared_weights = await fetch_index_shared_weights(conn)
        print(f"    [DB] {len(shared_weights):,} index shared-weight pairs loaded "
              f"for close estimation", flush=True)

        daily_df = await build_daily_df(conn, latest_dates,
                                        stale_keys=stale_keys,
                                        shared_weights=shared_weights,
                                        code_filter=code_filter,
                                        forced_date=forced.isoformat() if forced else None)

        # --date availability gate: no source CSV row exists at the forced
        # date (real or estimable) — same contract as forced_date_scope.
        if forced is not None and (daily_df is None or len(daily_df) == 0):
            print(f"[FATAL] --date {forced}: no data for this date in "
                  f"index daily source CSVs", file=sys.stderr, flush=True)
            raise SystemExit(1)

        # (--code filtering is pushed down into build_daily_df / loaders —
        # only this code's source files are ever read.)

        # ------------------------------------------------------------------
        # 3. Insert to database
        # ------------------------------------------------------------------
        print("\n[3/3] Inserting daily data to database …", flush=True)
        new_daily = await insert_daily_to_db(conn, daily_df)

        print(f"    → Total new daily rows inserted: {new_daily:,}", flush=True)

    finally:
        await conn.close()

    print_wall_time(t0)


if __name__ == "__main__":
    from _common.post_check import post_check
    try:
        asyncio.run(main())
    finally:
        post_check()
