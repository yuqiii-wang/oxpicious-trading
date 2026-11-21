"""Entry point for analysis.oracle_signals.

Run via ``python -m analysis.oracle_signals``.

Oracle Signals — the hindsight ORACLE tier (see
database/sql/analysis/oracle_signals.sql for the decision grammar):
per-(sec_type, code, date) perfect-foresight actions at the LOCAL
EXTREMES for the 10 study indices — the v7 grammar (2026-11-20):
local extremes detected with a 1-DAY STRIDE over the 5-BAR WINDOW
[i-2, i+2] (v3.1 — v2's +-5 windows are gone), the 5D ONE-ACTION RULE
PER SIDE (consecutive same-side actions >= 5 traded rows apart; the
first candidate of a side's unit claims it, a more extreme same-side
candidate within the unit upgrades the placement), then THE
GENUINE-EXTREME RULE (v6 user-directed "2024-08 has small peaks that
could be noisy, while 2024-09 has a big drop, so should not trigger
buy action but sell some in advance to prepare more cash to buy in
deep in next month" + v7 "the buy in deep rule should also account
for recent rises unless the rises are small, refactor the buy in
deep sell in high logic to take care of recent and future min/max" +
v7.1 "it is still ok to buy if it is a local deep min despite even
in 20d there is a deeper local min ... the buy deep logic is more
about filter small fluctuation noises; 2024-10-16 should be a buy
day" — each side speaks only at a genuine extreme of its own
horizon: a BUY passes the GENUINE-DEEP-MIN TEST (the drawdown INTO
the pivot from the prior 20 rows' high >= 5% makes it a genuine deep
min — always a buy, even when a deeper local min follows within the
20d horizon; a shallower min is a small fluctuation and omits only
when a materially deeper level is imminent within those 20 rows);
a SELL must be a genuine HIGH (no more
than a small fall from the prior 20 rows' ceiling — a weak bounce is
not a high — and no close 5%+ above within the next 20 rows — a
higher exit is imminent), then THE FAVORABLE-MOVE GRADING
(v4 forward + v5 backward, user-directed "should look future 60d if
there are big rise/drop trend, should tune down action confidence
even take no action" + "should consider before and after local
min/max rises and falls" + "rises/drops before action date weighted
30%, future weighted 70%" — the 2020-01 small-gap and the
2020-11-09 / 2024-02-23 / 2025-05-14 top examples): each surviving
pivot is validated by its favorable excursion on EITHER side — a buy
by the biggest rise within the next 20 traded rows or the biggest
drop within the 60 rows before it, a sell by the biggest drop after /
biggest rise before — each leg graded 0-100 on the linear ramp from
CONF_NO_ACTION_MOVE (2%) to CONF_FULL_MOVE (10%) and combined as the
WEIGHTED SUM 0.3 * back + 0.7 * fwd, with a zero weighted grade
(both legs below the floor) OMITTING the row and a backward-dust
unrealized frontier window riding confidence NULL (re-graded by
later runs).
Each kept row's ext_* legs carry the next OPPOSITE placed action (the
hindsight swing target). Calibrated by temp_scripts/
oracle_forward60_study.py + temp_scripts/oracle_backward_leg_study.py
+ temp_scripts/oracle_prepare_deep_study.py +
temp_scripts/oracle_genuine_extreme_study.py
(docs/oracle_forward60_confidence_study_2026-11-19.md,
docs/oracle_backward_leg_study_2026-11-19.md,
docs/oracle_prepare_deep_study_2026-11-20.md,
docs/oracle_genuine_extreme_study_2026-11-20.md). DECOUPLED from
the analysis_signals rule tier — its own table, its own pipeline, no
rule cells / replay / live wiring.

Serves the Signals → Oracle page (/analysis/signals/oracle) through
GET /api/analysis/oracle/*.

STRUCTURED for the security sec_types (etf / index / stock —
per-sec_type source mapping in config.SEC_TYPE_SOURCES), but the RUN
SCOPE is index-only for now (config.ENABLED_SEC_TYPES; index = the 10
study indices). --sec-type selects one enabled sec_type.

Modes:
  incremental (default): the engine detects the FULL confirmed
    candidate set (extremes whose 2 right-side bars exist) and the run
    writes only what changed — newly kept pivots plus each code's
    REVISING TAIL: every decision at/after the engine's per-code tail
    cutoff (~25 traded rows back — the future-reading horizons are
    ALL 20 (user-directed "only 20d future look forward"), + the
    action span)
    is
    deleted + rewritten, because a pivot emitted with an unrealized
    forward window (confidence NULL) can re-grade — or drop out
    entirely once its realized move misses the floor — as bars
    arrive; earlier rows are immutable (first-run gap fill still
    upserts any missing earlier decision). A WEEKLY GATE no-ops the
    scheduled incremental run until the last build is
    config.WEEKLY_GATE_DAYS calendar days old (the nightly chain
    fires every trading day; the weekly tier's own guard makes
    off-cadence fires no-ops — the builds_monthly month-guard
    precedent).
  --force: purge the sec_type's rows + full recompute (bypasses the
    weekly gate).
  --code CODE: recompute ALL rows for one security (bypasses the
    weekly gate; the UI per-security rebuild path).
"""
from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

import time

from _common.labels import SEC_TYPE_CODE
from _common.log_setup import setup_logging

from _common.data_analysis import DataAnalysis
from _common.db_commons import (
    analyze_tables_async,
    bulk_upsert_async,
    copy_frame_chunked_async,
)
from analysis._common import sanitize_for_db_insert
from analysis.oracle_signals import config
from analysis.oracle_signals.data_definitions import (
    OUT_COLUMNS,
    OUT_NUMERIC_COLUMNS,
)
from analysis.oracle_signals.engine import OracleDecisionEngine
from analysis.oracle_signals.fetch import OracleSources

logger = setup_logging("oracle_signals")


TABLE = "analysis.oracle_signals"
ANALYSIS_NAME = "oracle_signals"
ANALYSIS_DESCRIPTION = (
    "Hindsight oracle actions at the local extremes for the 10 study "
    "indices: every local min of the 5-bar window (2 rows each side) "
    "takes a buy, every local max a sell, consecutive same-side "
    "actions >= 5 traded rows apart — a 5-row window holds one buy "
    "AND one sell (the per-side one-action window); each side then "
    "speaks only at a GENUINE EXTREME of its own horizon (the v7 "
    "rule, buy side refined v7.1): a BUY passes the genuine-deep-min "
    "test — the drawdown INTO the pivot from the prior 20 rows' high "
    "is 5%+ (a genuine deep min: always a buy, even when a deeper "
    "local min follows within the 20d horizon; a shallower min is a "
    "small fluctuation that omits only when a materially deeper "
    "level is imminent within those 20 rows) — and a SELL "
    "must be a genuine high — no more than a small fall from the "
    "prior 20 rows' ceiling (a weak bounce is not a high) and no "
    "close 5%+ above within the next 20 rows (a higher exit is "
    "imminent); each surviving pivot is then graded 0-100 by its "
    "favorable move on EITHER side (buy: biggest rise after / "
    "biggest drop before, sell: biggest drop after / biggest rise "
    "before — the forward window LOOKFORWARD_DAYS (20) traded rows "
    "(user-directed 2026-11-20 \"let future look only be 20d not "
    "60d\"; the backward LOOKBACK_DAYS stays 60); each leg ramped "
    "linearly 2% floor / 10% full and combined as the weighted sum "
    "0.3 * pre-action leg + 0.7 * forward leg), with a zero weighted "
    "grade (both legs below the floor) the pivot emits nothing and a "
    "backward-dust unrealized frontier window rides confidence NULL; "
    "each kept row carries the next opposite pivot as its hindsight "
    "swing target (the v7 grammar — v3.1's detection + selection, "
    "the genuine-extreme gate on both sides, graded by the "
    "favorable-move validation)."
)


class OracleAnalysis(DataAnalysis):
    """``python -m analysis.oracle_signals`` — the hindsight oracle
    tier, one decision row per (sec_type, code, decision date)."""

    title = ("ANALYZE ORACLE SIGNALS — hindsight oracle decisions "
             "(index scope)")
    component = "oracle_signals"
    sec_types = config.ENABLED_SEC_TYPES

    def add_arguments(self, parser) -> None:
        self.add_force_arg(parser)
        self.add_sec_type_arg(parser)
        self.add_code_arg(parser)

    def apply_args(self) -> None:
        pass

    def header_fields(self) -> dict:
        return {
            "tables": TABLE,
            "mode": ("FORCE (scoped delete + full recompute)"
                     if self.args.force
                     else ("single-code recompute" if self.args.code
                           else "incremental (missing decisions only)")),
        }

    async def run(self) -> None:
        t0 = time.time()
        force = bool(self.args.force)
        sec_types = self.resolve_sec_types()

        logger.info("=" * 78)
        logger.info("  ORACLE SIGNALS  (%s mode)  sec_types=%s",
                    "FORCE" if force else ("single-code" if self.args.code
                                           else "incremental"),
                    ",".join(sec_types))
        logger.info("=" * 78)

        n_total = 0
        for sec_type in sec_types:
            n_total += await self._run_sec_type(sec_type, t0)
        await self.upsert_identity(
            ANALYSIS_NAME, ANALYSIS_NAME, ANALYSIS_DESCRIPTION)
        logger.info("[done] %s row(s) written across %d sec_type(s) "
                    "(%.1fs).", f"{n_total:,}", len(sec_types),
                    time.time() - t0)

    async def _run_sec_type(self, sec_type: str, t0: float) -> int:
        """One sec_type's slice: gate → universe → fetch + decide →
        scope → write. Returns the rows written."""
        sec_code = SEC_TYPE_CODE[sec_type]
        sources = OracleSources(self.conn, sec_type, codes=[])
        codes = ([self.args.code] if self.args.code
                 else await sources.resolve_universe())
        sources.codes = codes
        if not codes:
            logger.info("[%s] no codes with close data — skipping.",
                        sec_type)
            return 0
        tag = f"oracle:{sec_type}"

        # ---- weekly gate (the scheduled incremental path only) --------
        if not self.args.force and not self.args.code:
            days_idle = await self._days_since_last_build()
            if days_idle is not None and days_idle < config.WEEKLY_GATE_DAYS:
                logger.info(
                    "[%s] weekly gate: last build %.1f day(s) ago "
                    "(< %d) — nothing to do.", tag, days_idle,
                    config.WEEKLY_GATE_DAYS)
                return 0

        # ---- fetch + decide --------------------------------------------
        closes = await sources.fetch_daily_closes()
        logger.info("[%s] %s close row(s).", tag, f"{len(closes):,}")
        if closes.empty:
            logger.info("[%s] no closes — nothing to compute.", tag)
            return 0
        engine = OracleDecisionEngine(closes, sec_code)
        frame = engine.build()
        logger.info("[%s]        -> %s decision row(s) (%.1fs)",
                    tag, f"{len(frame):,}", time.time() - t0)
        if frame.empty:
            return 0

        # ---- write ------------------------------------------------------
        if self.args.force:
            # Scoped force clear: DELETE only this sec_type's rows (a
            # TRUNCATE would wipe sec_types this run does not rebuild
            # — the multi-sec_type contract).
            await self.delete_sec_type_rows(TABLE, [sec_code])
            n = await copy_frame_chunked_async(
                self.conn, TABLE, frame,
                columns=OUT_COLUMNS,
                numeric_cols=OUT_NUMERIC_COLUMNS,
                round_to=6,
                date_cols=["date", "ext_date"],
                label=f"[{tag}]",
            )
            await analyze_tables_async(self.conn, [TABLE])
            logger.info("[%s] wrote %s row(s) (force).", tag, f"{n:,}")
            return n

        if self.args.code:
            # single-code recompute: purge that code's rows first (the
            # engine re-decides its whole evaluable grid)
            await self._delete_code_rows(sec_code, self.args.code)

        rows = sanitize_for_db_insert(
            frame, numeric_cols=OUT_NUMERIC_COLUMNS,
            round_to=6, date_cols=["date", "ext_date"])
        if self.args.code:
            n = 0
            if rows:
                n = await bulk_upsert_async(
                    self.conn, TABLE, rows,
                    key_columns=["sec_type", "code", "date"])
        else:
            # incremental: each code's REVISING TAIL is date-based —
            # every decision at/after the engine's tail cutoff (the
            # pivots whose forward-60d window is still unrealized can
            # re-grade or drop out entirely as bars arrive, and a
            # frontier unit's placement can UPGRADE) is deleted +
            # rewritten; earlier rows are immutable (first-run gap
            # fill still upserts any missing earlier decision)
            n = await self._write_incremental(sec_code, rows,
                                              engine.tail_cutoffs)
        logger.info("[%s] wrote %s row(s).", tag, f"{n:,}")

        # ---- sanity ------------------------------------------------------
        summary = await self.conn.fetchrow(f"""
            SELECT COUNT(*) AS n_rows, MIN(date) AS first_date,
                   MAX(date) AS last_date
              FROM {TABLE}
             WHERE sec_type = $1
        """, sec_code)
        if summary:
            logger.info(
                "[%s] %s row(s) now, %s .. %s.",
                tag, f"{summary['n_rows']:,}", summary["first_date"],
                summary["last_date"])
        return n

    async def _days_since_last_build(self) -> float | None:
        """Calendar days since the target table's last build (max
        created_at) — the weekly gate's clock. None = empty table."""
        row = await self.conn.fetchrow(
            f"SELECT EXTRACT(epoch FROM now() - MAX(created_at)) / 86400.0 "
            f"AS days FROM {TABLE}")
        if row is None or row["days"] is None:
            return None
        return float(row["days"])

    async def _delete_code_rows(self, sec_code: int, code: str) -> None:
        """Purge one code's rows (the single-code recompute's clear)."""
        from _common.db_commons import chunked_purge_async

        n = await chunked_purge_async(
            self.conn, TABLE,
            where_sql="sec_type = $1 AND code = $2",
            params=(sec_code, code),
        )
        logger.info("[oracle] purged %s row(s) for code %s.", f"{n:,}",
                    code)

    async def _write_incremental(
        self, sec_code: int, rows: list[dict],
        tail_cutoffs: dict[str, object],
    ) -> int:
        """The incremental write: per code, DELETE + rewrite the
        revisable TAIL — every decision at/after the engine's per-code
        tail cutoff date (the pivots whose forward-60d window is still
        unrealized can re-grade, or drop out entirely once the realized
        move misses the no-action floor, and a frontier unit's
        placement can UPGRADE) — and upsert the earlier decisions the
        target still lacks (first-run gap fill). Everything before the
        cutoff is immutable — the weekly write stays small without ever
        leaving a stale or wrongly-graded action row. Codes with no
        kept rows this run still get their tail delete (a fully-omitted
        tail must not linger)."""
        have = await self.conn.fetch(
            f"SELECT code, date FROM {TABLE} WHERE sec_type = $1",
            sec_code)
        existing = {(r["code"], r["date"]) for r in have}

        by_code: dict[str, list[dict]] = {}
        for r in rows:
            by_code.setdefault(r["code"], []).append(r)

        n = 0
        for code, cutoff in tail_cutoffs.items():
            decided = sorted(by_code.get(code, []),
                             key=lambda r: r["date"])
            await self.conn.execute(
                f"DELETE FROM {TABLE} "
                f"WHERE sec_type = $1 AND code = $2 AND date >= $3",
                sec_code, code, cutoff)
            tail = [r for r in decided if r["date"] >= cutoff]
            gap = [r for r in decided
                   if r["date"] < cutoff
                   and (r["code"], r["date"]) not in existing]
            write = tail + gap
            if write:
                n += await bulk_upsert_async(
                    self.conn, TABLE, write,
                    key_columns=["sec_type", "code", "date"])
        return n


def main() -> None:
    OracleAnalysis().execute()


if __name__ == "__main__":
    main()
