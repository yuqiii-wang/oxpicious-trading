"""
build_debt_baseline.py — Build debt-market baseline and insert directly to the
database (no intermediate CSV).

Aggregates daily-frequency data sources into database tables:

  1. PBoC OMO (Open Market Operations) daily reverse-repo announcements
     read from temp_data/analysis_output/pboc_repo_news/instruments_combined.csv
     → debt_omo table

  2. PBoC outright-repo tender announcements as date markers
     → debt_outright_repo table

  3. PBoC MLF (Medium-term Lending Facility) tender
     → debt_mlf table

  4. Repo lifecycle tracking (running cumulative)
     → debt_repo table

  5. SHIBOR daily fixing rates (O/N, 1W, 2W, 1M, 3M, 6M, 9M, 1Y)
     read from temps/shibor/shibor_his_*.xlsx
     → debt_shibor table

  6. China bond (中债国债) daily yield-curve data
     read from temps/chinabond/chinabond_bzqx_treasury_bond_*.xlsx
     → debt_treasury table

  7. PBoC LPR (Loan Prime Rate) monthly announcements (1Y + 5Y+ tenors)
     read from temps/pboc_lpr_news/lpr_combined.csv
     → debt_lpr table

  8. PBoC Open Market Announcements (公开市场业务公告) policy notices
     read from temps/pboc_oma_news/oma_combined.csv
     → pboc_oma table (composite PK date+title, no FK to debt_identity;
     always truncate+reload since announcements may occur on non-trading
     days and the dataset is small)

Missing-data detection flow (DB-first):
  1. Glob source files (filenames only — no reading)
  2. Read instruments CSV + LPR CSV (single files, fast) to discover
     available dates
  3. Query stats.debt_identity by index for existing dates
  4. missing_dates = available_dates - existing_dates
  5. If no missing dates: exit early (DB is up to date)
  6. Read OMO (full history for repo cumulative) + outright + MLF from
     instruments CSV; read LPR from lpr_combined.csv
  7. Filter SHIBOR/China bond yearly files to only those overlapping with
     missing dates' years, then read them
  8. After reading, check for additional dates from SHIBOR/China bond not
     in the instruments CSV
  9. Filter all frames to missing dates and bulk upsert into the 8 debt_* tables

With --force: truncate all 8 debt_* tables first, so all source dates are
treated as missing.

Usage:
  python build_debt_baseline.py
  python build_debt_baseline.py --start-date 2024-01-01 --end-date 2026-07-14
  python build_debt_baseline.py --force

Prerequisite:
  Run `python download_pboc_repo_news.py --reparse` first to (re)generate
  temp_data/analysis_output/pboc_repo_news/instruments_combined.csv.
  Run `python download_pboc_lpr_news.py` first to (re)generate
  temps/pboc_lpr_news/lpr_combined.csv.
  Run `python download_pboc_oma.py` first to (re)generate
  temps/pboc_oma_news/oma_combined.csv.
"""
import os
import re
import sys
import glob
import time
import argparse
from datetime import datetime, timedelta

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _build_commons import (
    setup_utf8_stdout, add_common_build_args, get_db_or_exit,
    find_missing_dates, parse_num, print_build_header, print_wall_time,
    glob_source_files, PROJECT_ROOT, TODAY_STR,
    bulk_upsert_async, truncate_table_async,
)

setup_utf8_stdout()

import asyncio

# ============================================================================
# Paths
# ============================================================================
PBOC_INSTRUMENTS_CSV = os.path.join(
    PROJECT_ROOT, "temp_data", "analysis_output", "pboc_repo_news", "instruments_combined.csv"
)
PBOC_LPR_CSV        = os.path.join(
    PROJECT_ROOT, "temps", "pboc_lpr_news", "lpr_combined.csv"
)
PBOC_OMA_CSV        = os.path.join(
    PROJECT_ROOT, "temps", "pboc_oma_news", "oma_combined.csv"
)
SHIBOR_DIR          = os.path.join(PROJECT_ROOT, "temps", "shibor")
CHINABOND_DIR       = os.path.join(PROJECT_ROOT, "temps", "chinabond")


# ============================================================================
# Helpers
# ============================================================================
def parse_duration_to_days(dur_str):
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
        df["pub_date"] = pd.to_datetime(df["pub_date"], errors="coerce")
        df = df.dropna(subset=["pub_date"])
        df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
        df["rate"] = pd.to_numeric(df["rate"], errors="coerce")
        _INSTRUMENTS_DF_CACHE = df

    df = _INSTRUMENTS_DF_CACHE
    if df is None or len(df) == 0:
        return df
    df = df.copy()
    if start_date:
        df = df[df["pub_date"] >= pd.Timestamp(start_date)]
    if end_date:
        df = df[df["pub_date"] <= pd.Timestamp(end_date)]
    return df


# ============================================================================
# (1) PBoC OMO transaction announcements  → daily OMO rate / qty / tenor
# ============================================================================
def build_pboc_omo_df(start_date=None, end_date=None, verbose=True):
    """Build a daily OMO operations frame from the combined instruments CSV.

    Returns DataFrame columns:
        date, omo_rate, omo_quantity, omo_tenor_days, omo_tenor_label,
        omo_all_rates, omo_all_tenors, omo_all_quantities, omo_dur_qty_pairs
    """
    if verbose:
        print(f"    [PBOC-OMO] reading {PBOC_INSTRUMENTS_CSV}", flush=True)

    inst = load_pboc_instruments_df(start_date=start_date, end_date=end_date, verbose=verbose)
    if inst is None or len(inst) == 0:
        if verbose:
            print(f"    [PBOC-OMO] no records in range", flush=True)
        return pd.DataFrame()

    inst = inst[inst["category"] == "omo_transaction"].copy()
    if len(inst) == 0:
        if verbose:
            print(f"    [PBOC-OMO] no omo_transaction records in range", flush=True)
        return pd.DataFrame()

    rows = []
    for pub_date, sub in inst.groupby(inst["pub_date"].dt.normalize()):
        sub = sub.reset_index(drop=True)
        repo_entries = sub[sub["instrument"] == "reverse_repo"]
        has_reverse_repo = len(repo_entries) > 0

        if has_reverse_repo:
            r = repo_entries.iloc[0]
            primary_rate = r["rate"]
            primary_qty = r["quantity"]
            primary_tenor = r["tenor"] or ""
        else:
            primary_rate = np.nan
            primary_qty = np.nan
            primary_tenor = ""

        rr_mlf = sub[sub["instrument"].isin(["reverse_repo", "MLF"])]
        all_rates = [f"{v:g}" for v in rr_mlf["rate"].dropna().tolist()]
        all_tenors = [str(t) for t in rr_mlf["tenor"].tolist() if t]
        all_qtys = [f"{v:g}" for v in rr_mlf["quantity"].dropna().tolist()]
        dur_qty_pairs = [
            f"{t}:{v:g}"
            for t, v in zip(rr_mlf["tenor"], rr_mlf["quantity"])
            if t and pd.notna(v)
        ]

        rows.append({
            "date":                  pub_date,
            "omo_rate":              primary_rate,
            "omo_quantity":          primary_qty,
            "omo_tenor_days":        parse_duration_to_days(primary_tenor),
            "omo_tenor_label":       primary_tenor,
            "omo_all_rates":         "|".join(all_rates),
            "omo_all_tenors":        "|".join(all_tenors),
            "omo_all_quantities":    "|".join(all_qtys),
            "omo_dur_qty_pairs":     "|".join(dur_qty_pairs),
            "omo_has_reverse_repo":  has_reverse_repo,
        })

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    df = df.sort_values(["date", "omo_has_reverse_repo"], ascending=[True, False])
    df = df.drop_duplicates(subset=["date"], keep="first").reset_index(drop=True)
    df = df[df["omo_has_reverse_repo"] == True].reset_index(drop=True)
    df = df.drop(columns=["omo_has_reverse_repo"])

    if verbose:
        if len(df):
            print(f"    [PBOC-OMO] parsed {len(df)} daily OMO records, "
                  f"{df['date'].min().date()} → {df['date'].max().date()}", flush=True)
            if df["omo_rate"].notna().any():
                print(f"    [PBOC-OMO] omo_rate range: "
                      f"{df['omo_rate'].min():.4f}% → {df['omo_rate'].max():.4f}%", flush=True)
            if df["omo_quantity"].notna().any():
                print(f"    [PBOC-OMO] omo_quantity range: "
                      f"{df['omo_quantity'].min():g} → {df['omo_quantity'].max():g} 亿元", flush=True)
        else:
            print(f"    [PBOC-OMO] no records in range", flush=True)
    return df


# ============================================================================
# (2) PBoC outright-repo tender announcements  → daily marker
# ============================================================================
def build_pboc_outright_repo_df(start_date=None, end_date=None, verbose=True):
    """Build a daily outright-repo marker frame from the combined instruments CSV."""
    if verbose:
        print(f"    [PBOC-OUTRIGHT] reading {PBOC_INSTRUMENTS_CSV}", flush=True)

    inst = load_pboc_instruments_df(start_date=start_date, end_date=end_date, verbose=verbose)
    if inst is None or len(inst) == 0:
        if verbose:
            print(f"    [PBOC-OUTRIGHT] no records in range", flush=True)
        return pd.DataFrame()

    inst = inst[inst["instrument"] == "outright_repo"].copy()
    if len(inst) == 0:
        if verbose:
            print(f"    [PBOC-OUTRIGHT] no outright_repo instruments in range", flush=True)
        return pd.DataFrame()

    inst = inst.reset_index(drop=True)
    inst["outright_repo_marker"] = 1
    inst["outright_repo_tenor_days"] = inst["tenor"].map(parse_duration_to_days)
    inst["outright_repo_tenor_label"] = inst["tenor"].fillna("")
    inst["outright_repo_serial"] = inst.apply(
        lambda r: f"{r['serial_year']}#{r['serial_no']}" if r.get("serial_no") else "",
        axis=1,
    )
    inst = inst.rename(columns={
        "pub_date":  "date",
        "quantity":  "outright_repo_quantity",
    })
    keep = ["date", "outright_repo_marker", "outright_repo_quantity",
            "outright_repo_tenor_days", "outright_repo_tenor_label",
            "outright_repo_serial"]
    df = inst[keep].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    df = df.groupby("date", as_index=False).agg({
        "outright_repo_marker":          "max",
        "outright_repo_quantity":        "sum",
        "outright_repo_tenor_days":      "min",
        "outright_repo_tenor_label":     lambda s: "|".join(s.astype(str)),
        "outright_repo_serial":          lambda s: "|".join(s.astype(str)),
    })
    df = df.sort_values("date").reset_index(drop=True)

    if verbose:
        if len(df):
            print(f"    [PBOC-OUTRIGHT] {len(df)} outright-repo announcements, "
                  f"{df['date'].min().date()} → {df['date'].max().date()}", flush=True)
            if df["outright_repo_quantity"].notna().any():
                print(f"    [PBOC-OUTRIGHT] quantity range: "
                      f"{df['outright_repo_quantity'].min():g} → "
                      f"{df['outright_repo_quantity'].max():g} 亿元", flush=True)
        else:
            print(f"    [PBOC-OUTRIGHT] no records in range", flush=True)
    return df


def build_pboc_mlf_df(start_date=None, end_date=None, verbose=True):
    """Build a daily MLF marker frame from the combined instruments CSV."""
    if verbose:
        print(f"    [PBOC-MLF] reading {PBOC_INSTRUMENTS_CSV}", flush=True)

    inst = load_pboc_instruments_df(start_date=start_date, end_date=end_date, verbose=verbose)
    if inst is None or len(inst) == 0:
        if verbose:
            print(f"    [PBOC-MLF] no records in range", flush=True)
        return pd.DataFrame()

    inst = inst[inst["instrument"] == "MLF"].copy()
    if len(inst) == 0:
        if verbose:
            print(f"    [PBOC-MLF] no MLF instruments in range", flush=True)
        return pd.DataFrame()

    inst = inst.reset_index(drop=True)
    inst["mlf_marker"] = 1
    inst["mlf_tenor_days"] = inst["tenor"].map(parse_duration_to_days)
    inst["mlf_tenor_label"] = inst["tenor"].fillna("")
    inst["mlf_serial"] = inst.apply(
        lambda r: f"{r['serial_year']}#{r['serial_no']}" if r.get("serial_no") else "",
        axis=1,
    )
    inst = inst.rename(columns={
        "pub_date":  "date",
        "quantity":  "mlf_quantity",
    })
    keep = ["date", "mlf_marker", "mlf_quantity",
            "mlf_tenor_days", "mlf_tenor_label", "mlf_serial"]
    df = inst[keep].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    df = df.groupby("date", as_index=False).agg({
        "mlf_marker":        "max",
        "mlf_quantity":      "sum",
        "mlf_tenor_days":    "min",
        "mlf_tenor_label":   lambda s: "|".join(s.astype(str)),
        "mlf_serial":        lambda s: "|".join(s.astype(str)),
    })
    df = df.sort_values("date").reset_index(drop=True)

    if verbose:
        if len(df):
            print(f"    [PBOC-MLF] {len(df)} MLF announcements, "
                  f"{df['date'].min().date()} → {df['date'].max().date()}", flush=True)
            if df["mlf_quantity"].notna().any():
                print(f"    [PBOC-MLF] quantity range: "
                      f"{df['mlf_quantity'].min():g} → "
                      f"{df['mlf_quantity'].max():g} 亿元", flush=True)
        else:
            print(f"    [PBOC-MLF] no records in range", flush=True)
    return df


# ============================================================================
# (3) PBoC LPR monthly announcements → 1Y + 5Y+ tenor rates
# ============================================================================
def build_lpr_df(start_date=None, end_date=None, verbose=True):
    """Build a daily LPR frame from the combined LPR CSV.

    Reads temps/pboc_lpr_news/lpr_combined.csv (produced by
    download_pboc_lpr_news.py). Each row is one monthly LPR announcement
    with two tenor rates (1Y and 5Y+).

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


# ============================================================================
# (3b) PBoC Open Market Announcements (公开市场业务公告) → policy notices
# ============================================================================
def build_oma_df(start_date=None, end_date=None, verbose=True):
    """Build a PBoC OMA (Open Market Announcements) frame from oma_combined.csv.

    Reads temps/pboc_oma_news/oma_combined.csv (produced by
    download_pboc_oma.py). Each row is one high-level policy notice such as
    overnight-reverse-repo scheduling, outright-repo tool introduction,
    central-bank bill policy, MLF policy changes, or interest-rate adjustments.
    Multiple announcements can share a pub_date.

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
    df = df.sort_values(["date", "title"]).reset_index(drop=True)

    if start_date:
        df = df[df["date"] >= pd.Timestamp(start_date)]
    if end_date:
        df = df[df["date"] <= pd.Timestamp(end_date)]
    df = df.reset_index(drop=True)

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


# ============================================================================
# (4) SHIBOR daily fixings
# ============================================================================
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


def _read_shibor_xlsx(path):
    """Read one shibor_his_*.xlsx file. Returns DataFrame indexed by date."""
    try:
        df = pd.read_excel(path)
    except Exception:
        return None
    if df is None or len(df) == 0:
        return None
    if "日期" not in df.columns:
        return None
    df = df.copy()
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
    """Aggregate all shibor_his_*.xlsx chunks into a daily SHIBOR frame.

    Args:
        files: if provided, read only these files (incremental mode — caller
               already filtered to files overlapping with missing dates).
               If None, glob all files.
    """
    if files is None:
        pattern = os.path.join(SHIBOR_DIR, "shibor_his_*.xlsx")
        files = sorted(glob.glob(pattern))
    if verbose:
        print(f"    [SHIBOR] reading {len(files)} shibor_his_*.xlsx files", flush=True)

    all_chunks = []
    n_bad = 0
    for path in files:
        df = _read_shibor_xlsx(path)
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
    big = big.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    if start_date:
        big = big[big["date"] >= pd.Timestamp(start_date)]
    if end_date:
        big = big[big["date"] <= pd.Timestamp(end_date)]
    big = big.reset_index(drop=True)

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


# ============================================================================
# (5) Repo lifecycle tracking — requires FULL OMO history for cumulative
# ============================================================================
def build_repo_lifecycle_df(omo_df):
    """Build repo lifecycle data from OMO records.

    Calculates:
    - repo_start_quantity: amount injected on repo start date
    - repo_end_quantity: amount withdrawn on repo end date (negative)
    - repo_net_injection: daily net money injection (start - end)
    - repo_cumulative: cumulative outstanding repo balance

    NOTE: repo_cumulative depends on the FULL chronological OMO history.
    Callers must pass the full omo_df (not a missing-dates-only subset) so
    the cumulative sum is correct. The INSERT step then filters to missing
    dates.
    """
    if omo_df is None or len(omo_df) == 0:
        return pd.DataFrame()

    repo_legs = []
    for _, row in omo_df.iterrows():
        start_date = row['date']
        qty = row['omo_quantity']
        tenor_days = row['omo_tenor_days']
        tenor_label = row['omo_tenor_label']

        if pd.isna(qty) or pd.isna(tenor_days):
            continue

        try:
            tenor_days = int(tenor_days)
            qty = float(qty)
        except (ValueError, TypeError):
            continue

        end_date = start_date + timedelta(days=tenor_days)

        repo_legs.append({
            'date': start_date,
            'repo_start_quantity': qty,
            'repo_end_quantity': 0,
        })
        repo_legs.append({
            'date': end_date,
            'repo_start_quantity': 0,
            'repo_end_quantity': -qty,
        })

    if not repo_legs:
        return pd.DataFrame()

    legs_df = pd.DataFrame(repo_legs)
    daily = legs_df.groupby('date').agg({
        'repo_start_quantity': 'sum',
        'repo_end_quantity': 'sum',
    }).reset_index()

    daily['repo_net_injection'] = daily['repo_start_quantity'] + daily['repo_end_quantity']
    daily['repo_cumulative'] = daily['repo_net_injection'].cumsum()

    return daily


# ============================================================================
# (6) China bond (中债国债) daily yield curve
# ============================================================================
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


def _read_chinabond_xlsx(path):
    """Read one chinabond_bzqx_*.xlsx file. Returns DataFrame indexed by date."""
    try:
        df = pd.read_excel(path)
    except Exception:
        return None
    if df is None or len(df) == 0:
        return None
    if "日期" not in df.columns or "标准期限说明" not in df.columns or "收益率(%)" not in df.columns:
        return None
    df = df.copy()
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
    """Aggregate all chinabond_bzqx_treasury_bond_*.xlsx files into a daily
    China bond yield-curve frame.

    Args:
        files: if provided, read only these files (incremental mode — caller
               already filtered to files overlapping with missing dates).
               If None, glob all files.
    """
    if files is None:
        pattern = os.path.join(CHINABOND_DIR, "chinabond_bzqx_treasury_bond_*.xlsx")
        files = sorted(glob.glob(pattern))
    if verbose:
        print(f"    [CHINABOND] reading {len(files)} chinabond_bzqx_treasury_bond_*.xlsx files", flush=True)

    all_chunks = []
    n_bad = 0
    for path in files:
        df = _read_chinabond_xlsx(path)
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


# ============================================================================
# Main pipeline
# ============================================================================
def _filter_files_by_missing_years(files, missing_dates):
    """Filter yearly files to those overlapping with missing dates' years.

    SHIBOR files: shibor_his_YYYY0101_YYYY1231.xlsx
    China bond files: chinabond_bzqx_treasury_bond_YYYY.xlsx

    Extracts 4-digit year tokens from filenames and keeps files whose years
    overlap with the set of years in missing_dates.
    """
    if not missing_dates:
        return []
    missing_years = {d.year for d in missing_dates}
    out = []
    for f in files:
        basename = os.path.basename(f)
        years_in_name = set(int(y) for y in re.findall(r'\d{4}', basename))
        if years_in_name & missing_years:
            out.append(f)
    return out


async def main():
    ap = argparse.ArgumentParser()
    add_common_build_args(ap)
    args = ap.parse_args()

    t0 = time.time()
    print_build_header(
        "BUILD DEBT MARKET BASELINE  ·  missing-dates-only → DATABASE",
        **{
            "PBoC instr CSV": PBOC_INSTRUMENTS_CSV,
            "PBoC LPR CSV":   PBOC_LPR_CSV,
            "PBoC OMA CSV":   PBOC_OMA_CSV,
            "SHIBOR dir":     SHIBOR_DIR,
            "China bond dir": CHINABOND_DIR,
            "Date range":     f"{args.start_date or '(all)'} → {args.end_date or '(all)'}",
            "Today":          TODAY_STR,
        }
    )

    if not os.path.exists(PBOC_INSTRUMENTS_CSV):
        # Non-fatal: OMA reload can still proceed. The debt_* tables just
        # won't get new dates from the instruments CSV.
        print(f"\n  [WARN] {PBOC_INSTRUMENTS_CSV} not found — debt_* tables will "
              f"not be updated. Run `python download_pboc_repo_news.py --reparse` "
              f"to enable debt loading. Continuing with OMA-only reload.", flush=True)

    # ------------------------------------------------------------------
    # (1) Discover source files (fast — filenames only, no reading)
    # ------------------------------------------------------------------
    print("\n[1/5] Discovering source files …", flush=True)
    shibor_files_all = glob_source_files(SHIBOR_DIR, "shibor_his_*.xlsx")
    chinabond_files_all = glob_source_files(CHINABOND_DIR, "chinabond_bzqx_treasury_bond_*.xlsx")
    print(f"    → SHIBOR: {len(shibor_files_all)} yearly files", flush=True)
    print(f"    → China bond: {len(chinabond_files_all)} yearly files", flush=True)

    # ------------------------------------------------------------------
    # (2) Connect to DB and find missing dates
    # ------------------------------------------------------------------
    print("\n[2/5] Connecting to database and detecting missing dates …", flush=True)
    conn = await get_db_or_exit()

    try:
        if args.force:
            print("    [DB] Force mode: truncating existing tables", flush=True)
            for tbl in ("stats.pboc_oma",
                        "stats.debt_lpr", "stats.debt_treasury", "stats.debt_shibor",
                        "stats.debt_mlf", "stats.debt_outright_repo", "stats.debt_repo",
                        "stats.debt_omo", "stats.debt_identity"):
                await truncate_table_async(conn, tbl)

        # ------------------------------------------------------------------
        # (2b) Always reload PBoC OMA (small dataset, no FK to debt_identity)
        # ------------------------------------------------------------------
        print("\n[2b/5] Reloading PBoC OMA announcements (always truncate+insert) …", flush=True)
        oma_df = build_oma_df(args.start_date, args.end_date, verbose=True)
        # Always truncate so the table matches the latest CSV exactly. The
        # dataset is small (~15 rows) and announcements may occur on non-
        # trading days, so the missing-dates-only logic does not apply.
        await truncate_table_async(conn, "stats.pboc_oma")
        if oma_df is not None and len(oma_df) > 0:
            oma_rows = oma_df.copy()
            oma_rows["date"] = oma_rows["date"].dt.date
            oma_rows = oma_rows.to_dict("records")
            inserted = await bulk_upsert_async(
                conn, "stats.pboc_oma", oma_rows, ["date", "title"]
            )
            print(f"    [DB] Inserted {inserted:,} rows into stats.pboc_oma", flush=True)
        else:
            print(f"    [DB] No OMA rows to insert into stats.pboc_oma", flush=True)

        # ------------------------------------------------------------------
        # Discover available dates & find missing dates for debt_* tables
        # ------------------------------------------------------------------
        # Discover available dates from the instruments CSV + LPR CSV (fast)
        inst_df = load_pboc_instruments_df(verbose=True)
        all_available_dates = set()
        if inst_df is not None and len(inst_df) > 0:
            all_available_dates.update(inst_df["pub_date"].dt.date.tolist())

        # LPR announcements are monthly — their dates must also be present
        # in debt_identity for the FK to hold.
        lpr_dates_only_df = build_lpr_df(verbose=False)
        if lpr_dates_only_df is not None and len(lpr_dates_only_df) > 0:
            all_available_dates.update(lpr_dates_only_df["date"].dt.date.tolist())

        if args.force:
            existing_dates_set = set()
            missing_dates = all_available_dates
        else:
            existing_rows = await conn.fetch("SELECT DISTINCT date FROM stats.debt_identity")
            existing_dates_set = {r["date"] for r in existing_rows}
            missing_dates = all_available_dates - existing_dates_set
        print(f"    [DB] {len(missing_dates)} dates missing from stats.debt_identity", flush=True)

        if not missing_dates:
            print("    [INFO] Database is up to date — no new debt dates to insert "
                  "(OMA already reloaded above)", flush=True)
            print_wall_time(t0)
            return

        # ------------------------------------------------------------------
        # (3) Read OMO (full history for repo cumulative) + build outright/MLF/LPR
        # ------------------------------------------------------------------
        print("\n[3/5] Building PBoC OMO + outright + MLF + LPR (full history for repo cumulative) …", flush=True)
        # NOTE: OMO is read WITHOUT date filtering so the repo lifecycle
        # cumulative balance is computed over the full history. Only the
        # INSERT step filters to missing dates.
        omo_df = build_pboc_omo_df(verbose=True)
        outright_df = build_pboc_outright_repo_df(args.start_date, args.end_date, verbose=True)
        mlf_df = build_pboc_mlf_df(args.start_date, args.end_date, verbose=True)
        lpr_df = build_lpr_df(args.start_date, args.end_date, verbose=True)

        # ------------------------------------------------------------------
        # (4) Read SHIBOR + China bond (filtered to missing years) + repo lifecycle
        # ------------------------------------------------------------------
        print("\n[4/5] Building SHIBOR + repo lifecycle + China bond (missing years only) …", flush=True)

        # Filter yearly files to only those overlapping with missing dates
        missing_shibor_files = _filter_files_by_missing_years(shibor_files_all, missing_dates)
        missing_chinabond_files = _filter_files_by_missing_years(chinabond_files_all, missing_dates)
        print(f"    → SHIBOR: {len(missing_shibor_files)} files to read "
              f"(out of {len(shibor_files_all)} total)", flush=True)
        print(f"    → China bond: {len(missing_chinabond_files)} files to read "
              f"(out of {len(chinabond_files_all)} total)", flush=True)

        shibor_df = build_shibor_df(args.start_date, args.end_date, verbose=True,
                                     files=missing_shibor_files)
        repo_lifecycle_df = build_repo_lifecycle_df(omo_df)
        if len(repo_lifecycle_df):
            print(f"    [REPO-LIFECYCLE] {len(repo_lifecycle_df)} daily records, "
                  f"peak cumulative: {repo_lifecycle_df['repo_cumulative'].max():,.0f} 亿元", flush=True)
        chinabond_df = build_chinabond_df(args.start_date, args.end_date, verbose=True,
                                           files=missing_chinabond_files)

        # After reading SHIBOR/China bond, check for additional dates not in
        # the instruments CSV (e.g., trading days with SHIBOR data but no OMO)
        for df in [shibor_df, chinabond_df]:
            if df is not None and len(df) > 0:
                extra_dates = set(df["date"].dt.date.tolist()) - existing_dates_set
                if extra_dates:
                    missing_dates = missing_dates | extra_dates
                    print(f"    → Found {len(extra_dates)} additional missing dates "
                          f"from SHIBOR/China bond (not in instruments CSV)", flush=True)

        # ------------------------------------------------------------------
        # (5) Filter to missing dates and insert
        # ------------------------------------------------------------------
        print("\n[5/5] Inserting data to database (missing dates only) …", flush=True)

        # Insert new identities for missing dates only
        identity_rows = [{"date": d} for d in sorted(missing_dates)]
        inserted = await bulk_upsert_async(conn, "stats.debt_identity", identity_rows, ["date"])
        print(f"    [DB] Inserted {inserted:,} rows into stats.debt_identity", flush=True)

        # Helper: filter a frame to missing dates, convert to rows for insert
        def df_to_missing_rows(df, date_col="date"):
            if df is None or len(df) == 0:
                return []
            df = df.copy()
            df[date_col] = df[date_col].dt.date
            df = df[df[date_col].isin(missing_dates)]
            if len(df) == 0:
                return []
            return df.to_dict("records")

        # Insert each source table, filtered to missing dates
        table_source_pairs = [
            ("stats.debt_omo",            omo_df),
            ("stats.debt_repo",           repo_lifecycle_df),
            ("stats.debt_outright_repo",  outright_df),
            ("stats.debt_mlf",            mlf_df),
            ("stats.debt_lpr",            lpr_df),
            ("stats.debt_shibor",         shibor_df),
            ("stats.debt_treasury",       chinabond_df),
        ]
        for tbl, df in table_source_pairs:
            rows = df_to_missing_rows(df)
            if rows:
                inserted = await bulk_upsert_async(conn, tbl, rows, ["date"])
                print(f"    [DB] Inserted {inserted:,} rows into {tbl} "
                      f"(filtered to {len(missing_dates)} missing dates)", flush=True)
            else:
                print(f"    [DB] No new rows to insert into {tbl}", flush=True)

    finally:
        await conn.close()

    # Coverage summary (over full source range, not just missing)
    print(f"\n  Coverage by source (full range):", flush=True)
    if oma_df is not None and len(oma_df) > 0:
        print(f"    · PBoC OMA            : {len(oma_df):>5d} announcements", flush=True)
    if omo_df is not None and len(omo_df) > 0:
        n = int(omo_df["omo_rate"].notna().sum())
        print(f"    · PBoC OMO rate       : {n:>5d} days", flush=True)
    if outright_df is not None and len(outright_df) > 0:
        n = int((outright_df["outright_repo_marker"] == 1).sum())
        print(f"    · PBoC outright-repo  : {n:>5d} announcements", flush=True)
    if mlf_df is not None and len(mlf_df) > 0:
        n = int((mlf_df["mlf_marker"] == 1).sum())
        print(f"    · PBoC MLF            : {n:>5d} announcements", flush=True)
    if lpr_df is not None and len(lpr_df) > 0:
        n = int(lpr_df["lpr_1y"].notna().sum())
        print(f"    · PBoC LPR (1Y)       : {n:>5d} announcements", flush=True)
    if shibor_df is not None and len(shibor_df) > 0:
        n = int(shibor_df["shibor_o_n"].notna().sum())
        print(f"    · SHIBOR O/N          : {n:>5d} days", flush=True)
    if chinabond_df is not None and len(chinabond_df) > 0:
        n = int(chinabond_df["cb_1y"].notna().sum())
        print(f"    · China bond 1Y yield  : {n:>5d} days", flush=True)

    print_wall_time(t0)


if __name__ == "__main__":
    asyncio.run(main())
