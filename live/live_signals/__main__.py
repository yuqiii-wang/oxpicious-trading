"""Entry point for live.live_signals.

Run via ``python -m live.live_signals --code 000300 [--signal-scheme analysis]``
or ``python -m live.live_signals --sec-type index [--date YYYY-MM-DD]``.

LIVE breach check of the RULE CELLS for ONE code (the LIVE signal
tier — a breach of an active analysis_signals.signal_rules cell's bar
IS the live signal; never a re-detection):

  1. Fetch the code's CURRENT intraday close — the latest
     stats.{sec_type}_intraday_5min bar (date, time, close). The
     sec_type is derived from whichever intraday table holds the code.
     NO intraday price → print ``404`` and exit 404.
  2. Load the code's ACTIVE RULE CELLS (the analysis_signals.
     signal_rules rows of the live-wired families — one per (sub,
     trend, regime, action), each carrying its own action, bar_def
     and confidence scale) and apply ONE GENERIC rule per cell — the
     DAY-CONTEXT MATCH (the day's own trend + market regime pick
     which cells may fire) then the cell's OWN action vs its OWN bar
     (bollinger's fixed ±kσ; mov_rsi's adaptive percentile tail),
     direction by the action (sell breaches above, buy below); NO
     per-family logic. The current value per family comes from the
     declarative SIGNAL_VALUE_SOURCE map (live_signals/analysis/
     fetch): the latest analysis.mov_ave_rsi RSI column (mov_rsi) /
     the on-the-fly σ-depth z (bollinger).
  3. Triggered cells are recorded in live.live_signals (one row per
     (code, sec_type, signal_type, signal_sub_type, date, time); PK
     upsert so re-running the same bar updates in place). Every
     cell's evaluation (triggered or not) is printed. mov_rsi is a
     STATE family: it keeps flagging on every qualifying observation
     while the breach persists.
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

# cudf.pandas activation — must run before pandas first import (the
# repo-wide entry-point convention; this pipeline itself is asyncpg-only).
# Bootstrap-first layout: the hook is installed before the module-level
# imports below (LivePipeline.execute()'s own bootstrap is a no-op —
# flag-guarded).
from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime(require_gpu=False)

import datetime  # noqa: E402
import sys  # noqa: E402

from _common.build_commons import (  # noqa: E402
    print_build_header,
    print_wall_time,
)
from _common.db_commons import bulk_upsert_async  # noqa: E402
from _common.sql_views import frag, render_sql_view  # noqa: E402

from live._base import LivePipeline  # noqa: E402
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

EXIT_NOT_FOUND = 404


class LiveSignalsCheck(LivePipeline):
    """The live.live_signals breach check (live / as-of / range replay)."""

    # The header prints inside run() (after usage validation, per mode);
    # an empty title keeps the base header/wall-time out of the way.
    title = ""
    component = "live_signals"

    def add_arguments(self, parser) -> None:
        parser.description = (
            "Live breach check of the analysis_signals threshold "
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
        parser.add_argument(
            "--signal-scheme", choices=SIGNAL_SCHEMES, default="analysis",
            help="Threshold source scheme (default: analysis).",
        )
        parser.add_argument(
            "--code", default=None,
            help="Single ticker to check (sec_type probed; exits 404 when "
                 "it has no intraday price).",
        )
        parser.add_argument(
            "--sec-type", default=None,
            help="Comma-separated sec_types (e.g. 'index' or 'index,etf'): "
                 "check EVERY code with active signal configs of these "
                 "sec_types. Mutually exclusive with --code.",
        )
        parser.add_argument(
            "--date", default=None,
            help="ON-DEMAND as-of date YYYY-MM-DD: evaluate at the last "
                 "intraday bar of that date, falling back to the official "
                 "daily close (15:00, is_day_close_trigger = TRUE) when the "
                 "date has no intraday bar. Value sources are bounded to "
                 "rows at-or-before the date (no peeking at later data).",
        )
        parser.add_argument(
            "--date-from", default=None,
            help="RANGE REPLAY start YYYY-MM-DD (with --date-to): iterate "
                 "the as-of evaluation over every trading date of the range "
                 "with source data — the breach-history rebuild after a "
                 "purge. Mutually exclusive with --date.",
        )
        parser.add_argument(
            "--date-to", default=None,
            help="RANGE REPLAY end YYYY-MM-DD (inclusive; requires "
                 "--date-from).",
        )

    def apply_args(self) -> None:
        args = self.args
        self.as_of: datetime.date | None = None
        if args.date is not None:
            try:
                self.as_of = datetime.date.fromisoformat(args.date)
            except ValueError:
                self.parser.error("--date must be YYYY-MM-DD, got "
                                  f"'{args.date}'.")

        self.date_range: tuple[datetime.date, datetime.date] | None = None
        if args.date_from is not None or args.date_to is not None:
            if args.date is not None:
                self.parser.error("--date and --date-from/--date-to are "
                                  "mutually exclusive.")
            if args.date_from is None or args.date_to is None:
                self.parser.error("--date-from and --date-to are required "
                                  "together.")
            try:
                self.date_range = (
                    datetime.date.fromisoformat(args.date_from),
                    datetime.date.fromisoformat(args.date_to),
                )
            except ValueError:
                self.parser.error("--date-from/--date-to must be "
                                  f"YYYY-MM-DD, got '{args.date_from}' / "
                                  f"'{args.date_to}'.")
            if self.date_range[0] > self.date_range[1]:
                self.parser.error(f"--date-from {self.date_range[0]} is "
                                  f"after --date-to {self.date_range[1]}.")

        if args.code and args.sec_type:
            self.parser.error("--code and --sec-type are mutually exclusive.")
        if not args.code and not args.sec_type:
            self.parser.error("one of --code / --sec-type is required.")
        if args.signal_scheme == "strategy":
            self.parser.error("--signal-scheme strategy is not implemented "
                              "yet; only 'analysis' is available.")

        self.sec_types: list[str] = []
        if args.sec_type:
            self.sec_types = [
                s.strip() for s in args.sec_type.split(",") if s.strip()
            ]
            bad = [s for s in self.sec_types if s not in INTRADAY_TABLES]
            if bad:
                self.parser.error(f"unknown sec_type(s): {bad} — valid: "
                                  f"{list(INTRADAY_TABLES)}")

    # ------------------------------------------------------------------
    # Range replay: the source-data dates of [date_from, date_to]
    # ------------------------------------------------------------------
    async def _replay_dates(
        self, sec_types: list[str], date_from: datetime.date,
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
            # live/live_signals/__main__/replay_dates — the range's source-data
            # dates (the sec_type's intraday bars ∪ daily closes, code-narrowed)
            view = render_sql_view(
                "live/live_signals/__main__/replay_dates",
                intraday_table=frag(intraday),
                daily_table=frag(daily),
                date_from=date_from,
                date_to=date_to,
                code=code,
            )
            rows = await self.conn.fetch(view.sql, *view.params)
            dates.update(r["date"] for r in rows)
        return sorted(dates)

    # ------------------------------------------------------------------
    # Main
    # ------------------------------------------------------------------
    async def run(self) -> None:
        args = self.args
        t0 = self.t0
        if self.date_range is not None:
            mode = (f"range replay {self.date_range[0]} .. "
                    f"{self.date_range[1]} "
                    "(per-date as-of, daily-close fallback)")
        elif self.as_of is not None:
            mode = f"as-of {self.as_of} (on-demand, daily-close fallback)"
        else:
            mode = "live (latest bar)"
        print_build_header(
            "LIVE SIGNALS (analysis_signals threshold breach check)",
            tables=f"live.live_signals, {SIGNALS_TABLE} (read)",
            code=args.code or f"sec_types={self.sec_types}",
            scheme=args.signal_scheme,
            mode=mode,
        )

        conn = self.conn
        evaluator = AnalysisEvaluator(conn)

        if self.date_range is not None:
            # ---- Range replay: the as-of evaluation iterated over the
            # range's source-data dates, each date committed immediately
            # (per-date upsert — resumable; one daily row per breach by
            # design).
            dates = await self._replay_dates(
                self.sec_types, self.date_range[0], self.date_range[1],
                args.code,
            )
            if dates:
                self.logger.info(
                    f"  replay: {len(dates)} dates with source data "
                    f"({dates[0]} .. {dates[-1]})"
                )
            else:
                self.logger.info(
                    "  replay: no dates with source data in range")
            total_records = []
            for d in dates:
                if args.code:
                    day_records = []
                    for st in SEC_TYPE_PROBE_ORDER:
                        recs, _has_price = await evaluator.process_code(
                            st, args.code, verbose=False, as_of=d,
                        )
                        day_records.extend(recs)
                else:
                    day_records = await evaluator.process_sec_types(
                        self.sec_types, verbose=False, as_of=d,
                    )
                if day_records:
                    await bulk_upsert_async(
                        conn, "live.live_signals",
                        [r.model_dump() for r in day_records],
                        LIVE_SIGNAL_PK,
                    )
                total_records.extend(day_records)
                self.logger.info(f"  {d}: {len(day_records)} breaches")
        elif args.code:
            # ---- Single-code mode: probe all sec_types, 404 when none ----
            if self.as_of is None:
                hits: list[tuple[str, object, object, float]] = []
                for st in SEC_TYPE_PROBE_ORDER:
                    bar = await evaluator.fetch_latest_intraday(st, args.code)
                    if bar is not None:
                        d, tm, close = bar
                        hits.append((st, d, tm, close))
                if not hits:
                    self.logger.info(
                        f"  404: code {args.code} has no intraday "
                        f"price in any of {list(SEC_TYPE_PROBE_ORDER)} — "
                        f"nothing to check.")
                    print_wall_time(t0)
                    self.exit_code = EXIT_NOT_FOUND
                    return
                for st, d, tm, close in hits:
                    self.logger.info(
                        f"  [{st}] latest intraday bar: {d} {tm} "
                        f"close={close}")

                total_records = []
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
                        st, args.code, verbose=True, as_of=self.as_of,
                    )
                    if has_price:
                        n_price += 1
                    total_records.extend(recs)
                if n_price == 0:
                    self.logger.info(
                        f"  404: code {args.code} has no intraday "
                        f"bar and no daily close on {self.as_of} in "
                        f"any of {list(SEC_TYPE_PROBE_ORDER)} — "
                        f"nothing to check.")
                    print_wall_time(t0)
                    self.exit_code = EXIT_NOT_FOUND
                    return
        else:
            # ---- Batch mode: every active code of the given sec_types ----
            total_records = await evaluator.process_sec_types(
                self.sec_types, verbose=True, as_of=self.as_of,
            )

        # ---- Record the breaches (PK upsert — idempotent) ---------------
        if total_records:
            await bulk_upsert_async(
                conn, "live.live_signals",
                [r.model_dump() for r in total_records],
                LIVE_SIGNAL_PK,
            )

        await self.upsert_live_identity(
            name=PIPELINE_NAME,
            detail_name="live_signals",
            summary_name=None,
            description=PIPELINE_DESCRIPTION,
        )
        self.logger.info(f"\n  breaches recorded: {len(total_records)}")
        print_wall_time(t0)
        self.exit_code = 0


if __name__ == "__main__":
    job = LiveSignalsCheck()
    job.execute()
    sys.exit(job.exit_code)
