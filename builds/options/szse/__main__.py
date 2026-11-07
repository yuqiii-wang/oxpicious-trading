"""
build_szse_options.py — Build SZSE ETF Options data and insert directly to the
database (no intermediate CSV, missing-data-only).

Reads per-day SZSE option CSV files from szse_trend/szse_trend_option_YYYYMMDD.csv
and ETF OHLCV from szse_trend/szse_trend_etf_YYYYMMDD.csv (for moneyness).

Missing-data detection flow:
  1. Glob szse_trend_option_*.csv → discover available dates from filenames
  2. Query SELECT DISTINCT date FROM stats.options_identity → existing dates
  3. missing_dates = available_dates - existing_dates
  4. Read ONLY option files + ETF files whose YMD ∈ missing_dates
  5. Parse contracts, compute derived columns (moneyness, ratios, IV, Greeks)
  6. Bulk upsert into 7 options_* tables

With --force: truncate all 7 options_* tables first, so all source dates are
treated as missing.

With --date YYYY-MM-DD: scope the run to that single date and bypass the DB
missing-date skip — the date is always (re)processed and rows already in the
DB are refreshed through the ON CONFLICT upsert write path (no truncation,
no deletes). Mutually exclusive with --force.

Parses key fields from 合约简称 (contract name):
  • 标的名称 (underlying_name) - e.g., "深证100ETF"
  • 期权类型 (option_type) - 购=CALL, 沽=PUT
  • 到期月份 (expiry_month) - 11月, 12月, 3月, 6月
  • 行权价 (strike_price) - numeric strike (A suffix = contract series, not a scale change)

Parses 标的证券简称（代码）:
  • underlying_code - 6-digit ETF code

Derived columns for volatility smile / option wall analysis:
  • moneyness_ratio - strike / underlying_close (normalized moneyness)
  • days_to_expiry - calendar days from trade date to expiry
  • open_interest_pct - OI as % of total OI for that underlying
  • volume_pct - volume as % of total volume for that underlying
  • oi_call_put_ratio - call OI / put OI for each strike
  • vol_call_put_ratio - call volume / put volume for each strike

Inserts to database tables:
  • options_identity   (date, contract_code, contract_name)
  • options_terms      (date, contract_code, underlying, type, expiry, days_to_expiry)
  • options_strike     (date, contract_code, strike_str, strike_price, has_a_suffix)
  • options_settlement (date, contract_code, prev_settle, close, settle, pct_change, underlying_close, moneyness_ratio)
  • options_greeks     (date, contract_code, IV, delta, theta, gamma, vega, rho)
  • options_volume_oi  (date, contract_code, volume, open_interest, _wan variants)
  • options_aggregate  (date, contract_code, totals, pcts, call/put ratios)

Usage:
  python build_szse_options.py
  python build_szse_options.py --start-date 2024-01-01 --end-date 2025-12-31
  python build_szse_options.py --force
  python build_szse_options.py --code 159915           (single-underlying test filter)
  python build_szse_options.py --date 2026-08-28       (force single-date rebuild, no DB skip)
"""

# Runtime bootstrap — pre-check → silence warnings → cudf.pandas hook →
# UTF-8 stdout. MUST run before the pandas import below (import hook).
from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()


import asyncio
import os, sys

from _common.build_commons import (
    PROJECT_ROOT, TODAY_STR,
    bulk_upsert_async,
)
from builds._commons.row_emission import dates_as_date_list

from builds.options.__main__ import OptionsBuild

from builds.options.szse.builders import (
    add_derived_columns,
    build_options_df,
    load_etf_ohlcv,
)

# ============================================================================
# Paths
# ============================================================================
SZSE_TREND_DIR = os.path.join(PROJECT_ROOT, "temps", "szse_trend")


# CFFEX index-option underlying codes (IO/HO/MO/CO → index codes) — used
# to skip this SZSE-only builder when --code targets a CFFEX underlying.
from builds.options.cffex.config import PRODUCT_UNDERLYING as _CFFEX_PRODUCT_UNDERLYING  # noqa: E402

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("szse")
_CFFEX_INDEX_UNDERLYING_CODES = frozenset(
    code for code, _name in _CFFEX_PRODUCT_UNDERLYING.values()
)


class SzseOptionsBuild(OptionsBuild):
    """``python -m builds.options.szse`` — SZSE ETF options ETL → DB.

    Embeddable: builds.options constructs this class with preset args and
    awaits amain() directly (its SZSE child phase).
    """

    title = "BUILD SZSE ETF OPTIONS  ·  missing-data-only → DATABASE"
    component = "szse"

    def add_arguments(self, parser) -> None:
        self.add_date_range_args(parser)
        self.add_code_arg(parser)

    def apply_args(self) -> None:
        # --date / --force are mutually exclusive; parse the forced single date.
        # When set, the date also supersedes any explicit --start/--end range so
        # discovery/loading is scoped to this single day.
        self.forced_date = self.apply_date_force_args()
        if self.forced_date:
            self.args.start_date = self.forced_date.isoformat()
            self.args.end_date = self.forced_date.isoformat()

        # SZSE option underlyings are bare 6-digit ETF codes (e.g. 159915) —
        # strip the exchange suffix normalize_code may have appended.
        self.code_filter = self.resolve_code_filter(strip_suffix=True)

    def header_fields(self) -> dict:
        return {
            "Trend dir":  SZSE_TREND_DIR,
            "Date range": f"{self.args.start_date or '(all)'} → {self.args.end_date or '(all)'}",
            "Code filter": self.code_filter or "(none — all underlyings)",
            "Today":      TODAY_STR,
        }

    async def find_missing_szse_dates(
        self,
        conn,
        source_dates,
        code_filter: str | None = None,
    ):
        """Find dates from source_dates that do NOT already have SZSE options data.

        Scoped to ``exchange = 'SZSE'`` on the options tables (every options_*
        table carries the venue column) — CFFEX/SSE rows never mask SZSE gaps.

        With code_filter (bare underlying code), the check is scoped to that
        underlying via stats.options_terms — dates loaded for OTHER underlyings
        no longer mask this underlying's gaps.
        """
        if not source_dates:
            return set()

        if code_filter:
            sql = (
                'SELECT DISTINCT date FROM stats.options_terms '
                "WHERE underlying_code = $1 AND exchange = 'SZSE'"
            )
            existing_rows = await conn.fetch(sql, code_filter)
        else:
            sql = "SELECT DISTINCT date FROM stats.options_identity WHERE exchange = 'SZSE'"
            existing_rows = await conn.fetch(sql)
        existing_dates = {r["date"] for r in existing_rows if r["date"] is not None}

        return source_dates - existing_dates

    async def upsert_split_tables_date_mode(self, conn, tables) -> None:
        """Upsert each split table (FK parent first) for --date mode.

        insert_split_tables' plain COPY is conflict-free only because rows are
        PK-checked missing dates upstream; --date mode intentionally re-processes
        rows that may already exist (refresh semantics), so every table is
        written with a bulk ON CONFLICT (date, contract_code) DO UPDATE upsert
        instead — no truncation, no deletes.
        """
        for tbl, rows in tables.items():
            if not rows:
                logger.info(f"    [DB] No rows to upsert into {tbl}")
                continue
            n = await bulk_upsert_async(conn, tbl, rows, key_columns=["date", "contract_code"])
            logger.info(f"    [DB] Upserted {n:,} rows into {tbl}")

    async def run(self) -> None:
        from builds._commons.code_filter import normalize_code  # noqa: F401
        from _common.build_commons import (
            get_db_or_exit, parse_date, ymd_from_filename, ymd_to_date,
            glob_source_files, truncate_table_async, forced_date_scope,
        )

        args = self.args
        code_filter = self.code_filter
        forced_date = self.forced_date

        if code_filter:
            logger.info(f"    [CODE FILTER] Restricting build to single underlying: {code_filter}")
            if code_filter in _CFFEX_INDEX_UNDERLYING_CODES:
                logger.info("    [CODE FILTER] CFFEX index underlying — nothing to do for SZSE; skipping")
                return
        if forced_date:
            logger.info(f"[DATE MODE] Forced single-date build: {forced_date}")

        # ------------------------------------------------------------------
        # 1. Discover source files and available dates
        # ------------------------------------------------------------------
        logger.info("\n[1/4] Discovering source CSV files …")
        option_files = glob_source_files(SZSE_TREND_DIR, "szse_trend_option_*.csv")
        etf_files = glob_source_files(SZSE_TREND_DIR, "szse_trend_etf_*.csv")
        logger.info(f"    → {len(option_files)} option files, {len(etf_files)} ETF files available")

        if not option_files:
            logger.error("    [FATAL] No option source CSVs found")
            sys.exit(1)

        # Extract available dates from option file filenames
        available_dates = set()
        for f in option_files:
            ymd = ymd_from_filename(f, "szse_trend_option_")
            if ymd:
                d = ymd_to_date(ymd)
                if d:
                    available_dates.add(d)

        # Apply --start-date / --end-date range filter
        if args.start_date:
            start_d = parse_date(args.start_date)
            if start_d:
                available_dates = {d for d in available_dates if d >= start_d}
        if args.end_date:
            end_d = parse_date(args.end_date)
            if end_d:
                available_dates = {d for d in available_dates if d <= end_d}

        logger.info(f"    → {len(available_dates)} unique dates available in range")

        # ------------------------------------------------------------------
        # 2. Connect to DB and find missing dates
        # ------------------------------------------------------------------
        logger.info("\n[2/4] Connecting to database and detecting missing dates …")
        conn = await self.connect_db()

        try:
            if args.force:
                if code_filter:
                    # Single-code force mode: delete only this underlying's rows
                    # instead of truncating (FK-safe order handled by the helper).
                    logger.info(f"    [DB] Force mode for underlying {code_filter}: deleting existing rows")
                    from builds.options.tables import delete_underlying_rows_async
                    await delete_underlying_rows_async(conn, code_filter)
                else:
                    logger.info("    [DB] Force mode: truncating existing tables")
                    # CASCADE truncates all FK child tables automatically
                    for tbl in ("stats.options_aggregate", "stats.options_volume_oi",
                                "stats.options_greeks", "stats.options_settlement",
                                "stats.options_strike", "stats.options_terms",
                                "stats.options_identity"):
                        await truncate_table_async(conn, tbl)
                missing_dates = available_dates
            elif forced_date:
                # --date mode: bypass the DB missing-date skip — the forced date
                # is ALWAYS (re)processed; rows already in the DB are refreshed
                # through the ON CONFLICT upsert path in step 4 (no deletes).
                missing_dates = forced_date_scope(available_dates, forced_date)
            else:
                # Query DISTINCT dates for SZSE-specific contracts (excluding
                # CFFEX prefixes). This ensures CFFEX data doesn't mask dates
                # that still need SZSE data. With --code, only that underlying's
                # dates are checked.
                missing_dates = await self.find_missing_szse_dates(conn, available_dates, code_filter=code_filter)

            logger.info(f"    [DB] {len(missing_dates)} dates missing from stats.options_identity "
                  f"(out of {len(available_dates)} available)")

            if not missing_dates:
                logger.info("    [INFO] Database is up to date — no new dates to insert")
                return

            # ------------------------------------------------------------------
            # 3. Read only missing-date source files and build options frame
            # ------------------------------------------------------------------
            logger.info(f"\n[3/4] Reading source CSVs for {len(missing_dates)} missing dates …")
            missing_ymd = {d.strftime("%Y%m%d") for d in missing_dates}

            missing_option_files = [
                f for f in option_files
                if ymd_from_filename(f, "szse_trend_option_") in missing_ymd
            ]
            missing_etf_files = [
                f for f in etf_files
                if ymd_from_filename(f, "szse_trend_etf_") in missing_ymd
            ]
            logger.info(f"    → {len(missing_option_files)} option files, {len(missing_etf_files)} ETF files to read")

            options_df = build_options_df(missing_option_files)

            # Filter to the target underlying if --code is set — BEFORE the
            # derived columns, whose per-underlying aggregates (volume_pct,
            # total_volume_underlying, …) are computed within one underlying.
            if code_filter and len(options_df) > 0:
                n_before = len(options_df)
                options_df = options_df[
                    options_df["underlying_code"] == code_filter
                ].reset_index(drop=True)
                logger.info(f"    [CODE FILTER] Options rows {n_before:,} → {len(options_df):,} for underlying {code_filter}")

            if len(options_df) == 0:
                logger.info("    [INFO] No options rows parsed from missing-date files")
                return

            logger.info(f"    → {len(options_df):,} options rows  ·  {options_df['underlying_code'].nunique()} underlyings")
            # ONE numpy pass — Timestamp.date on a GPU-backed proxy Timestamp
            # takes the cudf slow path per call
            _d_range = dates_as_date_list(options_df["date"])
            logger.info(f"    → date range: {min(_d_range)} → {max(_d_range)}")

            etf_ohlcv = load_etf_ohlcv(missing_etf_files)
            options_df = add_derived_columns(options_df, etf_ohlcv)

            # Host boundary: .dt.date is NOT implemented in cuDF — the object-date
            # column assigned back cascades MixedTypeError / "Fast-to-slow
            # transfer is blocked" on every later op. Convert to host pandas
            # ONCE (asyncpg DATE codec needs datetime.date, all conversions
            # below are then proxy-free). Must happen AFTER add_derived_columns:
            # merging a real-pandas frame with a cudf.pandas proxy frame trips
            # "TypeError: all inputs must be Index" inside the merge.
            if hasattr(options_df, "to_pandas"):  # GPU frame → host at DB boundary
                options_df = options_df.to_pandas()

            # ------------------------------------------------------------------
            # 4. Insert to database
            # ------------------------------------------------------------------
            logger.info("\n[4/4] Inserting data to database …")

            # Dates stay datetime64 on the frame: a .dt.date object column
            # poisons every later cudf op with MixedTypeError fallbacks. The
            # datetime64 columns are emitted as datetime.date by
            # records_from_frame's numpy M-branch (asyncpg DATE codec).
            options_db = options_df.copy()

            # Dedupe within the batch to avoid duplicate (date, contract_code)
            # PKs (multiple files may produce the same contract row).
            options_db = options_db.drop_duplicates(subset=["date", "contract_code"], keep="last")

            # Split into the 7 options_* tables: plain COPY-insert when rows
            # are PK-checked missing dates (conflict-free), ON CONFLICT upsert
            # in --date mode (rows may already exist → refreshed, not duplicated).
            from builds.options.tables import build_split_tables, insert_split_tables

            tables = build_split_tables(
                options_db, underlying_target_type="ETF", exchange="SZSE",
            )
            if forced_date:
                # --date refresh: rows may already exist → ON CONFLICT upsert
                # (plain COPY in insert_split_tables would hit PK conflicts)
                await self.upsert_split_tables_date_mode(conn, tables)
            else:
                await insert_split_tables(conn, tables)

        finally:
            await conn.close()

        # Console summary
        logger.info(f"\n  Underlying distribution:")
        for code, sub in options_df.groupby("underlying_code"):
            name = str(sub["underlying_name"].dropna().iloc[0]) if sub["underlying_name"].notna().any() else ""
            n_dates = int(sub["date"].dt.strftime("%Y-%m-%d").nunique())
            n_strikes = int(sub["strike_price"].nunique())
            logger.info(f"    · {code:<8s} {name:<12s} {n_dates:>4d} days  {n_strikes:>3d} strikes")


if __name__ == "__main__":
    SzseOptionsBuild().execute()
