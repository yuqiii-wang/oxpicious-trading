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

# B5 byte-level placeholder token: SZSE/SSE holiday exports carry a single
# "没有找到符合条件的数据！" row with no canonical code token. A real data
# row always contains the "NNNNNN." code prefix (pure ASCII).
_PLACEHOLDER_HINT = "没有找到符合条件的数据".encode("utf-8")


def _csv_has_data_row(path: str) -> bool:
    """True when the file has at least one real data row (byte-level check).

    Reads only the first 64KB — placeholder/holiday exports are tiny. A
    file whose body (after the header line) is empty or carries the
    "没有找到符合条件的数据" no-data hint is treated as empty, so
    permanently-empty dates (139 holiday exports) are skipped without ever
    reaching the CSV parser (was ~8s of re-parsing every run).
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(65536)
    except OSError:
        return False
    nl = head.find(b"\n")
    if nl == -1:
        return False
    body = head[nl + 1:]
    if not body.strip():
        return False
    if _PLACEHOLDER_HINT in body:
        return False
    return True


def _drop_placeholder_files(files: List[str], label: str, verbose: bool = True) -> List[str]:
    good = [f for f in files if _csv_has_data_row(f)]
    n_skip = len(files) - len(good)
    if verbose and n_skip:
        print(f"    [B5] {label}: skipped {n_skip} placeholder/holiday "
              f"CSVs (byte-level check)", flush=True)
    return good


async def load_source_frames(
    conn,
    files: Dict[str, List[str]],
    *,
    force: bool,
    code_filter: str | None,
    dates_to_read: Set,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | None]:
    """Read the source CSVs needed this run and merge in DB history.

    Force mode reads ALL files; up-to-date mode skips CSV reads entirely and
    queries only the DB; otherwise only ``dates_to_read`` files are parsed
    (glob push-down by filename date key) and combined with DB history.

    Returns: (ohlcv_df, margin_df, adj_seeds). adj_seeds is None except on
    the window-truncated market-wide DB path (B3) — pass it through to
    prepare_features.
    """
    if force:
        print("\n[3/7] Reading ALL source CSVs (force mode) …", flush=True)
        ohlcv_df = build_ohlcv_df(verbose=True, code=code_filter)
        margin_df = build_margin_df(verbose=True, code=code_filter)
        adj_seeds = None
    elif not dates_to_read:
        print("\n[3/7] OHLCV up to date — querying DB for historical context only …", flush=True)
        ohlcv_df, margin_df, adj_seeds = await query_existing_ohlcv_margin_from_db(
            conn, verbose=True, code=code_filter)
    else:
        from builds.etf.pipeline.discover import restrict_files_to_dates, YMD_PREFIXES
        read_ymd = {d.strftime("%Y%m%d") for d in dates_to_read}
        subset = restrict_files_to_dates(files, read_ymd)
        # B5: permanently-empty (holiday/placeholder) dates stay "missing"
        # forever — byte-check each scheduled file so they cost nothing.
        for _name in ("szse_archive", "szse_trend", "sse_trend"):
            subset[_name] = _drop_placeholder_files(subset[_name], _name)
        subset["szse"] = _drop_placeholder_files(subset["szse"], "szse_margin")
        subset["sse"] = _drop_placeholder_files(subset["sse"], "sse_margin")
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

        hist_ohlcv_df, hist_margin_df, adj_seeds = await query_existing_ohlcv_margin_from_db(
            conn, verbose=True, code=code_filter)
        ohlcv_df = _combine_frames(hist_ohlcv_df, new_ohlcv_df)
        margin_df = _combine_frames(hist_margin_df, new_margin_df)

    # Single-code restriction early — split adjustment and MAs are per-code.
    # Boolean-mask results are fresh frames; downstream steps (split
    # adjustment, features, writes) sort/emit index-blind, so no reindex.
    if code_filter:
        if len(ohlcv_df) > 0:
            n_before = len(ohlcv_df)
            ohlcv_df = ohlcv_df[ohlcv_df["code"] == code_filter]
            print(f"    [CODE FILTER] OHLCV rows {n_before:,} → {len(ohlcv_df):,} for code {code_filter}", flush=True)
        if len(margin_df) > 0:
            n_before = len(margin_df)
            margin_df = margin_df[margin_df["code"] == code_filter]
            print(f"    [CODE FILTER] Margin rows {n_before:,} → {len(margin_df):,} for code {code_filter}", flush=True)

    if len(ohlcv_df) == 0:
        print("    [FATAL] No OHLCV rows to process — check source files and DB", flush=True)
        sys.exit(1)
    if len(margin_df) == 0:
        print("    [WARN] No margin rows — proceeding with OHLCV only", flush=True)
        margin_df = pd.DataFrame(columns=EMPTY_MARGIN_COLS)
    return ohlcv_df, margin_df, adj_seeds


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
