"""builds.bond.chinabond — China bond (中债国债) daily yield-curve builder.

Aggregates temps/chinabond/chinabond_bzqx_treasury_bond_*.csv yearly files
into a daily yield-curve frame → stats.debt_treasury (0d … 50y tenors).
CSV ONLY — canonical CSVs are produced by downloads (xlsx->csv conversion);
a missing CSV next to its xlsx source is a downloads bug and raises.
"""
from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd

from downloads._common import read_build_csv
from builds.bond.paths import CHINABOND_DIR

CHINABOND_TENOR_MAP = [
    ("0d",  "cb_0d"),
    ("1m",  "cb_1m"),
    ("2m",  "cb_2m"),
    ("3m",  "cb_3m"),
    ("6m",  "cb_6m"),
    ("9m",  "cb_9m"),
    ("1y",  "cb_1y"),
    ("2y",  "cb_2y"),
    ("3y",  "cb_3y"),
    ("5y",  "cb_5y"),
    ("7y",  "cb_7y"),
    ("10y", "cb_10y"),
    ("15y", "cb_15y"),
    ("20y", "cb_20y"),
    ("30y", "cb_30y"),
    ("40y", "cb_40y"),
    ("50y", "cb_50y"),
]


def assert_chinabond_converted() -> None:
    """Every chinabond xlsx must have its canonical csv counterpart."""
    root = CHINABOND_DIR
    xl = {os.path.basename(p).replace(".xlsx", ".csv")
          for p in glob.glob(os.path.join(root, "chinabond_bzqx_treasury_bond_*.xlsx"))}
    cs = set(os.path.basename(p)
             for p in glob.glob(os.path.join(root, "chinabond_bzqx_treasury_bond_*.csv")))
    missing = sorted(xl - cs)
    if missing:
        raise FileNotFoundError(
            f"[CHINABOND] {len(missing)} chinabond xlsx have no converted "
            f"CSV (e.g. {missing[:3]}) — downloads conversion bug, fix "
            f"downloads instead of reading xlsx in builds"
        )


def read_chinabond_csv(path):
    """Read one chinabond_bzqx_*.csv file (long format) and pivot wide."""
    try:
        df = read_build_csv(path, dtype={"日期": str, "标准期限说明": str})
    except Exception:
        return None
    if df is None or len(df) == 0:
        return None
    if "日期" not in df.columns or "标准期限说明" not in df.columns or "收益率(%)" not in df.columns:
        return None
    df["日期"] = df["日期"].astype(str).str.strip().str.replace("/", "-", regex=False)
    df = df[df["日期"].str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)]
    if len(df) == 0:
        return None
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    df = df.dropna(subset=["日期"])
    df["标准期限说明"] = df["标准期限说明"].astype(str).str.strip().str.lower()
    df["收益率(%)"] = pd.to_numeric(df["收益率(%)"], errors="coerce")
    wide = df.pivot_table(
        index="日期", columns="标准期限说明", values="收益率(%)", aggfunc="last",
    ).reset_index()
    return wide


def build_chinabond_df(start_date=None, end_date=None, verbose=True, files=None):
    """Aggregate all chinabond_bzqx_treasury_bond_*.csv files into a daily
    China bond yield-curve frame.

    Args:
        files: if provided, read only these files (incremental mode — caller
               already filtered to files overlapping with missing dates).
               If None, glob all files.
    """
    if files is None:
        assert_chinabond_converted()
        pattern = os.path.join(CHINABOND_DIR, "chinabond_bzqx_treasury_bond_*.csv")
        files = sorted(glob.glob(pattern))
    if verbose:
        print(f"    [CHINABOND] reading {len(files)} chinabond_bzqx_treasury_bond_*.csv files", flush=True)

    all_chunks = []
    n_bad: int = 0
    for path in files:
        df = read_chinabond_csv(path)
        if df is None or len(df) == 0:
            n_bad += 1
            continue
        all_chunks.append(df)
    if not all_chunks:
        return pd.DataFrame()
    big = pd.concat(all_chunks, ignore_index=True)
    rename = {src: tgt for src, tgt in CHINABOND_TENOR_MAP if src in big.columns}
    keep_cols = ["日期"] + [src for src, _ in CHINABOND_TENOR_MAP if src in big.columns]
    big = big[keep_cols].rename(columns=rename)
    big = big.rename(columns={"日期": "date"})
    tenor_cols = [tgt for _, tgt in CHINABOND_TENOR_MAP if tgt in big.columns]
    agg_dict = {c: lambda s: s.dropna().iloc[-1] if len(s.dropna()) else np.nan
                for c in tenor_cols}
    big = big.groupby("date", as_index=False).agg(agg_dict)
    big = big.sort_values("date").reset_index(drop=True)

    if start_date:
        big = big[big["date"] >= pd.Timestamp(start_date)]
    if end_date:
        big = big[big["date"] <= pd.Timestamp(end_date)]
    big = big.reset_index(drop=True)

    if verbose:
        if len(big):
            print(f"    [CHINABOND] {len(big)} daily yield-curve records "
                  f"(skipped {n_bad} bad files), "
                  f"{big['date'].min().date()} → {big['date'].max().date()}", flush=True)
            if "cb_1y" in big.columns and big["cb_1y"].notna().any():
                print(f"    [CHINABOND] 1Y yield range: "
                      f"{big['cb_1y'].min():.4f}% → {big['cb_1y'].max():.4f}%", flush=True)
        else:
            print(f"    [CHINABOND] no records in range", flush=True)
    return big
