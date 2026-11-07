"""builds/options/sse/__main__.py — Build SSE ETF options data into the 7 options_* tables.

Reads three per-day SSE sources (see downloads/_common/exchanges/sse_options.py
for the endpoint study) and writes the same split tables as the SZSE/CFFEX
builders, distinguished by exchange='SSE':

  1. EOD quotes  — temps/sse_options_daily/sse_options_daily_YYYYMMDD.csv
     (the price page https://www.sse.com.cn/assortment/options/price/ EOD
     snapshot: one row per contract post-close with day OHLC + day totals —
     close = ``last``, 成交量(手) = ``volume``, 成交额(元) = ``amount``,
     written by ``downloads.stream.sse.price --options-eod`` alongside its
     intraday archive; the daily build NEVER aggregates the intraday CSV)
  2. Contract listing — temps/sse_options_price/sse_options_price_YYYYMMDD.csv
     (preinfo 当日合约: numeric 合约编码 = contract_code, 合约简称, 类型
     认购/认沽, 行权价, 到期日, 前结算价 — authoritative, no name-regex
     parsing needed)

Listing-only dates: the 当日合约 listing has run daily for far longer than
the intraday stream, and SSE publishes no historical per-contract EOD
endpoint (yunhq ``sho/dayk`` does not exist; tstyle is live-only), so every
listing date WITHOUT a final (>= 15:00) quotes file still builds — terms,
strike and identity at full fidelity, ``prev_settle`` from the listing's
own 前结算价, and ``settle`` recovered from the NEXT listing date's
前结算价 (settle(T) == 前结算价(T+1) — the exchange publishes settlement
with a one-day lag). close / pct_change stay NULL and volume is 0 on those
dates: no source exists, the nullable option_* columns carry the gap, and
the IV calibration prices off settle instead of close.
  3. Underlying close — temps/sse_trend/sse_trend_etf_YYYYMMDD.csv (今收 of
     the 5 underlying ETFs → moneyness / IV)
  4. Risk indicators — temps/sse_options_risk/sse_options_risk_YYYYMMDD.csv
     (the exchange's 期权风险指标 table, downloadRisk.do; supplies
     Delta/Theta/Gamma/Vega/Rho DIRECTLY per contract — never recomputed.
     Dates without a risk file fall back to the shared BS pipeline;
     implied_vol has no published SSE source and stays model-computed
     off close.)

SSE-specific gaps vs the SZSE source (by design, documented):
  * settle — SSE publishes the daily settlement price with a one-day lag;
    rows carry settle recovered from the next listing date's 前结算价
    (see ``build_settle_map``); the latest built date and expiry-day
    contracts (absent from every later listing) keep settle = NULL and
    price IV off ``close``. Normal runs refresh the most recent built
    date once its settle becomes knowable (``_pick_settle_refresh_date``).
  * open_interest — no per-contract OI source exists for SSE (tstyle
    ``position`` returns null; no daily-stats endpoint), so per the
    accepted approximation ``open_interest`` carries the per-contract
    ACCUMULATED VOLUME since the intraday stream started
    (``chain_open_interest``: OI(T) = OI(prev trading row) + volume(T),
    seeded from the latest stored row before the build window). The same
    upper-bound estimator ``live.options_intraday_oi`` chains its
    intraday estimate from (oi_base = this column); closes/rolls are not
    netted, so the series only grows while a contract trades and freezes
    after expiry. Listing-only dates (volume 0) carry the level forward.
  * Greeks — loaded directly from the exchange risk table
    (temps/sse_options_risk/, scheduled in downloads_daily.sh); the BS
    pipeline is the fallback only.

Missing-data detection: dates available = contract-listing dates (a final
quotes file upgrades the date to the full-EOD path); missing = those not in
``SELECT DISTINCT date FROM stats.options_terms WHERE exchange='SSE'``.
Underlying closes: temps/sse_trend_etf_*.csv daily snapshots, with the
per-code dayk archives temps/sse_archive/{code}_trend.csv filling any
(etf_code, date) pair the snapshots lack (fetch via the
``downloads.stock.sse.archive`` dayk endpoint — full immutable history).

Backfill fix: a build date whose daily CSV is missing (EOD capture failed
or ran before the file landed) gets one reconstructed from the intraday
raw archive — the LAST sample per contract carries the day-cumulative
volume/amount at that moment (``backfill_daily_from_intraday``). A
partial-day intraday archive yields a partial daily row — better than no
volume/amount; the captured ``update_time`` column keeps the shortfall
visible. No external sources are used.

Usage:
  python -m builds.options.sse
  python -m builds.options.sse --start-date 2026-09-01 --end-date 2026-09-21
  python -m builds.options.sse --force
  python -m builds.options.sse --code 510050           (single-underlying test filter)
  python -m builds.options.sse --date 2026-09-21       (force single-date refresh)

Also embedded as PHASE 3 of ``python -m builds.options``.
"""

# Runtime bootstrap — pre-check → silence warnings → cudf.pandas hook →
# UTF-8 stdout. MUST run before the pandas import below (import hook).
from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()


import datetime
import sys

import numpy as np
import pandas as pd

from _common.build_commons import (
    parse_date, ymd_from_filename, ymd_to_date,
    glob_source_files,
    TODAY_STR,
    truncate_table_async,
    forced_date_scope,
    bulk_upsert_async,
)
from builds._commons.row_emission import dates_as_date_list
from _common.log_setup import setup_logging

from builds.options.__main__ import OptionsBuild

from builds.options.tables import build_split_tables

from builds.options.sse.builders import (
    SSE_OPTIONS_DAILY_DIR,
    SSE_OPTIONS_PRICE_DIR,
    SSE_OPTIONS_RISK_DIR,
    SSE_TREND_DIR,
    _newest_listing_underlyings,
    _scan_final_quote_ymds,
    add_derived_columns,
    backfill_daily_from_intraday,
    build_contract_meta_df,
    build_listing_only_quotes,
    build_quotes_df,
    build_settle_map,
    chain_open_interest,
    load_etf_close,
    load_risk_indicators,
)

import asyncio

# Epoch anchor for vectorized days_to_expiry math (no date objects in frames)
_EPOCH = datetime.date(1970, 1, 1)

logger = setup_logging("sse_options")


class SseOptionsBuild(OptionsBuild):
    """``python -m builds.options.sse`` — SSE ETF options ETL → DB.

    Embeddable: builds.options constructs this class with preset args and
    awaits amain() directly (its SSE child phase).
    """

    title = "BUILD SSE ETF OPTIONS  ·  missing-data-only → DATABASE"
    component = "sse_options"

    def add_arguments(self, parser) -> None:
        self.add_date_range_args(parser)
        self.add_code_arg(parser)

    def apply_args(self) -> None:
        self.forced_date = self.apply_date_force_args()
        if self.forced_date:
            self.args.start_date = self.forced_date.isoformat()
            self.args.end_date = self.forced_date.isoformat()

        # SSE option underlyings are bare 6-digit ETF codes (510050 …)
        self.code_filter = self.resolve_code_filter(strip_suffix=True)

    def header_fields(self) -> dict:
        return {
            "Quotes dir":  SSE_OPTIONS_DAILY_DIR,
            "Date range":  f"{self.args.start_date or '(all)'} → {self.args.end_date or '(all)'}",
            "Code filter": self.code_filter or "(none — all underlyings)",
            "Today":       TODAY_STR,
        }

    async def fetch_prev_oi_seed(self, conn, before_date, code_filter=None) -> pd.DataFrame:
        """Latest stored SSE open_interest per contract strictly before a date.

        Seeds ``chain_open_interest`` so a build window continues each
        contract's accumulated-volume OI from where the stored rows left off
        (0 for contracts never built before the cutoff).
        """
        where_extra = ""
        params: list = [before_date]
        if code_filter:
            where_extra = " AND t.underlying_code = $2"
            params.append(code_filter)
        rows = await conn.fetch(
            "SELECT DISTINCT ON (v.contract_code) v.contract_code, v.open_interest "
            "FROM stats.options_volume_oi v "
            "JOIN stats.options_terms t "
            "  ON t.contract_code = v.contract_code AND t.date = v.date "
            f"WHERE v.exchange = 'SSE' AND v.date < $1{where_extra} "
            "ORDER BY v.contract_code, v.date DESC",
            *params,
        )
        if not rows:
            return pd.DataFrame(columns=["contract_code", "open_interest"])
        return pd.DataFrame({
            "contract_code": [r["contract_code"] for r in rows],
            "open_interest": [float(r["open_interest"] or 0.0) for r in rows],
        })

    async def find_missing_sse_dates(
        self,
        conn,
        source_dates: set,
        code_filter: str | None = None,
        source_underlyings: set | None = None,
    ) -> set:
        """Dates from source_dates whose (date, underlying) coverage is incomplete.

        A date is missing while ANY source underlying lacks rows that date — a
        partially-built date (one underlying's source arrived late) or a newly
        listed underlying whose whole history is otherwise "present" both
        re-enter the queue. The universe defaults to the underlyings already in
        the table; pass ``source_underlyings`` (today's listing codes) to catch
        underlyings with no rows at all.

        Probes stats.options_terms — NOT options_identity: the SSE options
        stream writes identity rows itself (FK parent for intraday bars), so
        identity-based checks would mask dates whose 7-table build is missing.
        """
        if not source_dates:
            return set()

        if code_filter:
            sql = (
                'SELECT DISTINCT date, underlying_code FROM stats.options_terms '
                "WHERE underlying_code = $1 AND exchange = 'SSE'"
            )
            existing_rows = await conn.fetch(sql, code_filter)
        else:
            sql = ("SELECT DISTINCT date, underlying_code FROM stats.options_terms "
                   "WHERE exchange = 'SSE'")
            existing_rows = await conn.fetch(sql)
        existing_pairs = {(r["date"], r["underlying_code"]) for r in existing_rows
                          if r["date"] is not None}

        universe = source_underlyings if source_underlyings else {
            u for _, u in existing_pairs}
        if code_filter:
            universe = {code_filter}
        if not universe:
            return set(source_dates)

        return {
            d for d in source_dates
            if any((d, u) not in existing_pairs for u in universe)
        }

    async def upsert_split_tables_date_mode(self, conn, tables) -> None:
        """ON CONFLICT (date, contract_code) DO UPDATE upsert, FK parent first."""
        for tbl, rows in tables.items():
            if not rows:
                logger.info(f"    [DB] No rows to upsert into {tbl}")
                continue
            n = await bulk_upsert_async(conn, tbl, rows, key_columns=["date", "contract_code"])
            logger.info(f"    [DB] Upserted {n:,} rows into {tbl}")

    async def _pick_settle_refresh_date(self, conn, missing_dates, code_filter, max_listing_date):
        """Most recent already-built SSE date whose settle can now be filled.

        settle(T) arrives with the T+1 当日合约 listing (one-day publication
        lag — ``build_settle_map``), so a normal run refreshes at most one
        prior date: the newest built date before the missing dates' minimum
        (daily flow: yesterday), or the latest built date itself when the DB
        is otherwise current. Dates without a newer listing file, or whose
        settle is already complete, are left alone.
        """
        if max_listing_date is None:
            return None

        where_code = " AND underlying_code = $1" if code_filter else ""
        params = [code_filter] if code_filter else []
        rows = await conn.fetch(
            "SELECT DISTINCT date FROM stats.options_terms "
            f"WHERE exchange = 'SSE'{where_code} "
            "ORDER BY date DESC LIMIT 60",
            *params,
        )
        built_desc = [r["date"] for r in rows]
        if not built_desc:
            return None

        if missing_dates:
            cut = min(missing_dates)
            prior = [d for d in built_desc if d < cut]
            if not prior:
                return None
            cand = prior[0]  # newest built date before the new dates
        else:
            cand = built_desc[0]  # latest built date
        if cand >= max_listing_date:
            return None  # no newer listing → nothing to recover yet

        n_null = await conn.fetchval(
            "SELECT count(*) FROM stats.options_settlement "
            "WHERE exchange = 'SSE' AND date = $1 AND settle IS NULL",
            cand,
        )
        if not n_null:
            return None
        return cand

    async def run(self) -> None:
        args = self.args
        code_filter = self.code_filter
        forced_date = self.forced_date

        if code_filter:
            logger.info(f"    [CODE FILTER] Restricting build to single underlying: {code_filter}")
        if forced_date:
            logger.info(f"[DATE MODE] Forced single-date build: {forced_date}")

        # ------------------------------------------------------------------
        # 1. Discover source files and available dates
        # ------------------------------------------------------------------
        logger.info("\n[1/4] Discovering source CSV files …")
        quote_files = glob_source_files(SSE_OPTIONS_DAILY_DIR, "sse_options_daily_*.csv")
        meta_files = glob_source_files(SSE_OPTIONS_PRICE_DIR, "sse_options_price_*.csv")
        etf_files = glob_source_files(SSE_TREND_DIR, "sse_trend_etf_*.csv")
        logger.info(f"    → {len(quote_files)} quote files, {len(meta_files)} contract-listing files, "
              f"{len(etf_files)} ETF close files available")

        if not meta_files:
            logger.error("    [FATAL] No contract-listing source CSVs found")
            sys.exit(1)

        # available = every listing date in range; a final (>=15:00) quotes
        # file upgrades the date to the full-EOD path (close + volume), the
        # rest build listing-only (terms + strike + settle — see
        # build_listing_only_quotes). ETF closes are gap-filled from the
        # dayk archives (moneyness needs the spot on every date).
        meta_ymds = {ymd_from_filename(f, "sse_options_price_") for f in meta_files} - {None}
        # current venue universe from the newest listing (missing-date
        # detection uses it — see find_missing_sse_dates)
        listing_universe = _newest_listing_underlyings(meta_files)

        available_dates = {d for y in meta_ymds if (d := ymd_to_date(y))}

        if args.start_date:
            start_d = parse_date(args.start_date)
            if start_d:
                available_dates = {d for d in available_dates if d >= start_d}
        if args.end_date:
            end_d = parse_date(args.end_date)
            if end_d:
                available_dates = {d for d in available_dates if d <= end_d}

        logger.info(f"    → {len(available_dates)} listing dates available in range")

        # ------------------------------------------------------------------
        # 2. Connect to DB and find missing dates
        # ------------------------------------------------------------------
        logger.info("\n[2/4] Connecting to database and detecting missing dates …")
        conn = await self.connect_db()

        try:
            if args.force:
                if code_filter:
                    logger.info(f"    [DB] Force mode for underlying {code_filter}: deleting existing rows")
                    from builds.options.tables import delete_underlying_rows_async
                    await delete_underlying_rows_async(conn, code_filter)
                else:
                    logger.info("    [DB] Force mode: truncating existing tables")
                    for tbl in ("stats.options_aggregate", "stats.options_volume_oi",
                                "stats.options_greeks", "stats.options_settlement",
                                "stats.options_strike", "stats.options_terms",
                                "stats.options_identity"):
                        await truncate_table_async(conn, tbl)
                missing_dates = available_dates
            elif forced_date:
                missing_dates = forced_date_scope(available_dates, forced_date)
            else:
                missing_dates = await self.find_missing_sse_dates(
                    conn, available_dates, code_filter=code_filter,
                    source_underlyings=listing_universe)
                # Settle-lag refresh: settle(T) is only knowable once the
                # T+1 listing exists, so also rebuild the most recent built
                # date that precedes a newer listing file — keeps settle
                # self-healing on daily runs without --date gymnastics.
                refresh_date = await self._pick_settle_refresh_date(
                    conn, missing_dates, code_filter,
                    max_listing_date=max(available_dates) if available_dates else None)
                if refresh_date:
                    logger.info(f"    [DB] settle-lag refresh of already-built date {refresh_date}")
                    missing_dates = set(missing_dates) | {refresh_date}

            logger.info(f"    [DB] {len(missing_dates)} dates to build "
                  f"(missing or settle-refresh; out of {len(available_dates)} available)")

            if not missing_dates:
                logger.info("    [INFO] Database is up to date — no new dates to insert")
                return

            # ------------------------------------------------------------------
            # 3. Read only missing-date source files and build the options frame
            # ------------------------------------------------------------------
            logger.info(f"\n[3/4] Reading source CSVs for {len(missing_dates)} missing dates …")
            missing_ymd = {d.strftime("%Y%m%d") for d in missing_dates}

            # Backfill fix: daily CSVs missing for build dates are
            # reconstructed from the intraday archives (last sample =
            # day-cumulative volume/amount) before the quote glob below;
            # the finality scan runs AFTER it so regenerated files count
            backfilled = backfill_daily_from_intraday(missing_ymd)
            if backfilled:
                quote_files = glob_source_files(SSE_OPTIONS_DAILY_DIR, "sse_options_daily_*.csv")
            final_quote_ymds, n_not_final = _scan_final_quote_ymds(
                quote_files, meta_ymds, forced_date)
            logger.info(f"    → {len(final_quote_ymds)} dates with daily quote files "
                  f"({n_not_final} non-final → listing-only path)")

            missing_quote_files = [
                f for f in quote_files
                if ymd_from_filename(f, "sse_options_daily_") in missing_ymd
                and ymd_from_filename(f, "sse_options_daily_") in final_quote_ymds
            ]
            # listings: build dates plus every LATER file — build_settle_map
            # reads the T+1 前结算价 off those rows (look-ahead)
            min_build_ymd = min(missing_ymd)
            meta_lookahead_files = [
                f for f in meta_files
                if ymd_from_filename(f, "sse_options_price_") >= min_build_ymd
            ]
            missing_etf_files = [
                f for f in etf_files
                if ymd_from_filename(f, "sse_trend_etf_") in missing_ymd
            ]
            logger.info(f"    → {len(missing_quote_files)} quote files, {len(meta_lookahead_files)} "
                  f"contract-listing files (incl. settle look-ahead), "
                  f"{len(missing_etf_files)} ETF close files to read")

            quotes_df = build_quotes_df(missing_quote_files)
            meta_df = build_contract_meta_df(meta_lookahead_files)
            if len(meta_df) == 0:
                logger.info("    [INFO] No rows parsed from listing files")
                return

            # listing-only dates (no final quotes file) → settle-only rows
            listing_only_ymd = {y for y in missing_ymd if y not in final_quote_ymds}
            synth_df = build_listing_only_quotes(meta_df, listing_only_ymd)
            if len(quotes_df) == 0 and len(synth_df) == 0:
                logger.info("    [INFO] No rows parsed from missing-date files")
                return
            if len(synth_df) and len(quotes_df):
                quotes_df = pd.concat([quotes_df, synth_df], ignore_index=True)
            elif len(synth_df):
                quotes_df = synth_df

            # meta's own prev_settle stays out of the join — the quotes side
            # carries it (intraday prev_settle == listing 前结算价, verified)
            options_df = quotes_df.merge(
                meta_df.drop(columns=["prev_settle"]),
                on=["date", "trade_code"], how="inner")
            n_dropped = len(quotes_df) - len(options_df)
            if n_dropped:
                logger.info(f"    [OPTIONS] {n_dropped} quote rows without a same-day contract listing — dropped")
            if len(options_df) == 0:
                logger.info("    [INFO] No joinable option rows")
                return

            # official settle(T) from the next listing date's 前结算价
            settle_map = build_settle_map(meta_df)
            if len(settle_map):
                options_df = options_df.merge(
                    settle_map, on=["date", "contract_code"], how="left")
            else:
                options_df = options_df.assign(settle=np.nan)

            # accumulated-volume OI chain — seed from the latest stored rows
            # before the build window so daily/--date runs continue each
            # contract's level instead of restarting it
            oi_seed = await self.fetch_prev_oi_seed(conn, min(missing_dates))
            options_df = chain_open_interest(options_df, oi_seed)

            if code_filter:
                n_before = len(options_df)
                options_df = options_df[
                    options_df["underlying_code"] == code_filter
                ].reset_index(drop=True)
                logger.info(f"    [CODE FILTER] Options rows {n_before:,} → {len(options_df):,} "
                      f"for underlying {code_filter}")
                if len(options_df) == 0:
                    logger.info("    [INFO] Nothing to do for this underlying (SSE underlyings: "
                          "510050 / 510300 / 510500 / 588000 / 588080)")
                    return

            logger.info(f"    → {len(options_df):,} options rows  ·  {options_df['underlying_code'].nunique()} underlyings")
            _d_range = dates_as_date_list(options_df["date"])
            logger.info(f"    → date range: {min(_d_range)} → {max(_d_range)}")

            etf_close = load_etf_close(missing_etf_files)

            # Exchange risk-indicator table (published greeks) for the same
            # missing dates — missing/empty files fall back to the BS pipeline.
            risk_files_all = glob_source_files(SSE_OPTIONS_RISK_DIR, "sse_options_risk_*.csv")
            missing_risk_files = [
                f for f in risk_files_all
                if ymd_from_filename(f, "sse_options_risk_") in missing_ymd
            ]
            risk_df = load_risk_indicators(missing_risk_files)

            options_df = add_derived_columns(options_df, etf_close, risk_df=risk_df)

            # Host boundary: cuDF → host pandas ONCE at the DB boundary
            # (asyncpg codecs need host types; mirrors the SZSE builder)
            if hasattr(options_df, "to_pandas"):
                options_df = options_df.to_pandas()

            # ------------------------------------------------------------------
            # 4. Insert to database
            # ------------------------------------------------------------------
            logger.info("\n[4/4] Inserting data to database …")

            options_db = options_df.drop_duplicates(subset=["date", "contract_code"], keep="last")

            tables = build_split_tables(
                options_db, underlying_target_type="ETF", exchange="SSE",
            )
            # Always the ON CONFLICT upsert path (never plain COPY): the SSE
            # options stream co-writes options_identity rows (FK parent for
            # intraday bars) before this builder runs each day, so fresh
            # normal-mode inserts would conflict on existing identity rows.
            await self.upsert_split_tables_date_mode(conn, tables)

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
    SseOptionsBuild().execute()
