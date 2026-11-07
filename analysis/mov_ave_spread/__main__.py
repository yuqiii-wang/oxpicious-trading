"""Entry point for analysis.mov_ave_spread.

Run via ``python -m analysis.mov_ave_spread``.

Pipeline
  1. Fetch per-(sec_type, code, date) price + MAs from stats schema for
     every sec_type in SEC_TYPES (active-universe pre-filter applied).
  2. Compute 9 wide gap columns + 6 derivative columns per row
     (price + ma5/20 x slope/curv — direct diffs of the series lines;
     the 60..255-day MA derivatives are not persisted — filtered to
     target_dates in incremental mode).
  3. Upsert detail.
  4. Upsert analysis_identity.
  5. INTERNAL STEP: compute Wilder RSI (3/6/10/14/20/60d)
     from the SAME source price data already loaded in Step 1 ->
     analysis.mov_ave_rsi (see steps/rsi.py). Reuses the same DB connection +
     source DataFrame. This step used to be a standalone
     analysis.mov_ave_rsi package; it is now an internal step because it
     shares the same source price data and active-universe pre-filter.
     Its post-compute hook WHOLESALE-rebuilds the per-code RSI history
     ranks -> analysis.mov_ave_rsi_ranks (see steps/rsi_ranks.py): every
     new RSI day can re-rank a code's whole history, so the ranks table
     is purged + bulk-populated per sec_type on every run that computes
     RSI (never on no-op runs).
  6. INTERNAL STEP: compute holiday / non-trading-day risk metrics
     (previous-day trading/weekend/holiday status + today's intraday
     gaps) from the SAME source data -> analysis.mov_ave_rsi_holiday
     (see steps/holiday.py). Must run AFTER RSI due to FK.
  7. INTERNAL STEP: compute EMA spread detail (the 6 TYPICAL EMA gap
     pairs over {6,12,26} + the EMA6/EMA12 slope + curvature) from the
     SAME source data
     -> analysis.mov_ave_spreads_detail_ema (see steps/ema.py). Reuses the
     same DB connection + source DataFrame.
  8. INTERNAL STEP: compute rolling OHLC detail (today_close +
     open/high/low/second-high/second-low over 7 windows:
     20/60/120/255/500/750/1275 trading days)
     from the SAME source data ->
     analysis.mov_ave_spreads_roof_and_floor_lines (renamed 2026-10 from
     mov_ave_spreads_detail_ohlc)
     (see steps/ohlc.py). Reuses the same DB connection + source DataFrame.
  8b. INTERNAL STEP: compute daily intraday OHLC gap metrics (gap_open /
      intra / uw / lw / rng / up_gap / dn_gap / close_pos — the
      高开低走/低开高走/冲高回落/探底回升 feature set), from the SAME
      source data with estimated rows (is_ohl_estimated) excluded ->
      analysis.mov_ave_spreads_detail_
      ohlc_gaps (see steps/ohlc_gaps.py). Reuses the same DB connection +
      source DataFrame.
  8c. INTERNAL STEP: compute the quantitative roof/floor LEVEL
      ESTIMATES (one row per (code, date, period, method): the
      estimated resistance/support level at the date under trendline /
      donchian / quantile / channel / boll / median — see the
      roof_floor_estimate step package) from the SAME source data ->
      analysis.mov_ave_spreads_roof_floor_estimates. Reuses the same
      DB connection + source DataFrame.
  8d. INTERNAL STEP: compute the roof/floor REBOUND AUDIT (one row per
      recorded extreme event: rebound frequency/amplitude + retest
      hold/break outcomes with bounce/break-depth amplitudes — see the
      roof_floor_rebound step package) from the SAME source data ->
      analysis.mov_ave_spreads_roof_floor_rebounds. WHOLESALE rebuild
      per sec_type (outcomes depend on post-event data). Reuses the
      same DB connection + source DataFrame.
  9. INTERNAL STEP: compute trading-amount liquidity-impact ratios
     (6 slope ratios + range ratio + overnight-gap ratio + MA5 versions)
     from the SAME source data -> analysis.mov_ave_trading_amt_ratios
     (see steps/trading_amt_ratios.py). Reuses the same DB connection +
     source DataFrame.
  9b. INTERNAL STEP: compute lookback-adaptive momentum (fixed 5/20d
      anchors + three volatility-adaptive short/mid/long lookbacks
      derived per (code, regime) from the stats.market_regimes vol
      evidence via equal-expected-move targeting) from the SAME source
      data -> analysis.mov_ave_momentum (see steps/momentum.py).
      Reuses the same DB connection + source DataFrame (plus one scoped
      market_regimes fetch). The per-(code, regime) sigma is full-
      history, so past lookbacks shift with new data — WHOLESALE
      rebuild per sec_type on every run that computes (rsi_ranks
      precedent).
  10. INTERNAL STEP: compute multi-period high/low price percentile
     BANDS (one row per security x calendar month x period x pct_type
     1/5/10: low_val = the pct_type-th percentile of daily lows,
     high_val = the (100-pct_type)-th percentile of daily highs over
     the trailing `period`-row window — 255/500/750/1275 = ~1/2/3/5
     trading years — ending at the month's last trading row) from the
     SAME source data (high/low columns) ->
     analysis.mov_ave_high_low_pct (see steps/high_low_pct.py). Trailing
     window: completed months are immutable, so only missing (code,
     month) pairs are computed and inserted.
  11. INTERNAL STEP: audit band-BREAK excursion STREAKS against the
     high-low-percentile bands (one row per excursion streak per
     security x period x pct_type: maximal consolidations of same-side
     days whose adjusted close falls above high_val / below low_val,
     tolerating in-band re-entries of up to 5 consecutive trading
     days; a longer gap or a side switch starts a new streak) ->
     analysis.mov_ave_high_low_pct_streaks (see
     high_low_pct_streaks.py). Episodes SHIFT with new data, so they
     are rebuilt WHOLESALE per sec_type (margin_changes precedent).

Every internal step is a :class:`TableStep
<analysis.mov_ave_spread._common.steps.TableStep>` subclass under
steps/ (or the roof_floor_* packages); the pipeline carries them in
``MovAveSpreadAnalysis.STEPS`` and drives them through the shared
template ``execute`` — the per-step compute/detect logic lives in the
step classes, not here.

Default (incremental) mode:
  Only dates present in source identity tables (stats.etf_identity +
  stats.index_identity + stats.stock_identity + the stats.industry_identity
  view) but NOT yet in analysis.mov_ave_spreads_detail are (re)computed
  and upserted.

  The missing-date check is PER-sec_type: a date populated for ETF does
  NOT mask the same date being missing for index/stock. This matters
  because the analysis table PK is (sec_type, code, date) — a global
  date check would falsely skip a date for sec_type B just because
  sec_type A already had it.

--force mode:
  Full-universe force (no --sec-type): TRUNCATE the detail tables,
  then recompute and insert all rows for the active universe.
  Scoped force (--sec-type X --force): DELETE only X's rows from the
  detail tables — other sec_types' data is NOT touched (they are not
  rebuilt by this run).

--sec-type mode:
  Process only the specified sec_type (for testing). Default: all.

--backfill mode:
  Lift the default increment bound. By default the nightly run builds
  ONLY the latest missing dates (the newest INCREMENTAL_RECENT_DATES
  identity dates); deep-history backlog dates are deferred and logged.
  --backfill targets EVERY missing date — the one-time convergence for
  a table newer than the history it covers (slow by design).
"""
from __future__ import annotations

import sys

# Runtime bootstrap — pre-check → silence warnings → cudf.pandas hook →
# UTF-8 stdout. MUST run before the pandas-importing modules below.
from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

import pandas as pd  # (post-activate; used by the detail incremental
#                     target-date filter)

from analysis._common import (
    build_and_insert_chunked_df,
)
from _common.pre_check_and_load import find_missing_analysis_dates
from _common.db_commons import chunked_purge_async
from _common.labels import SEC_TYPE_CODE
from analysis.mov_ave_spread.config import (
    ANALYSIS_NAME,
    DETAIL_TABLE,
    DESCRIPTION,
    EMA_DETAIL_TABLE,
    ESTIMATE_TABLE,
    HIGH_LOW_PCT_STREAKS_TABLE,
    HIGH_LOW_PCT_TABLE,
    HOLIDAY_TABLE,
    OHLC_GAPS_TABLE,
    OHLC_TABLE,
    PAIRS,
    PRICE_VS_AMT_TABLE,
    REBOUND_TABLE,
    SEC_TYPES,
    SEC_TYPE_IDENTITY_TABLE,
    TRADING_AMT_RATIOS_TABLE,
    TRADING_AMT_TABLE,
)
from analysis.mov_ave_spread.fetch import fetch_source_data
from analysis.mov_ave_spread._common.steps import (
    INCREMENTAL_RECENT_DATES,
    StepRun,
    TableStep,
)
from analysis.mov_ave_spread.steps import (
    RSI_RANKS_TABLE,
    RSI_TABLE,
    MOMENTUM_TABLE,
    build_detail_frame,
    refresh_codes_summary,
    EmaStep,
    HighLowPctStep,
    HighLowPctStreaksStep,
    HolidayStep,
    MomentumStep,
    OhlcGapsStep,
    OhlcStep,
    PriceVsAmtStep,
    RsiStep,
    RoofFloorEstimateStep,
    RoofFloorReboundStep,
    TradingAmtStep,
    TradingAmtRatiosStep,
)
from analysis.trends.rsi_episode_days import TREND_EPISODE_DAYS_TABLE

from _common.log_setup import setup_logging
from _common.data_analysis import DataAnalysis

logger = setup_logging("mov_ave_spread")


class MovAveSpreadAnalysis(DataAnalysis):
    title = "ANALYZE MA-SPREADS (ETF + INDEX + STOCK + INDUSTRY)"
    sec_types = tuple(SEC_TYPES)
    component = "mov_ave_spread"

    # The internal steps, in run order (one TableStep per target table).
    STEPS: tuple[TableStep, ...] = (
        RsiStep(),
        HolidayStep(),
        EmaStep(),
        OhlcStep(),
        OhlcGapsStep(),
        RoofFloorEstimateStep(),
        RoofFloorReboundStep(),
        TradingAmtStep(),
        TradingAmtRatiosStep(),
        MomentumStep(),
        PriceVsAmtStep(),
        HighLowPctStep(),
        HighLowPctStreaksStep(),
    )

    def add_arguments(self, parser) -> None:
        self.add_force_arg(parser)
        self.add_sec_type_arg(parser)
        self.add_max_concurrent_arg(parser)
        self.add_code_arg(parser)
        parser.add_argument(
            "--backfill", action="store_true",
            help="Converge ALL missing dates (deep-history backlog too). "
                 "Default: the nightly increment builds only the latest "
                 "missing dates (the newest "
                 f"{INCREMENTAL_RECENT_DATES} identity "
                 "dates) and defers backlog dates.",
        )

    def apply_args(self) -> None:
        if self.args.code and self.args.force:
            logger.error("ERROR: --code and --force are mutually exclusive.")
            sys.exit(2)
        self.pool_size = max(1, self.args.max_concurrent)

    def header_fields(self) -> dict:
        return {
            "detail_table": DETAIL_TABLE,
            "pairs": f"{len(PAIRS)} (5 Price/MA + 4 MA5/MA)",
            "sec_types": ", ".join(self.resolve_sec_types()),
            "mode": (
                f"SINGLE-CODE {self.args.code} (full recompute for this security)"
                if self.args.code else
                "FORCE (full recompute)" if self.args.force
                else "incremental (missing dates only)"
            ),
        }

    # ------------------------------------------------------------------
    # Internal-step plumbing
    # ------------------------------------------------------------------
    def _make_step_run(self, *, force: bool, code_filter: str | None) -> StepRun:
        """Bundle the per-call execution context the steps used to
        receive as a keyword soup (force/pool/max_concurrent/
        code_filter/backfill)."""
        return StepRun(
            conn=self.conn,
            pool=self.pool,
            force=force,
            max_concurrent=self.pool_size,
            code_filter=code_filter,
            backfill=bool(self.args.backfill),
        )

    async def _run_steps(
        self, df: pd.DataFrame, st: str, *,
        force: bool, code_filter: str | None,
    ) -> None:
        """Run every internal step on the fetched source DataFrame, in
        STEPS order (was: the 14-call run_* list, written twice)."""
        run = self._make_step_run(force=force, code_filter=code_filter)
        for step in self.STEPS:
            await step.execute(run, df, sec_type=st)

    async def _process_one_sec_type(
        self, st: str, target_dates_st, force: bool,
    ) -> int:
        """Process a single sec_type end-to-end.

        Fetches the FULL per-code history for ``st``, builds + inserts
        detail rows (filtered to ``target_dates_st`` in incremental
        mode), then runs the internal step classes on the same source
        data. The source DataFrame is freed before returning so the
        caller can process the next sec_type without cumulative memory
        growth.

        Splitting per-sec_type is essential for the stock universe: 11K+
        codes × 1.6K dates = ~17M rows (~6 GB in pandas). Loading all 3
        sec_types at once (18.5M rows) OOMs the host; processing one
        sec_type at a time bounds peak memory to a single sec_type's
        data.
        """
        # ---- Fetch FULL source data for this sec_type ----------------------
        logger.info(f"\n  [{st}] Fetching FULL per-(code, date) price + MAs "
              f"from stats schema...")
        df = await fetch_source_data(self.conn, st, target_dates=None)
        logger.info(f"  [{st}]   {len(df):,} (code, date) source rows")
        if df.empty:
            logger.info(f"  [{st}]   no source data; skipping.")
            return 0

        # ---- Build + insert detail (chunked by date) -----------------------
        if target_dates_st is not None and len(target_dates_st) == 0:
            # Empty set means force mode — compute ALL dates (no filtering).
            logger.info(f"\n  [{st}] Computing + inserting detail rows (FORCE mode, all dates)...")
            detail_df = df
            n_detail = await build_and_insert_chunked_df(
                self.conn, self.pool, detail_df,
                lambda sub: build_detail_frame(sub),
                table_name=DETAIL_TABLE,
                force=force,
                sec_types=(st,),
                chunk_target_rows=100_000,  # wide table — no melt, keep big chunks
                max_concurrent=self.pool_size,
                label=f"detail[{st}]",
            )
            logger.info(f"  [{st}]   inserted {n_detail:,} detail rows")
            del detail_df
        elif target_dates_st is None:
            logger.info(f"\n  [{st}] Detail up-to-date; skipping detail "
                  f"computation.")
            n_detail = 0
            # Still need to run internal steps even if detail is up-to-date
        else:
            logger.info(f"\n  [{st}] Computing + inserting detail rows in date-bounded "
                  f"chunks (9 gap cols + 6 deriv cols per row)...")
            detail_df = df
            if len(target_dates_st) > 0:
                n_before = len(detail_df)
                # datetime64 ndarray comparison — isin with a python-date SET
                # never matches a datetime64 column (fetch.py incremental-
                # filter convention).
                td64 = pd.to_datetime(sorted(target_dates_st)).values
                detail_df = detail_df[
                    detail_df["date"].isin(td64)
                ].reset_index(drop=True)
                logger.info(f"  [{st}]   incremental filter: {len(detail_df):,} of "
                      f"{n_before:,} rows are in target_dates")
            logger.info(f"  [{st}]   building {len(detail_df):,} detail rows "
                  f"(COPY per chunk)")

            n_detail = await build_and_insert_chunked_df(
                self.conn, self.pool, detail_df,
                lambda sub: build_detail_frame(sub),
                table_name=DETAIL_TABLE,
                force=force,
                sec_types=(st,),
                chunk_target_rows=100_000,  # wide table — no melt, keep big chunks
                max_concurrent=self.pool_size,
                label=f"detail[{st}]",
            )
            logger.info(f"  [{st}]   inserted {n_detail:,} detail rows")

            del detail_df

        # ---- Internal steps (reuses same source DataFrame) -----------------
        await self._run_steps(df, st, force=force, code_filter=None)

        # Free the source DataFrame — full history no longer needed.
        del df

        return n_detail

    async def _process_single_code(
        self, st: str, code: str,
    ) -> int:
        """Recompute ALL analysis rows for ONE (sec_type, code) — ``--code`` mode.

        Used by the UI per-security build button when a security has no
        analysis data. Steps:
          1. DELETE the code's existing rows from every target table (FK-safe
             order: holiday references mov_ave_rsi, so it goes first).
          2. Fetch the code's FULL source history (bypassing the
             active-universe pre-filter).
          3. Recompute + COPY-insert detail, then run ALL internal steps
             with ``code_filter`` so each step skips its per-sec_type
             missing-date detection (dates covered by OTHER codes would
             mask this code's gaps) and bypasses the per-sec_type
             skip-filter (``sec_types=()`` — rows are pre-deleted, so
             COPY cannot conflict).

        Returns the number of detail rows inserted (0 when the sec_type has
        no source data for the code).
        """
        logger.info(f"\n  [{st}] SINGLE-CODE mode: rebuilding all rows for {code}")

        # ---- Step 1: delete the code's rows (FK-safe order) ------------------
        # mov_ave_rsi_holiday has a FK to mov_ave_rsi → delete holiday first.
        # mov_ave_rsi_ranks carries no FK (derived-data design) — the RSI
        # step's post-compute hook also purges it per code; the DELETE here
        # only covers the skip case (RSI step finds no source data).
        for table in (
            HOLIDAY_TABLE, RSI_TABLE, RSI_RANKS_TABLE, DETAIL_TABLE,
            EMA_DETAIL_TABLE,
            OHLC_TABLE, OHLC_GAPS_TABLE, ESTIMATE_TABLE,
            REBOUND_TABLE, TRADING_AMT_TABLE,
            TRADING_AMT_RATIOS_TABLE,
            PRICE_VS_AMT_TABLE, HIGH_LOW_PCT_TABLE,
            HIGH_LOW_PCT_STREAKS_TABLE, MOMENTUM_TABLE,
            TREND_EPISODE_DAYS_TABLE,
        ):
            status = await self.conn.execute(
                f"DELETE FROM {table} WHERE sec_type = $1 AND code = $2",
                SEC_TYPE_CODE[st], code,
            )
            n_del = int(status.rsplit(" ", 1)[-1]) if status else 0
            logger.info(f"  [{st}]   deleted {n_del:,} rows from {table}")

        # ---- Step 2: fetch the code's FULL source history --------------------
        logger.info(f"  [{st}] Fetching FULL per-(code, date) price + MAs "
              f"for {code}...")
        df = await fetch_source_data(self.conn, st, target_dates=None,
                                     code_filter=code)
        logger.info(f"  [{st}]   {len(df):,} (code, date) source rows")
        if df.empty:
            logger.info(f"  [{st}]   no source data for {code} in {st}; skipping.")
            return 0

        # ---- Step 3: detail + all internal steps -----------------------------
        n_detail = await build_and_insert_chunked_df(
            self.conn, self.pool, df,
            lambda sub: build_detail_frame(sub),
            table_name=DETAIL_TABLE,
            force=False,
            sec_types=(),  # bypass per-sec_type skip-filter (rows pre-deleted)
            chunk_target_rows=100_000,  # wide table — no melt, keep big chunks
            max_concurrent=self.pool_size,
            label=f"detail[{st}:{code}]",
        )
        logger.info(f"  [{st}]   inserted {n_detail:,} detail rows")

        await self._run_steps(df, st, force=False, code_filter=code)

        del df
        return n_detail

    async def run(self) -> None:
        conn = self.conn
        args = self.args
        force = args.force
        sec_types = self.resolve_sec_types()
        # ---- Single-code mode (--code): rebuild ONE security -------------
        # Bypasses the per-sec_type missing-date detection entirely — the
        # UI fires this when a security has NO analysis rows while the
        # rest of the sec_type is up to date (date-level detection would
        # see nothing missing and skip it).
        if args.code:
            total_detail = 0
            for st in sec_types:
                total_detail += await self._process_single_code(st, args.code)
            # Per-code summary refresh (scoped to the rebuilt code).
            await refresh_codes_summary(conn, sec_types, code=args.code)
            logger.info(f"\n  -> Upserting analysis.analysis_identity registry...")
            await self.upsert_identity(
                ANALYSIS_NAME, "mov_ave_spreads_detail", DESCRIPTION,
            )
            logger.info(f"\n  TOTAL: {total_detail:,} detail rows inserted")
            return

        # ---- Step 0: determine target dates (per-sec_type) --------------
        if force:
            if set(sec_types) == set(SEC_TYPES):
                # Full-universe force: EVERY sec_type is rebuilt by this
                # run, so TRUNCATE (fast, resets storage) is safe.
                logger.info("\n[0/4] Force mode: truncating detail "
                      "tables...")
                await self.truncate_tables((
                    DETAIL_TABLE, TRADING_AMT_TABLE,
                    TRADING_AMT_RATIOS_TABLE, HOLIDAY_TABLE,
                    PRICE_VS_AMT_TABLE, HIGH_LOW_PCT_TABLE,
                    OHLC_GAPS_TABLE, MOMENTUM_TABLE, ESTIMATE_TABLE,
                ))
                logger.info("    -> truncated; will recompute all rows")
            else:
                # Scoped --sec-type force: DELETE only the scoped
                # sec_type's rows — a TRUNCATE here would wipe the OTHER
                # sec_types' data (they are NOT rebuilt by this run).
                logger.info(f"\n[0/4] Force mode: deleting "
                      f"{', '.join(sec_types)} rows from detail tables "
                      f"(other sec_types untouched)...")
                for table in (DETAIL_TABLE, TRADING_AMT_TABLE,
                              TRADING_AMT_RATIOS_TABLE, HOLIDAY_TABLE,
                              PRICE_VS_AMT_TABLE,
                              HIGH_LOW_PCT_TABLE,
                              HIGH_LOW_PCT_STREAKS_TABLE,
                              OHLC_GAPS_TABLE, MOMENTUM_TABLE,
                              ESTIMATE_TABLE, REBOUND_TABLE):
                    await chunked_purge_async(
                        conn, table,
                        where_sql="sec_type = ANY($1::smallint[])",
                        params=([SEC_TYPE_CODE[st] for st in sec_types],),
                    )
                logger.info("    -> deleted; will recompute scoped rows")
            # Use empty set (not None) so _process_one_sec_type knows to
            # compute ALL dates (no filtering) in force mode.
            target_dates_per_st: dict = {st: set() for st in sec_types}
            pending_per_st: dict = {
                st: {step: False for step in self.STEPS if step.in_skip_gate}
                for st in sec_types
            }
        else:
            logger.info("\n[0/4] Detecting missing dates PER-sec_type "
                  "(etf_identity vs detail[etf], index_identity vs "
                  "detail[index], stock_identity vs detail[stock])...")
            gate_steps = tuple(s for s in self.STEPS if s.in_skip_gate)
            target_dates_per_st: dict = {}
            pending_per_st: dict = {}
            for st in sec_types:
                src_tbl = SEC_TYPE_IDENTITY_TABLE[st]
                td_st = await find_missing_analysis_dates(
                    conn, DETAIL_TABLE, [src_tbl], sec_type=SEC_TYPE_CODE[st],
                )
                target_dates_per_st[st] = td_st
                logger.info(f"    -> {st}: detail {len(td_st)} missing dates")
                # Each gate step reports whether IT has work for this
                # sec_type (missing dates / repair dates / pair-keyed
                # bands / wholesale empty checks — per-step granularity
                # lives in the step classes' has_pending_work).
                pending_per_st[st] = {
                    step: await step.has_pending_work(conn, st, src_tbl)
                    for step in gate_steps
                }
                for step, pending in pending_per_st[st].items():
                    if pending:
                        logger.info(
                            f"    -> {st}: {step.detail_name} pending")
            total_missing = sum(
                len(s) for s in target_dates_per_st.values()
            )
            any_pending = any(
                any(p.values()) for p in pending_per_st.values()
            )
            if total_missing == 0 and not any_pending:
                logger.info("    -> DB is up to date; nothing to do.")
                return
            # For sec_types where only the internal steps (not the
            # detail table) need updates, set target_dates to None so
            # the detail step is skipped but the internal steps (which
            # have their own checks) still run.
            for st in sec_types:
                if not target_dates_per_st.get(st) and any(
                    pending_per_st[st].values()
                ):
                    target_dates_per_st[st] = None

        # ---- Steps 1-5: process each sec_type independently -------------
        # Each sec_type is processed end-to-end (fetch → compute → insert →
        # internal steps → free memory) before the next starts. This bounds
        # peak memory to a single sec_type's data — critical for the stock
        # universe (11K+ codes × 1.6K dates ≈ 17M rows / ~6 GB in pandas).
        # Loading all 3 sec_types at once (18.5M rows) OOMs the host.
        total_detail = 0
        for st in sec_types:
            td_st = target_dates_per_st.get(st)
            if td_st is not None and len(td_st) == 0 and not force:
                if not any(pending_per_st[st].values()):
                    logger.info(f"\n  [{st}] up to date; skipping.")
                    continue
            n_detail = await self._process_one_sec_type(st, td_st, force)
            # Refresh this sec_type's per-code summary (DELETE + INSERT —
            # vanished codes never linger as stale rows).
            await refresh_codes_summary(conn, [st])
            total_detail += n_detail

        # ---- Upsert analysis_identity ------------------------------------
        logger.info(f"\n  -> Upserting analysis.analysis_identity registry...")
        await self.upsert_identity(
            ANALYSIS_NAME, "mov_ave_spreads_detail", DESCRIPTION,
        )

        logger.info(f"\n  TOTAL: {total_detail:,} detail rows inserted")


if __name__ == "__main__":
    MovAveSpreadAnalysis().execute()
