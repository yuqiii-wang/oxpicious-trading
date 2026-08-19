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

Missing-data detection flow (DAILY):
  1. Query stats.index_tech_stats for existing (date, code) pairs.
  2. Read all source CSVs (full per-code history needed for MA correctness).
  3. Compute MAs over the full per-code history.
  4. Filter rows to (date, code) pairs NOT in existing_keys.
  5. Bulk upsert only the missing rows into the 4 daily index_* tables.

With --force: truncate the 4 daily index_* tables first, so all source data
is treated as missing. (stats.index_intraday_5min is owned by
stream_sse_price.py and is NOT truncated here.)

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
"""
import argparse
import asyncio
import time

from _common.build_commons import (
    setup_utf8_stdout, add_common_build_args, get_db_or_exit,
    print_build_header, print_wall_time,
    TODAY_STR,
    get_existing_keys_async, truncate_table_async,
)

setup_utf8_stdout()

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

    t0 = time.time()
    print_build_header(
        "BUILD CSINDEX DAILY  ·  missing-data-only → DATABASE",
        **{
            "CSIndex dir": CSINDEX_DIR,
            "Today":       TODAY_STR,
        }
    )

    # ------------------------------------------------------------------
    # 1. Connect to DB and query existing keys
    # ------------------------------------------------------------------
    print("\n[1/3] Connecting to database and querying existing keys …", flush=True)
    conn = await get_db_or_exit()

    try:
        if args.force:
            print("    [DB] Force mode: truncating existing daily tables", flush=True)
            # NOTE: stats.index_intraday_5min is owned by stream_sse_price.py
            # (real-time SSE streaming) and is intentionally NOT truncated here.
            for tbl in ("stats.index_tech_stats",
                        "stats.index_valuation", "stats.index_basic_stats",
                        "stats.index_identity"):
                await truncate_table_async(conn, tbl)
            existing_daily_keys = set()
        else:
            # Use index_tech_stats (the LAST table in the insert sequence) for
            # the existing-keys check, NOT index_identity. Each table's upsert
            # runs in its own transaction (bulk_upsert_async), so a crash after
            # identity is committed but before tech_stats leaves orphaned rows
            # in identity. Checking tech_stats ensures that if ANY table is
            # missing a (date, code), the build will re-process it and upsert
            # all 4 tables (upsert is idempotent for tables that already have
            # the row).
            existing_daily_keys = await get_existing_keys_async(
                conn, "stats.index_tech_stats", ["date", "code"]
            )
            print(f"    [DB] {len(existing_daily_keys):,} existing (date, code) pairs in stats.index_tech_stats", flush=True)

            # Self-heal: drop recent estimated/NULL-OHLC keys so they are
            # rebuilt from the local CSVs. Covers rows gap-filled by a
            # build that ran before the EOD CSV publish landed; rows whose
            # CSVs still lack the data are re-estimated identically.
            if args.refresh_estimated_days > 0:
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
                stale_keys = {(r["date"], r["code"]) for r in stale_rows}
                if stale_keys:
                    dropped = len(existing_daily_keys & stale_keys)
                    existing_daily_keys -= stale_keys
                    print(
                        f"    [DB] refresh-estimated({args.refresh_estimated_days}d): "
                        f"{len(stale_keys):,} estimated/NULL-open keys found; "
                        f"{dropped:,} dropped from existing keys for rebuild",
                        flush=True,
                    )
                else:
                    print(
                        f"    [DB] refresh-estimated({args.refresh_estimated_days}d): "
                        f"no estimated/NULL-open rows in window",
                        flush=True,
                    )

        # ------------------------------------------------------------------
        # 2. Build daily frame (filtered to missing keys)
        # ------------------------------------------------------------------
        print("\n[2/3] Building daily history frame (missing keys only) …", flush=True)

        # Fetch shared weights for close-price estimation of missing dates
        shared_weights = await fetch_index_shared_weights(conn)
        print(f"    [DB] {len(shared_weights):,} index shared-weight pairs loaded "
              f"for close estimation", flush=True)

        daily_df = build_daily_df(existing_daily_keys, shared_weights=shared_weights)

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
    asyncio.run(main())
