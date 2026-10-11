"""SnapshotSelection — the incremental snapshot-resolution mixin of
SignalEngine (analysis.analysis_signals.engines._snapshots).

Snapshot-keyed end to end over the engine's OWN snapshot grid (the
retired forecast identities registry went with the forecast layer,
2026-10-05): the annual grid's completed year-ends plus the ROLLING
LATEST snapshot (keyed at the sec_type's latest available RSI data
date), each over its trailing WINDOW_YEARS window, with the mutable
scope rule shared by the grid (config.mutable_dates).
"""
from __future__ import annotations

from datetime import date

from _common.labels import SEC_TYPE_CODE, SIGNAL_TYPE_CODE

from _common.db_commons import chunked_dml_by_key_async

from analysis.analysis_signals.config import (
    TABLE_CURVES,
    TABLE_HISTORY,
    TABLE_THRESHOLDS,
    build_stat_specs,
    mutable_dates,
)
from analysis.analysis_signals.fetch import fetch_latest_rsi_date


class SnapshotSelection:
    """resolve_snapshots / sweep_stale_snapshots — which stat_dates
    this family (re-)emits for a sec_type, and which retired keys to
    purge. Requires the engine's ``signal_type`` identity attr."""

    signal_type: str

    def _codes(self, sec_type: str) -> tuple[int, int]:
        """(sec_type, signal_type) as their stored SMALLINT label
        codes — the SQL params the selection/purge statements compare
        against (the labels stay the call/log vocabulary)."""
        return (SEC_TYPE_CODE[sec_type], SIGNAL_TYPE_CODE[self.signal_type])

    async def latest_data_date(self, conn, sec_type: str) -> date | None:
        """The family's value-source max date — the ROLLING LATEST
        snapshot's key and window end. The default reads the RSI table
        (mov_rsi); a family with its own value source overrides (the
        boll_std engine reads the latest price-data date — its values
        are computed in-engine from prices)."""
        return await fetch_latest_rsi_date(conn, sec_type)

    async def resolve_snapshots(
        self,
        conn,
        sec_type: str,
        force: bool,
        snapshots: int | None,
    ) -> list:
        """The target StatSpecs, ascending.

        The grid = build_stat_specs(latest_date = the sec_type's
        latest available RSI data date) — the last N_YEARS completed
        year-ends plus the ROLLING LATEST key.

        FULL emission = the snapshots MISSING from signal_thresholds
        plus the MUTABLE SCOPE among the present ones
        (config.mutable_dates — the ROLLING LATEST snapshot, always,
        plus the newest completed year-end while its 20-trading-day
        forward windows are still unrealized). A snapshot written while
        its long-horizon mixed legs were incomplete is deleted +
        re-emitted on every run until it freezes; completed year-ends
        outside the mutable scope are all-history and never re-emitted.
        --force makes every present snapshot a full target (the caller
        purges first). ``snapshots`` caps to the newest N."""
        latest = await self.latest_data_date(conn, sec_type)
        if latest is None:
            return []
        specs = build_stat_specs(latest_date=latest)
        if snapshots is not None:
            specs = specs[-snapshots:]
        if force:
            return specs

        written_rows = await conn.fetch(
            f"SELECT DISTINCT end_date FROM {TABLE_THRESHOLDS} "
            f"WHERE sec_type = $1 AND signal_type = $2",
            *self._codes(sec_type),
        )
        have: set[date] = {r["end_date"] for r in written_rows}
        refresh = mutable_dates({s.stat_date for s in specs}, date.today())
        return [
            s for s in specs
            if s.stat_date not in have or s.stat_date in refresh
        ]

    async def sweep_stale_snapshots(self, conn, sec_type: str) -> int:
        """Delete the family's rows whose end_date is NOT on the
        current snapshot grid — the ROLLING LATEST key's predecessor
        (yesterday's key once today's has taken over) and any legacy
        keys. Runs BEFORE resolve_snapshots so a retired key can
        neither suppress a re-emission (it counts as 'have') nor
        linger. The retired snapshot's HISTORY rows live in its
        calendar year (the ownership interval) — deleted with it. The
        deletes are CHUNKED BY PARTITION KEY (the bulk-write rule's
        delete flavor): per-code ~100K-row whole-code transactions
        (both tables of a chunk in one transaction), replica-lag
        throttle between chunks. Returns the number of retired keys."""
        latest = await self.latest_data_date(conn, sec_type)
        if latest is None:
            return 0
        specs = build_stat_specs(latest_date=latest)
        present: set[date] = {s.stat_date for s in specs}
        have_rows = await conn.fetch(
            f"SELECT DISTINCT end_date FROM {TABLE_THRESHOLDS} "
            f"WHERE sec_type = $1 AND signal_type = $2",
            *self._codes(sec_type),
        )
        stale = sorted({r["end_date"] for r in have_rows} - present)
        for d in stale:
            first = date(d.year, 1, 1)
            next_year = date(d.year + 1, 1, 1)
            # per-code doomed-row counts across ALL THREE tables (the
            # chunker's weights; each query carries its own args)
            weights: dict[str, int] = {}
            for sql, args in (
                (f"SELECT code, count(*) AS n FROM {TABLE_THRESHOLDS} "
                 f"WHERE sec_type = $1 AND signal_type = $2 "
                 f"AND end_date = $3 GROUP BY code",
                 (*self._codes(sec_type), d)),
                (f"SELECT code, count(*) AS n FROM {TABLE_HISTORY} "
                 f"WHERE sec_type = $1 AND signal_type = $2 "
                 f"AND date >= $3 AND date < $4 GROUP BY code",
                 (*self._codes(sec_type), first, next_year)),
                (f"SELECT code, count(*) AS n FROM {TABLE_CURVES} "
                 f"WHERE sec_type = $1 AND signal_type = $2 "
                 f"AND end_date = $3 GROUP BY code",
                 (*self._codes(sec_type), d)),
            ):
                for r in await conn.fetch(sql, *args):
                    weights[r["code"]] = weights.get(r["code"], 0) + r["n"]
            await chunked_dml_by_key_async(
                conn,
                statements=[
                    (f"DELETE FROM {TABLE_THRESHOLDS} "
                     f"WHERE sec_type = $1 AND signal_type = $2 "
                     f"AND end_date = $3",
                     " AND code = ANY(${ph}::text[])",
                     (*self._codes(sec_type), d)),
                    (f"DELETE FROM {TABLE_HISTORY} "
                     f"WHERE sec_type = $1 AND signal_type = $2 "
                     f"AND date >= $3 AND date < $4",
                     " AND code = ANY(${ph}::text[])",
                     (*self._codes(sec_type), first, next_year)),
                    (f"DELETE FROM {TABLE_CURVES} "
                     f"WHERE sec_type = $1 AND signal_type = $2 "
                     f"AND end_date = $3",
                     " AND code = ANY(${ph}::text[])",
                     (*self._codes(sec_type), d)),
                ],
                params=(),
                weighted_keys=list(weights.items()),
            )
        return len(stale)
