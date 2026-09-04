"""builds.bond.instruments — PBoC combined instruments CSV loading.

Loads temp_data/analysis_output/pboc_repo_news/instruments_combined.csv
(one row per parsed instrument) with process-level caching, plus the
duration-token helper shared by all instrument-based builders.
"""
from __future__ import annotations

import os
import re

import numpy as np
import pandas as pd

from builds._commons.safe_parse import safe_to_datetime
from builds.bond.paths import PBOC_INSTRUMENTS_CSV


def parse_duration_to_days(dur_str: str):
    """Convert a duration token like '7D', '6M', '1Y', '91D' to integer days.

    Month durations are approximated at 30 days; year durations at 365 days;
    pure-day durations are used verbatim.
    """
    s = str(dur_str).strip().upper()
    if not s:
        return None
    m = re.match(r"^(\d+)\s*([DMY])$", s)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    if unit == "D":
        return n
    if unit == "M":
        return n * 30
    if unit == "Y":
        return n * 365
    return None


# Cached instruments dataframe (loaded once per process)
_INSTRUMENTS_DF_CACHE: "pd.DataFrame | None" = None


def load_pboc_instruments_df(csv_path: str = PBOC_INSTRUMENTS_CSV,
                             start_date=None, end_date=None,
                             verbose: bool = False) -> "pd.DataFrame":
    """Load the combined PBoC instruments CSV (one row per parsed instrument).

    Returns a DataFrame with columns:
        pub_date, category, title, detail_url, serial_year, serial_no,
        detail_slug, instrument, tenor, start_date, quantity, rate, end_date,
        parse_warnings, source_file
    """
    global _INSTRUMENTS_DF_CACHE
    if _INSTRUMENTS_DF_CACHE is None:
        if not os.path.exists(csv_path):
            if verbose:
                print(f"    [PBOC-INSTR] WARNING: {csv_path} not found; "
                      f"run `python download_pboc_repo_news.py --reparse` first.", flush=True)
            return pd.DataFrame()
        df = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        df["pub_date"] = safe_to_datetime(df["pub_date"]).astype("datetime64[ns]")
        df = df.dropna(subset=["pub_date"])
        df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
        df["rate"] = pd.to_numeric(df["rate"], errors="coerce")
        _INSTRUMENTS_DF_CACHE = df

    df = _INSTRUMENTS_DF_CACHE
    if df is None or len(df) == 0:
        return df
    df = df.copy()
    # np.datetime64 bounds: pd.Timestamp(start_date) takes the cudf slow
    # path per comparison (proxy Timestamp cannot transform)
    if start_date:
        df = df[df["pub_date"] >= np.datetime64(start_date, "ns")]
    if end_date:
        df = df[df["pub_date"] <= np.datetime64(end_date, "ns")]
    return df
