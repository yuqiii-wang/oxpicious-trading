"""
build_szse_sse_etf_and_margin.py — Build combined SZSE + SSE ETF OHLCV + margin + composition CSV.

Reads the per-day SZSE/SSE CSV archives produced by download scripts:
  • SZSE: szse_archive/szse_etf_YYYYMMDD.csv        (2022-01 → 2025-06-30 legacy)
  • SZSE: szse_trend/szse_trend_etf_YYYYMMDD.csv    (2025-07 → today snapshot)
  • SSE: sse_trend/sse_trend_stock_YYYYMMDD.csv     (today snapshot, stocks only — NO ETFs)
  • SZSE: szse_margin/szse_margin_detail_YYYYMMDD.csv  (per-security margin detail)
  • SZSE: szse_etf_composition/szse_etf_comp_YYYYMMDD_<code>.csv (per-file finished CSV; .md sibling kept as raw archive)

CRITICAL: Stock/ETF codes must be disambiguated with exchange suffixes (.SS for Shanghai,
.SZ for Shenzhen) because 000xxx/001xxx codes overlap between indices and stocks.
ETF codes have NO overlap:
  - SSE: 510xxx, 511xxx, 512xxx, 513xxx, 515xxx, 516xxx, 518xxx, 56xxx
  - SZSE: 150xxx, 159xxx, 16xxx

NOTE: The SSE price endpoint (download_sse_price.py) returns only stocks (600/601/603/605/688),
not ETFs. SSE ETF data would require a separate download script targeting a different endpoint.

Filters ETF/LOF rows by exchange-specific code prefixes, excludes money-market /
fixed-income ETFs, parses comma-formatted numeric strings, and merges OHLCV
with margin data on (date, code). Also aggregates composition per-file CSVs
(produced by download_szse_etf_composition.py) to extract top 5 weighted
holdings for each ETF.

Outputs:
  • analysis_output/szse_sse_etf_margin/etf_margin_combined.csv  (long format with top5 columns)
  • analysis_output/szse_sse_etf_margin/per_etf/<code>.csv         (per-ETF wide)
  • analysis_output/szse_sse_etf_margin/etf_universe.csv            (one row per ETF)
  • analysis_output/szse_etf_composition/composition_combined.csv
  • analysis_output/szse_etf_composition/composition_universe.csv

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
from _study_select_etf import ETF_THEME_RULES
from _db_commons import (
    get_db_connection_async, get_existing_keys_async, bulk_upsert_async,
    truncate_table_async
)

# ---------------------------------------------------------------------------
# stdout encoding (Windows)
# ---------------------------------------------------------------------------
import locale as _locale
try:
    _locale.setlocale(_locale.LC_ALL, "")
except Exception:
    pass
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ============================================================================
# Paths
# ============================================================================
PROJECT_ROOT        = os.path.dirname(os.path.abspath(__file__))
TEMP_DATA           = os.path.join(PROJECT_ROOT, "temp_data")
SZSE_ARCHIVE_DIR    = os.path.join(PROJECT_ROOT, "temps", "szse_archive")
SZSE_TREND_DIR      = os.path.join(PROJECT_ROOT, "temps", "szse_trend")
SSE_TREND_DIR       = os.path.join(PROJECT_ROOT, "temps", "sse_trend")
SZSE_MARGIN_DIR     = os.path.join(PROJECT_ROOT, "temps", "szse_margin")
SSE_MARGIN_DIR      = os.path.join(PROJECT_ROOT, "temps", "sse_margin")
COMP_DIR            = os.path.join(PROJECT_ROOT, "temps", "szse_etf_composition")
INDEX_COMP_DIR      = os.path.join(PROJECT_ROOT, "temps", "csi_index_composition")
OUTPUT_DIR          = os.path.join(TEMP_DATA, "analysis_output", "szse_sse_etf_margin")
PER_ETF_DIR         = os.path.join(OUTPUT_DIR, "per_etf")
COMP_OUTPUT_DIR     = os.path.join(TEMP_DATA, "analysis_output", "szse_etf_composition")
COMP_PER_ETF_DIR    = os.path.join(COMP_OUTPUT_DIR, "per_etf")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(PER_ETF_DIR, exist_ok=True)
os.makedirs(COMP_OUTPUT_DIR, exist_ok=True)
os.makedirs(COMP_PER_ETF_DIR, exist_ok=True)

TODAY_STR = datetime.datetime.now().strftime("%Y-%m-%d")

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


def is_etf_code(code):
    return is_szse_etf_code(code) or is_sse_etf_code(code)


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


def parse_num(s):
    if s is None:
        return 0.0
    if isinstance(s, (int, float)):
        try:
            v = float(s)
            return 0.0 if not np.isfinite(v) else v
        except Exception:
            return 0.0
    txt = str(s).strip()
    if not txt or txt in ("--", "-", "—", "null", "NULL", "None", "nan", "NaN"):
        return 0.0
    txt = txt.replace(",", "").replace("，", "").replace(" ", "").replace("\u3000", "")
    try:
        v = float(txt)
        return 0.0 if not np.isfinite(v) else v
    except Exception:
        return 0.0


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


def _ymd_from_filename(path, prefix, suffix=".csv"):
    b = os.path.basename(path)
    if not b.startswith(prefix):
        return None
    m = re.search(r"(\d{8})", b)
    if not m:
        return None
    return m.group(1)


def _in_range(ymd, start_ymd, end_ymd):
    if ymd is None:
        return False
    if start_ymd and ymd < start_ymd:
        return False
    if end_ymd and ymd > end_ymd:
        return False
    return True


# ============================================================================
# Composition: read finished per-file CSVs produced by download_szse_etf_composition.py
# Each .md file has a sibling .csv (same stem) with COMBINED_COLS schema, so the
# build step just aggregates the finished CSVs instead of re-parsing markdown.
# ============================================================================
COMBINED_COLS = [
    "trade_date", "etf_code", "etf_name", "fund_type", "target_index",
    "nav_per_unit", "min_unit_nav",
    "stock_code", "stock_name", "shares", "cash_sub_flag", "market",
]


def build_composition(limit=None, verbose=True):
    files = sorted(glob.glob(os.path.join(COMP_DIR, "szse_etf_comp_*.csv")))
    if limit:
        files = files[:limit]
    if verbose:
        print(f"    [COMP] {len(files)} per-file CSVs in {COMP_DIR}", flush=True)

    counts = Counter()
    dfs = []
    for path in files:
        try:
            # keep_default_na=False preserves "" for empty cells (matches the original
            # MD-parsing behaviour where missing fields were "" not NaN); numeric cols
            # are coerced to numeric below.
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
    # Ensure expected column order / fill missing columns
    for c in COMBINED_COLS:
        if c not in combined.columns:
            combined[c] = None
    combined = combined[COMBINED_COLS]
    combined["trade_date"] = pd.to_datetime(combined["trade_date"], errors="coerce")
    for c in ("nav_per_unit", "min_unit_nav", "shares"):
        combined[c] = pd.to_numeric(combined[c], errors="coerce")
    combined = combined.sort_values(["etf_code", "trade_date", "stock_code"]).reset_index(drop=True)

    combined_path = os.path.join(COMP_OUTPUT_DIR, "composition_combined.csv")
    combined.to_csv(combined_path, index=False, encoding="utf-8-sig")
    if verbose:
        print(f"    [SAVE] {combined_path} ({len(combined):,} rows, "
              f"{combined['etf_code'].nunique()} ETFs, "
              f"{combined['trade_date'].dt.strftime('%Y-%m-%d').nunique()} dates)",
              flush=True)

    n_written = 0
    for code, sub in combined.groupby("etf_code"):
        out = os.path.join(COMP_PER_ETF_DIR, f"{code}.csv")
        sub.sort_values(["trade_date", "stock_code"]).to_csv(
            out, index=False, encoding="utf-8-sig")
        n_written += 1
    if verbose:
        print(f"    [SAVE] {COMP_PER_ETF_DIR} ({n_written} per-ETF files)", flush=True)

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
    universe_path = os.path.join(COMP_OUTPUT_DIR, "composition_universe.csv")
    universe.to_csv(universe_path, index=False, encoding="utf-8-sig")
    if verbose:
        print(f"    [SAVE] {universe_path} ({len(universe)} ETFs)", flush=True)

    print(f"    [STATS] parsed={counts['parsed']} failed={counts['failed']} "
          f"total_holdings={counts['holdings']:,}", flush=True)
    return combined, universe


def get_top5_constituents_for_date(comp_long, code, target_date, n=5):
    if comp_long is None:
        return [], None
    target_code = strip_exchange_suffix(str(code)).zfill(6)
    sub = comp_long[comp_long["etf_code"] == target_code]
    if sub.empty:
        return [], None

    sub = sub[sub["trade_date"].notna()].copy()
    if sub.empty:
        return [], None

    comp_dates = sorted(sub["trade_date"].unique())

    best_date = None
    target_month = target_date.to_period("M")
    target_quarter = target_date.to_period("Q")

    same_month_dates = [d for d in comp_dates if d.to_period("M") == target_month]
    if same_month_dates:
        prior_month = [d for d in same_month_dates if d <= target_date]
        if prior_month:
            best_date = max(prior_month)

    if best_date is None:
        same_quarter_dates = [d for d in comp_dates if d.to_period("Q") == target_quarter]
        if same_quarter_dates:
            prior_quarter = [d for d in same_quarter_dates if d <= target_date]
            if prior_quarter:
                best_date = max(prior_quarter)

    if best_date is None:
        candidates = [d for d in comp_dates if d <= target_date]
        if candidates:
            best_date = max(candidates)
        else:
            best_date = min(comp_dates, key=lambda d: abs((d - target_date).days))

    snap = sub[(sub["trade_date"] == best_date) & (sub["cash_sub_flag"] != "必须")].copy()
    if snap.empty:
        return [], best_date
    snap["_shares"] = pd.to_numeric(snap["shares"], errors="coerce").fillna(0.0)
    snap["_w"] = snap["_shares"].abs()
    total_w = float(snap["_w"].sum())
    if total_w <= 0:
        return [], best_date
    snap["_pct"] = snap["_w"] / total_w * 100.0
    snap = snap.sort_values("_pct", ascending=False).head(n)
    out = []
    for _, r in snap.iterrows():
        out.append({
            "stock_code": str(r.get("stock_code", "")).strip(),
            "stock_name": str(r.get("stock_name", "")).strip(),
            "weight_pct": float(r["_pct"]),
        })
    return out, best_date


def build_top5_snapshots(comp_long):
    if comp_long is None or comp_long.empty:
        return pd.DataFrame()

    df = comp_long[comp_long["cash_sub_flag"] != "必须"].copy()
    if df.empty:
        return pd.DataFrame()

    df["_shares"] = pd.to_numeric(df["shares"], errors="coerce").fillna(0.0)
    df["_w"] = df["_shares"].abs()

    snapshot_rows = []
    for (etf_code, trade_date), sub in df.groupby(["etf_code", "trade_date"]):
        total_w = float(sub["_w"].sum())
        if total_w <= 0:
            continue
        sub = sub.copy()
        sub["_pct"] = sub["_w"] / total_w * 100.0
        sub = sub.sort_values("_pct", ascending=False).head(5)

        row = {"etf_code": etf_code, "comp_date": trade_date}
        for i, (_, r) in enumerate(sub.iterrows(), 1):
            row[f"top{i}_code"] = str(r.get("stock_code", "")).strip()
            row[f"top{i}_name"] = str(r.get("stock_name", "")).strip()
            row[f"top{i}_weight_pct"] = float(r["_pct"])
        for i in range(len(sub) + 1, 6):
            row[f"top{i}_code"] = ""
            row[f"top{i}_name"] = ""
            row[f"top{i}_weight_pct"] = 0.0
        snapshot_rows.append(row)

    if not snapshot_rows:
        return pd.DataFrame()

    snaps = pd.DataFrame(snapshot_rows)
    snaps["comp_date"] = pd.to_datetime(snaps["comp_date"], errors="coerce")
    snaps = snaps.sort_values(["etf_code", "comp_date"]).reset_index(drop=True)
    return snaps


# ============================================================================
# Index composition: read CSI index closeweight CSVs produced by
# download_index_composition.py. Each CSV has columns:
#   snapshot_date, index_code, index_name, stock_code, stock_name, weight_pct
# The stock_code already carries an exchange suffix (.SS/.SZ).
# ============================================================================
def build_index_composition_rows(verbose=True):
    """Read CSI index composition CSVs and build rows for stats.sec_composition.

    Returns a list of dicts with keys:
      snapshot_date, code, source_type, rank, stock_code, stock_name, weight_pct

    The 'code' is the bare 6-digit index code (e.g. '930606').
    'source_type' is always 'index'.
    'rank' is assigned 1..N by weight_pct DESC within each (index, snapshot).
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
    # Ensure expected columns exist
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
# Build OHLCV long DataFrame from szse_etf_*.csv + szse_trend_etf_*.csv + sse_trend_stock_*.csv
# ============================================================================
def _scan_ohlcv_dir(scan_dir, file_prefix, start_ymd, end_ymd, market):
    pattern = os.path.join(scan_dir, f"{file_prefix}*.csv")
    files = sorted(glob.glob(pattern))
    rows = []
    n_empty = 0
    n_ok = 0
    for path in files:
        ymd = _ymd_from_filename(path, file_prefix)
        if not _in_range(ymd, start_ymd, end_ymd):
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


def build_ohlcv_df(start_date=None, end_date=None, verbose=True):
    start_ymd = start_date.replace("-", "") if start_date else None
    end_ymd   = end_date.replace("-", "")   if end_date   else None

    rows_a, ok_a, empty_a, tot_a = [], 0, 0, 0
    rows_t, ok_t, empty_t, tot_t = [], 0, 0, 0
    rows_sse, ok_sse, empty_sse, tot_sse = [], 0, 0, 0

    if os.path.isdir(SZSE_ARCHIVE_DIR):
        if verbose:
            print(f"    [OHLCV-szse-archive] scanning {os.path.join(SZSE_ARCHIVE_DIR, 'szse_etf_*.csv')}", flush=True)
        rows_a, ok_a, empty_a, tot_a = _scan_ohlcv_dir(SZSE_ARCHIVE_DIR, "szse_etf_", start_ymd, end_ymd, "深圳")
        if verbose:
            print(f"      → scanned {tot_a} files  {ok_a} ok  {empty_a} empty  {len(rows_a)} rows", flush=True)

    if os.path.isdir(SZSE_TREND_DIR):
        if verbose:
            print(f"    [OHLCV-szse-trend] scanning {os.path.join(SZSE_TREND_DIR, 'szse_trend_etf_*.csv')}", flush=True)
        rows_t, ok_t, empty_t, tot_t = _scan_ohlcv_dir(SZSE_TREND_DIR, "szse_trend_etf_", start_ymd, end_ymd, "深圳")
        if verbose:
            print(f"      → scanned {tot_t} files  {ok_t} ok  {empty_t} empty  {len(rows_t)} rows", flush=True)

    if os.path.isdir(SSE_TREND_DIR):
        if verbose:
            print(f"    [OHLCV-sse-trend] scanning {os.path.join(SSE_TREND_DIR, 'sse_trend_stock_*.csv')}", flush=True)
        rows_sse, ok_sse, empty_sse, tot_sse = _scan_ohlcv_dir(SSE_TREND_DIR, "sse_trend_stock_", start_ymd, end_ymd, "上海")
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

    if verbose:
        n_szse = out["code"].str.endswith(".SZ").sum()
        n_sse = out["code"].str.endswith(".SS").sum()
        print(f"    [OHLCV] final rows: {len(out):,}  "
              f"unique codes: {out['code'].nunique()}  "
              f"SZSE (.SZ): {n_szse:,}  SSE (.SS): {n_sse:,}  "
              f"date range: {out['date'].min().date()} → {out['date'].max().date()}", flush=True)
    return out


# ============================================================================
# Build margin long DataFrame from szse_margin_detail_*.csv + sse_margin_detail_*.csv
# ============================================================================
def _scan_margin_dir(scan_dir, file_prefix, start_ymd, end_ymd, market, verbose=True):
    pattern = os.path.join(scan_dir, f"{file_prefix}*.csv")
    files = sorted(glob.glob(pattern))
    if verbose:
        print(f"    [MARGIN-{market}] scanning {len(files)} {file_prefix}*.csv files", flush=True)

    rows = []
    n_empty = 0
    n_ok = 0
    for path in files:
        ymd = _ymd_from_filename(path, file_prefix)
        if not _in_range(ymd, start_ymd, end_ymd):
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


def build_margin_df(start_date=None, end_date=None, verbose=True):
    start_ymd = start_date.replace("-", "") if start_date else None
    end_ymd   = end_date.replace("-", "")   if end_date   else None

    all_rows = []
    n_ok_total = 0
    n_empty_total = 0

    if os.path.isdir(SZSE_MARGIN_DIR):
        rows_szse, ok_szse, empty_szse = _scan_margin_dir(
            SZSE_MARGIN_DIR, "szse_margin_detail_", start_ymd, end_ymd, "深圳", verbose)
        all_rows.extend(rows_szse)
        n_ok_total += ok_szse
        n_empty_total += empty_szse

    if os.path.isdir(SSE_MARGIN_DIR):
        rows_sse, ok_sse, empty_sse = _scan_margin_dir(
            SSE_MARGIN_DIR, "sse_margin_detail_", start_ymd, end_ymd, "上海", verbose)
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

    # Vectorized dedup: for each (date, code), sum the margin cols. Since 0 + x = x,
    # this correctly handles all edge cases (single row, all-zero dup, one-non-zero dup,
    # multi-non-zero dup) without dropping the grouping columns (which groupby().apply()
    # can do in pandas 2.x).
    out = out.groupby(["date", "code"], as_index=False)[margin_cols].sum()
    n_after = len(out)
    n_merged = n_before - n_after

    if verbose:
        print(f"    [MARGIN] total: {n_ok_total} files with data, {n_empty_total} empty, "
              f"{len(all_rows)} raw rows → {n_after} merged rows ({n_merged} duplicates handled)", flush=True)

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
# Main pipeline
# ============================================================================
async def main():
    import asyncio
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-date", default=None, help="YYYY-MM-DD inclusive")
    ap.add_argument("--end-date",   default=None, help="YYYY-MM-DD inclusive")
    ap.add_argument("--force",      action="store_true", help="Overwrite existing outputs")
    args = ap.parse_args()

    t0 = time.time()
    print("=" * 78, flush=True)
    print("  BUILD SZSE + SSE ETF + MARGIN + COMPOSITION TO DATABASE", flush=True)
    print("=" * 78, flush=True)
    print(f"  SZSE Archive dir   (2023 → 2025-06): {SZSE_ARCHIVE_DIR}", flush=True)
    print(f"  SZSE Trend dir     (2025-07 → today): {SZSE_TREND_DIR}", flush=True)
    print(f"  SSE Trend dir      (today): {SSE_TREND_DIR}", flush=True)
    print(f"  Margin dir : {SZSE_MARGIN_DIR}", flush=True)
    print(f"  Composition dir: {COMP_DIR}", flush=True)
    print(f"  Index comp dir : {INDEX_COMP_DIR}", flush=True)
    print(f"  Output dir : {OUTPUT_DIR}", flush=True)
    print(f"  Date range : {args.start_date or '(all)'} → {args.end_date or '(all)'}", flush=True)
    print(f"  Today      : {TODAY_STR}", flush=True)

    combined_csv = os.path.join(OUTPUT_DIR, "etf_margin_combined.csv")
    universe_csv = os.path.join(OUTPUT_DIR, "etf_universe.csv")
    if (not args.force
            and os.path.exists(combined_csv)
            and os.path.exists(universe_csv)
            and os.path.getsize(combined_csv) > 1000):
        print(f"\n  [SKIP] outputs already exist ({combined_csv}); use --force to rebuild", flush=True)
        return

    print("\n[1/5] Building OHLCV long frame from szse_etf_*.csv + sse_trend_stock_*.csv …", flush=True)
    ohlcv_df = build_ohlcv_df(args.start_date, args.end_date)
    if len(ohlcv_df) == 0:
        print("    [FATAL] No OHLCV rows parsed — check archive dir", flush=True)
        sys.exit(1)
    n_szse = ohlcv_df["code"].str.endswith(".SZ").sum()
    n_sse = ohlcv_df["code"].str.endswith(".SS").sum()
    print(f"    → {len(ohlcv_df):,} OHLCV rows  ·  {ohlcv_df['code'].nunique()} unique ETFs", flush=True)
    print(f"    → SZSE (.SZ): {n_szse:,}  ·  SSE (.SS): {n_sse:,}", flush=True)
    print(f"    → date range: {ohlcv_df['date'].min().date()} → {ohlcv_df['date'].max().date()}", flush=True)

    print("\n[2/5] Building margin long frame from szse_margin_detail_*.csv …", flush=True)
    margin_df = build_margin_df(args.start_date, args.end_date)
    if len(margin_df) == 0:
        print("    [WARN] No margin rows parsed — proceeding with OHLCV only", flush=True)
        margin_df = pd.DataFrame(columns=["date", "code", "rz_buy", "rz_balance",
                                            "rq_sell_qty", "rq_balance_qty",
                                            "rq_balance_amt", "total_balance"])
    else:
        print(f"    → {len(margin_df):,} margin rows  ·  {margin_df['code'].nunique()} unique ETFs", flush=True)
        print(f"    → date range: {margin_df['date'].min().date()} → {margin_df['date'].max().date()}", flush=True)

    print("\n[3/5] Building composition long frame from szse_etf_comp_*.csv …", flush=True)
    comp_long, comp_universe = build_composition(verbose=True)

    print("\n[4/5] Merging OHLCV + margin on (date, code) …", flush=True)
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

    print("\n  [4b/5] Computing rq_balance_amt for SSE ETFs (quantity × mid price) …", flush=True)
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
            print(f"    → Filled rq_balance_amt for {missing_rq_amt.sum():,} SSE ETF rows "
                  f"(融券余量 × (open+close)/2 mid price)", flush=True)
        else:
            print(f"    → No SSE ETF rows need rq_balance_amt computation", flush=True)
    else:
        print(f"    → Skipped: rq_balance_qty/rq_balance_amt not available", flush=True)

    print("\n  [4c/5] Applying corp-action adjustment (splits + dividends) …", flush=True)
    merged = apply_split_adjustment(merged, verbose=True)

    print("\n  [4d/5] Adding MA statistics (adj_close-based) …", flush=True)
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

    print("\n  [4e/5] Adding top5 constituent columns (date-range matched) …", flush=True)
    top5_cols = []
    for i in range(1, 6):
        top5_cols.extend([f"top{i}_code", f"top{i}_name", f"top{i}_weight_pct"])
    # NOTE: do NOT pre-initialize top1..top5_* columns in `merged` here.
    # merge_asof would otherwise see a name conflict with snaps' top*_code and
    # create suffixed columns (top1_code_x empty + top1_code_y populated),
    # silently dropping the populated data. Columns are filled after the merge.
    merged["comp_match_date"] = ""

    if comp_long is not None and len(comp_long) > 0:
        print("    Building top5 snapshot table …", flush=True)
        snaps = build_top5_snapshots(comp_long)
        if not snaps.empty:
            print(f"    → {len(snaps):,} snapshots across {snaps['etf_code'].nunique()} ETFs", flush=True)

            merged["_comp_key"] = merged["code"].apply(
                lambda c: strip_exchange_suffix(str(c)).zfill(6))
            snaps = snaps.rename(columns={"etf_code": "_comp_key"})

            merged_sorted = merged.sort_values(["_comp_key", "date"]).reset_index(drop=True)
            snaps_sorted = snaps.sort_values(["_comp_key", "comp_date"]).reset_index(drop=True)

            print("    Running merge_asof (per-ETF batches) …", flush=True)
            matched_dfs = []
            for comp_key, etf_sub in merged_sorted.groupby("_comp_key", sort=False):
                snap_sub = snaps_sorted[snaps_sorted["_comp_key"] == comp_key]
                if snap_sub.empty:
                    matched_dfs.append(etf_sub)
                    continue

                etf_sub_sorted = etf_sub.sort_values("date")
                snap_sub_sorted = snap_sub.sort_values("comp_date")

                etf_matched = pd.merge_asof(
                    etf_sub_sorted,
                    snap_sub_sorted,
                    left_on="date",
                    right_on="comp_date",
                    direction="backward",
                )
                matched_dfs.append(etf_matched)

            matched = pd.concat(matched_dfs, ignore_index=True)
            matched = matched.sort_values(["code", "date"]).reset_index(drop=True)

            # After merge, top*_code columns exist only for matched groups.
            # Create/fill them for unmatched groups so the final schema is uniform.
            for i in range(1, 6):
                for col in [f"top{i}_code", f"top{i}_name"]:
                    if col in matched.columns:
                        matched[col] = matched[col].fillna("")
                    else:
                        matched[col] = ""
                pct_col = f"top{i}_weight_pct"
                if pct_col in matched.columns:
                    matched[pct_col] = matched[pct_col].fillna(0.0)
                else:
                    matched[pct_col] = 0.0

            matched["comp_match_date"] = matched["comp_date"].dt.strftime("%Y-%m-%d").fillna("")
            matched = matched.drop(columns=["comp_date", "_comp_key"], errors="ignore")

            merged = matched
            print(f"    → top5 data applied via merge_asof", flush=True)
        else:
            print(f"    → [WARN] No composition snapshots built", flush=True)
            for col in top5_cols:
                merged[col] = "" if "_pct" not in col else 0.0

        n_matched = (merged["comp_match_date"] != "").sum()
        print(f"    → {n_matched:,} / {len(merged):,} rows matched to composition data", flush=True)
    else:
        print(f"    → [WARN] No composition data available for top5 columns", flush=True)
        for col in top5_cols:
            merged[col] = "" if "_pct" not in col else 0.0

    col_order = [
        "date", "code", "name",
        "prev_close", "open", "high", "low", "close", "pct_change",
        "cum_split_factor", "is_split_event_day",
        "action_type", "implied_dividend_per_share", "cum_dividend_per_share",
        "adj_prev_close", "adj_open", "adj_high", "adj_low", "adj_close",
        "ma5", "ma5_ratio", "ma20", "ma60", "ma120", "ma255",
        "volume_wan", "amount_wan",
        "rz_buy", "rz_balance",
        "rq_sell_qty", "rq_balance_qty", "rq_balance_amt", "total_balance",
        "comp_match_date",
    ] + top5_cols
    col_order = [c for c in col_order if c in merged.columns]
    merged = merged[col_order].sort_values(["code", "date"]).reset_index(drop=True)
    n_szse_out = merged["code"].str.endswith(".SZ").sum()
    n_sse_out = merged["code"].str.endswith(".SS").sum()
    print(f"    → merged: {len(merged):,} rows  ·  {merged['code'].nunique()} ETFs", flush=True)
    print(f"    → SZSE (.SZ): {n_szse_out:,}  ·  SSE (.SS): {n_sse_out:,}", flush=True)

    merged.to_csv(combined_csv, index=False, encoding="utf-8-sig")
    print(f"    → Saved {combined_csv}  ({os.path.getsize(combined_csv):,} bytes)", flush=True)

    n_per_etf = 0
    for code, sub in merged.groupby("code"):
        sub = sub.sort_values("date").reset_index(drop=True)
        out_path = os.path.join(PER_ETF_DIR, f"{code}.csv")
        sub.to_csv(out_path, index=False, encoding="utf-8-sig")
        n_per_etf += 1
    print(f"    → Saved {n_per_etf} per-ETF CSVs under {PER_ETF_DIR}", flush=True)

    print("\n[5/5] Building ETF universe CSV …", flush=True)
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
    uni_df.to_csv(universe_csv, index=False, encoding="utf-8-sig")
    print(f"    → Saved {universe_csv}  ({len(uni_df)} ETFs)", flush=True)

    # ------------------------------------------------------------------
    # Insert to database
    # ------------------------------------------------------------------
    print("\n[6/6] Inserting data to database …", flush=True)
    
    # Connect to database (async)
    print("    [DB] Connecting to database …", flush=True)
    try:
        conn = await get_db_connection_async()
        print("    [DB] Connected successfully", flush=True)
    except Exception as e:
        print(f"    [DB] [WARN] Database connection failed: {e}", flush=True)
        print(f"    [DB] Continuing without database insertion", flush=True)
    else:
        try:
            if args.force:
                print("    [DB] Force mode: truncating existing tables", flush=True)
                # CASCADE truncates all FK child tables automatically
                await truncate_table_async(conn, "stats.etf_identity")
                # sec_composition, etf_meta have no FK to etf_identity
                await truncate_table_async(conn, "stats.sec_composition")
                await truncate_table_async(conn, "stats.etf_meta")

            # Convert date to datetime.date for asyncpg DATE codec.
            # asyncpg requires datetime.date instances; passing str raises
            # "expected a date instance, got 'str'".
            merged_db = merged.copy()
            merged_db["date"] = merged_db["date"].dt.date
            # comp_match_date is a string ("YYYY-MM-DD" or ""); convert to date or None
            def _parse_comp_date(s):
                if not s or str(s).strip() == "":
                    return None
                try:
                    return datetime.datetime.strptime(str(s), "%Y-%m-%d").date()
                except ValueError:
                    return None
            merged_db["comp_match_date"] = merged_db["comp_match_date"].apply(_parse_comp_date)

            # Get existing keys from etf_identity (the PK parent table)
            existing_keys = await get_existing_keys_async(
                conn, "stats.etf_identity", ["date", "code"]
            )
            print(f"    [DB] {len(existing_keys):,} existing (date, code) pairs in stats.etf_identity", flush=True)

            # Build rows for each split table, skipping rows already present.
            identity_rows, basic_rows, tech_rows = [], [], []
            adj_rows, liq_rows, comp_link_rows = [], [], []
            for _, row in merged_db.iterrows():
                key = (row["date"], row["code"])
                if key in existing_keys:
                    continue
                identity_rows.append({
                    "date": row["date"],
                    "code": row["code"],
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
                comp_link_rows.append({
                    "date": row["date"],
                    "code": row["code"],
                    "comp_match_date": row.get("comp_match_date"),
                })

            # Insert identity first (FK parent), then sub-tables
            pk_cols = ["date", "code"]
            split_tables = [
                ("stats.etf_identity",         identity_rows),
                ("stats.etf_basic_stats",       basic_rows),
                ("stats.etf_tech_stats",       tech_rows),
                ("stats.etf_adjustment",        adj_rows),
                ("stats.etf_liquidity_margin",  liq_rows),
                ("stats.etf_composition_link",  comp_link_rows),
            ]
            for tbl, rows in split_tables:
                if rows:
                    inserted = await bulk_upsert_async(conn, tbl, rows, pk_cols)
                    print(f"    [DB] Inserted {inserted:,} rows into {tbl}", flush=True)
                else:
                    print(f"    [DB] No new rows to insert into {tbl}", flush=True)

            # ---- sec_composition: ALL stock holdings per ETF per snapshot ----
            # Source 1 (preferred): comp_long — ALL holdings from per-file
            #   composition CSVs (download_szse_etf_composition.py). ~65 ETFs
            #   with complete holdings. Rank assigned 1..N by weight DESC.
            # Source 2 (fallback): top1..top5_* columns in merged_db — top 5
            #   from SZSE trend/archive CSVs. ~505 ETFs with only top 5.
            # Source 3: CSI index composition CSVs (download_index_composition.py).
            #   ALL constituents for CSI indices, source_type='index'.
            # ETFs with full composition (Source 1) are skipped in Source 2.
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
                        # Sort by weight DESC and assign rank 1..N
                        sub_sorted = sub.sort_values("_w", ascending=False).reset_index(drop=True)
                        rows_before = len(holdings_rows)
                        for rank_idx, (_, r) in enumerate(sub_sorted.iterrows(), start=1):
                            # stock_code in comp CSVs carries an exchange suffix
                            # (e.g. "300001.SZ"); keep it as-is so it matches
                            # stock_industry_map.stock_code (built from this table).
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
                        # Only mark this ETF as having full composition when at
                        # least one holdings row was actually added; otherwise
                        # the top5 fallback (Source 2) must still cover it.
                        if len(holdings_rows) > rows_before:
                            etf_codes_with_full_comp.add(code_str)
                            n_full += 1
                    print(f"    [DB] Built {len(holdings_rows):,} sec_composition rows (full comp) "
                          f"from {n_full} ETFs", flush=True)
            else:
                print(f"    [DB] No full composition data (comp_long empty)", flush=True)

            # Source 2: Top-5 fallback (top1..top5_* columns) for ETFs without
            # full composition data.
            n_top5 = 0
            n_top5_rows = 0
            if "comp_match_date" in merged_db.columns:
                snap_df = merged_db[
                    merged_db["comp_match_date"].notna()
                    & (merged_db["comp_match_date"].astype(str) != "")
                ].drop_duplicates(subset=["code", "comp_match_date"], keep="first")
                for _, row in snap_df.iterrows():
                    etf_code = str(row.get("code", "")).strip()
                    # Strip suffix to check against full-comp set
                    etf_stripped = etf_code.split(".")[0].zfill(6)
                    if etf_stripped in etf_codes_with_full_comp:
                        continue  # Already have full composition
                    snap_date = row.get("comp_match_date")
                    if not etf_code or snap_date is None:
                        continue
                    n_top5 += 1
                    for rank in range(1, 6):
                        stock_code = str(row.get(f"top{rank}_code", "")).strip()
                        if not stock_code:
                            continue
                        stock_name = str(row.get(f"top{rank}_name", "")).strip()
                        weight_pct = float(row.get(f"top{rank}_weight_pct", 0.0) or 0.0)
                        holdings_rows.append({
                            "snapshot_date": snap_date,
                            "code": etf_code,
                            "source_type": "etf",
                            "rank": rank,
                            "stock_code": stock_code,
                            "stock_name": stock_name,
                            "weight_pct": weight_pct,
                        })
                        n_top5_rows += 1
                print(f"    [DB] Built {n_top5_rows:,} sec_composition rows (top5 fallback) "
                      f"from {n_top5} ETFs", flush=True)
            else:
                print(f"    [DB] No comp_match_date column — skipping top5 fallback", flush=True)

            # Source 3: CSI index composition (download_index_composition.py)
            index_comp_rows = build_index_composition_rows(verbose=True)
            if index_comp_rows:
                holdings_rows.extend(index_comp_rows)
                print(f"    [DB] Built {len(index_comp_rows):,} sec_composition rows (index comp) "
                      f"from {len(set(r['code'] for r in index_comp_rows))} indices", flush=True)
            else:
                print(f"    [DB] No index composition data found", flush=True)

            if holdings_rows:
                inserted = await bulk_upsert_async(
                    conn, "stats.sec_composition", holdings_rows,
                    ["code", "snapshot_date", "rank"],
                )
                print(f"    [DB] Inserted {inserted:,} rows into stats.sec_composition", flush=True)
            else:
                print(f"    [DB] No rows to insert into stats.sec_composition", flush=True)

            # ---- etf_meta: per-ETF quality metrics for ranking (Feature 3) ----
            # n_ohlcv_days  : sufficient daily data (from uni_df)
            # has_margin    : any margin data
            # avg_volume_wan: mean daily volume (万) per ETF
            # data_quality_score = (n_ohlcv_days>=200?100:0) + (has_margin?50:0)
            #                    + volume_rank_component(0..50)
            avg_vol_by_code: dict = {}
            if "volume_wan" in merged_db.columns:
                avg_vol_by_code = merged_db.groupby("code")["volume_wan"].mean().to_dict()

            etf_meta_rows = []
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
                etf_meta_rows.append({
                    "code": code,
                    "name": str(row.get("name", "") or ""),
                    "n_ohlcv_days": n_days,
                    "has_margin": has_margin,
                    "avg_volume_wan": avg_vol,
                    "first_date": fd_date,
                    "last_date": ld_date,
                    "data_quality_score": base_score,  # volume rank added below
                })

            # Add volume-rank component (0..50): highest-volume ETF gets +50,
            # scaling down linearly by rank.
            if etf_meta_rows:
                by_vol = sorted(etf_meta_rows, key=lambda r: r["avg_volume_wan"], reverse=True)
                n_etf = len(by_vol)
                for rank_i, r in enumerate(by_vol):
                    r["data_quality_score"] += int(50 * (1.0 - rank_i / max(n_etf, 1)))

            if etf_meta_rows:
                inserted = await bulk_upsert_async(
                    conn, "stats.etf_meta", etf_meta_rows, ["code"]
                )
                print(f"    [DB] Inserted {inserted:,} rows into stats.etf_meta", flush=True)
            else:
                print(f"    [DB] No rows to insert into stats.etf_meta", flush=True)

            # (etf_composition table is deprecated — all holdings now go to
            #  stats.sec_composition above. See 08_etf_composition.sql for the DROP.)
        finally:
            await conn.close()

    print(f"\n  Theme distribution:", flush=True)
    for tid, sub in uni_df.groupby("theme_id"):
        print(f"    · {tid:<20s} {len(sub):>4d}", flush=True)

    print(f"\n  Exchange distribution:", flush=True)
    for exc, sub in uni_df.groupby("exchange"):
        print(f"    · {exc:<4s} {len(sub):>4d} ETFs", flush=True)

    print(f"\n  Wall time: {int(time.time()-t0)}s", flush=True)
    print("=" * 78, flush=True)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
