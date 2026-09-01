"""builds.bond.shibor — SHIBOR daily fixing rates builder.

Aggregates temps/shibor/shibor_his_*.csv yearly chunks into a daily
SHIBOR frame → stats.debt_shibor (O/N, 1W, 2W, 1M, 3M, 6M, 9M, 1Y).
CSV ONLY — canonical CSVs are produced by downloads (xlsx->csv conversion);
a missing CSV next to its xlsx source is a downloads bug and raises.
"""
from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd

from downloads._common import read_build_csv
from builds.bond.paths import SHIBOR_DIR

SHIBOR_TENOR_MAP = [
    ("O/N", "shibor_o_n"),
    ("1W",  "shibor_1w"),
    ("2W",  "shibor_2w"),
    ("1M",  "shibor_1m"),
    ("3M",  "shibor_3m"),
    ("6M",  "shibor_6m"),
    ("9M",  "shibor_9m"),
    ("1Y",  "shibor_1y"),
]


def assert_shibor_converted() -> None:
    """Every shibor xlsx must have its canonical csv counterpart."""
    root = SHIBOR_DIR
    xl = {os.path.basename(p).replace(".xlsx", ".csv")
          for p in glob.glob(os.path.join(root, "shibor_his_*.xlsx"))}
    cs = set(os.path.basename(p)
             for p in glob.glob(os.path.join(root, "shibor_his_*.csv")))
    missing = sorted(xl - cs)
    if missing:
        raise FileNotFoundError(
            f"[SHIBOR] {len(missing)} shibor_his_*.xlsx have no converted "
            f"CSV (e.g. {missing[:3]}) — downloads conversion bug, fix "
            f"downloads instead of reading xlsx in builds"
        )


def read_shibor_csv(path):
    """Read one shibor_his_*.csv file. Returns DataFrame indexed by date."""
    try:
        df = read_build_csv(path, dtype={"日期": str})
    except Exception:
        return None
    if df is None or len(df) == 0:
        return None
    if "日期" not in df.columns:
        return None
    df["日期"] = df["日期"].astype(str).str.strip()
    df = df[df["日期"].str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)]
    if len(df) == 0:
        return None
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    df = df.dropna(subset=["日期"])
    for src_col, _ in SHIBOR_TENOR_MAP:
        if src_col in df.columns:
            df[src_col] = pd.to_numeric(df[src_col], errors="coerce")
    return df


def build_shibor_df(start_date=None, end_date=None, verbose=True, files=None):
    """Aggregate all shibor_his_*.csv chunks into a daily SHIBOR frame.

    Args:
        files: if provided, read only these files (incremental mode — caller
               already filtered to files overlapping with missing dates).
               If None, glob all files.
    """
    if files is None:
        assert_shibor_converted()
        pattern = os.path.join(SHIBOR_DIR, "shibor_his_*.csv")
        files = sorted(glob.glob(pattern))
    if verbose:
        print(f"    [SHIBOR] reading {len(files)} shibor_his_*.csv files", flush=True)

    all_chunks = []
    n_bad: int = 0
    for path in files:
        df = read_shibor_csv(path)
        if df is None or len(df) == 0:
            n_bad += 1
            continue
        all_chunks.append(df)
    if not all_chunks:
        return pd.DataFrame()
    big = pd.concat(all_chunks, ignore_index=True)
    big = big.sort_values("日期")
    rename = {src: tgt for src, tgt in SHIBOR_TENOR_MAP if src in big.columns}
    keep_cols = ["日期"] + [src for src, _ in SHIBOR_TENOR_MAP if src in big.columns]
    big = big[keep_cols].rename(columns=rename)
    agg_dict = {tgt: lambda s: s.dropna().iloc[-1] if len(s.dropna()) else np.nan
                for _, tgt in SHIBOR_TENOR_MAP if tgt in big.columns}
    big = big.groupby("日期", as_index=False).agg(agg_dict)
    big = big.rename(columns={"日期": "date"})
    big["date"] = pd.to_datetime(big["date"], errors="coerce")
    # groupby(as_index=False) already sorted by date and returned a fresh
    # index — no re-sort/reset; the trailing masks yield fresh frames
    big = big.dropna(subset=["date"])

    if start_date:
        big = big[big["date"] >= pd.Timestamp(start_date)]
    if end_date:
        big = big[big["date"] <= pd.Timestamp(end_date)]

    if verbose:
        if len(big):
            print(f"    [SHIBOR] {len(big)} daily SHIBOR records "
                  f"(skipped {n_bad} bad chunks), "
                  f"{big['date'].min().date()} → {big['date'].max().date()}", flush=True)
            if "shibor_o_n" in big.columns and big["shibor_o_n"].notna().any():
                print(f"    [SHIBOR] O/N range: {big['shibor_o_n'].min():.4f}% → "
                      f"{big['shibor_o_n'].max():.4f}%", flush=True)
        else:
            print(f"    [SHIBOR] no records in range", flush=True)
    return big
