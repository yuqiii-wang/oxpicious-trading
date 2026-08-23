"""One-off SQL backfill: populate high/low_line_slope_Wd in
analysis.mov_ave_spreads_detail_ohlc from the already-stored anchor
values + anchor dates.

Slope (price units per trading day) of the line through the two anchors:

    slope_Wd = (2nd anchor value - top anchor value)
               / (trading days between the two anchor dates)

The trading-day distance is the per-code row count between the two dates,
computed once into a temp positional table (row_number per
(sec_type, code) ordered by date) — exactly the position semantics of the
py pipeline's ohlc_vector.compute_group_anchors_all_windows, so SQL- and
py-written slopes agree.

Notes:
  - Idempotent: only rows whose 2nd anchor is populated while the slope is
    still NULL are updated, so re-runs are no-ops.
  - Rows whose anchor dates are NULL keep a NULL slope — they are caught
    by find_ohlc_repair_dates for py recompute.
  - The slope of the line through two points is direction-invariant, so
    rows written under the old (pre after-top) 2nd-anchor semantics still
    get the mathematically correct slope for the anchors they store; those
    rows are flagged for recompute by the before-top repair predicate.

Run via ``python -m analyze.mov_ave_spread._backfill_ohlc_slopes_sql``.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from _common.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
)

setup_utf8_stdout()

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate  # noqa: E402

activate()

from analyze.mov_ave_spread.config import OHLC_WINDOWS  # noqa: E402

TABLE: str = "analysis.mov_ave_spreads_detail_ohlc"

SEC_TYPES: tuple[str, ...] = ("etf", "index", "stock")


def parse_update_count(status: str | None) -> int:
    """asyncpg execute() returns e.g. 'UPDATE 864169' — extract the count."""
    if not status:
        return 0
    try:
        return int(status.rsplit(" ", 1)[-1])
    except ValueError:
        return 0


async def main() -> None:
    t0: float = time.time()
    conn = await get_db_connection_async()
    try:
        # ---- 1. Per-code positional temp table (trading-day index) --------
        print("[1/3] Building per-code positional temp table _ohlc_seq...",
              flush=True)
        await conn.execute("DROP TABLE IF EXISTS _ohlc_seq")
        await conn.execute(f"""
            CREATE TEMP TABLE _ohlc_seq AS
            SELECT sec_type, code, date,
                   row_number() OVER (PARTITION BY sec_type, code
                                      ORDER BY date) - 1 AS pos
            FROM {TABLE}
        """)
        await conn.execute(
            "CREATE UNIQUE INDEX ON _ohlc_seq (sec_type, code, date)"
        )
        n_seq: int = await conn.fetchval("SELECT count(*) FROM _ohlc_seq")
        print(f"    -> {n_seq:,} rows positioned", flush=True)

        # ---- 2. Backfill slopes: one UPDATE per (sec_type, window, side) --
        print("[2/3] Backfilling slopes "
              f"({len(SEC_TYPES)} sec_types x {len(OHLC_WINDOWS)} windows "
              "x 2 sides)...", flush=True)
        total: int = 0
        for st in SEC_TYPES:
            for w in OHLC_WINDOWS:
                for side in ("high", "low"):
                    status: str | None = await conn.execute(
                        f"""
                        UPDATE {TABLE} t
                        SET {side}_line_slope_{w}d =
                            (t.{side}_2nd_{w}d - t.{side}_{w}d)
                            / NULLIF(s2.pos - s1.pos, 0)
                        FROM _ohlc_seq s1, _ohlc_seq s2
                        WHERE t.sec_type = '{st}'
                          AND s1.sec_type = t.sec_type
                          AND s1.code = t.code
                          AND s1.date = t.{side}_date_{w}d
                          AND s2.sec_type = t.sec_type
                          AND s2.code = t.code
                          AND s2.date = t.{side}_2nd_date_{w}d
                          AND t.{side}_2nd_{w}d IS NOT NULL
                          AND t.{side}_line_slope_{w}d IS NULL
                        """
                    )
                    n: int = parse_update_count(status)
                    total += n
                    print(f"    -> {st:5s} {side}_line_slope_{w:<4d}d: "
                          f"{n:,} rows", flush=True)

        # ---- 3. Verify remaining gaps -------------------------------------
        print("[3/3] Verifying remaining NULL-slope gaps...", flush=True)
        for st in SEC_TYPES:
            row = await conn.fetchrow(
                f"SELECT count(*) AS total,"
                f" count(*) FILTER (WHERE high_2nd_20d IS NOT NULL"
                f"   AND high_line_slope_20d IS NULL) AS miss_h_20,"
                f" count(*) FILTER (WHERE low_2nd_20d IS NOT NULL"
                f"   AND low_line_slope_20d IS NULL) AS miss_l_20,"
                f" count(*) FILTER (WHERE high_2nd_1275d IS NOT NULL"
                f"   AND high_line_slope_1275d IS NULL) AS miss_h_1275"
                f" FROM {TABLE} WHERE sec_type = '{st}'"
            )
            print(f"    {st}: {dict(row)}", flush=True)
        print(f"Total slope cells updated: {total:,}", flush=True)
        print(f"Backfill wall time: {time.time() - t0:.1f}s", flush=True)
    finally:
        try:
            await conn.execute("DROP TABLE IF EXISTS _ohlc_seq")
        except Exception:
            pass
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass


if __name__ == "__main__":
    asyncio.run(main())
