"""builds/options/sse/__main__.py — Build SSE ETF options data into the 7 options_* tables.

Reads three per-day SSE sources (see downloads/_common/exchanges/sse_options.py
for the endpoint study) and writes the same split tables as the SZSE/CFFEX
builders, distinguished by exchange='SSE':

  1. EOD quotes  — temps/sse_options_intraday/sse_options_intraday_YYYYMMDD.csv
     (the stream's raw snapshot archive; the LAST sample per contract is the
     EOD state — day OHLC + day-cumulative volume, captured post-close by
     ``downloads.stream.sse.price --options-eod`` or the final in-session poll)
  2. Contract listing — temps/sse_options_price/sse_options_price_YYYYMMDD.csv
     (preinfo 当日合约: numeric 合约编码 = contract_code, 合约简称, 类型
     认购/认沽, 行权价, 到期日 — authoritative, no name-regex parsing needed)
  3. Underlying close — temps/sse_trend/sse_trend_etf_YYYYMMDD.csv (今收 of
     the 5 underlying ETFs → moneyness / IV)

SSE-specific gaps vs the SZSE source (by design, documented):
  * settle — SSE publishes the daily settlement price with a lag; rows are
    written with settle = NULL and the IV/Greeks calibration prices off
    ``close`` (settle coalesced to close). Backfilling settle from the t+1
    snapshot's prev_settle is a future refinement (re-run with --date).
  * open_interest — no per-contract OI source exists for SSE (tstyle
    ``position`` returns null); OI columns are NULL and the OI aggregate
    ratios degenerate to 0. Per-underlying OI totals exist on the exchange's
    每日统计 endpoint (future: downloads → options_aggregate refresh).
  * Greeks — Black-Scholes via the shared _common.df_utils pipeline (same as
    SZSE/CFFEX). The exchange-published risk CSV (temps/sse_options_risk/)
    is a possible future override source.

Missing-data detection: dates available = EOD-quote CSVs whose last sample is
>= 15:00 (final) AND a same-day contract listing exists; missing = those not
in ``SELECT DISTINCT date FROM stats.options_identity WHERE exchange='SSE'``.

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
import os
import sys

import numpy as np
import pandas as pd

from downloads._common import read_csv_preferred
from _common.df_utils import safe_columns

from _common.build_commons import (
    parse_date, ymd_from_filename, ymd_to_date,
    glob_source_files,
    TODAY_STR,
    truncate_table_async,
    forced_date_scope,
    bulk_upsert_async,
)
from builds._commons.safe_parse import safe_to_numeric, safe_to_datetime
from builds._commons.row_emission import dates_as_date_list
from _common.log_setup import setup_logging

from _common.data_build import DataBuild

from builds.options.tables import build_split_tables

import asyncio

# Epoch anchor for vectorized days_to_expiry math (no date objects in frames)
_EPOCH = datetime.date(1970, 1, 1)

# ============================================================================
# Paths
# ============================================================================
SSE_OPTIONS_INTRADAY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "temps", "sse_options_intraday")
SSE_OPTIONS_PRICE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "temps", "sse_options_price")
SSE_TREND_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "temps", "sse_trend")

logger = setup_logging("sse_options")


def _resolve(path: str) -> str:
    return os.path.normpath(os.path.abspath(path))


SSE_OPTIONS_INTRADAY_DIR = _resolve(SSE_OPTIONS_INTRADAY_DIR)
SSE_OPTIONS_PRICE_DIR = _resolve(SSE_OPTIONS_PRICE_DIR)
SSE_TREND_DIR = _resolve(SSE_TREND_DIR)


# ============================================================================
# Source frames
# ============================================================================
def build_quotes_df(files, verbose=True):
    """EOD quotes: LAST raw sample per contract from each daily archive.

    Each file is one trading day's appended snapshots (raw cumulative
    volume/amount); after close the last sample per contract is the day's
    final state — close = ``last``, day volume = ``volume`` (cumulative at
    EOD), prev_settle = ``prev_settle``, pct_change = ``chg_rate``.
    """
    if verbose:
        logger.info(f"    [OPTIONS] reading {len(files)} sse_options_intraday_*.csv files")

    frames: list[pd.DataFrame] = []
    n_empty = 0
    n_ok = 0

    for path in files:
        ymd = ymd_from_filename(path, "sse_options_intraday_")
        if not ymd:
            continue
        try:
            df = read_csv_preferred(path, dtype={"code": str})
        except Exception:
            n_empty += 1
            continue
        if df is None or len(df) == 0 or "code" not in safe_columns(df):
            n_empty += 1
            continue

        date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        trade_date_dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")

        # Last sample per contract = EOD state (file appends in poll order)
        df = df.sort_values("update_time", kind="stable")
        df = df.drop_duplicates(subset=["code"], keep="last")

        part = pd.DataFrame({
            "trade_code": df["code"].astype(str).str.strip(),
            "date": np.datetime64(trade_date_dt.date(), "ns"),
        })
        for src, dst in (
            ("last", "close"),
            ("prev_settle", "prev_settle"),
            ("chg_rate", "pct_change"),
            ("volume", "volume"),
        ):
            part[dst] = safe_to_numeric(df[src]) if src in safe_columns(df) else np.nan
        frames.append(part)
        n_ok += 1

    if not frames:
        if verbose:
            logger.info(f"    [OPTIONS] {n_ok} files with data, {n_empty} empty, 0 rows")
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    if verbose:
        logger.info(f"    [OPTIONS] {n_ok} files with data, {n_empty} empty, {len(out):,} EOD quote rows")
    return out


def build_contract_meta_df(files, verbose=True):
    """Contract listing (当日合约): numeric code, type, strike, expiry, underlying.

    Uses the authoritative columns directly (类型 认购/认沽, 行权价, 到期日,
    标的券名称及代码) — SSE 合约简称 patterns (50ETF/科创50/科创板50…) are not
    name-parseable the way SZSE's are.
    """
    if verbose:
        logger.info(f"    [CONTRACTS] reading {len(files)} sse_options_price_*.csv files")

    frames: list[pd.DataFrame] = []
    n_empty = 0

    for path in files:
        ymd = ymd_from_filename(path, "sse_options_price_")
        if not ymd:
            continue
        try:
            df = read_csv_preferred(
                path,
                dtype={"合约编码": str, "合约交易代码": str, "合约简称": str, "标的券名称及代码": str},
            )
        except Exception:
            n_empty += 1
            continue
        if df is None or len(df) == 0 or "合约交易代码" not in safe_columns(df):
            n_empty += 1
            continue

        date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        trade_date_dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")

        under = df["标的券名称及代码"].astype(str).str.strip()
        # "50ETF(510050)" / "科创50(588000)" → name + bare 6-digit code
        under_name = under.str.replace(r"\(\d{6}\)$", "", regex=True)
        under_code = under.str.extract(r"\((\d{6})\)", expand=False)

        part = pd.DataFrame({
            "date": np.datetime64(trade_date_dt.date(), "ns"),
            "contract_code": df["合约编码"].astype(str).str.strip(),
            "contract_name": df["合约简称"].astype(str).str.strip(),
            "trade_code": df["合约交易代码"].astype(str).str.strip(),
            # 认购 = right to buy → CALL; 认沽 → PUT
            "option_type": (df["类型"].astype(str).str.strip().eq("认购")
                            .map({True: "CALL", False: "PUT"})),
            "underlying_name": under_name,
            "underlying_code": under_code,
        })
        # strike in 厘 (×1000 yuan) — same ×1000 convention as options_strike
        part["strike_price_raw"] = (safe_to_numeric(df["行权价"]) * 1000.0).round(2)
        part["strike_price"] = part["strike_price_raw"]
        # contractid series marker at index 11 ('M' standard, 'A' adjusted)
        tc = part["trade_code"]
        part["has_a_suffix"] = tc.str.slice(11, 12).eq("A").astype(int)
        # 到期日 YYYYMMDD → datetime64 (authoritative expiry; no weekday math)
        part["expiry_date"] = pd.to_datetime(
            df["到期日"].astype(str).str.strip(), format="%Y%m%d", errors="coerce")
        frames.append(part)

    if not frames:
        if verbose:
            logger.info(f"    [CONTRACTS] 0 usable files ({n_empty} empty)")
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    out = out.dropna(subset=["expiry_date", "underlying_code", "contract_code", "strike_price_raw"])
    # strike_str display label ("2650" / "2850A") from the 厘 strike + series marker
    out["strike_str"] = out["strike_price_raw"].round(0).astype("int64").astype(str).str.cat(
        out["has_a_suffix"].map({1: "A", 0: ""}))
    if verbose:
        logger.info(f"    [CONTRACTS] {len(frames)} files with data, {n_empty} empty, {len(out):,} contract rows")
    return out


def load_etf_close(files, verbose=True):
    """Underlying ETF closes (今收) from sse_trend_etf_*.csv → moneyness."""
    if verbose:
        logger.info(f"    [ETF-CLOSE] reading {len(files)} sse_trend_etf_*.csv files")

    parts: list = []
    for path in files:
        ymd = ymd_from_filename(path, "sse_trend_etf_")
        if not ymd:
            continue
        try:
            df = read_csv_preferred(path, dtype={"证券代码": str})
        except Exception:
            continue
        if df is None or len(df) == 0 or "今收" not in safe_columns(df):
            continue
        date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        code_s = df["证券代码"].astype(str).str.strip().str.replace(".SS", "", regex=False)
        close_s = safe_to_numeric(df["今收"])
        m = (code_s.str.fullmatch(r"\d{6}") & close_s.notna()).to_numpy()
        if not bool(m.any()):
            continue
        part = pd.DataFrame({"etf_code": code_s[m]})
        part["date"] = date_str
        part["etf_close"] = close_s[m].astype(float)
        parts.append(part)

    if not parts:
        if verbose:
            logger.warning("    [WARN] No ETF close data loaded — moneyness will be 0")
        return pd.DataFrame()

    out = pd.concat(parts, ignore_index=True)
    out["date"] = safe_to_datetime(out["date"]).astype("datetime64[ns]")
    out = out.dropna(subset=["date"]).sort_values(["etf_code", "date"]).reset_index(drop=True)
    if verbose:
        logger.info(f"    → {len(out):,} ETF close rows  ·  {out['etf_code'].nunique()} ETFs")
    return out


# ============================================================================
# Derived columns (mirrors builds.options.szse.add_derived_columns; SSE gaps:
# settle NULL → IV priced off close; no per-contract OI → OI aggregates 0)
# ============================================================================
from _common.df_utils import compute_iv_and_greeks  # noqa: E402


def add_derived_columns(df, etf_close=None, verbose=True):
    """Add normalized prices, moneyness, per-underlying aggregates, IV, Greeks."""
    if df is None or len(df) == 0:
        return df

    df = df.copy()
    df["_date_key"] = df["date"].dt.strftime("%Y-%m-%d")

    df["settle"] = np.nan  # SSE settles publish with a lag — NULL for now
    df["volume_wan"] = df["volume"] / 10000.0
    # no per-contract OI source on SSE — 0, not NULL (options_volume_oi's
    # open_interest is NOT NULL; 0 keeps the OI aggregates consistently 0)
    df["open_interest"] = 0.0
    df["open_interest_wan"] = 0.0

    df["prev_settle_norm"] = df["prev_settle"] / 100.0
    df["close_norm"] = df["close"] / 100.0
    df["settle_norm"] = df["settle"] / 100.0

    if etf_close is not None and len(etf_close) > 0:
        etf = etf_close.copy()
        etf["_date_key"] = etf["date"].dt.strftime("%Y-%m-%d")
        df = df.merge(etf[["_date_key", "etf_code", "etf_close"]],
                      left_on=["_date_key", "underlying_code"],
                      right_on=["_date_key", "etf_code"], how="left")
        uc = df["etf_close"].fillna(0.0)
        # strikes live on the exchange's ×1000 (厘) naming scale — rescale the
        # yuan ETF close (<50) onto it so moneyness / IV are meaningful
        df["underlying_close"] = np.where((uc > 0) & (uc < 50), uc * 1000.0, uc)
        df["moneyness_ratio"] = np.where(df["underlying_close"] > 0,
                                         df["strike_price"] / df["underlying_close"], 0.0)
    else:
        df["underlying_close"] = 0.0
        df["moneyness_ratio"] = 0.0

    # days_to_expiry via epoch ints (cudf-safe; no .dt.date object column)
    date_epoch = df["date"].astype("datetime64[ns]").astype("int64") // 86_400_000_000_000
    exp_epoch = df["expiry_date"].astype("datetime64[ns]").astype("int64") // 86_400_000_000_000
    df["days_to_expiry"] = (exp_epoch - date_epoch).clip(lower=0)

    # expiry_month display label ("9月" / "12月") — month formatting on the
    # tiny unique-expiry frame host-side, merged back on an INT epoch-day key
    # (datetime-key merges between host and cudf-backed frames trip the
    # "all inputs must be Index" concat bug — same reason _date_key exists)
    df["_exp_day"] = exp_epoch
    uniq_exp = df[["_exp_day"]].drop_duplicates()
    if hasattr(uniq_exp, "to_pandas"):
        uniq_exp = uniq_exp.to_pandas()
    # host-side month labels from epoch-day ints (Arrow-backed setitem on the
    # to_pandas() frame breaks; the unique set is ≤ a handful of dates)
    _label_by_day = {
        int(d): f"{(datetime.date(1970, 1, 1) + datetime.timedelta(days=int(d))).month}月"
        for d in np.asarray(uniq_exp["_exp_day"]).tolist()
    }
    uniq_exp = uniq_exp.assign(
        expiry_month=[_label_by_day[int(d)] for d in np.asarray(uniq_exp["_exp_day"]).tolist()]
    )
    df = df.merge(uniq_exp, on="_exp_day", how="left")

    grouped = df.groupby(["_date_key", "underlying_code"])

    df["total_volume_underlying"] = grouped["volume"].transform("sum")
    df["total_oi_underlying"] = grouped["open_interest"].transform("sum").fillna(0.0)

    df["volume_pct"] = np.where(df["total_volume_underlying"] > 0,
                                df["volume"] / df["total_volume_underlying"] * 100.0, 0.0)
    df["open_interest_pct"] = 0.0  # no OI source

    _grp_keys = ["_date_key", "underlying_code", "strike_price"]
    call_vol = df[df["option_type"] == "CALL"].groupby(_grp_keys)["volume"].sum().reset_index().rename(columns={"volume": "call_vol"})
    put_vol = df[df["option_type"] == "PUT"].groupby(_grp_keys)["volume"].sum().reset_index().rename(columns={"volume": "put_vol"})
    ratios = pd.merge(call_vol, put_vol, on=_grp_keys, how="outer")
    ratios["vol_call_put_ratio"] = np.where(ratios["put_vol"] > 0, ratios["call_vol"] / ratios["put_vol"], np.nan)
    ratios["oi_call_put_ratio"] = np.nan  # no OI source

    df = df.merge(ratios[["_date_key", "underlying_code", "strike_price",
                          "oi_call_put_ratio", "vol_call_put_ratio"]],
                  on=_grp_keys, how="left")
    df["oi_call_put_ratio"] = df["oi_call_put_ratio"].fillna(0.0)
    df["vol_call_put_ratio"] = df["vol_call_put_ratio"].fillna(0.0)

    df["open_interest_call"] = 0.0
    df["open_interest_put"] = 0.0
    df["volume_call"] = np.where(df["option_type"] == "CALL", df["volume"], 0.0)
    df["volume_put"] = np.where(df["option_type"] == "PUT", df["volume"], 0.0)
    df["oi_total_call_put_ratio"] = 0.0  # no OI source

    if verbose:
        logger.info(f"    → Computing implied volatility and Greeks for {len(df):,} rows …")

    # IV calibration price: settle when published, else close (SSE settle lag)
    iv_frame = df.assign(settle=df["settle"].fillna(df["close"]))
    iv, delta, theta, gamma, vega, rho = compute_iv_and_greeks(iv_frame)
    df["implied_vol"] = iv
    df["delta"] = delta
    df["theta"] = theta
    df["gamma"] = gamma
    df["vega"] = vega
    df["rho"] = rho

    df = df.round({
        "prev_settle_norm": 4, "close_norm": 4, "settle_norm": 4,
        "volume_wan": 4,
        "volume_pct": 4,
        "oi_call_put_ratio": 4, "vol_call_put_ratio": 4,
        "oi_total_call_put_ratio": 4,
        "implied_vol": 4, "delta": 6, "theta": 6, "gamma": 6, "vega": 6, "rho": 6,
    })

    if verbose:
        logger.info("    → Derived columns added: moneyness, ratios, normalized prices, IV, Greeks")

    return df


# ============================================================================
# Missing-date detection (exchange column on every options_* table)
# ============================================================================
async def find_missing_sse_dates(
    conn,
    source_dates: set,
    code_filter: str | None = None,
) -> set:
    """Dates from source_dates not yet built for exchange='SSE'.

    Probes stats.options_terms — NOT options_identity: the SSE options
    stream writes identity rows itself (FK parent for intraday bars), so
    identity-based checks would mask dates whose 7-table build is missing.
    """
    if not source_dates:
        return set()

    if code_filter:
        sql = (
            'SELECT DISTINCT date FROM stats.options_terms '
            "WHERE underlying_code = $1 AND exchange = 'SSE'"
        )
        existing_rows = await conn.fetch(sql, code_filter)
    else:
        sql = "SELECT DISTINCT date FROM stats.options_terms WHERE exchange = 'SSE'"
        existing_rows = await conn.fetch(sql)
    existing_dates = {r["date"] for r in existing_rows if r["date"] is not None}

    return source_dates - existing_dates


# ============================================================================
# --date mode writer
# ============================================================================
async def upsert_split_tables_date_mode(conn, tables) -> None:
    """ON CONFLICT (date, contract_code) DO UPDATE upsert, FK parent first."""
    for tbl, rows in tables.items():
        if not rows:
            logger.info(f"    [DB] No rows to upsert into {tbl}")
            continue
        n = await bulk_upsert_async(conn, tbl, rows, key_columns=["date", "contract_code"])
        logger.info(f"    [DB] Upserted {n:,} rows into {tbl}")


# ============================================================================
# EOD-finality check (quotes CSV reached the 15:00 snapshot)
# ============================================================================
def _quotes_file_is_final(path) -> bool:
    """True if the day's raw-snapshot CSV's last update_time is >= 15:00."""
    try:
        df = read_csv_preferred(path, usecols=["update_time"])
    except Exception:
        return False
    if df is None or len(df) == 0:
        return False
    max_t = str(df["update_time"].max())
    try:
        return max_t[11:13] >= "15"
    except (IndexError, TypeError):
        return False


# ============================================================================
# Main pipeline — SseOptionsBuild entry class
# ============================================================================
class SseOptionsBuild(DataBuild):
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
            "Quotes dir":  SSE_OPTIONS_INTRADAY_DIR,
            "Date range":  f"{self.args.start_date or '(all)'} → {self.args.end_date or '(all)'}",
            "Code filter": self.code_filter or "(none — all underlyings)",
            "Today":       TODAY_STR,
        }

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
        quote_files = glob_source_files(SSE_OPTIONS_INTRADAY_DIR, "sse_options_intraday_*.csv")
        meta_files = glob_source_files(SSE_OPTIONS_PRICE_DIR, "sse_options_price_*.csv")
        etf_files = glob_source_files(SSE_TREND_DIR, "sse_trend_etf_*.csv")
        logger.info(f"    → {len(quote_files)} quote files, {len(meta_files)} contract-listing files, "
              f"{len(etf_files)} ETF close files available")

        if not quote_files or not meta_files:
            logger.error("    [FATAL] No quote or contract-listing source CSVs found")
            sys.exit(1)

        # available = dates with BOTH a final (>=15:00) quotes file and a
        # same-day contract listing; ETF closes are optional (moneyness→0)
        meta_ymds = {ymd_from_filename(f, "sse_options_price_") for f in meta_files} - {None}
        available_dates = set()
        n_not_final = 0
        for f in quote_files:
            ymd = ymd_from_filename(f, "sse_options_intraday_")
            if not ymd or ymd not in meta_ymds:
                continue
            if not _quotes_file_is_final(f):
                # --date rebuild accepts a non-final quotes file: a day cut
                # short by a streamer crash will never gain a 15:00 sample,
                # so its last snapshot is the best EOD that date will ever
                # have, and the terms enrichment (the part the intraday
                # skewness estimator reads the prev-day snapshot for) comes
                # from the same-day contract listing, not the quotes. The
                # regular missing-data pass keeps requiring finality.
                if not (forced_date is not None
                        and ymd == forced_date.strftime("%Y%m%d")):
                    n_not_final += 1
                    continue
                logger.info(
                    f"    → {ymd}: non-final quotes file accepted for "
                    f"--date rebuild (last snapshot wins)"
                )
            d = ymd_to_date(ymd)
            if d:
                available_dates.add(d)

        if args.start_date:
            start_d = parse_date(args.start_date)
            if start_d:
                available_dates = {d for d in available_dates if d >= start_d}
        if args.end_date:
            end_d = parse_date(args.end_date)
            if end_d:
                available_dates = {d for d in available_dates if d <= end_d}

        logger.info(f"    → {len(available_dates)} unique final dates available in range "
              f"({n_not_final} non-final quote files skipped)")

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
                missing_dates = await find_missing_sse_dates(conn, available_dates, code_filter=code_filter)

            logger.info(f"    [DB] {len(missing_dates)} dates missing from stats.options_identity "
                  f"(out of {len(available_dates)} available)")

            if not missing_dates:
                logger.info("    [INFO] Database is up to date — no new dates to insert")
                return

            # ------------------------------------------------------------------
            # 3. Read only missing-date source files and build the options frame
            # ------------------------------------------------------------------
            logger.info(f"\n[3/4] Reading source CSVs for {len(missing_dates)} missing dates …")
            missing_ymd = {d.strftime("%Y%m%d") for d in missing_dates}

            missing_quote_files = [
                f for f in quote_files
                if ymd_from_filename(f, "sse_options_intraday_") in missing_ymd
            ]
            missing_meta_files = [
                f for f in meta_files
                if ymd_from_filename(f, "sse_options_price_") in missing_ymd
            ]
            missing_etf_files = [
                f for f in etf_files
                if ymd_from_filename(f, "sse_trend_etf_") in missing_ymd
            ]
            logger.info(f"    → {len(missing_quote_files)} quote files, {len(missing_meta_files)} "
                  f"contract-listing files, {len(missing_etf_files)} ETF close files to read")

            quotes_df = build_quotes_df(missing_quote_files)
            meta_df = build_contract_meta_df(missing_meta_files)
            if len(quotes_df) == 0 or len(meta_df) == 0:
                logger.info("    [INFO] No rows parsed from missing-date files")
                return

            options_df = quotes_df.merge(meta_df, on=["date", "trade_code"], how="inner")
            n_dropped = len(quotes_df) - len(options_df)
            if n_dropped:
                logger.info(f"    [OPTIONS] {n_dropped} quote rows without a same-day contract listing — dropped")
            if len(options_df) == 0:
                logger.info("    [INFO] No joinable option rows")
                return

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
            options_df = add_derived_columns(options_df, etf_close)

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
            await upsert_split_tables_date_mode(conn, tables)

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
         
