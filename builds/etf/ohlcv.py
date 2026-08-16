"""ETF OHLCV source CSV reader.

Reads SZSE archive/trend + SSE trend CSVs and returns a long DataFrame
with raw OHLCV + trading volume/amount (converted to shares/yuan).
"""
import glob
import os
import re
from pathlib import Path

import pandas as pd

from downloads._common.core import read_csv_preferred
from _common.build_commons import parse_num, ymd_from_filename
from builds.etf.paths import (
    SZSE_ARCHIVE_DIR, SZSE_TREND_DIR, SSE_TREND_DIR,
    SZSE_ETF_PREFIXES, SSE_ETF_PREFIXES,
)
from builds.etf.codes import is_money_market_etf


def _scan_ohlcv_dir(scan_dir, file_prefix, market, files=None):
    if files is None:
        pattern = os.path.join(scan_dir, f"{file_prefix}*.csv")
        files = sorted(glob.glob(pattern))
    else:
        files = [f for f in files if os.path.basename(f).startswith(file_prefix)]
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
        else:
            etf_mask = df["_code_base"].str.startswith(SSE_ETF_PREFIXES) & (df["_code_base"].str.len() == 6)
        df = df[etf_mask].copy()
        if len(df) == 0:
            continue
        df["_mm"] = df["证券简称"].astype(str).apply(is_money_market_etf)
        df = df[~df["_mm"]].copy()
        if len(df) == 0:
            continue
        date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        volume_col = "成交量（万份）" if market == "深圳" else "成交量(万股)"
        amount_col = "成交金额(万元)"
        for _, r in df.iterrows():
            code = r["_code"]
            exchange = "SZ" if market == "深圳" else "SS"
            code_with_suffix = code if "." in code else f"{code}.{exchange}"
            rows.append({
                "date":         date_str,
                "code":         code_with_suffix,
                "name":         str(r.get("证券简称", "")).strip(),
                "prev_close":   parse_num(r.get("前收")),
                "open":         parse_num(r.get("开盘")),
                "high":         parse_num(r.get("最高")),
                "low":          parse_num(r.get("最低")),
                "close":        parse_num(r.get("今收")),
                "pct_change":   parse_num(r.get("涨跌幅（%）")),
                "volume_wan":   parse_num(r.get(volume_col)),
                "amount_wan":   parse_num(r.get(amount_col)),
            })
        n_ok += 1
    return rows, n_ok, n_empty, len(files)


def build_ohlcv_df(verbose=True, ohlcv_files=None):
    """Read OHLCV source CSVs and return a long DataFrame.

    Args:
        ohlcv_files: if provided, a dict with keys "szse_archive", "szse_trend",
                     "sse_trend" mapping to lists of file paths. Only these
                     files are read (incremental mode — caller already filtered
                     to missing dates via DB query). If None, glob all files
                     in the source directories (--force mode).

    Split adjustment and MA computation require the full per-code chronological
    history. In incremental mode, the caller queries the DB for existing data
    and concatenates it with the new rows before applying split/MA.
    """
    rows_a, ok_a, empty_a, tot_a = [], 0, 0, 0
    rows_t, ok_t, empty_t, tot_t = [], 0, 0, 0
    rows_sse, ok_sse, empty_sse, tot_sse = [], 0, 0, 0

    if ohlcv_files is not None:
        fa = ohlcv_files.get("szse_archive", [])
        if fa:
            rows_a, ok_a, empty_a, tot_a = _scan_ohlcv_dir(
                SZSE_ARCHIVE_DIR, "szse_etf_", "深圳", files=fa)
            if verbose:
                print(f"    [OHLCV-szse-archive] read {tot_a} files  {ok_a} ok  {empty_a} empty  {len(rows_a)} rows", flush=True)
        ft = ohlcv_files.get("szse_trend", [])
        if ft:
            rows_t, ok_t, empty_t, tot_t = _scan_ohlcv_dir(
                SZSE_TREND_DIR, "szse_trend_etf_", "深圳", files=ft)
            if verbose:
                print(f"    [OHLCV-szse-trend] read {tot_t} files  {ok_t} ok  {empty_t} empty  {len(rows_t)} rows", flush=True)
        fs = ohlcv_files.get("sse_trend", [])
        if fs:
            rows_sse, ok_sse, empty_sse, tot_sse = _scan_ohlcv_dir(
                SSE_TREND_DIR, "sse_trend_stock_", "上海", files=fs)
            if verbose:
                if len(rows_sse) == 0:
                    print(f"      → [INFO] SSE trend files contain stocks only (600/601/603/605/688 prefixes), not ETFs.", flush=True)
                print(f"      → read {tot_sse} files  {ok_sse} ok  {empty_sse} empty  {len(rows_sse)} rows", flush=True)
    else:
        if os.path.isdir(SZSE_ARCHIVE_DIR):
            if verbose:
                print(f"    [OHLCV-szse-archive] scanning {SZSE_ARCHIVE_DIR}", flush=True)
            rows_a, ok_a, empty_a, tot_a = _scan_ohlcv_dir(SZSE_ARCHIVE_DIR, "szse_etf_", "深圳")
            if verbose:
                print(f"      → scanned {tot_a} files  {ok_a} ok  {empty_a} empty  {len(rows_a)} rows", flush=True)
        if os.path.isdir(SZSE_TREND_DIR):
            if verbose:
                print(f"    [OHLCV-szse-trend] scanning {SZSE_TREND_DIR}", flush=True)
            rows_t, ok_t, empty_t, tot_t = _scan_ohlcv_dir(SZSE_TREND_DIR, "szse_trend_etf_", "深圳")
            if verbose:
                print(f"      → scanned {tot_t} files  {ok_t} ok  {empty_t} empty  {len(rows_t)} rows", flush=True)
        if os.path.isdir(SSE_TREND_DIR):
            if verbose:
                print(f"    [OHLCV-sse-trend] scanning {SSE_TREND_DIR}", flush=True)
            rows_sse, ok_sse, empty_sse, tot_sse = _scan_ohlcv_dir(SSE_TREND_DIR, "sse_trend_stock_", "上海")
            if verbose:
                if len(rows_sse) == 0:
                    print(f"      → [INFO] SSE trend files contain stocks only (600/601/603/605/688 prefixes), not ETFs.", flush=True)
                print(f"      → scanned {tot_sse} files  {ok_sse} ok  {empty_sse} empty  {len(rows_sse)} rows", flush=True)

    all_rows = rows_a + rows_t + rows_sse
    if verbose:
        print(f"    [OHLCV] total raw rows: {len(all_rows)}", flush=True)
    if not all_rows:
        return pd.DataFrame()
    out = pd.DataFrame(all_rows)
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date"])
    out = out.sort_values(["date", "code", "volume_wan"], kind="mergesort")
    out = out.drop_duplicates(subset=["date", "code"], keep="last")
    out = out.sort_values(["code", "date"]).reset_index(drop=True)

    # Normalize 1000x amount error in SZSE/SSE source data.
    _vol = pd.to_numeric(out["volume_wan"], errors="coerce")
    _cls = pd.to_numeric(out["close"], errors="coerce")
    _amt = pd.to_numeric(out["amount_wan"], errors="coerce")
    _ratio = _amt / (_vol * _cls)
    _szse = out["code"].str.endswith(".SZ")
    _bad_szse = _szse & (_ratio > 0.1) & (_vol > 0) & (_cls > 0) & (_amt > 0)
    _bad_sse = (~_szse) & (_ratio > 100) & (_vol > 0) & (_cls > 0) & (_amt > 0)
    _bad_mask = _bad_szse | _bad_sse
    _n_fixed = int(_bad_mask.sum())
    if _n_fixed > 0:
        out.loc[_bad_mask, "amount_wan"] = out.loc[_bad_mask, "amount_wan"] / 1000.0
        if verbose:
            print(f"    [AMT-FIX] normalized {_n_fixed:,} rows with 1000x amount error "
                  f"(SZSE: {int(_bad_szse.sum()):,}, SSE: {int(_bad_sse.sum()):,})", flush=True)

    out["trading_amount"] = out["amount_wan"] * 10000.0  # 万元 → yuan
    out = out.drop(columns=["amount_wan"])

    if "volume_wan" in out.columns:
        out["trading_shares"] = out["volume_wan"] * 10000.0  # 万份/万股 → shares
        out = out.drop(columns=["volume_wan"])

    if verbose:
        n_szse = out["code"].str.endswith(".SZ").sum()
        n_sse = out["code"].str.endswith(".SS").sum()
        print(f"    [OHLCV] final rows: {len(out):,}  "
              f"unique codes: {out['code'].nunique()}  "
              f"SZSE (.SZ): {n_szse:,}  SSE (.SS): {n_sse:,}  "
              f"date range: {out['date'].min().date()} → {out['date'].max().date()}", flush=True)
    return out
