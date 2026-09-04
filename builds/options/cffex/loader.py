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

# Epoch anchor for vectorized days_to_expiry math (no date objects in frames)
_EPOCH = date(1970, 1, 1)

from builds.options.cffex.config import (
    COL_MAP,
    PRODUCT_UNDERLYING,
    compute_expiry_date,
    parse_contract_code,
)
from downloads._common import read_csv_gpu_safe
from builds.options.cffex.paths import (
    glob_options_files,
    ymd_from_options_filename,
)
from builds._commons.safe_parse import safe_to_datetime


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

    Fully vectorized: the YYYYMMDD token is regex-extracted from ALL paths
    in ONE Series.str pass and matched against the target set via isin —
    no per-file Python loop, no list append; kept paths go straight from
    the boolean mask to a numpy ``.tolist()``.
    """
    if not files or not target_dates:
        return []
    # target dates → YYYYMMDD strings without a Python loop (datetime64 →
    # "YYYY-MM-DD" → strip "-")
    d64 = np.asarray(sorted(target_dates), dtype="datetime64[D]")
    target_ymd = set(np.char.replace(d64.astype("U10"), "-", ""))

    paths = pd.Series(files, dtype="object")
    # (\d{8})_options.csv anchored at the end — separator-agnostic and
    # equivalent to ymd_from_options_filename (stem must be exactly 8 digits)
    ymd = paths.str.extract(r"(\d{8})_options\.csv$", expand=False)
    keep = ymd.notna() & ymd.isin(target_ymd)
    return np.asarray(paths[keep], dtype=object).tolist()


def _read_one_csv(filepath: str) -> Optional[pd.DataFrame]:
    """Read a single options CSV file and return a clean DataFrame.

    Source CSVs are generated canonical by downloads (whitespace-free cells,
    null tokens already ""), so the read is PLAIN — no dtype argument, no
    post-parse coercion. pandas auto-inference lands every column on its
    final type (str contract ids, float64 numerics); a column that cannot
    be inferred cleanly is a downloads bug, fixed at the generator.

    Returns DataFrame or None if file is empty/unreadable.
    """
    try:
        df = read_csv_gpu_safe(filepath)
    except Exception:
        return None

    if df is None or len(df) == 0:
        return None

    # Check for required column
    if "合约代码" not in np.asarray(df.columns).tolist():
        return None

    # Filter out rows with empty contract codes (one vectorized str op)
    df = df[df["合约代码"].astype(str).str.len() > 0]
    if df.empty:
        return None

    # Rename columns
    return df.rename(columns=COL_MAP)


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

    Contract attributes (product/underlying/type/strike/expiry) are
    resolved by parsing each UNIQUE contract code exactly once (host-side)
    into a small lookup frame merged back on ``contract_code`` — no
    iterrows / per-row dict loops (each element extraction is a cudf.pandas
    slow-path fallback).

    Args:
        files: list of *_options.csv file paths (already filtered to missing dates)
        index_ohlcv: DataFrame with (date, underlying_code, close) for moneyness
        verbose: print progress messages

    Returns:
        pd.DataFrame ready for DB insertion, or empty DataFrame if no data.
    """
    if verbose:
        print(f"    [CFFEX OPTIONS] reading {len(files)} *_options.csv files", flush=True)

    frames: list[pd.DataFrame] = []
    n_empty = 0
    n_ok = 0

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

        # Scalar broadcast: numpy datetime64[ns]. A pd.Timestamp scalar takes
        # the cudf slow path (2 fallbacks/file) AND lands the column as
        # datetime64[s], which breaks the ns-epoch math below.
        df["date"] = np.datetime64(trade_date, "ns")
        frames.append(df)
        n_ok += 1

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    out["date"] = safe_to_datetime(out["date"])
    out = out.dropna(subset=["date"])

    # Parse each UNIQUE contract code exactly once (host-side). Expiry for
    # CFFEX index options depends only on the contract month (4th Wednesday),
    # so it is a per-contract constant stored as epoch days.
    uniq_codes = sorted(set(np.asarray(out["contract_code"]).tolist()))
    meta_rows: list[dict] = []
    n_invalid_contracts = 0
    for code in uniq_codes:
        try:
            parsed = parse_contract_code(code)
        except ValueError:
            n_invalid_contracts += 1
            continue
        product = parsed["product"]
        month = parsed["month"]
        underlying_code, underlying_name = PRODUCT_UNDERLYING.get(product, ("", ""))
        expiry_date = compute_expiry_date(_EPOCH, month)  # month-only constant
        meta_rows.append({
            "contract_code": code,
            "contract_name": code,  # CFFEX uses code as name
            "underlying_code": underlying_code,
            "underlying_name": underlying_name,
            "option_type": parsed["option_type"],
            "expiry_month": month,
            "strike_str": parsed["strike_str"],
            "strike_price_raw": parsed["strike"],
            "strike_price": parsed["strike"],
            "has_a_suffix": 0,  # CFFEX doesn't use A-suffix
            "_exp_days": (expiry_date - _EPOCH).days,
        })
    if not meta_rows:
        return pd.DataFrame()
    n_pre_merge = len(out)
    out = out.merge(pd.DataFrame(meta_rows), on="contract_code", how="inner")
    # rows dropped by the inner merge = invalid-contract rows
    n_parse_fail = n_pre_merge - len(out)

    # Days to expiry from epoch-day ints; expiry_date back to datetime64.
    # The [ns] cast is unit-explicit: astype("int64") of a datetime64 column
    # yields the column's own unit (seconds under a [s] column), so the
    # ns-per-day divisor is only correct after normalizing to [ns].
    date_epoch = (
        out["date"].astype("datetime64[ns]").astype("int64") // 86_400_000_000_000
    )
    out["days_to_expiry"] = (out["_exp_days"] - date_epoch).clip(lower=0)
    out["expiry_date"] = pd.to_datetime(
        out["_exp_days"].astype("int64") * 86_400_000_000_000)

    # CSV delta may be absent entirely (older exports) — keep the column
    # contract that compute_iv_and_greeks expects.
    if "csv_delta" not in np.asarray(out.columns).tolist():
        out["csv_delta"] = np.nan

    out = out.sort_values(
        ["underlying_code", "date", "expiry_date", "strike_price", "option_type"]
    ).reset_index(drop=True)

    # --- Fill underlying_close from index data (vectorized merge — no
    # per-row .apply over a python-dict lookup) ---
    if index_ohlcv is not None and len(index_ohlcv) > 0:
        # index_ohlcv should have columns: date, underlying_code, close
        index_map = index_ohlcv.copy()
        index_map["date"] = safe_to_datetime(index_map["date"])
        index_map = index_map.dropna(subset=["date"])
        index_map = index_map.drop_duplicates(
            subset=["date", "underlying_code"], keep="last"
        ).rename(columns={"close": "underlying_close"})
        out = out.drop(columns=["underlying_close"], errors="ignore").merge(
            index_map[["date", "underlying_code", "underlying_close"]],
            on=["date", "underlying_code"], how="left",
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

    if verbose:
        print(
            f"    [CFFEX OPTIONS] {n_ok} files with data, {n_empty} empty, "
            f"{n_invalid_contracts} invalid contracts "
            f"({n_parse_fail} rows dropped), {len(out)} rows",
            flush=True,
        )

    # --- Add derived columns ---
    out["volume_wan"] = out["volume"] / 10000.0
    out["open_interest_wan"] = out["open_interest"] / 10000.0
    # 涨跌2 (change vs prev settle in %) feeds stats.options_settlement.pct_change
    out["pct_change"] = out["change_pct"]
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

    # Drop pre-existing ratio columns if present, to avoid merge suffix conflicts
    out = out.drop(columns=["oi_call_put_ratio", "vol_call_put_ratio"], errors="ignore")

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