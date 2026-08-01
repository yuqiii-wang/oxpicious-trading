"""
build_szse_sse_etf_and_margin.py — Build combined SZSE + SSE ETF OHLCV + margin +
composition data and insert directly to the database (no intermediate CSV,
missing-data-only).

Reads the per-day SZSE/SSE CSV archives produced by download scripts:
  • SZSE: szse_archive/szse_etf_YYYYMMDD.csv        (2022-01 → 2025-06-30 legacy)
  • SZSE: szse_trend/szse_trend_etf_YYYYMMDD.csv    (2025-07 → today snapshot)
  • SSE: sse_trend/sse_trend_stock_YYYYMMDD.csv     (today snapshot, stocks only — NO ETFs)
  • SZSE: szse_margin/szse_margin_detail_YYYYMMDD.csv  (per-security margin detail)
  • SZSE: szse_etf_composition/szse_etf_comp_YYYYMMDD_<code>.csv (per-file finished CSV)
  • CSI:  csi_index_composition/*_closeweight_*.csv (index composition)

CRITICAL: Stock/ETF codes must be disambiguated with exchange suffixes (.SS for
Shanghai, .SZ for Shenzhen) because 000xxx/001xxx codes overlap between indices
and stocks. ETF codes have NO overlap:
  - SSE: 510xxx, 511xxx, 512xxx, 513xxx, 515xxx, 516xxx, 518xxx, 56xxx
  - SZSE: 150xxx, 159xxx, 16xxx

Missing-data detection flow (DB-first):
  OHLCV + margin (cross-date dependency — splits + MAs need FULL per-code history):
    1. Glob all source CSV files (filenames only — no reading yet)
    2. Extract available dates from filenames
    3. Query stats.etf_identity by index for existing (date, code) pairs
    4. missing_dates = available_dates - existing_dates
    5. If no missing dates: query DB for historical OHLCV+margin only (for
       composition merge_asof + sec_classification stats), skip CSV reading entirely
    6. If missing dates exist: read ONLY the source CSVs for those missing
       dates, then query DB for existing OHLCV+margin (historical context
       for split adjustment + MA computation), and concatenate the two
    7. Merge OHLCV + margin, apply split adjustment, compute MAs (over the
       combined full history)
    8. Filter merged to (date, code) NOT in existing_keys [and within
       --start/--end range]
    9. Bulk upsert only the missing rows into etf_identity + 5 sub-tables

  Composition (sec_composition — no cross-date dependency):
    1. Read all composition CSVs, build holdings rows
    2. Query stats.sec_composition for existing (code, snapshot_date) pairs
    3. Filter to missing (code, snapshot_date) pairs
    4. Bulk upsert only the missing rows

  ETF meta (sec_classification type='etf' — per-code metadata, not per-date):
    Computed from full merged data (n_days, avg_volume_wan, etc.) and
    upserted unconditionally (ON CONFLICT DO UPDATE — idempotent). Only
    quality-metric columns are updated; classification + index_code columns
    (populated by build_etf_index_map.py) are preserved on conflict.

With --force: truncate all target tables first, then read ALL source CSVs
(no DB historical query needed since DB is empty).

Usage:
  python build_szse_sse_etf_and_margin.py
  python build_szse_sse_etf_and_margin.py --start-date 2024-01-01 --end-date 2025-06-30
  python build_szse_sse_etf_and_margin.py --force
"""
import os, sys, re, glob, time, argparse
from collections import Counter
import datetime
from pathlib import Path

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _download_commons import read_csv_preferred, strip_exchange_suffix
from _study_select_etf import ETF_THEMES
# Derive ETF_THEME_RULES (list of (theme_id, label, slug, keywords) tuples)
# from ETF_THEMES OrderedDict for the keyword-based classify_etf_theme below.
ETF_THEME_RULES = [
    (tid, cfg.get("theme_label", tid), cfg.get("slug", tid), cfg.get("kw", []))
    for tid, cfg in ETF_THEMES.items()
]
from _build_commons import (
    setup_utf8_stdout, add_common_build_args, get_db_or_exit,
    parse_num, parse_date, ymd_from_filename, ymd_to_date, in_range,
    glob_source_files, print_build_header, print_wall_time,
    PROJECT_ROOT, TODAY_STR,
    get_existing_keys_async, bulk_upsert_async, truncate_table_async,
)

setup_utf8_stdout()

import asyncio

# ============================================================================
# Paths
# ============================================================================
SZSE_ARCHIVE_DIR    = os.path.join(PROJECT_ROOT, "temps", "szse_archive")
SZSE_TREND_DIR      = os.path.join(PROJECT_ROOT, "temps", "szse_trend")
SSE_TREND_DIR       = os.path.join(PROJECT_ROOT, "temps", "sse_trend")
SZSE_MARGIN_DIR     = os.path.join(PROJECT_ROOT, "temps", "szse_margin")
SSE_MARGIN_DIR      = os.path.join(PROJECT_ROOT, "temps", "sse_margin")
COMP_DIR            = os.path.join(PROJECT_ROOT, "temps", "szse_etf_composition")
INDEX_COMP_DIR      = os.path.join(PROJECT_ROOT, "temps", "csi_index_composition")
SZSE_INDEX_COMP_DIR = os.path.join(PROJECT_ROOT, "temps", "szse_index_composition")

# ============================================================================
# ETF code patterns by exchange (NO overlap between exchanges)
# ============================================================================
SZSE_ETF_PREFIXES = ("15", "16")
SSE_ETF_PREFIXES = ("510", "511", "512", "513", "515", "516", "518", "56")


def is_szse_etf_code(code):
    s = str(code).strip()
    if "." in s:
        s = s.split(".")[0]
    try:
        s = str(int(float(s))).zfill(6)
    except Exception:
        pass
    return len(s) == 6 and s.isdigit() and s[:2] in SZSE_ETF_PREFIXES


def is_sse_etf_code(code):
    s = str(code).strip()
    if "." in s:
        s = s.split(".")[0]
    try:
        s = str(int(float(s))).zfill(6)
    except Exception:
        pass
    return len(s) == 6 and s.isdigit() and any(s.startswith(p) for p in SSE_ETF_PREFIXES)


def get_exchange_for_etf(code):
    if is_szse_etf_code(code):
        return "SZ"
    if is_sse_etf_code(code):
        return "SS"
    return None


# ============================================================================
# Helpers
# ============================================================================
MONEY_MARKET_KW = (
    "货币", "快线", "快钱", "现金宝", "添利", "理财",
    "债券", "债基", "短融", "国债", "信用", "利率", "纯债",
    "稳健", "增益", "固定",
    "国开", "政金", "地债", "地方债", "进出口", "农发",
)


def is_money_market_etf(name):
    s = str(name)
    return any(k in s for k in MONEY_MARKET_KW)


def apply_split_adjustment(df: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return df

    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    n_rows = len(df)
    cum_factors = np.ones(n_rows, dtype=float)
    daily_factors = np.ones(n_rows, dtype=float)
    close_prevs = np.zeros(n_rows, dtype=float)
    is_split = np.zeros(n_rows, dtype=bool)

    codes = df["code"].values
    closes = df["close"].astype(float).values
    pcts = df["pct_change"].astype(float).values

    n_splits_detected = 0
    start_idx = 0
    cur_code = codes[0]
    for i in range(1, n_rows + 1):
        if i == n_rows or codes[i] != cur_code:
            sub_slice = slice(start_idx, i)
            sub_n = i - start_idx
            if sub_n > 1:
                sub_close = closes[sub_slice]
                sub_pct = pcts[sub_slice]
                raw_ret = np.concatenate([[0.0], np.diff(sub_close) / np.where(sub_close[:-1] == 0, np.nan, sub_close[:-1])])
                szse_ret = sub_pct / 100.0
                raw_ret = np.nan_to_num(raw_ret, nan=0.0, posinf=0.0, neginf=0.0)
                szse_ret = np.nan_to_num(szse_ret, nan=0.0, posinf=0.0, neginf=0.0)
                d_factor = np.where(
                    np.abs(raw_ret - szse_ret) > 0.002,
                    (1.0 + szse_ret) / (1.0 + raw_ret),
                    1.0,
                )
                d_factor[0] = 1.0
                sub_factor = np.cumprod(d_factor)
                cum_factors[sub_slice] = sub_factor
                daily_factors[sub_slice] = d_factor
                close_prevs[sub_slice.start + 1 : sub_slice.stop] = sub_close[:-1]
                diff_mask = np.abs(np.log(np.maximum(d_factor, 1e-12))) > 1e-3
                is_split[sub_slice] = diff_mask
                n_splits_detected += int(diff_mask.sum())
            else:
                cum_factors[sub_slice] = 1.0
                daily_factors[sub_slice] = 1.0
                is_split[sub_slice] = False
            if i < n_rows:
                start_idx = i
                cur_code = codes[i]

    df["cum_split_factor"] = cum_factors
    df["is_split_event_day"] = is_split.astype(int)

    DIV_FACTOR_TOL = 0.15
    evt_mask = is_split.astype(bool)
    abs_dev = np.abs(daily_factors - 1.0)
    is_div_like = evt_mask & (abs_dev < DIV_FACTOR_TOL)
    is_split_like = evt_mask & ~is_div_like

    prev_close_arr = df["prev_close"].astype(float).values
    D_from_szse = np.where(
        evt_mask & (close_prevs > 0),
        close_prevs - prev_close_arr,
        0.0,
    )
    df["implied_dividend_per_share"] = np.where(is_div_like, np.round(D_from_szse, 6), 0.0)

    df["cum_dividend_per_share"] = df.groupby("code", sort=False)["implied_dividend_per_share"].cumsum().round(6)

    act_type = np.full(n_rows, "", dtype=object)
    act_type[is_div_like] = "dividend"
    act_type[is_split_like] = "split_or_conv"
    df["action_type"] = act_type

    cf = df["cum_split_factor"].values
    df["adj_prev_close"] = df["prev_close"].astype(float).values * cf
    df["adj_open"] = df["open"].astype(float).values * cf
    df["adj_high"] = df["high"].astype(float).values * cf
    df["adj_low"] = df["low"].astype(float).values * cf
    df["adj_close"] = df["close"].astype(float).values * cf

    valid = df["close"].astype(float).values > 1e-9
    szse_prevclose_equiv = np.where(
        valid,
        df["adj_close"].values / (1.0 + df["pct_change"].astype(float).values / 100.0),
        df["adj_prev_close"].values,
    )
    use_equiv = np.zeros(n_rows, dtype=bool)
    cur_code = codes[0]
    start_idx = 0
    for i in range(1, n_rows + 1):
        if i == n_rows or codes[i] != cur_code:
            if i - start_idx > 1:
                use_equiv[start_idx + 1 : i] = True
            if i < n_rows:
                start_idx = i
                cur_code = codes[i]
    df.loc[use_equiv & valid, "adj_prev_close"] = szse_prevclose_equiv[use_equiv & valid]

    for col in ["adj_prev_close", "adj_open", "adj_high", "adj_low", "adj_close"]:
        df[col] = df[col].round(6)

    col_order = list(df.columns)
    block_tail = [
        "cum_split_factor", "is_split_event_day",
        "action_type", "implied_dividend_per_share", "cum_dividend_per_share",
        "adj_prev_close", "adj_open", "adj_high", "adj_low", "adj_close",
    ]
    for col in block_tail:
        if col in col_order:
            col_order.remove(col)
    anchor = "pct_change"
    if anchor in col_order:
        pos = col_order.index(anchor) + 1
    else:
        pos = len(col_order)
    col_order[pos:pos] = block_tail
    df = df[col_order]

    if verbose:
        n_etfs_affected = int(df.loc[df["is_split_event_day"] == 1, "code"].nunique())
        n_div = int(is_div_like.sum())
        n_split = int(is_split_like.sum())
        print(f"    [CORP-ADJ] detected {n_splits_detected} corp-action days "
              f"({n_div} dividend-like, {n_split} split/conv) across {n_etfs_affected} ETFs; "
              f"added adj_* OHLC + dividend columns", flush=True)

    return df


# ============================================================================
# Composition: read finished per-file CSVs produced by download_szse_etf_composition.py
# ============================================================================
COMBINED_COLS = [
    "trade_date", "etf_code", "etf_name", "fund_type", "target_index",
    "nav_per_unit", "min_unit_nav",
    "stock_code", "stock_name", "shares", "cash_sub_flag", "market",
]


def build_composition(verbose=True):
    """Read all per-file composition CSVs and return (comp_long, comp_universe).

    No CSV output — caller inserts directly to database.
    """
    files = sorted(glob.glob(os.path.join(COMP_DIR, "szse_etf_comp_*.csv")))
    if verbose:
        print(f"    [COMP] {len(files)} per-file CSVs in {COMP_DIR}", flush=True)

    counts = Counter()
    dfs = []
    for path in files:
        try:
            df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        except Exception:
            counts["failed"] += 1
            continue
        if df is None or len(df) == 0:
            counts["failed"] += 1
            continue
        counts["parsed"] += 1
        counts["holdings"] += len(df)
        dfs.append(df)

    if not dfs:
        print("    [WARN] No holdings read from any per-file CSV "
              "(run download_szse_etf_composition.py to generate them)", flush=True)
        return pd.DataFrame(columns=COMBINED_COLS), pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True)
    for c in COMBINED_COLS:
        if c not in combined.columns:
            combined[c] = None
    combined = combined[COMBINED_COLS]
    combined["trade_date"] = pd.to_datetime(combined["trade_date"], errors="coerce")
    for c in ("nav_per_unit", "min_unit_nav", "shares"):
        combined[c] = pd.to_numeric(combined[c], errors="coerce")
    combined = combined.sort_values(["etf_code", "trade_date", "stock_code"]).reset_index(drop=True)

    if verbose:
        print(f"    [COMP] {len(combined):,} rows, {combined['etf_code'].nunique()} ETFs, "
              f"{combined['trade_date'].dt.strftime('%Y-%m-%d').nunique()} dates", flush=True)

    universe_rows = []
    for code, sub in combined.groupby("etf_code"):
        sub_sorted = sub.sort_values("trade_date")
        latest_date = sub_sorted["trade_date"].iloc[-1]
        latest = sub_sorted[sub_sorted["trade_date"] == latest_date]
        name = str(sub_sorted["etf_name"].dropna().iloc[0]) if len(sub_sorted) else ""
        ftype = str(sub_sorted["fund_type"].dropna().iloc[0]) if len(sub_sorted) else ""
        tidx = str(sub_sorted["target_index"].dropna().iloc[0]) if len(sub_sorted) else ""
        non_empty = sub_sorted.loc[sub_sorted["target_index"].astype(str).str.strip() != "", "target_index"]
        if len(non_empty):
            tidx = str(non_empty.iloc[-1])
        universe_rows.append({
            "etf_code":           code,
            "etf_name":           name,
            "fund_type":          ftype,
            "target_index":       tidx,
            "n_dates":            int(sub_sorted["trade_date"].dt.strftime("%Y-%m-%d").nunique()),
            "latest_date":        latest_date.strftime("%Y-%m-%d") if pd.notna(latest_date) else "",
            "n_holdings_latest":  int(len(latest)),
            "n_equity_latest":    int((latest["cash_sub_flag"] != "必须").sum()),
        })
    universe = pd.DataFrame(universe_rows).sort_values("etf_code").reset_index(drop=True)

    print(f"    [STATS] parsed={counts['parsed']} failed={counts['failed']} "
          f"total_holdings={counts['holdings']:,}", flush=True)
    return combined, universe


# ============================================================================
# Index composition: read CSI index closeweight CSVs
# ============================================================================
def build_index_composition_rows(verbose=True):
    """Read CSI index composition CSVs and build rows for stats.sec_composition.

    Returns a list of dicts with keys:
      snapshot_date, code, source_type, rank, stock_code, stock_name, weight_pct
    """
    if not os.path.isdir(INDEX_COMP_DIR):
        if verbose:
            print(f"    [INDEX-COMP] dir not found: {INDEX_COMP_DIR}", flush=True)
        return []

    files = sorted(glob.glob(os.path.join(INDEX_COMP_DIR, "*_closeweight_*.csv")))
    if not files:
        if verbose:
            print(f"    [INDEX-COMP] no CSVs found in {INDEX_COMP_DIR}", flush=True)
        return []

    if verbose:
        print(f"    [INDEX-COMP] {len(files)} CSV files in {INDEX_COMP_DIR}", flush=True)

    dfs = []
    for path in files:
        try:
            df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue
        dfs.append(df)

    if not dfs:
        return []

    combined = pd.concat(dfs, ignore_index=True)
    for c in ("snapshot_date", "index_code", "stock_code", "stock_name", "weight_pct"):
        if c not in combined.columns:
            if verbose:
                print(f"    [INDEX-COMP] WARN: missing column '{c}'", flush=True)
            return []
    combined["weight_pct"] = pd.to_numeric(combined["weight_pct"], errors="coerce").fillna(0.0)
    combined = combined.sort_values(
        ["index_code", "snapshot_date", "weight_pct"],
        ascending=[True, True, False],
    ).reset_index(drop=True)

    rows = []
    for (index_code, snap_date), sub in combined.groupby(["index_code", "snapshot_date"]):
        snap_date_str = str(snap_date).strip()
        try:
            snap_date_obj = datetime.datetime.strptime(snap_date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        for rank_idx, (_, r) in enumerate(sub.iterrows(), start=1):
            sc = str(r.get("stock_code", "")).strip()
            sc_stripped = sc.split(".")[0].zfill(6)
            if len(sc_stripped) != 6 or not sc_stripped.isdigit():
                continue
            rows.append({
                "snapshot_date": snap_date_obj,
                "code": str(index_code).strip().zfill(6),
                "source_type": "index",
                "rank": rank_idx,
                "stock_code": sc,
                "stock_name": str(r.get("stock_name", "") or ""),
                "weight_pct": float(r["weight_pct"]),
            })

    if verbose:
        n_indices = combined["index_code"].nunique()
        n_dates = combined["snapshot_date"].nunique()
        print(f"    [INDEX-COMP] {len(rows):,} rows from {n_indices} indices, "
              f"{n_dates} snapshot dates", flush=True)
    return rows


# ============================================================================
# SZSE index composition: read SZSE index composition CSVs
# ============================================================================
def build_szse_index_composition_rows(verbose=True):
    """Read SZSE index composition CSVs and build rows for stats.sec_composition.

    Reads files from temps/szse_index_composition/ which are produced by
    download_szse_index_composition.py. These contain the latest constituent
    stocks for SZSE indices like 399001 (深证成指), 399006 (创业板指), and
    399237 (运输指数), with weights computed from float shares.

    Returns a list of dicts with keys:
      snapshot_date, code, source_type, rank, stock_code, stock_name, weight_pct
    """
    if not os.path.isdir(SZSE_INDEX_COMP_DIR):
        if verbose:
            print(f"    [SZSE-INDEX-COMP] dir not found: {SZSE_INDEX_COMP_DIR}", flush=True)
        return []

    files = sorted(glob.glob(os.path.join(SZSE_INDEX_COMP_DIR, "*_closeweight_*.csv")))
    if not files:
        if verbose:
            print(f"    [SZSE-INDEX-COMP] no CSVs found in {SZSE_INDEX_COMP_DIR}", flush=True)
        return []

    if verbose:
        print(f"    [SZSE-INDEX-COMP] {len(files)} CSV files in {SZSE_INDEX_COMP_DIR}", flush=True)

    dfs = []
    for path in files:
        try:
            df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue
        dfs.append(df)

    if not dfs:
        return []

    combined = pd.concat(dfs, ignore_index=True)
    for c in ("snapshot_date", "index_code", "stock_code", "stock_name", "weight_pct"):
        if c not in combined.columns:
            if verbose:
                print(f"    [SZSE-INDEX-COMP] WARN: missing column '{c}'", flush=True)
            return []
    combined["weight_pct"] = pd.to_numeric(combined["weight_pct"], errors="coerce").fillna(0.0)
    combined = combined.sort_values(
        ["index_code", "snapshot_date", "weight_pct"],
        ascending=[True, True, False],
    ).reset_index(drop=True)

    rows = []
    for (index_code, snap_date), sub in combined.groupby(["index_code", "snapshot_date"]):
        snap_date_str = str(snap_date).strip()
        try:
            snap_date_obj = datetime.datetime.strptime(snap_date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        for rank_idx, (_, r) in enumerate(sub.iterrows(), start=1):
            sc = str(r.get("stock_code", "")).strip()
            sc_stripped = sc.split(".")[0].zfill(6)
            if len(sc_stripped) != 6 or not sc_stripped.isdigit():
                continue
            rows.append({
                "snapshot_date": snap_date_obj,
                "code": str(index_code).strip().zfill(6),
                "source_type": "index",
                "rank": rank_idx,
                "stock_code": sc,
                "stock_name": str(r.get("stock_name", "") or ""),
                "weight_pct": float(r["weight_pct"]),
            })

    if verbose:
        n_indices = combined["index_code"].nunique()
        n_dates = combined["snapshot_date"].nunique()
        print(f"    [SZSE-INDEX-COMP] {len(rows):,} rows from {n_indices} indices, "
              f"{n_dates} snapshot dates", flush=True)
    return rows


# ============================================================================
# Build OHLCV long DataFrame (FULL history — no date filter, needed for
# split adjustment + MA correctness)
# ============================================================================
def _scan_ohlcv_dir(scan_dir, file_prefix, market, files=None):
    if files is None:
        pattern = os.path.join(scan_dir, f"{file_prefix}*.csv")
        files = sorted(glob.glob(pattern))
    else:
        files = [f for f in files if os.path.basename(f).startswith(file_prefix)]
    rows = []
    n_empty = 0
    n_ok = 0
    for path in files:
        ymd = ymd_from_filename(path, file_prefix)
        if not ymd:
            continue
        xlsx_path = str(Path(path).with_suffix(".xlsx"))
        try:
            df = read_csv_preferred(xlsx_path, dtype={"证券代码": str, "证券简称": str})
        except Exception:
            continue
        if df is None or len(df) == 0:
            n_empty += 1
            continue
        first_cell = str(df.iloc[0, 0]) if len(df) else ""
        if "没有找到" in first_cell or "无数据" in first_cell:
            n_empty += 1
            continue
        if "证券代码" not in df.columns:
            continue
        df["_code"] = df["证券代码"].astype(str).str.strip()
        df["_code"] = df["_code"].apply(
            lambda s: str(int(float(s))).zfill(6) if re.fullmatch(r"\d+(\.0+)?", s or "") else s
        )
        df["_code_base"] = df["_code"].str.split(".").str[0]

        if market == "深圳":
            etf_mask = df["_code_base"].str.startswith(SZSE_ETF_PREFIXES) & (df["_code_base"].str.len() == 6)
        else:
            etf_mask = df["_code_base"].str.startswith(SSE_ETF_PREFIXES) & (df["_code_base"].str.len() == 6)
        df = df[etf_mask].copy()
        if len(df) == 0:
            continue
        df["_mm"] = df["证券简称"].astype(str).apply(is_money_market_etf)
        df = df[~df["_mm"]].copy()
        if len(df) == 0:
            continue
        date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        volume_col = "成交量（万份）" if market == "深圳" else "成交量(万股)"
        amount_col = "成交金额(万元)"
        for _, r in df.iterrows():
            code = r["_code"]
            exchange = "SZ" if market == "深圳" else "SS"
            code_with_suffix = code if "." in code else f"{code}.{exchange}"
            rows.append({
                "date":         date_str,
                "code":         code_with_suffix,
                "name":         str(r.get("证券简称", "")).strip(),
                "prev_close":   parse_num(r.get("前收")),
                "open":         parse_num(r.get("开盘")),
                "high":         parse_num(r.get("最高")),
                "low":          parse_num(r.get("最低")),
                "close":        parse_num(r.get("今收")),
                "pct_change":   parse_num(r.get("涨跌幅（%）")),
                "volume_wan":   parse_num(r.get(volume_col)),
                "amount_wan":   parse_num(r.get(amount_col)),
            })
        n_ok += 1
    return rows, n_ok, n_empty, len(files)


def build_ohlcv_df(verbose=True, ohlcv_files=None):
    """Read OHLCV source CSVs and return a long DataFrame.

    Args:
        ohlcv_files: if provided, a dict with keys "szse_archive", "szse_trend",
                     "sse_trend" mapping to lists of file paths. Only these
                     files are read (incremental mode — caller already filtered
                     to missing dates via DB query). If None, glob all files
                     in the source directories (--force mode).

    Split adjustment and MA computation require the full per-code chronological
    history. In incremental mode, the caller queries the DB for existing data
    and concatenates it with the new rows before applying split/MA.
    """
    rows_a, ok_a, empty_a, tot_a = [], 0, 0, 0
    rows_t, ok_t, empty_t, tot_t = [], 0, 0, 0
    rows_sse, ok_sse, empty_sse, tot_sse = [], 0, 0, 0

    if ohlcv_files is not None:
        # Incremental: read only the provided (missing-date) files
        fa = ohlcv_files.get("szse_archive", [])
        if fa:
            rows_a, ok_a, empty_a, tot_a = _scan_ohlcv_dir(
                SZSE_ARCHIVE_DIR, "szse_etf_", "深圳", files=fa)
            if verbose:
                print(f"    [OHLCV-szse-archive] read {tot_a} files  {ok_a} ok  {empty_a} empty  {len(rows_a)} rows", flush=True)
        ft = ohlcv_files.get("szse_trend", [])
        if ft:
            rows_t, ok_t, empty_t, tot_t = _scan_ohlcv_dir(
                SZSE_TREND_DIR, "szse_trend_etf_", "深圳", files=ft)
            if verbose:
                print(f"    [OHLCV-szse-trend] read {tot_t} files  {ok_t} ok  {empty_t} empty  {len(rows_t)} rows", flush=True)
        fs = ohlcv_files.get("sse_trend", [])
        if fs:
            rows_sse, ok_sse, empty_sse, tot_sse = _scan_ohlcv_dir(
                SSE_TREND_DIR, "sse_trend_stock_", "上海", files=fs)
            if verbose:
                if len(rows_sse) == 0:
                    print(f"      → [INFO] SSE trend files contain stocks only (600/601/603/605/688 prefixes), not ETFs.", flush=True)
                print(f"      → read {tot_sse} files  {ok_sse} ok  {empty_sse} empty  {len(rows_sse)} rows", flush=True)
    else:
        # --force mode: glob all files in source directories
        if os.path.isdir(SZSE_ARCHIVE_DIR):
            if verbose:
                print(f"    [OHLCV-szse-archive] scanning {SZSE_ARCHIVE_DIR}", flush=True)
            rows_a, ok_a, empty_a, tot_a = _scan_ohlcv_dir(SZSE_ARCHIVE_DIR, "szse_etf_", "深圳")
            if verbose:
                print(f"      → scanned {tot_a} files  {ok_a} ok  {empty_a} empty  {len(rows_a)} rows", flush=True)
        if os.path.isdir(SZSE_TREND_DIR):
            if verbose:
                print(f"    [OHLCV-szse-trend] scanning {SZSE_TREND_DIR}", flush=True)
            rows_t, ok_t, empty_t, tot_t = _scan_ohlcv_dir(SZSE_TREND_DIR, "szse_trend_etf_", "深圳")
            if verbose:
                print(f"      → scanned {tot_t} files  {ok_t} ok  {empty_t} empty  {len(rows_t)} rows", flush=True)
        if os.path.isdir(SSE_TREND_DIR):
            if verbose:
                print(f"    [OHLCV-sse-trend] scanning {SSE_TREND_DIR}", flush=True)
            rows_sse, ok_sse, empty_sse, tot_sse = _scan_ohlcv_dir(SSE_TREND_DIR, "sse_trend_stock_", "上海")
            if verbose:
                if len(rows_sse) == 0:
                    print(f"      → [INFO] SSE trend files contain stocks only (600/601/603/605/688 prefixes), not ETFs.", flush=True)
                print(f"      → scanned {tot_sse} files  {ok_sse} ok  {empty_sse} empty  {len(rows_sse)} rows", flush=True)

    all_rows = rows_a + rows_t + rows_sse
    if verbose:
        print(f"    [OHLCV] total raw rows: {len(all_rows)}", flush=True)
    if not all_rows:
        return pd.DataFrame()
    out = pd.DataFrame(all_rows)
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date"])
    out = out.sort_values(["date", "code", "volume_wan"], kind="mergesort")
    out = out.drop_duplicates(subset=["date", "code"], keep="last")
    out = out.sort_values(["code", "date"]).reset_index(drop=True)

    # ------------------------------------------------------------------
    # Normalize 1000x amount error in SZSE/SSE source data.
    #
    # The SZSE archive .xlsx reports 成交金额(万元) in MIXED units: some
    # rows have amount in 万元 (correct), others have amount = vol_wan ×
    # close (1000× too big). The SSE trend has the same issue for some
    # securities. The discriminator is amt / (vol × close):
    #   SZSE close is in 千分位 (price_yuan × 1000):
    #     correct ratio ≈ 0.001, bad ratio ≈ 1.0  → threshold 0.1
    #   SSE close is in 元 (price_yuan):
    #     correct ratio ≈ 1.0, bad ratio ≈ 1000  → threshold 100
    # When bad, amount_wan is divided by 1000 to convert to proper 万元.
    # ------------------------------------------------------------------
    _vol = pd.to_numeric(out["volume_wan"], errors="coerce")
    _cls = pd.to_numeric(out["close"], errors="coerce")
    _amt = pd.to_numeric(out["amount_wan"], errors="coerce")
    _ratio = _amt / (_vol * _cls)
    _szse = out["code"].str.endswith(".SZ")
    _bad_szse = _szse & (_ratio > 0.1) & (_vol > 0) & (_cls > 0) & (_amt > 0)
    _bad_sse = (~_szse) & (_ratio > 100) & (_vol > 0) & (_cls > 0) & (_amt > 0)
    _bad_mask = _bad_szse | _bad_sse
    _n_fixed = int(_bad_mask.sum())
    if _n_fixed > 0:
        out.loc[_bad_mask, "amount_wan"] = out.loc[_bad_mask, "amount_wan"] / 1000.0
        if verbose:
            print(f"    [AMT-FIX] normalized {_n_fixed:,} rows with 1000× amount error "
                  f"(SZSE: {int(_bad_szse.sum()):,}, SSE: {int(_bad_sse.sum()):,})", flush=True)

    if verbose:
        n_szse = out["code"].str.endswith(".SZ").sum()
        n_sse = out["code"].str.endswith(".SS").sum()
        print(f"    [OHLCV] final rows: {len(out):,}  "
              f"unique codes: {out['code'].nunique()}  "
              f"SZSE (.SZ): {n_szse:,}  SSE (.SS): {n_sse:,}  "
              f"date range: {out['date'].min().date()} → {out['date'].max().date()}", flush=True)
    return out


# ============================================================================
# Build margin long DataFrame (FULL history — needed for sec_classification has_margin)
# ============================================================================
def _scan_margin_dir(scan_dir, file_prefix, market, verbose=True, files=None):
    if files is None:
        pattern = os.path.join(scan_dir, f"{file_prefix}*.csv")
        files = sorted(glob.glob(pattern))
    else:
        files = [f for f in files if os.path.basename(f).startswith(file_prefix)]
    if verbose:
        print(f"    [MARGIN-{market}] reading {len(files)} {file_prefix}*.csv files", flush=True)

    rows = []
    n_empty = 0
    n_ok = 0
    for path in files:
        ymd = ymd_from_filename(path, file_prefix)
        if not ymd:
            continue
        xlsx_path = str(Path(path).with_suffix(".xlsx"))
        try:
            df = read_csv_preferred(xlsx_path, dtype={"证券代码": str, "证券简称": str})
        except Exception:
            continue
        if df is None or len(df) == 0:
            n_empty += 1
            continue
        first_cell = str(df.iloc[0, 0]) if len(df) else ""
        if "没有找到" in first_cell or "无数据" in first_cell:
            n_empty += 1
            continue
        if "证券代码" not in df.columns:
            continue
        df["_code"] = df["证券代码"].astype(str).str.strip()
        df["_code"] = df["_code"].apply(
            lambda s: str(int(float(s))).zfill(6) if re.fullmatch(r"\d+(\.0+)?", s or "") else s
        )
        df["_code_base"] = df["_code"].str.split(".").str[0]

        if market == "深圳":
            etf_mask = df["_code_base"].str.startswith(SZSE_ETF_PREFIXES) & (df["_code_base"].str.len() == 6)
            default_suffix = ".SZ"
        else:
            etf_mask = df["_code_base"].str.startswith(SSE_ETF_PREFIXES) & (df["_code_base"].str.len() == 6)
            default_suffix = ".SS"
        df = df[etf_mask].copy()
        if len(df) == 0:
            continue
        date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        for _, r in df.iterrows():
            code = r["_code"]
            code_with_suffix = code if "." in code else f"{code}{default_suffix}"
            rows.append({
                "date":           date_str,
                "code":           code_with_suffix,
                "rz_buy":         parse_num(r.get("融资买入额(元)")),
                "rz_balance":     parse_num(r.get("融资余额(元)")),
                "rq_sell_qty":    parse_num(r.get("融券卖出量(股/份)")),
                "rq_balance_qty": parse_num(r.get("融券余量(股/份)")),
                "rq_balance_amt": parse_num(r.get("融券余额(元)")),
                "total_balance":  parse_num(r.get("融资融券余额(元)")),
            })
        n_ok += 1
    if verbose:
        print(f"    [MARGIN-{market}] {n_ok} files with data, {n_empty} empty, {len(rows)} rows", flush=True)
    return rows, n_ok, n_empty


def build_margin_df(verbose=True, margin_files=None):
    """Read margin source CSVs and return a long DataFrame.

    Args:
        margin_files: if provided, a dict with keys "szse", "sse" mapping to
                      lists of file paths. Only these files are read (incremental
                      mode). If None, glob all files (--force mode).
    """
    all_rows = []
    n_ok_total = 0
    n_empty_total = 0

    if margin_files is not None:
        if margin_files.get("szse"):
            rows_szse, ok_szse, empty_szse = _scan_margin_dir(
                SZSE_MARGIN_DIR, "szse_margin_detail_", "深圳", verbose,
                files=margin_files["szse"])
            all_rows.extend(rows_szse)
            n_ok_total += ok_szse
            n_empty_total += empty_szse
        if margin_files.get("sse"):
            rows_sse, ok_sse, empty_sse = _scan_margin_dir(
                SSE_MARGIN_DIR, "sse_margin_detail_", "上海", verbose,
                files=margin_files["sse"])
            all_rows.extend(rows_sse)
            n_ok_total += ok_sse
            n_empty_total += empty_sse
    else:
        if os.path.isdir(SZSE_MARGIN_DIR):
            rows_szse, ok_szse, empty_szse = _scan_margin_dir(
                SZSE_MARGIN_DIR, "szse_margin_detail_", "深圳", verbose)
            all_rows.extend(rows_szse)
            n_ok_total += ok_szse
            n_empty_total += empty_szse
        if os.path.isdir(SSE_MARGIN_DIR):
            rows_sse, ok_sse, empty_sse = _scan_margin_dir(
                SSE_MARGIN_DIR, "sse_margin_detail_", "上海", verbose)
            all_rows.extend(rows_sse)
            n_ok_total += ok_sse
            n_empty_total += empty_sse

    if not all_rows:
        if verbose:
            print(f"    [MARGIN] total: {n_ok_total} files with data, {n_empty_total} empty, {len(all_rows)} rows", flush=True)
        return pd.DataFrame()

    out = pd.DataFrame(all_rows)
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date"])

    margin_cols = ["rz_buy", "rz_balance", "rq_sell_qty", "rq_balance_qty", "rq_balance_amt", "total_balance"]
    for c in margin_cols:
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0)

    n_before = len(out)
    out = out.groupby(["date", "code"], as_index=False)[margin_cols].sum()
    n_after = len(out)
    n_merged = n_before - n_after

    if verbose:
        print(f"    [MARGIN] total: {n_ok_total} files with data, {n_empty_total} empty, "
              f"{n_before} raw rows → {n_after} merged rows ({n_merged} duplicates handled)", flush=True)

    out = out.sort_values(["code", "date"]).reset_index(drop=True)
    return out


_BUILD_THEME_RULE_ORDER = {tid: i for i, (tid, _, _, _) in enumerate(ETF_THEME_RULES)}


def classify_etf_theme(name):
    s = str(name)
    best = None
    best_score = None
    for tid, label, slug, kws in ETF_THEME_RULES:
        hits = [kw for kw in kws if kw in s]
        if not hits:
            continue
        n_hits = len(hits)
        total_len = sum(len(k) for k in hits)
        longest_kw = max(len(k) for k in hits)
        rule_order = _BUILD_THEME_RULE_ORDER.get(tid, 9999)
        score = (total_len, n_hits, longest_kw, -rule_order)
        if best_score is None or score > best_score:
            best_score = score
            best = (tid, label, slug)
    if best is not None:
        return best
    return "OTHER", "其他｜未分类  Unclassified", "other"


# ============================================================================
# Query existing OHLCV + margin from the database (replaces reading full
# source CSV history — the DB is indexed and much faster than scanning
# 1400+ CSV files). Used in incremental mode to get historical context for
# split adjustment + MA computation, concatenated with missing-date CSVs.
# ============================================================================
async def query_existing_ohlcv_margin_from_db(conn, verbose=True):
    """Query all existing OHLCV + margin data from the database.

    Returns (ohlcv_df, margin_df) with the same column schemas as
    build_ohlcv_df() and build_margin_df() so they can be concatenated
    with new (missing-date) source data before applying split/MA.
    """
    if verbose:
        print("    [DB] Querying existing OHLCV + margin from database …", flush=True)

    rows = await conn.fetch("""
        SELECT
            i.date, i.code, i.name,
            b.prev_close, b.open, b.high, b.low, b.close, b.pct_change,
            COALESCE(l.volume_wan, 0)   AS volume_wan,
            COALESCE(l.amount_wan, 0)   AS amount_wan,
            COALESCE(l.rz_buy, 0)       AS rz_buy,
            COALESCE(l.rz_balance, 0)   AS rz_balance,
            COALESCE(l.rq_sell_qty, 0)  AS rq_sell_qty,
            COALESCE(l.rq_balance_qty, 0) AS rq_balance_qty,
            COALESCE(l.rq_balance_amt, 0) AS rq_balance_amt,
            COALESCE(l.total_balance, 0) AS total_balance
        FROM stats.etf_identity i
        JOIN stats.etf_basic_stats b ON b.date = i.date AND b.code = i.code
        LEFT JOIN stats.etf_liquidity_margin l ON l.date = i.date AND l.code = i.code
        ORDER BY i.code, i.date
    """)

    if not rows:
        if verbose:
            print("    [DB] No existing OHLCV data found", flush=True)
        return pd.DataFrame(), pd.DataFrame()

    df = pd.DataFrame([dict(r) for r in rows])
    # Use astype("datetime64[us]") to match CSV-parsed dates (pandas 2.x
    # defaults to us for string parsing, but datetime.date objects from
    # asyncpg produce datetime64[s], causing merge_asof dtype mismatch)
    df["date"] = pd.to_datetime(df["date"]).astype("datetime64[us]")
    # asyncpg returns NUMERIC columns as decimal.Decimal, which makes the
    # DataFrame columns object-dtype. When concatenated with CSV-sourced
    # rows (float64), the mixed Decimal+float column stays object-dtype and
    # breaks downstream numeric aggregations (e.g. groupby().mean() on
    # volume_wan). Coerce all numeric columns to float64 up front so the
    # DB-sourced frame matches the CSV-sourced frame's dtypes.
    for _nc in ["prev_close", "open", "high", "low", "close", "pct_change",
                "volume_wan", "amount_wan", "rz_buy", "rz_balance",
                "rq_sell_qty", "rq_balance_qty", "rq_balance_amt",
                "total_balance"]:
        if _nc in df.columns:
            df[_nc] = pd.to_numeric(df[_nc], errors="coerce")

    ohlcv_cols = ["date", "code", "name", "prev_close", "open", "high", "low",
                  "close", "pct_change", "volume_wan", "amount_wan"]
    margin_cols = ["date", "code", "rz_buy", "rz_balance", "rq_sell_qty",
                   "rq_balance_qty", "rq_balance_amt", "total_balance"]

    ohlcv_df = df[ohlcv_cols].copy()

    # Margin: only keep rows with actual margin activity
    margin_df = df[margin_cols].copy()
    margin_mask = (
        (margin_df["rz_balance"] > 0) |
        (margin_df["rq_balance_qty"] > 0) |
        (margin_df["total_balance"] > 0)
    )
    margin_df = margin_df[margin_mask].reset_index(drop=True)

    if verbose:
        n_codes = ohlcv_df["code"].nunique()
        n_dates = ohlcv_df["date"].dt.strftime("%Y-%m-%d").nunique()
        d0 = ohlcv_df["date"].min().date()
        d1 = ohlcv_df["date"].max().date()
        print(f"    [DB] OHLCV: {len(ohlcv_df):,} rows | {n_codes} codes | "
              f"{n_dates} dates | {d0} → {d1}", flush=True)
        print(f"    [DB] Margin: {len(margin_df):,} rows with margin activity", flush=True)

    return ohlcv_df, margin_df


# ============================================================================
# Main pipeline
# ============================================================================
async def main():
    ap = argparse.ArgumentParser(
        description="Build SZSE + SSE ETF + margin + composition and insert to database (missing-data-only)."
    )
    add_common_build_args(ap)
    args = ap.parse_args()

    t0 = time.time()
    print_build_header(
        "BUILD SZSE + SSE ETF + MARGIN + COMPOSITION  ·  missing-data-only → DATABASE",
        **{
            "SZSE Archive dir": SZSE_ARCHIVE_DIR,
            "SZSE Trend dir":   SZSE_TREND_DIR,
            "Margin dir":       SZSE_MARGIN_DIR,
            "Composition dir":  COMP_DIR,
            "Index comp dir":   INDEX_COMP_DIR,
            "Date range":       f"{args.start_date or '(all)'} → {args.end_date or '(all)'}",
            "Today":            TODAY_STR,
        }
    )

    # ------------------------------------------------------------------
    # (1) Discover source files (fast — filenames only, no reading)
    # ------------------------------------------------------------------
    print("\n[1/6] Discovering source CSV files …", flush=True)
    szse_archive_files = glob_source_files(SZSE_ARCHIVE_DIR, "szse_etf_*.csv")
    szse_trend_files   = glob_source_files(SZSE_TREND_DIR, "szse_trend_etf_*.csv")
    sse_trend_files    = glob_source_files(SSE_TREND_DIR, "sse_trend_stock_*.csv")
    szse_margin_files  = glob_source_files(SZSE_MARGIN_DIR, "szse_margin_detail_*.csv")
    sse_margin_files   = glob_source_files(SSE_MARGIN_DIR, "sse_margin_detail_*.csv")

    # Extract available OHLCV dates from filenames
    available_ohlcv_dates = set()
    for f in szse_archive_files:
        ymd = ymd_from_filename(f, "szse_etf_")
        if ymd:
            d = ymd_to_date(ymd)
            if d:
                available_ohlcv_dates.add(d)
    for f in szse_trend_files:
        ymd = ymd_from_filename(f, "szse_trend_etf_")
        if ymd:
            d = ymd_to_date(ymd)
            if d:
                available_ohlcv_dates.add(d)

    print(f"    → OHLCV: {len(szse_archive_files)} szse_archive + "
          f"{len(szse_trend_files)} szse_trend + {len(sse_trend_files)} sse_trend files", flush=True)
    print(f"    → Margin: {len(szse_margin_files)} szse + {len(sse_margin_files)} sse files", flush=True)
    print(f"    → {len(available_ohlcv_dates)} unique OHLCV dates available in source files", flush=True)

    # ------------------------------------------------------------------
    # (2) Connect to DB and find missing dates
    # ------------------------------------------------------------------
    print("\n[2/6] Connecting to database and detecting missing dates …", flush=True)
    conn = await get_db_or_exit()

    try:
        if args.force:
            print("    [DB] Force mode: truncating existing tables", flush=True)
            await truncate_table_async(conn, "stats.etf_identity")
            await truncate_table_async(conn, "stats.sec_composition")
            # NOTE: do NOT truncate stats.sec_classification here — it holds both ETF and
            # index rows. ETF classification + index_code come from
            # build_etf_index_map.py; only quality metrics are refreshed below.
            existing_keys = set()
            existing_dates = set()
        else:
            existing_keys = await get_existing_keys_async(
                conn, "stats.etf_identity", ["date", "code"]
            )
            existing_dates = {d for (d, _c) in existing_keys}

        missing_ohlcv_dates = available_ohlcv_dates - existing_dates
        print(f"    [DB] {len(existing_keys):,} existing (date, code) pairs in stats.etf_identity", flush=True)
        print(f"    [DB] {len(missing_ohlcv_dates)} dates missing "
              f"(out of {len(available_ohlcv_dates)} available)", flush=True)

        # ------------------------------------------------------------------
        # (3) Read ONLY missing-date source CSVs + query DB for historical context
        #
        # The DB is the source of truth for existing data. We query it by
        # index (etf_identity PK = date+code) to find which dates are missing,
        # then read ONLY the source CSVs for those missing dates. For split
        # adjustment + MA correctness (cross-date dependency), we also query
        # the existing OHLCV+margin from the DB and concatenate with the new
        # data before applying the corp-action algorithm.
        # ------------------------------------------------------------------
        if args.force:
            print("\n[3/6] Reading ALL source CSVs (force mode) …", flush=True)
            ohlcv_df = build_ohlcv_df(verbose=True)
            margin_df = build_margin_df(verbose=True)
        elif not missing_ohlcv_dates:
            print("\n[3/6] OHLCV up to date — querying DB for historical context only …", flush=True)
            ohlcv_df, margin_df = await query_existing_ohlcv_margin_from_db(conn, verbose=True)
        else:
            print(f"\n[3/6] Reading source CSVs for {len(missing_ohlcv_dates)} missing dates "
                  f"+ querying DB for historical context …", flush=True)
            missing_ymd = {d.strftime("%Y%m%d") for d in missing_ohlcv_dates}

            missing_szse_archive = [f for f in szse_archive_files
                                    if ymd_from_filename(f, "szse_etf_") in missing_ymd]
            missing_szse_trend   = [f for f in szse_trend_files
                                    if ymd_from_filename(f, "szse_trend_etf_") in missing_ymd]
            missing_sse_trend    = [f for f in sse_trend_files
                                    if ymd_from_filename(f, "sse_trend_stock_") in missing_ymd]
            missing_szse_margin  = [f for f in szse_margin_files
                                    if ymd_from_filename(f, "szse_margin_detail_") in missing_ymd]
            missing_sse_margin   = [f for f in sse_margin_files
                                    if ymd_from_filename(f, "sse_margin_detail_") in missing_ymd]

            print(f"    → OHLCV files to read: {len(missing_szse_archive)} szse_archive + "
                  f"{len(missing_szse_trend)} szse_trend + {len(missing_sse_trend)} sse_trend", flush=True)
            print(f"    → Margin files to read: {len(missing_szse_margin)} szse + "
                  f"{len(missing_sse_margin)} sse", flush=True)

            ohlcv_file_sets = {
                "szse_archive": missing_szse_archive,
                "szse_trend":   missing_szse_trend,
                "sse_trend":    missing_sse_trend,
            }
            new_ohlcv_df = build_ohlcv_df(verbose=True, ohlcv_files=ohlcv_file_sets)

            margin_file_sets = {
                "szse": missing_szse_margin,
                "sse":  missing_sse_margin,
            }
            new_margin_df = build_margin_df(verbose=True, margin_files=margin_file_sets)

            # Query historical OHLCV + margin from DB (for split/MA correctness)
            hist_ohlcv_df, hist_margin_df = await query_existing_ohlcv_margin_from_db(conn, verbose=True)

            # Combine historical (DB) + new (CSV) — keep last for overlapping keys
            if len(hist_ohlcv_df) and len(new_ohlcv_df):
                ohlcv_df = pd.concat([hist_ohlcv_df, new_ohlcv_df], ignore_index=True)
                ohlcv_df = ohlcv_df.drop_duplicates(subset=["date", "code"], keep="last")
            elif len(new_ohlcv_df):
                ohlcv_df = new_ohlcv_df
            else:
                ohlcv_df = hist_ohlcv_df
            ohlcv_df = ohlcv_df.sort_values(["code", "date"]).reset_index(drop=True)

            if len(hist_margin_df) and len(new_margin_df):
                margin_df = pd.concat([hist_margin_df, new_margin_df], ignore_index=True)
                margin_df = margin_df.drop_duplicates(subset=["date", "code"], keep="last")
            elif len(new_margin_df):
                margin_df = new_margin_df
            else:
                margin_df = hist_margin_df
            if len(margin_df):
                margin_df = margin_df.sort_values(["code", "date"]).reset_index(drop=True)

        if len(ohlcv_df) == 0:
            print("    [FATAL] No OHLCV rows to process — check source files and DB", flush=True)
            sys.exit(1)
        if len(margin_df) == 0:
            print("    [WARN] No margin rows — proceeding with OHLCV only", flush=True)
            margin_df = pd.DataFrame(columns=["date", "code", "rz_buy", "rz_balance",
                                              "rq_sell_qty", "rq_balance_qty",
                                              "rq_balance_amt", "total_balance"])

        # ------------------------------------------------------------------
        # (4) Merge OHLCV + margin, apply split adjustment + MAs (full history)
        # ------------------------------------------------------------------
        print("\n[4/6] Merging OHLCV + margin, applying corp-action adjustment + MAs …", flush=True)
        if len(margin_df):
            merged = ohlcv_df.merge(margin_df, on=["date", "code"], how="left", validate="m:1")
        else:
            merged = ohlcv_df.copy()
            for c in ["rz_buy", "rz_balance", "rq_sell_qty", "rq_balance_qty",
                      "rq_balance_amt", "total_balance"]:
                merged[c] = 0.0
        for c in ["rz_buy", "rz_balance", "rq_sell_qty", "rq_balance_qty",
                  "rq_balance_amt", "total_balance"]:
            if c in merged.columns:
                merged[c] = pd.to_numeric(merged[c], errors="coerce").fillna(0.0)

        # Compute rq_balance_amt for SSE ETFs (quantity × mid price)
        has_rq_qty = "rq_balance_qty" in merged.columns and (merged["rq_balance_qty"] > 0).any()
        has_rq_amt = "rq_balance_amt" in merged.columns
        if has_rq_qty and has_rq_amt:
            sse_mask = merged["code"].str.endswith(".SS")
            missing_rq_amt = sse_mask & (merged["rq_balance_amt"] == 0) & (merged["rq_balance_qty"] > 0)
            if missing_rq_amt.any():
                mid_price = (
                    merged.loc[missing_rq_amt, "open"] + merged.loc[missing_rq_amt, "close"]
                ) / 2.0
                merged.loc[missing_rq_amt, "rq_balance_amt"] = (
                    merged.loc[missing_rq_amt, "rq_balance_qty"] * mid_price
                )
                print(f"    → Filled rq_balance_amt for {missing_rq_amt.sum():,} SSE ETF rows", flush=True)

        # Apply corp-action adjustment (needs full per-code history)
        merged = apply_split_adjustment(merged, verbose=True)

        # Compute MAs (needs full per-code history)
        merged = merged.sort_values(["code", "date"]).reset_index(drop=True)
        merged["ma5"] = merged.groupby("code", sort=False)["adj_close"].transform(
            lambda x: x.rolling(window=5, min_periods=1).mean()
        ).round(6)
        merged["ma5_ratio"] = ((merged["adj_close"] / merged["ma5"]) - 1.0).round(6)
        merged["ma20"] = merged.groupby("code", sort=False)["adj_close"].transform(
            lambda x: x.rolling(window=20, min_periods=1).mean()
        ).round(6)
        merged["ma60"] = merged.groupby("code", sort=False)["adj_close"].transform(
            lambda x: x.rolling(window=60, min_periods=1).mean()
        ).round(6)
        merged["ma120"] = merged.groupby("code", sort=False)["adj_close"].transform(
            lambda x: x.rolling(window=120, min_periods=1).mean()
        ).round(6)
        merged["ma255"] = merged.groupby("code", sort=False)["adj_close"].transform(
            lambda x: x.rolling(window=255, min_periods=1).mean()
        ).round(6)
        print(f"    → MA columns added: ma5, ma5_ratio, ma20, ma60, ma120, ma255", flush=True)

        # ------------------------------------------------------------------
        # (4b) Build composition (for sec_composition insertion)
        # ------------------------------------------------------------------
        print("\n    Building composition …", flush=True)
        comp_long, comp_universe = build_composition(verbose=True)

        # ------------------------------------------------------------------
        # (4c) Build universe (for sec_classification stats — from FULL merged data)
        # ------------------------------------------------------------------
        uni_rows = []
        for code, sub in merged.groupby("code"):
            name = str(sub["name"].dropna().iloc[0]) if sub["name"].notna().any() else ""
            tid, tlabel, tslug = classify_etf_theme(name)
            code_base = strip_exchange_suffix(code)
            has_comp = comp_universe is not None and code_base in comp_universe["etf_code"].values
            n_comp_dates = 0
            n_holdings_latest = 0
            if has_comp:
                cu = comp_universe[comp_universe["etf_code"] == code_base].iloc[0]
                n_comp_dates = int(cu.get("n_dates", 0))
                n_holdings_latest = int(cu.get("n_holdings_latest", 0))
            exchange = "SZ" if code.endswith(".SZ") else "SS"
            uni_rows.append({
                "code":             code,
                "exchange":         exchange,
                "name":             name,
                "n_ohlcv_days":     int(len(sub)),
                "n_margin_days":    int((sub["rz_balance"] > 0).sum()) if "rz_balance" in sub.columns else 0,
                "n_comp_dates":     n_comp_dates,
                "n_holdings_latest": n_holdings_latest,
                "first_date":       sub["date"].min().strftime("%Y-%m-%d") if len(sub) else "",
                "last_date":        sub["date"].max().strftime("%Y-%m-%d") if len(sub) else "",
                "theme_id":         tid,
                "theme_label":      tlabel,
                "theme_slug":       tslug,
            })
        uni_df = pd.DataFrame(uni_rows).sort_values(["theme_id", "n_ohlcv_days"],
                                                     ascending=[True, False])

        # ------------------------------------------------------------------
        # (5) Filter to missing (date, code) pairs and insert OHLCV/margin tables
        #
        # CORP-ACTION RE-SYNC: codes with any detected split/dividend event
        # (is_split_event_day=1 OR cum_split_factor deviates from 1.0) must
        # have ALL their rows re-upserted — not just missing ones. Otherwise
        # the cumulative split factor fails to propagate to rows inserted
        # before the event day was backfilled (e.g. 2022 rows inserted before
        # 2021 history existed → factor stays 1.0 → adj_close gap on chart).
        # ------------------------------------------------------------------
        print("\n[5/6] Filtering to missing (date, code) pairs and inserting …", flush=True)

        # Identify codes whose adjustment factors must be re-synced
        split_affected_codes: set = set(
            merged.loc[merged["is_split_event_day"] == 1, "code"].unique()
        )
        # Also include codes with non-trivial cumulative factor (dividend-like
        # events produce cum_split_factor slightly > 1.0 via cumprod of daily
        # dividend factors). These also need propagation to all rows.
        split_affected_codes |= set(
            merged.loc[merged["cum_split_factor"].abs() - 1.0 > 1e-4, "code"].unique()
        )
        if split_affected_codes:
            print(f"    [CORP-RESYNC] {len(split_affected_codes)} codes with corp-action "
                  f"events — re-upserting ALL their rows (not just missing)", flush=True)

        # Filter merged to missing (date, code) pairs [and within --start/--end range]
        merged_db = merged.copy()
        merged_db["date"] = merged_db["date"].dt.date

        start_d = parse_date(args.start_date) if args.start_date else None
        end_d = parse_date(args.end_date) if args.end_date else None

        def _should_upsert(row):
            d = row["date"]
            if start_d and d < start_d:
                return False
            if end_d and d > end_d:
                return False
            if (d, row["code"]) not in existing_keys:
                return True
            # Re-upsert ALL rows for split-affected codes so the cumulative
            # split factor propagates to previously-inserted rows.
            if row["code"] in split_affected_codes:
                return True
            return False

        missing_mask = merged_db.apply(_should_upsert, axis=1)
        merged_missing = merged_db[missing_mask].reset_index(drop=True)
        print(f"    [DB] {len(merged_missing):,} rows to upsert "
              f"(out of {len(merged_db):,} total, missing + corp-action resync)", flush=True)

        if len(merged_missing) == 0 and not args.force:
            print("    [INFO] etf_identity is up to date — no new OHLCV/margin rows to insert", flush=True)
        else:
            # Dedupe within the batch
            merged_missing = merged_missing.drop_duplicates(subset=["date", "code"], keep="last")

            # Build rows for each split table
            identity_rows, basic_rows, tech_rows = [], [], []
            adj_rows, liq_rows = [], []
            for _, row in merged_missing.iterrows():
                code = str(row["code"])
                suffix = (code.split(".")[-1]
                          if "." in code and code.split(".")[-1] in ("SZ", "SS", "SH")
                          else None)
                identity_rows.append({
                    "date": row["date"],
                    "code": code,
                    "code_suffix": suffix,
                    "name": str(row.get("name", "")) if pd.notna(row.get("name")) else "",
                })
                basic_rows.append({
                    "date": row["date"],
                    "code": row["code"],
                    "prev_close": row.get("prev_close"),
                    "open": row.get("open"),
                    "high": row.get("high"),
                    "low": row.get("low"),
                    "close": row.get("close"),
                    "pct_change": row.get("pct_change"),
                    "is_close_estimated": bool(row.get("is_close_estimated", False)),
                })
                tech_rows.append({
                    "date": row["date"],
                    "code": row["code"],
                    "ma5": row.get("ma5"),
                    "ma5_ratio": row.get("ma5_ratio"),
                    "ma20": row.get("ma20"),
                    "ma60": row.get("ma60"),
                    "ma120": row.get("ma120"),
                    "ma255": row.get("ma255"),
                })
                adj_rows.append({
                    "date": row["date"],
                    "code": row["code"],
                    "cum_split_factor": row.get("cum_split_factor", 1.0),
                    "is_split_event_day": int(row.get("is_split_event_day", 0)),
                    "action_type": row.get("action_type") or None,
                    "implied_dividend_per_share": row.get("implied_dividend_per_share", 0),
                    "cum_dividend_per_share": row.get("cum_dividend_per_share", 0),
                    "adj_prev_close": row.get("adj_prev_close"),
                    "adj_open": row.get("adj_open"),
                    "adj_high": row.get("adj_high"),
                    "adj_low": row.get("adj_low"),
                    "adj_close": row.get("adj_close"),
                })
                liq_rows.append({
                    "date": row["date"],
                    "code": row["code"],
                    "volume_wan": row.get("volume_wan", 0),
                    "amount_wan": row.get("amount_wan", 0),
                    "rz_buy": row.get("rz_buy", 0),
                    "rz_balance": row.get("rz_balance", 0),
                    "rq_sell_qty": row.get("rq_sell_qty", 0),
                    "rq_balance_qty": row.get("rq_balance_qty", 0),
                    "rq_balance_amt": row.get("rq_balance_amt", 0),
                    "total_balance": row.get("total_balance", 0),
                })

            pk_cols = ["date", "code"]
            split_tables = [
                ("stats.etf_identity",         identity_rows),
                ("stats.etf_basic_stats",       basic_rows),
                ("stats.etf_tech_stats",       tech_rows),
                ("stats.etf_adjustment",        adj_rows),
                ("stats.etf_liquidity_margin",  liq_rows),
            ]
            for tbl, rows in split_tables:
                if rows:
                    inserted = await bulk_upsert_async(conn, tbl, rows, pk_cols)
                    print(f"    [DB] Inserted {inserted:,} rows into {tbl}", flush=True)
                else:
                    print(f"    [DB] No new rows to insert into {tbl}", flush=True)

        # ------------------------------------------------------------------
        # (6) sec_composition: insert only missing (code, snapshot_date) pairs
        # ------------------------------------------------------------------
        print("\n[6/6] Inserting composition data (missing snapshots only) …", flush=True)

        # Query existing (code, snapshot_date) pairs from sec_composition
        comp_existing_rows = await conn.fetch(
            "SELECT DISTINCT code, snapshot_date FROM stats.sec_composition"
        )
        existing_comp_keys = {(r["code"], r["snapshot_date"]) for r in comp_existing_rows}
        print(f"    [DB] {len(existing_comp_keys):,} existing (code, snapshot_date) pairs in stats.sec_composition", flush=True)

        holdings_rows = []
        etf_codes_with_full_comp: set = set()

        # Source 1: Full composition data (comp_long → ALL holdings)
        if comp_long is not None and len(comp_long) > 0:
            comp_eq = comp_long[comp_long["cash_sub_flag"] != "必须"].copy()
            if len(comp_eq) > 0:
                comp_eq["_shares"] = pd.to_numeric(comp_eq["shares"], errors="coerce").fillna(0.0)
                comp_eq["_w"] = comp_eq["_shares"].abs()
                n_full = 0
                for (etf_stripped, trade_date), sub in comp_eq.groupby(["etf_code", "trade_date"]):
                    if pd.isna(trade_date):
                        continue
                    total_w = float(sub["_w"].sum())
                    if total_w <= 0:
                        continue
                    code_str = str(etf_stripped).strip().zfill(6)
                    suffix = get_exchange_for_etf(code_str)
                    if not suffix:
                        continue
                    etf_code_full = f"{code_str}.{suffix}"
                    snap_date = pd.Timestamp(trade_date).date()
                    # Skip if this (code, snapshot_date) is already in DB
                    if (etf_code_full, snap_date) in existing_comp_keys:
                        continue
                    sub_sorted = sub.sort_values("_w", ascending=False).reset_index(drop=True)
                    rows_before = len(holdings_rows)
                    for rank_idx, (_, r) in enumerate(sub_sorted.iterrows(), start=1):
                        sc = str(r.get("stock_code", "")).strip()
                        sc_stripped = sc.split(".")[0].zfill(6)
                        if len(sc_stripped) != 6 or not sc_stripped.isdigit():
                            continue
                        holdings_rows.append({
                            "snapshot_date": snap_date,
                            "code": etf_code_full,
                            "source_type": "etf",
                            "rank": rank_idx,
                            "stock_code": sc,
                            "stock_name": str(r.get("stock_name", "") or ""),
                            "weight_pct": float(r["_w"]) / total_w * 100.0,
                        })
                    if len(holdings_rows) > rows_before:
                        etf_codes_with_full_comp.add(code_str)
                        n_full += 1
                print(f"    [DB] Built {len(holdings_rows):,} sec_composition rows (full comp) "
                      f"from {n_full} ETFs (skipped existing)", flush=True)

        # Source 2: CSI index composition
        index_comp_rows = build_index_composition_rows(verbose=True)
        if index_comp_rows:
            # Filter to missing (code, snapshot_date) pairs
            n_index_before = len(index_comp_rows)
            index_comp_rows = [
                r for r in index_comp_rows
                if (r["code"], r["snapshot_date"]) not in existing_comp_keys
            ]
            n_index_skipped = n_index_before - len(index_comp_rows)
            holdings_rows.extend(index_comp_rows)
            print(f"    [DB] Built {len(index_comp_rows):,} sec_composition rows (CSI index comp) "
                  f"from {len(set(r['code'] for r in index_comp_rows))} indices "
                  f"(skipped {n_index_skipped} existing)", flush=True)
        else:
            print(f"    [DB] No CSI index composition data found", flush=True)

        # Source 2b: SZSE index composition (399001, 399006, 399237, etc.)
        szse_index_comp_rows = build_szse_index_composition_rows(verbose=True)
        if szse_index_comp_rows:
            n_szse_before = len(szse_index_comp_rows)
            szse_index_comp_rows = [
                r for r in szse_index_comp_rows
                if (r["code"], r["snapshot_date"]) not in existing_comp_keys
            ]
            n_szse_skipped = n_szse_before - len(szse_index_comp_rows)
            holdings_rows.extend(szse_index_comp_rows)
            print(f"    [DB] Built {len(szse_index_comp_rows):,} sec_composition rows (SZSE index comp) "
                  f"from {len(set(r['code'] for r in szse_index_comp_rows))} indices "
                  f"(skipped {n_szse_skipped} existing)", flush=True)
        else:
            print(f"    [DB] No SZSE index composition data found", flush=True)

        if holdings_rows:
            inserted = await bulk_upsert_async(
                conn, "stats.sec_composition", holdings_rows,
                ["code", "snapshot_date", "rank"],
            )
            print(f"    [DB] Inserted {inserted:,} rows into stats.sec_composition", flush=True)
        else:
            print(f"    [DB] No new rows to insert into stats.sec_composition", flush=True)

        # ---- sec_classification (type='etf'): per-ETF quality metrics (upsert all from full data) ----
        # Only quality-metric columns are in the row dict, so ON CONFLICT (code)
        # DO UPDATE SET preserves classification + index_code columns populated
        # by build_etf_index_map.py. New ETFs not in the CSV get inserted with
        # classification defaults ('OTHER') — re-run build_etf_index_map.py to
        # backfill their classification + index_code.
        avg_vol_by_code: dict = {}
        if "volume_wan" in merged_db.columns:
            avg_vol_by_code = merged_db.groupby("code")["volume_wan"].mean().to_dict()

        sec_classification_rows = []
        for _, row in uni_df.iterrows():
            code = str(row.get("code", "")).strip()
            if not code:
                continue
            n_days = int(row.get("n_ohlcv_days", 0) or 0)
            n_margin = int(row.get("n_margin_days", 0) or 0)
            has_margin = n_margin > 0
            avg_vol = float(avg_vol_by_code.get(code, 0.0) or 0.0)
            fd = row.get("first_date", "")
            ld = row.get("last_date", "")
            fd_date = datetime.datetime.strptime(str(fd), "%Y-%m-%d").date() if fd else None
            ld_date = datetime.datetime.strptime(str(ld), "%Y-%m-%d").date() if ld else None
            base_score = (100 if n_days >= 200 else 0) + (50 if has_margin else 0)
            sec_classification_rows.append({
                "code": code,
                "name": str(row.get("name", "") or ""),
                "type": "etf",
                "n_days": n_days,
                "has_margin": has_margin,
                "avg_volume_wan": avg_vol,
                "first_date": fd_date,
                "last_date": ld_date,
                "selectivity_rank_score": base_score,
            })

        # Add volume-rank component (0..50)
        if sec_classification_rows:
            by_vol = sorted(sec_classification_rows, key=lambda r: r["avg_volume_wan"], reverse=True)
            n_etf = len(by_vol)
            for rank_i, r in enumerate(by_vol):
                r["selectivity_rank_score"] += int(50 * (1.0 - rank_i / max(n_etf, 1)))

        if sec_classification_rows:
            inserted = await bulk_upsert_async(
                conn, "stats.sec_classification", sec_classification_rows, ["code"]
            )
            print(f"    [DB] Upserted {inserted:,} ETF quality rows into stats.sec_classification", flush=True)
        else:
            print(f"    [DB] No ETF quality rows to insert into stats.sec_classification", flush=True)

    finally:
        await conn.close()

    # Console summary
    print(f"\n  Theme distribution:", flush=True)
    for tid, sub in uni_df.groupby("theme_id"):
        print(f"    · {tid:<20s} {len(sub):>4d}", flush=True)

    print(f"\n  Exchange distribution:", flush=True)
    for exc, sub in uni_df.groupby("exchange"):
        print(f"    · {exc:<4s} {len(sub):>4d} ETFs", flush=True)

    print_wall_time(t0)


if __name__ == "__main__":
    asyncio.run(main())
