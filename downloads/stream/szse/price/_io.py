"""SZSE stream IO — CSV archive writer and DB upsert helpers.

``write_cycle_csv`` archives one cycle's emitted bars to a CSV under
``temps/szse_intraday/``. ``load_bars_sync`` / ``load_index_bars_sync``
upsert identity rows (FK parent) then intraday bars for stocks / indices.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from downloads._common.core import (
    resolve_out_dir,
    setup_logger,
)
from _common.db_commons import bulk_upsert

logger = setup_logger("stream_szse")

# Local CSV archive: one file per cycle, written under temps/szse_intraday/.
CSV_COLUMNS = [
    "update_time", "date", "code", "name", "time",
    "open", "high", "low", "close", "trading_shares", "change", "change_pct",
]


def write_cycle_csv(cycle_dt: datetime, bar_rows: List[dict]) -> Optional[Path]:
    """Archive one cycle's emitted bars to a CSV under temps/szse_intraday/."""
    if not bar_rows:
        return None
    out_dir = resolve_out_dir(__file__, "szse_intraday", None)
    ts = cycle_dt.strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"szse_intraday_{ts}.csv"
    iso = cycle_dt.strftime("%Y-%m-%d %H:%M:%S")

    with open(out_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in bar_rows:
            out = {"update_time": iso}
            out.update(row)
            writer.writerow(out)
    return out_file


def load_bars_sync(conn, identity_rows: List[dict], bar_rows: List[dict]) -> None:
    """Upsert identity rows (FK parent) then intraday bars (sync).

    ``aggregate_5min`` emits one identity row per 5-min bar, so a single stock
    with N bars yields N identical ``{date, code, name}`` rows. A multi-row
    INSERT ... ON CONFLICT (date, code) with duplicate keys raises
    "cannot affect row a second time", so collapse identity rows to one per
    (date, code) before upserting.
    """
    if identity_rows:
        seen = set()
        uniq: List[dict] = []
        for r in identity_rows:
            k = (r["date"], r["code"])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(r)
        bulk_upsert(conn, "stats.stock_identity", uniq, ["date", "code"])
    if bar_rows:
        bulk_upsert(conn, "stats.stock_intraday_5min", bar_rows, ["date", "code", "time"])


def load_index_bars_sync(conn, identity_rows: List[dict], bar_rows: List[dict]) -> None:
    """Upsert index identity rows (FK parent) then intraday bars (sync).

    Targets ``stats.index_identity`` + ``stats.index_intraday_5min`` — the
    index counterparts of the stock tables (no code_suffix, no trading_shares).
    """
    if identity_rows:
        seen = set()
        uniq: List[dict] = []
        for r in identity_rows:
            k = (r["date"], r["code"])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(r)
        bulk_upsert(conn, "stats.index_identity", uniq, ["date", "code"])
    if bar_rows:
        bulk_upsert(conn, "stats.index_intraday_5min", bar_rows, ["date", "code", "time"])
