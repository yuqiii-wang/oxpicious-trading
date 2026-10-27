"""downloads.futures.sgx_a50 — Download SGX FTSE China A50 futures daily bars.

Daily OHLCV for the SGX A50 chain (front-month continuous ``CN00Y`` + every
listed contract) from Eastmoney's global-futures kline API (market 104),
written to the SHARED futures tables — ``stats.futures_identity`` +
``stats.futures_basic_stats`` (the CFFEX family in
``database/sql/stats/12_futures.sql``; this family's codes all match
``CN%``) — plus a CFFEX-format CSV audit drop
(``temps/sgx_a50_daily/YYYYMM/YYYYMMDD_futures.csv`` — same layout as
``temps/cffex_trend/``, see ``csv_transform.py``).

Incremental behaviour:
  * continuous (``CN00Y``): re-fetched in FULL on every run — Eastmoney
    re-splices it on each roll without back-adjustment, so its history
    mutates; the upsert makes the refetch idempotent.
  * contracts: tail fetch from ``MAX(date) + 1 day``; new codes and codes
    with no DB rows fetch full history; contracts already up to date are
    skipped entirely.
  * ``--force``: purge the family's rows (``code LIKE 'CN%'``) from both
    futures tables, then re-fetch everything.

Usage:
  python -m downloads.futures.sgx_a50
  python -m downloads.futures.sgx_a50 --codes CN00Y,CN26V   # scope a run
  python -m downloads.futures.sgx_a50 --force
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Project root setup — MUST be before any project imports
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from _common.build_commons import print_build_header, print_wall_time
from _common.db_commons import bulk_upsert, get_db_connection

from downloads._common import resolve_out_dir, setup_logger, single_instance_lock
from downloads.futures.sgx_a50.config import (
    CONTINUOUS_CODE,
    FAMILY_CODE_LIKE,
    OUT_DIRNAME,
    SERVICE,
)
from downloads.futures.sgx_a50.csv_transform import write_cffex_style_files
from downloads.futures.sgx_a50.downloader import (
    A50Instrument,
    enumerate_a50_codes,
    fetch_daily_rows,
    identity_rows_for,
    next_day,
)

logger = setup_logger("sgx_a50")

TARGET_TABLE: str = "stats.futures_basic_stats"
IDENTITY_TABLE: str = "stats.futures_identity"


def get_max_dates(conn) -> Dict[str, date]:
    """Latest stored bar date per code (empty dict = nothing stored yet)."""
    rows = conn.execute(
        f"SELECT code, MAX(date) AS max_date FROM {TARGET_TABLE} "
        f"WHERE code LIKE %s GROUP BY code",
        (FAMILY_CODE_LIKE,),
    ).fetchall()
    return {str(code): max_date for code, max_date in rows}


def plan_fetches(
    instruments: List[A50Instrument],
    max_dates: Dict[str, date],
    force: bool,
    today: date,
) -> List[Tuple[A50Instrument, Optional[date]]]:
    """(instrument, beg) pairs to fetch; beg=None means full history.

    The continuous series always re-fetches full (re-splice semantics);
    contracts fetch the tail since their last stored date, or nothing when
    already current. ``force`` re-fetches everything full.
    """
    plan: List[Tuple[A50Instrument, Optional[date]]] = []
    for inst in instruments:
        if force or inst.is_continuous or inst.code not in max_dates:
            plan.append((inst, None))
            continue
        beg = next_day(max_dates[inst.code])
        if beg > today:
            logger.info("  %s up to date (last %s), skipping",
                        inst.code, max_dates[inst.code])
            continue
        plan.append((inst, beg))
    return plan


def run_scheduled(args: argparse.Namespace) -> None:
    """One incremental fetch cycle (single-instance lock held by caller)."""
    t0 = time.time()
    today: date = date.today()
    out_dir: Path = resolve_out_dir(__file__, OUT_DIRNAME, args.out_root)

    print_build_header(
        "DOWNLOAD SGX A50 FUTURES DAILY  ·  Eastmoney global futures (104)",
        **{
            "Kline URL": "push2his.eastmoney.com (curl_cffi + AntiBotProxy)",
            "Tables": f"{IDENTITY_TABLE} + {TARGET_TABLE} (codes {FAMILY_CODE_LIKE})",
            "CSV dir": str(out_dir),
            "Scope": args.codes or "(whole enumerated chain)",
            "Force": args.force,
        },
    )

    # ------------------------------------------------------------------
    # Step 1: enumerate the chain (falls back to the continuous code plus
    # whatever is already stored when the enumeration request fails)
    # ------------------------------------------------------------------
    logger.info("[1/4] Enumerating the A50 chain ...")
    instruments: List[A50Instrument] = enumerate_a50_codes(sleep_sec=args.sleep)
    if args.codes:
        wanted: Set[str] = {c.strip() for c in args.codes.split(",") if c.strip()}
        instruments = [inst for inst in instruments if inst.code in wanted]
    if not instruments:
        logger.warning("  Enumeration empty — falling back to stored codes")
        conn = get_db_connection()
        stored: List[str] = [
            str(r[0]) for r in conn.execute(
                f"SELECT DISTINCT code FROM {TARGET_TABLE} WHERE code LIKE %s",
                (FAMILY_CODE_LIKE,),
            ).fetchall()
        ]
        conn.close()
        names: Set[str] = {CONTINUOUS_CODE, *stored}
        instruments = [A50Instrument(c, c, c == CONTINUOUS_CODE) for c in sorted(names)]

    # ------------------------------------------------------------------
    # Step 2: DB state (latest date per code) and the fetch plan
    # ------------------------------------------------------------------
    logger.info("[2/4] Checking stored dates ...")
    conn = get_db_connection()
    if args.force:
        deleted_stats = conn.execute(
            f"DELETE FROM {TARGET_TABLE} WHERE code LIKE %s",
            (FAMILY_CODE_LIKE,),
        ).rowcount
        deleted_identity = conn.execute(
            f"DELETE FROM {IDENTITY_TABLE} WHERE code LIKE %s",
            (FAMILY_CODE_LIKE,),
        ).rowcount
        conn.commit()
        logger.info("  --force: purged %d basic_stats + %d identity rows (%s)",
                    deleted_stats, deleted_identity, FAMILY_CODE_LIKE)
    max_dates: Dict[str, date] = get_max_dates(conn)
    logger.info("  %d codes already stored", len(max_dates))

    logger.info("[3/4] Planning fetches ...")
    plan = plan_fetches(instruments, max_dates, args.force, today)
    full = sum(1 for _, beg in plan if beg is None)
    logger.info("  %d codes to fetch (%d full, %d tail)",
                len(plan), full, len(plan) - full)
    if not plan:
        logger.info("Nothing to fetch.")
        conn.close()
        print_wall_time(t0)
        return

    # ------------------------------------------------------------------
    # Step 3: fetch + upsert, one code at a time; identity twins first
    # (FK target), then the basic_stats rows. The CSV audit drop in
    # CFFEX format is written afterwards from all rows of this run.
    # ------------------------------------------------------------------
    logger.info("[4/4] Fetching and upserting ...")
    fetched_rows = 0
    run_rows: List[Dict[str, object]] = []
    failed_codes: List[str] = []
    for i, (inst, beg) in enumerate(plan):
        rows: List[Dict[str, object]] = fetch_daily_rows(
            inst.code, beg=beg, sleep_sec=args.sleep,
        )
        if not rows and beg is None:
            # Full fetch returned nothing: genuinely unknown/empty code
            logger.warning("  %s: no bars returned", inst.code)
            failed_codes.append(inst.code)
            continue
        if rows:
            inserted: int = bulk_upsert(
                conn, IDENTITY_TABLE, identity_rows_for(rows),
                key_columns=["code", "date"],
            )
            db_rows = [
                {
                    "date": row["date"],
                    "code": row["code"],
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "change": row["change"],
                    "change_pct": row["change_pct"],
                    "trading_shares": row["volume"],
                    # amount is EM-reported 0 for global futures and there
                    # is no settlement/OI in klines — those stay NULL
                }
                for row in rows  # amount/amplitude exist only in the CFFEX-format CSV
            ]
            inserted += bulk_upsert(
                conn, TARGET_TABLE, db_rows, key_columns=["code", "date"],
            )
            fetched_rows += inserted
            run_rows.extend(rows)
        logger.info("[%d/%d] %s: %d rows upserted",
                    i + 1, len(plan), inst.code, len(rows))

    conn.close()

    if run_rows:
        logger.info("Writing CFFEX-format CSV audit files ...")
        write_cffex_style_files(run_rows, out_dir)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    logger.info("Done: %d rows upserted across %d codes%s",
                fetched_rows, len(plan),
                f", failed: {failed_codes}" if failed_codes else "")
    print_wall_time(t0)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download SGX FTSE China A50 futures daily bars "
                    "(Eastmoney global-futures klines, market 104).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help=f"Purge the family's rows ({FAMILY_CODE_LIKE}) from the shared "
             "futures tables and re-fetch the whole chain in full.",
    )
    parser.add_argument(
        "--codes", default=None,
        help="Comma-separated code scope for this run (e.g. CN00Y,CN26V). "
             "Default: the whole enumerated chain.",
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
            run_scheduled(args)
    except RuntimeError as exc:
        # Overlapping invocations (e.g. a slow run under the block versus the
        # next scheduled start): the active run wins, this one no-ops.
        logger.warning("%s — skipping this invocation", exc)


if __name__ == "__main__":
    main()
