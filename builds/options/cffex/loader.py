"""builds.options.cffex.loader — CSV reading, parsing, and row construction for CFFEX options.

Reads per-day options CSV files from:
  - temps/cffex_archive/YYYYMM/YYYYMMDD_options.csv
  - temps/cffex_options_trend/YYYYMM/YYYYMMDD_options.csv

Produces a long DataFrame ready for insertion into the 7 options_* tables:
  - options_identity   (date, contract_code, contract_name)
  - options_terms      (date, contract_code, underlying, type, expiry, days_to_expiry)
  - options_strike     (date, contract_code, strike_str, strike_price, has_a_suffix)
  - options_settlement (date, contract_code, prev_settle, close, settle, ..., moneyness_ratio)
  - options_greeks     (date, contract_code, implied_vol, delta, theta, gamma, vega, rho)
  - options_volume_oi  (date, contract_code, volume, open_interest, ...)
  - options_aggregate  (date, contract_code, totals, pcts, call/put ratios)
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from builds.options.cffex.config import (
    COL_MAP,
    NUMERIC_COLS,
    PRODUCT_NAMES,
    PRODUCT_UNDERLYING,
    PRODUCT_TYPES,
    _NULL_TOKENS,
    compute_expiry_date,
    parse_contract_code,
)
from builds.options.cffex.paths import (
    glob_options_files,
    ymd_from_options_filename,
)
from _common.build_commons import parse_num


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ymd_to_date(ymd: str) -> Optional[date]:
    """Convert YYYYMMDD string to datetime.date."""
    if not ymd or len(ymd) != 8 or not ymd.isdigit():
        return None
    try:
        return date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))
    except ValueError:
        return None


def filter_files_by_dates(
    files: List[str],
    target_dates: set[date],
) -> List[str]:
    """Filter options CSV files to only those whose date is in target_dates.

    Args:
        files: list of file paths
        target_dates: set of date objects to keep

    Returns:
        Filtered list of file paths.
    """
    target_ymd = {d.strftime("%Y%m%d") for d in target_dates}
    out: List[str] = []
    for path in files:
        ymd = ymd_from_options_filename(path)
        if ymd and ymd in target_ymd:
            out.append(path)
    return out


def _read_one_csv(filepath: str) -> Optional[pd.DataFrame]:
    """Read a single options CSV file and return a clean DataFrame.

    Handles:
      - UTF-8 BOM (files saved with BOM from download step)
      - Trailing whitespace in contract codes
      - "--" as null value for numeric columns
      - Numeric coercion for all numeric columns

    Returns DataFrame or None if file is empty/unreadable.
    """
    try:
        df = pd.read_csv(filepath, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    except Exception:
        try:
            df = pd.read_csv(filepath, dtype=str, encoding="utf-8", keep_default_na=False)
        except Exception:
            return None

    if df is None or len(df) == 0:
        return None

    # Strip whitespace from all string columns
    df = df.apply(lambda c: c.str.strip() if c.dtype == "object" else c)

    # Check for required column
    if "合约代码" not in df.columns:
        return None

    # Filter out rows with empty/whitespace-only contract codes
    df = df[df["合约代码"].notna()].copy()
    df = df[df["合约代码"].str.len() > 0].copy()
    if df.empty:
        return None

    # Rename columns
    df = df.rename(columns=COL_MAP)

    # Convert numeric columns
    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = df[col].apply(
                lambda v: np.nan if str(v).strip() in _NULL_TOKENS else v
            )
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df if not df.empty else None


# ---------------------------------------------------------------------------
# Greeks computation (vectorized, CPU/GPU-routed —
# see _common/df_utils/black_scholes.py)
# ---------------------------------------------------------------------------

from _common.df_utils import compute_iv_and_greeks as _compute_iv_and_greeks


def compute_iv_and_greeks(options_df: pd.DataFrame) -> pd.DataFrame:
    """Compute implied volatility and Greeks from option prices.

    Vectorized implementation using Black-76 model (matches QuantLib's
    BlackCalculator). Uses CSV delta as fallback when IV computation
    fails (invalid inputs, no convergence).

    Args:
        options_df: DataFrame with columns:
            - underlying_close: spot price of the underlying index
            - strike_price: strike price
            - settle: option settlement price
            - days_to_expiry: calendar days to expiry
            - option_type: 'CALL' or 'PUT'
            - csv_delta: Delta from CFFEX CSV (used as fallback)

    Returns:
        DataFrame with additional columns: implied_vol, delta, theta, gamma, vega, rho
    """
    iv, delta, theta, gamma, vega, rho = _compute_iv_and_greeks(
        options_df,
        price_scale=1.0,
        opt_scale=1.0,
        csv_delta_col="csv_delta",
    )

    result = options_df.copy()
    result["implied_vol"] = iv
    result["delta"] = delta
    result["theta"] = theta
    result["gamma"] = gamma
    result["vega"] = vega
    result["rho"] = rho
    return result


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_options_df(
    files: List[str],
    index_ohlcv: Optional[pd.DataFrame] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Build a long options DataFrame from CFFEX options CSV files.

    Args:
        files: list of *_options.csv file paths (already filtered to missing dates)
        index_ohlcv: DataFrame with (date, underlying_code, close) for moneyness
        verbose: print progress messages

    Returns:
        pd.DataFrame ready for DB insertion, or empty DataFrame if no data.
    """
    if verbose:
        print(f"    [CFFEX OPTIONS] reading {len(files)} *_options.csv files", flush=True)

    rows: List[dict] = []
    n_empty = 0
    n_ok = 0
    n_parse_fail = 0

    for filepath in files:
        ymd = ymd_from_options_filename(filepath)
        if not ymd:
            continue
        trade_date = ymd_to_date(ymd)
        if trade_date is None:
            continue

        df = _read_one_csv(filepath)
        if df is None or df.empty:
            n_empty += 1
            continue

        for _, r in df.iterrows():
            contract_code = str(r.get("contract_code", "")).strip()
            try:
                parsed = parse_contract_code(contract_code)
            except ValueError:
                n_parse_fail += 1
                continue

            product = parsed["product"]
            month = parsed["month"]
            option_type = parsed["option_type"]
            strike = parsed["strike"]
            strike_str = parsed["strike_str"]

            # Get product info
            product_name = PRODUCT_NAMES.get(product, product)
            contract_type = PRODUCT_TYPES.get(product, "index")
            underlying_code, underlying_name = PRODUCT_UNDERLYING.get(
                product, ("", "")
            )

            # Compute expiry date (4th Wednesday)
            try:
                expiry_date = compute_expiry_date(trade_date, month)
                days_to_expiry = max(0, (expiry_date - trade_date).days)
            except Exception:
                expiry_date = trade_date
                days_to_expiry = 0

            row = {
                # Identity fields
                "date": trade_date,
                "contract_code": contract_code,
                "contract_name": contract_code,  # CFFEX uses code as name

                # Terms fields
                "underlying_code": underlying_code,
                "underlying_name": underlying_name,
                "option_type": option_type,
                "expiry_month": month,
                "expiry_date": expiry_date,
                "days_to_expiry": days_to_expiry,

                # Strike fields
                "strike_str": strike_str,
                "strike_price_raw": strike,
                "strike_price": strike,
                "has_a_suffix": 0,  # CFFEX doesn't use A-suffix

                # Settlement fields
                "prev_settle": r.get("prev_settle"),
                "close": r.get("close"),
                "settle": r.get("settle"),
                "pct_change": r.get("change_pct"),
                "underlying_close": np.nan,  # filled later from index data
                "moneyness_ratio": np.nan,    # filled later

                # Volume/OI fields
                "volume": r.get("volume"),
                "open_interest": r.get("open_interest"),

                # Greeks (from CSV)
                "csv_delta": r.get("delta"),  # raw CSV delta

                # Aggregate fields (computed later)
                "volume_wan": np.nan,
                "open_interest_wan": np.nan,
                "prev_settle_norm": np.nan,
                "close_norm": np.nan,
                "settle_norm": np.nan,
                "total_volume_underlying": np.nan,
                "total_oi_underlying": np.nan,
                "volume_pct": np.nan,
                "open_interest_pct": np.nan,
                "oi_call_put_ratio": np.nan,
                "vol_call_put_ratio": np.nan,
                "open_interest_call": np.nan,
                "open_interest_put": np.nan,
                "volume_call": np.nan,
                "volume_put": np.nan,
                "oi_total_call_put_ratio": np.nan,

                # Greeks (computed later)
                "implied_vol": np.nan,
                "delta": np.nan,
                "theta": np.nan,
                "gamma": np.nan,
                "vega": np.nan,
                "rho": np.nan,
            }
            rows.append(row)
        n_ok += 1

    if verbose:
        print(
            f"    [CFFEX OPTIONS] {n_ok} files with data, {n_empty} empty, "
            f"{n_parse_fail} parse failures, {len(rows)} rows",
            flush=True,
        )

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows)
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["expiry_date"] = pd.to_datetime(out["expiry_date"], errors="coerce")
    out = out.dropna(subset=["date"]).sort_values(
        ["underlying_code", "date", "expiry_date", "strike_price", "option_type"]
    ).reset_index(drop=True)

    # --- Fill underlying_close from index data ---
    if index_ohlcv is not None and len(index_ohlcv) > 0:
        # index_ohlcv should have columns: date, underlying_code, close
        index_map = index_ohlcv.copy()
        index_map["date"] = pd.to_datetime(index_map["date"], errors="coerce")
        # Build a lookup: (date, underlying_code) → close
        index_lookup = index_map.set_index(["date", "underlying_code"])["close"].to_dict()
        out["underlying_close"] = out.apply(
            lambda r: index_lookup.get((r["date"], r["underlying_code"]), np.nan),
            axis=1,
        )
        # Compute moneyness
        out["moneyness_ratio"] = np.where(
            out["underlying_close"] > 0,
            out["strike_price"] / out["underlying_close"],
            0.0,
        )
    else:
        out["underlying_close"] = 0.0
        out["moneyness_ratio"] = 0.0

    # --- Add derived columns ---
    out["volume_wan"] = out["volume"] / 10000.0
    out["open_interest_wan"] = out["open_interest"] / 10000.0
    out["prev_settle_norm"] = out["prev_settle"]
    out["close_norm"] = out["close"]
    out["settle_norm"] = out["settle"]

    # Per-underlying totals
    grouped = out.groupby(["date", "underlying_code"])
    out["total_volume_underlying"] = grouped["volume"].transform("sum")
    out["total_oi_underlying"] = grouped["open_interest"].transform("sum")

    out["volume_pct"] = np.where(
        out["total_volume_underlying"] > 0,
        out["volume"] / out["total_volume_underlying"] * 100.0,
        0.0,
    )
    out["open_interest_pct"] = np.where(
        out["total_oi_underlying"] > 0,
        out["open_interest"] / out["total_oi_underlying"] * 100.0,
        0.0,
    )

    # Call/Put ratios per strike
    call_oi = out[out["option_type"] == "CALL"].groupby(
        ["date", "underlying_code", "strike_price"]
    )["open_interest"].sum().reset_index().rename(columns={"open_interest": "call_oi"})
    put_oi = out[out["option_type"] == "PUT"].groupby(
        ["date", "underlying_code", "strike_price"]
    )["open_interest"].sum().reset_index().rename(columns={"open_interest": "put_oi"})
    call_vol = out[out["option_type"] == "CALL"].groupby(
        ["date", "underlying_code", "strike_price"]
    )["volume"].sum().reset_index().rename(columns={"volume": "call_vol"})
    put_vol = out[out["option_type"] == "PUT"].groupby(
        ["date", "underlying_code", "strike_price"]
    )["volume"].sum().reset_index().rename(columns={"volume": "put_vol"})

    ratios = pd.merge(call_oi, put_oi, on=["date", "underlying_code", "strike_price"], how="outer")
    ratios = pd.merge(ratios, call_vol, on=["date", "underlying_code", "strike_price"], how="outer")
    ratios = pd.merge(ratios, put_vol, on=["date", "underlying_code", "strike_price"], how="outer")

    ratios["oi_call_put_ratio"] = np.where(ratios["put_oi"] > 0, ratios["call_oi"] / ratios["put_oi"], np.nan)
    ratios["vol_call_put_ratio"] = np.where(ratios["put_vol"] > 0, ratios["call_vol"] / ratios["put_vol"], np.nan)

    # Drop pre-existing ratio columns to avoid merge suffix conflicts
    out = out.drop(columns=["oi_call_put_ratio", "vol_call_put_ratio"])

    out = out.merge(
        ratios[["date", "underlying_code", "strike_price", "oi_call_put_ratio", "vol_call_put_ratio"]],
        on=["date", "underlying_code", "strike_price"],
        how="left",
    )

    out["oi_call_put_ratio"] = out["oi_call_put_ratio"].fillna(0.0)
    out["vol_call_put_ratio"] = out["vol_call_put_ratio"].fillna(0.0)

    out["open_interest_call"] = np.where(out["option_type"] == "CALL", out["open_interest"], 0.0)
    out["open_interest_put"] = np.where(out["option_type"] == "PUT", out["open_interest"], 0.0)
    out["volume_call"] = np.where(out["option_type"] == "CALL", out["volume"], 0.0)
    out["volume_put"] = np.where(out["option_type"] == "PUT", out["volume"], 0.0)

    call_total = out.groupby(["date", "underlying_code"])["open_interest_call"].transform("sum")
    put_total = out.groupby(["date", "underlying_code"])["open_interest_put"].transform("sum")
    out["oi_total_call_put_ratio"] = np.where(put_total > 0, call_total / put_total, 0.0)

    # --- Compute Greeks ---
    if verbose:
        print(f"    → Computing implied volatility and Greeks for {len(out):,} rows …", flush=True)

    out = compute_iv_and_greeks(out)

    # Round numeric columns
    out = out.round({
        "volume_wan": 4, "open_interest_wan": 4,
        "volume_pct": 4, "open_interest_pct": 4,
        "oi_call_put_ratio": 4, "vol_call_put_ratio": 4,
        "oi_total_call_put_ratio": 4,
        "implied_vol": 4, "delta": 6, "theta": 6, "gamma": 6, "vega": 6, "rho": 6,
    })

    if verbose:
        print(f"    → Derived columns added: moneyness, ratios, IV, Greeks", flush=True)

    return out