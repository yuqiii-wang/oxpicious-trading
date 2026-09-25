"""History-CSV conversion and incremental merge for the CNINDEX daily archive.

Output contract (consumed by builds.index.baseline.loaders.load_cnindex_history):
``{code}_history.csv`` under temps/cnindex_archive with the CSIndex history
schema — ISO dates, trading_amount in YUAN, trading_shares in SHARES,
chronological order, utf-8-sig.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any, List, Optional

import pandas as pd

from ._api import (
    COL_AMOUNT,
    COL_CHG,
    COL_CLOSE,
    COL_CURRENT,
    COL_HIGH,
    COL_LOW,
    COL_OPEN,
    COL_PERCENT,
    COL_TIMESTAMP,
    COL_VOLUME,
    ms_to_date,
    to_float,
)

CSINDEX_SCHEMA_COLS = [
    "date", "indexCode", "indexName", "open", "high", "low", "close",
    "trading_shares", "trading_amount", "change", "changePct", "pe",
    "consNumber",
]


def daily_rows_from_bars(code: str, index_name: str, bars: list) -> List[dict]:
    """Convert raw daily bars to CSIndex-schema row dicts.

    Mapping (unit-verified against the 2026-08-07 archive row — see
    _config docstring): amount passes through as YUAN, volume as SHARES;
    ``changePct`` = percent × 100 (API stores a fraction). Rows without a
    parseable timestamp are skipped.
    """
    rows: List[dict] = []
    for b in bars or []:
        if not b or b[COL_TIMESTAMP] is None:
            continue
        d = ms_to_date(b[COL_TIMESTAMP])
        if d is None:
            continue
        close = to_float(b[COL_CLOSE])
        if close is None:
            close = to_float(b[COL_CURRENT])
        pct = to_float(b[COL_PERCENT])
        rows.append({
            "date": d.isoformat(),
            "indexCode": code,
            "indexName": index_name,
            "open": to_float(b[COL_OPEN]),
            "high": to_float(b[COL_HIGH]),
            "low": to_float(b[COL_LOW]),
            "close": close,
            "trading_shares": to_float(b[COL_VOLUME]),
            "trading_amount": to_float(b[COL_AMOUNT]),
            "change": to_float(b[COL_CHG]),
            "changePct": round(pct * 100, 4) if pct is not None else None,
            "pe": None,
            "consNumber": None,
        })
    return rows


def _read_csv_dates(csv_path: Path) -> set:
    """Dates already present in the history CSV (empty set when absent)."""
    if not csv_path.is_file() or csv_path.stat().st_size == 0:
        return set()
    try:
        df = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig")
    except Exception:
        return set()
    if df.empty or "date" not in df.columns:
        return set()
    return set(df["date"].dropna().astype(str).str.strip())


def append_missing_rows_to_csv(rows: List[dict], csv_path: Path) -> int:
    """Append rows whose dates are missing from the existing CSV.

    Append-only (existing rows are never modified), sorted by date,
    utf-8-sig — same contract as the csindex 1m CSV merge. Returns the
    number of newly appended rows.
    """
    if not rows:
        return 0
    existing_dates = _read_csv_dates(csv_path)
    new_rows = [r for r in rows if r["date"] not in existing_dates]
    if not new_rows:
        return 0

    if csv_path.is_file() and csv_path.stat().st_size > 0:
        df_existing = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig")
        df_combined = pd.concat([df_existing, pd.DataFrame(new_rows)],
                                ignore_index=True, sort=False)
    else:
        df_combined = pd.DataFrame(new_rows)

    df_combined = df_combined[CSINDEX_SCHEMA_COLS]
    df_combined = df_combined.sort_values("date").reset_index(drop=True)
    df_combined.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return len(new_rows)


def write_full_csv(rows: List[dict], csv_path: Path) -> int:
    """Rewrite the history CSV entirely from *rows* (force mode)."""
    if not rows:
        return 0
    df = pd.DataFrame(rows)[CSINDEX_SCHEMA_COLS]
    df = df.sort_values("date").reset_index(drop=True)
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return len(df)


def csv_has_date(out_dir: Path, code: str, yyyymmdd: str) -> bool:
    """True iff the code's history CSV contains a row for *yyyymmdd*.

    Content-freshness gate for the download skip decision — a fetch
    timestamp says nothing about what the source published after it ran.
    Reads the header + last ~500KB (append-only chronological CSV, so the
    target date is always near the end); unreadable/missing files count as
    NOT having the date.
    """
    f = out_dir / f"{code}_history.csv"
    if not f.is_file():
        return False
    try:
        fsize = f.stat().st_size
        if fsize == 0:
            return False
        with open(f, "r", encoding="utf-8-sig") as fh:
            header = fh.readline().strip()
        tail_size = min(fsize, 500_000)
        with open(f, "rb") as fh:
            fh.seek(-tail_size, 2)
            if fsize > tail_size:
                fh.readline()  # align to a row boundary
            tail_data = fh.read().decode("utf-8", errors="replace")
        df = pd.read_csv(io.StringIO(header + "\n" + tail_data), dtype=str)
        if df.empty or "date" not in df.columns:
            return False
        iso = f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"
        return df["date"].astype(str).str.strip().eq(iso).any()
    except Exception:
        return False
