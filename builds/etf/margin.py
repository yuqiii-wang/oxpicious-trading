"""ETF margin source CSV reader.

Reads SZSE + SSE margin detail CSVs and returns a long DataFrame with
per-(date, code) margin balances (rz_*, rq_*, total_balance).
"""
import glob
import os
import re
from pathlib import Path

import pandas as pd

from downloads._common.core import read_csv_preferred
from _common.build_commons import parse_num, ymd_from_filename
from builds.etf.paths import (
    SZSE_MARGIN_DIR, SSE_MARGIN_DIR,
    SZSE_ETF_PREFIXES, SSE_ETF_PREFIXES,
)


def _scan_margin_dir(scan_dir, file_prefix, market, verbose=True, files=None):
    if files is None:
        pattern = os.path.join(scan_dir, f"{file_prefix}*.csv")
        files = sorted(glob.glob(pattern))
    else:
        files = [f for f in files if os.path.basename(f).startswith(file_prefix)]
    if verbose:
        print(f"    [MARGIN-{market}] reading {len(files)} {file_prefix}*.csv files", flush=True)

    rows = []
    n_empty = 0
    n_ok = 0
    for path in files:
        ymd = ymd_from_filename(path, file_prefix)
        if not ymd:
            continue
        xlsx_path = str(Path(path).with_suffix(".xlsx"))
        try:
            df = read_csv_preferred(xlsx_path, dtype={"证券代码": str, "证券简称": str})
        except Exception:
            continue
        if df is None or len(df) == 0:
            n_empty += 1
            continue
        first_cell = str(df.iloc[0, 0]) if len(df) else ""
        if "没有找到" in first_cell or "无数据" in first_cell:
            n_empty += 1
            continue
        if "证券代码" not in df.columns:
            continue
        df["_code"] = df["证券代码"].astype(str).str.strip()
        df["_code"] = df["_code"].apply(
            lambda s: str(int(float(s))).zfill(6) if re.fullmatch(r"\d+(\.0+)?", s or "") else s
        )
        df["_code_base"] = df["_code"].str.split(".").str[0]

        if market == "深圳":
            etf_mask = df["_code_base"].str.startswith(SZSE_ETF_PREFIXES) & (df["_code_base"].str.len() == 6)
            default_suffix = ".SZ"
        else:
            etf_mask = df["_code_base"].str.startswith(SSE_ETF_PREFIXES) & (df["_code_base"].str.len() == 6)
            default_suffix = ".SS"
        df = df[etf_mask].copy()
        if len(df) == 0:
            continue
        date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        for _, r in df.iterrows():
            code = r["_code"]
            code_with_suffix = code if "." in code else f"{code}{default_suffix}"
            rows.append({
                "date":           date_str,
                "code":           code_with_suffix,
                "rz_buy":         parse_num(r.get("融资买入额(元)")),
                "rz_balance":     parse_num(r.get("融资余额(元)")),
                "rq_sell_qty":    parse_num(r.get("融券卖出量(股/份)")),
                "rq_balance_qty": parse_num(r.get("融券余量(股/份)")),
                "rq_balance_amt": parse_num(r.get("融券余额(元)")),
                "total_balance":  parse_num(r.get("融资融券余额(元)")),
            })
        n_ok += 1
    if verbose:
        print(f"    [MARGIN-{market}] {n_ok} files with data, {n_empty} empty, {len(rows)} rows", flush=True)
    return rows, n_ok, n_empty


def build_margin_df(verbose=True, margin_files=None):
    """Read margin source CSVs and return a long DataFrame.

    Args:
        margin_files: if provided, a dict with keys "szse", "sse" mapping to
                      lists of file paths. Only these files are read (incremental
                      mode). If None, glob all files (--force mode).
    """
    all_rows = []
    n_ok_total = 0
    n_empty_total = 0

    if margin_files is not None:
        if margin_files.get("szse"):
            rows_szse, ok_szse, empty_szse = _scan_margin_dir(
                SZSE_MARGIN_DIR, "szse_margin_detail_", "深圳", verbose,
                files=margin_files["szse"])
            all_rows.extend(rows_szse)
            n_ok_total += ok_szse
            n_empty_total += empty_szse
        if margin_files.get("sse"):
            rows_sse, ok_sse, empty_sse = _scan_margin_dir(
                SSE_MARGIN_DIR, "sse_margin_detail_", "上海", verbose,
                files=margin_files["sse"])
            all_rows.extend(rows_sse)
            n_ok_total += ok_sse
            n_empty_total += empty_sse
    else:
        if os.path.isdir(SZSE_MARGIN_DIR):
            rows_szse, ok_szse, empty_szse = _scan_margin_dir(
                SZSE_MARGIN_DIR, "szse_margin_detail_", "深圳", verbose)
            all_rows.extend(rows_szse)
            n_ok_total += ok_szse
            n_empty_total += empty_szse
        if os.path.isdir(SSE_MARGIN_DIR):
            rows_sse, ok_sse, empty_sse = _scan_margin_dir(
                SSE_MARGIN_DIR, "sse_margin_detail_", "上海", verbose)
            all_rows.extend(rows_sse)
            n_ok_total += ok_sse
            n_empty_total += empty_sse

    if not all_rows:
        if verbose:
            print(f"    [MARGIN] total: {n_ok_total} files with data, {n_empty_total} empty, {len(all_rows)} rows", flush=True)
        return pd.DataFrame()

    out = pd.DataFrame(all_rows)
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date"])

    margin_cols = ["rz_buy", "rz_balance", "rq_sell_qty", "rq_balance_qty", "rq_balance_amt", "total_balance"]
    for c in margin_cols:
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0)

    n_before = len(out)
    out = out.groupby(["date", "code"], as_index=False)[margin_cols].sum()
    n_after = len(out)
    n_merged = n_before - n_after

    if verbose:
        print(f"    [MARGIN] total: {n_ok_total} files with data, {n_empty_total} empty, "
              f"{n_before} raw rows → {n_after} merged rows ({n_merged} duplicates handled)", flush=True)

    out = out.sort_values(["code", "date"]).reset_index(drop=True)
    return out
