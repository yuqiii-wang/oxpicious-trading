"""Stock margin (融资融券) data processing for builds.stock.

Contains:
- _scan_stock_margin_dir: read SZSE/SSE margin detail CSVs, filter to stock codes
- build_stock_margin_df: aggregate stock margin data

Margin columns are written by the INDEPENDENT margin pass in
pipeline/writer.py (upsert_margin_only_conn, column-scoped); there is no
OHLCV join here anymore.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.build_commons import glob_source_files
from builds.stock._helpers import _safe_columns
from builds._commons.column_maps import (
    STOCK_MARGIN_COL_MAP as _MARGIN_COLS_SRC,
)


def _scan_stock_margin_dir(
    scan_dir: str,
    file_prefix: str,
    market: str,
    verbose: bool = True,
    files: list[str] | None = None,
    code: str | None = None,
) -> tuple[pd.DataFrame, int, int]:
    """Read margin detail CSVs from one directory, filtering to stock codes.

    Mirrors builds/etf/_scan_margin_dir + build_margin_df, but filters to STOCK
    rows via the canonical sec_type column (ETF/index rows excluded).

    When `code` is set, only byte-lines containing the code token are kept
    per file (WHERE-code pushed down to the raw-byte level) — whole-market
    margin snapshots never materialize in memory during --code builds.

    PERFORMANCE MODEL (profiled): launching one cudf CSV engine per file
    cost ~10s of pure parser overhead for 1,600+ SSE detail files, plus a
    duplicate open per file for the emptiness peek. Now: each file's bytes
    are read ONCE, validated/emptiness-checked/pre-filtered at the byte
    level, tagged with an injected leading "date" column, GROUPED BY exact
    header bytes, and every header-group chunk is parsed with ONE
    pd.read_csv call (chunks capped at _CHUNK_BYTES to bound GPU memory).

    Returns (df, n_files_with_data, n_empty_files).
    """
    if files is None:
        files = glob_source_files(scan_dir, f"{file_prefix}*.csv")

    if verbose:
        print(f"    [STOCK-MARGIN-{market}] reading {len(files)} {file_prefix}*.csv files",
              flush=True)

    from _common.build_commons import ymd_from_filename as _ymd

    # Read-time dtype map: ids/tokens as str, numerics float64 (downloads'
    # conversion guarantees normalized plain floats, so parsing assigns
    # final types directly). Superset keys for absent columns are ignored
    # by both pandas and cudf. "date" is our INJECTED first column.
    dtype_map: dict[str, str] = {
        "date": str,
        "证券代码": str, "证券简称": str,
        "sec_type": str, "exchange": str,
        **{c: "float64" for c in _MARGIN_COLS_SRC},
    }

    token: bytes = (code or "").encode("ascii")  # empty token matches all
    placeholder_marks: tuple[bytes, ...] = ("没有找到".encode(), "无数据".encode())
    frames: list[pd.DataFrame] = []
    groups: dict[bytes, list[bytes]] = {}  # header bytes -> injected data lines
    group_bytes: dict[bytes, int] = {}
    n_empty = 0
    n_ok = 0
    chunk_limit = 32 * 1024 * 1024

    def _flush_group(header: bytes) -> None:
        """Parse one header-group chunk with a SINGLE pd.read_csv call."""
        dlines = groups.pop(header)
        group_bytes.pop(header, None)
        if not dlines:
            return
        merged_header = b'"date",' + header
        merged = b"\n".join([merged_header] + dlines)
        try:
            # compression=None: chunk is our own uncompressed bytes —
            # silences cudf's per-parse AUTO-detection warning.
            df = pd.read_csv(merged, dtype=dtype_map, compression=None)
        except Exception:
            # degrades gracefully: malformed assembled chunk -> skip loudly
            print(f"      [WARN] {market} margin chunk parse failed "
                  f"(~{len(dlines)} lines dropped)", flush=True)
            return
        if "证券代码" not in _safe_columns(df):
            return
        if code is not None:
            df = df[df["证券代码"] == code]
        if not df.empty:
            frames.append(df)

    for path in files:
        ymd_raw = _ymd(path, file_prefix)
        if not ymd_raw:
            continue
        ymd = f"{ymd_raw[:4]}-{ymd_raw[4:6]}-{ymd_raw[6:8]}"
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            n_empty += 1
            continue
        raw = raw.replace(b"\r\n", b"\n")
        if not raw.strip(b"\xef\xbb\xbf \t"):
            n_empty += 1
            continue
        lines = raw.split(b"\n")
        header = lines[0]
        if header.startswith(b"\xef\xbb\xbf"):
            header = header[3:]
        data_all = [ln for ln in lines[1:] if ln.strip()]
        if not data_all or any(m in data_all[0] for m in placeholder_marks):
            n_empty += 1
            continue
        kept = data_all if not token else \
            [ln for ln in data_all if token in ln]
        if not kept:
            # whole snapshot scanned: zero rows for this code
            continue
        n_ok += 1
        tag = f'"{ymd}",'.encode()
        group = groups.setdefault(header, [])
        group.extend(tag + ln for ln in kept)
        group_bytes[header] = group_bytes.get(header, 0) + sum(
            len(tag) + len(ln) for ln in kept)
        if group_bytes[header] >= chunk_limit:
            _flush_group(header)

    for header in list(groups):
        _flush_group(header)

    if not frames:
        return pd.DataFrame(columns=["date", "code", *list(_MARGIN_COLS_SRC)]), n_ok, n_empty

    big = pd.concat(frames, ignore_index=True)

    # --- single vectorized pass (GPU) over all rows of all files ---
    big_cols = _safe_columns(big)
    if "sec_type" not in big_cols:
        # non-canonical files carry no schema — no stock rows for us
        return pd.DataFrame(columns=["date", "code", *list(_MARGIN_COLS_SRC)]), n_ok, n_empty
    # canonical CSV: sec_type == "stock" excludes ETF/index rows by
    # plain column equality (no prefix/regex matching)
    big = big[big["sec_type"] == "stock"]
    if len(big) == 0:
        return pd.DataFrame(columns=["date", "code", *list(_MARGIN_COLS_SRC)]), n_ok, n_empty
    # canonical CSV: 证券代码 already carries the .SS/.SZ suffix; the
    # exchange column is carried through as-is
    big["code"] = big["证券代码"].astype(str)

    out_cols = ["date", "code"]
    if "exchange" in _safe_columns(big):
        out_cols.append("exchange")
    big_cols = _safe_columns(big)
    # STOCK_MARGIN_COL_MAP is {src(Chinese) -> out(english)}; missing source
    # columns (SSE detail exports lack 融券余额(元)/融资融券余额(元)) become
    # NaN and are zeroed by _safe_to_numeric().fillna(0) downstream.
    rename_map: dict[str, str] = {}
    for src_col, out_col in _MARGIN_COLS_SRC.items():
        if src_col in big_cols:
            rename_map[src_col] = out_col
        else:
            big[out_col] = np.nan
        out_cols.append(out_col)
    big = big.rename(columns=rename_map)

    out = big[out_cols].reset_index(drop=True)
    if verbose:
        print(f"    [STOCK-MARGIN-{market}] {n_ok} files with data, {n_empty} empty, "
              f"{len(out)} stock rows", flush=True)
    return out, n_ok, n_empty


def build_stock_margin_df(
    szse_margin_dir: str,
    sse_margin_dir: str,
    verbose: bool = True,
    margin_files: dict | None = None,
    code: str | None = None,
) -> pd.DataFrame:
    """Read margin CSVs from SZSE + SSE dirs and return a long DataFrame.

    `code` is pushed into each per-file read (WHERE-code in the loader).
    """
    frames: list[pd.DataFrame] = []
    n_ok_total = 0
    n_empty_total = 0

    if margin_files is not None:
        scan_jobs: list[tuple[str, str, str, list[str]]] = []
        if margin_files.get("szse"):
            scan_jobs.append((szse_margin_dir, "szse_margin_detail_", "深圳", margin_files["szse"]))
        if margin_files.get("sse"):
            scan_jobs.append((sse_margin_dir, "sse_margin_detail_", "上海", margin_files["sse"]))
    else:
        import os
        scan_jobs: list[tuple[str, str, str, list[str]]] = []
        if os.path.isdir(szse_margin_dir):
            scan_jobs.append((szse_margin_dir, "szse_margin_detail_", "深圳", []))
        if os.path.isdir(sse_margin_dir):
            scan_jobs.append((sse_margin_dir, "sse_margin_detail_", "上海", []))

    for scan_dir, prefix, market, scan_file_list in scan_jobs:
        df_scan, ok_n, empty_n = _scan_stock_margin_dir(
            scan_dir, prefix, market, verbose,
            files=scan_file_list or None,
            code=code,
        )
        n_ok_total += ok_n
        n_empty_total += empty_n
        if not df_scan.empty:
            frames.append(df_scan)

    margin_cols = ["rz_buy", "rz_balance", "rq_sell_qty", "rq_balance_qty",
                   "rq_balance_amt", "total_balance"]
    if not frames:
        if verbose:
            print(f"    [STOCK-MARGIN] total: {n_ok_total} files with data, "
                  f"{n_empty_total} empty, 0 rows", flush=True)
        return pd.DataFrame()

    from builds.stock._helpers import _safe_to_datetime, _safe_to_numeric, _safe_columns as _sc

    out = pd.concat(frames, ignore_index=True)
    out["date"] = _safe_to_datetime(out["date"])
    out = out.dropna(subset=["date"])

    out_cols = _sc(out)
    for c in margin_cols:
        if c not in out_cols:
            out[c] = 0.0
        out[c] = _safe_to_numeric(out[c]).fillna(0.0)

    n_before = len(out)
    out = out.groupby(["date", "code"], as_index=False)[margin_cols].sum()
    n_after = len(out)
    n_merged = n_before - n_after

    if verbose:
        print(f"    [STOCK-MARGIN] total: {n_ok_total} files with data, "
              f"{n_empty_total} empty, {n_before} raw rows → {n_after} merged rows "
              f"({n_merged} duplicates handled)", flush=True)

    out = out.sort_values(["code", "date"]).reset_index(drop=True)
    return out
