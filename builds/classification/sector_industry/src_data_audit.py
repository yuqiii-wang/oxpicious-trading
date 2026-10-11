"""monitor.src_data_audit upsert — the source-data coverage audit.

The per-code coverage stats (n_days / first_date / last_date) the
classification build computes for the sec_classification upsert are ALSO
the audit of the raw source datasets, so they are upserted here — one row
per (sec_type, code) — for the monitoring UI.  This is where those fields
live since their 2026-10-10 migration out of the classification JSON cache
(see sector_industry.json_io): the shards hold static classification only.

Rows come straight from the in-memory state the leaves already built:
    index → state["indices"]  (coverage fetched fresh from
            stats.index_identity each run by index.db.fetch_index_meta;
            synthetic DUMMY_* indices are skipped — no source data)
    stock → state["stocks"]   (per-code coverage from
            stats.stock_identity; a stock's rows all share one coverage,
            deduped to the first occurrence per code)

ETF rows are written separately by the ETF OHLCV build
(builds.etf.pipeline.quality) — the classification build has no ETF
coverage.  stats.sec_classification keeps its denormalized copies of the
three columns; this table adds no new numbers, just the audit view.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List

from _common.build_commons import bulk_upsert_async, truncate_table_async
from _common.labels import SEC_TYPE_CODE

from builds.classification.sector_industry.catalog import _parse_date

import logging
logger = logging.getLogger(__name__)


async def upsert_src_data_audit(
    conn,
    state: Dict[str, Any],
    verbose: bool = True,
    force: bool = False,
) -> None:
    """Upsert index + stock coverage rows into monitor.src_data_audit."""
    if force:
        # Mirror the sec_classification force semantics: the audit is a full
        # snapshot of what this build computed, so drop stale rows entirely.
        await truncate_table_async(conn, "monitor.src_data_audit")

    audit_rows: List[Dict[str, Any]] = []
    now = datetime.datetime.now(datetime.timezone.utc)
    for code, v in state["indices"].items():
        if v.get("is_dummy", False):
            continue
        audit_rows.append({
            "sec_type": SEC_TYPE_CODE["index"],
            "code": code,
            "exchange": v.get("exchange") or None,
            "board": None,  # board is a stock_identity concept — indices have none
            "n_days": v.get("n_days", 0),
            "first_date": _parse_date(v.get("first_date")),
            "last_date": _parse_date(v.get("last_date")),
            "updated_at": now,
        })
    seen_stocks: set = set()
    for v in state["stocks"]:
        stock_code = v["code"]
        if stock_code in seen_stocks:
            continue  # one row per (stock, qualifying index) — coverage is per code
        seen_stocks.add(stock_code)
        audit_rows.append({
            "sec_type": SEC_TYPE_CODE["stock"],
            "code": stock_code,
            "exchange": v.get("exchange") or None,
            "board": v.get("board"),
            "n_days": v.get("n_days", 0),
            "first_date": _parse_date(v.get("first_date")),
            "last_date": _parse_date(v.get("last_date")),
            "updated_at": now,
        })
    if audit_rows:
        inserted = await bulk_upsert_async(
            conn, "monitor.src_data_audit", audit_rows,
            ["sec_type", "code"])
        if verbose:
            n_index = sum(1 for r in audit_rows
                          if r["sec_type"] == SEC_TYPE_CODE["index"])
            logger.info(f"    [DB] Upserted {inserted:,} source-data audit rows "
                  f"into monitor.src_data_audit "
                  f"({n_index:,} index, {inserted - n_index:,} stock)")
