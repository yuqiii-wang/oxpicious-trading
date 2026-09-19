"""Entry point for analyze.mov_ave_spread.

Run via ``python -m analyze.mov_ave_spread``.

Pipeline
  1. Fetch per-(sec_type, code, date) price + MAs from stats schema for
     every sec_type in SEC_TYPES (active-universe pre-filter applied).
  2. Compute 9 wide gap columns + 12 slope/curvature columns per row
     (filtered to target_dates in incremental mode).
  3. Upsert detail.
  4. Upsert analysis_identity.
  5. INTERNAL STEP: compute Wilder RSI (3/6/10/14/20/60d)
     from the SAME source price data already loaded in Step 1 ->
     analysis.mov_ave_rsi (see rsi.py). Reuses the same DB connection +
     source DataFrame. This step used to be a standalone
     analyze.mov_ave_rsi package; it is now an internal step because it
     shares the same source price data and active-universe pre-filter.
  6. INTERNAL STEP: compute holiday / non-trading-day risk metrics
     (previous-day trading/weekend/holiday status + today's intraday
     gaps) from the SAME source data -> analysis.mov_ave_rsi_holiday
     (see holiday.py). Must run AFTER RSI due to FK.
  7. INTERNAL STEP: compute EMA spread detail (9 EMA gap pairs + 5 EMA
     slope + 5 EMA curvature) from the SAME source data (EMA columns +
     pre-computed EMA slopes/curvatures already in the parent DataFrame)
     -> analysis.mov_ave_spreads_detail_ema (see ema.py). Reuses the
     same DB connection + source DataFrame.
  8. INTERNAL STEP: compute rolling OHLC detail (today_close +
     open/high/low/second-high/second-low over 7 windows:
     20/60/120/255/500/750/1275 trading days)
     from the SAME source data -> analysis.mov_ave_spreads_detail_ohlc
     (see ohlc.py). Reuses the same DB connection + source DataFrame.
  9. INTERNAL STEP: compute trading-amount liquidity-impact ratios
     (6 slope ratios + range ratio + overnight-gap ratio + MA5 versions)
     from the SAME source data -> analysis.mov_ave_trading_amt_ratios
     (see trading_amt_ratios.py). Reuses the same DB connection +
     source DataFrame.
  10. INTERNAL STEP: compute multi-period high/low price percentile
     BANDS (one row per security x calendar month x period x pct_type
     1/5/10: low_val = the pct_type-th percentile of daily lows,
     high_val = the (100-pct_type)-th percentile of daily highs over
     the trailing `period`-row window — 255/500/750/1275 = ~1/2/3/5
     trading years — ending at the month's last trading row) from the
     SAME source data (high/low columns) ->
     analysis.mov_ave_high_low_pct (see high_low_pct.py). Trailing
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

Default (incremental) mode:
  Only dates present in source identity tables (stats.etf_identity +
  stats.index_identity + stats.stock_identity) but NOT yet in
  analysis.mov_ave_spreads_detail are (re)computed and upserted.

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
"""
from __future__ import annotations

import sys
import time

# Runtime bootstrap — pre-check → silence warnings → cudf.pandas hook →
# UTF-8 stdout. MUST run before the pandas-importing modules below.
from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

import pandas as pd  # (post-activate; used by the detail incremental
#                     target-date filter)

from analyze._common import (
    build_and_insert_chunked_df,
)
from _common.pre_check_and_load import find_missing_analysis_dates
from analyze.mov_ave_spread.config import (
    ANALYSIS_NAME,
    DETAIL_TABLE,
    DESCRIPTION,
    EMA_DETAIL_TABLE,
    HIGH_LOW_PCT_STREAKS_TABLE,
    HIGH_LOW_PCT_TABLE,
    HOLIDAY_TABLE,
    OHLC_TABLE,
    PAIRS,
    PRICE_VS_AMT_TABLE,
    SEC_TYPES,
    SEC_TYPE_IDENTITY_TABLE,
    TRADING_AMT_ANALYSIS_NAME,
    TRADING_AMT_RATIOS_TABLE,
    TRADING_AMT_TABLE,
)
from analyze.mov_ave_spread.fetch import fetch_source_data
from analyze.mov_ave_spread.compute import build_detail_frame
from analyze.mov_ave_spread.rsi import run_rsi, RSI_TABLE
from analyze.mov_ave_spread.ema import run_ema
from analyze.mov_ave_spread.ohlc import run_ohlc, find_ohlc_repair_dates
from analyze.mov_ave_spread.trading_amt import run_trading_amt
from analyze.mov_ave_spread.trading_amt_ratios import run_trading_amt_ratios
from analyze.mov_ave_spread.price_vs_amt import run_price_vs_amt
from analyze.mov_ave_spread.high_low_pct import (
    find_missing_high_low_pct_pairs,
    run_high_low_pct,
)
from analyze.mov_ave_spread.high_low_pct_streaks import (
    run_high_low_pct_streaks,
)
from analyze.mov_ave_spread.holiday import run_holiday

from _common.log_setup import setup_logging
from _common.data_analysis import DataAnalysis

logger = setup_logging("mov_ave_spread")


async def _process_one_sec_type(
    conn, pool, st, target_dates_st, force, max_concurrent, t0,
):
    """Process a single sec_type end-to-end.

    Fetches the FULL per-code history for ``st``, builds + inserts
    detail rows (filtered to ``target_dates_st`` in incremental
    mode), then runs the RSI/holiday/EMA/OHLC/trading-amt/ratios
    steps on the same source data. The source DataFrame is freed
    before returning so the caller can process the next sec_type
    without cumulative memory growth.

    Splitting per-sec_type is essential for the stock universe: 11K+
    codes × 1.6K dates = ~17M rows (~6 GB in pandas). Loading all 3
    sec_types at once (18.5M rows) OOMs the host; processing one sec_type
    at a time bounds peak memory to a single sec_type's data.
    """
    # ---- Fetch FULL source data for this sec_type ----------------------
    logger.info(f"\n  [{st}] Fetching FULL per-(code, date) price + MAs "
          f"from stats schema...")
    df = await fetch_source_data(conn, st, target_dates=None)
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
            conn, pool, detail_df,
            lambda sub: build_detail_frame(sub),
            table_name=DETAIL_TABLE,
            force=force,
            sec_types=(st,),
            chunk_target_rows=100_000,  # wide table — no melt, keep big chunks
            max_concurrent=max_concurrent,
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
              f"chunks (9 gap cols + 12 slope/curv cols per row)...")
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
            conn, pool, detail_df,
            lambda sub: build_detail_frame(sub),
            table_name=DETAIL_TABLE,
            force=force,
            sec_types=(st,),
            chunk_target_rows=100_000,  # wide table — no melt, keep big chunks
            max_concurrent=max_concurrent,
            label=f"detail[{st}]",
        )
        logger.info(f"  [{st}]   inserted {n_detail:,} detail rows")

        del detail_df

    # ---- RSI step (reuses same source DataFrame) -----------------------
    await run_rsi(conn, df, force=force, pool=pool,
                  max_concurrent=max_concurrent, sec_type=st)

    # ---- Holiday step (reuses same source DataFrame) ------------------
    await run_holiday(conn, df, force=force, pool=pool,
                      max_concurrent=max_concurrent, sec_type=st)

    # ---- EMA detail step (reuses same source DataFrame) ---------------
    await run_ema(conn, df, force=force, pool=pool,
                  max_concurrent=max_concurrent, sec_type=st)

    # ---- OHLC detail step (reuses same source DataFrame) -------------
    await run_ohlc(conn, df, force=force, pool=pool,
                   max_concurrent=max_concurrent, sec_type=st)

    # ---- Trading-amount detail step (reuses same source DataFrame) ---
    await run_trading_amt(conn, df, force=force, pool=pool,
                          max_concurrent=max_concurrent, sec_type=st)

    # ---- Trading-amount ratios step (reuses same source DataFrame) --
    await run_trading_amt_ratios(conn, df, force=force, pool=pool,
                                 max_concurrent=max_concurrent, sec_type=st)

    # ---- Price-vs-amt state registry step (reuses same source
    #      DataFrame; the px_vol family's date-level source of truth) --
    await run_price_vs_amt(conn, df, force=force, pool=pool,
                           max_concurrent=max_concurrent, sec_type=st)

    # ---- High-low percentile band step (reuses same source DataFrame)
    await run_high_low_pct(conn, df, force=force, pool=pool,
                           max_concurrent=max_concurrent, sec_type=st)

    # ---- Band-break excursion streaks step (audits the bands table,
    # wholesale rebuild per sec_type) ---------------------------------
    await run_high_low_pct_streaks(conn, df, force=force, pool=pool,
                                   max_concurrent=max_concurrent,
                                   sec_type=st)

    # Free the source DataFrame — full history no longer needed.
    del df

    return n_detail


async def _process_single_code(
    conn, pool, st, code, max_concurrent,
) -> int:
    """Recompute ALL analysis rows for ONE (sec_type, code) — ``--code`` mode.

    Used by the UI per-security build button when a security has no
    analysis data. Steps:
      1. DELETE the code's existing rows from every target table (FK-safe
         order: holiday references mov_ave_rsi, so it goes first).
      2. Fetch the code's FULL source history (bypassing the
         active-universe pre-filter).
      3. Recompute + COPY-insert detail, then run ALL internal steps
         (RSI / holiday / EMA / OHLC / trading-amt / ratios) with
         ``code_filter`` so each step skips its per-sec_type missing-date
         detection (dates covered by OTHER codes would mask this code's
         gaps) and bypasses the per-sec_type skip-filter
         (``sec_types=()`` — rows are pre-deleted, so COPY cannot
         conflict).

    Returns the number of detail rows inserted (0 when the sec_type has
    no source data for the code).
    """
    logger.info(f"\n  [{st}] SINGLE-CODE mode: rebuilding all rows for {code}")

    # ---- Step 1: delete the code's rows (FK-safe order) ------------------
    # mov_ave_rsi_holiday has a FK to mov_ave_rsi → delete holiday first.
    for table in (
        HOLIDAY_TABLE, RSI_TABLE, DETAIL_TABLE, EMA_DETAIL_TABLE,
        OHLC_TABLE, TRADING_AMT_TABLE, TRADING_AMT_RATIOS_TABLE,
        PRICE_VS_AMT_TABLE, HIGH_LOW_PCT_TABLE,
        HIGH_LOW_PCT_STREAKS_TABLE,
    ):
        status = await conn.execute(
            f"DELETE FROM {table} WHERE sec_type = $1 AND code = $2",
            st, code,
        )
        n_del = int(status.rsplit(" ", 1)[-1]) if status else 0
        logger.info(f"  [{st}]   deleted {n_del:,} rows from {table}")

    # ---- Step 2: fetch the code's FULL source history --------------------
    logger.info(f"  [{st}] Fetching FULL per-(code, date) price + MAs "
          f"for {code}...")
    df = await fetch_source_data(conn, st, target_dates=None,
                                 code_filter=code)
    logger.info(f"  [{st}]   {len(df):,} (code, date) source rows")
    if df.empty:
        logger.info(f"  [{st}]   no source data for {code} in {st}; skipping.")
        return 0

    # ---- Step 3: detail + all internal steps -----------------------------
    n_detail = await build_and_insert_chunked_df(
        conn, pool, df,
        lambda sub: build_detail_frame(sub),
        table_name=DETAIL_TABLE,
        force=False,
        sec_types=(),  # bypass per-sec_type skip-filter (rows pre-deleted)
        chunk_target_rows=100_000,  # wide table — no melt, keep big chunks
        max_concurrent=max_concurrent,
        label=f"detail[{st}:{code}]",
    )
    logger.info(f"  [{st}]   inserted {n_detail:,} detail rows")

    await run_rsi(conn, df, force=False, pool=pool,
                  max_concurrent=max_concurrent, sec_type=st,
                  code_filter=code)
    await run_holiday(conn, df, force=False, pool=pool,
                      max_concurrent=max_concurrent, sec_type=st,
                      code_filter=code)
    await run_ema(conn, df, force=False, pool=pool,
                  max_concurrent=max_concurrent, sec_type=st,
                  code_filter=code)
    await run_ohlc(conn, df, force=False, pool=pool,
                   max_concurrent=max_concurrent, sec_type=st,
                   code_filter=code)
    await run_trading_amt(conn, df, force=False, pool=pool,
                          max_concurrent=max_concurrent, sec_type=st,
                          code_filter=code)
    await run_trading_amt_ratios(conn, df, force=False, pool=pool,
                                 max_concurrent=max_concurrent, sec_type=st,
                                 code_filter=code)
    await run_price_vs_amt(conn, df, force=False, pool=pool,
                           max_concurrent=max_concurrent, sec_type=st,
                           code_filter=code)
    await run_high_low_pct(conn, df, force=False, pool=pool,
                           max_concurrent=max_concurrent, sec_type=st,
                           code_filter=code)
    await run_high_low_pct_streaks(conn, df, force=False, pool=pool,
                                   max_concurrent=max_concurrent,
                                   sec_type=st, code_filter=code)

    del df
    return n_detail


class MovAveSpreadAnalysis(DataAnalysis):
    title = "ANALYZE MA-SPREADS (ETF + INDEX + STOCK)"
    sec_types = tuple(SEC_TYPES)
    component = "mov_ave_spread"

    def add_arguments(self, parser) -> None:
        self.add_force_arg(parser)
        self.add_sec_type_arg(parser)
        self.add_max_concurrent_arg(parser)
        self.add_code_arg(parser)

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

    async def run(self) -> None:
        t0 = time.time()
        conn = self.conn
        pool = self.pool
        args = self.args
        force = args.force
        sec_types = self.resolve_sec_types()
        max_concurrent = self.pool_size
        # ---- Single-code mode (--code): rebuild ONE security -------------
        # Bypasses the per-sec_type missing-date detection entirely — the
        # UI fires this when a security has NO analysis rows while the
        # rest of the sec_type is up to date (date-level detection would
        # see nothing missing and skip it).
        if args.code:
            total_detail = 0
            for st in sec_types:
                total_detail += await _process_single_code(
                    conn, pool, st, args.code, max_concurrent,
                )
            logger.info(f"\n  -> Upserting analysis.analysis_identity registry...")
            await self.upsert_identity(
                ANALYSIS_NAME, "mov_ave_spreads_detail", DESCRIPTION,
            )
            logger.info(f"\n  TOTAL: {total_detail:,} detail rows inserted")
            return

        # ---- Step 0: determine target dates (per-sec_type) --------------
        if args.force:
            if set(sec_types) == set(SEC_TYPES):
                # Full-universe force: EVERY sec_type is rebuilt by this
                # run, so TRUNCATE (fast, resets storage) is safe.
                logger.info("\n[0/4] Force mode: truncating detail "
                      "tables...")
                await self.truncate_tables((
                    DETAIL_TABLE, TRADING_AMT_TABLE,
                    TRADING_AMT_RATIOS_TABLE, HOLIDAY_TABLE,
                    PRICE_VS_AMT_TABLE, HIGH_LOW_PCT_TABLE,
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
                              HIGH_LOW_PCT_STREAKS_TABLE):
                    await conn.execute(
                        f"DELETE FROM {table} "
                        f"WHERE sec_type = ANY($1::text[])",
                        list(sec_types),
                    )
                logger.info("    -> deleted; will recompute scoped rows")
            # Use empty set (not None) so _process_one_sec_type knows to
            # compute ALL dates (no filtering) in force mode.
            target_dates_per_st = {st: set() for st in sec_types}
            ta_missing_per_st = {}
            ta_ratios_missing_per_st = {}
            ohlc_missing_per_st = {}
            hl_pct_missing_per_st = {}
            # Force mode rebuilds the streaks table wholesale inside
            # _process_one_sec_type; nothing to diff here.
            streaks_missing_per_st = {}
        else:
            logger.info("\n[0/4] Detecting missing dates PER-sec_type "
                  "(etf_identity vs detail[etf], index_identity vs "
                  "detail[index], stock_identity vs detail[stock])...")
            target_dates_per_st: dict = {}
            ta_missing_per_st: dict = {}
            ta_ratios_missing_per_st: dict = {}
            ohlc_missing_per_st: dict = {}
            hl_pct_missing_per_st: dict = {}
            streaks_missing_per_st: dict = {}
            for st in sec_types:
                src_tbl = SEC_TYPE_IDENTITY_TABLE[st]
                td_st = await find_missing_analysis_dates(
                    conn, DETAIL_TABLE, [src_tbl], sec_type=st,
                )
                target_dates_per_st[st] = td_st
                logger.info(f"    -> {st}: detail {len(td_st)} missing dates")
                # Also check trading_amt table independently
                td_ta = await find_missing_analysis_dates(
                    conn, TRADING_AMT_TABLE, [src_tbl], sec_type=st,
                )
                ta_missing_per_st[st] = td_ta
                if td_ta:
                    logger.info(f"    -> {st}: trading_amt {len(td_ta)} missing dates")
                # Also check trading_amt_ratios table independently
                td_tar = await find_missing_analysis_dates(
                    conn, TRADING_AMT_RATIOS_TABLE, [src_tbl], sec_type=st,
                )
                ta_ratios_missing_per_st[st] = td_tar
                if td_tar:
                    logger.info(f"    -> {st}: trading_amt_ratios "
                          f"{len(td_tar)} missing dates")
                # Also check the OHLC table independently (missing dates
                # + repair dates whose rows predate the DATE/2nd-extrema
                # columns — see ohlc.find_ohlc_repair_dates).
                td_oh = await find_missing_analysis_dates(
                    conn, OHLC_TABLE, [src_tbl], sec_type=st,
                )
                td_oh |= await find_ohlc_repair_dates(conn, st)
                ohlc_missing_per_st[st] = td_oh
                if td_oh:
                    logger.info(f"    -> {st}: ohlc {len(td_oh)} missing/repair dates")
                # The high-low-pct table is keyed by (code, month,
                # pct_type) — missing data is detected at (code, month)
                # pair granularity (see high_low_pct.
                # find_missing_high_low_pct_pairs; scoped to the active
                # universe + months with >= 255 cumulative rows + the
                # in-progress month excluded).
                hl_pairs = await find_missing_high_low_pct_pairs(
                    conn, src_tbl, st,
                )
                hl_pct_missing_per_st[st] = hl_pairs
                if hl_pairs:
                    logger.info(f"    -> {st}: high_low_pct "
                          f"{len(hl_pairs):,} missing (code, month) "
                          f"pairs")
                # The streaks table stores EXCURSION EPISODES (they
                # SHIFT with new data), so
                # per-date coverage diffing does not apply — it is
                # rebuilt wholesale whenever the sec_type is processed.
                # Only flag COMPLETELY EMPTY scopes (fresh schema /
                # wiped table) so the pipeline runs at least once.
                streaks_empty = not await conn.fetchval(
                    f"SELECT EXISTS (SELECT 1 FROM "
                    f"{HIGH_LOW_PCT_STREAKS_TABLE} WHERE sec_type = $1)",
                    st,
                )
                streaks_missing_per_st[st] = streaks_empty
                if streaks_empty:
                    logger.info(f"    -> {st}: high_low_pct_streaks empty "
                          f"(no streaks yet)")
            total_missing = sum(
                len(s) for s in target_dates_per_st.values()
            )
            total_ta_missing = sum(
                len(s) for s in ta_missing_per_st.values()
            )
            total_ta_ratios_missing = sum(
                len(s) for s in ta_ratios_missing_per_st.values()
            )
            total_ohlc_missing = sum(
                len(s) for s in ohlc_missing_per_st.values()
            )
            total_hl_pct_missing = sum(
                len(s) for s in hl_pct_missing_per_st.values()
            )
            if (
                total_missing == 0
                and total_ta_missing == 0
                and total_ta_ratios_missing == 0
                and total_ohlc_missing == 0
                and total_hl_pct_missing == 0
            ):
                logger.info("    -> DB is up to date; nothing to do.")
                return
            # For sec_types where only trading_amt / trading_amt_ratios /
            # OHLC / high_low_pct needs updates, set target_dates to
            # None so the detail step is skipped but the internal steps
            # (which have their own checks) still run.
            for st in sec_types:
                if not target_dates_per_st.get(st) and (
                    ta_missing_per_st.get(st)
                    or ta_ratios_missing_per_st.get(st)
                    or ohlc_missing_per_st.get(st)
                    or hl_pct_missing_per_st.get(st)
                ):
                    target_dates_per_st[st] = None

        # ---- Steps 1-5: process each sec_type independently -------------
        # Each sec_type is processed end-to-end (fetch → compute → insert →
        # RSI → free memory) before the next starts. This bounds peak
        # memory to a single sec_type's data — critical for the stock
        # universe (11K+ codes × 1.6K dates ≈ 17M rows / ~6 GB in pandas).
        # Loading all 3 sec_types at once (18.5M rows) OOMs the host.
        total_detail = 0
        for st in sec_types:
            td_st = target_dates_per_st.get(st) if target_dates_per_st else None
            ta_st = ta_missing_per_st.get(st) if ta_missing_per_st else None
            ta_ratio_st = (
                ta_ratios_missing_per_st.get(st)
                if ta_ratios_missing_per_st
                else None
            )
            oh_st = (
                ohlc_missing_per_st.get(st) if ohlc_missing_per_st else None
            )
            hl_st = (
                hl_pct_missing_per_st.get(st)
                if hl_pct_missing_per_st else None
            )
            sk_st = (
                streaks_missing_per_st.get(st)
                if streaks_missing_per_st else None
            )
            if td_st is not None and len(td_st) == 0 and not args.force:
                if not ta_st and not ta_ratio_st and not oh_st \
                        and not hl_st and not sk_st:
                    logger.info(f"\n  [{st}] up to date; skipping.")
                    continue
            n_detail = await _process_one_sec_type(
                conn, pool, st, td_st, args.force, max_concurrent, t0,
            )
            total_detail += n_detail

        # ---- Upsert analysis_identity ------------------------------------
        logger.info(f"\n  -> Upserting analysis.analysis_identity registry...")
        await self.upsert_identity(
            ANALYSIS_NAME, "mov_ave_spreads_detail", DESCRIPTION,
        )

        logger.info(f"\n  TOTAL: {total_detail:,} detail rows inserted")


if __name__ == "__main__":
    MovAveSpreadAnalysis().execute()
