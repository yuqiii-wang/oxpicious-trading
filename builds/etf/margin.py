"""ETF margin source CSV reader.

Reads SZSE + SSE margin detail CSVs and returns a long DataFrame with
per-(date, code) margin balances (rz_*, rq_*, total_balance).

Source CSVs carry the canonical code schema written by downloads
(证券代码 = "NNNNNN.XX" + exchange/board/sec_type columns), so per-file
work is minimal (read + sec_type filter + date tag); all row-level
transforms run once on the concatenated frame. SZSE detail CSVs publish
all six margin columns; SSE detail CSVs lack 融券余额(元) and
融资融券余额(元) — the missing columns surface as NaN after concat and
are zero-filled in the vectorized pass.
"""
import glob
import os
from pathlib import Path

import pandas as pd

from downloads._common import read_build_csv
from _common.build_commons import ymd_from_filename
from _common.df_utils import safe_columns
from builds.etf.paths import (
    SZSE_MARGIN_DIR, SSE_MARGIN_DIR,
)

# source column → output column
_MARGIN_RENAME = {
    "证券代码": "code",
    "融资买入额(元)": "rz_buy",
    "融资余额(元)": "rz_balance",
    "融券卖出量(股/份)": "rq_sell_qty",
    "融券余量(股/份)": "rq_balance_qty",
    "融券余额(元)": "rq_balance_amt",
    "融资融券余额(元)": "total_balance",
}
_MARGIN_COLS = ["rz_buy", "rz_balance", "rq_sell_qty",
                "rq_balance_qty", "rq_balance_amt", "total_balance"]

# One-pass dtype contract: final dtypes assigned AT PARSE TIME (downloads
# conversion writes plain normalized floats; a parse error is a downloads
# bug and stops the run). SSE detail CSVs lack 融券余额(元)/融资融券余额(元)
# — read_csv ignores dtype keys absent from a file; those columns are
# materialized as 0.0 after concat.
_MARGIN_DTYPES = {
    "证券代码": str, "证券简称": str, "sec_type": str, "exchange": str,
    **{c: "float64" for c in _MARGIN_RENAME if c != "证券代码"},
}


def _scan_margin_dir(scan_dir, file_prefix, label, verbose=True, files=None, code=None):
    """Read canonical per-date margin CSVs; return (frames, n_ok, n_empty).

    When *code* is set (canonical "NNNNNN.SZ"), each CSV is byte-prefiltered
    to that code's lines BEFORE parsing (see read_csv_preferred)."""
    if files is None:
        files = sorted(glob.glob(os.path.join(scan_dir, f"{file_prefix}*.csv")))
    else:
        files = [f for f in files if os.path.basename(f).startswith(file_prefix)]
    if verbose:
        print(f"    [MARGIN-{label}] reading {len(files)} {file_prefix}*.csv files", flush=True)

    frames: list[pd.DataFrame] = []
    n_empty = 0
    n_ok = 0
    for path in files:
        ymd = ymd_from_filename(path, file_prefix)
        if not ymd:
            continue
        # CSV ONLY — canonical CSVs are guaranteed by downloads. A PARSE
        # EXCEPTION here is a downloads-conversion bug — it propagates
        # and stops the run instead of being miscounted as an "empty"
        # file. Legit empties (missing/placeholder exports) come back
        # as None/empty frames WITHOUT raising.
        df = read_build_csv(path, dtype=_MARGIN_DTYPES, code=code)
        if df is None or len(df) == 0:
            n_empty += 1
            continue
        # placeholder/holiday exports were never canonicalized (no sec_type
        # column); header-only holiday files were already caught by len==0
        if "sec_type" not in safe_columns(df):
            n_empty += 1
            continue
        # canonical CSV: sec_type == "etf" by plain column equality;
        # 证券代码 already carries the .SS/.SZ suffix. When *code* is set,
        # apply the exact equality filter too — the byte prefilter is a
        # deliberate superset (substring match).
        df = df[df["sec_type"] == "etf"]
        if code is not None:
            df = df[df["证券代码"] == code]
        if len(df) == 0:
            continue
        df = df.rename(columns=_MARGIN_RENAME)
        df["date"] = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"  # scalar broadcast
        src_cols = safe_columns(df)
        frames.append(df[["date", "code"] + [c for c in _MARGIN_COLS if c in src_cols]])
        n_ok += 1
    if verbose:
        print(f"    [MARGIN-{label}] {n_ok} files with data, {n_empty} empty, "
              f"{sum(len(f) for f in frames):,} rows", flush=True)
    return frames, n_ok, n_empty


def build_margin_df(verbose=True, margin_files=None, code=None):
    """Read margin source CSVs and return a long DataFrame.

    Args:
        margin_files: if provided, a dict with keys "szse", "sse" mapping to
                      lists of file paths. Only these files are read (incremental
                      mode). If None, glob all files (--force mode).
        code: optional single-ETF filter enabling byte-prefiltered reads.
    """
    scans: list[tuple[str, str, str, str]] = [
        (SZSE_MARGIN_DIR, "szse_margin_detail_", "szse", "szse"),
        (SSE_MARGIN_DIR, "sse_margin_detail_", "sse", "sse"),
    ]

    frames: list[pd.DataFrame] = []
    n_ok_total = 0
    n_empty_total = 0
    for scan_dir, prefix, label, file_key in scans:
        if not os.path.isdir(scan_dir):
            continue
        files = margin_files.get(file_key) if margin_files is not None else None
        if margin_files is not None and not files:
            continue  # incremental: nothing to read for this source
        fs, n_ok, n_empty = _scan_margin_dir(scan_dir, prefix, label,
                                             verbose=verbose, files=files,
                                             code=code)
        frames.extend(fs)
        n_ok_total += n_ok
        n_empty_total += n_empty

    if not frames:
        if verbose:
            print(f"    [MARGIN] total: {n_ok_total} files with data, "
                  f"{n_empty_total} empty, 0 rows", flush=True)
        return pd.DataFrame()

    # ---- ONE vectorized pass over the concatenated frame ----
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"])
    out = out.dropna(subset=["date"])

    # Sources genuinely differ in column coverage (SSE lacks 融券余额(元) and
    # 融资融券余额(元); an all-empty source leaves its columns absent from the
    # concat entirely) → materialize the missing columns, then NaN → 0.
    # Anything NOT in _MARGIN_COLS at this point WOULD be a downloads bug and
    # is caught downstream by the DB schema / writer validation.
    for c in _MARGIN_COLS:
        if c not in safe_columns(out):
            out[c] = 0.0
        else:
            out[c] = out[c].fillna(0.0)

    n_before = len(out)
    out = out.groupby(["date", "code"], as_index=False)[_MARGIN_COLS].sum()
    n_after = len(out)
    n_merged = n_before - n_after

    if verbose:
        print(f"    [MARGIN] total: {n_ok_total} files with data, {n_empty_total} empty, "
              f"{n_before} raw rows → {n_after} merged rows ({n_merged} duplicates handled)", flush=True)

    out = out.sort_values(["code", "date"]).reset_index(drop=True)
    return out
