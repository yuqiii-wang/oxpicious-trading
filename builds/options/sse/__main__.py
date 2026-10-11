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
import os
import sys

import numpy as np
import pandas as pd

from downloads._common import read_csv_preferred
from downloads._common.exchanges.sse_options import OPTIONS_CSV_COLUMNS
from _common.df_utils import safe_columns, grouped_shift
from _common.labels import OPTION_TYPE_CODE

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
SSE_OPTIONS_DAILY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "temps", "sse_options_daily")
SSE_OPTIONS_INTRADAY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "temps", "sse_options_intraday")
SSE_OPTIONS_PRICE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "temps", "sse_options_price")
SSE_OPTIONS_RISK_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "temps", "sse_options_risk")
SSE_TREND_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "temps", "sse_trend")
SSE_ETF_ARCHIVE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "temps", "sse_archive")

logger = setup_logging("sse_options")


def _resolve(path: str) -> str:
    return os.path.normpath(os.path.abspath(path))


SSE_OPTIONS_DAILY_DIR = _resolve(SSE_OPTIONS_DAILY_DIR)
SSE_OPTIONS_PRICE_DIR = _resolve(SSE_OPTIONS_PRICE_DIR)
SSE_OPTIONS_RISK_DIR = _resolve(SSE_OPTIONS_RISK_DIR)
SSE_TREND_DIR = _resolve(SSE_TREND_DIR)
SSE_ETF_ARCHIVE_DIR = _resolve(SSE_ETF_ARCHIVE_DIR)

# The 5 SSE option underlying ETFs — the per-code dayk close archives under
# temps/sse_archive/ use these bare codes as filenames.
SSE_OPTIONS_UNDERLYING_CODES = ("510050", "510300", "510500", "588000", "588080")


# ============================================================================
# Source frames
# ============================================================================
def backfill_daily_from_intraday(build_ymd, verbose=True):
    """Materialize missing daily CSVs from the intraday raw archives.

    Backfill fix for build dates whose EOD capture never landed a
    sse_options_daily file: the intraday archive's LAST sample per
    contract already holds the day-cumulative 成交量/成交额 at that moment,
    so one dedup pass writes the day's daily row. No external requests —
    intraday aggregation is the only backfill source. Partial-day archives
    (stream died pre-close) yield partial totals; the written
    ``update_time`` keeps that visible.
    """
    made = []
    for ymd in sorted(build_ymd):
        daily_path = os.path.join(SSE_OPTIONS_DAILY_DIR, f"sse_options_daily_{ymd}.csv")
        if os.path.exists(daily_path):
            continue
        intraday_path = os.path.join(SSE_OPTIONS_INTRADAY_DIR, f"sse_options_intraday_{ymd}.csv")
        if not os.path.exists(intraday_path):
            continue
        try:
            df = read_csv_preferred(intraday_path, dtype={"code": str})
        except Exception:
            continue
        if df is None or len(df) == 0 or "code" not in safe_columns(df):
            continue
        df = df.sort_values("update_time", kind="stable")
        df = df.drop_duplicates(subset=["code"], keep="last")
        cols = [c for c in OPTIONS_CSV_COLUMNS if c in safe_columns(df)]
        df = df[cols]
        # cuDF's CSV writer emits full float64 reprs (0.09899999999999999) —
        # round the numeric legs back to quoted precision for clean files
        for c in ("open", "high", "low", "last", "prev_settle", "chg_rate",
                  "volume", "amount"):
            if c in safe_columns(df):
                df[c] = df[c].round(6)
        df.to_csv(daily_path, index=False, encoding="utf-8-sig")
        made.append(ymd)
        if verbose:
            logger.info(f"    [BACKFILL] {ymd}: daily CSV reconstructed from intraday — "
                  f"{len(df)} contracts, last sample {df['update_time'].max()}")
    return made


def build_quotes_df(files, verbose=True):
    """EOD quotes: the day's DAILY snapshot rows (price page, post-close).

    Each file is one trading day's EOD capture (day OHLC + day-total
    成交量(手)/成交额(元), raw cumulative values); a re-run capture appends
    duplicate rows, so the LAST sample per contract wins. close = ``last``,
    day volume = ``volume`` (张), amount = ``amount`` (元),
    prev_settle = ``prev_settle``, pct_change = ``chg_rate``.
    """
    if verbose:
        logger.info(f"    [OPTIONS] reading {len(files)} sse_options_daily_*.csv files")

    frames: list[pd.DataFrame] = []
    n_empty = 0
    n_ok = 0

    for path in files:
        ymd = ymd_from_filename(path, "sse_options_daily_")
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

        # Last sample per contract = EOD state (idempotent re-captures append)
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
            ("amount", "amount"),
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
            # 认购 = right to buy → CALL; 认沽 → PUT — stored as the
            # stats.label_dim 'option_type' SMALLINT code (0 CALL / 1 PUT)
            "option_type": np.where(
                df["类型"].astype(str).str.strip().eq("认购"),
                OPTION_TYPE_CODE["CALL"], OPTION_TYPE_CODE["PUT"],
            ),
            "underlying_name": under_name,
            "underlying_code": under_code,
        })
        # strike in 厘 (×1000 yuan) — same ×1000 convention as options_strike
        part["strike_price_raw"] = (safe_to_numeric(df["行权价"]) * 1000.0).round(2)
        part["strike_price"] = part["strike_price_raw"]
        # contractid series marker at index 11 ('M' standard, 'A' adjusted)
        tc = part["trade_code"]
        part["has_a_suffix"] = tc.str.slice(11, 12).eq("A").astype(int)
        # 前结算价 (previous settlement, yuan) — the listing's own price
        # column; feeds listing-only quotes and the settle(T+1) recovery
        part["prev_settle"] = (safe_to_numeric(df["前结算价"])
                               if "前结算价" in safe_columns(df) else np.nan)
        # 到期日 YYYYMMDD → datetime64 (authoritative expiry; no weekday math)
        # Clean-data fast path — to_datetime(errors="coerce") on a Series
        # is a cudf fallback per call (non-scalar arg); coerce only on junk.
        expiry_str = df["到期日"].astype(str).str.strip()
        try:
            part["expiry_date"] = pd.to_datetime(expiry_str, format="%Y%m%d")
        except (ValueError, TypeError):
            part["expiry_date"] = pd.to_datetime(
                expiry_str, format="%Y%m%d", errors="coerce")
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


def build_settle_map(meta_df, verbose=True):
    """Recover official settle(T) from the next listing date's 前结算价.

    The 当日合约 listing carries 前结算价 = the PREVIOUS trading day's
    settlement, so within one contract's listing-date sequence the row at
    T+1 holds date T's official settle (SSE publishes settlement with a
    one-day lag — the same fact that makes the intraday ``prev_settle``
    and the listing column agree). Contracts absent from every later
    listing (expiry-day drops, delisted) keep settle = NaN.

    ``meta_df`` must span the build dates AND all later listing files so
    the look-ahead row exists; only (date, contract_code, settle) comes
    back, left-merged onto the options frame by the caller.
    """
    if meta_df is None or len(meta_df) == 0 or "prev_settle" not in safe_columns(meta_df):
        return pd.DataFrame()

    keys = meta_df[["contract_code", "date", "prev_settle"]].sort_values(
        ["contract_code", "date"], kind="stable")
    # sort=False: already sorted on the FULL key above — grouped_shift's own
    # group-key-only sort would not preserve within-group date order
    keys = grouped_shift(keys, ["contract_code"], "prev_settle",
                         out_names="settle", periods=-1, sort=False)
    out = keys[["date", "contract_code", "settle"]]
    if verbose:
        n = int(out["settle"].notna().sum())
        logger.info(f"    [SETTLE] next-listing settle recovered for {n:,}/{len(out):,} contract-days")
    return out


def build_listing_only_quotes(meta_df, ymd_set, verbose=True):
    """Settle-only EOD rows for build dates lacking a final quotes file.

    The intraday stream is younger than the 当日合约 listing and crashes
    can cut a day before 15:00, so those dates build from the listing
    alone: full terms/strike/identity, ``prev_settle`` from the listing's
    前结算价, settle via ``build_settle_map``. close/pct_change stay NaN
    (no source) and volume is 0 — IV prices off settle.
    """
    if meta_df is None or len(meta_df) == 0 or not ymd_set:
        return pd.DataFrame()

    ymd_key = meta_df["date"].dt.strftime("%Y%m%d")
    lo = meta_df[ymd_key.isin(ymd_set)]
    if len(lo) == 0:
        return pd.DataFrame()

    part = pd.DataFrame({
        "trade_code": lo["trade_code"].astype(str).str.strip(),
        "date": lo["date"],
        "close": np.nan,
        "prev_settle": lo["prev_settle"],
        "pct_change": np.nan,
        "volume": 0.0,
    })
    if verbose:
        n_days = int(lo["date"].dt.strftime("%Y%m%d").nunique())
        logger.info(f"    [OPTIONS] {n_days} listing-only dates → {len(part):,} settle-only quote rows")
    return part


def _load_archive_etf_close():
    """Full-history closes for the option underlying ETFs from dayk archives.

    temps/sse_archive/{code}_trend.csv (downloads.stock.sse.archive, yunhq
    ``sh1/dayk`` endpoint) carries each security's complete immutable daily
    history — used to fill (etf_code, date) pairs the sse_trend_etf daily
    snapshots don't cover. Dates stay strings so the shared tail's single
    safe_to_datetime parse handles snapshot + archive rows alike.
    """
    parts: list = []
    for code in SSE_OPTIONS_UNDERLYING_CODES:
        path = os.path.join(SSE_ETF_ARCHIVE_DIR, f"{code}_trend.csv")
        if not os.path.exists(path):
            continue
        try:
            df = read_csv_preferred(path, dtype={"证券代码": str})
        except Exception:
            continue
        if df is None or len(df) == 0 or "今收" not in safe_columns(df):
            continue
        code_s = df["证券代码"].astype(str).str.strip().str.replace(".SS", "", regex=False)
        close_s = safe_to_numeric(df["今收"])
        date_s = df["交易日期"].astype(str).str.strip()
        m = (code_s.str.fullmatch(r"\d{6}") & close_s.notna()).to_numpy()
        if not bool(m.any()):
            continue
        part = pd.DataFrame({"etf_code": code_s[m]})
        part["date"] = date_s[m]
        part["etf_close"] = close_s[m].astype(float)
        parts.append(part)
    return parts


def load_etf_close(files, verbose=True):
    """Underlying ETF closes (今收) → moneyness.

    Primary: sse_trend_etf_*.csv same-day snapshots. The per-code dayk
    archives fill any (etf_code, date) pair the snapshots lack — the
    snapshot downloader started 2026-07-31, the options listings
    2026-07-17, and moneyness/IV need the spot on every build date.
    """
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

    parts.extend(_load_archive_etf_close())

    if not parts:
        if verbose:
            logger.warning("    [WARN] No ETF close data loaded — moneyness will be 0")
        return pd.DataFrame()

    out = pd.concat(parts, ignore_index=True)
    out["date"] = safe_to_datetime(out["date"]).astype("datetime64[ns]")
    # archive rows only fill gaps — the same-day snapshot wins on collision
    out = out.drop_duplicates(subset=["etf_code", "date"], keep="first")
    out = out.dropna(subset=["date"]).sort_values(["etf_code", "date"]).reset_index(drop=True)
    if verbose:
        logger.info(f"    → {len(out):,} ETF close rows  ·  {out['etf_code'].nunique()} ETFs")
    return out


# ============================================================================
# Exchange risk-indicator table (期权风险指标) — published greeks loaded
# DIRECTLY, never recomputed (see add_derived_columns)
# ============================================================================
# downloadRisk.do emits comma-separated fields padded with tabs/spaces and a
# trailing separator per row — read positional, then strip every field.
_RISK_SOURCE_COLS = ["日期", "合约编码", "交易代码", "合约简称",
                     "Delta", "Theta", "Gamma", "Vega", "Rho"]
_RISK_COLS = ["日期", "合约编码", "Delta", "Theta", "Gamma", "Vega", "Rho"]


def load_risk_indicators(files, verbose=True):
    """Per-contract published greeks from sse_options_risk_*.csv.

    Returns [date, contract_code, r_delta, r_theta, r_gamma, r_vega, r_rho]
    keyed by the CSV's own 日期/合约编码 (authoritative; the numeric
    合约编码 matches options_identity.contract_code).
    """
    if verbose:
        logger.info(f"    [RISK] reading {len(files)} sse_options_risk_*.csv files")

    frames: list[pd.DataFrame] = []
    n_empty = 0
    for path in files:
        try:
            df = read_csv_preferred(
                path,
                header=None, skiprows=1,
                names=_RISK_SOURCE_COLS + ["_pad"], usecols=_RISK_COLS,
                dtype={"合约编码": str},
            )
        except Exception:
            n_empty += 1
            continue
        if df is None or len(df) == 0:
            n_empty += 1
            continue

        date_s = df["日期"].astype(str).str.strip().str.replace("-", "", regex=False)
        code_s = df["合约编码"].astype(str).str.strip()
        keep = code_s.str.fullmatch(r"\d+")
        part = pd.DataFrame({
            "date": pd.to_datetime(date_s[keep], format="%Y%m%d", errors="coerce"),
            "contract_code": code_s[keep],
        })
        # normalize to the [ns] unit the options frame's date column uses —
        # datetime-key merges across [us]/[ns] units do not match
        part["date"] = part["date"].astype("datetime64[ns]")
        for col in ("Delta", "Theta", "Gamma", "Vega", "Rho"):
            part["r_" + col.lower()] = safe_to_numeric(df[col].astype(str).str.strip())
        part = part.dropna(subset=["date"])
        frames.append(part)

    if not frames:
        if verbose:
            logger.info(f"    [RISK] 0 usable files ({n_empty} empty) — greeks fall back to BS pipeline")
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["date", "contract_code"], keep="last")
    if verbose:
        logger.info(f"    [RISK] {len(frames)} files with data, {n_empty} empty, {len(out):,} published greek rows")
    return out


# ============================================================================
# Accumulated-volume OI chain — SSE's open_interest estimator (no published
# per-contract OI; volume only exists in the intraday captures)
# ============================================================================
async def fetch_prev_oi_seed(conn, before_date, code_filter=None) -> pd.DataFrame:
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


def chain_open_interest(df, seed_df=None, verbose=True):
    """open_interest(T) = open_interest(prev trading row) + volume(T).

    SSE publishes no per-contract OI, so the accepted approximation is the
    per-contract accumulated VOLUME (the intraday stream is the only
    volume source — the same upper-bound estimator
    ``live.options_intraday_oi`` chains from this column as oi_base).
    Within the build window this is one grouped cumsum over volume seeded
    with each contract's latest stored OI before the window; listing-only
    dates (volume 0) carry the level forward, contracts past expiry keep
    their frozen value only on rows that still exist for them.
    """
    if df is None or len(df) == 0:
        return df

    work = df[["contract_code", "date", "volume"]].sort_values(
        ["contract_code", "date"], kind="stable")
    if seed_df is not None and len(seed_df):
        work = work.merge(seed_df, on="contract_code", how="left")
    else:
        work = work.assign(open_interest=np.nan)
    # seed is constant per contract: seed + cumsum(volume) adds it exactly
    # once at the chain head of every row's running total; volume NaN
    # (no quote leg) counts as 0 — the level carries forward
    work["volume"] = work["volume"].fillna(0.0)
    seed = work["open_interest"].fillna(0.0)
    work["_oi"] = seed + work.groupby("contract_code", sort=False)["volume"].cumsum()

    if verbose:
        n_pos = int((work["_oi"] > 0).sum())
        logger.info(f"    [OI-CHAIN] accumulated-volume OI: {n_pos:,}/{len(work):,} "
              f"contract-days carry a positive level")
    return df.merge(
        work[["contract_code", "date", "_oi"]].rename(columns={"_oi": "open_interest"}),
        on=["contract_code", "date"], how="left")


# ============================================================================
# Derived columns (mirrors builds.options.szse.add_derived_columns; SSE gaps:
# settle NULL → IV priced off close; open_interest = accumulated volume)
# ============================================================================
from _common.df_utils import compute_iv_and_greeks  # noqa: E402


def add_derived_columns(df, etf_close=None, risk_df=None, verbose=True):
    """Add normalized prices, moneyness, per-underlying aggregates, IV, Greeks."""
    if df is None or len(df) == 0:
        return df

    df = df.copy()
    df["_date_key"] = df["date"].dt.strftime("%Y-%m-%d")

    # settle arrives from build_settle_map (next listing's 前结算价);
    # dates/contracts without a later listing keep NULL and price IV off
    # close below (the fillna order encodes that preference)
    if "settle" not in safe_columns(df):
        df["settle"] = np.nan
    df["volume_wan"] = df["volume"] / 10000.0
    # open_interest arrives chained (accumulated-volume estimator — see
    # chain_open_interest); the guard keeps direct add_derived callers
    # working. NOT NULL column → 0, never NaN.
    if "open_interest" not in safe_columns(df):
        df["open_interest"] = 0.0
    df["open_interest_wan"] = df["open_interest"] / 10000.0

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
    df["open_interest_pct"] = np.where(df["total_oi_underlying"] > 0,
                                       df["open_interest"] / df["total_oi_underlying"] * 100.0, 0.0)

    _grp_keys = ["_date_key", "underlying_code", "strike_price"]
    call_oi = df[df["option_type"] == OPTION_TYPE_CODE["CALL"]].groupby(_grp_keys)["open_interest"].sum().reset_index().rename(columns={"open_interest": "call_oi"})
    put_oi = df[df["option_type"] == OPTION_TYPE_CODE["PUT"]].groupby(_grp_keys)["open_interest"].sum().reset_index().rename(columns={"open_interest": "put_oi"})
    call_vol = df[df["option_type"] == OPTION_TYPE_CODE["CALL"]].groupby(_grp_keys)["volume"].sum().reset_index().rename(columns={"volume": "call_vol"})
    put_vol = df[df["option_type"] == OPTION_TYPE_CODE["PUT"]].groupby(_grp_keys)["volume"].sum().reset_index().rename(columns={"volume": "put_vol"})
    ratios = pd.merge(call_oi, put_oi, on=_grp_keys, how="outer")
    ratios = pd.merge(ratios, call_vol, on=_grp_keys, how="outer")
    ratios = pd.merge(ratios, put_vol, on=_grp_keys, how="outer")
    ratios["oi_call_put_ratio"] = np.where(ratios["put_oi"] > 0, ratios["call_oi"] / ratios["put_oi"], np.nan)
    ratios["vol_call_put_ratio"] = np.where(ratios["put_vol"] > 0, ratios["call_vol"] / ratios["put_vol"], np.nan)

    df = df.merge(ratios[["_date_key", "underlying_code", "strike_price",
                          "oi_call_put_ratio", "vol_call_put_ratio"]],
                  on=_grp_keys, how="left")
    df["oi_call_put_ratio"] = df["oi_call_put_ratio"].fillna(0.0)
    df["vol_call_put_ratio"] = df["vol_call_put_ratio"].fillna(0.0)

    df["open_interest_call"] = np.where(df["option_type"] == OPTION_TYPE_CODE["CALL"], df["open_interest"], 0.0)
    df["open_interest_put"] = np.where(df["option_type"] == OPTION_TYPE_CODE["PUT"], df["open_interest"], 0.0)
    df["volume_call"] = np.where(df["option_type"] == OPTION_TYPE_CODE["CALL"], df["volume"], 0.0)
    df["volume_put"] = np.where(df["option_type"] == OPTION_TYPE_CODE["PUT"], df["volume"], 0.0)
    call_oi_total = df.groupby(["_date_key", "underlying_code"])["open_interest_call"].transform("sum")
    put_oi_total = df.groupby(["_date_key", "underlying_code"])["open_interest_put"].transform("sum")
    df["oi_total_call_put_ratio"] = np.where(put_oi_total > 0, call_oi_total / put_oi_total, 0.0)

    if verbose:
        logger.info(f"    → Computing implied volatility and Greeks for {len(df):,} rows …")

    # IV calibration price: settle when recovered (official), else close
    # (settle publishes with a one-day lag — see build_settle_map)
    iv_frame = df.assign(settle=df["settle"].fillna(df["close"]))
    iv, delta, theta, gamma, vega, rho = compute_iv_and_greeks(iv_frame)
    df["implied_vol"] = iv
    df["delta"] = delta
    df["theta"] = theta
    df["gamma"] = gamma
    df["vega"] = vega
    df["rho"] = rho

    # Exchange-published risk indicators WIN over the model values — loaded
    # from the 期权风险指标 table directly, never recomputed. The BS values
    # above stay only as the fallback for dates/contracts the table lacks
    # (implied_vol has no published SSE source and stays model-computed).
    if risk_df is not None and len(risk_df) > 0:
        df = df.merge(risk_df, on=["date", "contract_code"], how="left")
        n_published = int(df["r_delta"].notna().sum())
        for pub, col in (
            ("r_delta", "delta"), ("r_theta", "theta"), ("r_gamma", "gamma"),
            ("r_vega", "vega"), ("r_rho", "rho"),
        ):
            df[col] = df[pub].fillna(df[col])
        df = df.drop(columns=["r_delta", "r_theta", "r_gamma", "r_vega", "r_rho"])
        if verbose:
            logger.info(f"    [RISK] published greeks loaded for {n_published:,}/{len(df):,} rows "
                        f"(rest: BS fallback / NULL)")

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
def _newest_listing_underlyings(meta_files) -> set:
    """Underlying codes of the newest 当日合约 file — the current SSE universe.

    Keeps missing-date detection honest about brand-new underlyings: a code
    with no options_terms rows at all would stay invisible to a
    table-derived universe.
    """
    newest = max(
        (f for f in meta_files
         if ymd_from_filename(f, "sse_options_price_")),
        key=lambda f: ymd_from_filename(f, "sse_options_price_") or "",
        default=None,
    )
    if newest is None:
        return set()
    try:
        df = read_csv_preferred(newest, dtype={"标的券名称及代码": str})
    except Exception:
        return set()
    if df is None or len(df) == 0 or "标的券名称及代码" not in safe_columns(df):
        return set()
    under = df["标的券名称及代码"].astype(str).str.strip()
    return set(under.str.extract(r"\((\d{6})\)", expand=False).dropna())
async def find_missing_sse_dates(
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


def _scan_final_quote_ymds(quote_files, meta_ymds, forced_date):
    """(final daily-file ymds, non-final count) over the CURRENT file set.

    Runs AFTER ``backfill_daily_from_intraday`` so regenerated daily files
    count as quote sources for their dates. --date rebuilds accept a
    non-final file (a day cut short by a streamer crash will never gain a
    15:00 sample); regular dates take the listing-only path instead
    (settle is official there; a partial close is not).
    """
    final = set()
    n_not_final = 0
    for f in quote_files:
        ymd = ymd_from_filename(f, "sse_options_daily_")
        if not ymd or ymd not in meta_ymds:
            continue
        if not _quotes_file_is_final(f):
            if not (forced_date is not None
                    and ymd == forced_date.strftime("%Y%m%d")):
                n_not_final += 1
                continue
            logger.info(
                f"    → {ymd}: non-final quotes file accepted for "
                f"--date rebuild (last snapshot wins)"
            )
        final.add(ymd)
    return final, n_not_final


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
            "Quotes dir":  SSE_OPTIONS_DAILY_DIR,
            "Date range":  f"{self.args.start_date or '(all)'} → {self.args.end_date or '(all)'}",
            "Code filter": self.code_filter or "(none — all underlyings)",
            "Today":       TODAY_STR,
        }

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
                missing_dates = await find_missing_sse_dates(
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
            oi_seed = await fetch_prev_oi_seed(conn, min(missing_dates))
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
         
