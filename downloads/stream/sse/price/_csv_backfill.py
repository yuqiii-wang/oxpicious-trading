"""SSE CSV backfill — load raw 1-min CSV snapshots to DB as 5-min OHLCV bars.

Scans temps/<csv_subdir>/ for daily CSV files, groups raw 1-min samples
into 5-min windows (ceiling_5min convention), aggregates OHLCV per code,
and upserts identity + bar rows to the asset's DB tables.

Called at startup and every 5 minutes (even outside trading hours) so CSV
data that wasn't loaded during live streaming (e.g. DB connection failure,
stream crash/restart) is recovered automatically. Idempotent: re-upserting
the same (date, code, time) row via ON CONFLICT just updates it.

DB-completeness guard: Before processing a CSV file, checks whether the
intraday table already has complete bars for that date (latest bar >=
CLOSE_TIME). If complete, skips the file entirely — no redundant CSV
parsing or re-insertion.
"""
from __future__ import annotations

import csv
import re
import time as _time
from datetime import datetime, time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from downloads._common.core import resolve_out_dir, setup_logger
from _common.db_commons import bulk_upsert

from ._io import is_intraday_complete
from ._model import AssetStream, CSV_COLUMNS, CLOSE_TIME, ceiling_5min, aggregate_bars

logger = setup_logger("stream_sse")

# Backfill interval: how often the main loop calls backfill_all_csvs.
BACKFILL_INTERVAL_SEC = 5 * 60  # 5 minutes


def _parse_csv_row(row: dict) -> Optional[Tuple[datetime, str, dict]]:
    """Parse one CSV row into (update_dt, code, record) or None on failure."""
    raw_time = row.get("update_time", "")
    code = (row.get("code") or "").strip()
    if not raw_time or not code:
        return None
    try:
        dt = datetime.strptime(raw_time.strip()[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    rec = {}
    for k in ("last", "volume", "open", "high", "low", "prev_close", "amount"):
        v = row.get(k)
        if v is None or v == "":
            continue
        try:
            rec[k] = float(v)
        except (ValueError, TypeError):
            pass
    rec["name"] = row.get("name") or ""
    return dt, code, rec


def _group_csv_by_windows(
    csv_path: Path,
) -> Dict[time, List[Tuple[datetime, Dict[str, dict]]]]:
    """Load a CSV file and group samples by 5-min ceiling window.

    Returns {bar_time: [(update_dt, snapshot), ...]} where snapshot is
    {code: record}. Each window will be fed to aggregate_bars.
    """
    windows: Dict[time, List[Tuple[datetime, Dict[str, dict]]]] = {}
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        # Group rows by update_time first → snapshot
        snapshots: Dict[datetime, Dict[str, dict]] = {}
        for row in reader:
            parsed = _parse_csv_row(row)
            if parsed is None:
                continue
            dt, code, rec = parsed
            snapshots.setdefault(dt, {})[code] = rec
        # Sort by time and group by 5-min ceiling
        for dt in sorted(snapshots.keys()):
            snap = snapshots[dt]
            wend = ceiling_5min(dt.time())
            windows.setdefault(wend, []).append((dt, snap))
    return windows


def _extract_date_from_filename(csv_path: Path) -> Optional[datetime]:
    """Extract trading date from a CSV filename like '<prefix>_YYYYMMDD.csv'."""
    match = re.search(r"(\d{8})", csv_path.stem)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d")
    except ValueError:
        return None


def backfill_csv_file(
    conn,
    asset: AssetStream,
    csv_path: Path,
    etf_member_codes: Optional[set] = None,
) -> Tuple[int, int]:
    """Load one CSV file, aggregate into 5-min bars, upsert to DB.

    Returns (n_identity, n_bars) upserted.

    DB-completeness guard: If the intraday table already has complete
    bars for this CSV's date (latest bar >= CLOSE_TIME), the file is
    skipped entirely — no redundant CSV parsing or re-insertion.
    """
    if not csv_path.exists():
        return 0, 0

    # --- DB-completeness guard: check if this date is already fully loaded ---
    trade_date = _extract_date_from_filename(csv_path)
    if trade_date is not None:
        try:
            if is_intraday_complete(conn, asset, trade_date.date()):
                logger.info(
                    "backfill %s: %s already complete in DB (latest >= 15:00); "
                    "skipping CSV.",
                    asset.name, csv_path.name,
                )
                return 0, 0
        except Exception as e:
            logger.warning(
                "backfill %s: completeness check failed for %s: %s "
                "(will proceed with backfill)",
                asset.name, csv_path.name, e,
            )

    t0 = _time.time()
    windows = _group_csv_by_windows(csv_path)
    if not windows:
        return 0, 0

    # For each 5-min window, build a mini-buffer and call aggregate_bars.
    # We use a scratch AssetStream to avoid mutating the live asset state.
    is_suffixed = asset.code_suffix is not None
    is_stock = asset.name == "stock"

    all_identity: List[dict] = []
    all_bars: List[dict] = []
    prev_bar_cumvol: Dict[str, float] = {}

    for wend in sorted(windows.keys()):
        samples = windows[wend]
        # Build a temporary buffer for aggregate_bars
        scratch = AssetStream(
            name=asset.name,
            list_url=asset.list_url,
            identity_table=asset.identity_table,
            intraday_table=asset.intraday_table,
            code_suffix=asset.code_suffix,
            has_volume=asset.has_volume,
            allowed_codes=asset.allowed_codes,
            csv_subdir=asset.csv_subdir,
            csv_prefix=asset.csv_prefix,
        )
        scratch.buffer = samples
        scratch.prev_bar_cumvol = prev_bar_cumvol
        scratch.finished_codes = set()  # no skip during backfill

        trade_date = samples[0][0].date()
        identity_rows, bar_rows, bar_time = aggregate_bars(
            scratch, trade_date, etf_member_codes=etf_member_codes,
        )
        all_identity.extend(identity_rows)
        all_bars.extend(bar_rows)

    # Dedup identity rows by (date, code) and upsert
    n_identity = 0
    n_bars = 0
    if all_identity:
        seen = set()
        uniq = []
        for r in all_identity:
            k = (r["date"], r["code"])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(r)
        bulk_upsert(conn, asset.identity_table, uniq, ["date", "code"])
        n_identity = len(uniq)
    if all_bars:
        bulk_upsert(conn, asset.intraday_table, all_bars, ["date", "code", "time"])
        n_bars = len(all_bars)

    elapsed = _time.time() - t0
    logger.info(
        "backfill %s: %s → %d windows, %d identity + %d bars in %.1fs",
        asset.name, csv_path.name, len(windows), n_identity, n_bars, elapsed,
    )
    return n_identity, n_bars


def backfill_all_csvs(
    conn,
    assets: List[AssetStream],
    etf_member_codes: Optional[set] = None,
) -> int:
    """Scan for all CSV files for each asset and backfill to DB.

    Returns total number of bars upserted across all assets.
    """
    total_bars = 0
    for asset in assets:
        out_dir = resolve_out_dir(
            None,  # __file__ not needed; resolve_out_dir uses project root
            asset.csv_subdir,
            None,
        )
        csv_files = sorted(out_dir.glob(f"{asset.csv_prefix}_*.csv"))
        if not csv_files:
            continue
        logger.info(
            "backfill %s: scanning %d CSV files in %s/",
            asset.name, len(csv_files), out_dir,
        )
        for csv_path in csv_files:
            try:
                _, n_bars = backfill_csv_file(
                    conn, asset, csv_path, etf_member_codes,
                )
                total_bars += n_bars
            except Exception as e:
                logger.warning(
                    "backfill %s: %s failed: %s", asset.name, csv_path.name, e,
                )
    if total_bars > 0:
        logger.info("backfill complete: %d total bars upserted.", total_bars)
    return total_bars
