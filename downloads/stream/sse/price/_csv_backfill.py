"""SSE CSV backfill — load raw 1-min CSV snapshots to DB as 5-min OHLCV bars.

Scans temps/<csv_subdir>/ for daily CSV files, groups raw 1-min samples
into 5-min windows (ceiling_5min convention), aggregates OHLCV per code,
and upserts identity + bar rows to the asset's DB tables.

Called at startup and every 5 minutes (even outside trading hours) so CSV
data that wasn't loaded during live streaming (e.g. DB connection failure,
stream crash/restart) is recovered automatically. Idempotent: re-upserting
the same (date, code, time) row via ON CONFLICT just updates it.

Date-check pattern (fast-path before reading any CSV content):
  1. Glob <prefix>_*.csv files (filenames only — no reading yet)
  2. Extract dates from filenames
  3. Query DB for MAX(time) per date
  4. A date is complete when the DB reaches CLOSE_TIME (15:00), or — for
     days the source stopped before 15:00 (early halt) — when the DB has
     caught up with the file's own last 5-min window (CSV tail read only)
  5. Read ONLY the files with incomplete dates

The second rule matters because a short day can never reach CLOSE_TIME;
without it the file would be re-read and re-upserted every cycle forever.
"""
from __future__ import annotations

import csv
import re
import time as _time
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from downloads._common import resolve_out_dir, setup_logger
from _common.db_commons import bulk_upsert

from ._io import is_intraday_complete
from ._model import AssetStream, CSV_COLUMNS, CLOSE_TIME, ceiling_5min, aggregate_bars

logger = setup_logger("stream_sse")

# Backfill interval: how often the main loop calls backfill_all_csvs.
BACKFILL_INTERVAL_SEC = 5 * 60

# Tail size read by _file_last_window to find a CSV's last snapshot.
_TAIL_BYTES = 64 * 1024
_UPDATE_TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


def _file_last_window(csv_path: Path) -> Optional[dtime]:
    """Return the ceiling_5min window of a CSV's last snapshot, or None.

    Reads only the tail of the file: write_snapshot_csv appends polls in
    order and all rows of one poll share an update_time, so the last
    timestamp in the file is the latest. A line cut in half by the tail
    boundary simply fails to match the timestamp pattern.

    Used to classify days whose source data ended before CLOSE_TIME: such
    a date can never reach 15:00 in the DB, so "complete" has to mean
    "caught up with the file's own last window" instead.
    """
    try:
        size = csv_path.stat().st_size
        if size == 0:
            return None
        with open(csv_path, "rb") as f:
            f.seek(max(0, size - _TAIL_BYTES))
            chunk = f.read().decode("utf-8-sig", errors="replace")
        matches = _UPDATE_TIME_RE.findall(chunk)
        if not matches:
            return None
        last_dt = datetime.strptime(max(matches), "%Y-%m-%d %H:%M:%S")
        return ceiling_5min(last_dt.time())
    except OSError as e:
        logger.warning("backfill: tail read failed for %s: %s", csv_path.name, e)
        return None


def _get_db_max_times(
    conn,
    asset: AssetStream,
    all_dates: Set[date],
) -> Dict[date, Optional[dtime]]:
    """Return {date: MAX(time)} in the asset's intraday table per date.

    None means the DB has no bars for that date yet.

    Single SQL query — batch-checks all dates at once instead of per-file.
    """
    if not all_dates:
        return {}

    date_list = list(all_dates)
    cur = conn.execute(
        f"SELECT date, MAX(time) AS max_time "
        f"FROM {asset.intraday_table} "
        f"WHERE date = ANY(%s) "
        f"GROUP BY date",
        (date_list,),
    )
    db_max = {row[0]: row[1] for row in cur.fetchall()}
    return {d: db_max.get(d) for d in all_dates}


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


def _extract_date_from_filename(csv_path: Path) -> Optional[date]:
    """Extract trading date from a CSV filename like '<prefix>_YYYYMMDD.csv'."""
    match = re.search(r"(\d{8})", csv_path.stem)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def backfill_csv_file(
    conn,
    asset: AssetStream,
    csv_path: Path,
    etf_member_codes: Optional[set] = None,
    last_window: Optional[dtime] = None,
) -> Tuple[int, int]:
    """Load one CSV file, aggregate into 5-min bars, upsert to DB.

    Returns (n_identity, n_bars) upserted.

    DB-completeness guard: If the intraday table already has complete
    bars for this CSV's date (latest bar >= CLOSE_TIME, or — when
    ``last_window`` is given — the DB reached the file's own last window
    on a day the source ended before 15:00), the file is skipped entirely
    — no redundant CSV parsing or re-insertion.
    """
    if not csv_path.exists():
        return 0, 0

    # --- DB-completeness guard: check if this date is already fully loaded ---
    trade_date = _extract_date_from_filename(csv_path)
    if trade_date is not None:
        try:
            if is_intraday_complete(conn, asset, trade_date, last_window=last_window):
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
    is_suffixed = asset.exchange is not None
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
            exchange=asset.exchange,
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

    Date-check fast-path: for each asset, filenames are checked first to
    identify dates already complete in the DB (latest bar >= 15:00, or —
    for days the source ended early — the DB caught up with the file's
    own last window). Only CSV files for incomplete dates are read —
    avoiding redundant per-file completeness checks and CSV parsing for
    already-complete days.

    Returns total number of bars upserted across all assets.
    """
    total_bars = 0
    for asset in assets:
        if asset.name == "options":
            # Options need their own path: bars subtract day-cumulative
            # volume AND amount, codes are numeric contract ids from the
            # day-contract map, and completeness is MAX(time)-based (the
            # identity table also holds SZSE/CFFEX rows that never stream,
            # so the generic ratio check can never pass).
            from ._options import backfill_options_asset

            try:
                total_bars += backfill_options_asset(conn, asset, asset.contract_map)
            except Exception as e:  # noqa: BLE001
                logger.warning("backfill options failed: %s", e)
            continue
        out_dir = resolve_out_dir(
            None,  # __file__ not needed; resolve_out_dir uses project root
            asset.csv_subdir,
            None,
        )
        csv_files = sorted(out_dir.glob(f"{asset.csv_prefix}_*.csv"))
        if not csv_files:
            continue

        # --- FAST-PATH: Extract dates from filenames (no CSV reading) ---
        file_dates: Dict[Path, Optional[date]] = {}
        all_dates: Set[date] = set()
        for f in csv_files:
            d = _extract_date_from_filename(f)
            file_dates[f] = d
            if d is not None:
                all_dates.add(d)

        if not all_dates:
            logger.info("backfill %s: no parseable dates in %d CSVs",
                        asset.name, len(csv_files))
            continue

        logger.info(
            "backfill %s: %d CSVs → %d unique dates, checking completeness",
            asset.name, len(csv_files), len(all_dates),
        )

        # --- Batch-check completeness: only read CSVs for incomplete dates ---
        # A date is complete when the DB reaches CLOSE_TIME, or (short days,
        # where the source stopped before 15:00) when the DB has caught up
        # with the CSV's own last 5-min window — tail read only.
        db_max_times = _get_db_max_times(conn, asset, all_dates)

        incomplete_dates: Set[date] = set()
        caught_up_dates: Set[date] = set()
        last_window_by_date: Dict[date, Optional[dtime]] = {}
        for f in csv_files:
            d = file_dates.get(f)
            if d is None or d in incomplete_dates or d in caught_up_dates:
                continue
            db_max = db_max_times.get(d)
            if db_max is None:
                incomplete_dates.add(d)
                continue
            if db_max >= CLOSE_TIME:
                continue
            last_window = _file_last_window(f)
            last_window_by_date[d] = last_window
            if last_window is not None and db_max >= last_window:
                caught_up_dates.add(d)
            else:
                incomplete_dates.add(d)

        if not incomplete_dates:
            logger.info(
                "backfill %s: all %d dates already complete in DB — skipping all reads",
                asset.name, len(all_dates),
            )
            continue

        n_skipped = len(all_dates) - len(incomplete_dates)
        logger.info(
            "backfill %s: %d dates incomplete (skipping %d complete dates, "
            "%d caught up with early-ended CSVs)",
            asset.name, len(incomplete_dates), n_skipped, len(caught_up_dates),
        )

        # Filter files to only those with incomplete dates
        filtered_files: List[Path] = []
        for f in csv_files:
            d = file_dates.get(f)
            if d is not None and d in incomplete_dates:
                filtered_files.append(f)

        if not filtered_files:
            continue

        logger.info(
            "backfill %s: reading %d/%d CSV files",
            asset.name, len(filtered_files), len(csv_files),
        )

        for csv_path in filtered_files:
            try:
                _, n_bars = backfill_csv_file(
                    conn, asset, csv_path, etf_member_codes,
                    last_window=last_window_by_date.get(file_dates.get(csv_path)),
                )
                total_bars += n_bars
            except Exception as e:
                logger.warning(
                    "backfill %s: %s failed: %s", asset.name, csv_path.name, e,
                )
    if total_bars > 0:
        logger.info("backfill complete: %d total bars upserted.", total_bars)
    return total_bars
