"""Step 3 — read missing-date source CSVs + combine with DB history."""
from __future__ import annotations

import sys
from typing import Dict, List, Set

import pandas as pd

from builds.etf.ohlcv import build_ohlcv_df
from builds.etf.margin import build_margin_df
from builds.etf.db_query import query_existing_ohlcv_margin_from_db

EMPTY_MARGIN_COLS: List[str] = [
    "date", "code", "rz_buy", "rz_balance", "rq_sell_qty", "rq_balance_qty",
    "rq_balance_amt", "total_balance",
]


async def load_source_frames(
    conn,
    files: Dict[str, List[str]],
    *,
    force: bool,
    code_filter: str | None,
    dates_to_read: Set,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the source CSVs needed this run and merge in DB history.

    Force mode reads ALL files; up-to-date mode skips CSV reads entirely and
    queries only the DB; otherwise only ``dates_to_read`` files are parsed
    (glob push-down by filename date key) and combined with DB history.

    Returns: (ohlcv_df, margin_df), each sorted by (code, date).
    """
    if force:
        print("\n[3/7] Reading ALL source CSVs (force mode) …", flush=True)
        ohlcv_df = build_ohlcv_df(verbose=True, code=code_filter)
        margin_df = build_margin_df(verbose=True, code=code_filter)
    elif not dates_to_read:
        print("\n[3/7] OHLCV up to date — querying DB for historical context only …", flush=True)
        ohlcv_df, margin_df = await query_existing_ohlcv_margin_from_db(
            conn, verbose=True, code=code_filter)
    else:
        from builds.etf.pipeline.discover import restrict_files_to_dates, YMD_PREFIXES
        read_ymd = {d.strftime("%Y%m%d") for d in dates_to_read}
        subset = restrict_files_to_dates(files, read_ymd)
        print(f"\n[3/7] Reading source CSVs for {len(dates_to_read)} dates "
              f"+ querying DB for historical context …", flush=True)
        print(f"    → OHLCV files to read: {len(subset['szse_archive'])} szse_archive + "
              f"{len(subset['szse_trend'])} szse_trend + {len(subset['sse_trend'])} sse_trend", flush=True)
        print(f"    → Margin files to read: {len(subset['szse'])} szse + "
              f"{len(subset['sse'])} sse", flush=True)

        new_ohlcv_df = build_ohlcv_df(
            verbose=True, ohlcv_files=subset, code=code_filter)
        new_margin_df = build_margin_df(
            verbose=True, margin_files={"szse": subset["szse"], "sse": subset["sse"]},
            code=code_filter)

        hist_ohlcv_df, hist_margin_df = await query_existing_ohlcv_margin_from_db(
            conn, verbose=True, code=code_filter)
        ohlcv_df = _combine_frames(hist_ohlcv_df, new_ohlcv_df)
        margin_df = _combine_frames(hist_margin_df, new_margin_df)

    # Single-code restriction early — split adjustment and MAs are per-code.
    if code_filter:
        if len(ohlcv_df) > 0:
            n_before = len(ohlcv_df)
            ohlcv_df = ohlcv_df[ohlcv_df["code"] == code_filter].reset_index(drop=True)
            print(f"    [CODE FILTER] OHLCV rows {n_before:,} → {len(ohlcv_df):,} for code {code_filter}", flush=True)
        if len(margin_df) > 0:
            n_before = len(margin_df)
            margin_df = margin_df[margin_df["code"] == code_filter].reset_index(drop=True)
            print(f"    [CODE FILTER] Margin rows {n_before:,} → {len(margin_df):,} for code {code_filter}", flush=True)

    if len(ohlcv_df) == 0:
        print("    [FATAL] No OHLCV rows to process — check source files and DB", flush=True)
        sys.exit(1)
    if len(margin_df) == 0:
        print("    [WARN] No margin rows — proceeding with OHLCV only", flush=True)
        margin_df = pd.DataFrame(columns=EMPTY_MARGIN_COLS)
    return ohlcv_df, margin_df


def _combine_frames(hist_df: pd.DataFrame, new_df: pd.DataFrame) -> pd.DataFrame:
    """Concat DB history + new CSV rows, keeping last on overlapping keys."""
    if len(hist_df) and len(new_df):
        combined = pd.concat([hist_df, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["date", "code"], keep="last")
    elif len(new_df):
        combined = new_df
    else:
        combined = hist_df
    return combined.sort_values(["code", "date"]).reset_index(drop=True)
