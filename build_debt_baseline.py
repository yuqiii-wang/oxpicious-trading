"""
build_debt_baseline.py — Build combined debt-market baseline to DATABASE.

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

Inserts to database tables:
  • debt_identity           (date)
  • debt_omo                (OMO rates, quantities)
  • debt_repo               (repo lifecycle tracking)
  • debt_outright_repo      (outright repo markers)
  • debt_mlf                (MLF markers)
  • debt_shibor             (SHIBOR fixings)
  • debt_treasury           (treasury yield curve)

Only inserts new data not already present in the database.

Usage:
  python build_debt_baseline.py
  python build_debt_baseline.py --start-date 2024-01-01 --end-date 2026-07-14
  python build_debt_baseline.py --force

Prerequisite:
  Run `python download_pboc_repo_news.py --reparse` first to (re)generate
  temp_data/analysis_output/pboc_repo_news/instruments_combined.csv.
"""
import os
import re
import sys
import glob
import time
import argparse
from pathlib import Path
from datetime import datetime, timedelta

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_commons import (
    get_db_connection_async, get_existing_keys_async, bulk_upsert_async,
    truncate_table_async
)

# ---------------------------------------------------------------------------
# stdout encoding (Windows console fix)
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
PBOC_NEWS_DIR       = os.path.join(PROJECT_ROOT, "temps", "pboc_repo_news")
PBOC_INSTRUMENTS_CSV = os.path.join(
    PROJECT_ROOT, "temp_data", "analysis_output", "pboc_repo_news", "instruments_combined.csv"
)
SHIBOR_DIR          = os.path.join(PROJECT_ROOT, "temps", "shibor")
CHINABOND_DIR       = os.path.join(PROJECT_ROOT, "temps", "chinabond")
OUTPUT_DIR          = os.path.join(PROJECT_ROOT, "temp_data", "analysis_output", "debt_baseline")
os.makedirs(OUTPUT_DIR, exist_ok=True)

COMBINED_CSV    = os.path.join(OUTPUT_DIR, "debt_baseline.csv")
TODAY_STR       = datetime.now().strftime("%Y-%m-%d")


# ============================================================================
# Helpers
# ============================================================================
def parse_num(s):
    """Coerce a string/number to float; return NaN on failure."""
    if s is None:
        return np.nan
    if isinstance(s, (int, float)):
        v = float(s)
        return v if np.isfinite(v) else np.nan
    txt = str(s).strip()
    if not txt or txt in ("--", "-", "—", "null", "NULL", "None", "nan", "NaN"):
        return np.nan
    txt = txt.replace(",", "").replace("，", "").replace(" ", "").replace("\u3000", "")
    try:
        v = float(txt)
        return v if np.isfinite(v) else np.nan
    except Exception:
        return np.nan


# Cached instruments dataframe (loaded once per process)
_INSTRUMENTS_DF_CACHE: "pd.DataFrame | None" = None


def load_pboc_instruments_df(csv_path: str = PBOC_INSTRUMENTS_CSV,
                             start_date=None, end_date=None,
                             verbose: bool = False) -> "pd.DataFrame":
    """Load the combined PBoC instruments CSV (one row per parsed instrument).

    Reads ``temp_data/analysis_output/pboc_repo_news/instruments_combined.csv``
    which is produced by ``download_pboc_repo_news.py --build-csv``. The CSV
    supersedes the old workflow of globbing ``pboc_*_*.md`` files and parsing
    their YAML front-matter.

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
        # keep_default_na=False preserves "" for empty cells (rate, etc.)
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


def _fmt_date(d):
    """Format a date / datetime / string as 'YYYY-MM-DD'."""
    if isinstance(d, str):
        try:
            return datetime.strptime(d.strip(), "%Y-%m-%d").strftime("%Y-%m-%d")
        except Exception:
            return d.strip()
    if isinstance(d, (datetime, pd.Timestamp)):
        return d.strftime("%Y-%m-%d")
    return str(d)


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

    # Only omo_transaction category contributes to the OMO series
    inst = inst[inst["category"] == "omo_transaction"].copy()
    if len(inst) == 0:
        if verbose:
            print(f"    [PBOC-OMO] no omo_transaction records in range", flush=True)
        return pd.DataFrame()

    # Per-date aggregation
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

        # Informational pipe-joined fields from reverse_repo + MLF entries
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

    # Deduplicate by date — prefer records with 'reverse_repo' instrument
    # (PBoC bill issuances in Hong Kong have instruments: [] and should not
    # override actual reverse repo operations)
    df = df.sort_values(["date", "omo_has_reverse_repo"], ascending=[True, False])
    df = df.drop_duplicates(subset=["date"], keep="first").reset_index(drop=True)

    # Filter out bill-only dates — these are dates where ONLY a PBoC bill
    # issuance was announced (no reverse repo), which shouldn't contribute
    # to the OMO rate series
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
    """Build a daily outright-repo marker frame from the combined instruments CSV.

    Returns DataFrame columns:
        date, outright_repo_marker (=1),
        outright_repo_quantity, outright_repo_tenor_days,
        outright_repo_tenor_label, outright_repo_serial
    """
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

    # If multiple announcements on same date (rare), aggregate
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
    """Build a daily MLF marker frame from the combined instruments CSV.

    Returns DataFrame columns:
        date, mlf_marker (=1),
        mlf_quantity, mlf_tenor_days,
        mlf_tenor_label, mlf_serial
    """
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
# (3) SHIBOR daily fixings
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
    """Read one shibor_his_*.xlsx file. Returns DataFrame indexed by date.

    Skips trailing '数据来源：' / 'www.chinamoney.com.cn' rows. Values are
    already percentages (e.g. 1.4604 means 1.4604%) — DO NOT scale.
    """
    try:
        df = pd.read_excel(path)
    except Exception:
        return None
    if df is None or len(df) == 0:
        return None
    # First column is the date column (named '日期')
    if "日期" not in df.columns:
        return None
    df = df.copy()
    df["日期"] = df["日期"].astype(str).str.strip()
    # Drop the data-source footer rows (any row whose date col is not a YYYY-MM-DD)
    df = df[df["日期"].str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)]
    if len(df) == 0:
        return None
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    df = df.dropna(subset=["日期"])
    # Coerce numeric tenor columns
    for src_col, _ in SHIBOR_TENOR_MAP:
        if src_col in df.columns:
            df[src_col] = pd.to_numeric(df[src_col], errors="coerce")
    return df


def build_shibor_df(start_date=None, end_date=None, verbose=True):
    """Aggregate all shibor_his_*.xlsx chunks into a daily SHIBOR frame.

    Multiple chunked files overlap (e.g. _20260101_20260714.csv and
    _20260714_20260714.csv). We take the LAST observation per date (newer
    chunk files win because they were downloaded later with fresher data).
    """
    pattern = os.path.join(SHIBOR_DIR, "shibor_his_*.xlsx")
    files = sorted(glob.glob(pattern))
    if verbose:
        print(f"    [SHIBOR] scanning {len(files)} shibor_his_*.xlsx files", flush=True)

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
    # Deduplicate by date, keeping the LAST occurrence (later chunks win)
    big = big.sort_values("日期")
    # Use groupby to keep last non-null value per date per tenor column
    rename = {src: tgt for src, tgt in SHIBOR_TENOR_MAP if src in big.columns}
    keep_cols = ["日期"] + [src for src, _ in SHIBOR_TENOR_MAP if src in big.columns]
    big = big[keep_cols].rename(columns=rename)
    # Aggregate by date: take last non-null for each tenor
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
# (4) Repo lifecycle tracking
# ============================================================================
def build_repo_lifecycle_df(omo_df):
    """Build repo lifecycle data from OMO records.
    
    Calculates:
    - repo_start_quantity: amount injected on repo start date
    - repo_end_quantity: amount withdrawn on repo end date (negative)
    - repo_net_injection: daily net money injection (start - end)
    - repo_cumulative: cumulative outstanding repo balance
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
# (5) China bond (中债国债) daily yield curve
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
    """Read one chinabond_bzqx_*.xlsx file. Returns DataFrame indexed by date.

    File format (long):
        日期 | 标准期限说明 | 标准期限(年) | 收益率(%)
    Pivots to wide format with tenor label as columns.
    Values are already percentages (e.g. 1.4604 means 1.4604%) — DO NOT scale.
    """
    try:
        df = pd.read_excel(path)
    except Exception:
        return None
    if df is None or len(df) == 0:
        return None
    # Required columns
    if "日期" not in df.columns or "标准期限说明" not in df.columns or "收益率(%)" not in df.columns:
        return None
    df = df.copy()
    # Date is stored as 'YYYY/MM/DD' string — normalize
    df["日期"] = df["日期"].astype(str).str.strip().str.replace("/", "-", regex=False)
    df = df[df["日期"].str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)]
    if len(df) == 0:
        return None
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    df = df.dropna(subset=["日期"])
    df["标准期限说明"] = df["标准期限说明"].astype(str).str.strip().str.lower()
    df["收益率(%)"] = pd.to_numeric(df["收益率(%)"], errors="coerce")
    # Pivot to wide
    wide = df.pivot_table(
        index="日期", columns="标准期限说明", values="收益率(%)", aggfunc="last",
    ).reset_index()
    return wide


def build_chinabond_df(start_date=None, end_date=None, verbose=True):
    """Aggregate all chinabond_bzqx_treasury_bond_*.xlsx files into a daily
    China bond yield-curve frame.
    """
    pattern = os.path.join(CHINABOND_DIR, "chinabond_bzqx_treasury_bond_*.xlsx")
    files = sorted(glob.glob(pattern))
    if verbose:
        print(f"    [CHINABOND] scanning {len(files)} chinabond_bzqx_treasury_bond_*.xlsx files", flush=True)

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
    # Standardize column names
    rename = {src: tgt for src, tgt in CHINABOND_TENOR_MAP if src in big.columns}
    # Drop any non-tenor columns except 日期
    keep_cols = ["日期"] + [src for src, _ in CHINABOND_TENOR_MAP if src in big.columns]
    big = big[keep_cols].rename(columns=rename)
    big = big.rename(columns={"日期": "date"})
    # Aggregate by date: take last non-null per tenor
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
async def main():
    import asyncio
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-date", default=None, help="YYYY-MM-DD inclusive")
    ap.add_argument("--end-date",   default=None, help="YYYY-MM-DD inclusive")
    ap.add_argument("--force",      action="store_true", help="Rebuild all data (truncate tables first)")
    args = ap.parse_args()

    t0 = time.time()
    print("=" * 78, flush=True)
    print("  BUILD DEBT MARKET BASELINE TO DATABASE", flush=True)
    print("=" * 78, flush=True)
    print(f"  PBoC instr CSV: {PBOC_INSTRUMENTS_CSV}", flush=True)
    print(f"  SHIBOR dir    : {SHIBOR_DIR}", flush=True)
    print(f"  China bond dir: {CHINABOND_DIR}", flush=True)
    print(f"  Date range    : {args.start_date or '(all)'} → {args.end_date or '(all)'}", flush=True)
    print(f"  Today         : {TODAY_STR}", flush=True)

    if not os.path.exists(PBOC_INSTRUMENTS_CSV):
        print(f"\n  [ERROR] {PBOC_INSTRUMENTS_CSV} not found.", flush=True)
        print(f"          Run `python download_pboc_repo_news.py --reparse` first.", flush=True)
        sys.exit(1)

    # ------------------------------------------------------------------
    # (1) PBoC OMO daily records
    # ------------------------------------------------------------------
    print("\n[1/5] Building PBoC OMO daily frame …", flush=True)
    omo_df = build_pboc_omo_df(args.start_date, args.end_date, verbose=True)

    # ------------------------------------------------------------------
    # (2) PBoC outright-repo markers
    # ------------------------------------------------------------------
    print("\n[2/5] Building PBoC outright-repo marker frame …", flush=True)
    outright_df = build_pboc_outright_repo_df(args.start_date, args.end_date, verbose=True)

    # ------------------------------------------------------------------
    # (2b) PBoC MLF markers
    # ------------------------------------------------------------------
    print("\n[3/5] Building PBoC MLF marker frame …", flush=True)
    mlf_df = build_pboc_mlf_df(args.start_date, args.end_date, verbose=True)

    # ------------------------------------------------------------------
    # (4) SHIBOR daily fixings
    # ------------------------------------------------------------------
    print("\n[4/5] Building SHIBOR daily frame …", flush=True)
    shibor_df = build_shibor_df(args.start_date, args.end_date, verbose=True)

    # ------------------------------------------------------------------
    # (4) Repo lifecycle tracking
    # ------------------------------------------------------------------
    print("\n[4/5] Building repo lifecycle frame …", flush=True)
    repo_lifecycle_df = build_repo_lifecycle_df(omo_df)
    if len(repo_lifecycle_df):
        print(f"    [REPO-LIFECYCLE] {len(repo_lifecycle_df)} daily records, "
              f"peak cumulative: {repo_lifecycle_df['repo_cumulative'].max():,.0f} 亿元", flush=True)

    # ------------------------------------------------------------------
    # (5) China bond daily yield curve
    # ------------------------------------------------------------------
    print("\n[5/5] Building China bond daily yield-curve frame …", flush=True)
    chinabond_df = build_chinabond_df(args.start_date, args.end_date, verbose=True)

    # ------------------------------------------------------------------
    # Insert to database
    # ------------------------------------------------------------------
    print("\n[6/6] Inserting data to database …", flush=True)
    
    # Connect to database (async)
    print("\n[0/6] Connecting to database …", flush=True)
    try:
        conn = await get_db_connection_async()
        print("    [DB] Connected successfully", flush=True)
    except Exception as e:
        print(f"    [FATAL] Database connection failed: {e}", flush=True)
        sys.exit(1)
    
    try:
        if args.force:
            print("    [DB] Force mode: truncating existing tables", flush=True)
            await truncate_table_async(conn, "stats.debt_treasury")
            await truncate_table_async(conn, "stats.debt_shibor")
            await truncate_table_async(conn, "stats.debt_mlf")
            await truncate_table_async(conn, "stats.debt_outright_repo")
            await truncate_table_async(conn, "stats.debt_repo")
            await truncate_table_async(conn, "stats.debt_omo")
            await truncate_table_async(conn, "stats.debt_identity")
        
        # Get existing dates
        existing_dates = await get_existing_keys_async(conn, "stats.debt_identity", ["date"])
        print(f"    [DB] {len(existing_dates):,} existing dates in stats.debt_identity", flush=True)
        
        # Collect all unique dates from all sources
        # IMPORTANT: use datetime.date objects, not strings — asyncpg's DATE
        # codec requires datetime.date instances and raises DataError on str.
        all_dates = set()
        for df in [omo_df, outright_df, mlf_df, shibor_df, repo_lifecycle_df, chinabond_df]:
            if df is not None and len(df) > 0:
                all_dates.update(df["date"].dt.date.tolist())
        
        # Insert new identities
        identity_rows = []
        for dt_obj in all_dates:
            if (dt_obj,) not in existing_dates:
                identity_rows.append({"date": dt_obj})
        
        if identity_rows:
            inserted = await bulk_upsert_async(conn, "stats.debt_identity", identity_rows, ["date"])
            print(f"    [DB] Inserted {inserted:,} rows into stats.debt_identity", flush=True)
        
        # Helper function to convert dataframe to list of dicts.
        # Returns datetime.date (NOT str) for the date column so asyncpg can
        # encode it for a DATE column.
        def df_to_rows(df, table_name, date_col="date"):
            if df is None or len(df) == 0:
                return []
            df = df.copy()
            df[date_col] = df[date_col].dt.date
            return df.to_dict("records")
        
        # Insert to each table
        if omo_df is not None and len(omo_df) > 0:
            rows = df_to_rows(omo_df, "stats.debt_omo")
            if rows:
                inserted = await bulk_upsert_async(conn, "stats.debt_omo", rows, ["date"])
                print(f"    [DB] Inserted {inserted:,} rows into stats.debt_omo", flush=True)
        
        if repo_lifecycle_df is not None and len(repo_lifecycle_df) > 0:
            rows = df_to_rows(repo_lifecycle_df, "stats.debt_repo")
            if rows:
                inserted = await bulk_upsert_async(conn, "stats.debt_repo", rows, ["date"])
                print(f"    [DB] Inserted {inserted:,} rows into stats.debt_repo", flush=True)
        
        if outright_df is not None and len(outright_df) > 0:
            rows = df_to_rows(outright_df, "stats.debt_outright_repo")
            if rows:
                inserted = await bulk_upsert_async(conn, "stats.debt_outright_repo", rows, ["date"])
                print(f"    [DB] Inserted {inserted:,} rows into stats.debt_outright_repo", flush=True)
        
        if mlf_df is not None and len(mlf_df) > 0:
            rows = df_to_rows(mlf_df, "stats.debt_mlf")
            if rows:
                inserted = await bulk_upsert_async(conn, "stats.debt_mlf", rows, ["date"])
                print(f"    [DB] Inserted {inserted:,} rows into stats.debt_mlf", flush=True)
        
        if shibor_df is not None and len(shibor_df) > 0:
            rows = df_to_rows(shibor_df, "stats.debt_shibor")
            if rows:
                inserted = await bulk_upsert_async(conn, "stats.debt_shibor", rows, ["date"])
                print(f"    [DB] Inserted {inserted:,} rows into stats.debt_shibor", flush=True)
        
        if chinabond_df is not None and len(chinabond_df) > 0:
            rows = df_to_rows(chinabond_df, "stats.debt_treasury")
            if rows:
                inserted = await bulk_upsert_async(conn, "stats.debt_treasury", rows, ["date"])
                print(f"    [DB] Inserted {inserted:,} rows into stats.debt_treasury", flush=True)
    finally:
        await conn.close()
    
    # Coverage summary
    print(f"\n  Coverage by source:", flush=True)
    if omo_df is not None and len(omo_df) > 0:
        n = int(omo_df["omo_rate"].notna().sum())
        print(f"    · PBoC OMO rate       : {n:>5d} days", flush=True)
    if outright_df is not None and len(outright_df) > 0:
        n = int((outright_df["outright_repo_marker"] == 1).sum())
        print(f"    · PBoC outright-repo  : {n:>5d} announcements", flush=True)
    if mlf_df is not None and len(mlf_df) > 0:
        n = int((mlf_df["mlf_marker"] == 1).sum())
        print(f"    · PBoC MLF            : {n:>5d} announcements", flush=True)
    if shibor_df is not None and len(shibor_df) > 0:
        n = int(shibor_df["shibor_o_n"].notna().sum())
        print(f"    · SHIBOR O/N          : {n:>5d} days", flush=True)
    if chinabond_df is not None and len(chinabond_df) > 0:
        n = int(chinabond_df["cb_1y"].notna().sum())
        print(f"    · China bond 1Y yield  : {n:>5d} days", flush=True)

    print(f"\n  Wall time: {int(time.time()-t0)}s", flush=True)
    print("=" * 78, flush=True)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
