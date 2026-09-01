"""builds.bond.pboc_oma — PBoC Open Market Announcements (公开市场业务公告) builder.

Reads temps/pboc_oma_news/oma_combined.csv (produced by download_pboc_oma.py)
→ stats.pboc_oma (composite PK date+title, no FK to debt_identity;
always truncate+reload since announcements may occur on non-trading
days and the dataset is small).
"""
from __future__ import annotations

import os

import pandas as pd

from builds.bond.paths import PBOC_OMA_CSV


def build_oma_df(start_date=None, end_date=None, verbose=True):
    """Build a PBoC OMA (Open Market Announcements) frame from oma_combined.csv.

    Each row is one high-level policy notice such as overnight-reverse-repo
    scheduling, outright-repo tool introduction, central-bank bill policy,
    MLF policy changes, or interest-rate adjustments. Multiple announcements
    can share a pub_date.

    NOTE: Primary dealer news (type='primary_dealer') is excluded from the
    database load as it's not relevant to the OMO analysis.

    Unlike the other debt_* tables, pboc_oma has NO foreign key to
    debt_identity (announcements may occur on non-trading days) and uses a
    composite PK (date, title).

    Returns DataFrame columns:
        date, title, type, content, detail_url, keywords,
        serial_year, serial_no, detail_slug
    """
    if verbose:
        print(f"    [PBOC-OMA] reading {PBOC_OMA_CSV}", flush=True)

    if not os.path.exists(PBOC_OMA_CSV):
        if verbose:
            print(f"    [PBOC-OMA] WARNING: {PBOC_OMA_CSV} not found; "
                  f"run `python download_pboc_oma.py` first.", flush=True)
        return pd.DataFrame()

    df = pd.read_csv(PBOC_OMA_CSV, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    if len(df) == 0:
        if verbose:
            print(f"    [PBOC-OMA] no records in {PBOC_OMA_CSV}", flush=True)
        return pd.DataFrame()

    df = df.rename(columns={"pub_date": "date"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])

    # Exclude primary dealer news from the database load
    df = df[df["type"] != "primary_dealer"].copy()
    if len(df) == 0:
        if verbose:
            print(f"    [PBOC-OMA] no records after excluding primary_dealer", flush=True)
        return pd.DataFrame()

    keep = ["date", "title", "type", "content", "detail_url",
            "keywords", "serial_year", "serial_no", "detail_slug"]
    df = df[keep].copy()
    df = df.sort_values(["date", "title"])

    if start_date:
        df = df[df["date"] >= pd.Timestamp(start_date)]
    if end_date:
        df = df[df["date"] <= pd.Timestamp(end_date)]

    if verbose:
        if len(df):
            type_counts = df["type"].value_counts().to_dict()
            type_summary = ", ".join(f"{t}={n}" for t, n in type_counts.items())
            print(f"    [PBOC-OMA] {len(df)} announcements, "
                  f"{df['date'].min().date()} → {df['date'].max().date()} "
                  f"[{type_summary}]", flush=True)
        else:
            print(f"    [PBOC-OMA] no records in range", flush=True)
    return df
