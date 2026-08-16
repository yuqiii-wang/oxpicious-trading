"""ETF composition CSV reader.

Reads per-file composition CSVs produced by download_szse_etf_composition.py
and returns (comp_long, comp_universe).
"""
import glob
import os
from collections import Counter

import pandas as pd

from builds.etf.paths import COMP_DIR

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
