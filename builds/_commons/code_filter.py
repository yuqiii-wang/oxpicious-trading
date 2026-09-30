"""Shared --code single-security filter helpers for build scripts.

Used by builds/etf, options, index, futures, stock to restrict a build
to one security code (development/testing aid — not for full runs).
"""
from __future__ import annotations

import argparse
from datetime import date
from typing import Iterable, Set

from _common.db_commons import _parse_table_name


def add_code_arg(parser: argparse.ArgumentParser) -> None:
    """Add the --code argument (single security filter, e.g. 000001.SZ)."""
    parser.add_argument(
        "--code", default=None,
        help="Filter to a single security code (e.g. 000001.SZ) for testing",
    )


def normalize_code(code: str | None) -> str | None:
    """Normalize --code: strip whitespace and append an exchange suffix to
    bare 6-digit STOCK codes (inferred from the leading digit).

    ETF/fund codes (leading "5" — SSE 5x fund families incl. OTC 519xxx;
    "15"/"16"/"18" — SZSE fund families) are BARE: returned unchanged,
    any suffix stripped (2026-09-30 convention — ETF/fund codes carry no
    exchange suffix; stocks keep theirs).

    Options codes (e.g. 10008657) are 8+ digits and are returned unchanged.
    """
    if not code:
        return None
    c = code.strip()
    base = c.partition(".")[0]
    if base.startswith(("5", "15", "16", "18")):
        return base
    if "." not in c:
        if c.startswith("6"):
            c += ".SS"
        elif c.startswith(("0", "1", "3")):
            c += ".SZ"
        elif c.startswith(("4", "8", "9")):
            c += ".BJ"
        else:
            c += ".SZ"
    return c


async def find_missing_dates_code_aware(
    conn,
    table: str,
    source_dates: Iterable[date],
    code: str,
) -> Set[date]:
    """Return subset of source_dates not present in `table` for a specific code.

    Mirrors _common.pre_check_and_load.missing_dates.find_missing_dates()
    but adds a `WHERE code = $code` filter. Only used when --code is set.
    """
    source_set = set(source_dates)
    if not source_set:
        return set()
    schema, tbl = _parse_table_name(table)
    from_clause = f'"{schema}"."{tbl}"' if schema else f'"{tbl}"'
    existing_rows = await conn.fetch(
        f'SELECT DISTINCT date FROM {from_clause} WHERE code = $1',
        code,
    )
    existing_dates = {r["date"] for r in existing_rows}
    return source_set - existing_dates
