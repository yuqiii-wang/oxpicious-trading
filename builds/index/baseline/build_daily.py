"""Build the daily history DataFrame (full per-code history for MA correctness).

Reads all CSIndex *_history.csv + *_1m.csv files, plus the SZSE / SSE /
CNINDEX supplements (see loaders.py), then:
  1. Normalizes column names and units across sources.
  2. Concatenates sources; deduplicates (date, code) keeping the LAST source
     appended (priority: CNINDEX > SSE trend > SZSE > 1m > history).
  3. Backfills PE from CSIndex rows that lost the dedup (e.g. SSE trend won
     OHLCV for 000xxx codes but its PE is NULL).
  4. Optionally fills missing trading days with estimated closes.
  5. Computes moving averages (ma5, ma20, ma60, ma120, ma255) and exponential
     moving averages (ema6, ema10, ema20, ema60, ema120, ema255) over the
     FULL per-code history (must use ALL rows, not just missing, for correctness).
  6. Filters to (date, code) pairs NOT in `existing_keys` for the upsert.

The *_1m.csv files are CSIndex's "recent 1-month" daily export with bilingual
column headers (日期Date, 开盘Open, etc.). They are appended AFTER history
so drop_duplicates(keep="last") picks the 1m version for overlapping dates —
1m has the most recent data.
"""
from __future__ import annotations

import glob
import os

import pandas as pd

from _common.build_commons import parse_num, parse_date
from _common.df_utils import compute_moving_averages, compute_emas

from builds.index.baseline.paths import CSINDEX_DIR, VALID_CODE_RE
from builds.index.baseline.close_estimation import fill_missing_closes
from builds.index.baseline.loaders import (
    load_szse_index_history, load_sse_index_history, load_cnindex_history,
)


def _normalize_and_clean(df: pd.DataFrame, code: str) -> pd.DataFrame:
    """Apply backward-compat column renames + numeric parsing + date parsing
    + 亿元→yuan conversion. Shared by history and 1m loaders.
    """
    df["code"] = code

    # Backward compat: old CSVs use "turnover", new ones use "trading_amount"
    if "turnover" in df.columns and "trading_amount" not in df.columns:
        df = df.rename(columns={"turnover": "trading_amount"})
    # Backward compat: old CSVs use "shares", new schema uses "trading_shares"
    if "shares" in df.columns and "trading_shares" not in df.columns:
        df = df.rename(columns={"shares": "trading_shares"})
    # history/1m CSVs use "volume"/"amount"; DB schema uses "trading_shares"/"trading_amount"
    if "volume" in df.columns and "trading_shares" not in df.columns:
        df = df.rename(columns={"volume": "trading_shares"})
    if "amount" in df.columns and "trading_amount" not in df.columns:
        df = df.rename(columns={"amount": "trading_amount"})

    for col in ["open", "high", "low", "close", "trading_shares", "trading_amount", "change", "changePct", "pe", "consNumber"]:
        if col in df.columns:
            df[col] = df[col].apply(parse_num)
    # CSIndex history trading_amount is in 亿元 → convert to yuan to match
    # the "yuan everywhere" DB convention.
    if "trading_amount" in df.columns:
        df["trading_amount"] = df["trading_amount"] * 1e8  # 亿元 → yuan

    df["date"] = df["date"].apply(parse_date)
    df = df.dropna(subset=["date"])
    return df


def _normalize_1m_headers(df: pd.DataFrame, code: str) -> pd.DataFrame:
    """Normalize bilingual column headers of a *_1m.csv (e.g. 日期Date → date).

    After header normalization, delegates to _normalize_and_clean for numeric
    parsing + unit conversion.
    """
    rename_map = {}
    for col in df.columns:
        s = str(col)
        sl = s.lower()
        if "日期" in s or sl == "date":
            rename_map[col] = "date"
        elif "代码" in s and "code" in sl:
            rename_map[col] = "indexCode"
        elif "中文简称" in s:
            rename_map[col] = "indexName"
        elif "开盘" in s or sl == "open":
            rename_map[col] = "open"
        elif "最高" in s or sl == "high":
            rename_map[col] = "high"
        elif "最低" in s or sl == "low":
            rename_map[col] = "low"
        elif "收盘" in s or sl == "close":
            rename_map[col] = "close"
        elif "涨跌幅" in s or "change%" in sl or "changepct" in sl or "change(" in sl:
            rename_map[col] = "changePct"
        elif "涨跌" in s or sl == "change":
            rename_map[col] = "change"
        elif "成交量" in s or "volume" in sl:
            rename_map[col] = "volume"
        elif "成交金额" in s or "turnover" in sl or "amount" in sl:
            rename_map[col] = "amount"
        elif "样本" in s or "cons" in sl:
            rename_map[col] = "consNumber"
    df = df.rename(columns=rename_map)

    if "indexName" in df.columns:
        df["indexName"] = df["indexName"].fillna("")
    if "pe" not in df.columns:
        df["pe"] = None

    return _normalize_and_clean(df, code)


def build_daily_df(existing_keys: set, shared_weights: dict = None,
                   verbose: bool = True) -> pd.DataFrame:
    """Read all *_history.csv + *_1m.csv files, compute MAs, filter to
    missing (date, code) pairs.

    Args:
        existing_keys: set of (date, code) tuples already in stats.index_tech_stats.
                       Rows matching these keys are skipped before insert.
        shared_weights: dict of {(code_a, code_b): shared_weight} from
                        sec_composition.  Used to fill missing trading days
                        with estimated close prices.  If None, no estimation
                        is performed.

    Returns a DataFrame with MA columns, filtered to missing (date, code) pairs.
    """
    history_files = sorted(glob.glob(os.path.join(CSINDEX_DIR, "*_history.csv")))
    onem_files = sorted(glob.glob(os.path.join(CSINDEX_DIR, "*_1m.csv")))
    if verbose:
        print(f"    [DAILY] {len(history_files)} history + {len(onem_files)} 1m CSVs in {CSINDEX_DIR}", flush=True)

    dfs = []
    n_skipped_files = 0

    # ---- Load *_history.csv (full per-code history) -----------------------
    for path in history_files:
        try:
            df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue

        code = os.path.basename(path).replace("_history.csv", "")
        # Skip codes that violate the DB check constraint (e.g. CES100)
        if not VALID_CODE_RE.match(code):
            n_skipped_files += 1
            continue
        df = _normalize_and_clean(df, code)
        dfs.append(df)

    # ---- Load *_1m.csv (recent 1-month export, bilingual headers) --------
    # Appended AFTER history so drop_duplicates(keep="last") below picks
    # the 1m version for overlapping dates — 1m has the most recent data.
    n_1m_loaded = 0
    for path in onem_files:
        try:
            df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue

        code = os.path.basename(path).replace("_1m.csv", "")
        if not VALID_CODE_RE.match(code):
            continue

        df = _normalize_1m_headers(df, code)
        dfs.append(df)
        n_1m_loaded += 1

    if verbose and n_1m_loaded:
        print(f"    [DAILY] loaded {n_1m_loaded} 1m CSVs (appended last for override)", flush=True)

    # Also load SZSE index data (archive + trend) for 399001 / 399006 / 399237
    szse_dfs = load_szse_index_history(verbose=verbose)
    for df in szse_dfs:
        dfs.append(df)

    # Also load SSE index trend data (today's EOD snapshot for ~200 SSE indices)
    sse_dfs = load_sse_index_history(verbose=verbose)
    for df in sse_dfs:
        dfs.append(df)

    # Also load CNINDEX (国证指数) history for 399303 / 399310 / 399311
    cnindex_dfs = load_cnindex_history(verbose=verbose)
    for df in cnindex_dfs:
        dfs.append(df)

    if n_skipped_files and verbose:
        print(f"    [DAILY] skipped {n_skipped_files} files (all dates already in DB)", flush=True)

    if not dfs:
        print("    [WARN] No new daily data to process", flush=True)
        return pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True)
    combined["code"] = combined["code"].astype(str).str.strip()
    combined = combined.sort_values(["code", "date"]).reset_index(drop=True)

    # Build a PE lookup from ALL sources BEFORE dedup. CSIndex history/1m
    # DataFrames carry PE (peg) from the CSIndex API; SSE trend and SZSE
    # data do not. After dedup, SSE trend wins for 000xxx codes (fresh OHLCV)
    # but its PE is NULL — this lookup fills those gaps.
    pe_lookup: dict = {}
    if "pe" in combined.columns:
        pe_rows = combined[combined["pe"].notna()]
        for _, row in pe_rows.iterrows():
            pe_lookup[(row["date"], row["code"])] = row["pe"]
        if verbose and pe_lookup:
            print(f"    [DAILY] PE lookup: {len(pe_lookup):,} (date, code) pairs with PE "
                  f"(from CSIndex)", flush=True)

    # Deduplicate (date, code) pairs: keep="last" picks the 1m version over
    # the history version for overlapping dates, since 1m DataFrames are
    # appended after history. SSE trend (appended after CSIndex) wins for
    # 000xxx codes — its fresh OHLCV is preserved. CNINDEX (appended last)
    # wins for 399303/399310/399311.
    n_before_dedup = len(combined)
    combined = combined.drop_duplicates(subset=["date", "code"], keep="last")
    combined = combined.reset_index(drop=True)
    n_after_dedup = len(combined)
    if verbose and n_before_dedup != n_after_dedup:
        print(f"    [DAILY] dedup: {n_before_dedup:,} → {n_after_dedup:,} rows "
              f"(1m/SZSE/SSE/CNINDEX overrode history for {n_before_dedup - n_after_dedup:,} dates)",
              flush=True)

    # Fill missing PE from the pre-dedup lookup. SSE trend rows won the dedup
    # for 000xxx codes but have NULL PE; CSIndex rows (which lost the dedup)
    # had PE — this merges it back without overriding OHLCV.
    if pe_lookup and "pe" in combined.columns:
        n_pe_missing_before = combined["pe"].isna().sum()
        combined["pe"] = combined.apply(
            lambda r: pe_lookup.get((r["date"], r["code"]), r["pe"])
            if pd.isna(r["pe"]) else r["pe"],
            axis=1,
        )
        n_pe_filled = n_pe_missing_before - combined["pe"].isna().sum()
        if verbose and n_pe_filled:
            print(f"    [DAILY] PE merge: filled {n_pe_filled:,} NULL PE values from CSIndex lookup",
                  flush=True)

    # Fill missing trading days with estimated close prices (if shared weights available)
    if shared_weights:
        combined = fill_missing_closes(combined, shared_weights, verbose=verbose)

    # Compute MAs over full per-code history (must use ALL rows, not just missing)
    compute_moving_averages(
        combined,
        group_key="code",
        value_col="close",
        windows=[5, 20, 60, 120, 255],
    )
    # Compute EMAs over full per-code history (same correctness constraint).
    # Stays on pandas: cuDF lacks grouped-ewm support (see
    # analyze/mov_ave_spread/rsi.py for the same constraint).
    compute_emas(
        combined,
        group_key="code",
        value_col="close",
        spans=[6, 10, 20, 60, 120, 255],
    )

    # Filter to missing (date, code) pairs only — this is the key optimization
    mask = combined.apply(lambda r: (r["date"], r["code"]) not in existing_keys, axis=1)
    combined = combined[mask].reset_index(drop=True)

    if verbose:
        print(f"    → {len(combined):,} new rows  ·  {combined['code'].nunique()} indexes", flush=True)
        if len(combined):
            print(f"    → date range: {combined['date'].min()} → {combined['date'].max()}", flush=True)

    return combined
