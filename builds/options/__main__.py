"""builds/options/__main__.py — Build both SZSE and CFFEX options data.

Orchestrates the two sub-builders sequentially:
  1. builds.options.szse  — SZSE ETF options (native ETF codes, 1599xx)
  2. builds.options.cffex — CFFEX index options (IO/HO/MO/CO → index codes)

Both write into the same 7 options_* tables. SZSE and CFFEX are
separated by underlying_code space: SZSE keeps native ETF codes
(e.g. 159919), CFFEX uses index codes (e.g. 000300), distinguished
further by underlying_target_type ('ETF' vs 'INDEX').

With --force: truncates all 7 tables ONCE upfront, then runs both
builders in normal (non-force) mode so they repopulate from scratch.

Usage:
  python -m builds.options
  python -m builds.options --start-date 2026-07-01 --end-date 2026-07-31
  python -m builds.options --force
"""
from __future__ import annotations

import asyncio
import sys
import time

from _common.build_commons import (
    add_common_build_args,
    get_db_or_exit,
    truncate_table_async,
    print_build_header,
    print_wall_time,
    TODAY_STR,
    setup_utf8_stdout,
)

setup_utf8_stdout()

# Re-export for argparse in the parent shell
import argparse
_ap = argparse.ArgumentParser(
    description="Build SZSE + CFFEX options data (missing dates only)."
)
add_common_build_args(_ap)
_args = _ap.parse_args()


async def _truncate_all_tables() -> None:
    """Truncate all 7 options_* tables (called once before force rebuild)."""
    tables = (
        "stats.options_aggregate",
        "stats.options_volume_oi",
        "stats.options_greeks",
        "stats.options_settlement",
        "stats.options_strike",
        "stats.options_terms",
        "stats.options_identity",
    )
    conn = await get_db_or_exit()
    try:
        for tbl in tables:
            await truncate_table_async(conn, tbl)
    finally:
        await conn.close()


def _build_child_argv(start_date: str | None, end_date: str | None) -> list[str]:
    """Build argv list for a child builder (without --force)."""
    argv = [sys.argv[0]]
    if start_date:
        argv += ["--start-date", start_date]
    if end_date:
        argv += ["--end-date", end_date]
    return argv


async def main() -> None:
    t0 = time.time()

    print_build_header(
        "BUILD OPTIONS (SZSE + CFFEX)  ·  missing-data-only → DATABASE",
        **{
            "Date range":   f"{_args.start_date or '(all)'} → {_args.end_date or '(all)'}",
            "Force rebuild": str(_args.force),
            "Today":        TODAY_STR,
        }
    )

    # --force: truncate all tables ONCE before running both builders
    if _args.force:
        print("\n[FORCE] Truncating all 7 options_* tables …", flush=True)
        await _truncate_all_tables()
        print("    Done.", flush=True)

    child_argv = _build_child_argv(_args.start_date, _args.end_date)

    # ---- 1. SZSE ETF options ----
    print("\n" + "=" * 60, flush=True)
    print("PHASE 1: SZSE ETF OPTIONS", flush=True)
    print("=" * 60, flush=True)
    _orig_argv = sys.argv
    sys.argv = child_argv
    try:
        from builds.options.szse.__main__ import main as szse_main
        await szse_main()
    except SystemExit as e:
        if e.code != 0:
            print(f"[ERROR] SZSE builder exited with code {e.code}", flush=True)
    finally:
        sys.argv = _orig_argv

    # ---- 2. CFFEX index options ----
    print("\n" + "=" * 60, flush=True)
    print("PHASE 2: CFFEX INDEX OPTIONS", flush=True)
    print("=" * 60, flush=True)
    sys.argv = child_argv
    try:
        from builds.options.cffex.__main__ import main as cffex_main
        await cffex_main()
    except SystemExit as e:
        if e.code != 0:
            print(f"[ERROR] CFFEX builder exited with code {e.code}", flush=True)
    finally:
        sys.argv = _orig_argv

    print("\n" + "=" * 60, flush=True)
    print("OPTIONS BUILD COMPLETE (SZSE + CFFEX)", flush=True)
    print("=" * 60, flush=True)
    print_wall_time(t0)


if __name__ == "__main__":
    asyncio.run(main())
