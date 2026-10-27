"""downloads.index.hkex_msci.daily — Download the MSCI China A 50 Connect
Index daily closes (HKEX marquee RIC .MICN0A5C0PCY) from the HKEX LabCI
quote widgets.

This index is the underlying of the HKEX MCA futures family
(stats.futures_identity codes MCA%): the Futures UI overlays it as the
spot curve on the MCA contract charts, and analysis.futures consumes it
for the basis-gap features. The series lands in the SHARED index tables —
``stats.index_identity`` + ``stats.index_basic_stats`` — under
``code = 'MICN0A5C0PCY'`` (close + daily open/high/low; weekly-tail rows
carry close only).

Source windows (see config.py): ~6 months of daily bars + ~2 years of
weekly bars per fetch, merged daily-wins; the whole available window is
re-upserted on every run, so incremental and forced runs differ only in
the pre-upsert purge. The exchange-side retention is the boundary — data
older than ~2 years is not retrievable from this source.

Usage:
  python -m downloads.index.hkex_msci.daily
  python -m downloads.index.hkex_msci.daily --force
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

# Project root setup — MUST be before any project imports
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from _common.build_commons import print_build_header, print_wall_time
from _common.db_commons import bulk_upsert, get_db_connection

from downloads._common import resolve_out_dir, setup_logger, single_instance_lock
from downloads.index.hkex_msci.daily.config import (
    DAILY_INT,
    DAILY_SPAN,
    INDEX_CODE,
    INDEX_NAME,
    OUT_DIRNAME,
    SERVICE,
    WEEKLY_INT,
    WEEKLY_SPAN,
)
from downloads.index.hkex_msci.daily.downloader import (
    fetch_chart_rows,
    fetch_token,
    merge_index_rows,
)

logger = setup_logger("hkex_msci_index")

IDENTITY_TABLE: str = "stats.index_identity"
TARGET_TABLE: str = "stats.index_basic_stats"


def run(args: argparse.Namespace) -> None:
    t0 = time.time()
    out_dir: Path = resolve_out_dir(__file__, OUT_DIRNAME, args.out_root)

    print_build_header(
        "DOWNLOAD MSCI CHINA A 50 CONNECT INDEX DAILY  ·  HKEX LabCI widgets",
        **{
            "Widget": "www1.hkex.com.hk/hkexwidget/data/getchartdata2 (JSONP)",
            "Tables": f"{IDENTITY_TABLE} + {TARGET_TABLE} (code {INDEX_CODE})",
            "Windows": f"daily span={DAILY_SPAN}/int={DAILY_INT}, weekly span={WEEKLY_SPAN}/int={WEEKLY_INT}",
            "CSV dir": str(out_dir),
            "Force": args.force,
        },
    )

    # ------------------------------------------------------------------
    # Step 1: fetch — token, then the daily and weekly windows
    # ------------------------------------------------------------------
    logger.info("[1/3] Rendering the prices page for the LabCI token ...")
    token = fetch_token(sleep_sec=args.sleep)
    logger.info("  token extracted (%d chars)", len(token))

    logger.info("[2/3] Fetching chart windows ...")
    daily_rows = fetch_chart_rows(token, DAILY_SPAN, DAILY_INT,
                                  sleep_sec=args.sleep)
    weekly_rows = fetch_chart_rows(token, WEEKLY_SPAN, WEEKLY_INT,
                                   sleep_sec=args.sleep)
    rows = merge_index_rows(daily_rows, weekly_rows)
    if not rows:
        logger.warning("No usable bars from either window — nothing to write.")
        print_wall_time(t0)
        return
    logger.info("  merged: %d dates (%s .. %s; %d with daily OHLC)",
                len(rows), rows[0]["date"], rows[-1]["date"],
                sum(1 for r in rows if r["open"] is not None))

    # ------------------------------------------------------------------
    # Step 2: DB — optional purge, then identity twins + basic stats
    # ------------------------------------------------------------------
    logger.info("[3/3] Upserting ...")
    conn = get_db_connection()
    if args.force:
        deleted_stats = conn.execute(
            f"DELETE FROM {TARGET_TABLE} WHERE code = %s", (INDEX_CODE,)
        ).rowcount
        deleted_identity = conn.execute(
            f"DELETE FROM {IDENTITY_TABLE} WHERE code = %s", (INDEX_CODE,)
        ).rowcount
        conn.commit()
        logger.info("  --force: purged %d basic_stats + %d identity rows",
                    deleted_stats, deleted_identity)

    identity_rows = [
        {"date": r["date"], "code": INDEX_CODE, "name": INDEX_NAME}
        for r in rows
    ]
    basic_rows = [
        {
            "date": r["date"],
            "code": INDEX_CODE,
            "open": r["open"],
            "high": r["high"],
            "low": r["low"],
            "close": r["close"],
        }
        for r in rows
    ]
    inserted = bulk_upsert(conn, IDENTITY_TABLE, identity_rows,
                           key_columns=["code", "date"])
    inserted += bulk_upsert(conn, TARGET_TABLE, basic_rows,
                            key_columns=["code", "date"])
    conn.close()

    # ------------------------------------------------------------------
    # Step 3: CSV audit drop (one dated snapshot file per run)
    # ------------------------------------------------------------------
    out_dir.mkdir(parents=True, exist_ok=True)
    audit_path = out_dir / f"{time.strftime('%Y%m%d')}_msci_a50_connect.csv"
    with open(audit_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "code", "open", "high", "low", "close"])
        for r in rows:
            writer.writerow([r["date"], INDEX_CODE,
                             r["open"] if r["open"] is not None else "",
                             r["high"] if r["high"] is not None else "",
                             r["low"] if r["low"] is not None else "",
                             r["close"]])
    logger.info("  audit drop: %s", audit_path)

    logger.info("Done: %d rows upserted across %d dates (%s .. %s)",
                inserted, len(rows), rows[0]["date"], rows[-1]["date"])
    print_wall_time(t0)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download the MSCI China A 50 Connect Index daily "
                    "closes from the HKEX LabCI quote widgets.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help=f"Purge the index's rows (code = {INDEX_CODE}) from the shared "
             "index tables and backfill the full retained window.",
    )
    parser.add_argument(
        "--out-root", default=None,
        help="Override the CSV audit directory root.",
    )
    parser.add_argument(
        "--sleep", type=float, default=None,
        help="Sleep seconds between requests (default: the service's "
             f"external_request_config row for {SERVICE!r}).",
    )
    args = parser.parse_args()

    from _common.service_heartbeat import autoregister  # lazy: keeps imports cheap
    autoregister()  # heartbeat + run audit (monitor.*)

    try:
        with single_instance_lock(SERVICE):
            run(args)
    except RuntimeError as exc:
        # Overlapping invocations: the active run wins, this one no-ops.
        logger.warning("%s — skipping this invocation", exc)


if __name__ == "__main__":
    main()
