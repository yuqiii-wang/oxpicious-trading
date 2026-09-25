"""Entry point for live.options_intraday_skewness.

Run via ``python -m live.options_intraday_skewness [--date YYYY-MM-DD]
[--underlying CODE[,CODE...]] [--force]``.

Computes the intraday OI-weighted moneyness skewness series
(live.options_intraday_skewness) — the 5-min sibling of
analysis.options_skewness_stats 'oi_moneyness' — for the option
underlyings found in stats.options_terms:

  * spot S(t): the underlying's own 5-min table
    (stats.etf_intraday_5min for ETF targets / stats.index_intraday_5min
    for INDEX targets, code suffix resolved at fetch time)
  * OI(t) per contract: prev-trading-day daily OI
    (stats.v_options_quote snapshot strictly before the target date —
    SSE rows carry 0, see config) PLUS the day-cumulative traded volume
    (stats.options_intraday_5min window sum). Contracts without intraday
    bars (SZSE / CFFEX today) keep the flat daily base.
  * per (bar time × expiry group): OI-weighted mean moneyness →
    skew_price / skew_pct / oi_total / OTM shares, plus a MEAN row per
    bar under the 9999-12-31 sentinel expiry (see config.py).

Modes:
  LIVE / INCREMENTAL (no --date): the latest spot date PER underlying;
  an underlying-day whose stored series already covers the spot bars'
  latest time is skipped (idempotent re-runs are cheap anyway). This is
  the mode the data_viz API keeps re-invoking on a stale live day. Also
  runs the RETENTION prune (newest RETENTION_DATES trading dates on BOTH
  this table and live.options_intraday_oi), guarded to once per new
  trading day via the PRUNE_IDENTITY_NAME live_identity row.

  ON-DEMAND (``--date D`` — the API's trading-signals-style compute for
  an older / missing date): computes the date unconditionally for every
  requested underlying (skips only underlyings with no spot bars that
  day → exit 404 when none qualify). NEVER prunes — a per-request path
  must not delete rows (the next daily prune reaps outside-window dates;
  a later request recomputes them).

  --force: truncate the series table first, then compute (rejected with
  --date — the request path must never truncate).

Exit codes: 0 ok, 404 requested date has no computable data.
"""
from __future__ import annotations

# Resource pre-check BEFORE the heavy imports (mirrors live.live_signals):
# the API-spawned on-demand runs would otherwise abort on the default
# 36 GiB RAM floor (this process peaks well under 1 GiB) and the GPU
# check is irrelevant — setdefault so an explicit env value still wins.
import os as _os

_os.environ.setdefault("ANALYZE_MIN_SYS_GB", "8")
from _common.pre_check import pre_check

pre_check(require_gpu=False)

import argparse  # noqa: E402
import asyncio  # noqa: E402
import datetime  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

# Project root on sys.path so ``_common`` imports resolve under
# ``python -m live.options_intraday_skewness``.
sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

# cudf.pandas activation — must run before pandas' first import anywhere
# in the module graph (fetch.py / compute.py import pandas).
from _common.df_utils._activate import activate  # noqa: E402

activate()

from _common.build_commons import (  # noqa: E402
    add_force_arg,
    get_db_connection_async,
    print_build_header,
    print_wall_time,
    setup_utf8_stdout,
    truncate_table_async,
)
from _common.log_setup import setup_logging  # noqa: E402

from live.options_intraday_skewness.compute import compute_skewness_frame  # noqa: E402
from live.options_intraday_skewness.config import (  # noqa: E402
    ADVISORY_LOCK_KEY,
    OI_TABLE,
    PIPELINE_DESCRIPTION,
    PIPELINE_NAME,
    PRICE_SCALE,
    PRUNE_IDENTITY_NAME,
    RETENTION_DATES,
    SKEW_PK,
    SKEW_TABLE,
)
from live.options_intraday_skewness import fetch  # noqa: E402

logger = setup_logging(PIPELINE_NAME)
setup_utf8_stdout()

EXIT_OK = 0
EXIT_NOT_FOUND = 404


async def _upsert_live_identity(conn) -> None:
    await conn.execute(
        """
        INSERT INTO live.live_identity
            (name, detail_name, summary_name, last_run_datetime, description)
        VALUES ($1, $2, $3, NOW(), $4)
        ON CONFLICT (name) DO UPDATE SET
            detail_name       = EXCLUDED.detail_name,
            summary_name      = EXCLUDED.summary_name,
            last_run_datetime = NOW(),
            description       = EXCLUDED.description
        """,
        PIPELINE_NAME,
        SKEW_TABLE.split(".", 1)[1],
        SKEW_TABLE.split(".", 1)[1],
        PIPELINE_DESCRIPTION,
    )


async def _prune_old_dates(conn) -> int:
    """Keep only the newest RETENTION_DATES trading dates on BOTH tables.

    The cutoff is the 20th-newest distinct intraday date across the two
    underlying spot tables (trading-date semantics — the same dates the
    rolling cache is meant to serve). On-demand --date runs never call
    this (a request path must not delete rows).
    """
    rows = await conn.fetch(
        """
        SELECT DISTINCT date FROM (
            SELECT date FROM stats.etf_intraday_5min
            UNION
            SELECT date FROM stats.index_intraday_5min
        ) d
        ORDER BY date DESC
        LIMIT $1
        """,
        RETENTION_DATES,
    )
    if len(rows) < RETENTION_DATES:
        # Less history exists than the window — nothing can be outside it.
        return 0
    cutoff = rows[-1]["date"]
    total = 0
    for table, key in ((SKEW_TABLE, "underlying_code"),
                       (OI_TABLE, "contract_code")):
        total += await chunked_purge_async(
            conn, table, where_sql="date < $1", params=(cutoff,),
            key_column=key,
        )
    logger.info(
        "    -> retention prune (keep newest %s dates, cutoff < %s): "
        "deleted %s rows",
        RETENTION_DATES, cutoff, total,
    )
    return total


async def _maybe_prune_for_new_day(conn, latest_date: datetime.date) -> None:
    """Retention prune at most once per NEW trading day (default mode).

    Guard: a plain PK probe on the PRUNE_IDENTITY_NAME live_identity row
    — its last_run_datetime::date compared against the latest date seen.
    """
    last_prune_date = await conn.fetchval(
        """
        SELECT last_run_datetime::date FROM live.live_identity
        WHERE name = $1
        """,
        PRUNE_IDENTITY_NAME,
    )
    if last_prune_date is not None and last_prune_date >= latest_date:
        return
    await _prune_old_dates(conn)
    await conn.execute(
        """
        INSERT INTO live.live_identity
            (name, detail_name, summary_name, last_run_datetime, description)
        VALUES ($1, NULL, NULL, NOW(), $2)
        ON CONFLICT (name) DO UPDATE SET last_run_datetime = NOW()
        """,
        PRUNE_IDENTITY_NAME,
        "Retention-prune bookkeeping row for "
        "live.options_intraday_skewness: last_run_datetime marks the "
        "trading date the rolling-window prune (RETENTION_DATES in "
        "live/options_intraday_skewness) last ran for — the default "
        "mode probes this row and prunes at most once per NEW trading "
        "day. Covers BOTH live.options_intraday_skewness and "
        "live.options_intraday_oi.",
    )


async def _compute_underlying_day(
    conn,
    underlying: str,
    target_type: str,
    trade_date: datetime.date,
    snapshot_cache: dict,
    incremental: bool,
) -> int:
    """Compute + upsert one (underlying, date) series. Returns the number
    of rows written; 0 when skipped (fresh series / no data)."""
    spot_code = await fetch.resolve_spot_code(conn, target_type, underlying, trade_date)
    if spot_code is None:
        logger.info(
            "    -> %s: no spot bars on %s (target_type=%s) — skipped",
            underlying, trade_date, target_type,
        )
        return 0

    # Incremental skip: series already covers the spot bars' latest time.
    if incremental:
        bars_max = await fetch.fetch_spot_max_time(conn, target_type, spot_code, trade_date)
        series_max = await fetch.fetch_series_max_time(conn, underlying, trade_date)
        if bars_max is not None and series_max is not None and series_max >= bars_max:
            logger.info(
                "    -> %s %s: series fresh (up to %s) — skipped",
                underlying, trade_date, series_max,
            )
            return 0

    cache_key = trade_date.isoformat()
    if cache_key not in snapshot_cache:
        snapshot_date, contracts = await fetch.fetch_contracts(conn, trade_date)
        cum_vols = await fetch.fetch_cum_volumes(conn, trade_date)
        snapshot_cache[cache_key] = (snapshot_date, contracts, cum_vols)
    snapshot_date, contracts, cum_vols = snapshot_cache[cache_key]
    if snapshot_date is None:
        logger.info(
            "    -> %s %s: no prior options snapshot exists — nothing to "
            "base OI on, skipped",
            underlying, trade_date,
        )
        return 0

    bars = await fetch.fetch_spot_bars(conn, target_type, spot_code, trade_date)
    if bars.empty:
        logger.info("    -> %s %s: no spot bars — skipped", underlying, trade_date)
        return 0

    # Venue-aware strike unit: ETF-target strikes are 厘 (1/1000 yuan);
    # INDEX-target (CFFEX) strikes are raw index points.
    strike_scale = 1.0 if target_type.strip().upper() == "INDEX" else PRICE_SCALE
    records = compute_skewness_frame(
        contracts, bars, cum_vols, underlying, trade_date, strike_scale
    )
    if not records:
        logger.info(
            "    -> %s %s: no valid (time, expiry) groups — nothing written",
            underlying, trade_date,
        )
        return 0
    from _common.db_commons import bulk_upsert_async

    n = await bulk_upsert_async(conn, SKEW_TABLE, records, list(SKEW_PK))
    logger.info(
        "    -> %s %s (snapshot %s): %s series rows upserted (%s bars)",
        underlying, trade_date, snapshot_date, n, len(bars),
    )
    return n


async def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Intraday OI-weighted moneyness skewness — per-5-min-bar "
            "skew_price/skew_pct per expiry group (+ mean row) against "
            "the underlying's 5-min spot, OI estimated as prev-day "
            "daily OI + day-cumulative volume."
        )
    )
    add_force_arg(ap)
    ap.add_argument(
        "--date",
        type=str,
        default="",
        help=(
            "ON-DEMAND as-of date YYYY-MM-DD: compute that date's series "
            "unconditionally (API-invoked for missing / older dates; "
            "exit 404 when no underlying has spot bars that day). "
            "Default: live mode — each underlying's latest spot date, "
            "incremental (fresh underlying-days skipped) + retention "
            "prune."
        ),
    )
    ap.add_argument(
        "--underlying",
        type=str,
        default="",
        help=(
            "Comma-separated underlying codes to limit scope to (e.g. "
            "'510050,159915'). Default: every underlying in the latest "
            "stats.options_terms snapshot."
        ),
    )
    args = ap.parse_args()
    if args.date and args.force:
        ap.error("--force is not supported with --date (the request path "
                 "must never truncate the table)")

    as_of: datetime.date | None = None
    if args.date:
        try:
            as_of = datetime.date.fromisoformat(args.date)
        except ValueError:
            ap.error(f"--date must be YYYY-MM-DD, got '{args.date}'")

    scope = [s.strip() for s in args.underlying.split(",") if s.strip()] or None

    t0 = time.time()
    conn = await get_db_connection_async()

    # Single-instance coordination: a concurrent run (CLI racing an
    # API spawn — the API's own tag dedupe usually prevents this) skips.
    lock_acquired = await conn.fetchval(
        "SELECT pg_try_advisory_lock($1)", ADVISORY_LOCK_KEY
    )
    if not lock_acquired:
        logger.info("advisory lock held by another run — exiting (its "
                    "idempotent upserts cover this run's scope).")
        print_wall_time(t0)
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass
        return EXIT_OK

    try:
        print_build_header(
            "LIVE OPTIONS INTRADAY SKEWNESS — "
            + (f"ON-DEMAND ({as_of})" if as_of else "LIVE / INCREMENTAL"),
            index_table=SKEW_TABLE,
            mode="ON-DEMAND --date" if as_of else "LIVE / INCREMENTAL",
        )

        universe = await fetch.fetch_option_underlyings(conn)
        if scope:
            wanted = set(scope)
            universe = [u for u in universe if u[0] in wanted]
            missing = wanted - {u[0] for u in universe}
            if missing:
                logger.warning("underlyings absent from the latest options "
                               "terms snapshot: %s", sorted(missing))
        if not universe:
            logger.info("no option underlyings in scope — nothing to do.")
            print_wall_time(t0)
            return EXIT_NOT_FOUND if as_of else EXIT_OK

        logger.info("[1/3] %s underlyings in scope: %s",
                    len(universe), [u[0] for u in universe])

        # ---- Step 0 (--force): truncate the series table ----------------
        if args.force:
            logger.info("[0/3] Force mode: truncating %s...", SKEW_TABLE)
            await truncate_table_async(conn, SKEW_TABLE)
            logger.info("    -> truncated; recompute-all follows")

        # ---- Step 2: compute + upsert per (underlying, date) ------------
        snapshot_cache: dict = {}
        n_rows = 0
        n_computed = 0
        dates_seen: list[datetime.date] = []
        for underlying, target_type in universe:
            if as_of is not None:
                trade_date = as_of
            else:
                spot_code = await fetch.resolve_spot_code(
                    conn, target_type, underlying, datetime.date.today()
                )
                if spot_code is None:
                    logger.info(
                        "    -> %s: no spot rows in the intraday table — "
                        "skipped",
                        underlying,
                    )
                    continue
                trade_date = await fetch.fetch_latest_spot_date(
                    conn, target_type, spot_code
                )
                if trade_date is None:
                    continue
            if trade_date not in dates_seen:
                dates_seen.append(trade_date)
            wrote = await _compute_underlying_day(
                conn,
                underlying,
                target_type,
                trade_date,
                snapshot_cache,
                incremental=as_of is None and not args.force,
            )
            if wrote:
                n_rows += wrote
                n_computed += 1
        logger.info("[2/3] computed %s underlying-day series, %s rows "
                    "upserted", n_computed, n_rows)

        # ---- Step 3: retention prune (default mode only) + identity -----
        if as_of is None:
            if dates_seen:
                await _maybe_prune_for_new_day(conn, max(dates_seen))
        else:
            logger.info("[3/3] on-demand run: prune skipped (a request "
                        "path must not delete rows)")
        await _upsert_live_identity(conn)
        print_wall_time(t0)

        # A --date request that computed nothing = nothing to show (the
        # API surfaces this as an empty series rather than an error, but
        # the exit code still flags the miss for ops).
        if as_of is not None and n_computed == 0:
            return EXIT_NOT_FOUND
        return EXIT_OK
    finally:
        try:
            await conn.execute("SELECT pg_advisory_unlock($1)", ADVISORY_LOCK_KEY)
        except Exception:
            pass  # connection close releases the lock anyway
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass


if __name__ == "__main__":
    from _common.post_check import post_check

    try:
        exit_code = asyncio.run(main())
    finally:
        post_check()
    sys.exit(exit_code)
