"""Entry point for live.live_signals.

Run via ``python -m live.live_signals --code 000300 [--signal-scheme analysis]``.

Live breach check of the threshold set for ONE code:

  1. Fetch the code's CURRENT intraday close — the latest
     stats.{sec_type}_intraday_5min bar (date, time, close). The
     sec_type is derived from whichever intraday table holds the code.
     NO intraday price → print ``404`` and exit 404.
  2. Load the code's ACTIVE signal configs (``is_active`` rows of
     analysis_signals.signals — all signal types / sub types of the
     resolved sec_type) and evaluate each against its
     signal_threshold (dispatched to per-signal-type evaluators under
     ``live_signals.analysis``):
       - mov_std: close vs band level (price space);
       - mov_rsi: current RSI (latest analysis.mov_ave_rsi row, per
         window) vs the top/bottom-1% threshold (indicator space — an
         RSI value is not a price; the record stores the RSI in
         ``signal``).
  3. Triggered configs are recorded in live.live_signals (one row per
     (code, sec_type, signal_type, signal_sub_type, date, time); PK
     upsert so re-running the same bar updates in place). Every
     config's evaluation (triggered or not) is printed.
  4. Upsert live.live_identity.

--signal-scheme analysis (default) | strategy — 'strategy' is reserved
for a future strategy.*-sourced threshold set and exits with code 2.

Exit codes: 0 = checked (breaches recorded or not), 2 = usage error /
unimplemented scheme, 404 = code has no intraday price.
"""
from __future__ import annotations


# resource pre-check — pure asyncpg DB I/O, no GPU needed
from _common.pre_check import pre_check

pre_check(require_gpu=False)
import argparse
import asyncio
import os
import sys
import time

# Ensure project root is on sys.path so ``_common`` is importable when run
# via ``python -m live.live_signals``.
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from _common.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    print_build_header,
    print_wall_time,
)
from _common.db_commons import (  # noqa: E402
    bulk_upsert_async,
)

setup_utf8_stdout()

from live.live_signals.analysis import AnalysisEvaluator  # noqa: E402
from live.live_signals.config import (  # noqa: E402
    INTRADAY_TABLES,
    LIVE_SIGNAL_PK,
    PIPELINE_DESCRIPTION,
    PIPELINE_NAME,
    SEC_TYPE_PROBE_ORDER,
    SIGNALS_TABLE,
    SIGNAL_SCHEMES,
)

EXIT_NOT_FOUND = 404
EXIT_USAGE = 2


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

async def _upsert_live_identity(conn) -> None:
    """Upsert the pipeline registration into live.live_identity."""
    await conn.execute(
        """
        INSERT INTO live.live_identity
            (name, detail_name, summary_name, last_run_datetime, description)
        VALUES ($1, $2, NULL, NOW(), $3)
        ON CONFLICT (name) DO UPDATE SET
            detail_name       = EXCLUDED.detail_name,
            summary_name      = EXCLUDED.summary_name,
            last_run_datetime = NOW(),
            description       = EXCLUDED.description
        """,
        PIPELINE_NAME,
        "live_signals",
        PIPELINE_DESCRIPTION,
    )


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

async def main() -> int:
    ap = argparse.ArgumentParser(
        description="Live breach check of the analysis_signals threshold "
                    "set: latest intraday close vs every active signal "
                    "config (mov_std close vs band; mov_rsi current RSI "
                    "vs top/bottom-1% threshold); triggered breaches are "
                    "recorded in live.live_signals. Either --code (one "
                    "code, sec_type probed, 404 exit when no intraday "
                    "price) or --sec-type (ALL codes with active signal "
                    "configs of the given sec_types; codes without "
                    "intraday price are skipped)."
    )
    ap.add_argument(
        "--signal-scheme", choices=SIGNAL_SCHEMES, default="analysis",
        help="Threshold source scheme (default: analysis).",
    )
    ap.add_argument(
        "--code", default=None,
        help="Single ticker to check (sec_type probed; exits 404 when "
             "it has no intraday price).",
    )
    ap.add_argument(
        "--sec-type", default=None,
        help="Comma-separated sec_types (e.g. 'index' or 'index,etf'): "
             "check EVERY code with active signal configs of these "
             "sec_types. Mutually exclusive with --code.",
    )
    args = ap.parse_args()

    if args.code and args.sec_type:
        print("  [ERROR] --code and --sec-type are mutually exclusive.",
              flush=True)
        return EXIT_USAGE
    if not args.code and not args.sec_type:
        print("  [ERROR] one of --code / --sec-type is required.",
              flush=True)
        return EXIT_USAGE
    if args.signal_scheme == "strategy":
        print("  [ERROR] --signal-scheme strategy is not implemented yet; "
              "only 'analysis' is available.", flush=True)
        return EXIT_USAGE

    sec_types: list[str] = []
    if args.sec_type:
        sec_types = [
            s.strip() for s in args.sec_type.split(",") if s.strip()
        ]
        bad = [s for s in sec_types if s not in INTRADAY_TABLES]
        if bad:
            print(f"  [ERROR] unknown sec_type(s): {bad} — valid: "
                  f"{list(INTRADAY_TABLES)}", flush=True)
            return EXIT_USAGE

    t0 = time.time()
    print_build_header(
        "LIVE SIGNALS (analysis_signals threshold breach check)",
        tables=f"live.live_signals, {SIGNALS_TABLE} (read)",
        code=args.code or f"sec_types={sec_types}",
        scheme=args.signal_scheme,
    )

    conn = await get_db_connection_async()
    try:
        evaluator = AnalysisEvaluator(conn)

        if args.code:
            # ---- Single-code mode: probe all sec_types, 404 when none ----
            hits: list[tuple[str, object, object, float]] = []
            for st in SEC_TYPE_PROBE_ORDER:
                bar = await evaluator.fetch_latest_intraday(st, args.code)
                if bar is not None:
                    d, tm, close = bar
                    hits.append((st, d, tm, close))
            if not hits:
                print(f"  404: code {args.code} has no intraday price in "
                      f"any of {list(SEC_TYPE_PROBE_ORDER)} — nothing to "
                      f"check.", flush=True)
                print_wall_time(t0)
                return EXIT_NOT_FOUND
            for st, d, tm, close in hits:
                print(f"  [{st}] latest intraday bar: {d} {tm} "
                      f"close={close}", flush=True)

            total_records: list[dict] = []
            for st, _d, _tm, _close in hits:
                recs, _ = await evaluator.process_code(
                    st, args.code, verbose=True,
                )
                total_records.extend(recs)
        else:
            # ---- Batch mode: every active code of the given sec_types ----
            total_records = await evaluator.process_sec_types(
                sec_types, verbose=True,
            )

        # ---- Record the breaches (PK upsert — idempotent) ---------------
        if total_records:
            await bulk_upsert_async(
                conn, "live.live_signals", total_records, LIVE_SIGNAL_PK,
            )

        await _upsert_live_identity(conn)
        print(f"\n  breaches recorded: {len(total_records)}", flush=True)
        print_wall_time(t0)
        return 0
    finally:
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass


if __name__ == "__main__":
    from _common.post_check import post_check
    try:
        rc = asyncio.run(main())
    finally:
        post_check()
    sys.exit(rc)
