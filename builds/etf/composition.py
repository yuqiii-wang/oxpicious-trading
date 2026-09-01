"""ETF composition CSV reader.

Reads per-file composition CSVs produced by download_szse_etf_composition.py
and returns (comp_long, comp_universe).

GPU-native reads: NO ``encoding`` kwarg (it forces a CPU fallback on EVERY
file — 16K files here); the UTF-8 BOM is stripped from the first column
name post-read instead. The universe frame is built with vectorized
groupby aggregations (no per-ETF Python loop).
"""
import glob
import os
from collections import Counter

import pandas as pd

from _common.df_utils import safe_columns
from builds.etf.paths import COMP_DIR

COMBINED_COLS = [
    "trade_date", "etf_code", "etf_name", "fund_type", "target_index",
    "nav_per_unit", "min_unit_nav",
    "stock_code", "stock_name", "shares", "cash_sub_flag", "market",
]

# Column dtypes: one-pass dtype contract — final dtypes assigned AT PARSE
# TIME (str code/label columns; float64 numerics; a parse error would be a
# downloads bug and stop the run).
_DTYPE_STR_COLS = (
    "trade_date", "etf_code", "etf_name", "fund_type", "target_index",
    "stock_code", "stock_name", "cash_sub_flag", "market",
)
_DTYPE_FLOAT_COLS = ("nav_per_unit", "min_unit_nav", "shares")


def _read_one(path: str) -> pd.DataFrame | None:
    """GPU-native CSV read (no encoding kwarg; BOM stripped post-read)."""
    try:
        df = pd.read_csv(
            path,
            dtype={
                **{c: str for c in _DTYPE_STR_COLS},
                **{c: "float64" for c in _DTYPE_FLOAT_COLS},
            },
            keep_default_na=False,
            na_values=[""],
        )
    except Exception:
        return None
    if df is None or len(df) == 0:
        return None
    cols = safe_columns(df)
    if cols and cols[0].startswith("\ufeff"):
        df = df.rename(columns={cols[0]: cols[0].lstrip("\ufeff")})
    return df


_FILENAME_PREFIX = "szse_etf_comp_"


def _filename_comp_key(path: str) -> str | None:
    """'szse_etf_comp_YYYYMMDD_<bare6>.csv' → '<bare6>|<YYYYMMDD>'.

    The DB gate keys are built the same way from stats.sec_composition
    (code stripped to its bare 6-digit part + snapshot_date), so a file
    whose (code, snapshot) pair already exists can be skipped without
    being opened.
    """
    base = os.path.basename(path)
    if not base.startswith(_FILENAME_PREFIX) or not base.endswith(".csv"):
        return None
    parts = base[len(_FILENAME_PREFIX):-len(".csv")].split("_")
    if len(parts) != 2 or len(parts[0]) != 8 or len(parts[1]) != 6:
        return None
    return parts[1] + "|" + parts[0]


def build_composition(verbose=True, code=None, existing_ymd_keys=None):
    """Read per-file composition CSVs and return (comp_long, comp_universe).

    When *code* is set (canonical "NNNNNN.SZ/.SS" or bare 6-digit), only that
    ETF's per-file CSVs are read: filenames end with the bare code
    (``szse_etf_comp_YYYYMMDD_<code>.csv``), so a --code build never parses
    other ETFs' holdings.

    When *existing_ymd_keys* is a set of ``"<bare6>|<YYYYMMDD>"`` strings
    (the (code, snapshot_date) pairs already in stats.sec_composition),
    matching files are skipped BEFORE any read/parse — the gate-first fix
    for the nightly run (a full parse of all 16K files costs ~172s; a
    nightly incremental parses only the 1-2 new snapshots).

    No CSV output — caller inserts directly to database.
    """
    if code is not None:
        bare = str(code).split(".")[0].strip().zfill(6)
        files = sorted(glob.glob(
            os.path.join(COMP_DIR, f"szse_etf_comp_*_{bare}.csv")))
    else:
        files = sorted(glob.glob(os.path.join(COMP_DIR, "szse_etf_comp_*.csv")))
    if existing_ymd_keys is not None:
        n_all = len(files)
        files = [f for f in files
                 if (k := _filename_comp_key(f)) is None
                 or k not in existing_ymd_keys]
        if verbose and n_all - len(files):
            print(f"    [COMP] DB gate: skipped {n_all - len(files)} of "
                  f"{n_all} per-file CSVs (snapshots already in DB)", flush=True)
    if verbose:
        print(f"    [COMP] {len(files)} per-file CSVs to read in {COMP_DIR}", flush=True)

    counts = Counter()
    dfs = []
    for path in files:
        df = _read_one(path)
        if df is None:
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
    del dfs
    combined_cols = safe_columns(combined)
    for c in COMBINED_COLS:
        if c not in combined_cols:
            combined[c] = None
    combined = combined[COMBINED_COLS]

    # trade_date comes back as a clean ISO string; parse vectorized.
    combined["trade_date"] = pd.to_datetime(combined["trade_date"])
    combined = combined.dropna(subset=["trade_date"])
    combined = combined.sort_values(
        ["etf_code", "trade_date", "stock_code"],
    ).reset_index(drop=True)

    if verbose:
        print(f"    [COMP] {len(combined):,} rows, "
              f"{combined['etf_code'].nunique()} ETFs, "
              f"{combined['trade_date'].nunique()} dates", flush=True)

    universe = _build_universe(combined)

    print(f"    [STATS] parsed={counts['parsed']} failed={counts['failed']} "
          f"total_holdings={counts['holdings']:,}", flush=True)
    return combined, universe


def _build_universe(combined: pd.DataFrame) -> pd.DataFrame:
    """Vectorized per-ETF universe frame (one row per etf_code).

    Replaces the former per-ETF Python loop (sort + .iloc[-1] +
    dropna().iloc[0] per iteration — hundreds of cudf fallbacks per ETF).
    All parts are index-aligned (etf_code) Series; concat aligns them.
    """
    # Latest snapshot per ETF: all rows on each code's max trade_date.
    max_date = combined.groupby("etf_code")["trade_date"].transform("max")
    latest = combined[combined["trade_date"] == max_date]

    g = combined.groupby("etf_code", sort=True)
    # First non-null label per code (groupby.first skips NaN).
    labels = g.first()[["etf_name", "fund_type"]]

    # target_index: last NON-EMPTY value per code.
    ti = combined[combined["target_index"].fillna("").str.strip() != ""]
    ti_last = ti.groupby("etf_code", sort=True).tail(1).set_index("etf_code")[
        ["target_index"]
    ]

    universe = pd.concat(
        [
            labels,
            ti_last,
            g["trade_date"].nunique().rename("n_dates"),
            g["trade_date"].max().dt.strftime("%Y-%m-%d").rename(
                "latest_date"),
            latest.groupby("etf_code", sort=True).size().rename(
                "n_holdings_latest"),
            latest[latest["cash_sub_flag"].fillna("") != "必须"]
            .groupby("etf_code", sort=True).size().rename("n_equity_latest"),
        ],
        axis=1,
    ).reset_index()

    # Codes missing from a part (e.g. no non-empty target_index, no
    # equity rows) surface as NaN — fill with the same defaults the old
    # loop produced.
    for c in ("etf_name", "fund_type", "target_index", "latest_date"):
        universe[c] = universe[c].fillna("")
    for c in ("n_dates", "n_holdings_latest", "n_equity_latest"):
        universe[c] = universe[c].fillna(0).astype("int64")

    universe_cols = [
        "etf_code", "etf_name", "fund_type", "target_index",
        "n_dates", "latest_date", "n_holdings_latest", "n_equity_latest",
    ]
    return universe[universe_cols].sort_values(
        "etf_code").reset_index(drop=True)
