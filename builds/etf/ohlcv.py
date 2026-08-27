"""ETF OHLCV source CSV reader.

Reads SZSE archive/trend + SSE trend canonical CSVs and returns a long
DataFrame with raw OHLCV + trading volume/amount (converted to shares/yuan).

Source CSVs carry the canonical code schema written by downloads
(证券代码 = "NNNNNN.XX" + exchange/board/sec_type columns — see
downloads._common.ensure_canonical_csv), so per-file work is minimal
(read + sec_type filter + date tag); all row-level transforms run once on
the concatenated frame using vectorized ops only. Numeric columns come back
float64 from read_csv auto-inference — no per-cell parsing needed.
"""
import glob
import os
from pathlib import Path

import numpy as np
import pandas as pd

from downloads._common import read_build_csv
from _common.build_commons import ymd_from_filename
from _common.df_utils import safe_columns
from builds.etf.paths import (
    SZSE_ARCHIVE_DIR, SZSE_TREND_DIR, SSE_TREND_DIR,
)
from builds.etf.codes import MONEY_MARKET_KW

# source column → output column (identical names across all canonical
# exports except the volume column, which is per-market and renamed by
# the caller — 成交量（万份） SZSE ETF vs 成交量(万股) SSE)
_OHLCV_RENAME = {
    "证券代码": "code",
    "证券简称": "name",
    "exchange": "exchange",
    "前收": "prev_close",
    "开盘": "open",
    "最高": "high",
    "最低": "low",
    "今收": "close",
    "涨跌幅（%）": "pct_change",
    "成交金额(万元)": "amount_wan",
}
# ``exchange`` is carried by the rename map itself (exchange→exchange) so
# downstream conditions branch on the DB-driven column — never on code-suffix
# string ops.
_OHLCV_OUT_COLS = ["date"] + list(_OHLCV_RENAME.values()) + ["volume_wan"]


def _scan_ohlcv_dir(scan_dir, file_prefix, volume_col, files=None, code=None):
    """Read canonical per-date CSVs; return (frames, n_ok, n_empty, n_total).

    volume_col is the source volume column name ("成交量（万份）" for SZSE
    ETF exports, "成交量(万股)" for SSE); it is renamed to ``volume_wan``.
    When *code* is set (canonical "NNNNNN.SZ/.SS"), each CSV is
    byte-prefiltered to that code's lines BEFORE parsing so --code builds
    never parse whole-market snapshots.
    """
    if files is None:
        files = sorted(glob.glob(os.path.join(scan_dir, f"{file_prefix}*.csv")))
    else:
        files = [f for f in files if os.path.basename(f).startswith(file_prefix)]
    frames: list[pd.DataFrame] = []
    n_empty = 0
    n_ok = 0
    for path in files:
        ymd = ymd_from_filename(path, file_prefix)
        if not ymd:
            continue
        # CSV ONLY — canonical CSVs are guaranteed by downloads; the
        # former xlsx-fallback path here is removed (a missing CSV is
        # a downloads bug, not something builds paper over). A PARSE
        # EXCEPTION here is likewise a downloads-conversion bug — it
        # propagates and stops the run instead of being miscounted as
        # an "empty" file. Legit empties (missing/placeholder exports)
        # come back as None/empty frames WITHOUT raising.
        df = read_build_csv(
            path,
            dtype={"证券代码": str, "证券简称": str, "sec_type": str, "exchange": str},
            code=code,
        )
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
        df = df.rename(columns={**_OHLCV_RENAME, volume_col: "volume_wan"})
        df["date"] = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"  # scalar broadcast
        frames.append(df[_OHLCV_OUT_COLS])
        n_ok += 1
    return frames, n_ok, n_empty, len(files)


def build_ohlcv_df(verbose=True, ohlcv_files=None, code=None):
    """Read OHLCV source CSVs and return a long DataFrame.

    Args:
        ohlcv_files: if provided, a dict with keys "szse_archive", "szse_trend",
                     "sse_trend" mapping to lists of file paths. Only these
                     files are read (incremental mode — caller already filtered
                     to missing dates via DB query). If None, glob all files
                     in the source directories (--force mode).
        code: optional single-ETF filter (canonical "NNNNNN.SZ"/".SS") —
              enables byte-prefiltered reads (see read_csv_preferred).

    Split adjustment and MA computation require the full per-code chronological
    history. In incremental mode, the caller queries the DB for existing data
    and concatenates it with the new rows before applying split/MA.
    """
    # (dir, file prefix, source volume column, ohlcv_files dict key)
    scans: list[tuple[str, str, str, str]] = [
        (SZSE_ARCHIVE_DIR, "szse_etf_", "成交量（万份）", "szse_archive"),
        (SZSE_TREND_DIR, "szse_trend_etf_", "成交量（万份）", "szse_trend"),
        (SSE_TREND_DIR, "sse_trend_etf_", "成交量(万股)", "sse_trend"),
    ]

    frames: list[pd.DataFrame] = []
    for scan_dir, prefix, volume_col, file_key in scans:
        if not os.path.isdir(scan_dir):
            continue
        files = ohlcv_files.get(file_key) if ohlcv_files is not None else None
        if ohlcv_files is not None and not files:
            continue  # incremental: nothing to read for this source
        fs, n_ok, n_empty, n_tot = _scan_ohlcv_dir(scan_dir, prefix, volume_col,
                                                   files=files, code=code)
        if verbose:
            print(f"    [OHLCV-{file_key}] read {n_tot} files  "
                  f"{n_ok} ok  {n_empty} empty  {sum(len(f) for f in fs):,} rows", flush=True)
        frames.extend(fs)

    if not frames:
        if verbose:
            print("    [OHLCV] no source rows", flush=True)
        return pd.DataFrame()

    # ---- ONE vectorized pass over the concatenated frame ----
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"])
    out = out.dropna(subset=["date"])

    # money-market / bond ETF filter — keyword OR via one boolean matrix
    # any() pass (no Series |= accumulation, which cudf can't fast-path)
    name_s = out["name"]
    _mm = np.logical_or.reduce([
        np.asarray(name_s.str.contains(kw, regex=False, na=False))
        for kw in MONEY_MARKET_KW
    ])
    out = out[~pd.Series(_mm, index=out.index)]

    out["name"] = out["name"].fillna("").astype(str).str.strip()
    out = out.sort_values(["date", "code", "volume_wan"], kind="mergesort")
    out = out.drop_duplicates(subset=["date", "code"], keep="last")
    out = out.sort_values(["code", "date"]).reset_index(drop=True)

    # Unit validation — downloads conversion guarantees 万元 ≈ 万份 × 元
    # (VWAP within the day's range ⇒ ratio ∈ [~0.5, ~1.3]; a generous
    # [0.2, 5] band still catches unit errors, which are 1000x). A ratio
    # far outside is a DOWNLOADS BUG — hard fail, never silently "fix":
    # the former /1000 heuristic fired on CORRECT rows once the decimal
    # parser was repaired and corrupted amounts 1000x.
    _vol = out["volume_wan"]
    _cls = out["close"]
    _amt = out["amount_wan"]
    _valid = (_vol > 0) & (_cls > 0) & (_amt > 0)
    _ratio = _amt / (_vol * _cls)
    _bad = _valid & ~_ratio.between(0.2, 5.0)
    if bool(_bad.any()):
        _i = int(np.asarray(_bad).argmax())
        _r = float(np.asarray(_ratio)[_i])
        raise ValueError(
            f"[OHLCV] unit check failed: amount/(volume*close) = {_r:.4f} "
            f"for {int(_bad.sum())} row(s) (e.g. code={out['code'].iloc[_i]}, "
            f"date={out['date'].iloc[_i]}) — downloads amount/volume unit "
            f"conversion is wrong, fix downloads")

    out["trading_amount"] = out["amount_wan"] * 10000.0  # 万元 → yuan
    out = out.drop(columns=["amount_wan"])

    out["trading_shares"] = out["volume_wan"] * 10000.0  # 万份/万股 → shares
    out = out.drop(columns=["volume_wan"])

    if verbose:
        n_szse = int(out["exchange"].eq("SZ").sum())
        n_sse = int(out["exchange"].eq("SS").sum())
        print(f"    [OHLCV] final rows: {len(out):,}  "
              f"unique codes: {out['code'].nunique()}  "
              f"SZSE (.SZ): {n_szse:,}  SSE (.SS): {n_sse:,}  "
              f"date range: {out['date'].min().date()} → {out['date'].max().date()}", flush=True)
    return out
