"""builds.index — Unified index build orchestrator.

Runs three sequential phases:

  1. Index composition (CSI + SZSE) → stats.sec_composition
  2. Index baseline (CSIndex daily OHLCV + PE + MAs)
  3. Index exts (stats.index_exts + stats.etf_trading_amt +
     stats.exchange_trading_amt + stats.sec_similars)

Composition must run before baseline because baseline's close-price
estimation relies on index composition shared weights. Exts must run
after baseline: exchange_trading_amt is driven by stats.index_basic_stats
dates (produced by baseline), and sec_similars is driven by
sec_composition snapshot dates (produced by phase 1).

Usage:
  python -m builds.index
  python -m builds.index --force
  python -m builds.index --date 2026-08-14   (force single-date rebuild)
  python -m builds.index --start-date 2024-01-01 --end-date 2026-07-23
  python -m builds.index --code 000300               (single-index test filter)
"""
from __future__ import annotations


# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

import argparse
import asyncio
import sys
import time

from _common.build_commons import (
    setup_utf8_stdout,
    add_common_build_args,
    print_build_header,
    print_wall_time,
    TODAY_STR,
    enforce_date_force_exclusion,
    parse_date_arg,
    forced_date_scope,
)

setup_utf8_stdout()

from builds._commons.code_filter import add_code_arg, normalize_code
from builds.index.composition import (
    build_index_composition_rows,
    build_szse_index_composition_rows,
    available_snapshot_dates,
)
from builds._commons.paths import INDEX_COMP_DIR, SZSE_INDEX_COMP_DIR
from builds.index._index_exts import build_index_exts
from builds.index._exchange_trading_amt import build_exchange_trading_amt
from builds.index._sec_similars import build_sec_similars


async def _run_composition(force: bool, code_filter: str | None = None,
                           forced_date=None) -> None:
    """Run index composition build (CSI + SZSE) → stats.sec_composition.

    Date-check fast-path: filenames are checked first to identify missing
    (code, snapshot_date) pairs before reading any CSV content. With
    code_filter, only that index's files/rows are processed. With
    forced_date (--date mode), the missing-pair skip is bypassed and only
    that snapshot date's rows are (re)built via the upsert path — the
    force-mode DELETE of sec_composition never runs.
    """
    from _common.build_commons import get_db_or_exit, copy_or_upsert_split_async

    if forced_date is not None:
        # --date availability gate: the forced snapshot date must exist
        # among the composition CSV filenames (CSI + SZSE union), else
        # exits(1) before any DB work.
        forced_date_scope(
            available_snapshot_dates(code_filter),
            forced_date,
            source_label="composition CSV filenames",
        )

    conn = await get_db_or_exit()
    try:
        print("\n    Building CSI index composition rows …", flush=True)
        index_comp_rows = await build_index_composition_rows(
            conn=conn, force=force, code_filter=code_filter,
            forced_date=forced_date,
        )

        print("\n    Building SZSE index composition rows …", flush=True)
        szse_index_comp_rows = await build_szse_index_composition_rows(
            conn=conn, force=force, code_filter=code_filter,
            forced_date=forced_date,
        )

        all_rows = index_comp_rows + szse_index_comp_rows
        print(f"\n    → total: {len(all_rows):,} index composition rows "
              f"({len(index_comp_rows):,} CSI + {len(szse_index_comp_rows):,} SZSE)", flush=True)

        if force:
            if code_filter:
                print(f"    [DB] Force mode for code {code_filter}: deleting existing index composition rows", flush=True)
                await conn.execute(
                    "DELETE FROM stats.sec_composition WHERE source_type = 'index' AND code = $1",
                    code_filter,
                )
            else:
                print("    [DB] Force mode: deleting existing index composition rows", flush=True)
                await conn.execute(
                    "DELETE FROM stats.sec_composition WHERE source_type = 'index'"
                )
        else:
            # Date-check fast-path already filtered CSVs before reading,
            # so only missing dates were processed. Skip the secondary
            # row-level filter — all remaining rows are genuinely new.
            pass

        if all_rows:
            n_copied, n_upserted = await copy_or_upsert_split_async(
                conn, "stats.sec_composition", all_rows,
                ["code", "snapshot_date", "rank"],
                date_column="snapshot_date",
            )
            total = n_copied + n_upserted
            via = "COPY" if n_copied > 0 and n_upserted == 0 else \
                  f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
                  "upsert"
            print(f"    [DB] Inserted {total:,} rows into stats.sec_composition via {via}", flush=True)
        else:
            print("    [DB] No new rows to insert", flush=True)
    finally:
        await conn.close()


async def main():
    ap = argparse.ArgumentParser(
        description="Build index composition (CSI+SZSE) → baseline (CSIndex daily) "
                    "→ exts (index_exts + etf/exchange trading amt + sec similars)."
    )
    add_common_build_args(ap)
    add_code_arg(ap)
    args = ap.parse_args()

    # --date mode: mutual exclusion + parse (SystemExit 2 on bad input).
    enforce_date_force_exclusion(args)
    forced = parse_date_arg(args.date)
    if forced is not None:
        print(f"[DATE MODE] Forced single-date build: {forced}", flush=True)

    # Index codes in the DB are bare 6-digit codes (e.g. 000300) — strip
    # the exchange suffix normalize_code may have appended.
    code_filter = normalize_code(args.code)
    if code_filter:
        code_filter = code_filter.split(".")[0]

    t0 = time.time()
    print_build_header(
        "BUILD INDEX  ·  composition (CSI+SZSE) → baseline (CSIndex daily) "
        "→ exts (ETF/exchange amt + sec similars)",
        **{
            "CSI comp dir":  INDEX_COMP_DIR,
            "SZSE comp dir": SZSE_INDEX_COMP_DIR,
            "Date range":    f"{args.start_date or '(all)'} → {args.end_date or '(all)'}",
            "Forced date":   str(forced) if forced else "(none)",
            "Code filter":   code_filter or "(none — all indices)",
            "Today":         TODAY_STR,
        }
    )
    if code_filter:
        print(f"    [CODE FILTER] Restricting build to single index: {code_filter}", flush=True)

    # ---- Phase 1: Index composition (CSI + SZSE) ----------------------
    print("\n" + "=" * 78)
    print("  PHASE 1: INDEX COMPOSITION (CSI + SZSE)")
    print("=" * 78)
    t1 = time.time()
    await _run_composition(force=args.force, code_filter=code_filter,
                           forced_date=forced)
    print(f"\n  Composition phase done ({int(time.time() - t1)}s)", flush=True)

    # ---- Phase 2: Index baseline (CSIndex daily) ----------------------
    print("\n" + "=" * 78)
    print("  PHASE 2: INDEX BASELINE (CSIndex daily OHLCV + PE + MAs)")
    print("=" * 78)
    t2 = time.time()

    # baseline.main uses sys.argv for argparse; set it and call directly.
    # --refresh-estimated-days: nightly self-heal — rebuild recent daily
    # rows gap-filled as ESTIMATED by a build that raced ahead of the
    # EOD CSV publish; idempotent when the CSVs already agree. Skipped in
    # --date mode: single-date scope only (the forced date is always
    # rebuilt regardless of its row state).
    original_argv = sys.argv.copy()
    try:
        sys.argv = [
            "builds.index.baseline",
            *(["--start-date", args.start_date] if args.start_date else []),
            *(["--end-date", args.end_date] if args.end_date else []),
            *(["--date", args.date] if args.date else []),
            *(["--force"] if args.force else []),
            *([] if (args.force or args.date) else ["--refresh-estimated-days", "10"]),
            *(["--code", code_filter] if code_filter else []),
        ]
        from builds.index.baseline import main as baseline_main
        await baseline_main()
    finally:
        sys.argv = original_argv

    print(f"\n  Baseline phase done ({int(time.time() - t2)}s)", flush=True)

    # ---- Phase 3: Index exts (index_exts + etf/exchange amt + similars) --
    # Each step has its own missing-date skip check, so in incremental mode
    # each only (re)computes the dates it is missing. The steps are
    # independent (different sources, different date grains) and do not
    # take a code filter.
    print("\n" + "=" * 78)
    print("  PHASE 3: INDEX EXTS (ETF metrics + exchange trading amt + sec similars)")
    print("=" * 78)
    t3 = time.time()

    from _common.build_commons import get_db_connection_async
    conn = await get_db_connection_async()
    try:
        # Step A: per-(date, index) + per-(date, industry) ETF metrics.
        # Driven by etf_liquidity_margin. --date mode recomputes the forced
        # date even when already present (upsert refresh, no truncation).
        await build_index_exts(conn, force=args.force, forced_date=forced)

        # Step B: per-(date, exchange) trading amount proxied by a
        # representative broad-market index per exchange (SZ->399001,
        # SS->000001). Driven by index_basic_stats (phase 2 output).
        await build_exchange_trading_amt(conn, force=args.force, forced_date=forced)

        # Step C: per-(composition-date, code) top-5 similar codes +
        # similar/dissimilar industries. Driven by sec_composition
        # snapshot dates (phase 1 output).
        await build_sec_similars(conn, force=args.force, forced_date=forced)
    finally:
        try:
            await conn.close()
        except Exception:
            pass

    print(f"\n  Exts phase done ({int(time.time() - t3)}s)", flush=True)
    print_wall_time(t0)


if __name__ == "__main__":
    from _common.post_check import post_check
    try:
        asyncio.run(main())
    finally:
        post_check()
