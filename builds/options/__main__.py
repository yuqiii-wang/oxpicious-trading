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
With --force --code <underlying>: only that underlying's rows are
deleted instead of truncating.

With --date YYYY-MM-DD: both builders are scoped to that single date
and the DB missing-date skip is bypassed — the date is always
(re)processed and rows already in the DB are refreshed through the
upsert write paths (no truncation, no deletes). Mutually exclusive
with --force.

Usage:
  python -m builds.options
  python -m builds.options --start-date 2026-07-01 --end-date 2026-07-31
  python -m builds.options --force
  python -m builds.options --code 159915              (single-underlying test filter)
  python -m builds.options --date 2026-08-28          (force single-date rebuild, no DB skip)
"""
from __future__ import annotations


# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()
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
    enforce_date_force_exclusion,
    parse_date_arg,
)

setup_utf8_stdout()

from builds._commons.code_filter import add_code_arg, normalize_code

# Re-export for argparse in the parent shell
import argparse
_ap = argparse.ArgumentParser(
    description="Build SZSE + CFFEX options data (missing dates only)."
)
add_common_build_args(_ap)
add_code_arg(_ap)
_args = _ap.parse_args()

# --date / --force are mutually exclusive; parse the forced single date.
# When set, the date also supersedes any explicit --start/--end range so
# downstream discovery/loading is scoped to this single day.
enforce_date_force_exclusion(_args)
forced_date = parse_date_arg(_args.date)
if forced_date:
    _args.start_date = forced_date.isoformat()
    _args.end_date = forced_date.isoformat()

# Normalized code filter (e.g. 159915 → 159915.SZ). Sub-builds compare
# against the BARE underlying_code column, so forward the bare form.
_code_filter_raw = normalize_code(_args.code)
code_filter = _code_filter_raw.split(".")[0] if _code_filter_raw else None


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


async def _purge_for_force(underlying: str | None) -> None:
    """--force: truncate all 7 options_* tables, or delete only the
    --code underlying's rows when a code filter is set."""
    if underlying:
        from builds.options.tables import delete_underlying_rows_async
        conn = await get_db_or_exit()
        try:
            n = await delete_underlying_rows_async(conn, underlying)
            print(f"    Deleted {n:,} (date, contract_code) rows of underlying {underlying}", flush=True)
        finally:
            await conn.close()
    else:
        await _truncate_all_tables()


def _build_child_argv(start_date: str | None, end_date: str | None,
                      child_code: str | None = None,
                      forced_date: str | None = None) -> list[str]:
    """Build argv list for a child builder (without --force)."""
    argv = [sys.argv[0]]
    if start_date:
        argv += ["--start-date", start_date]
    if end_date:
        argv += ["--end-date", end_date]
    if child_code:
        argv += ["--code", child_code]
    if forced_date:
        argv += ["--date", forced_date]
    return argv


async def main() -> None:
    t0 = time.time()

    print_build_header(
        "BUILD OPTIONS (SZSE + CFFEX)  ·  missing-data-only → DATABASE",
        **{
            "Date range":   f"{_args.start_date or '(all)'} → {_args.end_date or '(all)'}",
            "Force rebuild": str(_args.force),
            "Code filter":  code_filter or "(none — all underlyings)",
            "Today":        TODAY_STR,
        }
    )
    if code_filter:
        print(f"    [CODE FILTER] Restricting build to single underlying: {code_filter}", flush=True)
    if forced_date:
        print(f"[DATE MODE] Forced single-date build: {forced_date}", flush=True)

    # --force: purge tables ONCE before running both builders
    if _args.force:
        if code_filter:
            print(f"\n[FORCE] Deleting rows of underlying {code_filter} from the 7 options_* tables …", flush=True)
        else:
            print("\n[FORCE] Truncating all 7 options_* tables …", flush=True)
        await _purge_for_force(code_filter)
        print("    Done.", flush=True)

    child_argv = _build_child_argv(
        _args.start_date, _args.end_date, code_filter,
        forced_date.isoformat() if forced_date else None,
    )

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
    from _common.post_check import post_check
    try:
        asyncio.run(main())
    finally:
        post_check()
