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
"""
import os, sys, re, time, argparse
import datetime

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import QuantLib as ql

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _download_commons import read_csv_preferred
from _build_commons import (
    setup_utf8_stdout, add_common_build_args, get_db_or_exit,
    parse_num, parse_date, ymd_from_filename, ymd_to_date,
    glob_source_files,
    print_build_header, print_wall_time, PROJECT_ROOT, TODAY_STR,
    bulk_upsert_async, truncate_table_async,
)

setup_utf8_stdout()

import asyncio

# ============================================================================
# Paths
# ============================================================================
SZSE_TREND_DIR = os.path.join(PROJECT_ROOT, "temps", "szse_trend")


# ============================================================================
# Contract name parsing
# ============================================================================
_MONTH_MAP = {
    "1月": "01", "2月": "02", "3月": "03", "4月": "04",
    "5月": "05", "6月": "06", "7月": "07", "8月": "08",
    "9月": "09", "10月": "10", "11月": "11", "12月": "12",
}

_RE_CONTRACT = re.compile(
    r"^(.+?ETF)([购沽])(\d+月)([\d]+[A]?)$"
)

_RE_UNDERLYING = re.compile(
    r"^(.+?ETF).*?\((\d{6})\)$"
)


def parse_contract_name(name):
    """Parse 合约简称 into structured components.

    Examples:
      "深证100ETF购12月2342A" → {underlying:"深证100ETF", type:"CALL", month:"12月", strike:"2342A"}
      "创业板ETF购12月1700"    → {underlying:"创业板ETF", type:"CALL", month:"12月", strike:"1700"}
      "沪深300ETF沽12月3500"   → {underlying:"沪深300ETF", type:"PUT", month:"12月", strike:"3500"}
    """
    m = _RE_CONTRACT.match(str(name))
    if not m:
        return None
    underlying = m.group(1)
    opt_type = "CALL" if m.group(2) == "购" else "PUT"
    expiry_month = m.group(3)
    strike_str = m.group(4)

    has_a_suffix = strike_str.endswith("A")
    strike_base = strike_str[:-1] if has_a_suffix else strike_str

    try:
        strike_price = float(strike_base)
    except ValueError:
        return None

    return {
        "underlying_name": underlying,
        "option_type": opt_type,
        "expiry_month": expiry_month,
        "strike_str": strike_str,
        "strike_price": strike_price,
        "has_a_suffix": has_a_suffix,
    }


def parse_underlying_code(underlying_str):
    """Parse 标的证券简称（代码）→ (name, code).

    Handles both old and new SZSE naming conventions:
      "深证100ETF(159901)"         → ("深证100ETF", "159901")     # old format
      "深证100ETF易方达(159901)"   → ("深证100ETF", "159901")     # new format with fund company
    """
    m = _RE_UNDERLYING.match(str(underlying_str))
    if not m:
        return None, None
    name = m.group(1) + "ETF"
    code = m.group(2)
    return name, code


def compute_expiry_date(trade_date, expiry_month):
    """Compute the expiration date for a given trade date and expiry month.

    SZSE ETF options expire on the third Friday of the expiry month.
    """
    month_num = int(_MONTH_MAP.get(expiry_month, expiry_month[:-1]))
    year = trade_date.year

    if month_num < trade_date.month:
        year += 1

    first_day = datetime.datetime(year, month_num, 1)
    first_friday = first_day + datetime.timedelta(days=(4 - first_day.weekday() + 7) % 7)
    third_friday = first_friday + datetime.timedelta(weeks=2)

    return third_friday


# ============================================================================
# Build options DataFrame from a given list of source files
# ============================================================================
def build_options_df(files, verbose=True):
    """Build a long options DataFrame from the given szse_trend_option_*.csv files.

    Args:
        files: list of CSV file paths (already filtered to missing dates by caller)
    """
    if verbose:
        print(f"    [OPTIONS] reading {len(files)} szse_trend_option_*.csv files", flush=True)

    rows = []
    n_empty = 0
    n_ok = 0
    n_parse_fail = 0

    for path in files:
        ymd = ymd_from_filename(path, "szse_trend_option_")
        if not ymd:
            continue

        try:
            df = read_csv_preferred(path, dtype={"合约编码": str, "合约简称": str, "标的证券简称（代码）": str})
        except Exception:
            continue

        if df is None or len(df) == 0:
            n_empty += 1
            continue

        first_cell = str(df.iloc[0, 0]) if len(df) else ""
        if "没有找到" in first_cell or "无数据" in first_cell:
            n_empty += 1
            continue

        if "合约简称" not in df.columns:
            continue

        date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        trade_date_dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")

        for _, r in df.iterrows():
            contract_name = str(r.get("合约简称", "")).strip()
            parsed = parse_contract_name(contract_name)

            if parsed is None:
                n_parse_fail += 1
                continue

            underlying_name, underlying_code = parse_underlying_code(r.get("标的证券简称（代码）", ""))

            if underlying_code is None:
                n_parse_fail += 1
                continue

            expiry_dt = compute_expiry_date(trade_date_dt, parsed["expiry_month"])
            days_to_expiry = max(0, (expiry_dt - trade_date_dt).days)

            # All strikes (A-suffix or not) are in the same unit (厘 = 0.001 yuan).
            # The "A" suffix indicates a contract series adjustment, not a different scale.
            strike_price_norm = parsed["strike_price"]

            rows.append({
                "date": date_str,
                "contract_code": str(r.get("合约编码", "")).strip(),
                "contract_name": contract_name,
                "underlying_name": parsed["underlying_name"],
                "underlying_code": underlying_code,
                "option_type": parsed["option_type"],
                "expiry_month": parsed["expiry_month"],
                "expiry_date": expiry_dt.strftime("%Y-%m-%d"),
                "days_to_expiry": days_to_expiry,
                "strike_str": parsed["strike_str"],
                "strike_price_raw": parsed["strike_price"],
                "strike_price": strike_price_norm,
                "has_a_suffix": int(parsed["has_a_suffix"]),
                "prev_settle": parse_num(r.get("前结算价")),
                "close": parse_num(r.get("今收盘价")),
                "settle": parse_num(r.get("今结算价")),
                "pct_change": parse_num(r.get("涨跌幅（%）")),
                "volume": parse_num(r.get("成交量（张）")),
                "open_interest": parse_num(r.get("未平仓量（张）")),
            })
        n_ok += 1

    if verbose:
        print(f"    [OPTIONS] {n_ok} files with data, {n_empty} empty, {n_parse_fail} parse failures, {len(rows)} rows", flush=True)

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows)
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["expiry_date"] = pd.to_datetime(out["expiry_date"], errors="coerce")
    out = out.dropna(subset=["date"]).sort_values(["underlying_code", "date", "expiry_month", "strike_price", "option_type"]).reset_index(drop=True)

    return out


# ============================================================================
# Load ETF OHLCV for moneyness calculation from a given list of files
# ============================================================================
def load_etf_ohlcv(files, verbose=True):
    """Load ETF OHLCV data for computing moneyness ratios from given files.

    Args:
        files: list of szse_trend_etf_*.csv file paths (already filtered to missing dates)
    """
    if verbose:
        print(f"    [ETF-OHLCV] reading {len(files)} szse_trend_etf_*.csv files", flush=True)

    rows = []
    for path in files:
        ymd = ymd_from_filename(path, "szse_trend_etf_")
        if not ymd:
            continue
        try:
            df = read_csv_preferred(path, dtype={"证券代码": str, "证券简称": str})
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue
        if "证券代码" not in df.columns or "今收" not in df.columns:
            continue
        date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        for _, r in df.iterrows():
            code = str(r.get("证券代码", "")).strip()
            code = code.replace(".SZ", "").replace(".SS", "")
            try:
                code = str(int(float(code))).zfill(6)
            except Exception:
                continue
            close = parse_num(r.get("今收"))
            rows.append({
                "date": date_str,
                "etf_code": code,
                "etf_close": close,
            })

    if not rows:
        if verbose:
            print("    [WARN] No ETF OHLCV data loaded for moneyness calculation", flush=True)
        return pd.DataFrame()

    out = pd.DataFrame(rows)
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date"]).sort_values(["etf_code", "date"]).reset_index(drop=True)

    if verbose:
        print(f"    → {len(out):,} ETF OHLCV rows  ·  {out['etf_code'].nunique()} ETFs", flush=True)

    return out


# ============================================================================
# Black-Scholes Greeks
# ============================================================================
RISK_FREE_RATE = 0.02
PRICE_SCALE = 1000.0
OPT_SCALE = 10000.0


def compute_iv_and_greeks(df):
    """Compute implied volatility and Greeks using QuantLib's BlackCalculator."""
    from scipy.optimize import brentq

    S = df["underlying_close"].values / PRICE_SCALE
    K = df["strike_price"].values / PRICE_SCALE
    price = df["settle"].values / OPT_SCALE
    T = df["days_to_expiry"].values / 365.0
    is_call = df["option_type"].values == "CALL"
    r = RISK_FREE_RATE

    mask = (T > 0) & (price > 0) & (S > 0) & (K > 0)

    iv = np.full(len(df), np.nan)
    delta = np.full(len(df), np.nan)
    gamma = np.full(len(df), np.nan)
    theta = np.full(len(df), np.nan)
    vega = np.full(len(df), np.nan)
    rho = np.full(len(df), np.nan)

    def compute_single(idx):
        if not mask[idx]:
            return np.nan, np.nan, np.nan, np.nan, np.nan, np.nan

        s = float(S[idx])
        k = float(K[idx])
        p = float(price[idx])
        t = float(T[idx])
        call = bool(is_call[idx])

        try:
            payoff_type = ql.Option.Call if call else ql.Option.Put
            payoff = ql.PlainVanillaPayoff(payoff_type, k)

            F = s * np.exp(r * t)
            discount = np.exp(-r * t)

            def objective(sigma):
                std_dev = sigma * np.sqrt(t)
                calc = ql.BlackCalculator(payoff, F, std_dev, discount)
                return calc.value() - p

            vol = brentq(objective, 1e-6, 5.0, xtol=1e-8, rtol=1e-8, maxiter=200)

            std_dev = vol * np.sqrt(t)
            calc = ql.BlackCalculator(payoff, F, std_dev, discount)

            d = calc.delta(s)
            g = calc.gamma(s)
            th = calc.thetaPerDay(s, t)
            ve = calc.vega(t) * 0.01
            rh = calc.rho(t) * 0.01

            return vol, d, th, g, ve, rh
        except Exception:
            return np.nan, np.nan, np.nan, np.nan, np.nan, np.nan

    for i in range(len(df)):
        iv[i], delta[i], theta[i], gamma[i], vega[i], rho[i] = compute_single(i)

    return iv, delta, theta, gamma, vega, rho


# ============================================================================
# Add derived columns
# ============================================================================
def add_derived_columns(df, etf_ohlcv=None, verbose=True):
    """Add derived columns for volatility analysis."""
    if df is None or len(df) == 0:
        return df

    df = df.copy()

    df["volume_wan"] = df["volume"] / 10000.0
    df["open_interest_wan"] = df["open_interest"] / 10000.0

    df["prev_settle_norm"] = df["prev_settle"] / 100.0
    df["close_norm"] = df["close"] / 100.0
    df["settle_norm"] = df["settle"] / 100.0

    if etf_ohlcv is not None and len(etf_ohlcv) > 0:
        df_merged = df.merge(etf_ohlcv, left_on=["date", "underlying_code"],
                            right_on=["date", "etf_code"], how="left")
        df["underlying_close"] = df_merged["etf_close"].fillna(0.0)
        df["moneyness_ratio"] = np.where(df["underlying_close"] > 0,
                                         df["strike_price"] / df["underlying_close"], 0.0)
    else:
        df["underlying_close"] = 0.0
        df["moneyness_ratio"] = 0.0

    grouped = df.groupby(["date", "underlying_code"])

    df["total_volume_underlying"] = grouped["volume"].transform("sum")
    df["total_oi_underlying"] = grouped["open_interest"].transform("sum")

    df["volume_pct"] = np.where(df["total_volume_underlying"] > 0,
                                df["volume"] / df["total_volume_underlying"] * 100.0, 0.0)
    df["open_interest_pct"] = np.where(df["total_oi_underlying"] > 0,
                                       df["open_interest"] / df["total_oi_underlying"] * 100.0, 0.0)

    call_oi = df[df["option_type"] == "CALL"].groupby(["date", "underlying_code", "strike_price"])["open_interest"].sum().reset_index().rename(columns={"open_interest": "call_oi"})
    put_oi = df[df["option_type"] == "PUT"].groupby(["date", "underlying_code", "strike_price"])["open_interest"].sum().reset_index().rename(columns={"open_interest": "put_oi"})
    call_vol = df[df["option_type"] == "CALL"].groupby(["date", "underlying_code", "strike_price"])["volume"].sum().reset_index().rename(columns={"volume": "call_vol"})
    put_vol = df[df["option_type"] == "PUT"].groupby(["date", "underlying_code", "strike_price"])["volume"].sum().reset_index().rename(columns={"volume": "put_vol"})

    ratios = pd.merge(call_oi, put_oi, on=["date", "underlying_code", "strike_price"], how="outer")
    ratios = pd.merge(ratios, call_vol, on=["date", "underlying_code", "strike_price"], how="outer")
    ratios = pd.merge(ratios, put_vol, on=["date", "underlying_code", "strike_price"], how="outer")

    ratios["oi_call_put_ratio"] = np.where(ratios["put_oi"] > 0, ratios["call_oi"] / ratios["put_oi"], np.nan)
    ratios["vol_call_put_ratio"] = np.where(ratios["put_vol"] > 0, ratios["call_vol"] / ratios["put_vol"], np.nan)

    df = df.merge(ratios[["date", "underlying_code", "strike_price", "oi_call_put_ratio", "vol_call_put_ratio"]],
                  on=["date", "underlying_code", "strike_price"], how="left")

    df["oi_call_put_ratio"] = df["oi_call_put_ratio"].fillna(0.0)
    df["vol_call_put_ratio"] = df["vol_call_put_ratio"].fillna(0.0)

    df["open_interest_call"] = np.where(df["option_type"] == "CALL", df["open_interest"], 0.0)
    df["open_interest_put"] = np.where(df["option_type"] == "PUT", df["open_interest"], 0.0)
    df["volume_call"] = np.where(df["option_type"] == "CALL", df["volume"], 0.0)
    df["volume_put"] = np.where(df["option_type"] == "PUT", df["volume"], 0.0)

    call_total = df.groupby(["date", "underlying_code"])["open_interest_call"].transform("sum")
    put_total = df.groupby(["date", "underlying_code"])["open_interest_put"].transform("sum")

    df["oi_total_call_put_ratio"] = np.where(put_total > 0, call_total / put_total, 0.0)

    if verbose:
        print(f"    → Computing implied volatility and Greeks for {len(df):,} rows …", flush=True)

    iv, delta, theta, gamma, vega, rho = compute_iv_and_greeks(df)
    df["implied_vol"] = iv
    df["delta"] = delta
    df["theta"] = theta
    df["gamma"] = gamma
    df["vega"] = vega
    df["rho"] = rho

    df = df.round({
        "prev_settle_norm": 4, "close_norm": 4, "settle_norm": 4,
        "volume_wan": 4, "open_interest_wan": 4,
        "volume_pct": 4, "open_interest_pct": 4,
        "oi_call_put_ratio": 4, "vol_call_put_ratio": 4,
        "oi_total_call_put_ratio": 4,
        "implied_vol": 4, "delta": 6, "theta": 6, "gamma": 6, "vega": 6, "rho": 6,
    })

    if verbose:
        print(f"    → Derived columns added: moneyness, ratios, normalized prices, IV, Greeks", flush=True)

    return df


# ============================================================================
# Main pipeline
# ============================================================================
async def main():
    ap = argparse.ArgumentParser(
        description="Build SZSE ETF Options data and insert to database (missing dates only)."
    )
    add_common_build_args(ap)
    args = ap.parse_args()

    t0 = time.time()
    print_build_header(
        "BUILD SZSE ETF OPTIONS  ·  missing-data-only → DATABASE",
        **{
            "Trend dir":  SZSE_TREND_DIR,
            "Date range": f"{args.start_date or '(all)'} → {args.end_date or '(all)'}",
            "Today":      TODAY_STR,
        }
    )

    # ------------------------------------------------------------------
    # 1. Discover source files and available dates
    # ------------------------------------------------------------------
    print("\n[1/4] Discovering source CSV files …", flush=True)
    option_files = glob_source_files(SZSE_TREND_DIR, "szse_trend_option_*.csv")
    etf_files = glob_source_files(SZSE_TREND_DIR, "szse_trend_etf_*.csv")
    print(f"    → {len(option_files)} option files, {len(etf_files)} ETF files available", flush=True)

    if not option_files:
        print("    [FATAL] No option source CSVs found", flush=True)
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

    print(f"    → {len(available_dates)} unique dates available in range", flush=True)

    # ------------------------------------------------------------------
    # 2. Connect to DB and find missing dates
    # ------------------------------------------------------------------
    print("\n[2/4] Connecting to database and detecting missing dates …", flush=True)
    conn = await get_db_or_exit()

    try:
        if args.force:
            print("    [DB] Force mode: truncating existing tables", flush=True)
            # CASCADE truncates all FK child tables automatically
            for tbl in ("stats.options_aggregate", "stats.options_volume_oi",
                        "stats.options_greeks", "stats.options_settlement",
                        "stats.options_strike", "stats.options_terms",
                        "stats.options_identity"):
                await truncate_table_async(conn, tbl)
            missing_dates = available_dates
        else:
            # Query DISTINCT dates from options_identity (one row per date, not
            # one per contract — much smaller result set).
            rows = await conn.fetch("SELECT DISTINCT date FROM stats.options_identity")
            existing_dates = {r["date"] for r in rows}
            missing_dates = available_dates - existing_dates

        print(f"    [DB] {len(missing_dates)} dates missing from stats.options_identity "
              f"(out of {len(available_dates)} available)", flush=True)

        if not missing_dates:
            print("    [INFO] Database is up to date — no new dates to insert", flush=True)
            print_wall_time(t0)
            return

        # ------------------------------------------------------------------
        # 3. Read only missing-date source files and build options frame
        # ------------------------------------------------------------------
        print(f"\n[3/4] Reading source CSVs for {len(missing_dates)} missing dates …", flush=True)
        missing_ymd = {d.strftime("%Y%m%d") for d in missing_dates}

        missing_option_files = [
            f for f in option_files
            if ymd_from_filename(f, "szse_trend_option_") in missing_ymd
        ]
        missing_etf_files = [
            f for f in etf_files
            if ymd_from_filename(f, "szse_trend_etf_") in missing_ymd
        ]
        print(f"    → {len(missing_option_files)} option files, {len(missing_etf_files)} ETF files to read", flush=True)

        options_df = build_options_df(missing_option_files)

        if len(options_df) == 0:
            print("    [INFO] No options rows parsed from missing-date files", flush=True)
            print_wall_time(t0)
            return

        print(f"    → {len(options_df):,} options rows  ·  {options_df['underlying_code'].nunique()} underlyings", flush=True)
        print(f"    → date range: {options_df['date'].min().date()} → {options_df['date'].max().date()}", flush=True)

        etf_ohlcv = load_etf_ohlcv(missing_etf_files)
        options_df = add_derived_columns(options_df, etf_ohlcv)

        # ------------------------------------------------------------------
        # 4. Insert to database
        # ------------------------------------------------------------------
        print("\n[4/4] Inserting data to database …", flush=True)

        # Convert dates to datetime.date for asyncpg DATE codec.
        # asyncpg requires datetime.date instances; passing str raises
        # "expected a date instance, got 'str'".
        options_db = options_df.copy()
        options_db["date"] = options_db["date"].dt.date
        options_db["expiry_date"] = options_db["expiry_date"].dt.date

        # Dedupe within the batch to avoid "ON CONFLICT DO UPDATE cannot
        # affect row a second time" (multiple files may produce the same
        # (date, contract_code)).
        options_db = options_db.drop_duplicates(subset=["date", "contract_code"], keep="last")

        # Build rows for each split table. Since we already filtered to
        # missing dates, all rows should be new — no existing_keys check
        # needed here.
        identity_rows, terms_rows, strike_rows, settlement_rows = [], [], [], []
        greeks_rows, volume_oi_rows, aggregate_rows = [], [], []
        for _, row in options_db.iterrows():
            identity_rows.append({
                "date": row["date"],
                "contract_code": row["contract_code"],
                "contract_name": row["contract_name"],
            })
            terms_rows.append({
                "date": row["date"],
                "contract_code": row["contract_code"],
                "underlying_code": row["underlying_code"],
                "underlying_name": row["underlying_name"],
                "option_type": row["option_type"],
                "expiry_month": row["expiry_month"],
                "expiry_date": row["expiry_date"],
                "days_to_expiry": int(row["days_to_expiry"]),
            })
            strike_rows.append({
                "date": row["date"],
                "contract_code": row["contract_code"],
                "strike_str": row.get("strike_str"),
                "strike_price_raw": row.get("strike_price_raw"),
                "strike_price": row["strike_price"],
                "has_a_suffix": int(row["has_a_suffix"]),
            })
            settlement_rows.append({
                "date": row["date"],
                "contract_code": row["contract_code"],
                "prev_settle": row.get("prev_settle"),
                "close": row.get("close"),
                "settle": row.get("settle"),
                "pct_change": row.get("pct_change"),
                "prev_settle_norm": row.get("prev_settle_norm"),
                "close_norm": row.get("close_norm"),
                "settle_norm": row.get("settle_norm"),
                "underlying_close": row.get("underlying_close", 0),
                "moneyness_ratio": row.get("moneyness_ratio"),
            })
            greeks_rows.append({
                "date": row["date"],
                "contract_code": row["contract_code"],
                "implied_vol": row.get("implied_vol"),
                "delta": row.get("delta"),
                "theta": row.get("theta"),
                "gamma": row.get("gamma"),
                "vega": row.get("vega"),
                "rho": row.get("rho"),
            })
            volume_oi_rows.append({
                "date": row["date"],
                "contract_code": row["contract_code"],
                "volume": row["volume"],
                "volume_wan": row["volume_wan"],
                "open_interest": row["open_interest"],
                "open_interest_wan": row["open_interest_wan"],
            })
            aggregate_rows.append({
                "date": row["date"],
                "contract_code": row["contract_code"],
                "total_volume_underlying": row.get("total_volume_underlying"),
                "total_oi_underlying": row.get("total_oi_underlying"),
                "volume_pct": row.get("volume_pct"),
                "open_interest_pct": row.get("open_interest_pct"),
                "oi_call_put_ratio": row.get("oi_call_put_ratio"),
                "vol_call_put_ratio": row.get("vol_call_put_ratio"),
                "open_interest_call": row.get("open_interest_call"),
                "open_interest_put": row.get("open_interest_put"),
                "volume_call": row.get("volume_call"),
                "volume_put": row.get("volume_put"),
                "oi_total_call_put_ratio": row.get("oi_total_call_put_ratio"),
            })

        # Insert identity first (FK parent), then sub-tables
        pk_cols = ["date", "contract_code"]
        split_tables = [
            ("stats.options_identity",  identity_rows),
            ("stats.options_terms",     terms_rows),
            ("stats.options_strike",    strike_rows),
            ("stats.options_settlement",settlement_rows),
            ("stats.options_greeks",    greeks_rows),
            ("stats.options_volume_oi", volume_oi_rows),
            ("stats.options_aggregate", aggregate_rows),
        ]
        for tbl, rows in split_tables:
            if rows:
                inserted = await bulk_upsert_async(conn, tbl, rows, pk_cols)
                print(f"    [DB] Inserted {inserted:,} rows into {tbl}", flush=True)
            else:
                print(f"    [DB] No new rows to insert into {tbl}", flush=True)

    finally:
        await conn.close()

    # Console summary
    print(f"\n  Underlying distribution:", flush=True)
    for code, sub in options_df.groupby("underlying_code"):
        name = str(sub["underlying_name"].dropna().iloc[0]) if sub["underlying_name"].notna().any() else ""
        n_dates = int(sub["date"].dt.strftime("%Y-%m-%d").nunique())
        n_strikes = int(sub["strike_price"].nunique())
        print(f"    · {code:<8s} {name:<12s} {n_dates:>4d} days  {n_strikes:>3d} strikes", flush=True)

    print_wall_time(t0)


if __name__ == "__main__":
    asyncio.run(main())
