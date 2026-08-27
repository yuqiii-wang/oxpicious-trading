"""builds.bond.pboc_lpr — PBoC LPR monthly announcements builder.

Reads temps/pboc_lpr_news/lpr_combined.csv (produced by
download_pboc_lpr_news.py) → stats.debt_lpr (date, lpr_1y, lpr_5y).
"""
from __future__ import annotations

import os

import pandas as pd

from builds.bond.paths import PBOC_LPR_CSV


def build_lpr_df(start_date=None, end_date=None, verbose=True):
    """Build a daily LPR frame from the combined LPR CSV.

    Each row is one monthly LPR announcement with two tenor rates
    (1Y and 5Y+).

    Returns DataFrame columns: date, lpr_1y, lpr_5y
    """
    if verbose:
        print(f"    [PBOC-LPR] reading {PBOC_LPR_CSV}", flush=True)

    if not os.path.exists(PBOC_LPR_CSV):
        if verbose:
            print(f"    [PBOC-LPR] WARNING: {PBOC_LPR_CSV} not found; "
                  f"run `python download_pboc_lpr_news.py` first.", flush=True)
        return pd.DataFrame()

    df = pd.read_csv(PBOC_LPR_CSV, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    if len(df) == 0:
        if verbose:
            print(f"    [PBOC-LPR] no records in {PBOC_LPR_CSV}", flush=True)
        return pd.DataFrame()

    df = df.rename(columns={"pub_date": "date"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    df["lpr_1y"] = pd.to_numeric(df["lpr_1y"], errors="coerce")
    df["lpr_5y"] = pd.to_numeric(df["lpr_5y"], errors="coerce")

    keep = ["date", "lpr_1y", "lpr_5y"]
    df = df[keep].copy()
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    # Drop duplicates on date (keep last — should never happen with proper downloads)
    df = df.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)

    if start_date:
        df = df[df["date"] >= pd.Timestamp(start_date)]
    if end_date:
        df = df[df["date"] <= pd.Timestamp(end_date)]
    df = df.reset_index(drop=True)

    if verbose:
        if len(df):
            print(f"    [PBOC-LPR] {len(df)} monthly LPR announcements, "
                  f"{df['date'].min().date()} → {df['date'].max().date()}", flush=True)
            if df["lpr_1y"].notna().any():
                print(f"    [PBOC-LPR] 1Y range: {df['lpr_1y'].min():.4f}% → "
                      f"{df['lpr_1y'].max():.4f}%", flush=True)
            if df["lpr_5y"].notna().any():
                print(f"    [PBOC-LPR] 5Y+ range: {df['lpr_5y'].min():.4f}% → "
                      f"{df['lpr_5y'].max():.4f}%", flush=True)
        else:
            print(f"    [PBOC-LPR] no records in range", flush=True)
    return df
