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

# cudf.pandas activation — must run before pandas' first import anywhere
# in the module graph (fetch.py / compute.py import pandas).
# Bootstrap-first layout: the hook is installed before the module-level
# imports below (LivePipeline.execute()'s own bootstrap is a no-op —
# flag-guarded).
from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime(require_gpu=False)

import datetime  # noqa: E402
import sys  # noqa: E402

from _common.build_commons import (  # noqa: E402
    add_force_arg,
    print_build_header,
    print_wall_time,
    truncate_table_async,
)
from _common.db_commons import bulk_upsert_async, chunked_purge_async  # noqa: E402
from _common.sql_views import render_sql_view  # noqa: E402

from live._base import LivePipeline  # noqa: E402
from live.options_intraday_skewness.compute import SkewnessComputer  # noqa: E402
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
from live.options_intraday_skewness.fetch import SkewnessStore  # noqa: E402

EXIT_OK = 0
EXIT_NOT_FOUND = 404

# The retention prune's bookkeeping-row description (the once-per-new-day
# guard's probe target; see LivePipeline.maybe_prune_for_new_day).
PRUNE_IDENTITY_DESCRIPTION = (
    "Retention-prune bookkeeping row for "
    "live.options_intraday_skewness: last_run_datetime marks the "
    "trading date the rolling-window prune (RETENTION_DATES in "
    "live/options_intraday_skewness) last ran for — the default "
    "mode probes this row and prunes at most once per NEW trading "
    "day. Covers BOTH live.options_intraday_skewness and "
    "live.options_intraday_oi."
)


class OptionsSkewnessLive(LivePipeline):
    """The intraday OI-weighted moneyness skewness pipeline
    (live / incremental + on-demand --date)."""

    # The header prints inside run() (after the advisory-lock check, per
    # mode); an empty title keeps the base header/wall-time out of the way.
    title = ""
    component = PIPELINE_NAME

    # Retention prune bookkeeping (the shared once-per-new-day guard).
    prune_identity_name = PRUNE_IDENTITY_NAME
    prune_identity_description = PRUNE_IDENTITY_DESCRIPTION

    def add_arguments(self, parser) -> None:
        parser.description = (
            "Intraday OI-weighted moneyness skewness — per-5-min-bar "
            "skew_price/skew_pct per expiry group (+ mean row) against "
            "the underlying's 5-min spot, OI estimated as prev-day "
            "daily OI + day-cumulative volume."
        )
        add_force_arg(parser)
        parser.add_argument(
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
        parser.add_argument(
            "--underlying",
            type=str,
            default="",
            help=(
                "Comma-separated underlying codes to limit scope to (e.g. "
                "'510050,159915'). Default: every underlying in the latest "
                "stats.options_terms snapshot."
            ),
        )

    def apply_args(self) -> None:
        args = self.args
        if args.date and args.force:
            self.parser.error("--force is not supported with --date (the "
                              "request path must never truncate the table)")

        self.as_of: datetime.date | None = None
        if args.date:
            try:
                self.as_of = datetime.date.fromisoformat(args.date)
            except ValueError:
                self.parser.error(f"--date must be YYYY-MM-DD, got "
                                  f"'{args.date}'")

        self.scope: list[str] | None = (
            [s.strip() for s in args.underlying.split(",") if s.strip()]
            or None
        )

    # ------------------------------------------------------------------
    # Retention prune (the LivePipeline shared-shape, this pipeline's
    # cutoff discovery + purge targets)
    # ------------------------------------------------------------------
    async def prune_old_dates(self) -> int:
        """Keep only the newest RETENTION_DATES trading dates on BOTH tables.

        The cutoff is the 20th-newest distinct intraday date across the two
        underlying spot tables (trading-date semantics — the same dates the
        rolling cache is meant to serve). On-demand --date runs never call
        this (a request path must not delete rows).
        """
        # live/options_intraday_skewness/__main__/prune_cutoff_dates
        view = render_sql_view(
            "live/options_intraday_skewness/__main__/prune_cutoff_dates",
            keep_dates=RETENTION_DATES,
        )
        rows = await self.conn.fetch(view.sql, *view.params)
        if len(rows) < RETENTION_DATES:
            # Less history exists than the window — nothing can be outside it.
            return 0
        cutoff = rows[-1]["date"]
        total = 0
        for table, key in ((SKEW_TABLE, "underlying_code"),
                           (OI_TABLE, "contract_code")):
            total += await chunked_purge_async(
                self.conn, table, where_sql="date < $1", params=(cutoff,),
                key_column=key,
            )
        self.logger.info(
            "    -> retention prune (keep newest %s dates, cutoff < %s): "
            "deleted %s rows",
            RETENTION_DATES, cutoff, total,
        )
        await self.upsert_prune_bookkeeping_row()
        return total

    # ------------------------------------------------------------------
    # One (underlying, date) series
    # ------------------------------------------------------------------
    async def _compute_underlying_day(
        self,
        store: SkewnessStore,
        underlying: str,
        target_type: str,
        trade_date: datetime.date,
        snapshot_cache: dict,
        incremental: bool,
    ) -> int:
        """Compute + upsert one (underlying, date) series. Returns the number
        of rows written; 0 when skipped (fresh series / no data)."""
        conn = self.conn
        spot_code = await store.resolve_spot_code(target_type, underlying, trade_date)
        if spot_code is None:
            self.logger.info(
                "    -> %s: no spot bars on %s (target_type=%s) — skipped",
                underlying, trade_date, target_type,
            )
            return 0

        # Incremental skip: series already covers the spot bars' latest time.
        if incremental:
            bars_max = await store.fetch_spot_max_time(target_type, spot_code, trade_date)
            series_max = await store.fetch_series_max_time(underlying, trade_date)
            if bars_max is not None and series_max is not None and series_max >= bars_max:
                self.logger.info(
                    "    -> %s %s: series fresh (up to %s) — skipped",
                    underlying, trade_date, series_max,
                )
                return 0

        cache_key = trade_date.isoformat()
        if cache_key not in snapshot_cache:
            snapshot_date, contracts = await store.fetch_contracts(trade_date)
            cum_vols = await store.fetch_cum_volumes(trade_date)
            snapshot_cache[cache_key] = (snapshot_date, contracts, cum_vols)
        snapshot_date, contracts, cum_vols = snapshot_cache[cache_key]
        if snapshot_date is None:
            self.logger.info(
                "    -> %s %s: no prior options snapshot exists — nothing to "
                "base OI on, skipped",
                underlying, trade_date,
            )
            return 0

        bars = await store.fetch_spot_bars(target_type, spot_code, trade_date)
        if bars.empty:
            self.logger.info("    -> %s %s: no spot bars — skipped", underlying, trade_date)
            return 0

        # Venue-aware strike unit: ETF-target strikes are 厘 (1/1000 yuan);
        # INDEX-target (CFFEX) strikes are raw index points.
        strike_scale = 1.0 if target_type.strip().upper() == "INDEX" else PRICE_SCALE
        records = SkewnessComputer(strike_scale=strike_scale).run(
            contracts, bars, cum_vols, underlying, trade_date
        )
        if not records:
            self.logger.info(
                "    -> %s %s: no valid (time, expiry) groups — nothing written",
                underlying, trade_date,
            )
            return 0

        n = await bulk_upsert_async(
            conn, SKEW_TABLE,
            [r.model_dump() for r in records], list(SKEW_PK),
        )
        self.logger.info(
            "    -> %s %s (snapshot %s): %s series rows upserted (%s bars)",
            underlying, trade_date, snapshot_date, n, len(bars),
        )
        return n

    # ------------------------------------------------------------------
    # Main
    # ------------------------------------------------------------------
    async def run(self) -> None:
        args = self.args
        t0 = self.t0
        conn = self.conn
        store = SkewnessStore(conn)

        # Single-instance coordination: a concurrent run (CLI racing an
        # API spawn — the API's own tag dedupe usually prevents this) skips.
        lock_acquired = await conn.fetchval(
            "SELECT pg_try_advisory_lock($1)", ADVISORY_LOCK_KEY
        )
        if not lock_acquired:
            self.logger.info("advisory lock held by another run — exiting (its "
                             "idempotent upserts cover this run's scope).")
            print_wall_time(t0)
            return

        as_of = self.as_of
        try:
            print_build_header(
                "LIVE OPTIONS INTRADAY SKEWNESS — "
                + (f"ON-DEMAND ({as_of})" if as_of else "LIVE / INCREMENTAL"),
                index_table=SKEW_TABLE,
                mode="ON-DEMAND --date" if as_of else "LIVE / INCREMENTAL",
            )

            universe = await store.fetch_option_underlyings()
            if self.scope:
                wanted = set(self.scope)
                universe = [u for u in universe if u[0] in wanted]
                missing = wanted - {u[0] for u in universe}
                if missing:
                    self.logger.warning("underlyings absent from the latest "
                                        "options terms snapshot: %s",
                                        sorted(missing))
            if not universe:
                self.logger.info("no option underlyings in scope — nothing to do.")
                print_wall_time(t0)
                self.exit_code = EXIT_NOT_FOUND if as_of else EXIT_OK
                return

            self.logger.info("[1/3] %s underlyings in scope: %s",
                             len(universe), [u[0] for u in universe])

            # ---- Step 0 (--force): truncate the series table ----------------
            if args.force:
                self.logger.info("[0/3] Force mode: truncating %s...", SKEW_TABLE)
                await truncate_table_async(conn, SKEW_TABLE)
                self.logger.info("    -> truncated; recompute-all follows")

            # ---- Step 2: compute + upsert per (underlying, date) ------------
            snapshot_cache: dict = {}
            n_rows = 0
            n_computed = 0
            dates_seen: list[datetime.date] = []
            for underlying, target_type in universe:
                if as_of is not None:
                    trade_date = as_of
                else:
                    spot_code = await store.resolve_spot_code(
                        target_type, underlying, datetime.date.today()
                    )
                    if spot_code is None:
                        self.logger.info(
                            "    -> %s: no spot rows in the intraday table — "
                            "skipped",
                            underlying,
                        )
                        continue
                    trade_date = await store.fetch_latest_spot_date(
                        target_type, spot_code
                    )
                    if trade_date is None:
                        continue
                if trade_date not in dates_seen:
                    dates_seen.append(trade_date)
                wrote = await self._compute_underlying_day(
                    store,
                    underlying,
                    target_type,
                    trade_date,
                    snapshot_cache,
                    incremental=as_of is None and not args.force,
                )
                if wrote:
                    n_rows += wrote
                    n_computed += 1
            self.logger.info("[2/3] computed %s underlying-day series, %s rows "
                             "upserted", n_computed, n_rows)

            # ---- Step 3: retention prune (default mode only) + identity -----
            if as_of is None:
                if dates_seen:
                    await self.maybe_prune_for_new_day(max(dates_seen))
            else:
                self.logger.info("[3/3] on-demand run: prune skipped (a request "
                                 "path must not delete rows)")
            await self.upsert_live_identity(
                name=PIPELINE_NAME,
                detail_name=SKEW_TABLE.split(".", 1)[1],
                summary_name=SKEW_TABLE.split(".", 1)[1],
                description=PIPELINE_DESCRIPTION,
            )
            print_wall_time(t0)

            # A --date request that computed nothing = nothing to show (the
            # API surfaces this as an empty series rather than an error, but
            # the exit code still flags the miss for ops).
            self.exit_code = (
                EXIT_NOT_FOUND if (as_of is not None and n_computed == 0)
                else EXIT_OK
            )
        finally:
            try:
                await conn.execute("SELECT pg_advisory_unlock($1)",
                                   ADVISORY_LOCK_KEY)
            except Exception:
                pass  # connection close releases the lock anyway


if __name__ == "__main__":
    job = OptionsSkewnessLive()
    job.execute()
    sys.exit(job.exit_code)
