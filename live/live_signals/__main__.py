"""Entry point for live.live_signals.

Run via ``python -m live.live_signals --code 000300 [--signal-scheme analysis]``
or ``python -m live.live_signals --sec-type index [--date YYYY-MM-DD]``.

LIVE breach check of the threshold set for ONE code (the LIVE signal
tier — a breach of an active analysis_signals.signal_strategies
threshold IS the
live signal; never a re-detection):

  1. Fetch the code's CURRENT intraday close — the latest
     stats.{sec_type}_intraday_5min bar (date, time, close). The
     sec_type is derived from whichever intraday table holds the code.
     NO intraday price → print ``404`` and exit 404.
  2. Load the code's ACTIVE signal configs (``is_active`` rows of
     analysis_signals.signal_strategies — all signal types / sub
     types of the
     resolved sec_type) and apply ONE GENERIC rule per config — the
     row's OWN action vs its OWN signal_threshold, direction by the
     action (sell breaches above, buy below), confidence copied from
     the row; NO per-family logic. The current value per config comes
     from the declarative SIGNAL_VALUE_SOURCE map
     (live_signals/analysis/fetch): the intraday close (mov_std /
     high_low_streaks), the latest analysis.mov_ave_rsi RSI
     column (mov_rsi), the day's cross legs (mov_pairs /
     mov_pairs_ema — the day's ma5/ema6/price vs the day's
     ma{W}/ema{W} slow leg) or the recomputed
     ratio z (margin_ratio).
  3. Triggered configs are recorded in live.live_signals (one row per
     (code, sec_type, signal_type, signal_sub_type, date, time); PK
     upsert so re-running the same bar updates in place). Every
     config's evaluation (triggered or not) is printed. is_triggered_once
     strategies (the pair-cross families — the cross is an event) flag
     ONCE per breach episode: the evaluator resolves the episode from
     the daily legs-row chain and records at most one live row per
     episode; the state families keep flagging on every observation.
  4. Upsert live.live_identity.

ON-DEMAND AS-OF MODE (--date D — invoked by the Trading Signals API
when a selected date has no live rows): the evaluated bar becomes the
LAST intraday bar ON D when the intraday table has one (an end-of-day
replay of the live check), else the code's OFFICIAL DAILY CLOSE on D
from stats.{sec_type}_basic_stats — recorded at 15:00 with
is_day_close_trigger = TRUE (the no-intraday-data fallback). Every
value source is bounded to its latest row at-or-before D, so the
replay never peeks at later data. Codes with neither an intraday bar
nor a daily close on D are skipped. Works with --code and --sec-type.

RANGE REPLAY MODE (--date-from/--date-to): the as-of mode iterated
over every trading date of the range that has source data (the
intraday bars ∪ daily closes of the requested sec_types / code) — the
breach-history rebuild after a purge or a semantics change. Each date
evaluates exactly like --date D (one row per (code, signal_sub_type,
date): the day's last intraday bar, else the 15:00 daily close) and is
upserted immediately (per-date commit, resumable). Historical
multi-bar observations collapse to that one daily row by design.

--signal-scheme analysis (default) | strategy — 'strategy' is reserved
for a future strategy.*-sourced threshold set and exits with code 2.

Exit codes: 0 = checked (breaches recorded or not), 2 = usage error /
unimplemented scheme, 404 = code has no intraday price (no --date) /
no price on the --date (single-code mode only).
"""
from __future__ import annotations


# resource pre-check — pure asyncpg DB I/O, no GPU needed. The system-RAM
# floor is likewise lowered (the default 36 GiB guards the heavy cudf
# pipelines; this process peaks at ~1 GiB RSS) — without this, the
# API-invoked on-demand --date spawns would abort whenever the WSL VM
# reports < 36 GiB. setdefault: an explicit ANALYZE_MIN_SYS_GB in the
# environment still wins.
import os as _os

_os.environ.setdefault("ANALYZE_MIN_SYS_GB", "8")
from _common.pre_check import pre_check

pre_check(require_gpu=False)
import argparse
import asyncio
import datetime
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

# cudf.pandas activation — must run before pandas first import (the
# repo-wide entry-point convention; this pipeline itself is asyncpg-only)
from _common.df_utils._activate import activate  # noqa: E402

activate()

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
    DAILY_TABLES,
    INTRADAY_TABLES,
    LIVE_SIGNAL_PK,
    PIPELINE_DESCRIPTION,
    PIPELINE_NAME,
    SEC_TYPE_PROBE_ORDER,
    SIGNALS_TABLE,
    SIGNAL_SCHEMES,
)

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("live_signals")

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


async def _replay_dates(
    conn, sec_types: list[str], date_from: datetime.date,
    date_to: datetime.date, code: str | None,
) -> list[datetime.date]:
    """The trading dates of [date_from, date_to] that have SOURCE data
    for the replay — the union of the intraday tables' and the daily
    baseline's distinct dates (per sec_type; ``code`` narrows to one
    ticker in single-code mode). Dates without any bar/close are
    skipped: the as-of evaluation would produce nothing for them."""
    scopes = sec_types or list(SEC_TYPE_PROBE_ORDER)
    dates: set[datetime.date] = set()
    for st in scopes:
        intraday = INTRADAY_TABLES[st]
        daily = DAILY_TABLES[st]
        rows = await conn.fetch(
            f"""
            SELECT DISTINCT date FROM (
                SELECT date FROM {intraday}
                WHERE date BETWEEN $1 AND $2
                  AND ($3::text IS NULL OR code = $3)
                UNION
                SELECT date FROM {daily}
                WHERE date BETWEEN $1 AND $2
                  AND close IS NOT NULL
                  AND ($3::text IS NULL OR code = $3)
            ) d
            """,
            date_from, date_to, code,
        )
        dates.update(r["date"] for r in rows)
    return sorted(dates)


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

async def main() -> int:
    ap = argparse.ArgumentParser(
        description="Live breach check of the analysis_signals threshold "
                    "set (the LIVE signal tier): every active signal "
                    "config's current value (per the declarative "
                    "value-source map) vs the row's OWN threshold, "
                    "direction by the row's OWN action — the generic "
                    "rule, no per-family logic; triggered breaches are "
                    "recorded in live.live_signals with the row's "
                    "action and confidence. Either --code (one code, "
                    "sec_type probed, 404 exit when no intraday price) "
                    "or --sec-type (ALL codes with active signal "
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
    ap.add_argument(
        "--date", default=None,
        help="ON-DEMAND as-of date YYYY-MM-DD: evaluate at the last "
             "intraday bar of that date, falling back to the official "
             "daily close (15:00, is_day_close_trigger = TRUE) when the "
             "date has no intraday bar. Value sources are bounded to "
             "rows at-or-before the date (no peeking at later data).",
    )
    ap.add_argument(
        "--date-from", default=None,
        help="RANGE REPLAY start YYYY-MM-DD (with --date-to): iterate "
             "the as-of evaluation over every trading date of the range "
             "with source data — the breach-history rebuild after a "
             "purge. Mutually exclusive with --date.",
    )
    ap.add_argument(
        "--date-to", default=None,
        help="RANGE REPLAY end YYYY-MM-DD (inclusive; requires "
             "--date-from).",
    )
    args = ap.parse_args()

    as_of: datetime.date | None = None
    if args.date is not None:
        try:
            as_of = datetime.date.fromisoformat(args.date)
        except ValueError:
            logger.error(f"  [ERROR] --date must be YYYY-MM-DD, got "
                  f"'{args.date}'.")
            return EXIT_USAGE

    date_range: tuple[datetime.date, datetime.date] | None = None
    if args.date_from is not None or args.date_to is not None:
        if args.date is not None:
            logger.error("  [ERROR] --date and --date-from/--date-to are "
                  "mutually exclusive.")
            return EXIT_USAGE
        if args.date_from is None or args.date_to is None:
            logger.error("  [ERROR] --date-from and --date-to are required "
                  "together.")
            return EXIT_USAGE
        try:
            date_range = (
                datetime.date.fromisoformat(args.date_from),
                datetime.date.fromisoformat(args.date_to),
            )
        except ValueError:
            logger.error(f"  [ERROR] --date-from/--date-to must be "
                  f"YYYY-MM-DD, got '{args.date_from}' / '{args.date_to}'.")
            return EXIT_USAGE
        if date_range[0] > date_range[1]:
            logger.error(f"  [ERROR] --date-from {date_range[0]} is after "
                  f"--date-to {date_range[1]}.")
            return EXIT_USAGE

    if args.code and args.sec_type:
        logger.error("  [ERROR] --code and --sec-type are mutually exclusive.")
        return EXIT_USAGE
    if not args.code and not args.sec_type:
        logger.error("  [ERROR] one of --code / --sec-type is required.")
        return EXIT_USAGE
    if args.signal_scheme == "strategy":
        logger.error("  [ERROR] --signal-scheme strategy is not implemented yet; "
              "only 'analysis' is available.")
        return EXIT_USAGE

    sec_types: list[str] = []
    if args.sec_type:
        sec_types = [
            s.strip() for s in args.sec_type.split(",") if s.strip()
        ]
        bad = [s for s in sec_types if s not in INTRADAY_TABLES]
        if bad:
            logger.error(f"  [ERROR] unknown sec_type(s): {bad} — valid: "
                  f"{list(INTRADAY_TABLES)}")
            return EXIT_USAGE

    t0 = time.time()
    if date_range is not None:
        mode = (f"range replay {date_range[0]} .. {date_range[1]} "
                "(per-date as-of, daily-close fallback)")
    elif as_of is not None:
        mode = f"as-of {as_of} (on-demand, daily-close fallback)"
    else:
        mode = "live (latest bar)"
    print_build_header(
        "LIVE SIGNALS (analysis_signals threshold breach check)",
        tables=f"live.live_signals, {SIGNALS_TABLE} (read)",
        code=args.code or f"sec_types={sec_types}",
        scheme=args.signal_scheme,
        mode=mode,
    )

    conn = await get_db_connection_async()
    try:
        evaluator = AnalysisEvaluator(conn)

        if date_range is not None:
            # ---- Range replay: the as-of evaluation iterated over the
            # range's source-data dates, each date committed immediately
            # (per-date upsert — resumable; one daily row per breach by
            # design).
            dates = await _replay_dates(
                conn, sec_types, date_range[0], date_range[1], args.code,
            )
            if dates:
                logger.info(
                    f"  replay: {len(dates)} dates with source data "
                    f"({dates[0]} .. {dates[-1]})"
                )
            else:
                logger.info("  replay: no dates with source data in range")
            total_records: list[dict] = []
            for d in dates:
                if args.code:
                    day_records: list[dict] = []
                    for st in SEC_TYPE_PROBE_ORDER:
                        recs, _has_price = await evaluator.process_code(
                            st, args.code, verbose=False, as_of=d,
                        )
                        day_records.extend(recs)
                else:
                    day_records = await evaluator.process_sec_types(
                        sec_types, verbose=False, as_of=d,
                    )
                if day_records:
                    await bulk_upsert_async(
                        conn, "live.live_signals", day_records,
                        LIVE_SIGNAL_PK,
                    )
                total_records.extend(day_records)
                logger.info(f"  {d}: {len(day_records)} breaches")
        elif args.code:
            # ---- Single-code mode: probe all sec_types, 404 when none ----
            if as_of is None:
                hits: list[tuple[str, object, object, float]] = []
                for st in SEC_TYPE_PROBE_ORDER:
                    bar = await evaluator.fetch_latest_intraday(st, args.code)
                    if bar is not None:
                        d, tm, close = bar
                        hits.append((st, d, tm, close))
                if not hits:
                    logger.info(f"  404: code {args.code} has no intraday "
                          f"price in any of {list(SEC_TYPE_PROBE_ORDER)} — "
                          f"nothing to check.")
                    print_wall_time(t0)
                    return EXIT_NOT_FOUND
                for st, d, tm, close in hits:
                    logger.info(f"  [{st}] latest intraday bar: {d} {tm} "
                          f"close={close}")

                total_records: list[dict] = []
                for st, _d, _tm, _close in hits:
                    recs, _ = await evaluator.process_code(
                        st, args.code, verbose=True,
                    )
                    total_records.extend(recs)
            else:
                # As-of single code: process_code resolves the bar per
                # sec_type (last intraday bar of the date, else the
                # daily-close fallback) — collect from every hit.
                total_records = []
                n_price = 0
                for st in SEC_TYPE_PROBE_ORDER:
                    recs, has_price = await evaluator.process_code(
                        st, args.code, verbose=True, as_of=as_of,
                    )
                    if has_price:
                        n_price += 1
                    total_records.extend(recs)
                if n_price == 0:
                    logger.info(f"  404: code {args.code} has no intraday "
                          f"bar and no daily close on {as_of} in any of "
                          f"{list(SEC_TYPE_PROBE_ORDER)} — nothing to "
                          f"check.")
                    print_wall_time(t0)
                    return EXIT_NOT_FOUND
        else:
            # ---- Batch mode: every active code of the given sec_types ----
            total_records = await evaluator.process_sec_types(
                sec_types, verbose=True, as_of=as_of,
            )

        # ---- Record the breaches (PK upsert — idempotent) ---------------
        if total_records:
            await bulk_upsert_async(
                conn, "live.live_signals", total_records, LIVE_SIGNAL_PK,
            )

        await _upsert_live_identity(conn)
        logger.info(f"\n  breaches recorded: {len(total_records)}")
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
