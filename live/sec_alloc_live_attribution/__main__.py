"""Entry point for live.sec_alloc_live_attribution.

Run via ``python -m live.sec_alloc_live_attribution [--mode {all,live,ref,compute}]``.

The pipeline is split into THREE INDEPENDENT PROCESSES (plus a back-compat
combined mode), each with its own PG advisory lock so they never block
each other:

  LIVE  (--mode live — the 5-min auto-refresh path, fired by the Market
        Movements UI on every route via the App-root keeper):
        Self-contained EQUAL-WEIGHT ticks only. For every tick-eligible
        (benchmark, date) pair, appends fallback rows
        (is_without_trading_amt = TRUE — prev close = prev-day LAST 5-min
        bar close from stats.index_intraday_5min itself, NO basic_stats
        dependency). The loader's anti-join skips (code, time) rows that
        already exist with ANY flag, so pairs covered by daily-close-basis
        rows are natural no-ops. If the live lock is held by a concurrent
        instance, exits fast (next 5-min run catches up). Also runs the
        RETENTION PRUNE, guarded to once per NEW trading day via a
        live_identity bookkeeping row (a plain PK probe — the prune's
        unindexed date scan must not run on every 5-min tick).

  REF   (--mode ref — the manual yday-ref path, fired by the "Build Yday
        Ref" button on the Market Movements page):
        WEIGHTED tick pass: (re)fetch rows missing or present only as
        fallback (TRUE) — prev closes are computed AT TICK TIME from
        stats.index_basic_stats (the former heavy
        live.sec_alloc_live_prev_ref reference table was consolidated
        away; trading-amount weights / composition-overlap shared weights
        are computed at READ time by the API service) — and upserted as
        daily-close-basis (FALSE) rows, upgrading fallback rows in place.
        Waits (bounded) for its lock instead of skipping.

  COMPUTE (--mode compute --date YYYY-MM-DD — the on-demand backfill path,
        invoked by the API service when a requested (benchmark, date) has
        NO tick rows — a date selected on the UI that has fallen out of
        the RETENTION_DATES window, or one the 5-min keeper never
        reached): computes that date's rows from the base tables exactly
        like ref mode does for the latest date (weighted daily-close-basis
        pass + fallback fill for members without a basic_stats prev
        close) and upserts them into the tick table. Idempotent — the
        loaders' anti-joins make existing rows no-ops and upgrade
        fallback rows in place. Never prunes (a per-request path must not
        delete the date it was asked to build). Bounded by the RAW
        intraday table's retention, not by this table's window.

  ALL   (--mode all — combined back-compat behavior for CLI runs):
        weighted ticks + fallback fill for any rows still missing,
        exactly as before the split.

--force: truncate tick table first, then recompute (all/ref modes;
rejected in compute mode — the request path must never truncate).

--date YYYY-MM-DD: required by (and only valid with) --mode compute.

--benchmark CODE[,CODE,...]: limit scope to specific benchmark codes.
"""
from __future__ import annotations


# resource pre-check -- exit early when sys/GPU memory is insufficient.
# This pipeline is pure asyncpg DB I/O (no cudf/GPU work) — the GPU VRAM
# check is skipped so the 5-min UI-keeper run is never killed by heavy
# GPU-resident jobs holding the card (which silently starved the Market
# Movements page of live tick rows). The system-RAM floor is likewise
# lowered (default 36 GiB guards the heavy cudf pipelines; this process
# peaks at ~0.5 GiB RSS) — without this, the API-invoked on-demand
# compute spawns would abort whenever the WSL VM reports < 36 GiB.
# setdefault: an explicit ANALYZE_MIN_SYS_GB in the environment still wins.
import os as _os

_os.environ.setdefault("ANALYZE_MIN_SYS_GB", "8")

# Bootstrap-first layout (this pipeline is asyncpg-only; the hook keeps the
# repo-wide entry-point convention): LivePipeline.execute()'s own bootstrap
# is a no-op (flag-guarded) after this module-level one.
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
from _common.db_commons import chunked_purge_async  # noqa: E402

from live._base import LivePipeline  # noqa: E402
from live.sec_alloc_live_attribution.config import (  # noqa: E402
    COMPUTE_ADVISORY_LOCK_KEY,
    COMPUTE_LOCK_WAIT_S,
    PIPELINE_DESCRIPTION,
    PIPELINE_NAME,
    PRUNE_IDENTITY_NAME,
    REF_ADVISORY_LOCK_KEY,
    REF_LOCK_WAIT_S,
    RETENTION_DATES,
    ADVISORY_LOCK_KEY,
    TICK_TABLE,
)
from live.sec_alloc_live_attribution.fetch import AttributionStore  # noqa: E402
from live.sec_alloc_live_attribution.ticks import TickWriter  # noqa: E402

# The retention prune's bookkeeping-row description (the once-per-new-day
# guard's probe target; see LivePipeline.maybe_prune_for_new_day).
PRUNE_IDENTITY_DESCRIPTION = (
    "Retention-prune bookkeeping row for live.sec_alloc_live_attribution: "
    "last_run_datetime marks the trading date the rolling-window prune "
    "(RETENTION_DATES in live/sec_alloc_live_attribution) last ran for, "
    "so the 5-min LIVE mode probes this row (plain PK lookup) and prunes "
    "at most once per NEW trading day instead of scanning on every tick."
)


class SecAllocAttributionLive(LivePipeline):
    """Per-5-min-tick member attribution (live / ref / compute modes)."""

    # The header prints inside run() and DIFFERS PER MODE (live ticks /
    # yday ref / compute backfill); an empty title keeps the base
    # header/wall-time out of the way.
    title = ""
    component = PIPELINE_NAME

    # Retention prune bookkeeping (the shared once-per-new-day guard).
    prune_identity_name = PRUNE_IDENTITY_NAME
    prune_identity_description = PRUNE_IDENTITY_DESCRIPTION

    def add_arguments(self, parser) -> None:
        parser.description = (
            "Live sec-alloc attribution — per-5-min-tick member % vs prev "
            "day close (equal-weight live ticks + daily-close-basis "
            "weighted upgrades, under the live schema)."
        )
        add_force_arg(parser)
        parser.add_argument(
            "--mode",
            type=str,
            choices=("all", "live", "ref", "compute"),
            default="all",
            help=(
                "live = 5-min equal-weight fallback ticks only (self-contained, "
                "no daily-stats dependency); ref = weighted tick upgrades "
                "(prev closes from index_basic_stats at tick time; manual "
                "button); compute = on-demand backfill of --date (invoked by "
                "the API service for selected dates with no tick rows); "
                "all = combined (back-compat CLI)."
            ),
        )
        parser.add_argument(
            "--date",
            type=str,
            default="",
            help=(
                "Target date YYYY-MM-DD for --mode compute (the on-demand "
                "backfill). Required with compute, invalid with other modes."
            ),
        )
        parser.add_argument(
            "--rebuild-latest-date",
            action="store_true",
            help=(
                "Before the weighted pass, DELETE this date's existing tick "
                "rows so they are rebuilt from scratch. Set by the 'Build Yday "
                "Ref' chain (the chain refreshes CSVs + daily stats first, so "
                "any ticks computed from stale/estimated closes must be "
                "invalidated). Ref/all modes only."
            ),
        )
        parser.add_argument(
            "--benchmark",
            type=str,
            default="",
            help=(
                "Comma-separated benchmark codes to limit scope to. "
                "Without this flag, all indices with 5-min tick data "
                "(stats.index_intraday_5min) are considered."
            ),
        )

    def apply_args(self) -> None:
        args = self.args
        # --date is compute-only; --force must never ride the request path.
        if args.mode == "compute":
            if not args.date:
                self.parser.error("--mode compute requires --date YYYY-MM-DD")
            if args.force:
                self.parser.error("--force is not supported with --mode "
                                  "compute (the request path must never "
                                  "truncate the table)")
        elif args.date:
            self.parser.error("--date is only valid with --mode compute")

        self.target_compute_date: datetime.date | None = None
        if args.mode == "compute":
            try:
                self.target_compute_date = datetime.date.fromisoformat(args.date)
            except ValueError:
                self.parser.error(f"--date must be YYYY-MM-DD, got '{args.date}'")

        self.benchmarks: list[str] | None = [
            s.strip() for s in args.benchmark.split(",") if s.strip()
        ] or None

    # ------------------------------------------------------------------
    # Bounded advisory-lock acquisition
    # ------------------------------------------------------------------
    async def _acquire_lock_bounded(self, lock_key: int, wait_s: int,
                                    label: str) -> bool:
        """Bounded-blocking acquire of an advisory lock.

        Sets a per-statement timeout so pg_advisory_lock() gives up after
        wait_s instead of hanging forever, then restores no timeout.
        Returns False when another run still holds the lock.
        """
        await self.conn.execute(f"SET statement_timeout = '{wait_s * 1000}ms'")
        try:
            await self.conn.execute("SELECT pg_advisory_lock($1)", lock_key)
            return True
        except Exception as e:  # QueryCanceledError on timeout
            self.logger.info(f"    -> {label} lock not acquired within {wait_s}s "
                             f"({type(e).__name__}); aborting this run.")
            return False
        finally:
            await self.conn.execute("SET statement_timeout = '0'")

    # ------------------------------------------------------------------
    # Retention prune (the LivePipeline shared-shape, this pipeline's
    # cutoff discovery + purge targets)
    # ------------------------------------------------------------------
    async def prune_old_dates(self) -> int:
        """Delete tick rows older than the RETENTION_DATES newest trading dates.

        The cutoff is the OLDEST of the RETENTION_DATES most recent distinct
        intraday dates in the SOURCE table (the pipeline writes the latest
        date only, so the tick table always tracks those dates).

        Runs in ref/all modes and (guarded to once per new trading day by
        maybe_prune_for_new_day) in the 5-min live mode. Dates that fall out
        of the window are re-computable on demand via --mode compute.

        Touches the PRUNE_IDENTITY_NAME live_identity row afterwards so the
        live-mode guard knows the prune ran for this trading date.

        Returns tick_rows_deleted.
        """
        keep = await self._store.fetch_latest_intraday_dates(n_dates=RETENTION_DATES)
        if len(keep) < RETENTION_DATES:
            # Less history exists than the window — nothing can be outside it.
            return 0
        cutoff = keep[-1]
        # Retention prune chunked by the partition key (the purge can span
        # months of tick rows across the whole universe).
        n_tick = await chunked_purge_async(
            self.conn, TICK_TABLE, where_sql="date < $1", params=(cutoff,),
        )
        if n_tick:
            self.logger.info(f"    -> retention prune (keep newest {RETENTION_DATES} "
                             f"dates, cutoff < {cutoff}): deleted {n_tick:,} tick rows")
        else:
            self.logger.info(f"    -> retention prune: within the newest "
                             f"{RETENTION_DATES}-date window; nothing deleted")
        await self.upsert_prune_bookkeeping_row()
        return n_tick

    async def _upsert_identity(self, mode: str) -> None:
        """Register the pipeline in live.live_identity (with the mode tag)."""
        await self.upsert_live_identity(
            name=PIPELINE_NAME,
            detail_name=TICK_TABLE.split(".", 1)[1],
            summary_name=TICK_TABLE.split(".", 1)[1],
            description=f"{PIPELINE_DESCRIPTION} [last mode: {mode}]",
        )
        self.logger.info(f"    -> upserted live_identity (name='{PIPELINE_NAME}', "
                         f"mode={mode})")

    # ------------------------------------------------------------------
    # Main
    # ------------------------------------------------------------------
    async def run(self) -> None:
        args = self.args
        t0 = self.t0
        conn = self.conn

        # The conn-scoped stores (built here — the base amain() owns the
        # connection lifecycle; prune_old_dates reads self._store).
        self._store = AttributionStore(conn)
        writer = TickWriter(conn)

        # ======================== COMPUTE mode ===========================
        # On-demand backfill of a SELECTED date (API-invoked): weighted
        # daily-close-basis pass + fallback fill, exactly the ref/all passes
        # but scoped to an arbitrary date instead of the latest. Idempotent
        # (loader anti-joins); never prunes (a per-request path must not
        # delete the date it was asked to build — the daily prune will reap
        # it again on the next new trading day, and the next request for the
        # date simply recomputes).
        # =================================================================
        if args.mode == "compute":
            assert self.target_compute_date is not None  # validated above
            lock_acquired = await self._acquire_lock_bounded(
                COMPUTE_ADVISORY_LOCK_KEY, COMPUTE_LOCK_WAIT_S, "compute"
            )
            if not lock_acquired:
                print_wall_time(t0)
                return

            print_build_header(
                "LIVE SEC ALLOC ATTRIBUTION — COMPUTE "
                "(on-demand backfill of a selected date)",
                index_table=TICK_TABLE,
                mode=f"COMPUTE (date = {self.target_compute_date})",
            )
            pairs = await self._store.find_compute_tick_pairs(
                self.benchmarks, self.target_compute_date
            )
            self.logger.info(f"\n[1/3] Compute pass: {len(pairs)} tick-eligible "
                             f"(benchmark, date) pairs for "
                             f"{self.target_compute_date}...")
            if not pairs:
                self.logger.info("    -> no tick-eligible pairs (no raw intraday "
                                 "bars for that date — outside the raw table's "
                                 "retention, a non-trading day, or no curated "
                                 "benchmark traded); nothing written.")
            n_ticks = await writer.load_missing_ticks(pairs)
            self.logger.info(f"    -> weighted total: {n_ticks:,} rows")
            self.logger.info("\n[2/3] Fallback fill (members without a "
                             "basic_stats prev close)...")
            n_fb = await writer.load_fallback_ticks(pairs)
            self.logger.info(f"    -> fallback total: {n_fb:,} TRUE tick rows")
            self.logger.info("\n[3/3] Registering in live.live_identity...")
            await self._upsert_identity(args.mode)
            print_wall_time(t0)
            try:
                await conn.execute("SELECT pg_advisory_unlock($1)",
                                   COMPUTE_ADVISORY_LOCK_KEY)
            except Exception:
                pass  # connection close releases the lock anyway
            return

        # ---- Resolve the live date -------------------------------------
        target_dates = await self._store.fetch_latest_intraday_dates(n_dates=1)
        if not target_dates:
            self.logger.info("\n    -> no intraday dates in "
                             "stats.index_intraday_5min; nothing to do.")
            print_wall_time(t0)
            return
        latest_date = target_dates[0]

        # ======================== LIVE mode ==============================
        # Equal-weight fallback ticks only — the 5-min auto-refresh path.
        # =================================================================
        if args.mode == "live":
            lock_acquired = await conn.fetchval(
                "SELECT pg_try_advisory_lock($1)", ADVISORY_LOCK_KEY
            )
            print_build_header(
                "LIVE SEC ALLOC ATTRIBUTION — LIVE TICKS "
                "(per-5-min-tick % vs prev day close, equal-weight fallback)",
                index_table=TICK_TABLE,
                mode=(
                    "LIVE (equal-weight ticks, self-contained prev closes)"
                    if lock_acquired
                    else "SKIPPED (another live instance holds the advisory lock)"
                ),
            )
            self.logger.info(f"\n[1/3] Latest intraday date = {latest_date}")
            if not lock_acquired:
                self.logger.info("[2/3] Lock held — exiting fast; the next "
                                 "5-min run catches up.")
                print_wall_time(t0)
                return

            pairs = await self._store.find_live_tick_pairs(self.benchmarks)
            self.logger.info(f"[2/3] LIVE fallback tick pass: {len(pairs)} "
                             f"tick-eligible (benchmark, date) pairs...")
            if pairs:
                n_fb = await writer.load_fallback_ticks(pairs)
                self.logger.info(f"    -> fallback total: {n_fb:,} TRUE tick rows")
            else:
                self.logger.info("    -> no tick-eligible pairs; nothing to do.")
            # Retention prune, at most once per NEW trading day (the guard is
            # a plain PK probe on the prune bookkeeping identity row — see
            # LivePipeline.maybe_prune_for_new_day). Dates outside the window
            # stay re-computable on demand via --mode compute.
            await self.maybe_prune_for_new_day(latest_date)
            await self._upsert_identity("live")
            print_wall_time(t0)
            try:
                await conn.execute("SELECT pg_advisory_unlock($1)",
                                   ADVISORY_LOCK_KEY)
            except Exception:
                pass  # connection close releases the lock anyway
            return

        # ======================== REF / ALL modes ========================
        # Weighted (daily-close-basis) tick upgrades (ref), optionally
        # combined with the fallback fill (all).
        # =================================================================
        lock_acquired = await self._acquire_lock_bounded(
            REF_ADVISORY_LOCK_KEY, REF_LOCK_WAIT_S, "ref"
        )
        if not lock_acquired:
            print_wall_time(t0)
            return

        print_build_header(
            "LIVE SEC ALLOC ATTRIBUTION — YDAY REF "
            "(trading-amount weighted, daily-close basis)"
            if args.mode == "ref"
            else "LIVE SEC ALLOC ATTRIBUTION "
                 "(per-5-min-tick % vs prev day close, trading-amount weighted)",
            index_table=TICK_TABLE,
            mode=(
                "REF (weighted tick upgrades, tick-time prev closes)"
                if args.mode == "ref"
                else "INCREMENTAL (weighted ticks + fallback fill)"
            ),
        )

        # ---- Force mode → truncate the tick table -----------------------
        if args.force:
            self.logger.info("\n[0/3] Force mode: truncating tick table...")
            await truncate_table_async(conn, TICK_TABLE)
            self.logger.info("    -> truncated; will recompute all rows")

        # ---- Step 0: invalidate this date's ticks (--rebuild-latest-date)
        # The "Build Yday Ref" chain refreshes CSVs + daily stats BEFORE this
        # process runs; any existing ticks for the date may have been computed
        # from stale/estimated closes → delete so both passes rebuild.
        if args.rebuild_latest_date:
            n_tick_del = await writer.invalidate_ticks_for_date(latest_date)
            self.logger.info(f"\n[0/3] --rebuild-latest-date: deleted "
                             f"{n_tick_del:,} tick rows for {latest_date} — "
                             f"rebuilding")

        # ---- Step 1: WEIGHTED tick pass (incremental + upgrades) --------
        self.logger.info(f"\n[1/3] Weighted tick pass (latest intraday date = "
                         f"{latest_date}; pairs with missing or fallback-only "
                         f"ticks)...")
        tick_pairs = await self._store.find_pairs_with_missing_ticks(
            self.benchmarks
        )
        self.logger.info(f"    -> {len(tick_pairs)} (benchmark, date) pairs pending")
        if tick_pairs:
            n_ticks = await writer.load_missing_ticks(tick_pairs)
            self.logger.info(f"    -> weighted total: {n_ticks:,} rows")
        else:
            self.logger.info("    -> all weighted ticks up to date.")

        # ---- Step 2: FALLBACK tick fill (all mode only) -----------------
        # The weighted loader's anti-join covers missing (code, time) rows
        # per pair, but pairs whose prev-day basic_stats rows are missing
        # entirely (fetch_missing_ticks returns nothing) would keep no data —
        # the fallback fill gives them equal-weight rows. In the split design
        # the LIVE process owns fallback ticks; all mode just catches up.
        if args.mode == "all":
            self.logger.info("\n[2/3] Fallback tick fill (pairs still missing "
                             "rows)...")
            n_fb = await writer.load_fallback_ticks(tick_pairs)
            self.logger.info(f"    -> fallback total: {n_fb:,} TRUE tick rows")

        # ---- Step 3: retention prune + register in live_identity ---------
        # Skipped on --force (the truncate above already emptied the table)
        # and in live mode (the 5-min path stays scan-free; the ref/all runs
        # fire at least once per trading day via the Build Yday Ref chain).
        if not args.force:
            await self.prune_old_dates()

        self.logger.info("\n[3/3] Registering in live.live_identity...")
        await self._upsert_identity(args.mode)

        print_wall_time(t0)
        try:
            await conn.execute("SELECT pg_advisory_unlock($1)",
                               REF_ADVISORY_LOCK_KEY)
        except Exception:
            pass  # connection close releases the lock anyway


if __name__ == "__main__":
    job = SecAllocAttributionLive()
    job.execute()
    sys.exit(job.exit_code)
