"""DB upsert functions for builds.sec_info.

Writes four targets:
  1. stats.sec_owners      — truncate + rebuild from sec_owners.json (moved
                             here from builds.classification.sector_industry.owners)
  2. stats.sec_info        — latest-value snapshot per code (missing-data:
                             only upsert codes whose new report_date is newer
                             than the stored last_report_date, or new codes)
  3. stats.sec_reports     — one row per (code, report_date); missing-data
                             (skip existing pairs unless --force)
  4. stats.sec_composition — top10_holdings injection (source_type='etf',
                             snapshot_date=report_date). Always missing-data:
                             skip (code, snapshot_date) pairs already present
                             so builds.etf full-composition snapshots are never
                             overwritten by the smaller top-10 source.

All upserts use the shared bulk_upsert_async / truncate_table_async helpers
from _common.build_commons.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List

from _common.build_commons import (
    bulk_upsert_async, truncate_table_async, rec_col,
)


# ============================================================================
# sec_owners  (moved from builds.classification.sector_industry.owners)
# ============================================================================
async def upsert_owners(
    conn,
    owners: List[Dict[str, Any]],
    verbose: bool = True,
) -> None:
    """Rebuild stats.sec_owners from the curated sec_owners.json each run.

    Truncate + rebuild keeps the table in sync with hand edits (stale entries
    removed when the JSON is edited).  Safe to run independently of
    builds.classification — sec_classification.owner_id is a logical (non-FK)
    reference, so the table can be rebuilt at any time.
    """
    if not owners:
        if verbose:
            print("    [DB] No owners to load — skipping stats.sec_owners",
                  flush=True)
        return
    owner_rows = [{
        "owner_id": o["owner_id"],
        "name": o.get("name", ""),
        "type": o.get("type"),
        "aliases": list(o.get("aliases", [])),
        "full_names": list(o.get("full_names", [])),
    } for o in owners]
    await truncate_table_async(conn, "stats.sec_owners")
    inserted = await bulk_upsert_async(
        conn, "stats.sec_owners", owner_rows, ["owner_id"])
    if verbose:
        print(f"    [DB] Inserted {inserted:,} owner rows into "
              f"stats.sec_owners", flush=True)


# ============================================================================
# sec_info  (latest-value snapshot per code)
# ============================================================================
# Columns written by this upsert (excludes `code` PK + `last_report_date` which
# is set to the report_date of the row being written).
_SEC_INFO_COLS = [
    "fund_main_code", "name", "exchange_abbreviation", "operation_method",
    "contract_effective_date", "benchmark", "risk_return_characteristics",
    "manager", "custodian",
]


async def fetch_existing_sec_info(conn) -> Dict[str, datetime.date]:
    """Return {code: last_report_date} for all rows currently in sec_info."""
    rows = await conn.fetch("SELECT code, last_report_date FROM stats.sec_info")
    return dict(zip(rec_col(rows, "code"), rec_col(rows, "last_report_date")))


def build_sec_info_rows(
    latest_per_code: Dict[str, Dict[str, Any]],
    existing: Dict[str, datetime.date],
    force: bool,
) -> List[Dict[str, Any]]:
    """Filter to sec_info rows worth writing.

    A code is written when:
      · force=True                          → always (truncate happens first), OR
      · code not in existing                → new fund, OR
      · latest report_date > existing last_report_date → newer report available.
    """
    out: List[Dict[str, Any]] = []
    for code, info in latest_per_code.items():
        new_date = info["report_date"]
        if not force and code in existing and existing[code] is not None and new_date <= existing[code]:
            continue
        row: Dict[str, Any] = {"code": code, "last_report_date": new_date}
        for c in _SEC_INFO_COLS:
            row[c] = info.get(c)
        out.append(row)
    return out


async def upsert_sec_info(conn, rows: List[Dict[str, Any]], force: bool,
                          verbose: bool = True) -> int:
    """Upsert sec_info rows.  Truncates first when force=True."""
    if not rows and not force:
        if verbose:
            print("    [DB] No new/updated sec_info rows — skipping",
                  flush=True)
        return 0
    if force:
        await truncate_table_async(conn, "stats.sec_info")
    if not rows:
        if verbose:
            print("    [DB] No sec_info rows to insert", flush=True)
        return 0
    inserted = await bulk_upsert_async(conn, "stats.sec_info", rows, ["code"])
    if verbose:
        print(f"    [DB] Upserted {inserted:,} rows into stats.sec_info "
              f"({'force' if force else 'incremental'})", flush=True)
    return inserted


# ============================================================================
# sec_reports  (one row per code + report quarter)
# ============================================================================
async def fetch_existing_sec_reports(conn) -> set:
    """Return {(code, report_date)} pairs already in sec_reports."""
    rows = await conn.fetch("SELECT code, report_date FROM stats.sec_reports")
    return set(zip(rec_col(rows, "code"), rec_col(rows, "report_date")))


def build_sec_reports_rows(
    reports: List[Dict[str, Any]],
    existing: set,
    force: bool,
) -> List[Dict[str, Any]]:
    """Filter to sec_reports rows worth writing (missing-data unless force)."""
    out: List[Dict[str, Any]] = []
    for r in reports:
        key = (r["code"], r["report_date"])
        if not force and key in existing:
            continue
        out.append(r)
    return out


async def upsert_sec_reports(conn, rows: List[Dict[str, Any]], force: bool,
                             verbose: bool = True) -> int:
    """Upsert sec_reports rows.  Truncates first when force=True."""
    if not rows and not force:
        if verbose:
            print("    [DB] No new sec_reports rows — skipping", flush=True)
        return 0
    if force:
        await truncate_table_async(conn, "stats.sec_reports")
    if not rows:
        if verbose:
            print("    [DB] No sec_reports rows to insert", flush=True)
        return 0
    inserted = await bulk_upsert_async(
        conn, "stats.sec_reports", rows, ["code", "report_date"])
    if verbose:
        print(f"    [DB] Upserted {inserted:,} rows into stats.sec_reports "
              f"({'force' if force else 'incremental'})", flush=True)
    return inserted


# ============================================================================
# sec_composition  (top10_holdings injection — always missing-data)
# ============================================================================
async def fetch_existing_composition_keys(conn) -> set:
    """Return {(code, snapshot_date)} pairs already in sec_composition."""
    rows = await conn.fetch(
        "SELECT DISTINCT code, snapshot_date FROM stats.sec_composition")
    return set(zip(rec_col(rows, "code"), rec_col(rows, "snapshot_date")))


def build_composition_rows(
    top10_snapshots: List[Dict[str, Any]],
    existing: set,
) -> List[Dict[str, Any]]:
    """Build sec_composition rows from top10_holdings snapshots.

    Skips (code, snapshot_date) pairs already present so builds.etf full-
    composition snapshots are never overwritten by the smaller top-10 source.
    Assigns a running rank per snapshot (1 = first row in the CSV).
    """
    out: List[Dict[str, Any]] = []
    for snap in top10_snapshots:
        etf_code = snap["etf_code"]
        snap_date = snap["snapshot_date"]
        if (etf_code, snap_date) in existing:
            continue
        for rank, h in enumerate(snap["holdings"], start=1):
            out.append({
                "snapshot_date": snap_date,
                "code": etf_code,
                "source_type": "etf",
                "rank": rank,
                "stock_code": h["stock_code"],
                "stock_name": h["stock_name"],
                "weight_pct": h["weight_pct"],
            })
    return out


async def inject_top10_composition(conn, rows: List[Dict[str, Any]],
                                   verbose: bool = True) -> int:
    """Insert top10-holdings rows into sec_composition (missing snapshots only)."""
    if not rows:
        if verbose:
            print("    [DB] No new sec_composition rows from top10_holdings — skipping",
                  flush=True)
        return 0
    inserted = await bulk_upsert_async(
        conn, "stats.sec_composition", rows,
        ["code", "snapshot_date", "rank"])
    if verbose:
        print(f"    [DB] Inserted {inserted:,} top10-holdings rows into "
              f"stats.sec_composition", flush=True)
    return inserted
